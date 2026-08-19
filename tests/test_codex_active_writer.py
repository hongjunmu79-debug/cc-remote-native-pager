"""Zero-token regressions for Codex Desktop writer handoff."""
from __future__ import annotations

import asyncio

from cc_remote.wrapper.codex_handle import (
    CodexActiveWriterError,
    _is_active_writer_error,
)
from tests.test_multisession import _mk_ctx, _mk_machine


class _WriterLeaseSdk:
    shared_daemon_affinity = True
    thread_id = "sid"

    def __init__(self) -> None:
        self.released = False
        self.connects = 0

    @property
    def using_daemon_proxy(self) -> bool:
        return self.released

    async def connect(self, **_kwargs) -> None:
        self.connects += 1
        if not self.released:
            raise CodexActiveWriterError("already has an active writer")


def test_json_rpc_active_writer_error_is_typed_narrowly():
    assert _is_active_writer_error({
        "code": -32600,
        "message": "thread sid already has an active writer",
    })
    assert not _is_active_writer_error({
        "code": -32600,
        "message": "invalid request",
    })
    assert not _is_active_writer_error("already has an active writer")


def test_writer_conflict_stays_read_only_then_auto_recovers():
    async def run() -> None:
        machine, transport = _mk_machine()
        ctx = _mk_ctx("sid", "sid")
        ctx.engine = "codex"
        ctx.space = "code"
        ctx.sdk = _WriterLeaseSdk()
        machine.sessions[ctx.key] = ctx

        async def mirror(_sid: str) -> None:
            return None

        machine._push_mirrored_history = mirror
        await machine._mark_codex_writer_blocked(ctx, emit=False)
        assert machine._is_external("sid") is True
        assert ctx.control_mode == "desktop"
        assert ctx.write_state == "read_only"

        assert await machine._retry_codex_writer_attach(ctx, force=True) is False
        assert ctx.sdk.connects == 1
        assert ctx.codex_writer_blocked is True
        assert ctx.write_state == "read_only"

        ctx.sdk.released = True
        assert await machine._retry_codex_writer_attach(ctx, force=True) is True
        assert ctx.sdk.connects == 2
        assert ctx.codex_writer_blocked is False
        assert ctx.control_mode == "codex_shared"
        assert ctx.write_state == "writable"
        assert any(event.type == "takeover_state" for event in transport.sent)

    asyncio.run(run())
