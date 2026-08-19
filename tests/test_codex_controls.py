"""Zero-token regressions for Codex approvals and cross-engine controls."""
from __future__ import annotations

import asyncio
import json
import os
import time
from types import SimpleNamespace

import pytest
from claude_agent_sdk import PermissionResultAllow, PermissionResultDeny
from pydantic import ValidationError

from cc_remote import __version__
from cc_remote.protocol import (
    CollaborationMode, Error, GoalState, Model, NewSession, PinSession, StateEvent,
    ThreadGoal, TurnBinding, TurnEnd, UserMsg,
)
from cc_remote.wrapper import codex_handle as codex_handle_module
from cc_remote.wrapper import codex_models as codex_models_module
from cc_remote.wrapper import codex_sessions as codex_sessions_module
from cc_remote.wrapper import machine as machine_module
from cc_remote.wrapper.codex_handle import (
    CodexHandle,
    CodexManagedOverflow,
    _provider_error_diagnostic,
)
from cc_remote.wrapper.sdk import SdkHandle
from cc_remote.wrapper.work_prompt import (
    WORK_BASE_INSTRUCTIONS,
    WORK_DEVELOPER_INSTRUCTIONS,
)
from tests.test_multisession import _mk_ctx, _mk_machine


class _Cfg:
    cc_cwd = "/tmp"
    tool_result_max = 8000


def test_provider_error_diagnostic_keeps_only_safe_classification():
    diagnostic = _provider_error_diagnostic({
        "willRetry": True,
        "error": {
            "message": "connection failed for SECRET_ENDPOINT",
            "additionalDetails": "token=SECRET_TOKEN",
            "codexErrorInfo": {
                "responseStreamDisconnected": {"httpStatusCode": 503},
            },
        },
    })

    assert diagnostic == {
        "will_retry": True,
        "category": "upstream_server",
        "http_status": 503,
    }
    assert "SECRET" not in repr(diagnostic)


def test_codex_initialize_declares_experimental_api_for_collaboration_mode():
    assert codex_handle_module._initialize_params() == {
        "clientInfo": {"name": "cc-remote", "version": __version__},
        "capabilities": {"experimentalApi": True},
    }


@pytest.mark.parametrize("version, expected", [
    ("0.144.5", False),
    ("0.144.6", True),
    ("0.145.0-alpha.1", True),
    (None, False),
    ("invalid", False),
])
def test_codex_lightweight_resume_version_gate(version, expected):
    assert codex_handle_module._supports_lightweight_resume(version) is expected


_WORK_SKILLS_RESPONSE = {
    "data": [{
        "cwd": "/tmp",
        "skills": [
            {"path": "/home/test/.codex/skills/private/SKILL.md", "enabled": True},
            {"path": "/system/skills/official/SKILL.md", "enabled": True},
            {"path": "/disabled/SKILL.md", "enabled": False},
        ],
    }],
}
_WORK_CONFIG_RESPONSE = {
    "config": {
        "model": "gpt-configured",
        "model_provider": "openai",
        "mcp_servers": {
            "private": {"command": "private-mcp"},
            "already-off": {"command": "unused", "enabled": False},
        },
    },
}


def _expected_work_config():
    return codex_handle_module._work_thread_config(
        _WORK_SKILLS_RESPONSE, _WORK_CONFIG_RESPONSE)


def test_codex_work_thread_config_disables_context_without_model_or_auth_override():
    config = _expected_work_config()

    assert config["features"] == {
        name: False for name in codex_handle_module._WORK_DISABLED_FEATURES
    }
    assert config["skills"]["config"] == [
        {"path": "/home/test/.codex/skills/private/SKILL.md", "enabled": False},
        {"path": "/system/skills/official/SKILL.md", "enabled": False},
    ]
    assert config["mcp_servers"] == {
        "private": {"enabled": False},
        "already-off": {"enabled": False},
    }
    assert config["project_doc_max_bytes"] == 0
    assert config["web_search"] == "cached"
    assert "goals" not in config["features"]
    assert not ({"model", "model_provider", "auth"} & config.keys())


@pytest.mark.parametrize("bad", [None, {}, {"data": None}, {"data": [{}]}])
def test_codex_work_thread_config_fails_closed_on_invalid_skill_inventory(bad):
    with pytest.raises(RuntimeError, match="skills/list"):
        codex_handle_module._work_thread_config(bad, _WORK_CONFIG_RESPONSE)


def test_codex_sessions_toml_loader_falls_back_on_python_310(monkeypatch):
    fallback = object()
    imports: list[str] = []

    def fake_import(name: str):
        imports.append(name)
        if name == "tomllib":
            raise ModuleNotFoundError("No module named 'tomllib'", name=name)
        assert name == "tomli"
        return fallback

    monkeypatch.setattr(codex_sessions_module, "import_module", fake_import)

    assert codex_sessions_module._load_tomllib() is fallback
    assert imports == ["tomllib", "tomli"]


def test_codex_session_list_cold_reads_are_singleflight(monkeypatch):
    async def run():
        machine, transport = _mk_machine()
        calls = 0

        async def fake_list(_limit):
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.01)
            return [{
                "session_id": "catalog-session", "summary": "catalog",
                "cwd": "/tmp", "status": "idle",
            }]

        monkeypatch.setattr(machine_module, "list_codex_sessions", fake_list)
        await asyncio.gather(*[
            machine._handle_list_sessions(SimpleNamespace(
                engine="codex", space="code", client_id=client))
            for client in ("client-a", "client-b")
        ])

        assert calls == 1
        lists = [event for event in transport.sent if event.type == "session_list"]
        assert len(lists) == 2
        assert {event.to for event in lists} == {"client-a", "client-b"}

    asyncio.run(run())


def test_stale_codex_session_list_paints_before_refresh_finishes(monkeypatch):
    async def run():
        machine, transport = _mk_machine()
        machine._codex_session_list_cache = (time.monotonic() - 60, [{
            "session_id": "stale-session", "summary": "stale",
            "cwd": "/tmp", "status": "idle",
        }])
        release = asyncio.Event()

        async def fake_list(_limit):
            await release.wait()
            return [{
                "session_id": "fresh-session", "summary": "fresh",
                "cwd": "/tmp", "status": "idle",
            }]

        monkeypatch.setattr(machine_module, "list_codex_sessions", fake_list)
        task = asyncio.create_task(machine._handle_list_sessions(
            SimpleNamespace(engine="codex", space="code", client_id="client")))
        for _ in range(100):
            if any(event.type == "session_list" for event in transport.sent):
                break
            await asyncio.sleep(0.001)
        assert [event.sessions[0].session_id for event in transport.sent
                if event.type == "session_list"] == ["stale-session"]
        release.set()
        await task
        assert [event.sessions[0].session_id for event in transport.sent
                if event.type == "session_list"] == [
                    "stale-session", "fresh-session"]

    asyncio.run(run())


@pytest.mark.parametrize("model", ["gpt-5.6-terra", "gpt-5.6-luna"])
def test_codex_model_id_is_exact_through_wrapper_and_turn_start(model):
    async def run():
        machine, transport = _mk_machine()
        ctx = _mk_ctx("codex-model", "codex-model")
        handle = CodexHandle(_Cfg())
        handle.proc = SimpleNamespace(returncode=None)
        handle.thread_id = "codex-model"
        ctx.sdk = handle
        ctx.engine = "codex"
        machine.sessions[ctx.key] = ctx
        requests = []

        async def request(method, params=None):
            requests.append((method, params))
            return {"turn": {"id": "turn-model"}}

        handle._request = request
        await machine._handle_set_model(SimpleNamespace(
            sid=ctx.key, model=model))

        assert handle.model == model
        assert ctx.announced_model == model
        model_event = next(event for event in transport.sent
                           if isinstance(event, Model))
        assert model_event.model == model
        assert requests == [("thread/settings/update", {
            "threadId": "codex-model", "model": model,
        })]
        requests.clear()
        await handle.query("which model")
        assert requests[-1][0] == "turn/start"
        assert requests[-1][1]["model"] == model
        assert handle.owned_turn_ids == {"turn-model"}
        assert handle.turn_active is True and handle.turn_start_pending is False
        await handle._dispatch({
            "method": "turn/completed",
            "params": {"turn": {"id": "turn-model", "status": "completed"}},
        })
        assert handle.turn_active is False

    asyncio.run(run())


def test_codex_collaboration_mode_is_dynamic_and_separate_from_approval():
    async def run():
        handle = CodexHandle(_Cfg())
        handle.proc = SimpleNamespace(returncode=None)
        handle.thread_id = "codex-plan"
        handle.model = "gpt-plan"
        handle.effort = "xhigh"
        handle.approval = "on-request"
        requests = []

        async def request(method, params=None):
            requests.append((method, params))
            return {"turn": {"id": f"turn-{len(requests)}"}}

        handle._request = request
        await handle.set_collaboration_mode("plan")
        await handle.query("make a plan")
        plan = requests[-1][1]
        assert plan["approvalPolicy"] == "on-request"
        assert plan["collaborationMode"] == {
            "mode": "plan",
            "settings": {
                "model": "gpt-plan",
                "reasoning_effort": "xhigh",
                "developer_instructions": None,
            },
        }

        await handle._dispatch({
            "method": "turn/completed",
            "params": {"turn": {"id": "turn-1", "status": "completed"}},
        })
        handle.model = "gpt-next"
        handle.effort = "high"
        await handle.set_collaboration_mode("default")
        await handle.query("implement")
        normal = requests[-1][1]
        assert normal["approvalPolicy"] == "on-request"
        assert normal["collaborationMode"] == {
            "mode": "default",
            "settings": {
                "model": "gpt-next",
                "reasoning_effort": "high",
                "developer_instructions": None,
            },
        }

    asyncio.run(run())


def test_codex_app_server_uses_and_cleans_its_own_process_group(monkeypatch):
    if codex_handle_module.os.name != "posix":
        pytest.skip("POSIX process groups only")

    async def run():
        spawn_kwargs = {}

        async def fail_after_capture(*_args, **kwargs):
            spawn_kwargs.update(kwargs)
            raise RuntimeError("capture spawn options")

        monkeypatch.setattr(
            codex_handle_module, "_resolve_codex_bin", lambda: "/usr/bin/codex")
        monkeypatch.setattr(
            codex_handle_module.asyncio, "create_subprocess_exec", fail_after_capture)
        with pytest.raises(RuntimeError, match="capture spawn options"):
            await CodexHandle(_Cfg()).connect()
        assert spawn_kwargs["start_new_session"] is True

        class FakeProcess:
            pid = 424242
            returncode = None

            async def wait(self):
                self.returncode = 0
                return 0

            def terminate(self):
                raise AssertionError("POSIX cleanup must signal the process group")

            def kill(self):
                raise AssertionError("POSIX cleanup must signal the process group")

        signals = []
        monkeypatch.setattr(
            codex_handle_module.os, "killpg",
            lambda pgid, sig: signals.append((pgid, sig)),
        )
        handle = CodexHandle(_Cfg())
        handle.proc = FakeProcess()
        handle._process_group = handle.proc.pid
        await handle.disconnect()

        assert signals == [
            (424242, codex_handle_module.signal.SIGTERM),
            (424242, codex_handle_module.signal.SIGKILL),
        ]

    asyncio.run(run())


def test_codex_turn_completion_before_start_waiter_does_not_resurrect_active():
    async def run():
        handle = CodexHandle(_Cfg())
        handle.proc = SimpleNamespace(returncode=None)
        handle.thread_id = "codex-fast-turn"

        async def request(method, _params=None):
            assert method == "turn/start"
            await handle._dispatch({
                "method": "turn/completed",
                "params": {"turn": {"id": "fast", "status": "completed"}},
            })
            return {"turn": {"id": "fast"}}

        handle._request = request
        await handle.query("fast")
        assert handle.turn_start_pending is False
        assert handle.turn_active is False
        assert handle.owned_turn_ids == {"fast"}

    asyncio.run(run())


def test_codex_review_owns_outer_lifecycle_and_nested_execution_turn():
    async def run():
        handle = CodexHandle(_Cfg())
        handle.proc = SimpleNamespace(returncode=None)
        handle.thread_id = "review-thread"

        async def request(method, params=None):
            assert method == "review/start"
            assert params == {
                "threadId": "review-thread",
                "target": {"type": "uncommittedChanges"},
                "delivery": "inline",
            }
            # The outer Review item can precede the RPC response. It owns the
            # visible lifecycle, while the nested reviewer is still our actual
            # execution/rollout turn and must not be classified as a terminal.
            await handle._dispatch({
                "method": "item/started",
                "params": {
                    "threadId": "review-thread", "turnId": "review-outer",
                    "item": {"id": "entered", "type": "enteredReviewMode"},
                },
            })
            await handle._dispatch({
                "method": "turn/started",
                "params": {
                    "threadId": "review-thread",
                    "turn": {"id": "review-inner"},
                },
            })
            await handle._dispatch({
                "method": "turn/completed",
                "params": {
                    "threadId": "review-thread",
                    "turn": {"id": "review-outer", "status": "completed"},
                },
            })
            return {
                "reviewThreadId": "review-thread",
                "turn": {"id": "review-outer"},
            }

        handle._request = request
        result = await handle.start_review({"type": "uncommittedChanges"})
        frames = [frame async for frame in handle.receive_response()]

        assert result == {
            "thread_id": "review-thread", "turn_id": "review-outer"}
        assert [frame["method"] for frame in frames] == [
            "item/started", "turn/started", "turn/completed"]
        assert handle.owned_turn_ids == {"review-outer", "review-inner"}
        assert handle.turn_active is False
        assert handle.turn_start_pending is False
        assert handle.turn_attribution_pending is False
        assert handle._review_active is False

    asyncio.run(run())


