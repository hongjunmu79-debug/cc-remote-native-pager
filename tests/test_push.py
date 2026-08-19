"""Zero-model tests for durable, privacy-bounded Web Push delivery."""
from __future__ import annotations

import asyncio
import json

from fastapi.testclient import TestClient

from cc_remote.config import RelayConfig
from cc_remote.protocol import TurnResult
from cc_remote.relay.push import (
    PushDispatcher, PushSubscription, PushSubscriptionStore,
)
from cc_remote.relay.server import _turn_push_outcome, create_app


def _cfg(tmp_path, **overrides) -> RelayConfig:
    values = {
        "login_password": "correct horse battery staple",
        "session_secret": "s" * 48,
        "wrapper_token": "w" * 48,
        "public_origin": "https://remote.example",
        "push_vapid_public_key": "B" + "a" * 86,
        "push_vapid_private_key": str(tmp_path / "vapid-private.pem"),
        "push_vapid_subject": "mailto:admin@example.com",
        "push_db_path": str(tmp_path / "push.sqlite3"),
        "device_db_path": str(tmp_path / "devices.sqlite3"),
    }
    values.update(overrides)
    return RelayConfig(**values)


def _subscription(machine_id: str = "nono") -> dict[str, object]:
    return {
        "machine_id": machine_id,
        "endpoint": "https://push.example/send/token",
        "keys": {"p256dh": "p" * 87, "auth": "a" * 22},
    }


def test_push_subscription_requires_auth_and_machine_scope(tmp_path):
    users = json.dumps({
        "alice": {
            "password": "alice correct horse battery staple",
            "machines": ["nono"],
        },
    })
    cfg = _cfg(tmp_path, login_users_json=users)
    app = create_app(cfg)
    with TestClient(app, base_url=cfg.public_origin) as client:
        assert client.get("/api/push-config").status_code == 401
        assert client.post("/api/login", json={
            "username": "alice",
            "password": "alice correct horse battery staple",
        }).status_code == 200
        config = client.get("/api/push-config")
        assert config.json() == {
            "enabled": True, "public_key": cfg.push_vapid_public_key,
        }
        assert client.post(
            "/api/push/subscribe", json=_subscription("mac"),
        ).status_code == 400
        assert client.post(
            "/api/push/subscribe", json=_subscription(),
        ).json() == {"ok": True}

        stored = asyncio.run(app.state.push_store.for_machine("nono"))
        assert len(stored) == 1
        assert stored[0].subject == "alice"
        assert stored[0].session_jti
        assert stored[0].expires_at > 0
        assert client.post("/api/push/unsubscribe", json={
            "endpoint": "https://push.example/send/token",
        }).json() == {"ok": True}
        assert asyncio.run(app.state.push_store.for_machine("nono")) == []


def test_dispatcher_payload_is_generic_and_prunes_expired_endpoint(tmp_path):
    async def run():
        store = PushSubscriptionStore(str(tmp_path / "push.sqlite3"))
        subscription = PushSubscription(
            subject="alice",
            machine_id="nono",
            endpoint="https://push.example/send/token",
            p256dh="p" * 87,
            auth="a" * 22,
            session_jti="j" * 24,
            expires_at=4_102_444_800,
        )
        await store.upsert(subscription)
        payloads: list[str] = []

        async def sender(_subscription, payload):
            payloads.append(payload)
            return 410

        dispatcher = PushDispatcher(
            store,
            vapid_private_key="unused",
            vapid_subject="mailto:admin@example.com",
            sender=sender,
        )
        await dispatcher.notify_turn_end("nono", outcome="success")
        assert len(payloads) == 1
        payload = json.loads(payloads[0])
        assert payload["body"] == "远程会话已经完成"
        assert "session" not in payloads[0].lower()
        assert "nono" not in payloads[0]
        assert await store.for_machine("nono") == []

    asyncio.run(run())


