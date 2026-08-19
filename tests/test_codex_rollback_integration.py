from __future__ import annotations

import asyncio

from cc_remote.protocol import (
    ArtifactInvalidated,
    CommandAck,
    History,
    HistoryInvalidated,
    Notice,
    RollbackResult,
    RollbackSession,
    StateEvent,
)
from cc_remote.wrapper.codex_checkpoints import CheckpointError
from cc_remote.wrapper.machine import WrapperMachine
from tests.test_multisession import _StubTransport, _mk_ctx, _mk_machine


class _FakeCodex:
    def __init__(self):
        self.rollbacks: list[int] = []

    async def rollback_thread(self, num_turns: int):
        self.rollbacks.append(num_turns)
        return {"id": "codex-session"}


class _FakeJournal:
    def __init__(self):
        self.restores: list[tuple[int, bool]] = []
        self.discards: list[tuple[int, bool]] = []
        self.cleaned = False

    def rollback(self, num_turns: int, *, consume: bool = True):
        self.restores.append((num_turns, consume))

    def discard(self, num_turns: int, *, allow_partial: bool = False):
        self.discards.append((num_turns, allow_partial))

    def cleanup(self, *, force: bool = False):
        assert force is True
        self.cleaned = True


def test_combined_rollback_retires_checkpoint_before_native_history_mutation():
    async def run():
        machine, transport = _mk_machine()
        ctx = _mk_ctx("codex-session", "codex-session")
        ctx.engine = "codex"
        ctx.space = "code"
        ctx.sdk = _FakeCodex()
        journal = _FakeJournal()
        ctx.codex_checkpoint = journal
        machine.sessions[ctx.key] = ctx

        async def code_context(_cmd, _action):
            return ctx

        async def history(_sid, **_kwargs):
            return History(
                session_id="codex-session",
                revision="test-history-revision",
                events=[],
                has_more=False,
            )

        async def no_list(_cmd):
            return None

        machine._codex_code_context = code_context
        machine._build_history = history
        machine._handle_list_sessions = no_list
        # Prove a destructive reset does not replace the ring or restart seq.
        await machine._emit(ctx, StateEvent(state="idle"))
        command = RollbackSession(
            session_id="codex-session",
            engine="codex",
            restore="both",
            num_turns=2,
            cmd_id="rollback-command",
            client_id="client-1",
        )

        await machine._process_command(command)
        await machine._process_command(command)

        assert journal.restores == [(2, False)]
        assert journal.cleaned is True
        assert ctx.codex_checkpoint is None
        assert ctx.sdk.rollbacks == [2]
        assert journal.discards == []
        barriers = [
            item for item in transport.sent if isinstance(item, HistoryInvalidated)
        ]
        # The first marker is broadcast and the reliable-command retry replays
        # the cached response only to the originating client.
        assert len(barriers) == 2
        assert barriers[0].seq == 3
        assert barriers[1].seq == 3
        assert barriers[1].to == "client-1"
        artifacts = [
            item for item in transport.sent if isinstance(item, ArtifactInvalidated)
        ]
        assert len(artifacts) == 2
        assert artifacts[0].seq == 2
        assert artifacts[1].to == "client-1"
        assert ctx.buffer.tail_seq == 3
        replay = ctx.buffer.replay_from(
            1,
            cc_session_id=ctx.session_id,
            state=ctx.state,
            generation="generation-1",
        )
        assert any(isinstance(item, HistoryInvalidated) for item in replay)
        assert any(isinstance(item, ArtifactInvalidated) for item in replay)
        assert not any(isinstance(item, RollbackResult) for item in replay)
        histories = [item for item in transport.sent if isinstance(item, History)]
        results = [item for item in transport.sent if isinstance(item, RollbackResult)]
        acks = [item for item in transport.sent if isinstance(item, CommandAck)]
        assert len(histories) == 2 and all(item.reset for item in histories)
        assert histories[0].to is None
        assert histories[1].to == "client-1"
        assert len(results) == 2
        assert results[0].conversation == results[0].files == "succeeded"
        assert results[1].to == "client-1"
        assert len(acks) == 2

    asyncio.run(run())


