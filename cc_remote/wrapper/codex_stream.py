"""Translate Codex app-server notifications into the remote rich-event model.

Only app-server fields that are explicitly part of its public client protocol are
forwarded.  In particular, reasoning *summary* is visible, while raw/encrypted
reasoning and terminal stdin are deliberately hidden.  A terminal-interaction
marker is still forwarded so the remote timeline does not silently omit the step.
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import shlex
from datetime import datetime
from itertools import islice

from cc_remote.protocol import (
    AssistantMsgStart, Delta, ToolUse, ToolDelta, ToolResult, AssistantMsgEnd,
    ProcessEvent, TurnPlan, TurnDiff, TurnEnd, TurnResult, UserMsg, Error,
    StateEvent, ERR_CC_CRASH,
)
from cc_remote.wrapper.codex_external import visible_codex_user_message
from cc_remote.wrapper.sanitize import bounded_text, bounded_tool_input

_TOOL_TYPES = {
    "commandExecution", "fileChange", "mcpToolCall", "dynamicToolCall",
    "webSearch",
}
_PROCESS_ITEM_TYPES = {
    "plan", "reasoning", "collabAgentToolCall", "subAgentActivity",
    "contextCompaction", "imageView", "sleep", "imageGeneration",
    "enteredReviewMode", "exitedReviewMode",
}
_MAX_HISTORY_RECORD_CHARS = 16 * 1024 * 1024
_SAFE_WIRE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$")
_CREDENTIAL_EXACT_KEYS = frozenset({"env", "environment"})
_CREDENTIAL_KEY_FRAGMENTS = (
    "secret",
    "password",
    "passwd",
    "token",
    "authorization",
    "credential",
    "cookie",
    "accesskey",
    "privatekey",
    "apikey",
)
_REDACTED = "[REDACTED]"
_REDACTION_BUDGET_EXCEEDED = "<redaction budget exceeded>"
_REDACTION_REMAINDER_KEY = "<remaining omitted>"
_MAX_REDACTION_DEPTH = 6
_MAX_REDACTION_NODES = 2048
_MAX_REDACTION_DICT_ITEMS = 64
_MAX_REDACTION_SEQUENCE_ITEMS = 32
_EMPTY_COMPLETED_MESSAGE = (
    "Codex 回合已结束，但没有返回任何内容；上游服务可能暂时不可用，请重试。"
)
_MAX_DELTA_STREAMS = 2048
_MAX_DELTA_EVENTS_PER_STREAM = 1024
_MAX_FINISHED_DELTA_ITEMS = 4096
_MAX_LIVE_ITEMS = 4096
_LIVE_ITEMS_OMITTED_ID = "cc-remote-live-items-omitted"
_DELTA_TRUNCATION_NOTICE = "\n…（后续输出已截断）"
_MODEL_NAME_MAX_CHARS = 256
_MODEL_ENUM_MAX_CHARS = 256
_MODEL_LIST_MAX_ITEMS = 32
_MODEL_DETAIL_MAX_CHARS = 16 * 1024
_DEFAULT_HISTORY_WINDOW_MAX_BYTES = 32 * 1024 * 1024
_REVERSE_HISTORY_CHUNK_BYTES = 1024 * 1024
_MAX_HISTORY_REVERSE_RECORD_BYTES = 1024 * 1024
_MAX_HISTORY_BOUNDARY_RECORD_BYTES = 1024 * 1024
_MAX_HISTORY_BOUNDARY_FORWARD_BYTES = 64 * 1024 * 1024
_MAX_PENDING_HISTORY_COMPACTIONS = 32


def _bounded_jsonl_records(file, *, end_offset: int | None = None):
    """Yield bounded complete records with stable absolute byte offsets.

    ``end_offset`` freezes a growing rollout at the snapshot selected by the
    history pager.  Byte offsets, unlike window-local line numbers, remain
    stable when a large file is read through different pages.
    """
    while True:
        record_offset = file.tell()
        if end_offset is not None and record_offset >= end_offset:
            return
        read_limit = _MAX_HISTORY_RECORD_CHARS + 1
        if end_offset is not None:
            read_limit = min(read_limit, end_offset - record_offset)
        line = file.readline(read_limit)
        if not line:
            return
        complete = (
            line.endswith(b"\n")
            or len(line) < _MAX_HISTORY_RECORD_CHARS + 1
            or (end_offset is not None and file.tell() >= end_offset)
        )
        if complete:
            yield record_offset, line.decode("utf-8", "replace")
            continue
        while line and not line.endswith(b"\n"):
            if end_offset is not None and file.tell() >= end_offset:
                return
            read_limit = _MAX_HISTORY_RECORD_CHARS + 1
            if end_offset is not None:
                read_limit = min(read_limit, end_offset - file.tell())
            line = file.readline(read_limit)


def _reverse_jsonl_records(path: str):
    """Yield ``(byte_offset, line)`` from newest to oldest without buffering.

    Fixed-size reverse reads avoid mmap implementations faulting a large part
    of a multi-gigabyte rollout into RSS. Individual pathological records and
    the cross-chunk carry remain bounded.
    """
    with open(path, "rb") as source:
        size = os.fstat(source.fileno()).st_size
        if size <= 0:
            return
        position = size
        carry = b""
        dropping_oversized = False
        while position > 0:
            read_size = min(_REVERSE_HISTORY_CHUNK_BYTES, position)
            position -= read_size
            source.seek(position)
            chunk = source.read(read_size)
            data = chunk if dropping_oversized else chunk + carry
            parts = data.split(b"\n")
            if len(parts) == 1:
                if (dropping_oversized
                        or len(data) > _MAX_HISTORY_REVERSE_RECORD_BYTES):
                    carry = b""
                    dropping_oversized = True
                else:
                    carry = data
                continue

            starts = [position]
            for part in parts[:-1]:
                starts.append(starts[-1] + len(part) + 1)
            for index in range(len(parts) - 1, 0, -1):
                # The newest fragment still belongs to a record whose newer
                # suffix was discarded after it exceeded the per-record cap.
                if dropping_oversized and index == len(parts) - 1:
                    continue
                line = parts[index]
                if line and len(line) <= _MAX_HISTORY_REVERSE_RECORD_BYTES:
                    yield starts[index], line
            carry = parts[0]
            dropping_oversized = len(carry) > _MAX_HISTORY_REVERSE_RECORD_BYTES
            if dropping_oversized:
                carry = b""

        if (not dropping_oversized and carry
                and len(carry) <= _MAX_HISTORY_REVERSE_RECORD_BYTES):
            yield 0, carry


def _next_jsonl_offset(path: str, offset: int, end_offset: int) -> int:
    """Move a byte budget boundary to the next complete JSONL record."""
    if offset <= 0:
        return 0
    with open(path, "rb") as source:
        source.seek(offset - 1)
        if source.read(1) == b"\n":
            return offset
        source.seek(offset)
        while source.tell() < end_offset:
            chunk = source.readline(
                min(_MAX_HISTORY_RECORD_CHARS + 1,
                    end_offset - source.tell()))
            if not chunk:
                return end_offset
            if chunk.endswith(b"\n"):
                return source.tell()
        return end_offset


def _previous_jsonl_record_offset(path: str, offset: int) -> int:
    """Return the start of the complete record immediately before ``offset``.

    A persisted user ``response_item`` can contain a large inline image and is
    therefore intentionally skipped by the bounded reverse JSON decoder.  The
    following small ``event_msg/user_message`` remains a useful page boundary,
    but its page must start one record earlier so history replay still sees the
    image.  Locate that record by newlines without buffering or decoding it.
    """
    if offset <= 0:
        return 0
    with open(path, "rb") as source:
        position = offset - 1  # exclude the newline immediately before offset
        while position > 0:
            start = max(0, position - _REVERSE_HISTORY_CHUNK_BYTES)
            source.seek(start)
            chunk = source.read(position - start)
            newline = chunk.rfind(b"\n")
            if newline >= 0:
                return start + newline + 1
            position = start
    return 0


def _fallback_history_id(path: str, kind: str, offset: int, raw_ts: str,
                         identity: str) -> str:
    seed = "\0".join((path, kind, identity, str(offset), raw_ts))
    return hashlib.sha256(seed.encode("utf-8", "surrogatepass")).hexdigest()[:32]


def _history_user_cursor(
    path: str,
    offset: int,
    line: bytes,
    *,
    prefer_turn_id: bool = True,
) -> str | None:
    if (len(line) > _MAX_HISTORY_BOUNDARY_RECORD_BYTES
            or (b'"type":"user_message"' not in line
                and b'"type": "user_message"' not in line)):
        return None
    try:
        row = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None
    payload = row.get("payload") if isinstance(row, dict) else None
    if (row.get("type") != "event_msg" or not isinstance(payload, dict)
            or payload.get("type") != "user_message"):
        return None
    if not visible_codex_user_message(payload.get("message")):
        return None
    turn_id = payload.get("turn_id")
    if (prefer_turn_id and isinstance(turn_id, str)
            and _SAFE_WIRE_ID.fullmatch(turn_id)):
        return turn_id
    raw_ts = row.get("timestamp", "")
    return _fallback_history_id(
        path, "user", offset, str(raw_ts), type(turn_id).__name__)


def _history_turn_cursor(line: bytes) -> str | None:
    if (len(line) > _MAX_HISTORY_BOUNDARY_RECORD_BYTES
            or (b'"type":"task_started"' not in line
                and b'"type": "task_started"' not in line)):
        return None
    try:
        row = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None
    payload = row.get("payload") if isinstance(row, dict) else None
    if (row.get("type") != "event_msg" or not isinstance(payload, dict)
            or payload.get("type") != "task_started"):
        return None
    turn_id = payload.get("turn_id")
    if isinstance(turn_id, str) and _SAFE_WIRE_ID.fullmatch(turn_id):
        return turn_id
    return None


_HISTORY_TERMINAL_TYPES = frozenset({
    "task_complete",
    "turn_aborted",
    "task_failed",
    "turn_failed",
    "task_error",
    "task_cancelled",
})


def _history_terminal_marker(line: bytes) -> bool:
    if (len(line) > _MAX_HISTORY_BOUNDARY_RECORD_BYTES
            or not any(marker.encode() in line
                       for marker in _HISTORY_TERMINAL_TYPES)):
        return False
    try:
        row = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return False
    payload = row.get("payload") if isinstance(row, dict) else None
    return bool(
        row.get("type") == "event_msg"
        and isinstance(payload, dict)
        and payload.get("type") in _HISTORY_TERMINAL_TYPES
    )


def _history_boundaries(path: str, *, use_turns: bool):
    if not use_turns:
        for offset, line in _reverse_jsonl_records(path):
            cursor = _history_user_cursor(path, offset, line)
            if cursor is not None:
                yield offset, cursor
        return

    # A task_started without a user_message is ambiguous. It is an independent
    # goal/background turn only when the preceding visible turn had already
    # reached a terminal record; otherwise it is an automatic continuation of
    # the same visible reply. Resolve that relationship while walking backwards
    # so pagination counts visible chat turns, not internal app-server turns.
    # Multiple user messages may be steered into one still-running Codex task.
    # The oldest message keeps the authoritative task cursor; every newer
    # message is an independent visible chat boundary with its own fallback id.
    # Delaying emission until task_started preserves reverse chronological order
    # while retaining the long-standing task cursor for ordinary one-user turns.
    segment_users: list[tuple[int, str, str]] = []
    pending_assistant_only: tuple[int, str] | None = None
    for offset, line in _reverse_jsonl_records(path):
        user_cursor = _history_user_cursor(path, offset, line)
        if user_cursor is not None:
            fallback_cursor = _history_user_cursor(
                path, offset, line, prefer_turn_id=False)
            if fallback_cursor is None:
                continue
            segment_users.append((
                _previous_jsonl_record_offset(path, offset),
                user_cursor,
                fallback_cursor,
            ))
            continue

        turn_cursor = _history_turn_cursor(line)
        if turn_cursor is not None:
            # An unresolved newer no-user start had no terminal boundary
            # between it and this older start, so it merely continued this turn.
            pending_assistant_only = None
            if segment_users:
                # Reverse scan order is newest -> oldest. Extra steered messages
                # need stable per-record cursors; the oldest message keeps the
                # task id so existing pagination cursors remain compatible.
                for boundary, _cursor, extra_cursor in segment_users[:-1]:
                    yield boundary, extra_cursor
                yield offset, turn_cursor
            else:
                pending_assistant_only = (offset, turn_cursor)
            segment_users = []
            continue

        if (pending_assistant_only is not None
                and _history_terminal_marker(line)):
            yield pending_assistant_only
            pending_assistant_only = None


def codex_history_window(
    path: str, *, before: str | None, limit: int | None,
    max_bytes: int = _DEFAULT_HISTORY_WINDOW_MAX_BYTES,
) -> tuple[int, int, bool, str | None, int | None]:
    """Select a bounded Codex history page by scanning user turns backwards.

    Returns ``(start_offset, end_offset, has_older, forced_oldest_cursor,
    forced_boundary_offset)``.
    The rollout can be many gigabytes: only boundary records are decoded while
    locating the latest page, and the forward translator sees at most the
    configured source window.  ``forced_oldest_cursor`` preserves pagination
    when one visible turn alone is larger than the byte budget and its prefix
    must be omitted. ``forced_boundary_offset`` lets the caller recover the
    omitted user boundary without parsing or retaining that entire turn.
    """
    size = os.path.getsize(path)
    if size <= 0 or not isinstance(limit, int) or limit <= 0:
        return 0, size, False, None, None
    byte_budget = max(1024 * 1024, int(max_bytes))

    # Current app-server rollouts have an authoritative task_started boundary
    # for user and assistant-only continuation turns.  Fall back to historical
    # user_message boundaries only for older rollout shapes.
    for use_turns in (True, False):
        end_offset = size
        target_found = before is None
        boundaries: list[tuple[int, str]] = []
        saw_boundary = False
        for offset, cursor in _history_boundaries(path, use_turns=use_turns):
            saw_boundary = True
            if not target_found:
                if cursor == before:
                    target_found = True
                    end_offset = offset
                continue
            boundaries.append((offset, cursor))
            if end_offset - offset > byte_budget:
                if len(boundaries) > 1:
                    start_offset, _ = boundaries[-2]
                    return start_offset, end_offset, True, None, None
                # Preserve the recent tail of a pathological single turn. The
                # true task cursor remains the paging cursor for older history.
                start_offset = _next_jsonl_offset(
                    path, max(0, end_offset - byte_budget), end_offset)
                return start_offset, end_offset, True, cursor, offset
            if len(boundaries) > limit:
                start_offset, _ = boundaries[limit - 1]
                return start_offset, end_offset, True, None, None
        if saw_boundary:
            if before is not None and not target_found:
                return 0, 0, False, None, None
            return 0, end_offset, False, None, None
    if size > byte_budget:
        start_offset = _next_jsonl_offset(
            path, size - byte_budget, size)
        return start_offset, size, True, None, None
    return 0, size, False, None, None


def codex_history_boundary_user(
    path: str,
    boundary_offset: int,
    cursor: str,
    *,
    max_scan_bytes: int = _MAX_HISTORY_BOUNDARY_FORWARD_BYTES,
) -> UserMsg | None:
    """Recover the user row omitted from a bounded single-turn tail.

    A single Codex turn can grow far beyond the history byte window, especially
    after one or more ``compacted`` records.  Reading only its recent tail keeps
    memory bounded but otherwise leaves the browser with tool/assistant events
    that have no prompt.  Scan forward from the already-discovered turn boundary
    only until the first visible user record and reuse the paging cursor as its
    stable id.  Oversized JSONL records are skipped by ``_bounded_jsonl_records``
    rather than materialized.
    """
    if (not isinstance(boundary_offset, int) or boundary_offset < 0
            or not isinstance(cursor, str)
            or not _SAFE_WIRE_ID.fullmatch(cursor)):
        return None
    try:
        size = os.path.getsize(path)
        end_offset = min(
            size,
            boundary_offset + max(
                1024 * 1024, int(max_scan_bytes)),
        )
        source = open(path, "rb")
    except (OSError, TypeError, ValueError):
        return None

    saw_task_start = False
    pending_images: list = []
    with source:
        source.seek(boundary_offset)
        for _offset, line in _bounded_jsonl_records(
                source, end_offset=end_offset):
            try:
                row = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            payload = row.get("payload") if isinstance(row, dict) else None
            if not isinstance(payload, dict):
                continue
            row_type = row.get("type")
            payload_type = payload.get("type")
            if row_type == "event_msg" and payload_type == "task_started":
                if saw_task_start:
                    return None
                saw_task_start = True
                continue
            if (row_type == "response_item"
                    and payload_type == "message"
                    and payload.get("role") == "user"):
                for item in payload.get("content") or []:
                    if (isinstance(item, dict)
                            and item.get("type") == "input_image"):
                        image = _data_uri_to_img(item.get("image_url"))
                        if image:
                            pending_images.append(image)
                continue
            if row_type != "event_msg" or payload_type != "user_message":
                continue
            prompt = visible_codex_user_message(payload.get("message"))
            if not prompt:
                return None
            event = UserMsg(msg_id=cursor, prompt=prompt)
            if pending_images:
                event.images = pending_images
            raw_ts = row.get("timestamp")
            if isinstance(raw_ts, str):
                try:
                    event.ts = datetime.fromisoformat(
                        raw_ts.replace("Z", "+00:00")).timestamp()
                except (TypeError, ValueError):
                    pass
            return event
    return None


class CodexStreamTranslator:
    def __init__(self, tool_result_max: int):
        self.tool_result_max = tool_result_max
        self._started: set[str] = set()
        self._text_seen: set[str] = set()
        self._message_channels: dict[str, str] = {}
        self._tools_started: set[str] = set()
        self._tool_message_ids: dict[str, str] = {}
        self._reasoning_started: set[str] = set()
        self._file_diffs: dict[str, str] = {}
        self._open_msg: str | None = None
        self._open_channel = "unknown"
        self._visible_output = False
        self._terminal_error = False
        self._delta_chars: dict[tuple[str, str], int] = {}
        self._delta_events: dict[tuple[str, str], int] = {}
        self._truncated_delta_streams: set[tuple[str, str]] = set()
        self._finished_delta_items: set[str] = set()
        # One translator owns one turn. Keep a fixed admission set instead of
        # allowing every distinct provider id to grow several parallel maps.
        # Rejected ids are not tombstoned individually: once full, *all* new ids
        # stay rejected, so a later completed event cannot resurrect them.
        self._live_items: set[str] = set()
        self._live_items_truncated = False
        self._turn_closed = False

    def feed(self, msg: dict) -> list:
        method = msg.get("method")
        p = msg.get("params") if isinstance(msg.get("params"), dict) else {}
        out: list = []

        if method == "item/agentMessage/delta":
            iid = _live_id(p.get("itemId"), "agent-message")
            if not self._admit_live_item(iid, out):
                return out
            channel = self._message_channels.get(iid, "unknown")
            if iid not in self._started:
                if self._open_msg is not None and self._open_msg != iid:
                    self._close_open(out)
                self._started.add(iid)
                self._open_msg = iid
                self._open_channel = channel
                out.append(AssistantMsgStart(message_id=iid, channel=channel))
            delta = p.get("delta")
            if isinstance(delta, str) and delta:
                self._text_seen.add(iid)
                self._visible_output = True
                out.append(Delta(message_id=iid, text=delta, channel=channel))

        elif method == "item/started":
            item = p.get("item") if isinstance(p.get("item"), dict) else {}
            item_type = item.get("type")
            if item_type == "agentMessage":
                iid = _live_id(item.get("id"), "agent-message")
                if not self._admit_live_item(iid, out):
                    return out
                channel = _assistant_channel(item.get("phase"))
                self._message_channels[iid] = channel
                if iid not in self._started:
                    if self._open_msg is not None and self._open_msg != iid:
                        self._close_open(out)
                    self._started.add(iid)
                    self._open_msg = iid
                    self._open_channel = channel
                    out.append(AssistantMsgStart(
                        message_id=iid, channel=channel))
            elif item_type in _TOOL_TYPES:
                self._visible_output = True
                out.extend(self._tool_use(item))
            elif item_type in _PROCESS_ITEM_TYPES:
                iid = _live_id(item.get("id"), str(item_type or "process"))
                if not self._admit_live_item(iid, out):
                    return out
                event = self._process_item(item, p, completed=False)
                if event is not None:
                    self._visible_output = True
                    out.append(event)

        elif method == "item/completed":
            item = p.get("item") if isinstance(p.get("item"), dict) else {}
            t = item.get("type")
            if t == "agentMessage":
                text = item.get("text") if isinstance(item.get("text"), str) else ""
                iid = _live_id(item.get("id") or text, "agent-message")
                if not self._admit_live_item(iid, out):
                    return out
                channel = _assistant_channel(item.get("phase"))
                if channel == "unknown":
                    channel = self._message_channels.get(iid, "unknown")
                else:
                    self._message_channels[iid] = channel
                # Some providers send only item/completed with the final text and
                # no delta notification. Preserve that answer instead of turning
                # it into a false empty-completed error.
                if text and iid not in self._text_seen:
                    if iid not in self._started:
                        if self._open_msg is not None and self._open_msg != iid:
                            self._close_open(out)
                        self._started.add(iid)
                        self._open_msg = iid
                        self._open_channel = channel
                        out.append(AssistantMsgStart(
                            message_id=iid, channel=channel))
                    self._text_seen.add(iid)
                    self._visible_output = True
                    out.append(Delta(
                        message_id=iid, text=text, channel=channel))
                if iid in self._started:
                    out.append(AssistantMsgEnd(
                        message_id=iid, channel=channel))
                    if self._open_msg == iid:
                        self._open_msg = None
                        self._open_channel = "unknown"
            elif t in _TOOL_TYPES:
                self._visible_output = True
                iid = _live_id(item.get("id"), f"{t}-tool")
                if not self._admit_live_item(iid, out):
                    return out
                if iid not in self._tools_started:
                    out.extend(self._tool_use(item))
                elif t == "fileChange":
                    out.append(self._tool_update(item))
                out.append(self._tool_result(item))
            elif t in _PROCESS_ITEM_TYPES:
                iid = _live_id(item.get("id"), str(t or "process"))
                if not self._admit_live_item(iid, out):
                    return out
                event = self._process_item(item, p, completed=True)
                if event is not None:
                    self._visible_output = True
                    out.append(event)
            if t in _TOOL_TYPES | _PROCESS_ITEM_TYPES:
                fallback = {
                    "commandExecution": "command-tool",
                    "fileChange": "fileChange-tool",
                    "mcpToolCall": "mcpToolCall-tool",
                    "dynamicToolCall": "dynamicToolCall-tool",
                    "webSearch": "webSearch-tool",
                }.get(t, str(t or "process"))
                finished_id = _live_id(item.get("id"), fallback)
                self._finish_delta_item(finished_id)
                self._file_diffs.pop(finished_id, None)

        elif method == "item/reasoning/summaryPartAdded":
            iid = _live_id(p.get("itemId"), "reasoning")
            if not self._admit_live_item(iid, out):
                return out
            event = self._ensure_reasoning(iid, p)
            if event is not None:
                self._visible_output = True
                out.append(event)

        elif method == "item/reasoning/summaryTextDelta":
            iid = _live_id(p.get("itemId"), "reasoning")
            if not self._admit_live_item(iid, out):
                return out
            event = self._ensure_reasoning(iid, p)
            if event is not None:
                out.append(event)
            delta = self._bounded_live_delta(
                iid, "reasoning-summary", p.get("delta"), 512 * 1024)
            if delta:
                self._visible_output = True
                out.append(ProcessEvent(
                    item_id=iid,
                    kind="reasoning",
                    phase="update",
                    status="running",
                    turn_id=_optional_wire_id(p.get("turnId"), "turn"),
                    title="思考",
                    append_to="summary",
                    delta=delta,
                ))

        elif method == "item/plan/delta":
            iid = _live_id(p.get("itemId"), "plan")
            if not self._admit_live_item(iid, out):
                return out
            delta = self._bounded_live_delta(
                iid, "plan-detail", p.get("delta"), 512 * 1024)
            if delta:
                self._visible_output = True
                out.append(ProcessEvent(
                    item_id=iid,
                    kind="plan",
                    phase="update",
                    status="running",
                    turn_id=_optional_wire_id(p.get("turnId"), "turn"),
                    title="计划",
                    append_to="detail",
                    delta=delta,
                ))

        elif method == "turn/plan/updated":
            turn_id = _optional_wire_id(p.get("turnId"), "turn")
            iid = _live_id(
                f"plan:{turn_id or p.get('turnId') or 'current'}", "plan")
            if not self._admit_live_item(iid, out):
                return out
            plan = []
            for entry in (p.get("plan") or [])[:128]:
                if not isinstance(entry, dict):
                    continue
                step, _ = bounded_text(entry.get("step"), 16 * 1024)
                if not step:
                    continue
                plan.append({
                    "step": step,
                    "status": _plan_status(entry.get("status")),
                })
            explanation, _ = bounded_text(p.get("explanation"), 64 * 1024)
            self._visible_output = True
            out.append(TurnPlan(
                item_id=iid,
                turn_id=turn_id,
                explanation=explanation or None,
                plan=plan,
            ))

        elif method == "item/commandExecution/outputDelta":
            iid = _live_id(p.get("itemId"), "command-tool")
            if not self._admit_live_item(iid, out):
                return out
            delta = self._bounded_live_delta(
                iid, "output", p.get("delta"),
                min(self.tool_result_max, 512 * 1024))
            if delta:
                out.append(ToolDelta(
                    tool_use_id=iid,
                    stream="output",
                    delta=delta,
                ))

        elif method == "item/fileChange/outputDelta":
            # Kept for old app-server builds; 0.144.1 marks it deprecated.
            iid = _live_id(p.get("itemId"), "fileChange-tool")
            if not self._admit_live_item(iid, out):
                return out
            delta = self._bounded_live_delta(
                iid, "output", p.get("delta"),
                min(self.tool_result_max, 512 * 1024))
            if delta:
                out.append(ToolDelta(
                    tool_use_id=iid,
                    stream="output",
                    delta=delta,
                ))

        elif method == "item/fileChange/patchUpdated":
            iid = _live_id(p.get("itemId"), "fileChange-tool")
            if not self._admit_live_item(iid, out):
                return out
            if iid in self._tools_started:
                out.append(self._tool_update({
                    "type": "fileChange",
                    "id": p.get("itemId"),
                    "status": "inProgress",
                    "changes": p.get("changes"),
                }))
            latest, _ = bounded_text(_changes_diff(p.get("changes")), 2 * 1024 * 1024)
            previous = self._file_diffs.get(iid, "")
            self._file_diffs[iid] = latest
            # patchUpdated is a snapshot. ToolDelta is append-only, so forward
            # only the genuinely-new suffix; non-monotonic rewrites are still
            # delivered authoritatively by item/completed.diff.
            if latest and latest.startswith(previous):
                delta = self._bounded_live_delta(
                    iid, "diff", latest[len(previous):], 512 * 1024)
                if delta:
                    out.append(ToolDelta(
                        tool_use_id=iid, stream="diff", delta=delta))

        elif method == "turn/diff/updated":
            turn_id = _optional_wire_id(p.get("turnId"), "turn")
            iid = _live_id(
                f"diff:{turn_id or p.get('turnId') or 'current'}", "diff")
            if not self._admit_live_item(iid, out):
                return out
            diff, truncated = bounded_text(p.get("diff"), 2 * 1024 * 1024)
            self._visible_output = True
            out.append(TurnDiff(
                item_id=iid,
                turn_id=turn_id,
                diff=diff,
                truncated=True if truncated else None,
            ))

        elif method == "item/mcpToolCall/progress":
            iid = _live_id(p.get("itemId"), "mcpToolCall-tool")
            if not self._admit_live_item(iid, out):
                return out
            progress = self._bounded_live_delta(
                iid, "progress", p.get("message"), 64 * 1024)
            if progress:
                out.append(ToolDelta(
                    tool_use_id=iid,
                    stream="progress",
                    delta=progress,
                ))

        elif method == "item/commandExecution/terminalInteraction":
            # The official payload's only interaction body is `stdin`.  It may
            # contain a password, token, or an answer to a secret prompt, so never
            # copy it to the wire.  Preserve a visible, sanitized timeline marker
            # instead of making the interaction look like a stalled command.
            command_id = _live_id(p.get("itemId"), "command-tool")
            iid = _live_id(f"{command_id}:terminal", "terminal")
            if not self._admit_live_item(iid, out):
                return out
            self._visible_output = True
            out.append(ProcessEvent(
                item_id=iid,
                kind="terminal",
                phase="snapshot",
                status="succeeded",
                turn_id=_optional_wire_id(p.get("turnId"), "turn"),
                parent_id=command_id,
                title="终端交互",
                summary="已向运行中的终端进程写入输入（内容已隐藏）",
            ))

        elif method == "model/rerouted":
            turn_id = _optional_wire_id(p.get("turnId"), "turn")
            from_model = _bounded_model_field(
                p.get("fromModel"), _MODEL_NAME_MAX_CHARS)
            to_model = _bounded_model_field(
                p.get("toModel"), _MODEL_NAME_MAX_CHARS)
            reason = _bounded_model_field(
                p.get("reason"), _MODEL_ENUM_MAX_CHARS)
            if turn_id and from_model and to_model and reason:
                iid = _live_id(
                    f"reroute:{turn_id}:{from_model}:{to_model}:{reason}",
                    "model-reroute",
                )
                if not self._admit_live_item(iid, out):
                    return out
                summary, _ = bounded_text(
                    f"{from_model} → {to_model}", 1024)
                detail, _ = bounded_text(
                    f"原因：{reason}", _MODEL_DETAIL_MAX_CHARS)
                self._visible_output = True
                out.append(ProcessEvent(
                    item_id=iid,
                    kind="model",
                    phase="snapshot",
                    status="succeeded",
                    turn_id=turn_id,
                    title="模型已重路由",
                    summary=summary,
                    detail=detail,
                ))

        elif method == "model/safetyBuffering/updated":
            turn_id = _optional_wire_id(p.get("turnId"), "turn")
            model = _bounded_model_field(
                p.get("model"), _MODEL_NAME_MAX_CHARS)
            showing = p.get("showBufferingUi")
            if turn_id and model and isinstance(showing, bool):
                # One card follows the lifecycle of one turn/model pair. Repeated
                # updates therefore merge instead of filling the timeline.
                iid = _live_id(
                    f"safety-buffering:{turn_id}:{model}",
                    "model-safety-buffering",
                )
                if not self._admit_live_item(iid, out):
                    return out
                reasons = _bounded_model_list(p.get("reasons"))
                use_cases = _bounded_model_list(p.get("useCases"))
                faster_model = _bounded_model_field(
                    p.get("fasterModel"), _MODEL_NAME_MAX_CHARS)
                detail_parts = []
                if reasons:
                    detail_parts.append("原因：" + "、".join(reasons))
                if use_cases:
                    detail_parts.append("使用场景：" + "、".join(use_cases))
                if faster_model:
                    detail_parts.append(f"可用的更快模型：{faster_model}")
                detail, _ = bounded_text(
                    "\n".join(detail_parts), _MODEL_DETAIL_MAX_CHARS)
                self._visible_output = True
                out.append(ProcessEvent(
                    item_id=iid,
                    kind="safety",
                    phase="start" if showing else "end",
                    status="running" if showing else "succeeded",
                    turn_id=turn_id,
                    title="模型安全缓冲",
                    summary=f"模型：{model}",
                    detail=detail or None,
                ))

        elif method == "model/verification":
            turn_id = _optional_wire_id(p.get("turnId"), "turn")
            verifications = _bounded_model_list(p.get("verifications"))
            if turn_id and verifications:
                iid = _live_id(
                    f"model-verification:{turn_id}", "model-verification")
                if not self._admit_live_item(iid, out):
                    return out
                summary, _ = bounded_text(
                    "、".join(verifications), _MODEL_DETAIL_MAX_CHARS)
                self._visible_output = True
                out.append(ProcessEvent(
                    item_id=iid,
                    kind="safety",
                    phase="snapshot",
                    status="succeeded",
                    turn_id=turn_id,
                    title="模型验证",
                    summary=summary,
                ))

        elif method in {
            "item/autoApprovalReview/started",
            "item/autoApprovalReview/completed",
        }:
            event = _auto_approval_review_event(
                p, completed=method.endswith("/completed"))
            if event is not None and self._admit_live_item(event.item_id, out):
                self._visible_output = True
                out.append(event)

        elif method == "turn/moderationMetadata":
            # ``metadata`` is deliberately untyped in the public schema and may
            # contain provider-internal data. Preserve the lifecycle marker but
            # never forward the opaque payload across the remote boundary.
            turn_id = _optional_wire_id(p.get("turnId"), "turn")
            if turn_id and p.get("metadata") is not None:
                iid = _live_id(f"moderation:{turn_id}", "moderation")
                if not self._admit_live_item(iid, out):
                    return out
                self._visible_output = True
                out.append(ProcessEvent(
                    item_id=iid,
                    kind="safety",
                    phase="snapshot",
                    status="succeeded",
                    turn_id=turn_id,
                    title="内容安全检查",
                    summary="已完成（详细元数据未在远程端展示）",
                ))

        elif method in {"hook/started", "hook/completed"}:
            event = _hook_event(p, completed=(method == "hook/completed"))
            if event is not None and self._admit_live_item(event.item_id, out):
                self._visible_output = True
                out.append(event)

        elif method == "thread/compacted":
            turn_id = _optional_wire_id(p.get("turnId"), "turn")
            iid = _live_id(
                f"compaction:{turn_id or p.get('turnId') or 'current'}",
                "compaction")
            if not self._admit_live_item(iid, out):
                return out
            self._visible_output = True
            out.append(ProcessEvent(
                item_id=iid,
                kind="compaction",
                phase="end",
                status="succeeded",
                turn_id=turn_id,
                title="压缩上下文",
            ))

        # Raw reasoning text is intentionally ignored. Only the public summary
        # notifications above and the summary array on a completed item cross the
        # remote boundary.

        elif method == "error":
            # Retrying provider failures are progress, not terminal errors. Emit a
            # running StateEvent so old clients remain compatible while new clients
            # can replace the generic spinner with a useful status.
            err = p.get("error") if isinstance(p.get("error"), dict) else {}
            if p.get("willRetry"):
                out.append(StateEvent(
                    state="running",
                    phase="retrying",
                    detail=_retry_detail(err),
                ))
            else:
                self._terminal_error = True
                out.append(Error(
                    code=ERR_CC_CRASH,
                    message="Codex 本次回复未完成，请重试。",
                ))

        elif method == "turn/completed":
            self._close_open(out)
            turn = p.get("turn") or {}
            st = turn.get("status") or "completed"
            # A failed turn may carry provider diagnostics in turn.error. Keep
            # those on the local engine boundary and emit only stable product
            # copy (the error notification above may not fire for every mode).
            if st == "failed":
                if not self._terminal_error:
                    out.append(Error(
                        code=ERR_CC_CRASH,
                        message="Codex 本次回复未完成，请重试。",
                    ))
                    self._terminal_error = True
            # Codex 0.144.1 can record an upstream 503 as completed/error=null with
            # only the userMessage item. Treat that impossible "empty success" as
            # a terminal failure, while allowing tool-only turns as visible output.
            if st == "completed" and not self._visible_output:
                if not self._terminal_error:
                    out.append(Error(
                        code=ERR_CC_CRASH,
                        message=_EMPTY_COMPLETED_MESSAGE,
                    ))
                    self._terminal_error = True
                st = "failed"
            elif st == "completed" and self._terminal_error:
                st = "failed"
            # Map codex TurnStatus (completed|interrupted|failed) onto cc's wire
            # subtype vocabulary so the engine-agnostic reducer treats them right:
            # "interrupted" -> "error_during_execution" is the token the client keys
            # on to render the "— 已打断 —" note (verified: turn/interrupt yields
            # turn/completed{status:"interrupted"}).
            subtype = ("success" if st == "completed"
                       else "error_during_execution" if st == "interrupted"
                       else "error")
            completed_turn_id = turn.get("id")
            out.append(TurnEnd(result=TurnResult(
                subtype=subtype,
                duration_ms=int(turn.get("durationMs") or 0),
                is_error=(st != "completed"),
            ), turn_id=(completed_turn_id
                        if isinstance(completed_turn_id, str) else None)))
            self._clear_all_delta_budgets()
            self._turn_closed = True

        # everything else (raw reasoning, userMessage, mcpServer/startupStatus,
        # thread/status, account/rateLimits, tokenUsage, remoteControl…) -> skip.
        return out

    # ---- helpers ----
    def _admit_live_item(self, item_id: str, out: list) -> bool:
        if self._turn_closed:
            return False
        if item_id in self._live_items:
            return True
        if len(self._live_items) < _MAX_LIVE_ITEMS:
            self._live_items.add(item_id)
            return True
        if not self._live_items_truncated:
            self._live_items_truncated = True
            self._visible_output = True
            out.append(ProcessEvent(
                item_id=_LIVE_ITEMS_OMITTED_ID,
                kind="compaction",
                phase="snapshot",
                status="succeeded",
                title="较早过程已省略",
                summary="此回合的处理项目过多，后续新增项目未实时展示。",
            ))
        return False

    def _bounded_live_delta(
        self, item_id: str, stream: str, value, single_event_cap: int,
    ) -> str:
        """Bound cumulative append-only payload and append count per UI field."""
        key = (item_id, stream)
        if (item_id in self._finished_delta_items
                or key in self._truncated_delta_streams):
            return ""
        if key not in self._delta_chars and len(self._delta_chars) >= _MAX_DELTA_STREAMS:
            return ""
        budget = max(1, self.tool_result_max)
        used = self._delta_chars.get(key, 0)
        count = self._delta_events.get(key, 0)
        remaining = budget - used
        # Reserve the final allowed append for an explicit truncation marker.
        if remaining <= 0 or count >= _MAX_DELTA_EVENTS_PER_STREAM - 1:
            self._truncated_delta_streams.add(key)
            if remaining <= 0:
                return ""
            notice = _DELTA_TRUNCATION_NOTICE[-remaining:]
            self._delta_chars[key] = used + len(notice)
            self._delta_events[key] = count + 1
            return notice

        text, truncated = bounded_text(
            value, min(max(1, single_event_cap), remaining))
        if not text and not truncated:
            return ""
        if truncated:
            self._truncated_delta_streams.add(key)
            notice = _DELTA_TRUNCATION_NOTICE
            if len(notice) >= remaining:
                text = notice[-remaining:]
            else:
                text = text[:remaining - len(notice)] + notice
        self._delta_chars[key] = used + len(text)
        self._delta_events[key] = count + 1
        return text

    def _finish_delta_item(self, item_id: str) -> None:
        if len(self._finished_delta_items) < _MAX_FINISHED_DELTA_ITEMS:
            self._finished_delta_items.add(item_id)
        for key in [key for key in self._delta_chars if key[0] == item_id]:
            self._delta_chars.pop(key, None)
            self._delta_events.pop(key, None)
            self._truncated_delta_streams.discard(key)

    def _clear_all_delta_budgets(self) -> None:
        self._delta_chars.clear()
        self._delta_events.clear()
        self._truncated_delta_streams.clear()
        self._finished_delta_items.clear()

    def _close_open(self, out: list) -> None:
        if self._open_msg is None:
            return
        out.append(AssistantMsgEnd(
            message_id=self._open_msg,
            channel=self._open_channel,
        ))
        self._open_msg = None
        self._open_channel = "unknown"

    def _ensure_block(self, mid: str, out: list) -> None:
        """A tool card needs an assistant message block to hang under (the reducer
        keys tool cards by message_id); open one lazily if none is active."""
        if self._open_msg is None:
            self._open_msg = mid
            self._open_channel = "commentary"
            self._started.add(mid)
            out.append(AssistantMsgStart(
                message_id=mid, channel="commentary"))

    def _tool_use(self, item: dict) -> list:
        out: list = []
        item_type = str(item.get("type") or "tool")
        iid = _live_id(item.get("id"), f"{item_type}-tool")
        if not self._admit_live_item(iid, out):
            return out
        if iid in self._tools_started:
            return out
        self._tools_started.add(iid)
        mid = self._open_msg or iid
        self._ensure_block(mid, out)
        self._tool_message_ids[iid] = self._open_msg or mid
        inp = _tool_input(item)
        tool, category, title, server = _tool_presentation(item)
        out.append(ToolUse(
            message_id=self._open_msg or "",
            tool_use_id=iid,
            tool=tool,
            input=bounded_tool_input(inp, self.tool_result_max),
            category=category,
            title=title,
            server=server,
        ))
        return out

    def _tool_update(self, item: dict) -> ToolUse:
        item_type = str(item.get("type") or "tool")
        iid = _live_id(item.get("id"), f"{item_type}-tool")
        tool, category, title, server = _tool_presentation(item)
        return ToolUse(
            message_id=self._tool_message_ids.get(iid, iid),
            tool_use_id=iid,
            tool=tool,
            input=bounded_tool_input(
                _tool_input(item), self.tool_result_max),
            category=category,
            title=title,
            server=server,
        )

    def _tool_result(self, item: dict) -> ToolResult:
        item_type = item.get("type")
        status = _process_status(item.get("status"))
        code = _nonnegative_or_signed_int(item.get("exitCode"))
        diff = None
        summary = None
        raw_content = item.get("aggregatedOutput") or item.get("output") or ""
        if item_type == "fileChange":
            diff, diff_truncated = bounded_text(
                _changes_diff(item.get("changes")), 2 * 1024 * 1024)
            paths = _change_paths(item.get("changes"))
            summary = _file_summary(paths, status)
            raw_content = summary
        elif item_type == "mcpToolCall":
            raw_content = _mcp_result_content(item)
            error = item.get("error") if isinstance(item.get("error"), dict) else {}
            summary, _ = bounded_text(error.get("message"), 64 * 1024)
            if summary:
                status = "failed"
        elif item_type == "dynamicToolCall":
            raw_content = _redact_credentials(item.get("contentItems") or "")
            success = item.get("success")
            if success is False:
                status = "failed"
            elif success is True:
                status = "succeeded"
        elif item_type == "webSearch":
            raw_content = {
                "query": item.get("query"),
                "action": item.get("action"),
            }
            status = "succeeded"
        text, was_truncated = bounded_text(raw_content, self.tool_result_max)
        truncated = True if was_truncated else None
        if item_type == "fileChange" and diff_truncated:
            truncated = True
        is_error = (
            status in {"failed", "declined", "cancelled", "interrupted"}
            or (code is not None and code != 0)
        )
        return ToolResult(
            tool_use_id=_live_id(item.get("id"), f"{item_type}-tool"),
            content=text,
            is_error=is_error,
            truncated=truncated,
            status=status,
            summary=summary or None,
            diff=diff or None,
            exit_code=code,
            duration_ms=_duration_ms(item.get("durationMs")),
        )

    def _ensure_reasoning(self, iid: str, params: dict):
        if iid in self._reasoning_started:
            return None
        self._reasoning_started.add(iid)
        return ProcessEvent(
            item_id=iid,
            kind="reasoning",
            phase="start",
            status="running",
            turn_id=_optional_wire_id(params.get("turnId"), "turn"),
            title="思考",
        )

    def _process_item(self, item: dict, params: dict, *, completed: bool):
        item_type = item.get("type")
        iid = _live_id(item.get("id"), str(item_type or "process"))
        turn_id = _optional_wire_id(params.get("turnId"), "turn")
        phase = "end" if completed else "start"
        status = "succeeded" if completed else "running"
        if item_type == "reasoning":
            summary = _reasoning_summary(item)
            if not summary:
                # Never substitute content/encryptedContent for a missing public
                # summary.
                return None
            self._reasoning_started.add(iid)
            return ProcessEvent(
                item_id=iid,
                kind="reasoning",
                phase=phase,
                status=status,
                turn_id=turn_id,
                title="思考",
                summary=summary,
            )
        if item_type == "plan":
            detail, _ = bounded_text(item.get("text"), 256 * 1024)
            return ProcessEvent(
                item_id=iid, kind="plan", phase=phase, status=status,
                turn_id=turn_id, title="计划", detail=detail or None)
        if item_type == "collabAgentToolCall":
            return _collab_event(item, turn_id, completed)
        if item_type == "subAgentActivity":
            return _subagent_event(item, turn_id, completed)
        if item_type == "contextCompaction":
            return ProcessEvent(
                item_id=iid, kind="compaction", phase=phase, status=status,
                turn_id=turn_id, title="压缩上下文")
        if item_type == "imageView":
            path, _ = bounded_text(item.get("path"), 16 * 1024)
            return ProcessEvent(
                item_id=iid, kind="server_tool", phase=phase, status=status,
                turn_id=turn_id, title="查看图片",
                summary=path or None,
                input={"file_path": path} if path else None,
            )
        if item_type == "sleep":
            duration = _duration_ms(item.get("durationMs"))
            return ProcessEvent(
                item_id=iid, kind="task", phase=phase, status=status,
                turn_id=turn_id, title="等待",
                summary=_human_duration(duration) if duration is not None else None,
                duration_ms=duration,
            )
        if item_type == "imageGeneration":
            generated_status = _process_status(item.get("status"))
            if completed and generated_status in {"unknown", "running", "pending"}:
                generated_status = "succeeded"
            prompt, prompt_truncated = bounded_text(
                item.get("revisedPrompt"), 64 * 1024)
            path, _ = bounded_text(item.get("savedPath"), 16 * 1024)
            # `result` may be a full base64 image. The saved file is previewable
            # through the existing authenticated artifact route; never duplicate
            # the binary payload into replay history or relay buffers.
            return ProcessEvent(
                item_id=iid, kind="server_tool", phase=phase,
                status=generated_status, turn_id=turn_id, title="生成图片",
                summary=prompt or (path if path else None),
                input={"file_path": path} if path else None,
                truncated=True if prompt_truncated else None,
            )
        if item_type in {"enteredReviewMode", "exitedReviewMode"}:
            review, review_truncated = bounded_text(
                item.get("review"), 256 * 1024)
            return ProcessEvent(
                item_id=iid, kind="safety", phase=phase, status=status,
                turn_id=turn_id,
                title=("进入 Review" if item_type == "enteredReviewMode"
                       else "退出 Review"),
                detail=review or None,
                truncated=True if review_truncated else None,
            )
        return None


def _human_duration(duration_ms: int) -> str:
    seconds = duration_ms / 1000
    if seconds < 60:
        return f"{seconds:g} 秒"
    minutes = seconds / 60
    if minutes < 60:
        return f"{minutes:g} 分钟"
    return f"{minutes / 60:g} 小时"


def _auto_approval_review_event(params: dict, *, completed: bool):
    review_id = params.get("reviewId")
    if not isinstance(review_id, str) or not review_id:
        return None
    review = params.get("review") if isinstance(params.get("review"), dict) else {}
    action = params.get("action") if isinstance(params.get("action"), dict) else {}
    action_type = str(action.get("type") or "")
    action_labels = {
        "command": "命令",
        "execve": "程序执行",
        "applyPatch": "文件修改",
        "networkAccess": "网络访问",
        "mcpToolCall": "MCP 工具",
        "requestPermissions": "权限请求",
    }
    review_status = _process_status(review.get("status"))
    if not completed:
        review_status = "running"
    elif review_status in {"unknown", "running", "pending"}:
        review_status = "succeeded"
    risk = str(review.get("riskLevel") or "")
    authorization = str(review.get("userAuthorization") or "")
    summary_parts = [action_labels.get(action_type, "工具操作")]
    if risk in {"low", "medium", "high", "critical"}:
        summary_parts.append(f"风险 {risk}")
    if authorization in {"unknown", "low", "medium", "high"}:
        summary_parts.append(f"用户授权 {authorization}")
    rationale, rationale_truncated = bounded_text(
        review.get("rationale"), 64 * 1024)
    started_at = _nonnegative_or_signed_int(params.get("startedAtMs"))
    completed_at = _nonnegative_or_signed_int(params.get("completedAtMs"))
    duration = None
    if started_at is not None and completed_at is not None and completed_at >= started_at:
        duration = completed_at - started_at
    return ProcessEvent(
        item_id=_live_id(review_id, "auto-approval-review"),
        kind="safety",
        phase="end" if completed else "start",
        status=review_status,
        turn_id=_optional_wire_id(params.get("turnId"), "turn"),
        parent_id=_optional_wire_id(params.get("targetItemId"), "item"),
        title="自动审批审查",
        summary=" · ".join(summary_parts),
        detail=rationale or None,
        duration_ms=duration,
        truncated=True if rationale_truncated else None,
    )


def _retry_detail(error: dict) -> str:
    """Return a bounded, credential-free retry status for the client."""
    message = error.get("message") if isinstance(error.get("message"), str) else ""
    details = (error.get("additionalDetails")
               if isinstance(error.get("additionalDetails"), str) else "")
    combined = message + " " + details
    status_match = re.search(r"\b([45]\d\d)\b", combined)
    status = status_match.group(1) if status_match else _structured_http_status(error)
    attempt = re.search(r"\b(\d+\s*/\s*\d+)\b", combined)
    if status:
        text = f"上游服务返回 HTTP {status}，Codex 正在重试"
    else:
        text = "Codex 上游请求暂时失败，正在重试"
    if attempt:
        text += f"（{attempt.group(1).replace(' ', '')}）"
    return text + "…"


def _bounded_model_field(value, max_chars: int) -> str:
    """Copy one declared model-notification string, never arbitrary payloads."""
    if not isinstance(value, str) or not value:
        return ""
    return bounded_text(value, max_chars)[0]


def _bounded_model_list(value) -> list[str]:
    """Bound declared string arrays by item count and per-item length."""
    if not isinstance(value, list):
        return []
    out = []
    for item in islice(value, _MODEL_LIST_MAX_ITEMS):
        text = _bounded_model_field(item, _MODEL_ENUM_MAX_CHARS)
        if text:
            out.append(text)
    return out


def _structured_http_status(error: dict) -> str | None:
    """Find a bounded codexErrorInfo.httpStatusCode without exposing details."""
    stack = [error.get("codexErrorInfo")]
    seen = 0
    while stack and seen < 32:
        value = stack.pop()
        seen += 1
        if not isinstance(value, dict):
            continue
        status = value.get("httpStatusCode")
        if isinstance(status, int) and 400 <= status <= 599:
            return str(status)
        stack.extend(list(value.values())[:16])
    return None


def _live_id(value, kind: str) -> str:
    """Return a protocol-safe, stable identity without trusting provider text."""
    if isinstance(value, str) and _SAFE_WIRE_ID.fullmatch(value):
        return value
    if isinstance(value, str):
        identity = value[:4096]
    elif value is None:
        identity = "missing"
    else:
        identity = type(value).__name__
    return hashlib.sha256(
        f"codex\0{kind}\0{identity}".encode("utf-8", "surrogatepass")
    ).hexdigest()[:32]


def _optional_wire_id(value, kind: str) -> str | None:
    return None if value is None else _live_id(value, kind)


def _assistant_channel(value) -> str:
    if value in {"final", "final_answer"}:
        return "final"
    if value == "commentary":
        return "commentary"
    if value == "thinking":
        return "thinking"
    return "unknown"


def _process_status(value) -> str:
    key = str(value or "").replace("_", "").replace("-", "").lower()
    if key in {"pending"}:
        return "pending"
    if key in {"inprogress", "running", "started"}:
        return "running"
    if key in {"completed", "complete", "succeeded", "success", "approved"}:
        return "succeeded"
    if key in {"failed", "failure", "error", "timedout", "timeout"}:
        return "failed"
    if key in {"declined", "denied", "blocked"}:
        return "declined"
    if key in {"cancelled", "canceled", "stopped"}:
        return "cancelled"
    if key in {"interrupted", "aborted"}:
        return "interrupted"
    return "unknown"


def _plan_status(value) -> str:
    status = _process_status(value)
    if status == "running":
        return "inProgress"
    if status == "succeeded":
        return "completed"
    return "pending"


def _duration_ms(value) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        result = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if result >= 0 else None


def _nonnegative_or_signed_int(value) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError, OverflowError):
        return None


def _is_credential_key(key_text: str) -> bool:
    """Match common credential keys across snake, kebab and camel case."""
    separated = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key_text)
    tokens = tuple(filter(None, re.split(r"[^a-z0-9]+", separated.lower())))
    compact = "".join(tokens)
    return (
        compact in _CREDENTIAL_EXACT_KEYS
        or any(fragment in compact for fragment in _CREDENTIAL_KEY_FRAGMENTS)
    )


def _redact_credentials(
    value,
    depth: int = 0,
    ancestors=None,
    node_budget=None,
):
    """Copy bounded JSON-like tool data and replace credential-bearing values."""
    if node_budget is None:
        node_budget = [_MAX_REDACTION_NODES]
    if node_budget[0] <= 0:
        return _REDACTION_BUDGET_EXCEEDED
    node_budget[0] -= 1
    if depth >= _MAX_REDACTION_DEPTH:
        return f"<{type(value).__name__} omitted>"
    if isinstance(value, dict):
        ancestors = ancestors if ancestors is not None else set()
        identity = id(value)
        if identity in ancestors:
            return "<cycle omitted>"
        ancestors.add(identity)
        try:
            out = {}
            for key, item in islice(
                value.items(), _MAX_REDACTION_DICT_ITEMS
            ):
                if node_budget[0] <= 0:
                    out[_REDACTION_REMAINDER_KEY] = (
                        _REDACTION_BUDGET_EXCEEDED
                    )
                    break
                key_text = key if isinstance(key, str) else f"<{type(key).__name__}>"
                if _is_credential_key(key_text):
                    node_budget[0] -= 1
                    safe_item = _REDACTED
                else:
                    safe_item = _redact_credentials(
                        item, depth + 1, ancestors, node_budget
                    )
                out[key_text[:128]] = safe_item
            return out
        finally:
            ancestors.discard(identity)
    if isinstance(value, (list, tuple)):
        ancestors = ancestors if ancestors is not None else set()
        identity = id(value)
        if identity in ancestors:
            return "<cycle omitted>"
        ancestors.add(identity)
        try:
            out = []
            for item in islice(value, _MAX_REDACTION_SEQUENCE_ITEMS):
                if node_budget[0] <= 0:
                    out.append(_REDACTION_BUDGET_EXCEEDED)
                    break
                out.append(_redact_credentials(
                    item, depth + 1, ancestors, node_budget
                ))
            return out
        finally:
            ancestors.discard(identity)
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    return f"<{type(value).__name__}>"


def _tool_input(item: dict) -> dict:
    item_type = item.get("type")
    if item_type == "commandExecution":
        out = {
            "command": item.get("command"),
            "cwd": item.get("cwd"),
            "actions": item.get("commandActions"),
            "source": item.get("source"),
        }
        if item.get("processId") is not None:
            out["process_id"] = item.get("processId")
        return {key: value for key, value in out.items() if value is not None}
    if item_type == "fileChange":
        changes = _change_descriptors(item.get("changes"))
        return {
            "changes": changes,
            "file_paths": _descriptor_paths(changes),
        }
    if item_type == "mcpToolCall":
        arguments = item.get("arguments")
        if isinstance(arguments, dict):
            return _redact_credentials(arguments)
        return {"arguments": _redact_credentials(arguments)}
    if item_type == "dynamicToolCall":
        arguments = item.get("arguments")
        sanitized = _redact_credentials(arguments)
        out = sanitized if isinstance(sanitized, dict) else {"arguments": sanitized}
        if item.get("namespace") is not None:
            out = dict(out)
            out["namespace"] = item.get("namespace")
        return out
    if item_type == "webSearch":
        return {
            key: item.get(key) for key in ("query", "action")
            if item.get(key) is not None
        }
    return {}


def _tool_presentation(item: dict) -> tuple[str, str, str | None, str | None]:
    item_type = item.get("type")
    if item_type == "commandExecution":
        actions = item.get("commandActions") or []
        first = actions[0] if actions and isinstance(actions[0], dict) else {}
        action_type = first.get("type")
        if action_type == "read":
            path = first.get("path") or first.get("name")
            title = f"读取 {path}" if path else "读取文件"
            return "readFile", "command", title, None
        if action_type == "listFiles":
            path = first.get("path")
            title = f"列出 {path}" if path else "列出文件"
            return "listFiles", "command", title, None
        if action_type == "search":
            query = first.get("query")
            title = f"搜索 {query}" if query else "搜索内容"
            return "search", "command", title, None
        return "shell", "command", "运行命令", None
    if item_type == "fileChange":
        paths = _change_paths(item.get("changes"))
        return "apply_patch", "file", _file_summary(paths, "running"), None
    if item_type == "mcpToolCall":
        server = str(item.get("server") or "MCP")[:1024]
        tool = str(item.get("tool") or "mcp")[:1024]
        return tool, "mcp", f"{server} · {tool}"[:1024], server
    if item_type == "dynamicToolCall":
        tool = str(item.get("tool") or "dynamicTool")[:1024]
        namespace = item.get("namespace")
        title = f"{namespace} · {tool}" if namespace else tool
        return tool, "server_tool", title[:1024], None
    if item_type == "webSearch":
        query, _ = bounded_text(item.get("query"), 900)
        return "webSearch", "web_search", (
            f"搜索 {query}" if query else "搜索网页"), None
    return str(item_type or "tool")[:1024], "tool", None, None


def _change_descriptors(changes) -> list[dict]:
    descriptors: list[dict] = []
    if isinstance(changes, list):
        iterable = changes[:64]
        for entry in iterable:
            if not isinstance(entry, dict):
                continue
            kind = entry.get("kind")
            if isinstance(kind, dict):
                kind = kind.get("type")
            descriptor = {
                "path": str(entry.get("path") or "")[:16 * 1024],
                "kind": str(kind or "update")[:128],
            }
            move_path = (entry.get("move_path")
                         or entry.get("destination_path") or entry.get("to"))
            if isinstance(move_path, str) and move_path:
                descriptor["move_path"] = move_path[:16 * 1024]
            descriptors.append(descriptor)
    elif isinstance(changes, dict):
        for path, change in list(changes.items())[:64]:
            kind = change.get("type") if isinstance(change, dict) else "update"
            descriptor = {
                "path": str(path)[:16 * 1024],
                "kind": str(kind or "update")[:128],
            }
            move_path = (change.get("move_path") if isinstance(change, dict)
                         else None)
            if isinstance(move_path, str) and move_path:
                descriptor["move_path"] = move_path[:16 * 1024]
            descriptors.append(descriptor)
    return descriptors


def _change_paths(changes) -> list[str]:
    return _descriptor_paths(_change_descriptors(changes))


def _descriptor_paths(descriptors: list[dict]) -> list[str]:
    paths: list[str] = []
    seen: set[str] = set()
    for entry in descriptors:
        for key in ("path", "move_path"):
            path = entry.get(key)
            if isinstance(path, str) and path and path not in seen:
                seen.add(path)
                paths.append(path)
    return paths


def _changes_diff(changes) -> str:
    """Normalize v2 FileUpdateChange arrays and legacy path->change maps."""
    parts: list[str] = []
    if isinstance(changes, list):
        for entry in changes[:64]:
            if not isinstance(entry, dict):
                continue
            diff = _change_diff(str(entry.get("path") or "file"), entry)
            if isinstance(diff, str) and diff:
                parts.append(diff)
    elif isinstance(changes, dict):
        for path, entry in list(changes.items())[:64]:
            if not isinstance(entry, dict):
                continue
            diff = _change_diff(str(path), entry)
            if isinstance(diff, str) and diff:
                parts.append(diff)
    return "\n".join(parts)


def _change_diff(path: str, entry: dict) -> str:
    explicit = entry.get("unified_diff") or entry.get("diff")
    if isinstance(explicit, str) and explicit:
        return explicit

    kind = entry.get("kind") or entry.get("type") or "update"
    if isinstance(kind, dict):
        kind = kind.get("type") or "update"
    old_content = entry.get("old_content")
    new_content = entry.get("content")
    if str(kind).lower() in {"add", "create", "added"}:
        old_content = ""
    elif str(kind).lower() in {"delete", "remove", "deleted"}:
        old_content = (entry.get("content") if old_content is None
                       else old_content)
        new_content = ""
    if not isinstance(old_content, str) or not isinstance(new_content, str):
        return ""
    from_file = "/dev/null" if not old_content else path
    to_file = "/dev/null" if not new_content else path
    return "".join(difflib.unified_diff(
        old_content.splitlines(keepends=True),
        new_content.splitlines(keepends=True),
        fromfile=from_file,
        tofile=to_file,
    ))


def _file_summary(paths: list[str], status: str) -> str:
    prefix = "修改了" if status == "succeeded" else "修改"
    if not paths:
        return f"{prefix}文件"
    if len(paths) == 1:
        return f"{prefix} {paths[0]}"[:64 * 1024]
    return f"{prefix} {len(paths)} 个文件"


def _reasoning_summary(item: dict) -> str:
    values = item.get("summary")
    parts: list[str] = []
    if isinstance(values, list):
        for value in values[:128]:
            if isinstance(value, str):
                text = value
            elif isinstance(value, dict) and value.get("type") == "summary_text":
                text = value.get("text")
            else:
                continue
            if isinstance(text, str) and text:
                parts.append(text)
    text, _ = bounded_text("\n\n".join(parts), 64 * 1024)
    return text


def _mcp_result_content(item: dict):
    error = item.get("error") if isinstance(item.get("error"), dict) else None
    if error is not None:
        return error.get("message") or "MCP tool call failed"
    result = item.get("result")
    if not isinstance(result, dict):
        return result or ""
    # `_meta` is server-private and can contain connector/session data. Never put
    # it in a replayable client ring.
    return _redact_credentials({
        key: result.get(key) for key in ("content", "structuredContent")
        if result.get(key) is not None
    })


def _collab_event(item: dict, turn_id: str | None, completed: bool):
    tool = str(item.get("tool") or "agent")[:1024]
    status = _process_status(item.get("status"))
    if completed and status in {"unknown", "running", "pending"}:
        status = "succeeded"
    labels = {
        "spawnAgent": "启动协作代理",
        "sendInput": "向协作代理发送消息",
        "resumeAgent": "恢复协作代理",
        "wait": "等待协作代理",
        "closeAgent": "关闭协作代理",
    }
    states = item.get("agentsStates")
    safe_states = {}
    if isinstance(states, dict):
        for agent_id, state in list(states.items())[:32]:
            if not isinstance(state, dict):
                continue
            safe_states[str(agent_id)[:128]] = {
                "status": _process_status(state.get("status")),
            }
    input_value = bounded_tool_input({
        "prompt": item.get("prompt"),
        "model": item.get("model"),
        "reasoning_effort": item.get("reasoningEffort"),
        "receivers": item.get("receiverThreadIds"),
        "agents": safe_states,
    }, 64 * 1024)
    return ProcessEvent(
        item_id=_live_id(item.get("id"), "collab-agent"),
        kind="agent",
        phase="end" if completed else "start",
        status=status,
        turn_id=turn_id,
        parent_id=_optional_wire_id(item.get("senderThreadId"), "thread"),
        title=labels.get(tool, "协作代理"),
        input=input_value,
        tool=tool,
    )


def _subagent_event(item: dict, turn_id: str | None, completed: bool):
    kind = str(item.get("kind") or "started")
    status = (
        "interrupted" if kind == "interrupted"
        else "succeeded" if completed
        else "running"
    )
    path, _ = bounded_text(item.get("agentPath"), 16 * 1024)
    return ProcessEvent(
        item_id=_live_id(item.get("id"), "sub-agent"),
        kind="agent",
        phase="end" if completed else "start",
        status=status,
        turn_id=turn_id,
        parent_id=_optional_wire_id(item.get("agentThreadId"), "thread"),
        title={
            "started": "协作代理已启动",
            "interacted": "协作代理有新进展",
            "interrupted": "协作代理已中断",
        }.get(kind, "协作代理"),
        summary=path or None,
    )


def _hook_event(params: dict, *, completed: bool):
    run = params.get("run") if isinstance(params.get("run"), dict) else None
    if run is None:
        return None
    status = _process_status(run.get("status"))
    if completed and status in {"unknown", "running", "pending"}:
        status = "succeeded"
    event_name = str(run.get("eventName") or "hook")[:256]
    handler_type = str(run.get("handlerType") or "")[:128]
    # Hook output/statusMessage can include command output, environment data, or
    # credentials. Only lifecycle metadata crosses the remote boundary.
    return ProcessEvent(
        item_id=_live_id(run.get("id"), "hook"),
        kind="hook",
        phase="end" if completed else "start",
        status=status,
        turn_id=_optional_wire_id(params.get("turnId"), "turn"),
        title=(f"Hook · {event_name}" + (
            f" · {handler_type}" if handler_type else ""))[:1024],
        duration_ms=_duration_ms(run.get("durationMs")),
    )


# ---- helpers the machine loop needs (codex analogs of stream.extract_*) ----

def codex_session_id(msg: dict) -> str | None:
    """Thread id from either current app-server notification shape."""
    p = msg.get("params") or {}
    thread_id = p.get("threadId")
    if isinstance(thread_id, str) and thread_id:
        return thread_id
    th = p.get("thread")
    if isinstance(th, dict):
        return th.get("id") or th.get("sessionId")
    return None


def is_turn_terminal(msg: dict) -> bool:
    """Codex's turn/completed plays the role of Claude's ResultMessage."""
    return msg.get("method") == "turn/completed"


# ---- on-disk Codex rollout -> wire events (session history) ----

def codex_translate_history(
    path: str,
    tool_result_max: int,
    *,
    start_offset: int = 0,
    end_offset: int | None = None,
    source_continuation: str | None = None,
    snapshot_in_progress: bool = False,
) -> tuple[list, str | None]:
    """Translate a Codex rollout .jsonl into wire events (same vocabulary as the
    live stream) + the model used. Codex analog of stream.translate_history.

    A turn = event_msg/user_message -> (function_call/reasoning...) -> agent_message.
    Skips the <environment_context>/<permissions> developer/user envelope messages;
    uses the clean event_msg user_message / agent_message text. Returns
    (events, model)."""
    events: list = []
    model: str | None = None
    turn_open = False
    active_turn_id: str | None = None
    active_msg_id: str | None = None
    pending_turn_id: str | None = None
    turn_visible = False
    turn_text_visible = False
    turn_final_visible = False
    turn_has_user = False
    turn_continuation_reason: str | None = None
    source_continuation_available = (
        source_continuation == "authoritative_page")
    assistant_open = False
    cur_mid: str | None = None
    cur_channel = "unknown"
    last_ts = None
    pending_images: list = []   # input_image blocks seen before the next user_message
    pending_compactions: list[
        tuple[str, float | None, str | None]
    ] = []
    pending_agent_message: tuple[dict, int, str] | None = None
    task_has_user = False
    seen_tool_uses: set[str] = set()
    seen_tool_results: set[str] = set()
    seen_authoritative_results: set[str] = set()
    plan_tool_ids: set[str] = set()
    seen_process_items: set[str] = set()
    history_tools: dict[str, tuple[str, str, str | None, str | None, dict]] = {}
    seen_agent_messages: set[tuple[str, str, str]] = set()
    seen_reasoning: set[tuple[str, str]] = set()

    def _ts(iso: str):
        try:
            return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
        except Exception:
            return None

    def _stable_id(kind: str, line_no: int, raw_ts: str = "", identity=None) -> str:
        """Deterministic fallback for rollout records that carry no item id."""
        stable_identity = str(identity or active_turn_id or "")
        return _fallback_history_id(
            path, kind, line_no, raw_ts, stable_identity)

    def _history_id(value, kind: str, line_no: int, raw_ts: str = "") -> str:
        if isinstance(value, str) and _SAFE_WIRE_ID.fullmatch(value):
            return value
        identity = value[:1024] if isinstance(value, str) else type(value).__name__
        return _stable_id(kind, line_no, raw_ts, identity)

    def _duration(payload: dict) -> int:
        try:
            return int(payload.get("duration_ms") or payload.get("durationMs") or 0)
        except (TypeError, ValueError):
            return 0

    def _completed_ts(payload: dict, fallback):
        value = payload.get("completed_at") or payload.get("completedAt")
        if isinstance(value, (int, float)):
            value = float(value)
            return value / 1000 if value > 100_000_000_000 else value
        if isinstance(value, str):
            return _ts(value) or fallback
        return fallback

    def ensure_assistant(
        line_no: int,
        raw_ts: str = "",
        item_id=None,
        channel: str = "commentary",
        *,
        force_new: bool = False,
    ):
        nonlocal assistant_open, cur_mid, cur_channel
        if force_new and assistant_open:
            close_assistant()
        if assistant_open and cur_channel != channel:
            close_assistant()
        if not assistant_open:
            cur_mid = _history_id(item_id, "assistant", line_no, raw_ts)
            assistant_open = True
            cur_channel = channel
            events.append(AssistantMsgStart(
                message_id=cur_mid, channel=channel))

    def close_assistant():
        nonlocal assistant_open, cur_mid, cur_channel
        if assistant_open and cur_mid:
            events.append(AssistantMsgEnd(
                message_id=cur_mid, channel=cur_channel))
        assistant_open = False
        cur_mid = None
        cur_channel = "unknown"

    def append_compaction(
        marker: tuple[str, float | None, str | None],
    ) -> None:
        nonlocal turn_visible
        item_id, stamp, owner = marker
        event = ProcessEvent(
            item_id=item_id,
            kind="compaction",
            phase="end",
            status="succeeded",
            turn_id=owner,
            title="压缩上下文",
        )
        if stamp is not None:
            event.ts = stamp
        events.append(event)
        turn_visible = True

    def flush_pending_compactions(target_owner: str | None) -> None:
        """Materialize only markers compatible with the now-visible owner.

        The marker freezes the task id visible when context_compacted arrived.
        A later task_started must never relabel it onto a new user turn.
        """
        if not pending_compactions:
            return
        normalized_target = _history_optional_turn_id(target_owner)
        markers = list(pending_compactions)
        pending_compactions.clear()
        for marker in markers:
            owner = marker[2]
            if (owner is not None and normalized_target is not None
                    and owner != normalized_target):
                continue
            append_compaction(marker)

    def upsert_tool_use(
        tool_id: str,
        tool: str,
        category: str,
        title: str | None,
        server: str | None,
        tool_input: dict,
        line_no: int,
        raw_ts: str,
    ) -> None:
        nonlocal turn_visible
        history_tools[tool_id] = (
            tool, category, title, server, tool_input)
        for event in reversed(events):
            if isinstance(event, ToolUse) and event.tool_use_id == tool_id:
                event.tool = tool
                event.category = category
                event.title = title
                event.server = server
                event.input = tool_input
                seen_tool_uses.add(tool_id)
                turn_visible = True
                return
        ensure_assistant(line_no, raw_ts)
        events.append(ToolUse(
            message_id=cur_mid or "",
            tool_use_id=tool_id,
            tool=tool,
            input=tool_input,
            category=category,
            title=title,
            server=server,
        ))
        seen_tool_uses.add(tool_id)
        turn_visible = True

    def upsert_tool_result(result: ToolResult) -> None:
        nonlocal turn_visible
        for index in range(len(events) - 1, -1, -1):
            event = events[index]
            if (isinstance(event, ToolResult)
                    and event.tool_use_id == result.tool_use_id):
                events[index] = result
                seen_tool_results.add(result.tool_use_id)
                turn_visible = True
                return
        events.append(result)
        seen_tool_results.add(result.tool_use_id)
        turn_visible = True

    def open_assistant_only_turn(
        reason: str | None = None,
        turn_id: str | None = None,
    ):
        """Start a visible continuation that has no user_message record.

        Goal/background continuations can begin with task_started after the
        previous user turn is already complete. The first visible assistant or
        tool item proves this is a separate assistant-only turn.
        """
        nonlocal turn_open, active_turn_id, active_msg_id
        nonlocal turn_visible, turn_text_visible, turn_final_visible
        nonlocal turn_has_user, turn_continuation_reason
        nonlocal source_continuation_available
        if turn_open:
            if (not turn_has_user and reason is not None
                    and turn_continuation_reason is None):
                turn_continuation_reason = reason
            flush_pending_compactions(active_turn_id)
            return
        pending_owner = (
            pending_compactions[0][2] if pending_compactions else None
        )
        turn_open = True
        active_turn_id = turn_id or pending_owner or pending_turn_id
        active_msg_id = None
        turn_visible = False
        turn_text_visible = False
        turn_final_visible = False
        turn_has_user = False
        turn_continuation_reason = (
            reason or ("context_compacted" if pending_compactions else None)
        )
        if (turn_continuation_reason is None
                and source_continuation_available):
            turn_continuation_reason = "authoritative_page"
        source_continuation_available = False
        flush_pending_compactions(active_turn_id)

    def materialize_pending_terminal(value) -> None:
        if not pending_compactions or turn_open:
            return
        terminal_owner = _history_optional_turn_id(value)
        marker_owner = pending_compactions[0][2]
        if (terminal_owner is not None and marker_owner is not None
                and terminal_owner != marker_owner):
            pending_compactions.clear()
            return
        open_assistant_only_turn(
            "context_compacted", marker_owner or terminal_owner)

    def emit_agent_message(
        payload: dict,
        line_no: int,
        raw_ts: str,
        item_id=None,
    ) -> None:
        nonlocal turn_visible, turn_text_visible, turn_final_visible
        open_assistant_only_turn()
        text = payload.get("message") or ""
        channel = _assistant_channel(payload.get("phase"))
        key = (
            str(active_turn_id or pending_turn_id or ""),
            channel,
            text,
        )
        if not text or key in seen_agent_messages:
            return
        seen_agent_messages.add(key)
        close_assistant()
        ensure_assistant(
            line_no,
            raw_ts,
            item_id or payload.get("id") or payload.get("message_id"),
            channel=channel,
        )
        turn_visible = True
        turn_text_visible = True
        if channel == "final":
            turn_final_visible = True
        events.append(Delta(
            message_id=cur_mid, text=text, channel=channel))
        close_assistant()

    def paired_agent_item_id(
        payload: dict,
        pending: tuple[dict, int, str],
    ) -> str | None:
        pending_payload, _line_no, _raw_ts = pending
        if (payload.get("type") != "message"
                or payload.get("role") != "assistant"
                or _assistant_channel(payload.get("phase"))
                != _assistant_channel(pending_payload.get("phase"))):
            return None
        response_text = "".join(
            item.get("text", "")
            for item in (payload.get("content") or [])
            if (isinstance(item, dict)
                and item.get("type") in {"output_text", "text"}
                and isinstance(item.get("text"), str))
        )
        clean_text = pending_payload.get("message")
        if (not isinstance(clean_text, str) or not clean_text
                or not response_text.startswith(clean_text)):
            return None
        item_id = payload.get("id")
        return (
            item_id
            if isinstance(item_id, str) and _SAFE_WIRE_ID.fullmatch(item_id)
            else None
        )

    def close_turn(
        subtype: str,
        duration_ms: int,
        is_error: bool,
        completed_ts=None,
        completed_turn_id=None,
        authoritative_boundary: bool = True,
    ):
        nonlocal turn_open, active_turn_id, active_msg_id, pending_turn_id
        nonlocal assistant_open, cur_mid, turn_visible, turn_text_visible
        nonlocal turn_final_visible, turn_has_user, turn_continuation_reason
        if not turn_open:
            return
        close_assistant()
        # Automatic continuations may replace the initially-visible turn id.
        # The message action must fork after the last internal turn that actually
        # completed this visible reply, so prefer the terminal record, then the
        # latest task_started/turn_context id, and only then the first user turn.
        terminal_turn_id = (
            completed_turn_id or pending_turn_id
            if authoritative_boundary else None
        )
        if (not isinstance(terminal_turn_id, str)
                or not _SAFE_WIRE_ID.fullmatch(terminal_turn_id)):
            terminal_turn_id = None
        te = TurnEnd(result=TurnResult(
            subtype=subtype, duration_ms=duration_ms, is_error=is_error),
            turn_id=terminal_turn_id)
        terminal_ts = completed_ts if completed_ts is not None else last_ts
        if terminal_ts is not None:
            te.ts = terminal_ts
        events.append(te)
        turn_open = False
        pending_turn_id = None
        active_turn_id = None
        active_msg_id = None
        turn_visible = False
        turn_text_visible = False
        turn_final_visible = False
        turn_has_user = False
        turn_continuation_reason = None
        pending_compactions.clear()

    try:
        f = open(path, "rb")
    except Exception:
        return [], None
    with f:
        if start_offset > 0:
            f.seek(start_offset)
        for line_no, line in _bounded_jsonl_records(f, end_offset=end_offset):
            try:
                d = json.loads(line)
            except Exception:
                if pending_agent_message is not None:
                    payload, pending_line, pending_ts = pending_agent_message
                    emit_agent_message(
                        payload, pending_line, pending_ts)
                    pending_agent_message = None
                continue
            t = d.get("type")
            p = d.get("payload") if isinstance(d.get("payload"), dict) else {}
            raw_ts = d.get("timestamp", "")
            ts = _ts(raw_ts)
            payload_type = p.get("type")

            consumed_paired_agent = False
            if pending_agent_message is not None:
                paired_id = (
                    paired_agent_item_id(p, pending_agent_message)
                    if t == "response_item" else None
                )
                payload, pending_line, pending_ts = pending_agent_message
                emit_agent_message(
                    payload, pending_line, pending_ts, paired_id)
                pending_agent_message = None
                consumed_paired_agent = paired_id is not None
            if consumed_paired_agent:
                if ts is not None:
                    last_ts = ts
                continue

            if t == "session_meta":
                continue
            elif t == "turn_context":
                if p.get("model"):
                    model = p["model"]
                context_turn_id = p.get("turn_id")
                if context_turn_id:
                    context_turn_id = str(context_turn_id)
                    # Codex can start an automatic continuation with a new turn_id
                    # but no new user_message. It is still the same visible chat
                    # turn, so only a real user_message creates a boundary.
                    pending_turn_id = context_turn_id
            elif t == "response_item" and p.get("type") == "message" and p.get("role") == "user":
                # the raw user turn carries any uploaded images (input_image, a
                # data: URI). It precedes the clean event_msg/user_message; buffer
                # them and attach to that UserMsg so images replay on reload.
                for it in (p.get("content") or []):
                    if isinstance(it, dict) and it.get("type") == "input_image":
                        img = _data_uri_to_img(it.get("image_url"))
                        if img:
                            pending_images.append(img)
            elif t == "event_msg" and payload_type == "task_started":
                next_turn_id = p.get("turn_id")
                if next_turn_id:
                    next_turn_id = str(next_turn_id)
                    marker_owner = (
                        pending_compactions[0][2]
                        if pending_compactions else None
                    )
                    if (pending_compactions
                            and marker_owner != _history_optional_turn_id(
                                next_turn_id)):
                        pending_compactions.clear()
                    pending_turn_id = next_turn_id
                task_has_user = False
            elif t == "event_msg" and payload_type == "user_message":
                msg = visible_codex_user_message(p.get("message"))
                if msg:
                    next_turn_id = p.get("turn_id") or pending_turn_id
                    if turn_open:
                        # Codex accepts another user message while the same
                        # app-server task is still running.  That is steering,
                        # not evidence that the preceding visible segment
                        # crashed.  We still need a synthetic boundary because
                        # the Web projection stores one user prompt per turn,
                        # but it must be a neutral non-error boundary.
                        steered_same_task = task_has_user and turn_has_user
                        # No terminal record proved where the previous visible
                        # reply ended. In particular, pending_turn_id now often
                        # belongs to this NEW user turn; never attach it to the
                        # synthetic boundary. Visible output is not completion
                        # evidence: only an assistant-only continuation carrying
                        # an authoritative compact/page reason may close cleanly.
                        proven_continuation = bool(
                            not turn_has_user
                            and turn_visible
                            and turn_continuation_reason in {
                                "context_compacted",
                                "authoritative_page",
                            }
                        )
                        if steered_same_task:
                            close_turn(
                                "steered", 0, False,
                                authoritative_boundary=False)
                        else:
                            close_turn(
                                "success" if proven_continuation else "error",
                                0, not proven_continuation,
                                authoritative_boundary=False)
                    active_turn_id = str(next_turn_id) if next_turn_id else None
                    pending_turn_id = active_turn_id
                    if task_has_user:
                        # A steered message inside the same app-server task has
                        # no fresh turn id. Reusing active_turn_id would make the
                        # reducer drop it as a duplicate of the first user row.
                        uid = _fallback_history_id(
                            path,
                            "user",
                            line_no,
                            raw_ts,
                            type(p.get("turn_id")).__name__,
                        )
                    else:
                        uid = _history_id(
                            active_turn_id, "user", line_no, raw_ts)
                    active_msg_id = uid
                    um = UserMsg(msg_id=uid, prompt=msg)
                    if pending_images:
                        um.images = pending_images
                    if ts is not None:
                        um.ts = ts
                    events.append(um)
                    turn_open = True
                    turn_has_user = True
                    turn_continuation_reason = None
                    source_continuation_available = False
                    flush_pending_compactions(active_turn_id)
                    task_has_user = True
                pending_images = []   # consume (per user turn)
            elif (t == "response_item"
                  and payload_type in {"function_call", "custom_tool_call"}):
                open_assistant_only_turn()
                tool_id = _history_id(
                    p.get("call_id") or p.get("id"),
                    "tool", line_no, raw_ts)
                arguments = (p.get("arguments") if payload_type == "function_call"
                             else p.get("input"))
                hist_input = _hist_tool_input(arguments, p.get("name"))
                plan_event = _history_plan_event(
                    p.get("name"), hist_input,
                    _history_optional_turn_id(
                        active_turn_id or pending_turn_id),
                    _history_id, tool_id, line_no, raw_ts,
                )
                if plan_event is not None:
                    plan_tool_ids.add(tool_id)
                    seen_tool_uses.add(tool_id)
                    turn_visible = True
                    events.append(plan_event)
                else:
                    ensure_assistant(line_no, raw_ts)
                    tool, category, title, server = _hist_tool_presentation(
                        p.get("name"), hist_input)
                    history_tools[tool_id] = (
                        tool, category, title, server, hist_input)
                    if tool_id not in seen_tool_uses:
                        seen_tool_uses.add(tool_id)
                        turn_visible = True
                        events.append(ToolUse(
                            message_id=cur_mid or "",
                            tool_use_id=tool_id,
                            tool=tool,
                            input=hist_input,
                            category=category,
                            title=title,
                            server=server,
                        ))
            elif (t == "response_item"
                  and payload_type in {
                      "function_call_output", "custom_tool_call_output"}):
                open_assistant_only_turn()
                tool_id = _history_id(
                    p.get("call_id"), "tool", line_no, raw_ts)
                tool_meta = history_tools.get(
                    tool_id, ("tool", "tool", None, None, {}))
                if tool_id in plan_tool_ids:
                    seen_tool_results.add(tool_id)
                else:
                    if tool_id not in seen_tool_uses:
                        ensure_assistant(line_no, raw_ts)
                        tool, category, title, server, hist_input = tool_meta
                        seen_tool_uses.add(tool_id)
                        events.append(ToolUse(
                            message_id=cur_mid or "", tool_use_id=tool_id,
                            tool=tool, input=hist_input, category=category,
                            title=title, server=server))
                    if tool_id not in seen_tool_results:
                        seen_tool_results.add(tool_id)
                        turn_visible = True
                        category = tool_meta[1]
                        raw_output = p.get("output")
                        structured_error = False
                        if category in {"mcp", "server_tool"}:
                            raw_output, structured_error = (
                                _history_structured_tool_output(raw_output))
                        output, was_truncated = bounded_text(
                            raw_output, tool_result_max)
                        exit_code = _history_exit_code(output)
                        is_error = structured_error or _exit_is_error(output)
                        events.append(ToolResult(
                            tool_use_id=tool_id,
                            content=output,
                            is_error=is_error,
                            truncated=True if was_truncated else None,
                            status="failed" if is_error else "succeeded",
                            exit_code=exit_code,
                        ))
            elif t == "event_msg" and payload_type == "exec_command_end":
                open_assistant_only_turn()
                tool_id = _history_id(
                    p.get("call_id"), "tool", line_no, raw_ts)
                if tool_id not in seen_authoritative_results:
                    seen_authoritative_results.add(tool_id)
                    command = _legacy_command_text(p.get("command"))
                    command_input = bounded_tool_input({
                        "command": command,
                        "cwd": p.get("cwd"),
                        "actions": p.get("parsed_cmd"),
                        "source": p.get("source"),
                        "process_id": p.get("process_id"),
                    }, 64 * 1024)
                    title = _legacy_command_title(p.get("parsed_cmd"))
                    upsert_tool_use(
                        tool_id, "shell", "command", title, None,
                        command_input, line_no, raw_ts)
                    output, truncated = bounded_text(
                        p.get("aggregated_output")
                        or p.get("formatted_output")
                        or p.get("stdout")
                        or p.get("stderr")
                        or "",
                        tool_result_max,
                    )
                    exit_code = _nonnegative_or_signed_int(p.get("exit_code"))
                    status = _process_status(p.get("status"))
                    if exit_code is not None and exit_code != 0:
                        status = "failed"
                    elif status in {"unknown", "running", "pending"}:
                        status = "succeeded"
                    upsert_tool_result(ToolResult(
                        tool_use_id=tool_id,
                        content=output,
                        is_error=(status in {
                            "failed", "declined", "cancelled", "interrupted"
                        }),
                        truncated=True if truncated else None,
                        status=status,
                        exit_code=exit_code,
                        duration_ms=_legacy_duration_ms(p.get("duration")),
                    ))
            elif t == "event_msg" and payload_type == "mcp_tool_call_end":
                open_assistant_only_turn()
                tool_id = _history_id(
                    p.get("call_id"), "tool", line_no, raw_ts)
                if tool_id not in seen_authoritative_results:
                    seen_authoritative_results.add(tool_id)
                    invocation = (p.get("invocation")
                                  if isinstance(p.get("invocation"), dict)
                                  else {})
                    server = str(invocation.get("server") or "MCP")[:1024]
                    tool = str(invocation.get("tool") or "mcp")[:1024]
                    arguments = invocation.get("arguments")
                    tool_input = bounded_tool_input(
                        _redact_credentials(
                            arguments if isinstance(arguments, dict)
                            else {"arguments": arguments}),
                        64 * 1024,
                    )
                    upsert_tool_use(
                        tool_id, tool, "mcp", f"{server} · {tool}"[:1024],
                        server, tool_input, line_no, raw_ts)
                    content, is_error = _legacy_mcp_result(p.get("result"))
                    output, truncated = bounded_text(content, tool_result_max)
                    upsert_tool_result(ToolResult(
                        tool_use_id=tool_id,
                        content=output,
                        is_error=is_error,
                        truncated=True if truncated else None,
                        status="failed" if is_error else "succeeded",
                        duration_ms=_legacy_duration_ms(p.get("duration")),
                    ))
            elif t == "event_msg" and payload_type == "item_completed":
                item = p.get("item") if isinstance(p.get("item"), dict) else {}
                if str(item.get("type") or "").lower() == "plan":
                    open_assistant_only_turn()
                    item_id = _history_id(
                        item.get("id"), "plan-detail", line_no, raw_ts)
                    if item_id not in seen_process_items:
                        seen_process_items.add(item_id)
                        detail, truncated = bounded_text(
                            item.get("text"), 256 * 1024)
                        events.append(ProcessEvent(
                            item_id=item_id,
                            kind="plan",
                            phase="end",
                            status="succeeded",
                            turn_id=_history_optional_turn_id(
                                p.get("turn_id") or active_turn_id
                                or pending_turn_id),
                            title="计划",
                            detail=detail or None,
                            truncated=True if truncated else None,
                        ))
                        turn_visible = True
            elif t == "response_item" and payload_type == "reasoning":
                summary = _reasoning_summary(p)
                key = (str(active_turn_id or pending_turn_id or ""), summary)
                if summary and key not in seen_reasoning:
                    seen_reasoning.add(key)
                    open_assistant_only_turn()
                    events.append(ProcessEvent(
                        item_id=_history_id(
                            p.get("id"), "reasoning", line_no, raw_ts),
                        kind="reasoning",
                        phase="end",
                        status="succeeded",
                        turn_id=_history_optional_turn_id(
                            active_turn_id or pending_turn_id),
                        title="思考",
                        summary=summary,
                    ))
            elif t == "event_msg" and payload_type == "agent_reasoning":
                summary, _ = bounded_text(p.get("text"), 64 * 1024)
                key = (str(active_turn_id or pending_turn_id or ""), summary)
                if summary and key not in seen_reasoning:
                    seen_reasoning.add(key)
                    open_assistant_only_turn()
                    events.append(ProcessEvent(
                        item_id=_history_id(
                            p.get("id") or p.get("event_id"),
                            "reasoning", line_no, raw_ts),
                        kind="reasoning",
                        phase="end",
                        status="succeeded",
                        turn_id=_history_optional_turn_id(
                            active_turn_id or pending_turn_id),
                        title="思考",
                        summary=summary,
                    ))
            elif t == "event_msg" and payload_type == "agent_message":
                pending_agent_message = (p, line_no, raw_ts)
            elif t == "event_msg" and payload_type == "patch_apply_end":
                open_assistant_only_turn()
                ensure_assistant(line_no, raw_ts)
                tool_id = _history_id(
                    p.get("call_id"), "tool", line_no, raw_ts)
                descriptors = _change_descriptors(p.get("changes"))
                paths = _descriptor_paths(descriptors)
                if tool_id not in seen_tool_uses:
                    seen_tool_uses.add(tool_id)
                    turn_visible = True
                    events.append(ToolUse(
                        message_id=cur_mid or "",
                        tool_use_id=tool_id,
                        tool="apply_patch",
                        input=bounded_tool_input({
                            "changes": descriptors,
                            "file_paths": paths,
                        }, 64 * 1024),
                        category="file",
                        title=_file_summary(paths, "running"),
                    ))
                if tool_id not in seen_tool_results:
                    seen_tool_results.add(tool_id)
                    turn_visible = True
                    success = p.get("success") is not False
                    diff, diff_truncated = bounded_text(
                        _changes_diff(p.get("changes")), 2 * 1024 * 1024)
                    output, output_truncated = bounded_text(
                        p.get("stdout") or p.get("stderr") or "",
                        tool_result_max)
                    events.append(ToolResult(
                        tool_use_id=tool_id,
                        content=output,
                        is_error=not success,
                        truncated=(True if diff_truncated or output_truncated
                                   else None),
                        status="succeeded" if success else "failed",
                        summary=_file_summary(
                            paths, "succeeded" if success else "failed"),
                        diff=diff or None,
                    ))
            elif t == "event_msg" and payload_type == "web_search_end":
                open_assistant_only_turn()
                ensure_assistant(line_no, raw_ts)
                tool_id = _history_id(
                    p.get("call_id"), "tool", line_no, raw_ts)
                query, _ = bounded_text(p.get("query"), 16 * 1024)
                if tool_id not in seen_tool_uses:
                    seen_tool_uses.add(tool_id)
                    turn_visible = True
                    events.append(ToolUse(
                        message_id=cur_mid or "",
                        tool_use_id=tool_id,
                        tool="webSearch",
                        input=bounded_tool_input({
                            "query": query, "action": p.get("action"),
                        }, 64 * 1024),
                        category="web_search",
                        title=(f"搜索 {query}" if query else "搜索网页")[:1024],
                    ))
                if tool_id not in seen_tool_results:
                    seen_tool_results.add(tool_id)
                    events.append(ToolResult(
                        tool_use_id=tool_id,
                        content="",
                        is_error=False,
                        status="succeeded",
                    ))
            elif t == "event_msg" and payload_type == "sub_agent_activity":
                open_assistant_only_turn()
                item = {
                    "id": p.get("event_id"),
                    "kind": p.get("kind"),
                    "agentThreadId": p.get("agent_thread_id"),
                    "agentPath": p.get("agent_path"),
                }
                events.append(_subagent_event(
                    item,
                    _history_optional_turn_id(
                        active_turn_id or pending_turn_id),
                    completed=True,
                ))
                turn_visible = True
            elif t == "event_msg" and payload_type == "context_compacted":
                marker = (
                    _history_id(
                        p.get("id"), "compaction", line_no, raw_ts),
                    ts,
                    _history_optional_turn_id(
                        active_turn_id or pending_turn_id),
                )
                if turn_open:
                    open_assistant_only_turn("context_compacted")
                    append_compaction(marker)
                else:
                    pending_compactions.append(marker)
                    if (len(pending_compactions)
                            > _MAX_PENDING_HISTORY_COMPACTIONS):
                        del pending_compactions[0]
            elif t == "event_msg" and payload_type == "task_complete":
                materialize_pending_terminal(p.get("turn_id"))
                last = p.get("last_agent_message")
                if (not turn_open and isinstance(last, str) and last):
                    open_assistant_only_turn()
                if turn_open:
                    turn_key = str(active_turn_id or pending_turn_id or "")
                    last_already_visible = any(
                        key[0] == turn_key and key[2] == last
                        for key in seen_agent_messages)
                    if (not turn_final_visible and isinstance(last, str) and last
                            and not last_already_visible):
                        close_assistant()
                        ensure_assistant(
                            line_no, raw_ts, channel="final", force_new=True)
                        events.append(Delta(
                            message_id=cur_mid, text=last, channel="final"))
                        close_assistant()
                        seen_agent_messages.add((turn_key, "final", last))
                        turn_visible = True
                        turn_text_visible = True
                        turn_final_visible = True
                    if turn_visible:
                        close_turn("success", _duration(p), False,
                                   _completed_ts(p, ts), p.get("turn_id"))
                    else:
                        events.append(Error(
                            code=ERR_CC_CRASH,
                            message=_EMPTY_COMPLETED_MESSAGE,
                            msg_id=active_msg_id,
                        ))
                        close_turn("error", _duration(p), True,
                                   _completed_ts(p, ts), p.get("turn_id"))
            elif t == "event_msg" and payload_type == "turn_aborted":
                materialize_pending_terminal(p.get("turn_id"))
                if turn_open:
                    # Current Codex rollouts can omit ``reason`` for an
                    # intentional interrupt. Explicit failure reasons remain
                    # errors; a bare turn_aborted is an interruption.
                    reason = str(p.get("reason") or "").lower()
                    interrupted = reason not in {"error", "failed", "crash"}
                    close_turn(
                        "error_during_execution" if interrupted else "error",
                        _duration(p), True, _completed_ts(p, ts),
                        p.get("turn_id"))
            elif t == "event_msg" and payload_type in {
                    "task_failed", "turn_failed", "task_error"}:
                materialize_pending_terminal(p.get("turn_id"))
                if turn_open:
                    close_turn("error", _duration(p), True,
                               _completed_ts(p, ts), p.get("turn_id"))
            # session_meta / world_state / token_count / private reasoning : skipped
            if ts is not None:
                last_ts = ts
    if pending_agent_message is not None and not snapshot_in_progress:
        payload, pending_line, pending_ts = pending_agent_message
        emit_agent_message(payload, pending_line, pending_ts)
    # A file can be read while Codex is still appending the current turn. Close
    # only its current text block; deliberately omit TurnEnd so the reducer keeps
    # the turn not-done instead of fabricating a completed status.
    close_assistant()
    return events, model


def _hist_tool_name(name) -> str:
    if name in ("exec", "exec_command", "shell", "local_shell"):
        return "shell"
    if name in ("apply_patch",):
        return "apply_patch"
    return name or "tool"


def _history_plan_event(
    name,
    tool_input: dict,
    turn_id: str | None,
    id_builder,
    tool_id: str,
    line_no: int,
    raw_ts: str,
):
    normalized = re.sub(r"[^a-z0-9]", "", str(name or "").lower())
    if not normalized.endswith("updateplan"):
        return None
    raw_plan = tool_input.get("plan")
    if not isinstance(raw_plan, list):
        return None
    plan = []
    for entry in raw_plan[:128]:
        if not isinstance(entry, dict):
            continue
        step, _ = bounded_text(entry.get("step"), 16 * 1024)
        if not step:
            continue
        plan.append({
            "step": step,
            "status": _plan_status(entry.get("status")),
        })
    explanation, _ = bounded_text(tool_input.get("explanation"), 64 * 1024)
    identity = f"plan:{turn_id or tool_id}"
    return TurnPlan(
        item_id=id_builder(identity, "plan", line_no, raw_ts),
        turn_id=turn_id,
        explanation=explanation or None,
        plan=plan,
    )


def _hist_tool_presentation(
    name, tool_input: dict,
) -> tuple[str, str, str | None, str | None]:
    raw_name = str(name or "tool")
    tool = _hist_tool_name(raw_name)
    if tool == "shell":
        return tool, "command", "运行命令", None
    if tool == "apply_patch":
        return tool, "file", "修改文件", None
    if raw_name in {"web_search", "webSearch", "search_web"}:
        query = tool_input.get("query")
        title = f"搜索 {query}" if query else "搜索网页"
        return "webSearch", "web_search", title[:1024], None
    if raw_name in {
        "spawn_agent", "spawnAgent", "send_input", "sendInput",
        "resume_agent", "resumeAgent", "wait_agent", "wait",
        "close_agent", "closeAgent",
    }:
        return raw_name[:1024], "agent", "协作代理", None
    if raw_name.startswith("mcp__"):
        parts = raw_name.split("__", 2)
        server = parts[1] if len(parts) > 1 and parts[1] else "MCP"
        mcp_tool = parts[2] if len(parts) > 2 and parts[2] else raw_name
        return mcp_tool[:1024], "mcp", f"{server} · {mcp_tool}"[:1024], server[:1024]
    return tool[:1024], "tool", raw_name[:1024], None


def _hist_tool_input(arguments, name=None) -> dict:
    try:
        a = json.loads(arguments) if isinstance(arguments, str) else (arguments or {})
    except Exception:
        if isinstance(arguments, str):
            mapped = _hist_tool_name(name)
            key = "command" if mapped == "shell" else (
                "patch" if mapped == "apply_patch" else "input")
            return bounded_tool_input({key: arguments}, 64 * 1024)
        a = {}
    if not isinstance(a, dict):
        return bounded_tool_input(
            {"args": _redact_credentials(a)}, 64 * 1024)
    out: dict = {}
    if a.get("cmd") is not None:
        out["command"] = a["cmd"]
    if a.get("workdir") is not None:
        out["cwd"] = a["workdir"]
    for k, v in a.items():
        if k not in ("cmd", "workdir", "yield_time_ms"):
            out[k] = v
    return bounded_tool_input(_redact_credentials(out), 64 * 1024)


def _history_structured_tool_output(output) -> tuple[object, bool]:
    """Allow-list replayable MCP/dynamic result fields from rollout output."""
    parsed = output
    if isinstance(output, str):
        try:
            parsed = json.loads(output)
        except (TypeError, ValueError):
            # Opaque strings can embed serialized `_meta` or credentials without
            # field boundaries, so never replay them as trusted MCP history.
            return "MCP 工具调用已完成（历史结果格式不可解析）", False
    if isinstance(parsed, list):
        return {"content": _redact_credentials(parsed)}, False
    if not isinstance(parsed, dict):
        return "MCP 工具调用已完成", False

    candidate = parsed.get("result")
    if not isinstance(candidate, dict):
        candidate = parsed
    error = parsed.get("error")
    if error is None and candidate is not parsed:
        error = candidate.get("error")
    if error:
        if isinstance(error, dict):
            message = error.get("message")
        else:
            message = str(error)
        safe_error, _ = bounded_text(message or "MCP tool call failed", 64 * 1024)
        return safe_error, True

    safe = {}
    aliases = (
        ("content", "content"),
        ("structuredContent", "structuredContent"),
        ("structured_content", "structuredContent"),
        ("contentItems", "content"),
    )
    for source, target in aliases:
        if source in candidate and target not in safe:
            safe[target] = _redact_credentials(candidate[source])
    failed = parsed.get("success") is False or str(
        parsed.get("status") or "").lower() in {"failed", "error"}
    return (safe or "MCP 工具调用已完成"), failed


def _legacy_command_text(command) -> str:
    """Normalize persisted ``exec_command_end.command`` into display text."""
    if isinstance(command, str):
        text, _ = bounded_text(command, 256 * 1024)
        return text
    if isinstance(command, (list, tuple)):
        argv = [str(part) for part in list(command)[:256]]
        try:
            text = shlex.join(argv)
        except (TypeError, ValueError):
            text = " ".join(argv)
        text, _ = bounded_text(text, 256 * 1024)
        return text
    text, _ = bounded_text(command, 256 * 1024)
    return text


def _legacy_command_title(parsed_command) -> str:
    """Give old rollout command records the same semantic title as live items."""
    actions = parsed_command if isinstance(parsed_command, list) else []
    first = actions[0] if actions and isinstance(actions[0], dict) else {}
    action_type = re.sub(
        r"[^a-z0-9]", "", str(first.get("type") or "").lower())
    if action_type == "read":
        path = first.get("path") or first.get("name")
        return (f"读取 {path}" if path else "读取文件")[:1024]
    if action_type in {"list", "listfiles"}:
        path = first.get("path")
        return (f"列出 {path}" if path else "列出文件")[:1024]
    if action_type in {"search", "grep"}:
        query = first.get("query") or first.get("pattern")
        return (f"搜索 {query}" if query else "搜索内容")[:1024]
    return "运行命令"


def _legacy_duration_ms(duration) -> int | None:
    """Convert persisted protobuf-style ``{secs, nanos}`` durations."""
    if not isinstance(duration, dict):
        return _duration_ms(duration)
    secs = duration.get("secs")
    nanos = duration.get("nanos")
    if isinstance(secs, bool) or isinstance(nanos, bool):
        return None
    try:
        milliseconds = int(secs or 0) * 1000 + int(nanos or 0) // 1_000_000
    except (TypeError, ValueError, OverflowError):
        return None
    return milliseconds if milliseconds >= 0 else None


def _legacy_mcp_result(result) -> tuple[object, bool]:
    """Decode persisted Rust ``Result`` while excluding server-private metadata."""
    if not isinstance(result, dict):
        return "MCP 工具调用已完成", False
    if "Err" in result:
        # Err is an opaque provider string and may itself contain connector
        # credentials. Preserve failure semantics without replaying it verbatim.
        return "MCP 工具调用失败", True
    value = result.get("Ok")
    if not isinstance(value, dict):
        return "MCP 工具调用已完成", False
    safe = _redact_credentials({
        key: value.get(key) for key in ("content", "structuredContent")
        if value.get(key) is not None
    })
    return safe or "MCP 工具调用已完成", bool(value.get("isError"))


def _exit_is_error(output: str) -> bool:
    code = _history_exit_code(output)
    return code is not None and code != 0


def _history_exit_code(output: str) -> int | None:
    match = re.search(
        r"\b(?:process\s+)?(?:exited|exit)\s+(?:with\s+)?code\s*[:=]?\s*(-?\d+)",
        output or "",
        re.IGNORECASE,
    )
    return int(match.group(1)) if match else None


def _history_optional_turn_id(value) -> str | None:
    return _optional_wire_id(value, "turn")


def _data_uri_to_img(url) -> dict | None:
    """`data:image/png;base64,XXXX` -> {media_type, data} (the web's QueryImg shape)."""
    if not isinstance(url, str) or not url.startswith("data:"):
        return None
    try:
        head, data = url.split(",", 1)
        mt = head[5:].split(";")[0] or "image/png"
        return {"media_type": mt, "data": data}
    except Exception:
        return None
