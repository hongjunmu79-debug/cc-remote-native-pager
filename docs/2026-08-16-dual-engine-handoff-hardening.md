# Dual-engine pager and handoff hardening

Date: 2026-08-16

## Scope

- Show Claude Code and Codex Code sessions together in the native Android pager.
- Route a pager card to its owning engine without merging foreign sessions into
  the active Web chat reducer.
- Keep the chat composer above the Android IME on legacy System WebView builds.
- Preserve the live WebSocket while moving between the native dashboard and Web
  chat, and probe immediately when the app returns to the foreground.
- Represent Codex's official per-thread active-writer lease as a stable read-only
  handoff state, then recover the same thread automatically after release.

## Architecture

### Native dashboard catalog

`web/src/native-pager/catalog.ts` owns the engine-neutral monitoring projection.
The Web UI continues to accept one authoritative `engine + space` catalog into
its reducer. On the native host, background `claude/code` and `codex/code` lists
are normalized, deduplicated, ordered by activity, and projected through the
bounded native bridge. Pager commands resolve the cached session and explicitly
switch the Web surface before focusing or mutating it.

This separation prevents the dashboard requirement from widening the active chat
state machine or increasing transcript rendering work.

### Android viewport and reveal path

`ImeInsetsController` consumes `WindowInsetsCompat.Type.ime()` and applies the
full occluded bottom inset as the WebView's bottom margin. `adjustResize` remains
declared as a platform fallback. The Web visual-viewport hook remains useful for
ordinary browsers, but it is no longer the only source of truth on Chromium 90.

Revealing chat refreshes the legacy compositor layer and checks that the native
JavaScript bridge is alive. A full page reload is now a bounded discarded-page
fallback, not normal navigation. Foreground/network wake events immediately ping
or rebuild an old socket.

### Codex writer handoff

The app-server error `-32600: already has an active writer` is promoted to the
typed `CodexActiveWriterError`. A conflicting resume creates a history-capable
resident context with `control_mode=desktop` and `write_state=read_only` instead
of returning a generic connection failure. The wrapper retries only the official
resume path with a 2–15 second bounded backoff. It never kills Codex Desktop and
never falls back to a competing private writer. Successful resume atomically
restores writable control and republishes current history.

### Reconnect and Windows durability

Wrapper transport backoff is now configuration-owned through
`WRAPPER_RECONNECT_MAX_SECONDS` and defaults to a 10-second ceiling instead of
30 seconds. The client treats an authenticated Wrapper reconnect or session
catalog as immediate transport liveness, while keeping every affected runtime
behind its independent `syncReady` write barrier until replay/snapshot proof.
This prevents both stale “Wrapper offline” dashboards and premature queue drain.

Windows session-state filenames are contained under `state/sessions` and never
interpret a drive-qualified working directory as a destination path. State
writes are flushed and atomically replaced through shared cross-platform file
primitives. The history index negotiates WAL once at initialization rather than
on every short-lived connection and allows a 15-second asynchronous SQLite busy
window for large concurrent rollout refreshes.

## Quality gates

- Web reliability suite, including dual-engine native projection: pass.
- Web lint and production build: pass.
- Android unit tests, `lintDebug`, and `assembleDebug`: pass.
- Codex handoff and shared-daemon regressions: 20 passed.
- Final focused Python reliability run: 61 passed (history concurrency, Windows
  session containment, reconnect backoff, Codex writer handoff, import safety).
- Final Web reliability suite, lint, TypeScript build, and production Vite build:
  pass; main bundle 335.02 kB / 99.51 kB gzip.
- Signed Android release gate: package `dev.ccremote.lan`, version code `30012`,
  version `3.0.0-pager.3`, label `CC Remote Pager`.
- Repository-wide Python run: 1,017 passed, 1 skipped; 14 unrelated deployment /
  Claude CLI tests fail because this Windows checkout presents CRLF shell scripts
  to WSL and the experimental executable is not present. No changed module is in
  those failures.

## Live acceptance checklist

- [x] Relay health reports Wrapper connected, one machine, and one phone client
  after controlled Wrapper and Web deployments.
- [x] Signed APK installed on the connected Redmi K40 with first-install state
  preserved across the update.
- [x] Native dashboard paints `CLAUDE 11`, `CODEX 35`, and `46` total tasks.
- [x] Cross-engine cards open the correct task/history; the current Codex task is
  first by numeric activity timestamp and is shown as running.
- [x] Android IME is visible while the new-chat composer remains fully above it;
  `dumpsys input_method` confirms `ADJUST_RESIZE` and `mInputShown=true`.
- [x] Native dashboard/chat reveal retains the live WebView instead of reloading
  it; foreground cold recovery restored the authenticated client within 4 s.
- [x] Current Desktop-owned Codex thread
  `019fffc6-9ef3-76b2-9cca-d51bfad19067` renders live history as read-only and
  logs a typed active-writer deferral instead of a disconnect.
- [x] Claude runtime `SDK 0.2.119 / CLI 2.1.232` verified and an existing Claude
  session resumed through the patched Windows state path.
- [x] Ten rapid dual-engine refreshes completed with no SQLite lock, permission,
  or command-handling error; Android frame sample was 545 frames, 0.55% jank,
  99th percentile 14 ms.
