"""Persistent device enrollment for multi-machine wrappers.

The relay stores only SHA-256 digests of high-entropy wrapper credentials and
one-time pairing codes.  Conversation data and wrapper configuration remain on
the enrolled machine.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import secrets
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

from cc_remote.config import valid_machine_id


_PAIR_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"


def _digest(value: str) -> bytes:
    return hashlib.sha256(value.encode("utf-8")).digest()


def _pairing_code() -> str:
    raw = "".join(secrets.choice(_PAIR_ALPHABET) for _ in range(20))
    return "-".join(raw[index:index + 5] for index in range(0, 20, 5))


def normalize_pairing_code(value: str) -> str:
    return "".join(char for char in value.upper() if char in _PAIR_ALPHABET)


def _route_id() -> str:
    return f"device-{secrets.token_hex(8)}"


@dataclass(frozen=True)
class DeviceRecord:
    machine_id: str
    subject: str
    label: str
    platform: str
    hostname: str
    created_at: int
    last_seen: int | None
    revoked_at: int | None


@dataclass(frozen=True)
class PairingGrant:
    code: str
    expires_at: int


@dataclass(frozen=True)
class EnrolledDevice:
    machine_id: str
    token: str
    label: str


class DeviceStore:
    """Small SQLite registry shared by HTTP enrollment and WS authentication."""

    def __init__(self, path: str):
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()
        os.chmod(self.path, 0o600)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=10000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS devices (
                    machine_id TEXT PRIMARY KEY,
                    subject TEXT NOT NULL,
                    label TEXT NOT NULL,
                    platform TEXT NOT NULL,
                    hostname TEXT NOT NULL,
                    token_hash BLOB NOT NULL UNIQUE,
                    created_at INTEGER NOT NULL,
                    last_seen INTEGER,
                    revoked_at INTEGER
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS devices_subject_idx "
                "ON devices(subject, revoked_at)"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS pairing_codes (
                    subject TEXT PRIMARY KEY,
                    code_hash BLOB NOT NULL UNIQUE,
                    created_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL
                )
                """
            )

    async def create_pairing(
        self,
        subject: str,
        *,
        ttl: int = 600,
        now: int | None = None,
    ) -> PairingGrant:
        return await asyncio.to_thread(
            self._create_pairing, subject, ttl, int(time.time() if now is None else now)
        )

    def _create_pairing(self, subject: str, ttl: int, now: int) -> PairingGrant:
        code = _pairing_code()
        expires_at = now + ttl
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO pairing_codes(subject, code_hash, created_at, expires_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(subject) DO UPDATE SET
                    code_hash=excluded.code_hash,
                    created_at=excluded.created_at,
                    expires_at=excluded.expires_at
                """,
                (subject, _digest(normalize_pairing_code(code)), now, expires_at),
            )
        return PairingGrant(code=code, expires_at=expires_at)

    async def close_pairing(self, subject: str) -> None:
        await asyncio.to_thread(self._close_pairing, subject)

    def _close_pairing(self, subject: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM pairing_codes WHERE subject=?", (subject,))

    async def pairing_expires_at(self, subject: str, *, now: int | None = None) -> int | None:
        return await asyncio.to_thread(
            self._pairing_expires_at, subject, int(time.time() if now is None else now)
        )

    def _pairing_expires_at(self, subject: str, now: int) -> int | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT expires_at FROM pairing_codes WHERE subject=?", (subject,)
            ).fetchone()
            if row is None:
                return None
            expires_at = int(row["expires_at"])
            if expires_at <= now:
                connection.execute("DELETE FROM pairing_codes WHERE subject=?", (subject,))
                return None
            return expires_at

    async def redeem(
        self,
        code: str,
        *,
        label: str,
        platform: str,
        hostname: str,
        now: int | None = None,
    ) -> EnrolledDevice | None:
        return await asyncio.to_thread(
            self._redeem,
            normalize_pairing_code(code),
            label,
            platform,
            hostname,
            int(time.time() if now is None else now),
        )

    def _redeem(
        self,
        code: str,
        label: str,
        platform: str,
        hostname: str,
        now: int,
    ) -> EnrolledDevice | None:
        if len(code) != 20:
            return None
        token = secrets.token_urlsafe(48)
        machine_id = _route_id()
        assert valid_machine_id(machine_id)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT subject, expires_at FROM pairing_codes WHERE code_hash=?",
                (_digest(code),),
            ).fetchone()
            if row is None or int(row["expires_at"]) <= now:
                connection.rollback()
                return None
            subject = str(row["subject"])
            active_count = int(connection.execute(
                "SELECT COUNT(*) FROM devices "
                "WHERE subject=? AND revoked_at IS NULL",
                (subject,),
            ).fetchone()[0])
            if active_count >= 64:
                connection.rollback()
                return None
            connection.execute("DELETE FROM pairing_codes WHERE subject=?", (subject,))
            connection.execute(
                """
                INSERT INTO devices(
                    machine_id, subject, label, platform, hostname, token_hash,
                    created_at, last_seen, revoked_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL)
                """,
                (
                    machine_id,
                    subject,
                    label,
                    platform,
                    hostname,
                    _digest(token),
                    now,
                ),
            )
            connection.commit()
            return EnrolledDevice(machine_id=machine_id, token=token, label=label)
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    async def machine_for_token(self, token: str) -> str | None:
        return await asyncio.to_thread(self._machine_for_token, token)

    def _machine_for_token(self, token: str) -> str | None:
        if len(token) < 32:
            return None
        with self._connect() as connection:
            row = connection.execute(
                "SELECT machine_id FROM devices "
                "WHERE token_hash=? AND revoked_at IS NULL",
                (_digest(token),),
            ).fetchone()
        return str(row["machine_id"]) if row is not None else None

    async def touch_seen(self, machine_id: str, *, now: int | None = None) -> None:
        await asyncio.to_thread(
            self._touch_seen, machine_id, int(time.time() if now is None else now)
        )

    def _touch_seen(self, machine_id: str, now: int) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE devices SET last_seen=? WHERE machine_id=? AND revoked_at IS NULL",
                (now, machine_id),
            )

    async def list_for_subject(self, subject: str) -> list[DeviceRecord]:
        return await asyncio.to_thread(self._list_for_subject, subject)

    def _list_for_subject(self, subject: str) -> list[DeviceRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT machine_id, subject, label, platform, hostname,
                       created_at, last_seen, revoked_at
                FROM devices
                WHERE subject=? AND revoked_at IS NULL
                ORDER BY COALESCE(last_seen, created_at) DESC, machine_id
                """,
                (subject,),
            ).fetchall()
        return [DeviceRecord(**dict(row)) for row in rows]

    async def owned_by(self, machine_id: str, subject: str) -> bool:
        return await asyncio.to_thread(self._owned_by, machine_id, subject)

    def _owned_by(self, machine_id: str, subject: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM devices "
                "WHERE machine_id=? AND subject=? AND revoked_at IS NULL",
                (machine_id, subject),
            ).fetchone()
        return row is not None

    async def rename(self, machine_id: str, subject: str, label: str) -> bool:
        return await asyncio.to_thread(self._rename, machine_id, subject, label)

    def _rename(self, machine_id: str, subject: str, label: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE devices SET label=? "
                "WHERE machine_id=? AND subject=? AND revoked_at IS NULL",
                (label, machine_id, subject),
            )
        return cursor.rowcount == 1

    async def revoke(self, machine_id: str, subject: str) -> bool:
        return await asyncio.to_thread(self._revoke, machine_id, subject)

    def _revoke(self, machine_id: str, subject: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM devices "
                "WHERE machine_id=? AND subject=? AND revoked_at IS NULL",
                (machine_id, subject),
            )
        return cursor.rowcount == 1
