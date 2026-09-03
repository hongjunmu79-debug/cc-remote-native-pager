# Changelog

[中文](CHANGELOG_zh.md)

## v3.0.0-pager.7 — 2026-09-03

Zero-configuration LAN onboarding on product v3.0.0 / wire protocol v19. No
session, engine, routing, or wire-protocol behavior changed.

- The Windows setup now ranks active physical/private default-gateway adapters
  instead of accepting the first RFC1918 address, which could be a WSL or
  Hyper-V switch. The phone QR therefore carries the reachable Wi-Fi/Ethernet
  address on machines with virtual adapters.
- The packaged local wrapper always connects to its co-located relay through
  `127.0.0.1`; DHCP and Wi-Fi address changes no longer break the internal
  wrapper/relay control link.
- Opening the Windows console automatically creates the one-time QR. A fresh
  Android install opens the scanner directly, keeps scanning past unrelated QR
  codes, and presents manual address/password entry only as a recovery path.
- Repair the Windows release-contract tests so the CI gate executes real
  PowerShell blocks instead of writing stray `\{` escapes into probe scripts.
- The Windows installer now supports Windows PowerShell 5 without a preinstalled
  Python and returns a non-zero exit code when real setup fails. It also accepts
  PowerShell 5's spurious `-1` native-pipeline status when the LAN selector
  produced a valid address, avoiding an incorrect `127.0.0.1` fallback.
- Windows artifacts carry a deterministic, ready-to-run Python/dependency
  bundle. First install performs no Python or package download, and the nested
  runtime archive avoids thousands of slow Inno Setup file operations.
- Bump the Android distribution to version name `3.0.0-pager.7` / version code
  `30016`, with matching two-download setup documentation.

## v3.0.0-pager.5 — 2026-08-27

Production release hardening of the native-pager distribution on cc-remote
product v3.0.0 / wire protocol v19. No wire-protocol change.

### Release metadata and repository identity

- Introduce `deploy/release-metadata.json` as the single canonical source for
  product version `3.0.0`, distribution `3.0.0-pager.5`, protocol `19`, Android
  version code `30014`, application id `dev.ccremote.lan`, and the public
  repository identity.
- Fix clone/release URLs and `install.sh` defaults to the public repository and
  the distribution release; add `docs/UPSTREAM.md` provenance; remove
  machine-specific usernames and LAN addresses from source/docs/tests.

### Reproducible Windows distribution

- Add `packaging/windows/`: deterministic installer + portable archive, no
  shipped `.venv`, no Node LAN proxy, first-run config with strong secrets,
  restrictive ACLs, placeholder rejection, supervised scheduled tasks, a
  LocalSubnet firewall rule, config preservation on upgrade, uninstall/rollback,
  and checksum pinning.
- Add a zero-token Windows packaging/clean-install smoke test suite.

### Generic Android endpoint and security

- Remove the machine-specific default endpoint; first launch requires entering a
  server origin.
- Accept any valid HTTPS root origin; for cleartext HTTP allow only explicit
  private/local IP literals after a visible warning, and reject public HTTP,
  userinfo, query, fragment, and non-root paths.
- Harden WebView enforcement so resource requests and main-frame navigation stay
  inside the selected origin; external links open in the system browser without
  bridge access. Keep the exact-origin native bridge and package
  `dev.ccremote.lan`.
- Add Android endpoint unit tests covering `10/8`, `172.16/12`, `192.168/16`,
  loopback, HTTPS, and rejected public HTTP.

### CI and release workflow

- Add `ci.yml` push/PR gate (Python on Linux + Windows, web, Android, metadata,
  secret/identity scans) separate from `release.yml` tag releases.
- Repair tag validation so `v3.0.0-pager.5` matches canonical distribution
  metadata; build all release assets from source; add SHA-256 checksums and
  GitHub attestations; support Android signing from protected secrets; make any
  failed gate block publication; pin third-party actions to immutable SHAs.

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
