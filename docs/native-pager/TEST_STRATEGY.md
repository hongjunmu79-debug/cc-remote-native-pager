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

## Legacy WebView geometry

- Terminal-card placement is a pure, unit-tested function rather than an
  inline collection of CSS offsets.
- Native Android hosts center the card in the fixed-position layout viewport.
  This remains correct when an older System WebView exposes a desktop-width
  layout viewport on a narrow physical display.
- Browser hosts retain trigger alignment, with both edges clamped inside the
  viewport. Tests cover the deployed 980px legacy layout and a 390px narrow
  layout.
- Release verification includes opening the terminal card on the supported
  Android device and confirming that its full outline and close action remain
  visible.
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
- Chat renders inside the dynamic status-bar, navigation-bar, and
  display-cutout safe insets in both orientations and in light/dark mode, and
  the composer stays above the IME.

## ADB manual check: chat safe insets

The chat WebView must never draw under the system bars or the display cutout.
The objective part of the check is below; the rest is visual confirmation on the
device.

```text
# 1. Build and install the debug APK, then launch chat
cd android-native
gradlew.bat assembleDebug
adb install -r app/build/outputs/apk/debug/app-debug.apk
adb shell am start -n dev.ccremote.lan.debug/dev.ccremote.pager.MainActivity

# 2. Prove the device reports a cutout (Huawei notch / punch hole). The
#    dumpsys line names the cutout geometry the app must respect.
adb shell dumpsys window displays | grep -i cutout

# 3. Portrait: the web client's top-right theme button must sit BELOW the
#    status bar (not under the battery icons), and the composer must sit ABOVE
#    the navigation bar.

# 4. Landscape: lock rotation and rotate; content must clear any left/right
#    cutout and the landscape navigation bar.
adb shell settings put system accelerometer_rotation 0
adb shell settings put system user_rotation 1    # 90 degrees
adb shell settings put system user_rotation 0    # back to portrait

# 5. IME: tap the composer so the keyboard shows. The WebView bottom edge must
#    lift above the keyboard and restore when it dismisses.
```

For devices without a physical cutout, enable Developer options → "Simulate a
display with a cutout" and repeat steps 3–5. The pure margin maths (`webViewInsets`
and `effectiveImeBottom`) is additionally covered by JVM unit tests, so the ADB
check is a smoke confirmation of the wiring, not the arithmetic.