def test_files_only_restore_does_not_consume_conversation_alignment():
    async def run():
        machine, transport = _mk_machine()
        ctx = _mk_ctx("codex-session", "codex-session")
        ctx.engine = "codex"
        ctx.space = "code"
        ctx.sdk = _FakeCodex()
        ctx.codex_checkpoint = _FakeJournal()

        async def code_context(_cmd, _action):
            return ctx

        machine._codex_code_context = code_context
        result = await machine._handle_rollback_session(
            RollbackSession(
                session_id="codex-session",
                engine="codex",
                restore="files",
                num_turns=1,
                cmd_id="files-only-command",
                client_id="client-1",
            )
        )

        assert isinstance(result, tuple)
        invalidated, rollback = result
        assert isinstance(invalidated, ArtifactInvalidated)
        assert isinstance(rollback, RollbackResult)
        assert rollback.files == "succeeded" and rollback.conversation == "skipped"
        assert ctx.codex_checkpoint.restores == [(1, False)]
        assert ctx.codex_checkpoint.discards == []
        assert ctx.sdk.rollbacks == []
        assert any(isinstance(item, RollbackResult) for item in transport.sent)

    asyncio.run(run())


def test_failed_post_capture_leaves_an_unavailable_turn_marker():
    class FailedJournal:
        def __init__(self):
            self.calls: list[tuple[str, str]] = []

        def finish_turn(self, turn_id: str):
            self.calls.append(("finish", turn_id))
            raise CheckpointError("index changed")

        def abort_turn(self, turn_id: str):
            self.calls.append(("abort", turn_id))

        def record_unavailable(self, turn_id: str, reason: str):
            self.calls.append((reason, turn_id))

    async def run():
        machine, transport = _mk_machine()
        ctx = _mk_ctx("codex-session", "codex-session")
        ctx.engine = "codex"
        ctx.space = "code"
        ctx.codex_checkpoint = FailedJournal()
        ctx.codex_checkpoint_turn_id = "turn-unavailable"
        ctx.codex_checkpoint_ready = True
        ctx.codex_checkpoint_accepted = True

        await machine._finish_codex_checkpoint(ctx)

        assert ctx.codex_checkpoint.calls == [
            ("finish", "turn-unavailable"),
            ("abort", "turn-unavailable"),
            ("CheckpointError", "turn-unavailable"),
        ]
        assert any(
            isinstance(item, Notice) and item.title == "本轮代码回滚不可用"
            for item in transport.sent
        )

    asyncio.run(run())


def test_failed_unavailable_marker_is_retried_at_turn_finish():
    class Journal:
        def __init__(self):
            self.calls: list[tuple[str, str]] = []
            self.cleaned = False

        def record_unavailable(self, turn_id: str, reason: str):
            self.calls.append((turn_id, reason))
            if len(self.calls) == 1:
                raise OSError("temporary manifest write failure")

        def cleanup(self, *, force: bool = False):
            assert force is True
            self.cleaned = True

    async def run():
        machine, _transport = _mk_machine()
        ctx = _mk_ctx("codex-session", "codex-session")
        ctx.engine = "codex"
        ctx.space = "code"
        journal = Journal()
        ctx.codex_checkpoint = journal
        ctx.codex_checkpoint_turn_id = "turn-unavailable"
        ctx.codex_checkpoint_unavailable_reason = "capture failed"

        await machine._accept_codex_checkpoint(ctx)

        assert journal.calls == [("turn-unavailable", "capture failed")]
        assert ctx.codex_checkpoint is journal
        assert ctx.codex_checkpoint_accepted is True
        assert ctx.codex_checkpoint_unavailable_reason == "capture failed"

        await machine._finish_codex_checkpoint(ctx)

        assert journal.calls == [
            ("turn-unavailable", "capture failed"),
            ("turn-unavailable", "capture failed"),
        ]
        assert journal.cleaned is False
        assert ctx.codex_checkpoint is journal
        assert ctx.codex_checkpoint_turn_id is None
        assert ctx.codex_checkpoint_unavailable_reason is None

    asyncio.run(run())