def test_codex_review_interrupt_retries_nested_turn_after_response_race():
    async def run():
        handle = CodexHandle(_Cfg())
        handle.proc = SimpleNamespace(returncode=None)
        handle.thread_id = "review-race"
        interrupt_targets = []

        async def request(method, params=None):
            if method == "review/start":
                return {
                    "reviewThreadId": "review-race",
                    "turn": {"id": "review-outer"},
                }
            assert method == "turn/interrupt"
            interrupt_targets.append(params["turnId"])
            if params["turnId"] == "review-outer":
                # Official app-server ordering can answer review/start before it
                # emits the nested turn/started frame. The first strict interrupt
                # is rejected, then the newly attributed executor must be retried.
                await handle._dispatch({
                    "method": "turn/started",
                    "params": {
                        "threadId": "review-race",
                        "turn": {"id": "review-inner"},
                    },
                })
                raise RuntimeError("active turn id is review-inner")
            return {}

        handle._request = request
        await handle.start_review({"type": "uncommittedChanges"})
        assert handle.turn_attribution_pending is True
        await handle.interrupt()

        assert interrupt_targets == ["review-outer", "review-inner"]
        assert handle.owned_turn_ids == {"review-outer", "review-inner"}
        assert handle.turn_attribution_pending is False
        await handle._dispatch({
            "method": "turn/completed",
            "params": {
                "threadId": "review-race",
                "turn": {"id": "review-outer", "status": "interrupted"},
            },
        })
        assert handle._review_active is False

    asyncio.run(run())


def test_codex_review_pre_response_burst_never_blocks_rpc_reader():
    class BurstCfg(_Cfg):
        turn_reader_queue_cap = 1
        ws_max_size_bytes = 1024 * 1024

    async def run():
        handle = CodexHandle(BurstCfg())
        handle.proc = SimpleNamespace(returncode=None)
        handle.thread_id = "review-burst"

        async def request(method, _params=None):
            assert method == "review/start"
            # More events than the configured managed bridge can retain arrive
            # before the response. The sole stdout reader must never await a
            # consumer which cannot start until this RPC returns.
            assert handle._turn_q is not None
            for index in range(handle._turn_q.max_items + 1):
                await handle._dispatch({
                    "method": "item/started",
                    "params": {
                        "threadId": "review-burst", "turnId": "review-outer",
                        "item": {
                            "id": f"early-{index}",
                            "type": "enteredReviewMode",
                        },
                    },
                })
            return {
                "reviewThreadId": "review-burst",
                "turn": {"id": "review-outer"},
            }

        handle._request = request
        result = await asyncio.wait_for(
            handle.start_review({"type": "uncommittedChanges"}),
            timeout=0.2,
        )
        assert result["turn_id"] == "review-outer"
        await handle._dispatch({
            "method": "turn/completed",
            "params": {
                "threadId": "review-burst",
                "turn": {"id": "review-outer", "status": "completed"},
            },
        })
        # EOF/disconnect racing the consumer must not replace the already
        # retained authoritative terminal with a bare sentinel.
        handle._force_turn_sentinel(handle._turn_q)
        frames = [frame async for frame in handle.receive_response()]
        assert len(frames) == 2
        assert isinstance(frames[0], CodexManagedOverflow)
        assert frames[1]["method"] == "turn/completed"

    asyncio.run(run())


def test_codex_managed_bridge_absorbs_normal_burst_before_consumer_runs():
    class BurstCfg(_Cfg):
        turn_reader_queue_cap = 4
        ws_max_size_bytes = 1024 * 1024

    async def run():
        handle = CodexHandle(BurstCfg())
        handle.thread_id = "normal-burst"
        handle.turn_id = "normal-burst-turn"
        handle.turn_active = True
        handle._open_managed_stream()

        # A tool/reasoning burst can outrun the relay-facing consumer for a few
        # event-loop turns. Four frames is a backpressure setting for Machine's
        # reader, not a safe app-server notification window.
        for index in range(32):
            await handle._dispatch({
                "method": "item/started",
                "params": {
                    "threadId": "normal-burst",
                    "turnId": "normal-burst-turn",
                    "item": {
                        "id": f"burst-{index}",
                        "type": "reasoning",
                    },
                },
            })
        await handle._dispatch({
            "method": "turn/completed",
            "params": {
                "threadId": "normal-burst",
                "turn": {
                    "id": "normal-burst-turn",
                    "status": "completed",
                },
            },
        })

        frames = [frame async for frame in handle.receive_response()]
        assert len(frames) == 33
        assert not any(
            isinstance(frame, CodexManagedOverflow) for frame in frames)
        assert frames[-1]["method"] == "turn/completed"

    asyncio.run(run())


def test_codex_buffered_stdout_yields_to_managed_consumer_without_false_overflow():
    class FairCfg(_Cfg):
        turn_reader_queue_cap = 4
        ws_max_size_bytes = 1024 * 1024

    class BufferedStdout:
        def __init__(self, messages):
            self.lines = [
                (json.dumps(message) + "\n").encode() for message in messages
            ]

        async def readline(self):
            return self.lines.pop(0) if self.lines else b""

    async def run():
        handle = CodexHandle(FairCfg())
        handle.thread_id = "fair-thread"
        handle.turn_id = "fair-turn"
        handle.turn_active = True
        handle._open_managed_stream()
        messages = [
            {
                "method": "item/started",
                "params": {
                    "threadId": "fair-thread", "turnId": "fair-turn",
                    "item": {"id": f"item-{index}", "type": "reasoning"},
                },
            }
            for index in range(5)
        ]
        messages.append({
            "method": "turn/completed",
            "params": {
                "threadId": "fair-thread",
                "turn": {"id": "fair-turn", "status": "completed"},
            },
        })
        proc = SimpleNamespace(stdout=BufferedStdout(messages))
        consumer = asyncio.create_task(
            _collect_async(handle.receive_response()))
        await handle._read_loop(proc, handle._generation)
        frames = await consumer

        assert len(frames) == len(messages)
        assert not any(isinstance(frame, CodexManagedOverflow)
                       for frame in frames)
        assert frames[-1]["method"] == "turn/completed"

    async def _collect_async(stream):
        return [item async for item in stream]

    asyncio.run(run())


def test_codex_review_response_turn_is_interruptible_and_streamed():
    async def run():
        machine, transport = _mk_machine()
        ctx = _mk_ctx("review-managed", "review-managed")
        ctx.engine = "codex"
        handle = CodexHandle(_Cfg())
        handle.proc = SimpleNamespace(returncode=None)
        handle.thread_id = ctx.session_id
        ctx.sdk = handle
        machine.sessions[ctx.key] = ctx

        async def no_external(_sid):
            return False

        machine._prime_codex_ownership = no_external
        review_entered = asyncio.Event()
        release_review = asyncio.Event()
        requests = []

        async def request(method, params=None):
            requests.append((method, params))
            if method == "review/start":
                review_entered.set()
                await release_review.wait()
                return {
                    "reviewThreadId": ctx.session_id,
                    "turn": {"id": "review-outer"},
                }
            if method == "turn/interrupt":
                return {}
            raise AssertionError(method)

        handle._request = request
        command = SimpleNamespace(
            session_id=ctx.session_id,
            engine="codex",
            space="code",
            target="uncommittedChanges",
            value=None,
            client_id="client-1",
        )
        start_task = asyncio.create_task(
            machine._handle_start_review(command))
        await review_entered.wait()

        # The RPC has not returned yet, but Review already owns the session.
        assert ctx.state == "running"
        duplicate = await machine._handle_query(SimpleNamespace(
            sid=ctx.key, prompt="must be busy", images=None, files=None,
            msg_id="review-race",
        ))
        assert isinstance(duplicate, Error) and duplicate.code == "busy"

        release_review.set()
        await start_task
        assert ctx.turn_task is not None
        # review/start's response names the outer UI lifecycle. The next
        # turn/started notification is the strict app-server interrupt target.
        await handle._dispatch({
            "method": "turn/started",
            "params": {
                "threadId": ctx.session_id,
                "turn": {"id": "review-inner"},
            },
        })
        assert handle.owned_turn_ids == {"review-outer", "review-inner"}
        await machine._handle_interrupt(SimpleNamespace(sid=ctx.key))
        assert ctx.state == "interrupting"
        # A repeated stop is idempotent and never produces not_running.
        errors_before = len([item for item in transport.sent
                             if isinstance(item, Error)])
        await machine._handle_interrupt(SimpleNamespace(sid=ctx.key))
        assert len([item for item in transport.sent
                    if isinstance(item, Error)]) == errors_before
        assert requests[-1] == (
            "turn/interrupt",
            {"threadId": ctx.session_id, "turnId": "review-inner"},
        )

        await handle._dispatch({
            "method": "turn/completed",
            "params": {
                "threadId": ctx.session_id,
                "turn": {"id": "review-outer", "status": "interrupted"},
            },
        })
        turn_task = ctx.turn_task
        assert turn_task is not None
        await turn_task

        assert ctx.state == "idle"
        assert [item.state for item in transport.sent
                if isinstance(item, StateEvent)][-3:] == [
                    "running", "interrupting", "idle"]
        anchors = [item for item in transport.sent
                   if isinstance(item, UserMsg)
                   and item.msg_id == "review-outer"]
        assert len(anchors) == 1 and anchors[0].prompt == ""
        terminal = [item for item in transport.sent
                    if isinstance(item, TurnEnd)][-1]
        assert terminal.turn_id == "review-outer"
        assert terminal.result.subtype == "error_during_execution"

    asyncio.run(run())


def test_codex_review_start_failure_clears_turn_ownership():
    async def run():
        handle = CodexHandle(_Cfg())
        handle.proc = SimpleNamespace(returncode=None)
        handle.thread_id = "review-failure"

        async def request(_method, _params=None):
            raise RuntimeError("review rejected")

        handle._request = request
        with pytest.raises(RuntimeError, match="review rejected"):
            await handle.start_review({"type": "uncommittedChanges"})
        assert handle.turn_active is False
        assert handle.turn_start_pending is False
        assert handle.turn_id is None
        assert handle._turn_q is None

        with pytest.raises(RuntimeError, match="not running"):
            await handle.interrupt()

    asyncio.run(run())


def test_codex_review_interrupt_timeout_reconnects_and_unlocks():
    async def run():
        machine, transport = _mk_machine()
        ctx = _mk_ctx("review-timeout", "review-timeout")
        ctx.engine = "codex"
        never = asyncio.Event()
        reconnects = []

        class StalledSdk:
            async def receive_response(self):
                await never.wait()
                if False:
                    yield None

            async def force_reconnect(self, session_id, cwd):
                reconnects.append((session_id, cwd))

        ctx.sdk = StalledSdk()
        ctx.state = "interrupting"
        ctx.active_msg_id = "review-timeout-turn"
        ctx.interrupt_event.set()
        ctx.interrupt_deadline = asyncio.get_running_loop().time() + 0.01
        ctx.turn_task = asyncio.current_task()

        await machine._run_codex_review_turn(ctx, "review-timeout-turn")

        assert reconnects == [(ctx.session_id, ctx.cwd)]
        assert ctx.state == "idle"
        assert ctx.turn_task is None
        timeout = [item for item in transport.sent
                   if isinstance(item, Error)
                   and item.code == "drain_timeout"]
        assert len(timeout) == 1
        terminal = [item for item in transport.sent
                    if isinstance(item, TurnEnd)][-1]
        assert terminal.result.subtype == "error_during_execution"

    asyncio.run(run())


def test_codex_review_overflow_preserves_authoritative_success_terminal():
    async def run():
        machine, transport = _mk_machine()
        ctx = _mk_ctx("review-overflow", "review-overflow")
        ctx.engine = "codex"
        ctx.state = "running"
        ctx.active_msg_id = "review-overflow-turn"
        ctx.turn_task = asyncio.current_task()

        class OverflowSdk:
            async def receive_response(self):
                yield CodexManagedOverflow("review-overflow-turn")
                yield {
                    "method": "turn/completed",
                    "params": {"turn": {
                        "id": "review-overflow-turn", "status": "completed",
                    }},
                }

        ctx.sdk = OverflowSdk()
        await machine._run_codex_review_turn(ctx, "review-overflow-turn")

        assert not [event for event in transport.sent
                    if isinstance(event, Error)]
        terminal = [event for event in transport.sent
                    if isinstance(event, TurnEnd)]
        assert len(terminal) == 1
        assert terminal[0].turn_id == "review-overflow-turn"
        assert terminal[0].result.subtype == "success"
        assert terminal[0].result.is_error is False

    asyncio.run(run())


