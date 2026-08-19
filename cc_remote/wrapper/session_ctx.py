"""Per-session state for the multi-session wrapper pool.

A SessionContext owns exactly one cc subprocess (via SdkHandle) plus its
conversation state: ring buffer, seq counter, state machine, turn task,
translator, pending ask_user futures, and an emit lock. The wrapper machine
holds a pool of these keyed by session id; switching the viewed session is a
focus change (no disconnect), so background turns keep streaming.

The drain contract (one async-for per turn, running to the terminal
ResultMessage before accepting another query) holds naturally per ctx: each
turn task is spawned on its own ctx with its own SDK subprocess.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Optional

from cc_remote.protocol import State
from cc_remote.wrapper.ringbuffer import RingBuffer
from cc_remote.wrapper.sdk import SdkHandle
from cc_remote.wrapper.stream import StreamTranslator


@dataclass
class SessionContext:
    # None until the first ResultMessage/init SystemMessage captures the real id
    # (a brand-new session). A resumed session knows its id at spawn time.
    session_id: Optional[str]
    sdk: SdkHandle                     # engine control adapter (SDK/app-server/broker)
    buffer: RingBuffer                 # per-session ring (own seq namespace)
    cwd: str                           # resume requires cwd to match the jsonl path
    # Pool key = the client-facing routing identity: the real sid once known,
    # else a temp `tmp-<uuid>` for a brand-new session. Kept in sync with the
    # machine's `sessions` dict key so every emit can stamp `sid` WITHOUT an
    # O(n) reverse lookup — and so a pre-capture new session's live frames route
    # deterministically (never leak into whatever is currently focused).
    key: Optional[str] = None
    seq: int = 0                       # per-session monotonic counter
    state: State = "idle"
    engine: str = "claude"             # "claude" (SdkHandle) | "codex" (CodexHandle)
    # Product-space identity. Work sessions are native engine sessions whose
    # cwd and metadata are owned by cc-remote's private Work registry.
    space: str = "code"
    work_id: Optional[str] = None
    # Work's new-session startup zero point. It is persisted by WorkRegistry and
    # subtracted only for the Work UI's growth gauge; the raw engine total stays
    # authoritative for capacity and compaction.
    work_context_baseline_tokens: Optional[int] = None
    # Only a brand-new Work record may establish a baseline. Migrated/resumed
    # rows with no baseline must keep showing the authoritative raw total rather
    # than silently reclassifying their existing conversation as engine cost.
    work_context_baseline_pending: bool = False
    turn_task: Optional[asyncio.Task] = None
    # Correlates asynchronous turn crashes/drain failures with the optimistic
    # client turn. Control-command errors must never terminate an unrelated turn.
    active_msg_id: Optional[str] = None
    # Interrupt must wake a consumer that is already blocked in queue.get().  The
    # absolute monotonic deadline prevents each subsequent queue item from
    # restarting the drain timeout.
    interrupt_event: asyncio.Event = field(default_factory=asyncio.Event)
    interrupt_deadline: Optional[float] = None
    translator: Optional[StreamTranslator] = None
    # Claude's SDK response queue outlives one ResultMessage. Background task
    # updates can therefore be consumed by the next turn's translator; preserve
    # their original turn/title by stable item id across translator instances.
    claude_item_turns: dict[str, str] = field(default_factory=dict)
    claude_item_titles: dict[str, str] = field(default_factory=dict)
    claude_item_meta: dict[str, tuple[str, str | None]] = field(default_factory=dict)
    # /btw ephemeral fork: a throwaway side-session forked from `parent_sid` that
    # inherits its context. Never persisted, excluded from the session list, and
    # discarded on close. Its turns reuse the normal _run_turn path.
    btw: bool = False
    parent_sid: Optional[str] = None
    owner_client_id: Optional[str] = None
    # cc fork_session persists a transcript under a new id (unlike codex's
    # ephemeral fork); capture it here so close_btw can hard-delete it.
    btw_real_id: Optional[str] = None
    announced_model: Optional[str] = None
    announced_effort: Optional[str] = None
    announced_perm: Optional[str] = None
    announced_collaboration_mode: Optional[str] = None
    # Goal state is restored silently.  The remote UI only reveals it after the
    # user explicitly invokes /goal (get/set); this avoids a permanent empty
    # panel above the composer merely because a Claude/Codex session was opened.
    goal_visible: bool = False
    # app-server goal loops/automatic continuations can start without the
    # machine calling query(). Their lifecycle is delivered separately from the
    # managed turn stream so the session remains single-writer and interruptible.
    codex_spontaneous_turn_id: Optional[str] = None
    codex_spontaneous_task: Optional[asyncio.Task] = None
    # Remote-owned Git checkpoint journal for Codex Code turns. It is created
    # lazily only in Git workspaces; Work and Claude use their own restore paths.
    codex_checkpoint: Any = None
    codex_checkpoint_turn_id: Optional[str] = None
    # ``turn/start`` acceptance is separate from the pre-turn filesystem
    # capture. A failed RPC must abort the active snapshot without consuming a
    # native-turn slot, while an accepted turn whose capture failed needs an
    # unavailable tombstone to keep count-based rollback aligned.
    codex_checkpoint_ready: bool = False
    codex_checkpoint_accepted: bool = False
    codex_checkpoint_unavailable_reason: Optional[str] = None
    # ---- external-write mirroring (a native `claude`/`codex` in the user's
    # terminal owns this session and is appending to its transcript) ----
    # epoch of the last append this wrapper did NOT make. Recent => the session is
    # externally owned: clients show it read-only and get mirrored History frames.
    external_ts: float = 0.0
    # an external append happened, so our resumed subprocess now holds a STALE
    # context. Reload (force_reconnect with resume) before running another turn,
    # else we'd continue from a conversation that has since moved on.
    needs_reload: bool = False
    # True only while this wrapper's Claude SDK child is expected to append to
    # the transcript. ``state=running`` starts earlier, during reconnect and
    # final ownership checks, so it is not sufficient write attribution.
    claude_write_active: bool = False
    # Authoritative v15 ownership/control projection.  These fields belong to
    # the resident session rather than to any browser so reconnects and history
    # refreshes cannot resurrect a stale read-only banner.  `control_revision`
    # advances whenever one of the public values changes.
    control_mode: str = "remote"
    write_state: str = "writable"
    terminal_attached: bool = False
    control_reason: Optional[str] = None
    control_can_takeover: bool = False
    control_revision: int = 0
    # Machine-local sid whose monotonic control epoch has been bound to this
    # resident context.  A newly-created context starts unbound so reopening an
    # evicted sid advances beyond the browser's last same-generation revision.
    control_revision_key: Optional[str] = None
    # A Claude session launched through the explicit local `claude-remote`
    # broker keeps the official TUI as its sole process owner.  The wrapper
    # stores only the broker generation needed to reject a stale PID/socket
    # record; it never owns or kills that process on an ordinary disconnect.
    claude_broker_generation: Optional[str] = None
    pending_asks: dict = field(default_factory=dict)
    # A Remote model chip can trigger Claude TUI's cached-history confirmation.
    # Track its question separately so a newer model choice can supersede the
    # old one without leaving an unreachable Future behind.
    pending_model_ask_id: Optional[str] = None
    # A file outside ``cwd`` is never previewable merely because the browser
    # knows its path.  The only exception is an exact path that this session's
    # built-in file-mutation tool has completed successfully. Keep the pending
    # tool/paths binding separate so a failed or declined tool call grants no
    # read capability.  Both maps are bounded by WrapperMachine.
    preview_write_candidates: dict[str, tuple[str, ...]] = field(default_factory=dict)
    preview_external_paths: dict[str, None] = field(default_factory=dict)
    emit_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # Serialize the tiny "final preflight check -> query accepted by engine"
    # window against interrupt().  Reconnects happen before this lock; once held,
    # interrupt either marks the event before query (so the turn aborts) or waits
    # until query() has returned and can interrupt the newly-created live turn.
    launch_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def next_seq(self) -> int:
        self.seq += 1
        return self.seq