def test_failed_unavailable_marker_retry_retires_journal_fail_closed():
    class Journal:
        def __init__(self):
            self.calls = 0
            self.cleaned = False

        def record_unavailable(self, _turn_id: str, _reason: str):
            self.calls += 1
            raise CheckpointError("persistent manifest write failure")

        def cleanup(self, *, force: bool = False):
            assert force is True
            self.cleaned = True

    async def run():
        machine, _transport = _mk_machine()
        ctx = _mk_ctx("codex-session", "codex-session")
        ctx.engine = "codex"
        ctx.space = "code"
        journal = Journal()
        ctx.codex_checkpoint = journal
        ctx.codex_checkpoint_turn_id = "turn-unavailable"
        ctx.codex_checkpoint_unavailable_reason = "capture failed"

        await machine._accept_codex_checkpoint(ctx)
        await machine._finish_codex_checkpoint(ctx)

        assert journal.calls == 2
        assert journal.cleaned is True
        assert ctx.codex_checkpoint is False
        assert ctx.codex_checkpoint_turn_id is None
        assert ctx.codex_checkpoint_unavailable_reason is None

    asyncio.run(run())


def test_unaccepted_turn_start_aborts_instead_of_finishing_empty_checkpoint():
    class Journal:
        def __init__(self):
            self.calls: list[tuple[str, str]] = []

        def abort_turn(self, turn_id: str):
            self.calls.append(("abort", turn_id))

        def finish_turn(self, turn_id: str):
            self.calls.append(("finish", turn_id))

    async def run():
        machine, _transport = _mk_machine()
        ctx = _mk_ctx("codex-session", "codex-session")
        ctx.engine = "codex"
        ctx.space = "code"
        ctx.codex_checkpoint = Journal()
        ctx.codex_checkpoint_turn_id = "turn-rejected"
        ctx.codex_checkpoint_ready = True
        ctx.codex_checkpoint_accepted = False

        await machine._finish_codex_checkpoint(ctx)

        assert ctx.codex_checkpoint.calls == [("abort", "turn-rejected")]
        assert ctx.codex_checkpoint_turn_id is None

    asyncio.run(run())


def test_failed_unaccepted_abort_retires_journal_fail_closed():
    class Journal:
        def __init__(self):
            self.cleaned = False

        def abort_turn(self, _turn_id: str):
            raise CheckpointError("manifest unavailable")

        def cleanup(self, *, force: bool = False):
            assert force is True
            self.cleaned = True

    async def run():
        machine, _transport = _mk_machine()
        ctx = _mk_ctx("codex-session", "codex-session")
        ctx.engine = "codex"
        ctx.space = "code"
        journal = Journal()
        ctx.codex_checkpoint = journal
        ctx.codex_checkpoint_turn_id = "turn-rejected"
        ctx.codex_checkpoint_ready = True

        await machine._finish_codex_checkpoint(ctx)

        assert journal.cleaned is True
        assert ctx.codex_checkpoint is False

    asyncio.run(run())


def test_spontaneous_turn_records_exactly_one_unavailable_slot():
    class Journal:
        def __init__(self):
            self.turns: set[str] = set()

        def record_unavailable(self, turn_id: str, _reason: str):
            self.turns.add(turn_id)

    async def run():
        machine, _transport = _mk_machine()
        ctx = _mk_ctx("codex-session", "codex-session")
        ctx.engine = "codex"
        ctx.space = "code"
        journal = Journal()
        ctx.codex_checkpoint = journal
        ctx.codex_spontaneous_turn_id = "automatic-turn"
        ctx.active_msg_id = "automatic-turn"

        await machine._finish_codex_spontaneous_turn(ctx, "automatic-turn")
        await machine._finish_codex_spontaneous_turn(ctx, "automatic-turn")

        assert journal.turns == {"automatic-turn"}
        assert ctx.codex_spontaneous_turn_id is None

    asyncio.run(run())


