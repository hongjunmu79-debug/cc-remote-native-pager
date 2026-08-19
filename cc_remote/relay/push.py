"""Durable, machine-scoped Web Push subscriptions for the relay.

The relay deliberately sends generic completion notices only. Prompt text,
model output, paths, session ids, and tool details never leave the authenticated
WebSocket path through the push provider.
"""
from __future__ import annotations

import asyncio
import json
import secrets
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, Literal

from cc_remote.log import logger

log = logger("cc_remote.relay.push")


@dataclass(frozen=True)
class PushSubscription:
    subject: str
    machine_id: str
    endpoint: str
    p256dh: str
    auth: str
    session_jti: str
    expires_at: float

    def browser_payload(self) -> dict[str, object]:
        return {
            "endpoint": self.endpoint,
            "keys": {"p256dh": self.p256dh, "auth": self.auth},
        }


class PushSubscriptionStore:
    """Small SQLite store opened per operation for crash-safe relay restarts."""

    def __init__(self, path: str) -> None:
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=10)
        db.execute("PRAGMA busy_timeout=10000")
        db.execute("PRAGMA journal_mode=WAL")
        return db

    def _initialize(self) -> None:
        with self._connect() as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS push_subscriptions (
                    subject TEXT NOT NULL,
                    machine_id TEXT NOT NULL,
                    endpoint TEXT NOT NULL,
                    p256dh TEXT NOT NULL,
                    auth TEXT NOT NULL,
                    session_jti TEXT NOT NULL,
                    expires_at REAL NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (subject, machine_id, endpoint)
                )
                """
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_push_machine "
                "ON push_subscriptions(machine_id)"
            )
            # Databases created by the first preview build are upgraded in
            # place. Empty defaults deliberately make legacy rows ineligible
            # for delivery until the authenticated browser subscribes again.
            columns = {
                row[1] for row in db.execute(
                    "PRAGMA table_info(push_subscriptions)").fetchall()
            }
            if "session_jti" not in columns:
                db.execute(
                    "ALTER TABLE push_subscriptions ADD COLUMN "
                    "session_jti TEXT NOT NULL DEFAULT ''"
                )
            if "expires_at" not in columns:
                db.execute(
                    "ALTER TABLE push_subscriptions ADD COLUMN "
                    "expires_at REAL NOT NULL DEFAULT 0"
                )

    async def upsert(self, subscription: PushSubscription) -> None:
        await asyncio.to_thread(self._upsert, subscription)

    def _upsert(self, subscription: PushSubscription) -> None:
        now = time.time()
        with self._connect() as db:
            # A browser has one PushManager subscription per origin. Moving that
            # browser to another account or machine must transfer the endpoint,
            # not leave duplicate deliveries attached to an older login.
            db.execute(
                "DELETE FROM push_subscriptions WHERE endpoint=?",
                (subscription.endpoint,),
            )
            db.execute(
                """
                INSERT INTO push_subscriptions
                    (subject, machine_id, endpoint, p256dh, auth, session_jti,
                     expires_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(subject, machine_id, endpoint) DO UPDATE SET
                    p256dh=excluded.p256dh,
                    auth=excluded.auth,
                    session_jti=excluded.session_jti,
                    expires_at=excluded.expires_at,
                    updated_at=excluded.updated_at
                """,
                (
                    subscription.subject,
                    subscription.machine_id,
                    subscription.endpoint,
                    subscription.p256dh,
                    subscription.auth,
                    subscription.session_jti,
                    subscription.expires_at,
                    now,
                    now,
                ),
            )

    async def remove_endpoint(self, subject: str, endpoint: str) -> int:
        return await asyncio.to_thread(self._remove_endpoint, subject, endpoint)

    def _remove_endpoint(self, subject: str, endpoint: str) -> int:
        with self._connect() as db:
            cursor = db.execute(
                "DELETE FROM push_subscriptions WHERE subject=? AND endpoint=?",
                (subject, endpoint),
            )
            return cursor.rowcount

    async def remove_session(self, session_jti: str) -> int:
        return await asyncio.to_thread(self._remove_session, session_jti)

    def _remove_session(self, session_jti: str) -> int:
        with self._connect() as db:
            cursor = db.execute(
                "DELETE FROM push_subscriptions WHERE session_jti=?",
                (session_jti,),
            )
            return cursor.rowcount

    async def remove_subscription(self, subscription: PushSubscription) -> None:
        await asyncio.to_thread(self._remove_subscription, subscription)

    def _remove_subscription(self, subscription: PushSubscription) -> None:
        with self._connect() as db:
            db.execute(
                "DELETE FROM push_subscriptions "
                "WHERE subject=? AND machine_id=? AND endpoint=?",
                (subscription.subject, subscription.machine_id, subscription.endpoint),
            )

    async def for_machine(self, machine_id: str) -> list[PushSubscription]:
        return await asyncio.to_thread(self._for_machine, machine_id)

    def _for_machine(self, machine_id: str) -> list[PushSubscription]:
        now = time.time()
        with self._connect() as db:
            db.execute(
                "DELETE FROM push_subscriptions WHERE expires_at<=?", (now,)
            )
            rows = db.execute(
                "SELECT subject, machine_id, endpoint, p256dh, auth, "
                "session_jti, expires_at FROM push_subscriptions "
                "WHERE machine_id=? AND expires_at>?",
                (machine_id, now),
            ).fetchall()
        return [PushSubscription(*row) for row in rows]


PushSender = Callable[[PushSubscription, str], Awaitable[int | None]]
PushSessionCheck = Callable[[PushSubscription], Awaitable[bool]]
PushOutcome = Literal["success", "failed", "interrupted"]

_PUSH_BODIES: dict[PushOutcome, str] = {
    "success": "远程会话已经完成",
    "failed": "远程会话执行失败",
    "interrupted": "远程会话已中断",
}


class PushDispatcher:
    def __init__(
        self,
        store: PushSubscriptionStore,
        *,
        vapid_private_key: str,
        vapid_subject: str,
        sender: PushSender | None = None,
        session_active: PushSessionCheck | None = None,
    ) -> None:
        self.store = store
        self.vapid_private_key = vapid_private_key
        self.vapid_subject = vapid_subject
        self._sender = sender or self._send_with_pywebpush
        self._session_active = session_active

    async def _send_with_pywebpush(
        self,
        subscription: PushSubscription,
        payload: str,
    ) -> int | None:
        def send() -> int | None:
            from pywebpush import WebPushException, webpush

            try:
                response = webpush(
                    subscription_info=subscription.browser_payload(),
                    data=payload,
                    vapid_private_key=self.vapid_private_key,
                    vapid_claims={"sub": self.vapid_subject},
                    ttl=300,
                )
                return getattr(response, "status_code", None)
            except WebPushException as exc:
                response = getattr(exc, "response", None)
                status = getattr(response, "status_code", None)
                if status in {404, 410}:
                    return status
                raise

        return await asyncio.to_thread(send)

    async def notify_turn_end(
        self,
        machine_id: str,
        *,
        outcome: PushOutcome,
    ) -> None:
        payload = json.dumps(
            {
                "title": "cc-remote",
                "body": _PUSH_BODIES[outcome],
                # A fresh opaque tag prevents a later turn from replacing an
                # earlier notification without exposing machine/session ids to
                # the push provider.
                "tag": f"cc-remote-turn-{secrets.token_hex(8)}",
                "url": "/",
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        subscriptions = await self.store.for_machine(machine_id)
        results = await asyncio.gather(
            *(self._deliver(subscription, payload) for subscription in subscriptions),
            return_exceptions=True,
        )
        failures = sum(isinstance(result, Exception) for result in results)
        if failures:
            log.warning(
                "web push delivery failed",
                machine_id=machine_id,
                failures=failures,
                total=len(results),
            )

    async def _deliver(self, subscription: PushSubscription, payload: str) -> None:
        if (self._session_active is not None
                and not await self._session_active(subscription)):
            await self.store.remove_subscription(subscription)
            return
        status = await self._sender(subscription, payload)
        if status in {404, 410}:
            await self.store.remove_subscription(subscription)
