"""Zero-token regressions for shared Codex daemon ownership semantics."""
from __future__ import annotations

import asyncio
import json

from cc_remote.protocol import (
    Effort, Error, Model, Query, SessionActivity, SessionControl, Takeover,
)
from cc_remote.wrapper.codex_external import HolderScan, ProcessIdentity
from tests.test_codex_external import _CodexSdk, _record_async, _watch
from tests.test_multisession import _mk_ctx, _mk_machine


def _event(kind: str, turn_id: str) -> bytes:
    return (json.dumps({
        "type": "event_msg",
        "payload": {"type": kind, "turn_id": turn_id},
    }) + "\n").encode()


def _user_event(message: str) -> bytes:
    return (json.dumps({
        "type": "event_msg",
        "payload": {"type": "user_message", "message": message},
    }) + "\n").encode()


class _SharedSdk(_CodexSdk):
    using_daemon_proxy = True
    model = "gpt-test"
    effort = "high"
    applied_effort = "high"
    service_tier = None
    collaboration_mode = "default"

    def __init__(self) -> None:
        super().__init__()
        self.queries: list[tuple[str, list[str] | None]] = []
        self.reconnects = 0

    async def query(self, prompt: str, images=None) -> None:
        self.queries.append((prompt, images))

    async def receive_response(self):
        yield {
            "method": "turn/completed",
            "params": {
                "threadId": "sid",
                "turn": {"id": "turn-1", "status": "completed"},
            },
        }

    async def force_reconnect(self, *_args, **_kwargs) -> None:
        self.reconnects += 1


class _InterruptedSharedSdk(_SharedSdk):
    shared_daemon_affinity = True

    def __init__(self) -> None:
        super().__init__()
        self.live = False

    @property
    def using_daemon_proxy(self) -> bool:
        return self.live

    async def force_reconnect(self, *_args, **_kwargs) -> None:
        self.reconnects += 1
        self.live = True


def test_shared_code_watcher_mirrors_growth_without_legacy_lock(tmp_path):
    async def go() -> None:
        machine, _transport = _mk_machine()
        path = tmp_path / "rollout.jsonl"
        path.write_bytes(b"")
        ctx = _mk_ctx("sid", "sid")
        ctx.engine = "codex"
        ctx.space = "code"
        ctx.sdk = _SharedSdk()
        # A stale legacy bit from an earlier stdio generation must be retired by
        # the first authoritative shared-daemon poll.
        ctx.needs_reload = True
        machine.sessions[ctx.key] = ctx
        watch = _watch(path)
        watch.update({
            "external": True,
            "active_external_turns": {"old-terminal-turn": 1.0},
            "pending_wrapper_turns": {
                "unattributed-turn": {"seen_at": 1.0},
            },
        })
        machine._watch["sid"] = watch
        mirrored: list[str] = []
        refreshed: list[str] = []

        async def mirror(sid: str):
            mirrored.append(sid)

        async def refresh(session_ctx):
            refreshed.append(session_ctx.session_id)

        machine._push_mirrored_history = mirror
        machine._refresh_codex_collaboration_mode = refresh
        holder = ProcessIdentity(101, 1001)
        path.write_bytes(
            _event("task_started", "terminal-visible")
            + _event("task_complete", "terminal-visible")
        )

        await machine._poll_codex_watch(
            "sid", watch, {holder}, 1000.0, writers={holder}
        )

        assert mirrored == ["sid"]
        assert refreshed == ["sid"]
        assert watch["external"] is False
        assert watch["takeover_pending"] is None
        assert watch["active_external_turns"] == {}
        assert watch["pending_wrapper_turns"] == {}
        assert ctx.needs_reload is False
        assert ctx.control_mode == "codex_shared"
        assert ctx.write_state == "writable"
        assert ctx.terminal_attached is True
        assert ctx.control_can_takeover is False

    asyncio.run(go())