def test_conversation_rollback_retires_journal_before_native_submission():
    async def run():
        machine, _transport = _mk_machine()
        ctx = _mk_ctx("codex-session", "codex-session")
        ctx.engine = "codex"
        ctx.space = "code"
        ctx.sdk = _FakeCodex()
        journal = _FakeJournal()
        ctx.codex_checkpoint = journal

        async def code_context(_cmd, _action):
            return ctx

        async def history(_sid, **_kwargs):
            return History(
                session_id="codex-session",
                revision="test-history-revision",
                events=[],
                has_more=False,
            )

        async def no_list(_cmd):
            return None

        machine._codex_code_context = code_context
        machine._build_history = history
        machine._handle_list_sessions = no_list
        result = await machine._handle_rollback_session(RollbackSession(
            session_id="codex-session",
            engine="codex",
            restore="conversation",
            num_turns=1,
            cmd_id="discard-failure-command",
            client_id="client-1",
        ))

        assert isinstance(result, tuple)
        rollback = next(item for item in result if isinstance(item, RollbackResult))
        assert rollback.conversation == "succeeded"
        assert journal.cleaned is True
        assert journal.discards == []
        assert ctx.codex_checkpoint is None
        assert ctx.sdk.rollbacks == [1]

    asyncio.run(run())


def test_post_rollback_discard_failure_quarantines_journal_and_reports_detail():
    class FailedDiscardJournal(_FakeJournal):
        def discard(self, num_turns: int, *, allow_partial: bool = False):
            self.discards.append((num_turns, allow_partial))
            raise OSError("simulated manifest write failure")

    async def run():
        machine, _transport = _mk_machine()
        ctx = _mk_ctx("codex-session", "codex-session")
        ctx.engine = "codex"
        ctx.space = "code"
        ctx.sdk = _FakeCodex()
        journal = FailedDiscardJournal()
        ctx.codex_checkpoint = journal

        async def code_context(_cmd, _action):
            return ctx

        async def retain_legacy_journal(_ctx):
            # Exercise the compatibility fallback directly. Production returns
            # True after pre-retiring the journal before native rollback.
            return False

        async def history(_sid, **_kwargs):
            return History(
                session_id="codex-session",
                revision="test-history-revision",
                events=[],
                has_more=False,
            )

        async def no_list(_cmd):
            return None

        machine._codex_code_context = code_context
        machine._prepare_codex_conversation_rollback = retain_legacy_journal
        machine._build_history = history
        machine._handle_list_sessions = no_list

        result = await machine._handle_rollback_session(RollbackSession(
            session_id="codex-session",
            engine="codex",
            restore="conversation",
            num_turns=1,
            cmd_id="discard-failure-command",
            client_id="client-1",
        ))

        rollback = next(
            item for item in (
                result if isinstance(result, tuple) else (result,)
            )
            if isinstance(item, RollbackResult)
        )
        assert rollback.conversation == "succeeded"
        assert "代码回滚记录同步失败，已安全重置" in (rollback.detail or "")
        assert journal.discards == [(1, True)]
        assert journal.cleaned is True
        assert ctx.codex_checkpoint is False
        assert ctx.sdk.rollbacks == [1]

    asyncio.run(run())