def test_managed_codex_overflow_preserves_authoritative_success_terminal():
    async def run():
        machine, transport = _mk_machine()
        ctx = _mk_ctx("managed-overflow", "managed-overflow")
        ctx.engine = "codex"
        ctx.state = "running"
        ctx.active_msg_id = "browser-overflow-message"
        ctx.turn_task = asyncio.current_task()

        class OverflowSdk:
            tier_dirty = False
            model = None
            effort = None
            collaboration_mode = "default"
            service_tier = None

            async def query(self, _prompt, images=None):
                return "managed-overflow-turn"

            async def receive_response(self):
                yield CodexManagedOverflow("managed-overflow-turn")
                yield {
                    "method": "turn/completed",
                    "params": {"turn": {
                        "id": "managed-overflow-turn", "status": "completed",
                    }},
                }

        ctx.sdk = OverflowSdk()
        machine._begin_codex_checkpoint = lambda _ctx: asyncio.sleep(0)
        machine._accept_codex_checkpoint = lambda _ctx: asyncio.sleep(0)
        await machine._run_turn(ctx, "hello")

        assert not [event for event in transport.sent
                    if isinstance(event, Error)]
        terminal = [event for event in transport.sent
                    if isinstance(event, TurnEnd)]
        assert len(terminal) == 1
        assert terminal[0].turn_id == "managed-overflow-turn"
        assert terminal[0].result.subtype == "success"
        assert terminal[0].result.is_error is False

    asyncio.run(run())


def test_managed_codex_overflow_reports_live_delay_without_idle_warning():
    async def run():
        machine, transport = _mk_machine()
        machine.cfg.codex_turn_idle_warn_seconds = 0.02
        ctx = _mk_ctx("managed-overflow-wait", "managed-overflow-wait")
        ctx.engine = "codex"
        ctx.state = "running"
        ctx.active_msg_id = "browser-overflow-wait"
        overflow_seen = asyncio.Event()
        release = asyncio.Event()

        class OverflowSdk:
            tier_dirty = False
            model = None
            effort = None
            collaboration_mode = "default"
            service_tier = None

            async def query(self, _prompt, images=None):
                return "managed-overflow-wait-turn"

            async def receive_response(self):
                yield CodexManagedOverflow("managed-overflow-wait-turn")
                overflow_seen.set()
                await release.wait()
                yield {
                    "method": "turn/completed",
                    "params": {"turn": {
                        "id": "managed-overflow-wait-turn",
                        "status": "completed",
                    }},
                }

        ctx.sdk = OverflowSdk()
        machine._begin_codex_checkpoint = lambda _ctx: asyncio.sleep(0)
        machine._accept_codex_checkpoint = lambda _ctx: asyncio.sleep(0)
        turn = asyncio.create_task(machine._run_turn(ctx, "hello"))

        await asyncio.wait_for(overflow_seen.wait(), timeout=0.2)
        for _ in range(20):
            if any(
                isinstance(event, StateEvent)
                and event.msg_id == "browser-overflow-wait"
                and event.detail
                for event in transport.sent
            ):
                break
            await asyncio.sleep(0)
        await asyncio.sleep(0.04)

        notices = [
            event for event in transport.sent
            if isinstance(event, StateEvent)
            and event.msg_id == "browser-overflow-wait"
            and event.detail
        ]
        assert len(notices) == 1
        assert "实时过程暂时延迟" in notices[0].detail
        assert "没有收到新进展" not in notices[0].detail
        assert ctx.state == "running"

        release.set()
        await asyncio.wait_for(turn, timeout=0.5)

        assert ctx.state == "idle"
        assert any(
            isinstance(event, StateEvent)
            and event.msg_id == "browser-overflow-wait"
            and event.detail is None
            for event in transport.sent
        )

    asyncio.run(run())


def test_managed_codex_overflow_keeps_authoritative_failure_terminal():
    async def run():
        machine, transport = _mk_machine()
        ctx = _mk_ctx("managed-overflow-failed", "managed-overflow-failed")
        ctx.engine = "codex"
        ctx.state = "running"
        ctx.active_msg_id = "browser-overflow-failed"
        ctx.turn_task = asyncio.current_task()

        class OverflowSdk:
            tier_dirty = False
            model = None
            effort = None
            collaboration_mode = "default"
            service_tier = None

            async def query(self, _prompt, images=None):
                return "managed-overflow-failed-turn"

            async def receive_response(self):
                yield CodexManagedOverflow("managed-overflow-failed-turn")
                yield {
                    "method": "turn/completed",
                    "params": {"turn": {
                        "id": "managed-overflow-failed-turn",
                        "status": "failed",
                        "error": {"message": "PRIVATE_PROVIDER_DIAGNOSTIC"},
                    }},
                }

        ctx.sdk = OverflowSdk()
        machine._begin_codex_checkpoint = lambda _ctx: asyncio.sleep(0)
        machine._accept_codex_checkpoint = lambda _ctx: asyncio.sleep(0)
        await machine._run_turn(ctx, "hello")

        errors = [event for event in transport.sent if isinstance(event, Error)]
        assert len(errors) == 1
        assert errors[0].message == "Codex 本次回复未完成，请重试。"
        assert "PRIVATE_PROVIDER_DIAGNOSTIC" not in errors[0].message
        terminal = [event for event in transport.sent
                    if isinstance(event, TurnEnd)]
        assert len(terminal) == 1
        assert terminal[0].result.subtype == "error"
        assert terminal[0].result.is_error is True

    asyncio.run(run())


def test_codex_turn_started_notification_tracks_automatic_turn_id():
    async def run():
        handle = CodexHandle(_Cfg())
        await handle._dispatch({
            "method": "turn/started",
            "params": {"turn": {"id": "automatic"}},
        })
        assert handle.turn_id == "automatic"
        assert handle.turn_active is True
        assert handle.owned_turn_ids == {"automatic"}

    asyncio.run(run())


def test_shared_codex_drops_unattributed_provider_error_from_other_thread():
    async def run():
        handle = CodexHandle(_Cfg())
        handle._using_daemon_proxy = True
        handle.thread_id = "current-thread"
        handle.turn_id = "current-turn"
        handle.turn_active = True
        handle._turn_q = asyncio.Queue()

        await handle._dispatch({
            "method": "error",
            "params": {
                "willRetry": True,
                "error": {"message": "Reconnecting... 2/5"},
            },
        })

        assert handle._turn_q.empty()
        assert handle.turn_active is True

    asyncio.run(run())


def test_shared_codex_keeps_attributed_provider_error_for_current_turn():
    async def run():
        handle = CodexHandle(_Cfg())
        handle._using_daemon_proxy = True
        handle.thread_id = "current-thread"
        handle.turn_id = "current-turn"
        handle.turn_active = True
        handle._turn_q = asyncio.Queue()
        message = {
            "method": "error",
            "params": {
                "threadId": "current-thread",
                "turnId": "current-turn",
                "willRetry": True,
                "error": {"message": "Reconnecting... 2/5"},
            },
        }

        await handle._dispatch(message)

        assert await asyncio.wait_for(handle._turn_q.get(), timeout=0.1) == message

    asyncio.run(run())


def test_private_codex_keeps_legacy_unattributed_provider_error():
    async def run():
        handle = CodexHandle(_Cfg())
        handle.thread_id = "current-thread"
        handle.turn_active = True
        handle._turn_q = asyncio.Queue()
        message = {
            "method": "error",
            "params": {
                "willRetry": True,
                "error": {"message": "Reconnecting... 2/5"},
            },
        }

        await handle._dispatch(message)

        assert await asyncio.wait_for(handle._turn_q.get(), timeout=0.1) == message

    asyncio.run(run())


def test_codex_spontaneous_lifecycle_detaches_old_queue_and_filters_local_turn():
    async def run():
        seen = []

        async def on_lifecycle(phase, turn_id):
            seen.append((phase, turn_id))

        handle = CodexHandle(_Cfg(), turn_lifecycle_callback=on_lifecycle)
        old_queue = asyncio.Queue()
        handle._turn_q = old_queue
        handle.turn_active = False  # the old turn completed; consumer is unwinding
        await handle._dispatch({
            "method": "turn/started",
            "params": {"turn": {"id": "goal-auto"}},
        })
        assert handle._turn_q is None
        assert seen == [("started", "goal-auto")]
        await handle._dispatch({
            "method": "turn/completed",
            "params": {"turn": {"id": "goal-auto", "status": "completed"}},
        })
        assert seen[-1] == ("completed", "goal-auto")

        # query() marks turn_active before its turn/start RPC. Its notifications
        # remain on the managed response queue and never double-drive Machine.
        local_queue = asyncio.Queue()
        handle._turn_q = local_queue
        handle.turn_active = True
        await handle._dispatch({
            "method": "turn/started",
            "params": {"turn": {"id": "remote-local"}},
        })
        assert handle._turn_q is local_queue
        assert seen == [
            ("started", "goal-auto"), ("completed", "goal-auto"),
        ]
        await handle._dispatch({
            "method": "turn/completed",
            "params": {"turn": {"id": "remote-local", "status": "completed"}},
        })
        assert seen == [
            ("started", "goal-auto"), ("completed", "goal-auto"),
        ]

    asyncio.run(run())


def test_codex_stderr_drain_records_only_byte_count(monkeypatch):
    class NoDecode(bytes):
        def decode(self, *_args, **_kwargs):
            raise AssertionError("stderr content must not be decoded")

    class Stderr:
        def __init__(self):
            self.lines = [NoDecode(b"secret stderr payload\n"), b""]

        async def readline(self):
            return self.lines.pop(0)

    seen = []
    monkeypatch.setattr(
        codex_handle_module.log, "debug",
        lambda message, **fields: seen.append((message, fields)),
    )
    handle = CodexHandle(_Cfg())
    process = SimpleNamespace(stderr=Stderr())

    asyncio.run(handle._drain_stderr(process, handle._generation))

    assert seen == [("codex stderr", {"bytes": len(b"secret stderr payload\n")})]


def test_codex_catalog_normalization_is_structurally_bounded(monkeypatch):
    monkeypatch.setattr(codex_models_module, "_MAX_MODELS", 2)
    monkeypatch.setattr(codex_models_module, "_MAX_EFFORTS", 2)
    monkeypatch.setattr(codex_models_module, "_MAX_CATALOG_TEXT", 8)
    raw = [
        "not-a-model",
        {
            "id": "model-one",
            "displayName": "display-name-too-long",
            "description": "description-too-long",
            "supportedReasoningEfforts": [
                {"reasoningEffort": "low"},
                {"reasoningEffort": "high"},
                {"reasoningEffort": "ultra"},
                "bad",
            ],
        },
        {"id": "model-two"},
        {"id": "model-three"},
    ]

    normalized = codex_models_module._normalize(raw)

    assert [model["id"] for model in normalized] == ["model-one", "model-two"]
    assert normalized[0]["display_name"] == "display-"
    assert normalized[0]["description"] == "descript"
    assert normalized[0]["efforts"] == ["low", "high"]


def test_codex_binary_resolution_probes_bounded_candidates_and_picks_newest(
        monkeypatch):
    monkeypatch.setattr(codex_handle_module, "_BIN_CACHE", None)
    monkeypatch.setattr(codex_handle_module, "_BIN_CACHE_INVENTORY", None)
    monkeypatch.delenv("CODEX_BIN", raising=False)
    monkeypatch.setattr(
        codex_handle_module, "_codex_candidates", lambda: ["old", "new", "broken"])
    versions = {"old": (0, 140, 0), "new": (0, 144, 1), "broken": (-1,)}
    monkeypatch.setattr(
        codex_handle_module, "_codex_version", lambda path: versions[path])

    assert codex_handle_module._resolve_codex_bin() == "new"


def test_oversized_resume_prefers_only_a_newer_official_desktop_core(
        monkeypatch, tmp_path):
    managed = tmp_path / "managed-codex"
    desktop = tmp_path / "desktop-codex"
    rollout = tmp_path / "rollout.jsonl"
    managed.write_text("managed")
    desktop.write_text("desktop")
    managed.chmod(0o755)
    desktop.chmod(0o755)
    with rollout.open("wb") as stream:
        stream.truncate(
            codex_handle_module._OVERSIZED_RESUME_PRIVATE_CORE_MIN_BYTES)

    monkeypatch.delenv("CODEX_BIN", raising=False)
    monkeypatch.setattr(
        codex_handle_module, "_CODEX_DESKTOP_BIN_CANDIDATES",
        (str(desktop),),
    )
    monkeypatch.setattr(
        codex_handle_module, "codex_rollout_path", lambda _sid: str(rollout))
    versions = {
        str(managed): (0, 144, 6),
        str(desktop): (0, 145, 0),
    }
    monkeypatch.setattr(
        codex_handle_module, "_codex_version", lambda path: versions[path])

    choose = codex_handle_module._newer_private_core_for_oversized_resume
    assert choose(str(managed), "huge-thread") == str(desktop)

    with rollout.open("r+b") as stream:
        stream.truncate(
            codex_handle_module._OVERSIZED_RESUME_PRIVATE_CORE_MIN_BYTES - 1)
    assert choose(str(managed), "small-thread") is None

    with rollout.open("r+b") as stream:
        stream.truncate(
            codex_handle_module._OVERSIZED_RESUME_PRIVATE_CORE_MIN_BYTES)
    versions[str(desktop)] = versions[str(managed)]
    assert choose(str(managed), "same-version-thread") is None

    versions[str(desktop)] = (0, 145, 0)
    monkeypatch.setenv("CODEX_BIN", str(managed))
    assert choose(str(managed), "explicit-bin-thread") is None