def test_shared_code_distinguishes_private_app_turn_from_shared_cli(tmp_path):
    async def go() -> None:
        machine, transport = _mk_machine()
        path = tmp_path / "rollout.jsonl"
        path.write_bytes(b"")
        ctx = _mk_ctx("sid", "sid")
        ctx.engine = "codex"
        ctx.space = "code"
        ctx.sdk = _SharedSdk()
        machine.sessions[ctx.key] = ctx
        watch = _watch(path)
        machine._watch["sid"] = watch

        with path.open("ab") as stream:
            stream.write(_event("task_started", "app-turn"))
        await machine._poll_codex_watch(
            "sid", watch, set(), 1000.0, writers=set()
        )

        assert watch["active_external_turns"] == {"app-turn": 1000.0}
        assert watch["desktop_active"] is True
        assert watch["external"] is True
        assert ctx.control_mode == "desktop"
        assert ctx.write_state == "read_only"
        assert ctx.control_can_takeover is False
        assert any(
            isinstance(event, SessionActivity)
            and event.session_id == "sid"
            and event.state == "running"
            for event in transport.sent
        )

        with path.open("ab") as stream:
            stream.write(_event("task_complete", "app-turn"))
        await machine._poll_codex_watch(
            "sid", watch, set(), 1001.0, writers=set()
        )

        assert watch["active_external_turns"] == {}
        assert watch["desktop_active"] is False
        assert watch["external"] is False
        assert ctx.control_mode == "codex_shared"
        assert ctx.write_state == "writable"
        assert any(
            isinstance(event, SessionActivity)
            and event.session_id == "sid"
            and event.state == "idle"
            for event in transport.sent
        )

        with path.open("ab") as stream:
            stream.write(_event("task_started", "cli-turn"))
        holder = ProcessIdentity(101, 1001)
        await machine._poll_codex_watch(
            "sid", watch, {holder}, 1002.0, writers={holder}
        )

        assert watch["active_external_turns"] == {"cli-turn": 1002.0}
        assert watch["desktop_active"] is False
        assert watch["external"] is False
        assert ctx.control_mode == "codex_shared"
        assert ctx.write_state == "writable"
        assert ctx.terminal_attached is True

    asyncio.run(go())


def test_shared_context_attach_preserves_running_private_app_turn(tmp_path):
    """Opening Remote mid-App turn must not manufacture an idle/writable state."""
    async def go() -> None:
        machine, _transport = _mk_machine()
        path = tmp_path / "rollout.jsonl"
        path.write_bytes(_event("task_started", "app-turn"))
        watch = _watch(path)
        watch.update({
            "external": True,
            "desktop_active": True,
            "active_external_turns": {"app-turn": 1000.0},
        })
        machine._watch["sid"] = watch

        # The sidebar watcher discovered the native App turn before Remote
        # focused the session. Focusing creates a shared-daemon context, but
        # that new passive connection does not own or finish the App turn.
        ctx = _mk_ctx("sid", "sid")
        ctx.engine = "codex"
        ctx.space = "code"
        ctx.sdk = _SharedSdk()
        machine.sessions[ctx.key] = ctx
        private = ProcessIdentity(109, 1009)

        await machine._poll_codex_watch(
            "sid",
            watch,
            set(),
            1001.0,
            writers={private},
            private_holders={private},
        )

        assert watch["active_external_turns"] == {"app-turn": 1000.0}
        assert watch["desktop_active"] is True
        assert watch["external"] is True
        assert ctx.control_mode == "desktop"
        assert ctx.write_state == "read_only"
        assert ctx.terminal_attached is True

    asyncio.run(go())


def test_shared_cli_user_message_refreshes_after_earlier_task_start(tmp_path):
    async def go() -> None:
        machine, _transport = _mk_machine()
        path = tmp_path / "rollout.jsonl"
        path.write_bytes(b"")
        ctx = _mk_ctx("sid", "sid")
        ctx.engine = "codex"
        ctx.space = "code"
        ctx.sdk = _SharedSdk()
        machine.sessions[ctx.key] = ctx
        watch = _watch(path)
        machine._watch["sid"] = watch
        mirrored: list[str] = []
        machine._push_mirrored_history = lambda sid: _record_async(
            mirrored, sid)
        holder = ProcessIdentity(101, 1001)

        # The real CLI flushes task_started first. That first refresh cannot
        # contain the prompt yet.
        with path.open("ab") as stream:
            stream.write(_event("task_started", "cli-turn"))
        await machine._poll_codex_watch(
            "sid", watch, {holder}, 1000.0, writers={holder})
        assert mirrored == ["sid"]

        # user_message arrives in a separate append and must independently
        # refresh the open browser instead of waiting for a session switch.
        with path.open("ab") as stream:
            stream.write(_user_event("在？测试测试"))
        await machine._poll_codex_watch(
            "sid", watch, {holder}, 1000.1, writers={holder})

        assert mirrored == ["sid", "sid"]
        assert watch["external"] is False
        assert ctx.control_mode == "codex_shared"
        assert ctx.write_state == "writable"

    asyncio.run(go())


