# Windows Claude Takeover and Agent Deck Engine Filter

## Scope

- Make explicit Remote takeover work for Claude sessions owned by a native
  Windows process without weakening the existing fail-closed ownership model.
- Add persistent-in-screen `ALL / CLAUDE / CODEX` projections to Agent Deck.

## Root cause

Claude ownership discovery supported Linux `/proc` and macOS `ps`/`lsof`, but
had no Windows process-table implementation. Transcript growth could therefore
correctly mark a session external while the explicit takeover probe always
returned `complete=false`. The wrapper rejected the command by design.

## Windows ownership implementation

`process_scan.py` now provides native Windows process primitives:

1. A bounded ToolHelp snapshot supplies the complete parent graph.
2. `GetProcessTimes` supplies a stable creation FILETIME for PID-reuse defense.
3. Command lines are read from the candidate PEB and parsed with
   `CommandLineToArgvW`; no PowerShell/WMI subprocess runs in the polling loop.
4. Claude's PID-scoped registry (`~/.claude/sessions/<pid>.json`) binds the exact
   process identity to `sessionId`. Modern `procStart` values must match exactly;
   older `startedAt` rows use a narrow compatibility window.
5. Wrapper descendants are removed through the full parent graph. Missing,
   contradictory, oversized, unreadable, or reused identities remain incomplete
   and keep Remote read-only.
6. An explicit session outside the watched catalog is isolated from the current
   probe. It cannot make an unrelated single-session takeover incomplete merely
   because both processes use `--resume`; contradictions that point into the
   watched catalog still fail closed.
7. Explicit takeover compares the target and wrapper owner SID, revalidates the
   exact process identity, and terminates only that Windows process handle. The
   user's terminal shell, process group, and unrelated Claude sessions are not
   targeted.

The takeover command remains reliable and at-most-once. History is reloaded only
after every captured process identity disappears, after which the resident SDK
must resume the exact transcript before Web becomes writable.

## Agent Deck filtering

`PagerEngineFilter` is a pure domain projection with three values: `ALL`,
`CLAUDE`, and `CODEX`. Compose owns only the selected enum and derives the
visible list with `remember`; the canonical task list, ordering, pin state, and
bridge state are unchanged. LazyColumn continues to use stable task IDs.

The summary cards are accessible selectable tabs with selected border/tint and
counts derived independently per engine. Changing tabs closes an expanded card
and returns the list to the first item. Empty engine projections show a scoped
empty state instead of pretending the entire dashboard has no sessions.

## Verification

- Windows live scan: complete; exact holders resolved for four Claude sessions;
  cc-remote SDK descendants excluded.
- Multi-session Windows scan: an unrelated explicitly resumed Claude process no
  longer poisons the current catalog; watched-target contradictions remain
  rejected.
- Windows exact-handle termination: isolated child exited with the expected code.
- Python Windows-focused regressions: passed, including multi-session isolation.
- Python Linux/WSL Claude ownership and takeover regressions: 47 passed.
- Android unit tests: passed, including filter order/exactness.
- Android release lint, unit tests, and signed assembly passed.
- Upgrade-installed `3.0.0-pager.4` on the connected Redmi K40. Agent Deck showed
  46 tasks (`Claude 11`, `Codex 35`) and both engine projections rendered without
  list corruption or visible jank.
- ADB end-to-end takeover of session
  `e4b668b6-ce46-4f78-a2cf-a2dbb1915188` stopped only its external PID, resumed
  the same transcript through the resident SDK, and changed the authoritative
  UI state from `external_cli/read_only` to `remote/writable`.
