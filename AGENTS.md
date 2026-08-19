# AGENTS.md — cc-remote

Guidance for Codex (and human contributors) working in this repo. User-facing setup/run docs live in [README.md](README.md) / [README_en.md](README_en.md).

## What this is
Self-hosted remote control for Claude Code and Codex: a phone/browser drives a
local `claude` or `codex` session through a WebSocket relay. Two independent links:
- **model link** — the local CLI → whatever its own settings/authentication point
  at. cc-remote never touches model credentials or the model API.
- **control link** (this repo) — client ⇄ relay ⇄ wrapper ⇄ Claude Agent SDK /
  Codex app-server. Native CLI ownership is detected and mirrored separately.

## Critical constraints / traps
- **Drain footgun**: after `ClaudeSDKClient.interrupt()`, the SDK does NOT kill
  the session — the current turn's stream still emits a terminal
  `ResultMessage(subtype="error_during_execution")`. You MUST keep consuming
  `receive_response()` until that ResultMessage before the next `query()`, else
  stale deltas from the interrupted turn bleed into the new turn. The wrapper
  handles this structurally: one `async for` per turn runs through the interrupt
  to the terminal ResultMessage; state only returns to `idle` (and the next
  query is only accepted) after that break. Reject-while-busy prevents a second
  query racing the drain.
- **cwd must match resume**: a session's jsonl lives at
  `~/.claude/projects/<cwd-with-/-as->/<uuid>.jsonl`. `ClaudeAgentOptions.cwd`
  MUST equal the original session's cwd or `resume` can't find it.
- **SDK pinned to `claude-agent-sdk==0.2.119`**: message-type shapes and the
  interrupt/drain contract can shift between minor versions. Re-run the
  interrupt+drain verification after any upgrade (`SdkHandle.preflight()` guards
  the major/minor at startup).