def test_shared_rollout_tail_cannot_revert_live_app_server_settings(
    monkeypatch,
):
    async def go() -> None:
        machine, transport = _mk_machine()
        ctx = _mk_ctx("sid", "sid")
        ctx.engine = "codex"
        ctx.space = "code"
        ctx.sdk = _SharedSdk()
        ctx.sdk.model = "gpt-live"
        ctx.sdk.effort = "ultra"
        ctx.sdk.applied_effort = "ultra"
        ctx.announced_model = "gpt-live"
        ctx.announced_effort = "ultra"
        machine.sessions[ctx.key] = ctx

        # A model switch updates the shared app-server immediately, but Codex
        # does not append the new turn_context until the next turn starts.  The
        # bounded rollout tail therefore still contains the previous model.
        monkeypatch.setattr(
            "cc_remote.wrapper.machine.codex_session_settings",
            lambda *_args, **_kwargs: {
                "model": "gpt-stale-rollout",
                "effort": "low",
            },
        )

        await machine._refresh_codex_collaboration_mode(ctx)

        assert ctx.sdk.model == "gpt-live"
        assert ctx.sdk.effort == "ultra"
        assert not [
            event for event in transport.sent
            if isinstance(event, (Model, Effort))
        ]

        # If Web was behind, publish the live app-server values rather than the
        # stale rollout values.
        ctx.announced_model = "gpt-old-ui"
        ctx.announced_effort = "low"
        await machine._refresh_codex_collaboration_mode(ctx)

        assert ctx.announced_model == "gpt-live"
        assert ctx.announced_effort == "ultra"
        assert [
            (event.type, getattr(event, "model", None),
             getattr(event, "effort", None))
            for event in transport.sent
            if isinstance(event, (Model, Effort))
        ] == [
            ("model", "gpt-live", None),
            ("effort", None, "ultra"),
        ]

    asyncio.run(go())


def test_shared_headless_backends_do_not_report_terminal_attached(tmp_path):
    async def go() -> None:
        machine, _transport = _mk_machine()
        path = tmp_path / "rollout.jsonl"
        path.write_bytes(b"")
        ctx = _mk_ctx("sid", "sid")
        ctx.engine = "codex"
        ctx.space = "code"
        ctx.sdk = _SharedSdk()
        machine.sessions[ctx.key] = ctx
        watch = _watch(path)
        machine._watch[ctx.session_id] = watch
        daemon = ProcessIdentity(102, 1002)
        stdio_proxy = ProcessIdentity(103, 1003)
        passive = {daemon, stdio_proxy}
        scan = HolderScan(
            {ctx.session_id: passive},
            True,
            {ctx.session_id: passive},
        )

        holders, writers, private_holders = machine._codex_holder_sets(
            watch, scan, ctx.session_id)
        await machine._poll_codex_watch(
            ctx.session_id,
            watch,
            holders,
            1000.0,
            writers=writers,
            private_holders=private_holders,
        )

        assert holders == set()
        assert writers == passive
        assert ctx.control_mode == "codex_shared"
        assert ctx.write_state == "writable"
        assert ctx.terminal_attached is False

    asyncio.run(go())


