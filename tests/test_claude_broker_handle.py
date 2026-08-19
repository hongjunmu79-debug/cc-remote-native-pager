from __future__ import annotations

import asyncio

import pytest

from cc_remote.claude_broker.client import BrokerClientError
from cc_remote.wrapper.claude_broker_handle import ClaudeBrokerHandle


class _Client:
    def __init__(self, metadata=None, error=None):
        self.metadata = metadata
        self.error = error
        self.sent = []
        self.interrupted = []
        self.models = []
        self.efforts = []
        self.permissions = []
        self.context_usage = {
            "totalTokens": 32_400,
            "maxTokens": 1_000_000,
            "percentage": 3.0,
            "model": "claude-opus-4-8[1m]",
            "categories": [],
        }

    async def status(self, _session_id):
        if self.error:
            raise self.error
        return {"ok": True, "session": dict(self.metadata)}

    async def send(self, session_id, text):
        self.sent.append((session_id, text))
        return {"ok": True, "session": dict(self.metadata)}

    async def interrupt(self, session_id):
        self.interrupted.append(session_id)
        return {"ok": True, "session": dict(self.metadata)}

    async def set_model(self, session_id, model):
        self.models.append((session_id, model))
        self.metadata["model"] = model
        return {"ok": True, "session": dict(self.metadata)}

    async def set_effort(self, session_id, effort):
        self.efforts.append((session_id, effort))
        self.metadata["effort"] = effort
        return {"ok": True, "session": dict(self.metadata)}

    async def set_permission_mode(self, session_id, mode):
        self.permissions.append((session_id, mode))
        self.metadata["permission_mode"] = mode
        return {"ok": True, "session": dict(self.metadata)}

    async def get_context_usage(self, session_id):
        self.metadata["model"] = self.context_usage["model"]
        return {
            "ok": True,
            "session": dict(self.metadata),
            "context_usage": dict(self.context_usage),
        }


def _metadata():
    return {
        "id": "11111111-1111-4111-8111-111111111111",
        "generation": "generation-1",
        "cwd": "/tmp/project",
        "running": True,
        "model": "claude-sonnet-4-5",
        "effort": "high",
        "permission_mode": "bypassPermissions",
    }


def test_discovers_exact_running_session_and_never_stops_it_on_disconnect():
    async def go():
        client = _Client(_metadata())
        handle = await ClaudeBrokerHandle.discover(client, _metadata()["id"])
        assert handle is not None
        await handle.connect(_metadata()["id"], "/tmp/project")
        await handle.submit("hello")
        await handle.interrupt()
        await handle.disconnect()
        assert client.sent == [(_metadata()["id"], "hello")]
        assert client.interrupted == [_metadata()["id"]]

    asyncio.run(go())


def test_missing_broker_or_session_is_not_an_error_but_identity_drift_is():
    async def go():
        unavailable = _Client(error=BrokerClientError(
            "broker_unavailable", "missing"))
        assert await ClaudeBrokerHandle.discover(
            unavailable, _metadata()["id"]) is None

        metadata = _metadata()
        client = _Client(metadata)
        handle = await ClaudeBrokerHandle.discover(client, metadata["id"])
        assert handle is not None
        client.metadata = {**metadata, "generation": "generation-2"}
        with pytest.raises(BrokerClientError, match="identity changed"):
            await handle.refresh_status()

    asyncio.run(go())


def test_connect_rejects_a_status_response_for_another_session():
    async def go():
        metadata = _metadata()
        client = _Client(metadata)
        handle = await ClaudeBrokerHandle.discover(client, metadata["id"])
        assert handle is not None
        client.metadata = {
            **metadata,
            "id": "22222222-2222-4222-8222-222222222222",
        }
        with pytest.raises(BrokerClientError, match="identity changed"):
            await handle.connect(metadata["id"], metadata["cwd"])

    asyncio.run(go())


def test_controls_use_broker_confirmation_and_adopt_exact_runtime_state():
    async def go():
        metadata = _metadata()
        client = _Client(metadata)
        handle = await ClaudeBrokerHandle.discover(client, metadata["id"])
        assert handle is not None

        await handle.set_model("claude-opus-4-1")
        await handle.set_effort("max")
        await handle.set_permission_mode("default")

        assert client.models == [(metadata["id"], "claude-opus-4-1")]
        assert client.efforts == [(metadata["id"], "max")]
        assert client.permissions == [(metadata["id"], "default")]
        assert handle.model == "claude-opus-4-1"
        assert handle.effort == handle.applied_effort == "max"
        assert handle.permission_mode == "default"

    asyncio.run(go())


def test_control_does_not_accept_metadata_that_omits_the_requested_change():
    class StaleClient(_Client):
        async def set_permission_mode(self, session_id, mode):
            self.permissions.append((session_id, mode))
            return {"ok": True, "session": dict(self.metadata)}

    async def go():
        metadata = _metadata()
        client = StaleClient(metadata)
        handle = await ClaudeBrokerHandle.discover(client, metadata["id"])
        assert handle is not None
        with pytest.raises(BrokerClientError) as rejected:
            await handle.set_permission_mode("default")
        assert rejected.value.code == "control_unconfirmed"
        assert handle.permission_mode == "bypassPermissions"

    asyncio.run(go())


def test_control_rejects_confirmation_from_another_broker_generation():
    class WrongGenerationClient(_Client):
        async def set_model(self, session_id, model):
            self.models.append((session_id, model))
            return {"ok": True, "session": {
                **self.metadata,
                "generation": "generation-2",
                "model": model,
            }}

    async def go():
        metadata = _metadata()
        client = WrongGenerationClient(metadata)
        handle = await ClaudeBrokerHandle.discover(client, metadata["id"])
        assert handle is not None
        with pytest.raises(BrokerClientError) as stale:
            await handle.set_model("claude-opus-4-1")
        assert stale.value.code == "stale_generation"
        assert handle.model == "claude-sonnet-4-5"

    asyncio.run(go())


def test_context_query_returns_structured_usage_and_refreshes_model():
    async def go():
        metadata = _metadata()
        client = _Client(metadata)
        handle = await ClaudeBrokerHandle.discover(client, metadata["id"])
        assert handle is not None

        usage = await handle.get_context_usage()

        assert usage["totalTokens"] == 32_400
        assert usage["maxTokens"] == 1_000_000
        assert handle.model == "claude-opus-4-8[1m]"

    asyncio.run(go())
