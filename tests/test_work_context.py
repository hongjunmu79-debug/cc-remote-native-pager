"""Work-only context accounting; Code keeps the raw engine contract."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from cc_remote.protocol import ContextReport, serialize
from cc_remote.workspaces import WorkRegistry
from cc_remote.wrapper import work_context as work_context_module
from cc_remote.wrapper.session_ctx import SessionContext
from cc_remote.wrapper.ringbuffer import RingBuffer
from cc_remote.wrapper.work_context import (
    initial_work_context_baseline,
    recover_work_context_baseline,
    work_context_metrics,
)
from tests.test_multisession import _mk_machine


def _codex_usage() -> dict:
    return {
        "used_tokens": 11_194,
        "context_window": 353_400,
        "raw": {
            "last": {
                "inputTokens": 11_181,
                "outputTokens": 13,
                "totalTokens": 11_194,
            },
        },
    }


def test_work_context_metrics_keep_raw_overhead_out_of_session_gauge():
    assert initial_work_context_baseline("codex", _codex_usage()) == 11_181
    session, fixed, percentage, baseline = work_context_metrics(
        "codex", _codex_usage(), None)
    assert (session, fixed, baseline) == (13, 11_181, 11_181)
    assert percentage == 13 / 353_400 * 100

    session, fixed, percentage, baseline = work_context_metrics(
        "claude",
        {"totalTokens": 25_572, "maxTokens": 1_000_000},
        25_500,
    )
    assert (session, fixed, baseline) == (72, 25_500, 25_500)
    assert percentage == 72 / 1_000_000 * 100


def test_context_breakdown_is_emitted_only_when_work_has_a_baseline():
    code = ContextReport(
        total_tokens=100, max_tokens=1_000, percentage=10,
        model="test", categories=[],
    )
    assert not ({"session_tokens", "fixed_tokens", "session_percentage"}
                & json.loads(serialize(code)).keys())
    assert "available" not in json.loads(serialize(code))

    unavailable = code.model_copy(update={"available": False})
    assert json.loads(serialize(unavailable))["available"] is False

    work = code.model_copy(update={
        "session_tokens": 10,
        "fixed_tokens": 90,
        "session_percentage": 1.0,
    })
    payload = json.loads(serialize(work))
    assert payload["session_tokens"] == 10
    assert payload["fixed_tokens"] == 90


def test_migrated_work_baseline_recovers_from_native_histories(
    tmp_path: Path, monkeypatch,
):
    claude = tmp_path / "claude.jsonl"
    claude.write_text(
        '{"type":"user","message":{"role":"user"}}\n'
        '{"type":"assistant","message":{"usage":{'
        '"input_tokens":24676,"cache_creation_input_tokens":0,'
        '"cache_read_input_tokens":896,"output_tokens":19}}}\n',
        encoding="utf-8",
    )
    codex = tmp_path / "codex.jsonl"
    codex.write_text(
        '{"type":"event_msg","payload":{"type":"token_count",'
        '"info":{"last_token_usage":{"input_tokens":16774,'
        '"output_tokens":271}}}}\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        work_context_module, "transcript_path", lambda _sid: str(claude))
    monkeypatch.setattr(
        work_context_module, "codex_rollout_path", lambda _sid: str(codex))

    assert recover_work_context_baseline("claude", "claude-session") == 25_572
    assert recover_work_context_baseline("codex", "codex-session") == 16_774


def test_work_registry_persists_context_baseline_once(tmp_path: Path):
    store = WorkRegistry(tmp_path / "work", "codex")
    record = store.create_session()
    assert record.context_baseline_tokens is None
    assert store.set_context_baseline(record.work_id, 11_181) == 11_181
    assert store.set_context_baseline(record.work_id, 99_999) == 11_181
    restored = store.get_by_work_id(record.work_id)
    assert restored is not None
    assert restored.context_baseline_tokens == 11_181


class _ContextSdk:
    def __init__(self, usage: dict):
        self.usage = usage
        self.model = "test-model"

    async def get_context_usage(self) -> dict:
        return self.usage


def test_machine_splits_context_only_for_work(tmp_path: Path):
    async def run():
        machine, transport = _mk_machine()
        machine._work = machine._work.__class__(
            tmp_path / "claude-work", tmp_path / "codex-work")
        store = machine._work.for_engine("codex")
        record = store.create_session()
        store.bind_session(record.work_id, "work-session")
        work = SessionContext(
            session_id="work-session",
            sdk=_ContextSdk(_codex_usage()),
            buffer=RingBuffer(100, 100_000),
            cwd=record.cwd,
            key="work-session",
            engine="codex",
            space="work",
            work_id=record.work_id,
            work_context_baseline_pending=True,
        )
        migrated_record = store.create_session()
        store.bind_session(migrated_record.work_id, "migrated-work-session")
        migrated = SessionContext(
            session_id="migrated-work-session",
            sdk=_ContextSdk(_codex_usage()),
            buffer=RingBuffer(100, 100_000),
            cwd=migrated_record.cwd,
            key="migrated-work-session",
            engine="codex",
            space="work",
            work_id=migrated_record.work_id,
        )
        code = SessionContext(
            session_id="code-session",
            sdk=_ContextSdk(_codex_usage()),
            buffer=RingBuffer(100, 100_000),
            cwd="/tmp",
            key="code-session",
            engine="codex",
            space="code",
        )
        unknown = SessionContext(
            session_id="unknown-code-session",
            sdk=_ContextSdk({
                "used_tokens": None,
                "context_window": 256_000,
                "raw": {},
            }),
            buffer=RingBuffer(100, 100_000),
            cwd="/tmp",
            key="unknown-code-session",
            engine="codex",
            space="code",
        )
        machine.sessions = {
            "work-session": work,
            "migrated-work-session": migrated,
            "code-session": code,
            "unknown-code-session": unknown,
        }

        await machine._handle_get_context(SimpleNamespace(sid="work-session"))
        work_report = transport.sent[-1]
        assert work_report.total_tokens == 11_194
        assert work_report.percentage == 11_194 / 353_400 * 100
        assert work_report.session_tokens == 13
        assert work_report.fixed_tokens == 11_181
        assert work_report.session_percentage == 13 / 353_400 * 100
        assert work.work_context_baseline_pending is False
        assert store.get_by_work_id(
            record.work_id).context_baseline_tokens == 11_181

        # A pre-upgrade session with no durable baseline must keep the honest
        # raw reading; it may not relabel all existing history as fixed cost.
        await machine._handle_get_context(SimpleNamespace(
            sid="migrated-work-session"))
        migrated_report = transport.sent[-1]
        assert migrated_report.total_tokens == 11_194
        assert migrated_report.session_tokens is None
        assert migrated_report.fixed_tokens is None
        assert store.get_by_work_id(
            migrated_record.work_id).context_baseline_tokens is None

        await machine._handle_get_context(SimpleNamespace(sid="code-session"))
        code_report = transport.sent[-1]
        assert code_report.total_tokens == 11_194
        assert code_report.percentage == 11_194 / 353_400 * 100
        assert code_report.session_tokens is None
        assert code_report.fixed_tokens is None
        assert code_report.session_percentage is None

        # A lightweight Codex resume has no tokenUsage until a model turn emits
        # it.  The wrapper must preserve that unknown state rather than forge
        # the 0 / context-window reading that used to appear as 0%.
        await machine._handle_get_context(SimpleNamespace(
            sid="unknown-code-session"))
        unknown_report = transport.sent[-1]
        assert unknown_report.available is False
        assert unknown_report.total_tokens == 0
        assert unknown_report.max_tokens == 256_000
        assert unknown_report.percentage == 0

    asyncio.run(run())
