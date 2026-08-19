# Changelog

[中文](CHANGELOG_zh.md)

## v3.0.0 — 2026-07-24

cc-remote v3 adds an isolated Cowork-style Work surface to the established
Claude Code + Codex remote control plane, while rebuilding history, native
client coexistence, multi-machine routing, mobile reliability, and release
operations.

### Code and Work

- Add separate Code / Work spaces for both Claude and Codex, with independent
  session lists, focus, directories, prompts, permissions, and recovery state.
- Add provider-scoped Work projects with file, link, and note knowledge sources,
  reusable instruction templates, and materialized per-work context.
- Add persistent one-shot, daily, and weekly schedules with run records, leases,
  heartbeats, retries, and overlap prevention.
- Keep every Work item inside a registry-owned private directory. External
  material enters only through attachments or project knowledge sources.
- List files produced by a Work item as artifacts and preview source, Markdown,
  sanitized HTML, images, PDFs, and sandbox-converted Office documents locally.

### Sessions, controls, and extensions

- Add reliable delete, rename, archive, per-message fork, ephemeral side-chat,
  queue, interrupt, and background-session control without focus stealing.
- Add native Codex compact and Review, plus isolated Git worktree forks. The
  unfinished Codex Rollback and Claude Rewind surfaces remain unavailable.
- Keep model, reasoning effort, service tier, collaboration/Plan mode,
  permissions, context, goals, status, usage, and rate limits scoped to the
  active session.
- Add live Skills, Plugins, Apps, MCP, and Hooks catalogs. Code can manage
  Skills, plugins, and Claude Hooks where supported; Codex Hooks and all Work
  extension categories remain read-only.
- Forward Claude tool approval and Codex command, file-change, user-input,
  general-permission, and MCP elicitation requests to the controlling browser.

### Local-first history

- Paint the browser's last validated IndexedDB projection before network
  validation.
- Materialize source-fingerprinted turn summaries in a rebuildable wrapper
  SQLite index.
- Load newest turns first, page older history, and fetch heavy tool/reasoning
  detail only when that turn is expanded.
- Preserve the viewport while prepending pages and converge appended sources in
  the background.
- Resolve historical image assets on demand instead of embedding them in every
  history page.

### Long Codex sessions and native lifecycle

- Read Codex rollouts backward by turn without re-uploading history to the model
  or replacing app-server-native resume and compaction state.
- Add a narrowly gated official HTTP transport fallback for oversized Codex
  Desktop + OpenAI resumes whose WebSocket closes before completion.
- Keep Codex shared-daemon CLI activity distinct from private Codex App
  ownership.
- Bind prompts, steering, commentary, tools, compaction, aborts, and completion
  to their authoritative turn so history cannot drift to the bottom.
- Mirror interrupted and externally running work without stale read-only locks
  or permanent thinking indicators.

### Devices and ownership

- Add Device Center, expiring single-use pairing codes, hashed machine
  credentials, rename/revoke controls, and online state.
- Add optional multi-user account policies that restrict each account to an
  explicit set of wrapper machines.
- Enforce account-to-machine authorization on discovery, commands, events, and
  push subscriptions.
- Scope working directories and delayed focus/rekey frames by device, surface,
  engine, socket generation, and session ownership.
- Add shared Darwin/Linux process identity scanning for native Claude ownership
  while keeping takeover limited to an exact same-user process.
- Add privacy-preserving Web Push for background completion/failure state,
  scoped by user and machine and containing no conversation text.

### Mobile and artifact experience

- Add stable upward history pagination, local-first session switching, and
  bounded live-tail replay.
- Add on-demand conversation images and a touch-friendly lightbox with
  tap-to-close and pinch zoom.
- Support multiple image attachments, stable pending previews, and per-session
  composer drafts across session and engine switches.
- Keep Markdown relative links/images, source files, sanitized HTML, PDFs, and
  sandbox-converted Office previews inside the wrapper security boundary.
- Refresh PWA and notification assets and fix narrow-screen sheets, process
  timelines, and persistent error presentation.
- Keep running indicators above queue/interrupt controls, preserve Claude turn
  durations, and compact repeated tool activity without hiding final replies.

### Release and operations

- Align Python, Codex `clientInfo`, Web package metadata, and the public build
  manifest on product version `3.0.0`.
- Upgrade the strict wire gate to protocol v19.
- Publish reproducible, checksummed Relay/Wrapper archives for Linux x86_64,
  Linux arm64, macOS Intel, and macOS Apple Silicon, with GitHub artifact
  attestations.
- Add a verified role bootstrap, managed Python 3.13 environments, a macOS
  LaunchAgent installer, and a Linux Wrapper systemd installer. Device
  credentials remain outside immutable releases and service definitions.
- Validate product and protocol versions together before staging or activating
  a release.
- Use immutable VPS releases, release-local virtual environments, atomic
  activation, readiness checks, and rollback.

### Upgrade notes

- v3.0.0 uses wire protocol v19. Wrapper, relay, and Web must be upgraded
  together; mixed protocol versions are rejected.
- Hard-refresh already-open browser tabs after deployment so they load the v3
  hashed assets and rebuild their local projection against protocol v19.
- Keep runtime secrets and machine state outside release directories. Do not
  replace `.env`, `~/.cc-remote`, Claude transcripts, or Codex rollouts.
- Claude integration remains pinned to `claude-agent-sdk==0.2.119`.
- History browsing remains a local read: it does not resume Claude/Codex or
  create a model turn.