def test_post_commit_refresh_failure_never_replays_native_rollback():
    async def run():
        machine, _transport = _mk_machine()
        ctx = _mk_ctx("codex-session", "codex-session")
        ctx.engine = "codex"
        ctx.space = "code"
        ctx.sdk = _FakeCodex()
        journal = _FakeJournal()
        ctx.codex_checkpoint = journal
        machine.sessions[ctx.key] = ctx

        async def code_context(_cmd, _action):
            return ctx

        async def broken_history(_sid, **_kwargs):
            raise RuntimeError("simulated history refresh failure")

        async def broken_list(_cmd):
            raise RuntimeError("simulated sidebar refresh failure")

        machine._codex_code_context = code_context
        machine._build_history = broken_history
        machine._handle_list_sessions = broken_list
        command = RollbackSession(
            session_id="codex-session",
            engine="codex",
            restore="conversation",
            num_turns=1,
            cmd_id="post-commit-command",
            client_id="client-1",
        )

        await machine._process_command(command)
        await machine._process_command(command)

        assert ctx.sdk.rollbacks == [1]
        assert journal.cleaned is True
        assert journal.discards == []
        assert ctx.codex_checkpoint is None

    asyncio.run(run())


def test_wrapper_restart_never_replays_a_submitted_native_rollback():
    async def run():
        first, _transport = _mk_machine()
        command = RollbackSession(
            session_id="codex-session",
            engine="codex",
            restore="conversation",
            num_turns=1,
            cmd_id="crash-window-command",
            client_id="client-1",
        )
        first._rollback_commands.begin(
            "client-1",
            "crash-window-command",
            "codex-session",
            "codex",
            "conversation",
            1,
            None,
        )
        assert first._rollback_commands.mark_submitted(
            "client-1", "crash-window-command"
        ) is True

        # Simulate a fresh wrapper after the old process crossed app-server's
        # mutation boundary but died before recording its response.
        transport = _StubTransport()
        restarted = WrapperMachine(first.cfg, transport)
        ctx = _mk_ctx("codex-session", "codex-session")
        ctx.engine = "codex"
        ctx.space = "code"
        ctx.sdk = _FakeCodex()
        journal = _FakeJournal()
        ctx.codex_checkpoint = journal

        async def code_context(_cmd, _action):
            return ctx

        async def history(_sid, **_kwargs):
            return History(
                session_id="codex-session",
                revision="test-history-revision",
                events=[],
                has_more=False,
            )

        async def no_list(_cmd):
            return None

        restarted._codex_code_context = code_context
        restarted._build_history = history
        restarted._handle_list_sessions = no_list

        result = await restarted._handle_rollback_session(command)

        rollback = next(
            item for item in result if isinstance(item, RollbackResult)
        )
        assert rollback.conversation == "failed"
        assert "无法确认" in (rollback.detail or "")
        assert ctx.sdk.rollbacks == []
        assert journal.cleaned is True
        assert journal.discards == []
        assert ctx.codex_checkpoint is None
        persisted = restarted._rollback_commands.get(
            "client-1", "crash-window-command"
        )
        assert persisted["status"] == "complete"

    asyncio.run(run())


def test_checkpoint_quarantine_failure_blocks_native_conversation_rollback():
    class FailedCleanupJournal(_FakeJournal):
        def cleanup(self, *, force: bool = False):
            assert force is True
            raise CheckpointError("simulated quarantine failure")

    async def run():
        machine, _transport = _mk_machine()
        ctx = _mk_ctx("codex-session", "codex-session")
        ctx.engine = "codex"
        ctx.space = "code"
        ctx.sdk = _FakeCodex()
        ctx.codex_checkpoint = FailedCleanupJournal()

        async def code_context(_cmd, _action):
            return ctx

        machine._codex_code_context = code_context
        result = await machine._handle_rollback_session(RollbackSession(
            session_id="codex-session",
            engine="codex",
            restore="conversation",
            num_turns=1,
            cmd_id="quarantine-failure-command",
            client_id="client-1",
        ))

        rollback = next(
            item for item in (
                result if isinstance(result, tuple) else (result,)
            )
            if isinstance(item, RollbackResult)
        )
        assert rollback.conversation == "failed"
        assert ctx.sdk.rollbacks == []
        assert ctx.codex_checkpoint is False

    asyncio.run(run())