def test_shared_private_app_holder_is_informational_while_idle(tmp_path):
    async def go() -> None:
        machine, _transport = _mk_machine()
        path = tmp_path / "rollout.jsonl"
        path.write_bytes(b"")
        ctx = _mk_ctx("sid", "sid")
        ctx.engine = "codex"
        ctx.space = "code"
        ctx.sdk = _SharedSdk()
        machine.sessions[ctx.key] = ctx
        watch = _watch(path)
        machine._watch[ctx.session_id] = watch
        managed = ProcessIdentity(105, 1005)
        private = ProcessIdentity(106, 1006)
        scan = HolderScan(
            {ctx.session_id: {managed, private}},
            True,
            {ctx.session_id: {managed, private}},
            {},
            {ctx.session_id: {private}},
        )

        holders, writers, private_holders = machine._codex_holder_sets(
            watch, scan, ctx.session_id)
        await machine._poll_codex_watch(
            ctx.session_id,
            watch,
            holders,
            1000.0,
            writers=writers,
            private_holders=private_holders,
        )

        assert holders == set()
        assert writers == {managed, private}
        assert watch["private_app_loaded"] is True
        assert watch["desktop_active"] is False
        assert watch["external"] is False
        assert ctx.control_mode == "codex_shared"
        assert ctx.write_state == "writable"
        assert ctx.needs_reload is False

        await machine._poll_codex_watch(
            ctx.session_id,
            watch,
            set(),
            1001.0,
            writers={managed},
            private_holders=set(),
        )

        assert watch["private_app_loaded"] is False
        assert watch["external"] is False
        assert ctx.control_mode == "codex_shared"
        assert ctx.write_state == "writable"
        assert ctx.needs_reload is False

    asyncio.run(go())


def test_stdio_private_app_holder_is_informational_while_idle(tmp_path):
    async def go() -> None:
        machine, _transport = _mk_machine()
        path = tmp_path / "rollout.jsonl"
        path.write_bytes(b"")
        ctx = _mk_ctx("sid", "sid")
        ctx.engine = "codex"
        ctx.space = "code"
        ctx.sdk = _CodexSdk()
        machine.sessions[ctx.key] = ctx
        watch = _watch(path)
        machine._watch[ctx.session_id] = watch
        private = ProcessIdentity(107, 1007)

        await machine._poll_codex_watch(
            ctx.session_id,
            watch,
            set(),
            1000.0,
            writers={private},
            private_holders={private},
        )

        assert watch["private_app_loaded"] is True
        assert watch["desktop_active"] is False
        assert watch["external"] is False
        assert ctx.control_mode == "remote"
        assert ctx.write_state == "writable"
        assert ctx.control_can_takeover is False

    asyncio.run(go())


def test_private_app_idle_client_does_not_block_remote_query(
        tmp_path, monkeypatch):
    async def go() -> None:
        machine, _transport = _mk_machine()
        ctx = _mk_ctx("sid", "sid")
        ctx.engine = "codex"
        ctx.space = "code"
        ctx.sdk = _SharedSdk()
        machine.sessions[ctx.key] = ctx
        path = tmp_path / "rollout.jsonl"
        path.write_bytes(b"")
        watch = _watch(path)
        watch.update({"external": False, "private_app_loaded": True})
        machine._watch[ctx.session_id] = watch
        monkeypatch.setattr(machine, "_watch_session", lambda _sid: None)

        async def refresh_idle_app(_sid: str) -> bool:
            return False

        ran: list[tuple[str, str]] = []

        async def fake_turn(session_ctx, prompt, _images=None, _files=None):
            ran.append((session_ctx.session_id, prompt))
            await machine._set_state(session_ctx, "idle")

        monkeypatch.setattr(
            machine, "_prime_codex_ownership", refresh_idle_app)
        monkeypatch.setattr(machine, "_run_turn", fake_turn)

        result = await machine._handle_query(Query(
            sid="sid", prompt="remote-owned", msg_id="private-app-query"
        ))

        assert result is None
        assert ctx.turn_task is not None
        await ctx.turn_task
        assert ran == [("sid", "remote-owned")]
        assert ctx.state == "idle"

    asyncio.run(go())


