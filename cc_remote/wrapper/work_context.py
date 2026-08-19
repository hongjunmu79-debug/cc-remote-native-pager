"""Work-only context growth layered over authoritative engine totals."""
from __future__ import annotations

import json
from typing import Any

from cc_remote.wrapper.codex_sessions import codex_rollout_path
from cc_remote.wrapper.stream import _bounded_jsonl_lines, transcript_path


_BASELINE_HISTORY_RECORD_LIMIT = 256


def _nonnegative_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def initial_work_context_baseline(engine: str, usage: dict[str, Any]) -> int:
    """Return the fresh Work session's startup zero point.

    Claude is normally sampled before its first query by ``SdkHandle.connect``.
    The fallback is still useful for migrated sessions. Codex app-server emits
    token usage only after a turn, so its first input depth is the closest
    authoritative startup measurement; output stays user context.
    """
    if engine == "codex":
        raw = usage.get("raw") if isinstance(usage.get("raw"), dict) else {}
        last = raw.get("last") if isinstance(raw.get("last"), dict) else {}
        value = _nonnegative_int(last.get("inputTokens"))
        if value is not None:
            return value
        value = _nonnegative_int(usage.get("used_tokens"))
        return value or 0
    value = _nonnegative_int(usage.get("totalTokens"))
    return value or 0


def recover_work_context_baseline(
    engine: str, session_id: str,
) -> int | None:
    """Recover a migrated Work session's first authoritative input depth.

    Both native histories record input usage after the first turn. That is the
    same startup zero point used for new Codex Work sessions and avoids treating
    an old conversation's *current* depth as engine overhead after an upgrade.
    """
    path = (codex_rollout_path(session_id) if engine == "codex"
            else transcript_path(session_id))
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as history:
            for index, line in enumerate(_bounded_jsonl_lines(history)):
                if index >= _BASELINE_HISTORY_RECORD_LIMIT:
                    break
                try:
                    record = json.loads(line)
                except (TypeError, ValueError):
                    continue
                if not isinstance(record, dict):
                    continue
                if engine == "codex":
                    payload = (record.get("payload")
                               if record.get("type") == "event_msg" else None)
                    if (not isinstance(payload, dict)
                            or payload.get("type") != "token_count"):
                        continue
                    info = payload.get("info")
                    if not isinstance(info, dict):
                        continue
                    last = info.get("last_token_usage")
                    if not isinstance(last, dict):
                        last = info.get("last")
                    value = (_nonnegative_int(last.get("input_tokens"))
                             if isinstance(last, dict) else None)
                    if value is None and isinstance(last, dict):
                        value = _nonnegative_int(last.get("inputTokens"))
                    if value is not None and value > 0:
                        return value
                    continue

                message = (record.get("message")
                           if record.get("type") == "assistant" else None)
                usage = message.get("usage") if isinstance(message, dict) else None
                if not isinstance(usage, dict):
                    continue
                total = sum(
                    _nonnegative_int(usage.get(key)) or 0
                    for key in (
                        "input_tokens", "cache_creation_input_tokens",
                        "cache_read_input_tokens",
                    )
                )
                if total > 0:
                    return total
    except (OSError, UnicodeError):
        return None
    return None


def work_context_metrics(
    engine: str,
    usage: dict[str, Any],
    baseline_tokens: int | None,
) -> tuple[int, int, float, int]:
    """Split Work's raw total into startup baseline and later growth.

    The returned tuple is ``session, fixed, session_percentage, baseline``.
    Raw totals are deliberately not changed: callers still use them for the
    actual remaining context capacity and compaction threshold.
    """
    raw_key = "used_tokens" if engine == "codex" else "totalTokens"
    max_key = "context_window" if engine == "codex" else "maxTokens"
    raw_total = _nonnegative_int(usage.get(raw_key)) or 0
    max_tokens = _nonnegative_int(usage.get(max_key)) or 0
    baseline = _nonnegative_int(baseline_tokens)
    if baseline is None:
        baseline = initial_work_context_baseline(engine, usage)
    fixed = min(raw_total, baseline)
    session = max(0, raw_total - fixed)
    percentage = session / max_tokens * 100.0 if max_tokens else 0.0
    return session, fixed, percentage, baseline