def test_http_resume_fallback_is_bounded_to_oversized_desktop_openai_rollout(
        monkeypatch, tmp_path):
    rollout = tmp_path / "rollout.jsonl"

    def write_meta(**overrides):
        payload = {
            "id": "huge-thread",
            "originator": "Codex Desktop",
            "model_provider": "openai",
        }
        payload.update(overrides)
        first = json.dumps({
            "type": "session_meta", "payload": payload,
        }).encode() + b"\n"
        with rollout.open("wb") as stream:
            stream.write(first)
            stream.truncate(
                codex_handle_module._OVERSIZED_RESUME_PRIVATE_CORE_MIN_BYTES)

    monkeypatch.setattr(
        codex_handle_module, "codex_rollout_path", lambda _sid: str(rollout))
    requires_http = (
        codex_handle_module._oversized_desktop_openai_resume_requires_http)

    write_meta()
    assert requires_http("huge-thread") is True

    write_meta(originator="codex_cli_rs")
    assert requires_http("huge-thread") is False

    write_meta(model_provider="cubence")
    assert requires_http("huge-thread") is False

    write_meta()
    with rollout.open("r+b") as stream:
        stream.truncate(
            codex_handle_module._OVERSIZED_RESUME_PRIVATE_CORE_MIN_BYTES - 1)
    assert requires_http("small-thread") is False

    with rollout.open("wb") as stream:
        stream.write(b"{" + b"x" * (
            codex_handle_module._ROLLOUT_SESSION_META_MAX_BYTES + 1))
        stream.truncate(
            codex_handle_module._OVERSIZED_RESUME_PRIVATE_CORE_MIN_BYTES)
    assert requires_http("oversized-meta") is False


def test_codex_binary_resolution_reprobes_after_symlink_upgrade(
        monkeypatch, tmp_path):
    old = tmp_path / "codex-0.144.1"
    new = tmp_path / "codex-0.144.4"
    old.write_text("old")
    new.write_text("new release with a different identity")
    old.chmod(0o755)
    new.chmod(0o755)
    current = tmp_path / "codex"
    current.symlink_to(old)

    monkeypatch.setattr(codex_handle_module, "_BIN_CACHE", None)
    monkeypatch.setattr(codex_handle_module, "_BIN_CACHE_INVENTORY", None)
    monkeypatch.delenv("CODEX_BIN", raising=False)
    monkeypatch.setattr(
        codex_handle_module, "_codex_candidates", lambda: [str(current)])
    probed = []

    def version(path):
        probed.append(os.path.realpath(path))
        return ((0, 144, 4) if os.path.realpath(path) == str(new)
                else (0, 144, 1))

    monkeypatch.setattr(codex_handle_module, "_codex_version", version)
    assert codex_handle_module._resolve_codex_bin() == str(current)
    assert probed == [str(old)]
    assert codex_handle_module._resolve_codex_bin() == str(current)
    assert probed == [str(old)]

    current.unlink()
    current.symlink_to(new)
    assert codex_handle_module._resolve_codex_bin() == str(current)
    assert probed == [str(old), str(new)]


def test_codex_config_defaults_use_only_top_level_toml_keys(
        monkeypatch, tmp_path):
    config = tmp_path / "config.toml"
    config.write_text(
        'model = "gpt-top"\nmodel_reasoning_effort = "high"\n\n'
        '[profiles.work]\nmodel = "gpt-nested"\n'
        'model_reasoning_effort = "low"\nservice_tier = "fast"\n')
    monkeypatch.setattr(codex_sessions_module, "_CONFIG", str(config))

    assert codex_sessions_module.codex_model() == "gpt-top"
    assert codex_sessions_module.codex_effort() == "high"
    assert codex_sessions_module.codex_fast_enabled() is False


def test_codex_thread_settings_update_uses_official_01441_shapes():
    async def run():
        handle = CodexHandle(_Cfg())
        handle.thread_id = "thread-settings"
        handle.model = "gpt-before"
        handle.effort = "high"
        requests = []

        async def request(method, params=None):
            requests.append((method, params))
            return {}

        handle._request = request
        await handle.set_permission_mode("on-request")
        await handle.set_model("gpt-after")
        await handle.set_effort("ultra")
        await handle.set_collaboration_mode("plan")
        await handle.set_service_tier("fast")
        await handle.set_service_tier(None)

        assert requests == [
            ("thread/settings/update", {
                "threadId": "thread-settings", "approvalPolicy": "on-request",
            }),
            ("thread/settings/update", {
                "threadId": "thread-settings", "model": "gpt-after",
            }),
            ("thread/settings/update", {
                "threadId": "thread-settings", "effort": "ultra",
            }),
            ("thread/settings/update", {
                "threadId": "thread-settings",
                "collaborationMode": {
                    "mode": "plan",
                    "settings": {
                        "model": "gpt-after",
                        "developer_instructions": None,
                        "reasoning_effort": "ultra",
                    },
                },
            }),
            ("thread/settings/update", {
                "threadId": "thread-settings", "serviceTier": "fast",
            }),
            ("thread/settings/update", {
                "threadId": "thread-settings", "serviceTier": None,
            }),
        ]
        assert handle.approval == "on-request"
        assert handle.model == "gpt-after"
        assert handle.effort == "ultra"
        assert handle.collaboration_mode == "plan"
        assert handle.service_tier is None

    asyncio.run(run())


def test_codex_authoritative_thread_settings_restore_after_resume_or_notification():
    async def run():
        handle = CodexHandle(_Cfg())
        handle.model = "stale-model"
        handle.effort = "low"
        handle.approval = "never"
        handle.service_tier = None

        handle._apply_thread_settings({
            "model": "persisted-model",
            "reasoningEffort": "xhigh",
            "approvalPolicy": "on-request",
            "serviceTier": "fast",
        })
        assert (handle.model, handle.effort, handle.approval,
                handle.service_tier) == (
            "persisted-model", "xhigh", "on-request", "fast")

        handle.thread_id = "thread-settings"
        await handle._dispatch({
            "method": "thread/settings/updated",
            "params": {
                "threadId": "thread-settings",
                "threadSettings": {
                    "model": "notification-model",
                    "effort": "ultra",
                    "approvalPolicy": "untrusted",
                    "serviceTier": None,
                    "collaborationMode": {
                        "mode": "plan",
                        "settings": {"model": "notification-model"},
                    },
                },
            },
        })
        assert (handle.model, handle.effort, handle.approval,
                handle.service_tier, handle.collaboration_mode) == (
            "notification-model", "ultra", "untrusted", None, "plan")

    asyncio.run(run())


def test_codex_granular_approval_survives_resume_and_turn_start():
    async def run():
        handle = CodexHandle(_Cfg())
        handle.proc = SimpleNamespace(returncode=None)
        handle.thread_id = "granular-thread"
        granular = {"granular": {
            "mcp_elicitations": True,
            "rules": False,
            "sandbox_approval": True,
            "request_permissions": True,
        }}
        handle._apply_thread_settings({"approvalPolicy": granular})
        assert handle.approval == "on-request"
        assert handle.approval_policy == granular

        requests = []

        async def request(method, params=None):
            requests.append((method, params))
            return {"turn": {"id": "granular-turn"}}

        handle._request = request
        await handle.query("keep granular")
        assert requests[-1][1]["approvalPolicy"] == granular

    asyncio.run(run())


def test_codex_work_profile_grants_runtime_helper_binary_and_registered_cwd(
        monkeypatch):
    async def run():
        spawned = []

        async def capture(*args, **_kwargs):
            spawned.extend(args)
            raise RuntimeError("captured work profile")

        monkeypatch.setenv("CODEX_HOME", "/home/test/.codex-custom")
        monkeypatch.setattr(
            codex_handle_module, "_resolve_codex_bin", lambda: "/usr/bin/codex")
        monkeypatch.setattr(
            codex_handle_module.asyncio, "create_subprocess_exec", capture)
        cwd = "/home/test/.codex-custom/cc-remote/work/chats/work-1/workspace"
        with pytest.raises(RuntimeError, match="captured work profile"):
            await CodexHandle(_Cfg(), cwd=cwd, work_mode=True).connect()

        assert spawned[:3] == ["/usr/bin/codex", "app-server", "--stdio"]
        overrides = [
            spawned[index + 1]
            for index, value in enumerate(spawned[:-1])
            if value == "-c"
        ]
        assert 'default_permissions="cc_remote_work"' in overrides
        filesystem = next(
            value for value in overrides
            if value.startswith("permissions.cc_remote_work.filesystem="))
        assert '":minimal" = "read"' in filesystem
        assert '"~"' not in filesystem
        assert '"/home/test/.codex-custom/tmp" = "read"' in filesystem
        assert '"/usr/bin/codex" = "read"' in filesystem
        assert f'"{cwd}" = "write"' in filesystem
        assert "permissions.cc_remote_work.network.enabled=false" in overrides

    asyncio.run(run())


def test_codex_work_turn_uses_named_profile_without_legacy_sandbox_policy():
    async def run():
        cwd = "/home/test/.codex/cc-remote/work/chats/work-1/workspace"
        handle = CodexHandle(_Cfg(), cwd=cwd, work_mode=True)
        handle.proc = SimpleNamespace(returncode=None)
        handle.thread_id = "work-thread"
        handle.model = "gpt-work"
        handle.effort = None
        requests = []

        async def request(method, params=None):
            requests.append((method, params))
            return {"turn": {"id": "work-turn"}}

        handle._request = request
        await handle.query("create a file")

        method, params = requests[-1]
        assert method == "turn/start"
        assert params["cwd"] == cwd
        assert params["approvalPolicy"] == "never"
        assert "sandboxPolicy" not in params

    asyncio.run(run())


@pytest.mark.parametrize("work_mode,http_only", [
    (False, False),
    (False, True),
    (True, False),
])
def test_codex_resume_omits_local_defaults_and_adopts_app_server_settings(
        monkeypatch, work_mode, http_only):
    class FakeProcess:
        pid = 424243
        returncode = None
        stdin = stdout = stderr = SimpleNamespace()

        async def wait(self):
            self.returncode = 0
            return 0

        def terminate(self):
            self.returncode = 0

        def kill(self):
            self.returncode = 0

    async def run():
        process = FakeProcess()
        monkeypatch.setattr(
            codex_handle_module, "_resolve_codex_bin", lambda: "/usr/bin/codex")
        monkeypatch.setattr(
            codex_handle_module,
            "_oversized_desktop_openai_resume_requires_http",
            lambda _sid: http_only,
        )
        monkeypatch.setattr(
            codex_handle_module.asyncio, "create_subprocess_exec",
            lambda *_args, **_kwargs: asyncio.sleep(0, result=process))
        monkeypatch.setattr(codex_handle_module.os, "killpg", lambda *_args: None)
        handle = CodexHandle(_Cfg(), work_mode=work_mode)
        calls = []

        async def idle(*_args):
            await asyncio.Event().wait()

        async def request(method, params=None):
            calls.append((method, params))
            if method == "initialize":
                return {"userAgent": "codex_cli_rs/0.144.6 (test)"}
            if method == "skills/list":
                return _WORK_SKILLS_RESPONSE
            if method == "config/read":
                return _WORK_CONFIG_RESPONSE
            if method == "thread/resume":
                return {
                    "thread": {"id": "resume-thread"},
                    "model": "persisted-model",
                    "reasoningEffort": "ultra",
                    "approvalPolicy": "on-request",
                    "serviceTier": "fast",
                }
            raise AssertionError(method)

        handle._read_loop = idle
        handle._drain_stderr = idle
        handle._request = request
        handle._notify = lambda *_args, **_kwargs: asyncio.sleep(0)
        await handle.connect(resume_id="resume-thread", cwd="/tmp")

        expected_resume = {
            "threadId": "resume-thread", "cwd": "/tmp",
            "excludeTurns": True,
        }
        if http_only:
            expected_resume["modelProvider"] = (
                codex_handle_module._OPENAI_HTTP_RESUME_PROVIDER_ID)
        if work_mode:
            expected_resume.update({
                "baseInstructions": WORK_BASE_INSTRUCTIONS,
                "developerInstructions": WORK_DEVELOPER_INSTRUCTIONS,
                "personality": "none",
                "config": _expected_work_config(),
            })
        resume_call = next(call for call in calls if call[0] == "thread/resume")
        assert resume_call == ("thread/resume", expected_resume)
        assert not ({
            "sandbox", "sandboxPolicy", "approvalsReviewer",
        } & resume_call[1].keys())
        if not work_mode:
            assert not ({"config", "personality"} & resume_call[1].keys())
        assert (handle.model, handle.effort, handle.approval,
                handle.service_tier) == (
            "persisted-model", "ultra",
            "never" if work_mode else "on-request", "fast")
        await handle.disconnect()

    asyncio.run(run())


