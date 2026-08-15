# Native Pager test strategy

## Zero-network unit tests

- Web projection for offline, idle, running, waiting-answer, succeeded, and
  interrupted lifecycles.
- Activity classification for reasoning, file changes, commands, tests,
  searches, and subagents.
- Projection bounding and exclusion of raw tool output.
- Command schema validation and capability checks.
- Kotlin JSON parsing, version/sequence rejection, size limits, and domain
  normalization.
- Pixel motion math and reduced-motion behavior.
- View-model navigation, freshness, read-state, and acknowledgement handling.

## UI and integration tests

- Compose tests for empty, offline, multi-task, urgent, and expanded states.
- Web reliability suite with the native bridge enabled and absent.
- Android debug and release builds with lint.
- Web bundle production build and lint.
- A local fake bridge fixture exercises WebMessage snapshots and commands
  without starting a model.

## Physical-device acceptance

- Login, cookie retention, and relaunch.
- Dashboard/chat switching without duplicate WebView or bridge ownership;
  current WebViews keep the live surface and Chromium 90 uses its bounded
  reveal reload.
- Five simultaneous sessions and background activity updates.
- Answer and interrupt commands, including command rejection.
- Network loss/recovery and stale-bridge indication.
- 30-minute animation/battery/thermal observation.
- Old Android System WebView compatibility.
- Android Back returns from chat to the native dashboard (with standard IME
  dismissal taking precedence while the keyboard owns Back).
