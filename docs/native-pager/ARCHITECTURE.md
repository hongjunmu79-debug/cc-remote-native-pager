# Native Pager architecture

## Status

Accepted. The Android client uses a native Jetpack Compose dashboard while the
embedded cc-remote web client remains the sole owner of authentication,
WebSocket transport, replay cursors, session history, and command delivery.

## Context

The deployed Windows cc-remote instance is v3.0.0 on wire protocol v19. Its
Android package is currently a machine-specific WebView shell fixed to the LAN
origin `http://192.168.3.4:8766`. Reimplementing protocol v19 in Kotlin would
create a second state machine for login cookies, generations, replay, focus,
and the reliable command outbox.

## Decision

Use a versioned, origin-restricted WebMessage bridge:

```text
relay / wrapper
      |
      v
cc-remote web reducer ----> native snapshot projector
      |                              |
      |                              v
      |                    WebMessage bridge v1
      |                              |
      v                              v
WebView chat                  Kotlin StateFlow
                                     |
                                     v
                              Compose Pager UI
```

The web client publishes a bounded projection, never raw transcript/tool
payloads. Native commands are a small allow-list and are executed through the
existing `RelayWs` instance. The APK does not hold relay credentials.

## Boundaries

- `web/src/native-pager/` owns the wire contract, projection, validation, and
  bridge component.
- `android-native/app/.../bridge/` owns parsing, sequence validation, freshness,
  and command acknowledgements.
- `android-native/app/.../domain/` is transport-independent.
- `android-native/app/.../ui/` renders immutable domain models and never talks
  to WebView directly.
- `MainActivity` composes dependencies; it contains no projection or rendering
  algorithms.

## Reliability properties

- Exact allowed origin in `WebViewCompat.addWebMessageListener`.
- Monotonic snapshot sequence scoped to a per-page instance ID; stale or
  wrong-version frames are rejected, while a page reload can restart at 1.
- Snapshot heartbeat every 15 seconds and native freshness timeout.
- At most 64 tasks, 16 subagents per task, and 256 KiB per bridge frame.
- Commands are idempotently acknowledged by command ID.
- The WebView stays attached while the native dashboard is visible so auth and
  WebSocket state are not recreated when switching views.
- Native state is a projection only. Reloading the page rebuilds it from the
  authoritative web reducer.

## Licensing

The implementation is original MIT-licensed code. AgentPager source, assets,
fonts, motion constants, and Kotlin UI code are not copied. Only the general
product behavior of a multi-agent status dashboard is reproduced.