def test_remote_owned_turn_mirrored_into_private_app_stays_writable(tmp_path):
    async def go() -> None:
        machine, _transport = _mk_machine()
        path = tmp_path / "rollout.jsonl"
        path.write_bytes(b"")
        ctx = _mk_ctx("sid", "sid")
        ctx.engine = "codex"
        ctx.space = "code"
        ctx.sdk = _SharedSdk()
        ctx.sdk.remember_owned_turn_id("remote-turn")
        machine.sessions[ctx.key] = ctx
        watch = _watch(path)
        machine._watch[ctx.session_id] = watch
        private = ProcessIdentity(108, 1008)

        with path.open("ab") as stream:
            stream.write(_event("task_started", "remote-turn"))
        await machine._poll_codex_watch(
            ctx.session_id,
            watch,
            set(),
            1000.0,
            writers={private},
            private_holders={private},
        )

        assert watch["private_app_loaded"] is True
        assert watch["active_external_turns"] == {}
        assert watch["desktop_active"] is False
        assert watch["external"] is False
        assert ctx.control_mode == "codex_shared"
        assert ctx.write_state == "writable"

    asyncio.run(go())


def test_private_app_foreign_active_turn_is_read_only(tmp_path):
    async def go() -> None:
        machine, _transport = _mk_machine()
        path = tmp_path / "rollout.jsonl"
        path.write_bytes(b"")
        ctx = _mk_ctx("sid", "sid")
        ctx.engine = "codex"
        ctx.space = "code"
        ctx.sdk = _SharedSdk()
        machine.sessions[ctx.key] = ctx
        watch = _watch(path)
        machine._watch[ctx.session_id] = watch
        private = ProcessIdentity(109, 1009)

        with path.open("ab") as stream:
            stream.write(_event("task_started", "app-turn"))
        await machine._poll_codex_watch(
            ctx.session_id,
            watch,
            set(),
            1000.0,
            writers={private},
            private_holders={private},
        )

        assert watch["active_external_turns"] == {"app-turn": 1000.0}
        assert watch["desktop_active"] is True
        assert watch["external"] is True
        assert ctx.control_mode == "desktop"
        assert ctx.write_state == "read_only"
        assert ctx.control_can_takeover is False

    asyncio.run(go())


def test_shared_terminal_exit_clears_attachment_on_next_complete_scan(tmp_path):
    async def go() -> None:
        machine, transport = _mk_machine()
        path = tmp_path / "rollout.jsonl"
        path.write_bytes(b"")
        ctx = _mk_ctx("sid", "sid")
        ctx.engine = "codex"
        ctx.space = "code"
        ctx.sdk = _SharedSdk()
        machine.sessions[ctx.key] = ctx
        watch = _watch(path)
        machine._watch[ctx.session_id] = watch
        tui = ProcessIdentity(104, 1004)

        await machine._poll_codex_watch(
            ctx.session_id, watch, {tui}, 1000.0, writers={tui},
            ownership_scan_complete=True,
        )
        attached_revision = ctx.control_revision
        assert ctx.terminal_attached is True

        # The next complete /proc scan is authoritative. The shared daemon and
        # this wrapper's proxy may remain alive, but an exited TUI must not leave
        # a sticky terminal badge or require a transcript append to clear it.
        await machine._poll_codex_watch(
            ctx.session_id, watch, set(), 1001.5, writers=set(),
            ownership_scan_complete=True,
        )

        assert watch["holders"] == set()
        assert ctx.control_mode == "codex_shared"
        assert ctx.write_state == "writable"
        assert ctx.terminal_attached is False
        assert ctx.control_revision == attached_revision + 1
        controls = [
            event for event in transport.sent
            if isinstance(event, SessionControl)
        ]
        assert [event.terminal_attached for event in controls[-2:]] == [
            True, False,
        ]

    asyncio.run(go())


def test_shared_code_query_refreshes_activity_without_locking_cli(
    tmp_path, monkeypatch,
):
    async def go() -> None:
        machine, _transport = _mk_machine()
        ctx = _mk_ctx("sid", "sid")
        ctx.engine = "codex"
        ctx.space = "code"
        ctx.sdk = _SharedSdk()
        machine.sessions[ctx.key] = ctx
        path = tmp_path / "rollout.jsonl"
        path.write_bytes(b"")
        watch = _watch(path)
        watch.update({
            "external": True,
            "holders": {ProcessIdentity(111, 1101)},
            "writers": {ProcessIdentity(111, 1101)},
        })
        machine._watch["sid"] = watch
        monkeypatch.setattr(machine, "_watch_session", lambda _sid: None)

        activity_probes: list[str] = []

        async def refresh_activity(probe_sid: str) -> bool:
            activity_probes.append(probe_sid)
            return False

        ran: list[tuple[str, str]] = []

        async def fake_turn(session_ctx, prompt, _images=None, _files=None):
            ran.append((session_ctx.session_id, prompt))
            await machine._set_state(session_ctx, "idle")

        monkeypatch.setattr(
            machine, "_prime_codex_ownership", refresh_activity
        )
        monkeypatch.setattr(machine, "_run_turn", fake_turn)

        result = await machine._handle_query(Query(
            sid="sid", prompt="hello", msg_id="shared-query"
        ))
        assert result is None
        assert ctx.turn_task is not None
        await ctx.turn_task
        assert activity_probes == ["sid"]
        assert ran == [("sid", "hello")]
        assert ctx.state == "idle"

    asyncio.run(go())