def test_codex_legacy_resume_rejects_oversized_rollout_before_request(
        monkeypatch, tmp_path):
    class FakeProcess:
        pid = 424245
        returncode = None
        stdin = stdout = stderr = SimpleNamespace()

        async def wait(self):
            self.returncode = 0
            return 0

        def terminate(self):
            self.returncode = 0

        def kill(self):
            self.returncode = 0

    async def run():
        process = FakeProcess()
        rollout = tmp_path / "large.jsonl"
        with rollout.open("wb") as stream:
            stream.seek(codex_handle_module._PROXY_MESSAGE_MAX)
            stream.write(b"\n")
        monkeypatch.setattr(
            codex_handle_module, "_resolve_codex_bin", lambda: "/usr/bin/codex")
        monkeypatch.setattr(
            codex_handle_module, "codex_rollout_path", lambda _sid: str(rollout))
        monkeypatch.setattr(
            codex_handle_module.asyncio, "create_subprocess_exec",
            lambda *_args, **_kwargs: asyncio.sleep(0, result=process))
        monkeypatch.setattr(codex_handle_module.os, "killpg", lambda *_args: None)

        handle = CodexHandle(_Cfg())
        calls = []

        async def idle(*_args):
            await asyncio.Event().wait()

        async def request(method, params=None):
            calls.append((method, params))
            if method == "initialize":
                return {"userAgent": "codex_cli_rs/0.144.5 (test)"}
            raise AssertionError(method)

        handle._read_loop = idle
        handle._drain_stderr = idle
        handle._request = request
        handle._notify = lambda *_args, **_kwargs: asyncio.sleep(0)
        with pytest.raises(RuntimeError, match="不支持超长会话的轻量恢复"):
            await handle.connect(resume_id="large-thread", cwd="/tmp")
        assert [method for method, _params in calls] == ["initialize"]
        assert handle.proc is None

    asyncio.run(run())


@pytest.mark.parametrize("work_mode", [False, True])
def test_codex_fresh_thread_persists_all_first_turn_settings_before_return(
        monkeypatch, work_mode):
    class FakeProcess:
        pid = 424244
        returncode = None
        stdin = stdout = stderr = SimpleNamespace()

        async def wait(self):
            self.returncode = 0
            return 0

        def terminate(self):
            self.returncode = 0

        def kill(self):
            self.returncode = 0

    async def run():
        process = FakeProcess()
        monkeypatch.setattr(
            codex_handle_module, "_resolve_codex_bin", lambda: "/usr/bin/codex")
        monkeypatch.setattr(
            codex_handle_module.asyncio, "create_subprocess_exec",
            lambda *_args, **_kwargs: asyncio.sleep(0, result=process))
        monkeypatch.setattr(codex_handle_module.os, "killpg", lambda *_args: None)

        handle = CodexHandle(_Cfg(), work_mode=work_mode)
        handle.model = "first-model"
        handle.effort = "ultra"
        handle.applied_effort = "ultra"
        handle.approval = "on-request"
        handle.collaboration_mode = "plan"
        handle.service_tier = "fast"
        calls = []

        async def idle(*_args):
            await asyncio.Event().wait()

        async def request(method, params=None):
            calls.append((method, params))
            if method == "initialize":
                return {"userAgent": "codex_cli_rs/0.144.6 (test)"}
            if method == "skills/list":
                return _WORK_SKILLS_RESPONSE
            if method == "config/read":
                return _WORK_CONFIG_RESPONSE
            if method == "thread/start":
                return {
                    "thread": {"id": "fresh-thread"},
                    "model": "first-model",
                    "reasoningEffort": "low",
                    "approvalPolicy": "on-request",
                    "serviceTier": "priority",
                }
            if method == "thread/settings/update":
                await handle._dispatch({
                    "method": "thread/settings/updated",
                    "params": {
                        "threadId": "fresh-thread",
                        "threadSettings": {
                            "model": "first-model",
                            "effort": "ultra",
                            "approvalPolicy": "on-request",
                            "serviceTier": "priority",
                            "collaborationMode": {
                                "mode": "plan",
                                "settings": {"model": "first-model"},
                            },
                        },
                    },
                })
                return {}
            raise AssertionError(method)

        handle._read_loop = idle
        handle._drain_stderr = idle
        handle._request = request
        handle._notify = lambda *_args, **_kwargs: asyncio.sleep(0)
        await handle.connect(cwd="/tmp")

        expected_start = {
            "cwd": "/tmp",
            "approvalPolicy": "never" if work_mode else "on-request",
            "serviceTier": "fast",
            "model": "first-model",
        }
        if work_mode:
            expected_start.update({
                "baseInstructions": WORK_BASE_INSTRUCTIONS,
                "developerInstructions": WORK_DEVELOPER_INSTRUCTIONS,
                "personality": "none",
                "config": _expected_work_config(),
            })
        start_call = next(call for call in calls if call[0] == "thread/start")
        assert start_call == ("thread/start", expected_start)
        discovery_methods = [
            call for call in calls
            if call[0] in {"skills/list", "config/read"}
        ]
        if work_mode:
            assert discovery_methods == [
                ("skills/list", {"cwds": ["/tmp"], "forceReload": True}),
                ("config/read", {"cwd": "/tmp", "includeLayers": False}),
            ]
        else:
            # Code must keep the native process/config path byte-for-byte: no
            # Work inventory RPC and no thread-level config overlay.
            assert discovery_methods == []
        assert not ({
            "sandbox", "sandboxPolicy", "approvalsReviewer",
        } & start_call[1].keys())
        if not work_mode:
            assert not ({"config", "personality"} & start_call[1].keys())
        settings_call = next(
            call for call in calls if call[0] == "thread/settings/update")
        assert settings_call == ("thread/settings/update", {
            "threadId": "fresh-thread",
            "collaborationMode": {
                "mode": "plan",
                "settings": {
                    "model": "first-model",
                    "developer_instructions": (
                        WORK_DEVELOPER_INSTRUCTIONS if work_mode else None),
                    "reasoning_effort": "ultra",
                },
            },
            "effort": "ultra",
        })
        assert (handle.model, handle.effort, handle.approval,
                handle.collaboration_mode, handle.service_tier) == (
            "first-model", "ultra",
            "never" if work_mode else "on-request", "plan", "priority")
        await handle.disconnect()

    asyncio.run(run())


@pytest.mark.parametrize("work_mode", [False, True])
def test_codex_ephemeral_fork_replaces_coding_prompt_only_for_work(
        monkeypatch, work_mode):
    class FakeProcess:
        pid = 424245
        returncode = None
        stdin = stdout = stderr = SimpleNamespace()

        async def wait(self):
            self.returncode = 0
            return 0

        def terminate(self):
            self.returncode = 0

        def kill(self):
            self.returncode = 0

    async def run():
        process = FakeProcess()
        monkeypatch.setattr(
            codex_handle_module, "_resolve_codex_bin", lambda: "/usr/bin/codex")
        monkeypatch.setattr(
            codex_handle_module.asyncio, "create_subprocess_exec",
            lambda *_args, **_kwargs: asyncio.sleep(0, result=process))
        monkeypatch.setattr(codex_handle_module.os, "killpg", lambda *_args: None)

        handle = CodexHandle(_Cfg(), work_mode=work_mode)
        calls = []

        async def idle(*_args):
            await asyncio.Event().wait()

        async def request(method, params=None):
            calls.append((method, params))
            if method == "initialize":
                return {"userAgent": "codex_cli_rs/0.144.6 (test)"}
            if method == "skills/list":
                return _WORK_SKILLS_RESPONSE
            if method == "config/read":
                return _WORK_CONFIG_RESPONSE
            if method == "thread/fork":
                return {"thread": {"id": "forked-thread"}}
            raise AssertionError(method)

        handle._read_loop = idle
        handle._drain_stderr = idle
        handle._request = request
        handle._notify = lambda *_args, **_kwargs: asyncio.sleep(0)
        await handle.connect(
            resume_id="parent-thread", cwd="/tmp", fork=True)

        expected_fork = {
            "threadId": "parent-thread",
            "ephemeral": True,
            "cwd": "/tmp",
            "approvalPolicy": "never",
            "excludeTurns": True,
        }
        if work_mode:
            expected_fork.update({
                "baseInstructions": WORK_BASE_INSTRUCTIONS,
                "developerInstructions": WORK_DEVELOPER_INSTRUCTIONS,
                "personality": "none",
                "config": _expected_work_config(),
            })
        fork_call = next(call for call in calls if call[0] == "thread/fork")
        assert fork_call == ("thread/fork", expected_fork)
        await handle.disconnect()

    asyncio.run(run())


def test_codex_model_change_emits_app_server_adjusted_effort():
    async def run():
        machine, transport = _mk_machine()
        ctx = _mk_ctx("codex-model", "codex-model")
        handle = CodexHandle(_Cfg())
        handle.thread_id = "codex-model"
        handle.model = "gpt-before"
        handle.effort = "ultra"
        handle._reader = asyncio.create_task(asyncio.Event().wait())
        ctx.sdk = handle
        ctx.engine = "codex"
        ctx.announced_effort = "ultra"
        machine.sessions[ctx.key] = ctx

        async def request(method, params=None):
            assert (method, params) == ("thread/settings/update", {
                "threadId": "codex-model", "model": "gpt-after",
            })
            await handle._dispatch({
                "method": "thread/settings/updated",
                "params": {
                    "threadId": "codex-model",
                    "threadSettings": {
                        "model": "gpt-provider-fallback",
                        "effort": "max",
                        "approvalPolicy": "never",
                        "serviceTier": None,
                    },
                },
            })
            return {}

        handle._request = request
        await machine._handle_set_model(SimpleNamespace(
            sid="codex-model", model="gpt-after"))

        assert handle.model == "gpt-provider-fallback"
        assert handle.effort == "max"
        assert [(event.type, getattr(event, "model", None),
                 getattr(event, "effort", None))
                for event in transport.sent] == [
            ("model", "gpt-provider-fallback", None),
            ("effort", None, "max"),
        ]
        handle._reader.cancel()
        await asyncio.gather(handle._reader, return_exceptions=True)

    asyncio.run(run())


async def _dispatch_request(handle: CodexHandle, method: str, params) -> dict:
    sent: list[dict] = []

    async def send(message: dict) -> None:
        sent.append(message)

    handle._send = send  # type: ignore[method-assign]
    await handle._dispatch({
        "jsonrpc": "2.0", "id": 7, "method": method, "params": params,
    })
    if handle._server_request_tasks:
        await asyncio.gather(*list(handle._server_request_tasks))
    assert len(sent) == 1
    return sent[0]


def test_codex_approval_does_not_block_stdout_response_dispatch():
    async def run():
        started = asyncio.Event()
        release = asyncio.Event()

        async def approve(_method, _params):
            started.set()
            await release.wait()
            return "accept"

        handle = CodexHandle(_Cfg(), approval_callback=approve)
        handle.approval = "on-request"
        sent = []

        async def send(message):
            sent.append(message)

        handle._send = send
        await handle._dispatch({
            "jsonrpc": "2.0", "id": 7,
            "method": "item/commandExecution/requestApproval",
            "params": {"command": "true"},
        })
        await asyncio.wait_for(started.wait(), timeout=1)

        # The approval is still waiting for the user, but the sole reader can
        # already dispatch an unrelated response (not deadlock behind it).
        pending = asyncio.get_running_loop().create_future()
        handle._pending[99] = pending
        await handle._dispatch({"jsonrpc": "2.0", "id": 99, "result": {"ok": True}})
        assert await asyncio.wait_for(pending, timeout=1) == {"ok": True}

        release.set()
        await asyncio.gather(*list(handle._server_request_tasks))
        assert sent[-1] == {
            "jsonrpc": "2.0", "id": 7,
            "result": {"decision": "accept"},
        }

    asyncio.run(run())


def test_codex_server_request_tasks_are_capped_and_fail_closed(monkeypatch):
    async def run():
        release = asyncio.Event()

        async def approve(_method, _params):
            await release.wait()
            return "accept"

        monkeypatch.setattr(codex_handle_module, "_MAX_SERVER_REQUEST_TASKS", 2)
        handle = CodexHandle(_Cfg(), approval_callback=approve)
        handle.approval = "on-request"
        sent = []

        async def send(message):
            sent.append(message)

        handle._send = send
        for rid in (1, 2, 3):
            await handle._dispatch({
                "jsonrpc": "2.0", "id": rid,
                "method": "item/commandExecution/requestApproval",
                "params": {"command": "true"},
            })

        assert len(handle._server_request_tasks) == 2
        assert sent == [{
            "jsonrpc": "2.0", "id": 3,
            "result": {"decision": "decline"},
        }]

        release.set()
        await asyncio.gather(*list(handle._server_request_tasks))

    asyncio.run(run())


@pytest.mark.parametrize("decision", [
    "accept", "acceptForSession", "decline", "cancel",
])
def test_current_codex_approval_schema_returns_exact_decision(decision):
    async def run():
        calls = []

        async def approve(method, params):
            calls.append((method, params))
            return decision

        handle = CodexHandle(_Cfg(), approval_callback=approve)
        handle.approval = "on-request"
        response = await _dispatch_request(
            handle,
            "item/commandExecution/requestApproval",
            {"threadId": "t", "turnId": "u", "itemId": "i",
             "command": "rm -rf build", "cwd": "/tmp"},
        )
        assert response == {
            "jsonrpc": "2.0", "id": 7,
            "result": {"decision": decision},
        }
        assert calls and calls[0][0] == "item/commandExecution/requestApproval"

    asyncio.run(run())


