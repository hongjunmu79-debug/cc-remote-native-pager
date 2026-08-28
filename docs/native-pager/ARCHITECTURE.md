# Native Pager architecture

## Status

Accepted. The Android client uses a native Jetpack Compose dashboard while the
embedded cc-remote web client remains the sole owner of authentication,
WebSocket transport, replay cursors, session history, and command delivery.

## Context

The deployed Windows cc-remote instance is v3.0.0 on wire protocol v19. Its
Android package is a generic WebView shell: the user enters the relay origin on
first launch (any HTTPS root origin, or a private/local cleartext HTTP origin
after a visible warning). Reimplementing protocol v19 in Kotlin would create a
second state machine for login cookies, generations, replay, focus, and the
reliable command outbox.

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

`MainActivity` owns a native `FrameLayout`: the WebView is its direct base
child and the Compose dashboard is an opaque sibling layer. Dashboard mode
shows the Compose layer and disables WebView input; chat mode marks the Compose
layer `GONE`. This preserves the proven cc-remote WebView rendering path and
avoids `AndroidView`/Compose compositor defects on legacy vendor WebViews.

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
- One Activity-owned WebView remains alive while the native dashboard is
  visible, so cookies, JavaScript state, and the WebSocket bridge are not
  duplicated. The Compose layer never owns or destroys it.
- Chromium 90 vendor builds receive one authenticated same-page reload when
  chat is revealed. Current WebViews keep their live surface without reload.
- Native state is a projection only. Reloading the page rebuilds it from the
  authoritative web reducer.

## Edge-to-edge inset handling

`MainActivity` runs `enableEdgeToEdge()`, so the Compose dashboard draws edge to
edge and applies its own insets. The legacy CHAT WebView is a full-bleed sibling
under that same window, so without intervention its page renders beneath the
status bar, navigation bar, and display cutout — on a Huawei Android 12 device
the web client's top-right theme button overlaps the battery icons.

`WebViewInsetsController` is the WebView's single owner of window-inset handling.
It installs the only `OnApplyWindowInsetsListener` and a
`DISPATCH_MODE_CONTINUE_ON_SUBTREE` animation callback, then turns the merged
`systemBars()` + `displayCutout()` insets into the WebView's layout margins,
lifting the bottom edge by the `ime()` inset while the keyboard is visible. The
listener passes insets through unchanged, so the WebView subtree still receives
them downstream and IME/visual-viewport behaviour is preserved. The margin
merge lives in a pure, unit-tested function (`webViewInsets`) so device and
orientation maths are not coupled to the view. The `FrameLayout` root behind the
WebView is tinted `pager_window_background` (light `#F4F7F8` / dark `#090D12`,
matching the Compose theme), so the strips the inset WebView no longer covers
stay readable in both modes.

## Licensing

The implementation is original MIT-licensed code. AgentPager source, assets,
fonts, motion constants, and Kotlin UI code are not copied. Only the general
product behavior of a multi-agent status dashboard is reproduced.