def test_interrupted_shared_query_reconnects_and_refreshes_activity(
    tmp_path, monkeypatch,
):
    async def go() -> None:
        machine, _transport = _mk_machine()
        ctx = _mk_ctx("sid", "sid")
        ctx.engine = "codex"
        ctx.space = "code"
        ctx.sdk = _InterruptedSharedSdk()
        machine.sessions[ctx.key] = ctx
        path = tmp_path / "rollout.jsonl"
        path.write_bytes(b"")
        watch = _watch(path)
        watch.update({
            "external": True,
            "holders": {ProcessIdentity(111, 1101)},
            "writers": {ProcessIdentity(111, 1101)},
        })
        machine._watch["sid"] = watch
        monkeypatch.setattr(machine, "_watch_session", lambda _sid: None)

        activity_probes: list[str] = []

        async def refresh_activity(probe_sid: str) -> bool:
            activity_probes.append(probe_sid)
            return False

        ran: list[tuple[str, str]] = []

        async def fake_turn(session_ctx, prompt, _images=None, _files=None):
            ran.append((session_ctx.session_id, prompt))
            await machine._set_state(session_ctx, "idle")

        monkeypatch.setattr(
            machine, "_prime_codex_ownership", refresh_activity
        )
        monkeypatch.setattr(machine, "_run_turn", fake_turn)

        result = await machine._handle_query(Query(
            sid="sid", prompt="hello", msg_id="shared-reconnect-query"
        ))
        assert result is None
        assert ctx.turn_task is not None
        await ctx.turn_task
        assert ctx.sdk.reconnects == 1
        assert activity_probes == ["sid"]
        assert ctx.sdk.using_daemon_proxy is True
        assert ran == [("sid", "hello")]
        assert ctx.control_mode == "codex_shared"
        assert ctx.write_state == "writable"
        assert ctx.control_can_takeover is False

    asyncio.run(go())


def test_shared_code_final_launch_never_calls_legacy_ownership_probe(
    monkeypatch,
):
    async def go() -> None:
        machine, transport = _mk_machine()
        ctx = _mk_ctx("sid", "sid")
        ctx.engine = "codex"
        ctx.space = "code"
        ctx.sdk = _SharedSdk()
        ctx.state = "running"
        ctx.active_msg_id = "shared-turn"
        ctx.needs_reload = True
        # Keep this unit test independent of Git checkpoint availability.
        ctx.codex_checkpoint = False
        machine.sessions[ctx.key] = ctx

        async def legacy_probe_must_not_run(_sid: str) -> bool:
            raise AssertionError("shared Code final launch probed legacy ownership")

        monkeypatch.setattr(
            machine, "_prime_codex_ownership", legacy_probe_must_not_run
        )

        await asyncio.wait_for(machine._run_turn(ctx, "hello"), timeout=0.5)

        assert ctx.sdk.queries == [("hello", [])]
        assert ctx.sdk.reconnects == 0
        assert ctx.needs_reload is False
        assert ctx.state == "idle"
        assert not [
            event for event in transport.sent
            if isinstance(event, Error) and event.code == "busy"
        ]

    asyncio.run(go())