@pytest.mark.parametrize(("decision", "legacy"), [
    ("accept", "approved"),
    ("acceptForSession", "approved_for_session"),
    ("decline", "denied"),
    ("cancel", "abort"),
])
def test_legacy_codex_approval_schema_maps_decisions(decision, legacy):
    async def run():
        async def approve(_method, _params):
            return decision

        handle = CodexHandle(_Cfg(), approval_callback=approve)
        handle.approval = "untrusted"
        response = await _dispatch_request(
            handle, "applyPatchApproval",
            {"callId": "call", "fileChanges": {"x.py": {}}},
        )
        assert response["result"] == {"decision": legacy}

    asyncio.run(run())


def test_never_policy_and_unknown_requests_fail_closed():
    async def run():
        called = False

        async def should_not_run(_method, _params):
            nonlocal called
            called = True
            return "accept"

        handle = CodexHandle(_Cfg(), approval_callback=should_not_run)
        handle.approval = "never"
        current = await _dispatch_request(
            handle, "item/fileChange/requestApproval",
            {"threadId": "t", "turnId": "u", "itemId": "i"},
        )
        assert current["result"] == {"decision": "decline"}
        legacy = await _dispatch_request(
            handle, "execCommandApproval", {"callId": "c", "command": ["id"]},
        )
        assert legacy["result"] == {"decision": "denied"}
        assert called is False

        # Unknown server requests are rejected before the approval callback, even
        # under an interactive policy. They can never masquerade as an approvable
        # command and gain a user-driven allow response.
        handle.approval = "on-request"
        unknown = await _dispatch_request(handle, "account/deleteEverything", {})
        assert unknown["error"]["code"] == -32601
        assert "unsupported server request" in unknown["error"]["message"]
        assert called is False

    asyncio.run(run())


def test_codex_approval_timeout_declines(monkeypatch):
    async def run():
        monkeypatch.setattr(codex_handle_module, "_APPROVAL_TIMEOUT", 0.01)
        machine, transport = _mk_machine()
        ctx = _mk_ctx("codex-timeout", "codex-timeout")
        ctx.engine = "codex"
        handle = CodexHandle(_Cfg())
        ctx.sdk = handle
        machine.sessions[ctx.key] = ctx
        handle.approval_callback = (
            lambda method, params: machine._on_codex_approval(
                ctx, method, params))
        handle.approval = "on-request"
        response = await _dispatch_request(
            handle, "item/commandExecution/requestApproval",
            {"threadId": "t", "turnId": "u", "itemId": "i"},
        )
        assert response["result"] == {"decision": "decline"}
        assert any(message.type == "ask_user" for message in transport.sent)
        assert ctx.pending_asks == {}

    asyncio.run(run())


def test_machine_codex_approval_uses_ask_user_choices():
    async def run():
        machine, _ = _mk_machine()
        ctx = _mk_ctx("codex-1", "codex-1")
        ctx.engine = "codex"
        captured = []
        answers = iter(["允许一次", "本会话允许", "拒绝", "取消", "unexpected"])

        async def ask(_ctx, question, options):
            captured.append((question, options))
            return next(answers)

        machine._on_ask = ask  # type: ignore[method-assign]
        decisions = []
        for _ in range(5):
            decisions.append(await machine._on_codex_approval(
                ctx, "item/commandExecution/requestApproval",
                {"command": "git clean -fd", "cwd": "/work", "reason": "cleanup"},
            ))
        assert decisions == [
            "accept", "acceptForSession", "decline", "cancel", "decline",
        ]
        assert [o["label"] for o in captured[0][1]] == [
            "允许一次", "本会话允许", "拒绝", "取消",
        ]
        assert "git clean -fd" in captured[0][0]
        assert "目录：/work" in captured[0][0]

    asyncio.run(run())


def test_machine_claude_tool_permission_allows_or_denies_once():
    async def run():
        machine, _ = _mk_machine()
        ctx = _mk_ctx("claude-1", "claude-1")
        captured = []
        answers = iter(["允许一次", "拒绝"])

        async def ask(_ctx, question, options):
            captured.append((question, options))
            return next(answers)

        machine._on_ask = ask  # type: ignore[method-assign]
        context = SimpleNamespace(suggestions=[object()])
        allowed = await machine._on_claude_tool_permission(
            ctx, "Bash", {"command": "git status"}, context)
        denied = await machine._on_claude_tool_permission(
            ctx, "Write", {"file_path": "/tmp/a"}, context)

        assert isinstance(allowed, PermissionResultAllow)
        assert isinstance(denied, PermissionResultDeny)
        assert [o["label"] for o in captured[0][1]] == ["允许一次", "拒绝"]
        assert "Bash" in captured[0][0] and "git status" in captured[0][0]

    asyncio.run(run())


def test_claude_sdk_permission_callback_fails_closed_without_bridge():
    async def run():
        result = await SdkHandle(_Cfg())._can_use_tool(
            "Bash", {"command": "rm -rf /tmp/x"}, SimpleNamespace())
        assert isinstance(result, PermissionResultDeny)

    asyncio.run(run())


def test_codex_goal_rpc_uses_official_thread_api():
    async def run():
        handle = CodexHandle(_Cfg())
        handle.thread_id = "thread-1"
        requests = []

        async def request(method, params=None):
            requests.append((method, params))
            if method.endswith("/get"):
                return {"goal": None}
            if method.endswith("/set"):
                return {"goal": {
                    "threadId": "thread-1", "objective": "ship",
                    "status": "active", "tokensUsed": 0,
                    "timeUsedSeconds": 0, "createdAt": 1, "updatedAt": 1,
                    "futureSecret": "must-not-cross-the-wire",
                }}
            return {"cleared": True}

        handle._request = request
        assert await handle.get_goal() is None
        goal = await handle.set_goal(objective="ship", status="active", token_budget=12000)
        assert goal["objective"] == "ship"
        assert goal["engine"] == "codex"
        assert "futureSecret" not in goal
        assert await handle.clear_goal() is True
        assert requests == [
            ("thread/goal/get", {"threadId": "thread-1"}),
            ("thread/goal/set", {"threadId": "thread-1", "objective": "ship", "status": "active", "tokenBudget": 12000}),
            ("thread/goal/clear", {"threadId": "thread-1"}),
        ]

    asyncio.run(run())


def test_goal_wire_model_is_strict_and_shared_by_both_engines():
    base = {
        "threadId": "thread-1", "objective": "finish",
        "status": "active", "engine": "codex", "tokenBudget": None,
        "tokensUsed": 2, "timeUsedSeconds": 3,
        "createdAt": 1, "updatedAt": 2,
    }
    assert ThreadGoal(**base).engine == "codex"
    assert ThreadGoal(**{
        **base, "engine": "claude", "iterations": 4,
        "lastReason": "still working", "setAt": 1.5,
        "tokensAtStart": 10,
    }).iterations == 4
    with pytest.raises(ValidationError):
        ThreadGoal(**{**base, "futureSecret": "must-not-pass"})
    with pytest.raises(ValidationError):
        GoalState(goal={**base, "tokensUsed": True})


def test_codex_goal_notifications_are_sanitized_filtered_and_cleared():
    async def run():
        seen = []

        async def on_goal(goal):
            seen.append(goal)

        handle = CodexHandle(_Cfg(), goal_callback=on_goal)
        handle.thread_id = "thread-1"
        raw_goal = {
            "threadId": "thread-1", "objective": "ship",
            "status": "active", "tokensUsed": 7,
            "timeUsedSeconds": 9, "createdAt": 1, "updatedAt": 2,
            "futureSecret": "must-not-cross-the-wire",
        }
        await handle._dispatch({
            "method": "thread/goal/updated",
            "params": {"threadId": "other-thread", "goal": raw_goal},
        })
        assert seen == []

        await handle._dispatch({
            "method": "thread/goal/updated",
            "params": {"threadId": "thread-1", "turnId": "turn-1",
                       "goal": raw_goal},
        })
        assert seen[0]["engine"] == "codex"
        assert seen[0]["tokensUsed"] == 7
        assert "futureSecret" not in seen[0]
        assert handle.last_goal == seen[0]

        await handle._dispatch({
            "method": "thread/goal/cleared",
            "params": {"threadId": "thread-1"},
        })
        assert seen[-1] is None
        assert handle.last_goal is None

    asyncio.run(run())


def test_machine_goal_emits_authoritative_state():
    async def run():
        machine, transport = _mk_machine()
        ctx = _mk_ctx("goal-1", "goal-1")
        ctx.engine = "codex"
        goal = {
            "threadId": "goal-1", "objective": "finish",
            "status": "active", "engine": "codex", "tokensUsed": 2,
            "timeUsedSeconds": 3, "createdAt": 1, "updatedAt": 1,
        }
        ctx.sdk = SimpleNamespace(get_goal=lambda: None)

        async def get_goal(): return goal
        async def set_goal(**_kwargs): return goal
        async def clear_goal(): return True
        ctx.sdk.get_goal = get_goal
        ctx.sdk.set_goal = set_goal
        ctx.sdk.clear_goal = clear_goal
        machine.sessions[ctx.key] = ctx
        await machine._handle_get_goal(SimpleNamespace(
            sid=ctx.key, client_id="client-1"))
        await machine._handle_set_goal(SimpleNamespace(
            sid=ctx.key, client_id="client-1", objective="finish",
            status="active", token_budget=None))
        await machine._handle_clear_goal(SimpleNamespace(
            sid=ctx.key, client_id="client-1"))
        states = [m for m in transport.sent if isinstance(m, GoalState)]
        assert [state.goal.model_dump() if state.goal else None for state in states] == [
            ThreadGoal(**goal).model_dump(), ThreadGoal(**goal).model_dump(), None,
        ]
        # Reads are private one-shot responses; mutations are shared state and are
        # broadcast so another signed-in device updates immediately.
        assert [state.to for state in states] == ["client-1", None, None]

        await machine._on_codex_goal(ctx, goal)
        assert isinstance(transport.sent[-1], GoalState)
        assert transport.sent[-1].to is None

    asyncio.run(run())


def test_codex_goal_auto_turn_claims_session_interrupts_and_completes():
    async def run():
        machine, transport = _mk_machine()
        ctx = _mk_ctx("goal-auto", "goal-auto")
        ctx.engine = "codex"
        handle = CodexHandle(_Cfg())
        handle.thread_id = ctx.session_id
        handle.proc = SimpleNamespace(returncode=None)
        ctx.sdk = handle
        machine.sessions[ctx.key] = ctx
        handle.turn_lifecycle_callback = (
            lambda phase, turn_id: machine._on_codex_turn_lifecycle(
                ctx, phase, turn_id))

        entered = asyncio.Event()
        release = asyncio.Event()
        requests = []
        goal = {
            "threadId": ctx.session_id, "objective": "finish tests",
            "status": "active", "tokensUsed": 0,
            "timeUsedSeconds": 0, "createdAt": 1, "updatedAt": 1,
        }

        async def request(method, params=None):
            requests.append((method, params))
            if method == "thread/goal/set":
                entered.set()
                await release.wait()
                return {"goal": goal}
            if method == "turn/interrupt":
                return {}
            raise AssertionError(method)

        handle._request = request
        set_task = asyncio.create_task(machine._handle_set_goal(SimpleNamespace(
            sid=ctx.key, client_id="client-1", objective="finish tests",
            status="active", token_budget=None,
        )))
        await entered.wait()
        assert ctx.state == "running"

        # The goal RPC has not returned and turn/started has not arrived yet, but
        # the session is already claimed: no second remote writer can slip in.
        busy = await machine._handle_query(SimpleNamespace(
            sid=ctx.key, prompt="race", images=None, files=None,
            msg_id="race-query",
        ))
        assert isinstance(busy, Error) and busy.code == "busy"

        await handle._dispatch({
            "method": "turn/started",
            "params": {"turn": {"id": "auto-1"}},
        })
        release.set()
        result = await set_task
        assert isinstance(result, GoalState)
        assert ctx.state == "running"
        assert ctx.codex_spontaneous_turn_id == "auto-1"

        await machine._handle_interrupt(SimpleNamespace(sid=ctx.key))
        assert ctx.state == "interrupting"
        assert requests[-1] == (
            "turn/interrupt",
            {"threadId": ctx.session_id, "turnId": "auto-1"},
        )
        await handle._dispatch({
            "method": "turn/completed",
            "params": {"turn": {"id": "auto-1", "status": "interrupted"}},
        })
        spontaneous_task = ctx.codex_spontaneous_task
        assert spontaneous_task is not None
        await spontaneous_task
        assert ctx.state == "idle"
        assert ctx.codex_spontaneous_turn_id is None
        assert [event.state for event in transport.sent
                if isinstance(event, StateEvent)][-3:] == [
                    "running", "interrupting", "idle",
                ]

    asyncio.run(run())