- **`include_partial_messages`** is a `ClaudeAgentOptions` field (set at
  construction, not on `query()`). Streaming events arrive as `StreamEvent`
  (`.event` = raw Anthropic API stream-event dict) — NOT
  `SDKPartialAssistantMessage` (doesn't exist in 0.2.119). Extract
  `content_block_delta` → `delta.text` from `StreamEvent.event`.
- **tool_use is batched, not streamed**: emit one `tool_use` event from the
  assembled `AssistantMessage` (full `input`), never as JSON-fragment deltas.
  Text deltas still stream live via `StreamEvent`.
- **Claude only — don't set `setting_sources=[]`**: we WANT
  `~/.claude/settings.json` loaded so Claude inherits the model link
  (`ANTHROPIC_BASE_URL`), model id, and
  `bypassPermissions`. Note: settings.json's `env` block overrides the process
  env, so redirecting the model backend from cc-remote is not possible — it's
  the user's `settings.json` that decides.
- **Auth is URL-secret-free**: the wrapper uses `Authorization: Bearer <token>`
  at WS upgrade. Web clients POST `LOGIN_PASSWORD` to `/api/login` and receive a
  short-lived HttpOnly/SameSite cookie; `/ws` also enforces exact
  `PUBLIC_ORIGIN`. Never put tokens in URLs or protocol message bodies; logging
  redacts token/password fields.
- **Protocol version gate**: `PROTOCOL_VERSION` in both `protocol.py` and
  `web/src/protocol.ts`. `deserialize` hard-rejects a version mismatch, and
  `_Base` is `extra="forbid"`, so ANY protocol change must be deployed to all
  three tiers together (wrapper + relay + web) and the relay restarted — the
  relay imports `protocol.py` and drops frames it can't parse.
- **Device scope is an authorization boundary**: one relay can serve multiple
  wrappers. Every browser command, event, push subscription, and pairing token
  is scoped by `machine_id`; a credential for one enrolled device must never be
  accepted for another. Keep `cc_remote/device.py`, `relay/devices.py`, relay
  routing, and the Web device selector aligned when this contract changes.
- **Multi-session routing key**: the wrapper runs a POOL of resident sessions
  (`WrapperMachine.sessions: dict[key, SessionContext]`, cap
  `MAX_CONCURRENT_SESSIONS`). `ctx.key` is the routing identity = the real cc sid
  once known, else `tmp-<uuid>`. Every emit stamps `sid = ctx.session_id or
  ctx.key`, so a brand-new session's pre-capture frames route deterministically
  (never leak into the focused runtime). Keep `ctx.key` in sync with the pool
  dict key on every re-key.
- **Focus vs re-key (don't conflate)**: switching the viewed session is
  `SessionFocus` (focus only, no disconnect — the previous session keeps
  streaming). A new session capturing its real id mid-turn is `SessionRekey`
  (rename tmp-key→sid), which moves focus ONLY if the client was already viewing
  the temp key. Emitting SessionFocus on id-capture = focus-steal by background
  sessions.
- **External ownership is engine-specific**: a native Claude CLI owns its
  transcript and is mirrored read-only until it exits or the user explicitly
  takes over. Codex Code sessions use the official app-server; shared-daemon
  CLI activity and private Codex App activity are different ownership sources
  and must not be collapsed into one "external process" heuristic. Ordinary
  shared sessions stay on the daemon; only the guarded oversized-resume path may
  select a newer official private app-server for compatibility.
- **History = local projection + materialized summary pages; reconnect = live-tail replay**
  (protocol v19): IndexedDB paints the browser's last projection before network
  validation. `GetHistory(detail="summary")` returns a small canonical turn page
  (newest four, then `before`/`limit` pagination), while the wrapper's rebuildable
  SQLite index avoids retranslating unchanged transcript/rollout bytes. Heavy
  tools, reasoning, process output and oversized final text stay local until
  `GetTurnDetail` expands that exact turn. The relay remains stateless. A fresh
  hello sends lightweight resident `Snapshot`s; reconnect cursors replay only
  the bounded missing live tail. Source fingerprints invalidate appended pages,
  and rollback explicitly invalidates both server and browser projections. These
  reads never spawn/resume an engine or create a model turn.
- **Token-aware residency**: resuming an evicted Claude SDK session may rebuild
  a cold prompt cache, so it only happens on first spawn / re-focus after
  eviction; raising the cap trades RAM for fewer cold re-sends. Codex context is
  owned by the official app-server: cc-remote must page history and use native
  resume/compaction state, never re-upload a whole rollout. Browsing history is
  transcript/rollout I/O and must not create a model turn.

## Module map
- `cc_remote/protocol.py` — pydantic wire schema; all modules depend on it.
  `serialize`/`deserialize` with `v` check; `is_downstream` for seq/buffer.
  Control frames: `SessionFocus` / `SessionRekey` / `GetHistory` / `History` /
  `GetTurnDetail` / `TurnDetail` / `SessionInfo.state`.
- `cc_remote/config.py` — env-driven config (`RelayConfig`, `WrapperConfig`).
- `cc_remote/device.py` — device pairing CLI and persisted per-machine wrapper
  credential.
- `cc_remote/log.py` — JSON logging with token redaction; use `logger("...")`.
- `cc_remote/wrapper/` — `sdk.py` / `stream.py` and `claude_*` implement Claude;
  `codex_handle.py` / `codex_stream.py` / `codex_daemon.py` / `codex_external.py`
  implement the official Codex app-server paths; `history_store.py` owns the
  rebuildable SQLite projection; `machine.py`, `session_ctx.py`,
  `ringbuffer.py`, `transport.py`, and `session.py` provide the shared session
  pool, routing, live replay, relay transport, and persistence.
- `cc_remote/relay/` — server.py (FastAPI `/ws` + `/api/login` + static), auth.py
  (wrapper bearer + HMAC cookie session), `devices.py` (pairing and enrolled
  machine ownership), `pairing.py` (machine-scoped wrapper/client routing), and
  `forward.py` (bounded per-client queues; slow clients are disconnected without
  silently shedding deltas).
- `web/src/` — `reducer.ts` and `history-merge.ts` own per-session runtime and
  paged history merging; `ws.ts` is the relay client; `protocol.ts` mirrors
  `protocol.py`; `components/DeviceSheet.tsx` manages enrolled machines.

## Run / test
```bash
python -m pip install -r requirements-dev.txt
python -m cc_remote.relay        # terminal 1 (set WEB_STATIC_DIR=web/dist to serve the UI)
python -m cc_remote.wrapper      # terminal 2 (on each machine running Claude/Codex)
pytest                           # zero-token unit tests
npm --prefix web run test:reliability
npm --prefix web run lint
npm --prefix web run build
```
`pytest.ini` restricts collection to `tests/test_*.py`; these are zero-token
unit/regression tests (stub transport, no model). Real relay/wrapper/model probes
live under `scripts/live/` and may spend model tokens — run them explicitly,
keep prompts trivial ("hi"), and prefer the unit tests.