def test_shared_takeover_is_an_idempotent_noop(monkeypatch, tmp_path):
    async def go() -> None:
        machine, transport = _mk_machine()
        ctx = _mk_ctx("sid", "sid")
        ctx.engine = "codex"
        ctx.space = "code"
        ctx.sdk = _SharedSdk()
        machine.sessions[ctx.key] = ctx
        path = tmp_path / "rollout.jsonl"
        path.write_bytes(b"")
        holder = ProcessIdentity(303, 3003)
        watch = _watch(path)
        watch["holders"] = {holder}
        watch["writers"] = {holder}
        machine._watch["sid"] = watch

        def legacy_watch_must_not_run(_sid: str) -> None:
            raise AssertionError("shared takeover entered legacy watcher")

        monkeypatch.setattr(machine, "_watch_session", legacy_watch_must_not_run)

        result = await machine._handle_takeover(Takeover(
            sid="sid", cmd_id="stale-shared-takeover"
        ))

        assert result is None
        assert ctx.needs_reload is False
        assert watch["takeover_holders"] == set()
        assert watch["takeover_interactive_holders"] == set()
        assert ctx.control_mode == "codex_shared"
        assert ctx.write_state == "writable"
        assert ctx.terminal_attached is True
        state = transport.sent[-1]
        assert state.type == "takeover_state"
        assert state.pending is False
        assert "无需迁移或接管" in state.message

    asyncio.run(go())


def test_final_preflight_never_downgrades_interrupted_shared_proxy(monkeypatch):
    class ProxyFallsBackSdk(_SharedSdk):
        shared_daemon_affinity = True

        @property
        def using_daemon_proxy(self) -> bool:
            return False

    async def go() -> None:
        machine, transport = _mk_machine()
        ctx = _mk_ctx("sid", "sid")
        ctx.engine = "codex"
        ctx.space = "code"
        ctx.sdk = ProxyFallsBackSdk()
        ctx.state = "running"
        ctx.active_msg_id = "fallback-turn"
        ctx.codex_checkpoint = False
        machine.sessions[ctx.key] = ctx
        probes: list[str] = []

        async def occupied(sid: str) -> bool:
            probes.append(sid)
            return True

        monkeypatch.setattr(machine, "_prime_codex_ownership", occupied)

        await asyncio.wait_for(machine._run_turn(ctx, "must not send"), timeout=0.5)

        assert probes == []
        assert ctx.sdk.reconnects == 1
        assert ctx.sdk.queries == []
        assert ctx.state == "idle"
        disconnected = [
            event for event in transport.sent
            if isinstance(event, Error) and event.code == "not_running"
        ]
        assert len(disconnected) == 1
        assert disconnected[0].msg_id == "fallback-turn"

    asyncio.run(go())


def test_stdio_work_keeps_legacy_terminal_mutex_and_read_only_state(
    tmp_path, monkeypatch,
):
    async def go() -> None:
        machine, transport = _mk_machine()
        path = tmp_path / "rollout.jsonl"
        path.write_bytes(b"")
        ctx = _mk_ctx("sid", "sid")
        ctx.engine = "codex"
        ctx.space = "work"
        ctx.sdk = _CodexSdk()
        machine.sessions[ctx.key] = ctx
        watch = _watch(path)
        machine._watch["sid"] = watch
        holder = ProcessIdentity(202, 2002)
        machine._push_mirrored_history = lambda sid: _record_async(sid)
        path.write_bytes(
            _event("task_started", "work-terminal")
            + _event("task_complete", "work-terminal")
        )

        await machine._poll_codex_watch(
            "sid", watch, {holder}, 1000.0, writers={holder}
        )
        assert watch["external"] is True
        assert ctx.needs_reload is True
        assert ctx.control_mode == "external_cli"
        assert ctx.write_state == "read_only"
        assert ctx.terminal_attached is True
        assert ctx.control_can_takeover is True

        monkeypatch.setattr(machine, "_watch_session", lambda _sid: None)

        async def occupied(_sid: str) -> bool:
            return True

        monkeypatch.setattr(machine, "_prime_codex_ownership", occupied)
        result = await machine._handle_query(Query(
            sid="sid", prompt="must stay blocked", msg_id="work-query"
        ))
        assert isinstance(result, Error)
        assert result.code == "busy"
        assert result.msg_id == "work-query"
        assert ctx.state == "idle"
        assert ctx.turn_task is None
        assert transport.sent[-1] is result

    async def _record_async(_sid: str) -> None:
        return None

    asyncio.run(go())