def test_clearing_live_codex_goal_interrupts_automatic_turn():
    async def run():
        machine, _transport = _mk_machine()
        ctx = _mk_ctx("goal-clear-auto", "goal-clear-auto")
        ctx.engine = "codex"
        ctx.state = "running"
        ctx.codex_spontaneous_turn_id = "auto-clear"
        requests = []

        class Sdk:
            async def clear_goal(self):
                requests.append(("clear", None))
                return True

            async def interrupt(self):
                requests.append(("interrupt", "auto-clear"))

        ctx.sdk = Sdk()
        machine.sessions[ctx.key] = ctx
        result = await machine._handle_clear_goal(SimpleNamespace(
            sid=ctx.key, client_id="client-1"))
        assert isinstance(result, GoalState) and result.goal is None
        assert requests == [("clear", None), ("interrupt", "auto-clear")]
        assert ctx.state == "interrupting"

    asyncio.run(run())


def test_managed_turn_unwind_preserves_live_auto_interrupt_state():
    async def run():
        machine, _transport = _mk_machine()
        ctx = _mk_ctx("managed-auto-overlap", None)
        ctx.engine = "codex"
        ctx.state = "interrupting"
        ctx.active_msg_id = "managed-message"
        ctx.codex_spontaneous_turn_id = "auto-overlap"
        ctx.interrupt_deadline = 123.0
        ctx.interrupt_event.set()
        ctx.turn_task = asyncio.current_task()

        # The managed send loses the pre-turn/start race and unwinds. Its finally
        # block must not erase the interrupt that now belongs to the auto turn.
        await machine._run_turn(ctx, "must not be submitted")
        assert ctx.state == "interrupting"
        assert ctx.codex_spontaneous_turn_id == "auto-overlap"
        assert ctx.interrupt_event.is_set()
        assert ctx.interrupt_deadline == 123.0
        assert ctx.active_msg_id == "managed-message"
        assert ctx.turn_task is None

        await machine._on_codex_turn_lifecycle(
            ctx, "completed", "auto-overlap")
        assert ctx.state == "idle"
        assert not ctx.interrupt_event.is_set()

    asyncio.run(run())


def test_managed_turn_exception_does_not_unlock_live_auto_turn():
    async def run():
        machine, _transport = _mk_machine()
        ctx = _mk_ctx("managed-auto-error", None)
        ctx.engine = "codex"
        ctx.state = "running"
        ctx.active_msg_id = "managed-error"
        ctx.codex_spontaneous_turn_id = "auto-after-error"
        ctx.turn_task = asyncio.current_task()

        class FailingSdk:
            tier_dirty = False
            model = None
            effort = None

            async def query(self, _prompt, images=None):
                raise RuntimeError("managed launch failed")

        ctx.sdk = FailingSdk()
        await machine._run_turn(ctx, "fails")
        assert ctx.state == "running"
        assert ctx.codex_spontaneous_turn_id == "auto-after-error"
        assert ctx.turn_task is None

        await machine._on_codex_turn_lifecycle(
            ctx, "completed", "auto-after-error")
        assert ctx.state == "idle"

    asyncio.run(run())


def test_managed_codex_turn_emits_authoritative_browser_turn_binding():
    async def run():
        machine, transport = _mk_machine()
        ctx = _mk_ctx("binding-session", "binding-session")
        ctx.engine = "codex"
        ctx.state = "running"
        ctx.active_msg_id = "browser-message"
        ctx.turn_task = asyncio.current_task()

        class AcceptedSdk:
            tier_dirty = False
            model = None
            effort = None
            collaboration_mode = "default"
            service_tier = None

            async def query(self, _prompt, images=None):
                return "native-turn"

            async def receive_response(self):
                yield {
                    "method": "turn/completed",
                    "params": {"turn": {
                        "id": "native-turn", "status": "completed",
                    }},
                }

        ctx.sdk = AcceptedSdk()
        machine._begin_codex_checkpoint = lambda _ctx: asyncio.sleep(0)
        machine._accept_codex_checkpoint = lambda _ctx: asyncio.sleep(0)
        await machine._run_turn(ctx, "hello")

        binding = next(
            event for event in transport.sent if isinstance(event, TurnBinding))
        assert binding.sid == "binding-session"
        assert binding.msg_id == "browser-message"
        assert binding.turn_id == "native-turn"
        assert [event.type for event in transport.sent].index("turn_binding") < [
            event.type for event in transport.sent
        ].index("turn_end")

    asyncio.run(run())


def test_machine_goal_errors_are_routed_without_raw_exception_text():
    async def run():
        machine, transport = _mk_machine()
        ctx = _mk_ctx("goal-error", "goal-error")
        ctx.engine = "codex"

        async def fail(**_kwargs):
            raise RuntimeError("provider sk-secret must not cross the wire")

        ctx.sdk = SimpleNamespace(set_goal=fail)
        machine.sessions[ctx.key] = ctx
        await machine._handle_set_goal(SimpleNamespace(
            sid=ctx.key, client_id="client-1", objective="finish",
            status="active", token_budget=None))
        error = next(message for message in transport.sent
                     if isinstance(message, Error))
        assert error.to == "client-1"
        assert error.message == "设置 Goal 失败"
        assert "secret" not in error.message

    asyncio.run(run())


def test_codex_request_user_input_round_trips_all_answers():
    async def run():
        handle = CodexHandle(_Cfg())
        seen = []

        async def interact(method, params):
            seen.append((method, params))
            return {"answers": {"name": {"answers": ["Nancy"]}}}

        handle.interaction_callback = interact
        response = await _dispatch_request(handle, "item/tool/requestUserInput", {
            "threadId": "t", "turnId": "u", "itemId": "i",
            "questions": [{"id": "name", "header": "名称", "question": "你的名字？"}],
        })
        assert response["result"] == {"answers": {"name": {"answers": ["Nancy"]}}}
        assert seen[0][0] == "item/tool/requestUserInput"

    asyncio.run(run())


def test_machine_codex_request_user_input_supports_choice_text_and_secret():
    async def run():
        machine, _ = _mk_machine()
        ctx = _mk_ctx("codex-input", "codex-input")
        calls = []
        replies = iter(["A", "s3cr3t"])

        async def ask(_ctx, question, options, **kwargs):
            calls.append((question, options, kwargs))
            return next(replies)

        machine._on_ask = ask  # type: ignore[method-assign]
        result = await machine._on_codex_interaction(ctx, "item/tool/requestUserInput", {
            "questions": [
                {"id": "pick", "header": "选择", "question": "选哪个？", "options": [{"label": "A", "description": "first"}]},
                {"id": "token", "header": "密钥", "question": "请输入", "isSecret": True},
            ],
        })
        assert result == {"answers": {"pick": {"answers": ["A"]}, "token": {"answers": ["s3cr3t"]}}}
        assert calls[0][2]["allow_text"] is True
        assert calls[1][2]["allow_text"] is True and calls[1][2]["secret"] is True

    asyncio.run(run())


def test_machine_codex_generic_permissions_preserve_requested_profile():
    async def run():
        machine, _ = _mk_machine()
        ctx = _mk_ctx("codex-perm", "codex-perm")
        answers = iter(["允许本回合", "拒绝"])
        machine._on_ask = lambda *_args, **_kwargs: _next_answer(answers)  # type: ignore[method-assign]
        requested = {"network": {"enabled": True}}
        allowed = await machine._on_codex_interaction(ctx, "item/permissions/requestApproval", {"permissions": requested})
        denied = await machine._on_codex_interaction(ctx, "item/permissions/requestApproval", {"permissions": requested})
        assert allowed == {"permissions": requested, "scope": "turn"}
        assert denied == {"permissions": {}, "scope": "turn"}

    async def _next_answer(iterator):
        return next(iterator)

    asyncio.run(run())


def test_machine_codex_mcp_elicitation_form_returns_typed_content():
    async def run():
        machine, _ = _mk_machine()
        ctx = _mk_ctx("codex-mcp", "codex-mcp")
        answers = iter(["Nancy", "3"])

        async def ask(*_args, **_kwargs): return next(answers)
        machine._on_ask = ask  # type: ignore[method-assign]
        result = await machine._on_codex_interaction(ctx, "mcpServer/elicitation/request", {
            "mode": "form", "serverName": "demo", "message": "configure",
            "requestedSchema": {"type": "object", "properties": {
                "name": {"type": "string", "title": "Name"},
                "count": {"type": "integer", "title": "Count"},
            }, "required": ["name"]},
        })
        assert result == {"action": "accept", "content": {"name": "Nancy", "count": 3}}

    asyncio.run(run())


class _ControlSdk:
    def __init__(self, approval="never", fail_perm=False,
                 fail_collaboration=False):
        self.approval = approval
        self.fail_perm = fail_perm
        self.fail_collaboration = fail_collaboration
        self.collaboration_mode = "default"
        self.service_tier = None
        self.permission_calls: list[str] = []
        self.collaboration_calls: list[str] = []
        self.service_tier_calls: list[str | None] = []
        self.tier_dirty = False
        self.disconnected = False

    async def set_permission_mode(self, mode):
        self.permission_calls.append(mode)
        if self.fail_perm:
            raise RuntimeError("apply failed")
        self.approval = mode

    async def set_service_tier(self, tier):
        self.service_tier_calls.append(tier)
        self.service_tier = tier

    async def set_collaboration_mode(self, mode):
        self.collaboration_calls.append(mode)
        if self.fail_collaboration:
            raise RuntimeError("apply failed")
        self.collaboration_mode = mode

    async def disconnect(self):
        self.disconnected = True


def _control_ctx(key: str, engine: str, sdk=None):
    ctx = _mk_ctx(key, key)
    ctx.engine = engine
    ctx.sdk = sdk or _ControlSdk()
    return ctx


def test_permission_modes_are_engine_strict_and_broadcast_after_apply():
    async def run():
        machine, transport = _mk_machine()
        codex = _control_ctx("codex", "codex")
        claude = _control_ctx("claude", "claude")
        machine.sessions = {"codex": codex, "claude": claude}

        await machine._handle_set_perm(
            SimpleNamespace(sid="codex", mode="bypassPermissions"))
        assert codex.sdk.permission_calls == []
        assert transport.sent[-1].type == "error"

        await machine._handle_set_perm(
            SimpleNamespace(sid="codex", mode="on-request"))
        assert codex.sdk.permission_calls == ["on-request"]
        assert transport.sent[-1].type == "perm"
        assert transport.sent[-1].mode == "on-request"

        await machine._handle_set_perm(
            SimpleNamespace(sid="claude", mode="untrusted"))
        assert claude.sdk.permission_calls == []
        assert transport.sent[-1].type == "error"

        await machine._handle_set_perm(
            SimpleNamespace(sid="claude", mode="plan"))
        assert claude.sdk.permission_calls == ["plan"]
        assert transport.sent[-1].type == "perm"
        assert transport.sent[-1].mode == "plan"

        failing = _control_ctx("failing", "codex", _ControlSdk(fail_perm=True))
        machine.sessions["failing"] = failing
        await machine._handle_set_perm(
            SimpleNamespace(sid="failing", mode="on-request"))
        assert failing.announced_perm is None
        assert transport.sent[-1].type == "error"

    asyncio.run(run())


def test_codex_work_permission_cannot_escalate_outside_its_profile():
    async def run():
        machine, transport = _mk_machine()
        work = _control_ctx("codex-work", "codex")
        work.space = "work"
        machine.sessions = {"codex-work": work}

        await machine._handle_set_perm(SimpleNamespace(
            sid="codex-work", mode="on-request"))
        assert work.sdk.permission_calls == []
        assert transport.sent[-1].type == "error"
        assert transport.sent[-1].code == "auth"

        await machine._handle_set_perm(SimpleNamespace(
            sid="codex-work", mode="never"))
        assert work.sdk.permission_calls == ["never"]
        assert transport.sent[-1].type == "perm"
        assert transport.sent[-1].mode == "never"

    asyncio.run(run())


def test_collaboration_modes_are_codex_only_and_broadcast_after_apply():
    async def run():
        machine, transport = _mk_machine()
        codex = _control_ctx("codex", "codex")
        claude = _control_ctx("claude", "claude")
        machine.sessions = {"codex": codex, "claude": claude}

        await machine._handle_set_collaboration_mode(
            SimpleNamespace(sid="codex", mode="plan"))
        assert codex.sdk.collaboration_calls == ["plan"]
        assert codex.sdk.permission_calls == []
        assert codex.sdk.approval == "never"
        assert codex.announced_collaboration_mode == "plan"
        assert isinstance(transport.sent[-1], CollaborationMode)
        assert transport.sent[-1].mode == "plan"

        await machine._handle_set_collaboration_mode(
            SimpleNamespace(sid="claude", mode="plan"))
        assert claude.sdk.collaboration_calls == []
        assert transport.sent[-1].type == "error"

        failing = _control_ctx(
            "failing", "codex", _ControlSdk(fail_collaboration=True))
        machine.sessions["failing"] = failing
        await machine._handle_set_collaboration_mode(
            SimpleNamespace(sid="failing", mode="plan"))
        assert failing.announced_collaboration_mode is None
        assert transport.sent[-1].type == "error"

    asyncio.run(run())