def test_dispatcher_distinguishes_terminal_outcomes_without_tag_collisions(tmp_path):
    async def run():
        store = PushSubscriptionStore(str(tmp_path / "push.sqlite3"))
        await store.upsert(PushSubscription(
            subject="alice",
            machine_id="nono",
            endpoint="https://push.example/send/token",
            p256dh="p" * 87,
            auth="a" * 22,
            session_jti="j" * 24,
            expires_at=4_102_444_800,
        ))
        payloads: list[dict[str, object]] = []

        async def sender(_subscription, payload):
            payloads.append(json.loads(payload))
            return 201

        dispatcher = PushDispatcher(
            store,
            vapid_private_key="unused",
            vapid_subject="mailto:admin@example.com",
            sender=sender,
        )
        for outcome in ("success", "interrupted", "failed"):
            await dispatcher.notify_turn_end("nono", outcome=outcome)

        assert [payload["body"] for payload in payloads] == [
            "远程会话已经完成",
            "远程会话已中断",
            "远程会话执行失败",
        ]
        tags = [str(payload["tag"]) for payload in payloads]
        assert all(tag.startswith("cc-remote-turn-") for tag in tags)
        assert len(set(tags)) == len(tags)
        serialized = json.dumps(payloads, ensure_ascii=False).lower()
        assert "session" not in serialized
        assert "nono" not in serialized

    asyncio.run(run())


def test_turn_push_outcome_preserves_interruptions():
    assert _turn_push_outcome(TurnResult(
        subtype="success", duration_ms=1, is_error=False)) == "success"
    assert _turn_push_outcome(TurnResult(
        subtype="error", duration_ms=1, is_error=True)) == "failed"
    assert _turn_push_outcome(TurnResult(
        subtype="error_during_execution", duration_ms=1,
        is_error=True)) == "interrupted"


def test_logout_removes_only_the_current_session_push_subscription(tmp_path):
    cfg = _cfg(tmp_path)
    app = create_app(cfg)
    with TestClient(app, base_url=cfg.public_origin) as client:
        assert client.post("/api/login", json={
            "password": cfg.login_password,
        }).status_code == 200
        assert client.post(
            "/api/push/subscribe", json=_subscription(),
        ).status_code == 200
        assert len(asyncio.run(
            app.state.push_store.for_machine("nono"))) == 1

        assert client.post("/api/logout").status_code == 200
        assert asyncio.run(
            app.state.push_store.for_machine("nono")) == []


def test_expired_push_subscription_is_not_delivered(tmp_path):
    async def run():
        store = PushSubscriptionStore(str(tmp_path / "push.sqlite3"))
        await store.upsert(PushSubscription(
            subject="alice",
            machine_id="nono",
            endpoint="https://push.example/send/expired",
            p256dh="p" * 87,
            auth="a" * 22,
            session_jti="j" * 24,
            expires_at=1,
        ))
        assert await store.for_machine("nono") == []

    asyncio.run(run())


def test_inactive_login_subscription_is_pruned_before_delivery(tmp_path):
    async def run():
        store = PushSubscriptionStore(str(tmp_path / "push.sqlite3"))
        subscription = PushSubscription(
            subject="alice",
            machine_id="nono",
            endpoint="https://push.example/send/inactive",
            p256dh="p" * 87,
            auth="a" * 22,
            session_jti="j" * 24,
            expires_at=4_102_444_800,
        )
        await store.upsert(subscription)
        deliveries = 0

        async def sender(_subscription, _payload):
            nonlocal deliveries
            deliveries += 1
            return 201

        dispatcher = PushDispatcher(
            store,
            vapid_private_key="unused",
            vapid_subject="mailto:admin@example.com",
            sender=sender,
            session_active=lambda _subscription: asyncio.sleep(
                0, result=False),
        )
        await dispatcher.notify_turn_end("nono", outcome="success")
        assert deliveries == 0
        assert await store.for_machine("nono") == []

    asyncio.run(run())