def test_client_hello_always_seeds_resident_codex_collaboration_mode():
    async def run():
        machine, transport = _mk_machine()
        ctx = _control_ctx("codex", "codex")
        ctx.sdk.collaboration_mode = "plan"
        machine.sessions = {"codex": ctx}

        await machine._handle_client_hello(SimpleNamespace(
            cursors={"codex": 0}, generations={"codex": machine.instance_id},
            last_seq=None, client_id="client-1", route_id="route-1",
        ))

        modes = [message for message in transport.sent
                 if message.type == "collaboration_mode"]
        assert len(modes) == 1
        assert modes[0].mode == "plan"
        assert modes[0].sid == "codex"
        assert modes[0].to == "client-1"
        assert modes[0].route_id == "route-1"

    asyncio.run(run())


def test_external_codex_turn_refreshes_collaboration_mode_without_changing_approval(
        monkeypatch):
    async def run():
        machine, transport = _mk_machine()
        ctx = _control_ctx("codex", "codex")
        ctx.sdk.approval = "on-request"
        machine.sessions = {"codex": ctx}
        monkeypatch.setattr(
            machine_module, "codex_session_settings",
            lambda *_args, **_kwargs: {"collaboration_mode": "plan"})

        await machine._refresh_codex_collaboration_mode(ctx)

        assert ctx.sdk.collaboration_mode == "plan"
        assert ctx.sdk.approval == "on-request"
        assert ctx.announced_collaboration_mode == "plan"
        assert isinstance(transport.sent[-1], CollaborationMode)
        assert transport.sent[-1].mode == "plan"

    asyncio.run(run())


def test_fast_toggle_updates_only_target_codex_thread():
    async def run():
        machine, transport = _mk_machine()
        one = _control_ctx("c1", "codex")
        two = _control_ctx("c2", "codex")
        claude = _control_ctx("cc", "claude")
        machine.sessions = {"c1": one, "c2": two, "cc": claude}
        await machine._handle_set_service_tier(
            SimpleNamespace(sid="c1", service_tier="fast"))

        assert one.sdk.service_tier_calls == ["fast"]
        assert two.sdk.service_tier_calls == []
        assert claude.sdk.service_tier_calls == []
        fast = [message for message in transport.sent if message.type == "fast"]
        assert {message.sid for message in fast} == {"c1"}
        assert all(message.on is True for message in fast)

    asyncio.run(run())


def test_fast_toggle_uses_target_thread_state_and_can_clear_override():
    async def run():
        machine, transport = _mk_machine()
        ctx = _control_ctx("c1", "codex")
        ctx.sdk.service_tier = "fast"
        machine.sessions = {"c1": ctx}

        await machine._handle_set_service_tier(
            SimpleNamespace(sid="c1", service_tier="toggle"))

        assert ctx.sdk.service_tier_calls == [None]
        assert transport.sent[-1].type == "fast"
        assert transport.sent[-1].on is False

    asyncio.run(run())


def test_fast_accepts_app_server_priority_normalization():
    async def run():
        machine, transport = _mk_machine()
        ctx = _mk_ctx("c1", "c1")
        handle = CodexHandle(_Cfg())
        handle.thread_id = "c1"
        handle._reader = asyncio.create_task(asyncio.Event().wait())
        ctx.engine = "codex"
        ctx.sdk = handle
        machine.sessions = {"c1": ctx}

        async def request(method, params=None):
            assert (method, params) == ("thread/settings/update", {
                "threadId": "c1", "serviceTier": "fast",
            })
            await handle._dispatch({
                "method": "thread/settings/updated",
                "params": {
                    "threadId": "c1",
                    "threadSettings": {
                        "model": handle.model,
                        "effort": handle.effort,
                        "approvalPolicy": handle.approval,
                        "serviceTier": "priority",
                    },
                },
            })
            return {}

        handle._request = request
        await machine._handle_set_service_tier(SimpleNamespace(
            sid="c1", service_tier="fast"))

        assert handle.service_tier == "priority"
        assert transport.sent[-1].type == "fast"
        assert transport.sent[-1].on is True
        handle._reader.cancel()
        await asyncio.gather(handle._reader, return_exceptions=True)

    asyncio.run(run())


def test_codex_rename_archive_and_unarchive_use_app_server_for_hot_and_cold_sessions(
        monkeypatch):
    async def run():
        machine, transport = _mk_machine()
        ctx = _control_ctx("codex-id", "codex")
        machine.sessions = {"codex-id": ctx}
        rpc_calls = []
        refreshes = []

        async def rpc(method, params, cwd=None):
            rpc_calls.append((method, params, cwd))
            return {}

        async def refresh(cmd):
            refreshes.append(cmd)

        def claude_only(*_args, **_kwargs):
            raise AssertionError("Codex control must not call the Claude SDK")

        monkeypatch.setattr(machine_module, "codex_rpc", rpc)
        monkeypatch.setattr(machine_module, "rename_session", claude_only)
        monkeypatch.setattr(machine_module, "tag_session", claude_only)
        machine._list_codex_sessions = refresh

        rename_hot = SimpleNamespace(session_id="codex-id", title="new")
        archive_hot = SimpleNamespace(session_id="codex-id", archived=True)
        await machine._handle_rename_session(rename_hot)
        await machine._handle_archive_session(archive_hot)

        # Sidebar rows need not be resident. A rollout match still routes the
        # mutation through the app-server instead of the Claude SDK.
        machine.sessions.clear()
        monkeypatch.setattr(
            machine_module, "codex_rollout_path",
            lambda session_id: "/tmp/rollout.jsonl"
            if session_id == "cold-codex-id" else None,
        )
        rename_cold = SimpleNamespace(
            session_id="cold-codex-id", title="cold new")
        unarchive_cold = SimpleNamespace(
            session_id="cold-codex-id", archived=False)
        await machine._handle_rename_session(rename_cold)
        await machine._handle_archive_session(unarchive_cold)

        assert rpc_calls == [
            ("thread/name/set", {"threadId": "codex-id", "name": "new"}, None),
            ("thread/archive", {"threadId": "codex-id"}, None),
            (
                "thread/name/set",
                {"threadId": "cold-codex-id", "name": "cold new"},
                None,
            ),
            ("thread/unarchive", {"threadId": "cold-codex-id"}, None),
        ]
        assert refreshes == [rename_hot, archive_hot, rename_cold, unarchive_cold]
        assert not [message for message in transport.sent if message.type == "error"]

    asyncio.run(run())


def test_codex_pin_is_wrapper_persistent_and_refreshes_sidebar():
    async def run():
        machine, transport = _mk_machine()
        machine.sessions = {"codex-id": _control_ctx("codex-id", "codex")}
        refreshed = []

        async def refresh(cmd):
            refreshed.append(cmd)

        machine._list_codex_sessions = refresh
        pin = PinSession(
            session_id="codex-id", pinned=True, engine="codex",
            client_id="client-1")
        unpin = PinSession(
            session_id="codex-id", pinned=False, engine="codex",
            client_id="client-1")
        await machine._handle_pin_session(pin)
        assert machine._session_pins.ids("codex") == {"codex-id"}
        await machine._handle_pin_session(unpin)
        assert machine._session_pins.ids("codex") == set()
        assert refreshed == [pin, unpin]
        assert not [message for message in transport.sent
                    if message.type == "error"]

    asyncio.run(run())


def test_machine_codex_list_preserves_app_server_metadata(monkeypatch):
    async def run():
        machine, transport = _mk_machine()
        resident = _control_ctx("resident-id", "codex")
        resident.state = "interrupting"
        machine.sessions = {"resident-id": resident}
        requested_limits = []

        async def listed(limit):
            requested_limits.append(limit)
            return [
                {
                    "session_id": "resident-id",
                    "summary": "resident",
                    "first_prompt": "prompt",
                    "cwd": "/repo",
                    "last_modified": "20",
                    "git_branch": "main",
                    "tag": None,
                    "forked_from_id": "parent-id",
                    "status": "active",
                },
                {
                    "session_id": "cold-id",
                    "summary": "cold",
                    "first_prompt": None,
                    "cwd": "/cold",
                    "last_modified": "10",
                    "git_branch": None,
                    "tag": "archived",
                    "forked_from_id": None,
                    "status": "active",
                },
            ]

        monkeypatch.setattr(machine_module, "list_codex_sessions", listed)
        machine._session_pins.set_pinned("codex", "resident-id", True)
        await machine._list_codex_sessions(SimpleNamespace(client_id="client-1"))

        session_list = transport.sent[-1]
        assert session_list.type == "session_list"
        assert session_list.engine == "codex" and session_list.to == "client-1"
        hot, cold = session_list.sessions
        assert hot.summary == "resident" and hot.git_branch == "main"
        assert hot.forked_from_id == "parent-id" and hot.codex_status == "active"
        assert hot.state == "interrupting"
        assert hot.pinned is True and cold.pinned is False
        assert cold.tag == "archived" and cold.state == "running"
        assert requested_limits == [200]

    asyncio.run(run())


def test_codex_session_mutation_failure_refreshes_authoritative_list(monkeypatch):
    async def run():
        machine, transport = _mk_machine()
        machine.sessions = {"codex-id": _control_ctx("codex-id", "codex")}
        refreshed = []

        async def rpc(_method, _params, cwd=None):
            raise RuntimeError("request rejected")

        async def refresh(cmd):
            refreshed.append(cmd)

        monkeypatch.setattr(machine_module, "codex_rpc", rpc)
        machine._list_codex_sessions = refresh
        rename = SimpleNamespace(session_id="codex-id", title="new")
        archive = SimpleNamespace(session_id="codex-id", archived=True)

        await machine._handle_rename_session(rename)
        await machine._handle_archive_session(archive)

        assert refreshed == [rename, archive]
        errors = [message for message in transport.sent if message.type == "error"]
        assert len(errors) == 2

    asyncio.run(run())


class _FiniteTransport:
    def __init__(self, commands=()):
        self.sent = []
        self.commands = list(commands)
        self.on_connected = None
        self.started = False
        self.stopped = False

    async def start(self):
        self.started = True
        if self.on_connected:
            await self.on_connected()

    async def stop(self):
        self.stopped = True

    async def send(self, message):
        self.sent.append(message)

    async def incoming(self):
        for command in self.commands:
            yield command


def test_wrapper_stays_alive_when_claude_bootstrap_preflight_fails(
        monkeypatch, tmp_path):
    async def run():
        def fail(_cli_path):
            raise RuntimeError("claude unavailable")

        monkeypatch.setattr(SdkHandle, "preflight", staticmethod(fail))
        machine, _ = _mk_machine()
        transport = _FiniteTransport()
        machine.transport = transport
        transport.on_connected = machine._on_transport_connected
        machine.cfg.state_dir = tmp_path / "state"
        machine.cfg.cc_cwd = str(tmp_path)

        await machine.run()

        assert transport.started is True and transport.stopped is True
        assert machine.sessions == {} and machine.focused_sid is None
        assert any(message.type == "hello" for message in transport.sent)
        assert any(message.type == "error" and "Claude 暂时不可用" in message.message
                   for message in transport.sent)

    asyncio.run(run())


def test_empty_pool_accepts_codex_session_after_claude_bootstrap_failure(
        monkeypatch, tmp_path):
    class FakeCodexHandle:
        def __init__(self, _cfg, cwd=None, daemon_mode=None,
                     daemon_manager=None):
            self.cwd = cwd
            self.daemon_mode = daemon_mode
            self.daemon_manager = daemon_manager
            self.thread_id = None
            self.model = "gpt-test"
            self.effort = "high"
            self.applied_effort = "high"
            self.approval = "never"
            self.approval_callback = None
            self.disconnected = False

        async def connect(self, **_kwargs):
            self.thread_id = "fresh-codex-thread"

        async def activate_runtime_events(self):
            return None

        async def disconnect(self):
            self.disconnected = True

    async def run():
        def fail(_cli_path):
            raise RuntimeError("claude unavailable")

        monkeypatch.setattr(SdkHandle, "preflight", staticmethod(fail))
        monkeypatch.setattr(machine_module, "CodexHandle", FakeCodexHandle)
        machine, _ = _mk_machine()
        transport = _FiniteTransport([NewSession(engine="codex")])
        machine.transport = transport
        transport.on_connected = machine._on_transport_connected
        machine.cfg.state_dir = tmp_path / "state"
        machine.cfg.cc_cwd = str(tmp_path)

        await machine.run()

        assert len(machine.sessions) == 1
        ctx = next(iter(machine.sessions.values()))
        assert ctx.engine == "codex"
        assert machine.focused_sid == ctx.key
        assert any(message.type == "session_focus" for message in transport.sent)
        assert any(message.type == "perm" and message.mode == "never"
                   for message in transport.sent)
        assert ctx.sdk.disconnected is True

    asyncio.run(run())


def test_failed_claude_preflight_does_not_evict_codex(monkeypatch):
    async def run():
        def fail(cli_path):
            assert cli_path == "/opt/cc-remote/bin/claude"
            raise RuntimeError("claude unavailable")

        monkeypatch.setattr(SdkHandle, "preflight", staticmethod(fail))
        machine, _ = _mk_machine()
        machine.cfg.claude_bin = "/opt/cc-remote/bin/claude"
        machine.cfg.max_concurrent_sessions = 1
        codex = _control_ctx("codex-id", "codex")
        machine.sessions = {"codex-id": codex}

        spawned = await machine._spawn(
            resume_id=None, cwd="/tmp", engine="claude")

        assert spawned is None
        assert machine.sessions == {"codex-id": codex}
        assert codex.sdk.disconnected is False

    asyncio.run(run())
