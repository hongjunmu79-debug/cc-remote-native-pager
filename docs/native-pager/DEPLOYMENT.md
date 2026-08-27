# Native Pager deployment

## Release inputs

- Android application ID: `dev.ccremote.lan`
- Distribution version: `3.0.0-pager.5` (version code `30014`)
- cc-remote product version: `3.0.0`
- cc-remote protocol: `19`
- Native bridge: `1`
- Minimum Android: 8.0 (API 26)
- Target Android: API 36

There is no built-in default server endpoint. The first launch shows a
server-entry screen; the user supplies an HTTPS root origin or a private/local
cleartext HTTP origin. The canonical values live in
`deploy/release-metadata.json` and are consumed by the Gradle build and CI.

The web bundle and APK are one release unit. Deploying only the APK leaves the
chat client usable but the native dashboard has no state source. Deploying only
the web bundle remains backward-compatible because the bridge is dormant when
`window.ccRemoteNative` is absent.

## Build

```powershell
npm --prefix web ci
npm --prefix web run test:reliability
npm --prefix web run lint
npm --prefix web run build

$env:PAGER_SIGNING_PROPERTIES = 'C:\secure\cc-remote\keystore.properties'
android-native\gradlew.bat --no-daemon --no-watch-fs --max-workers=2 `
  :app:testDebugUnitTest :app:lintRelease :app:assembleRelease
```

Signing properties and keystores must remain outside the repository. The
release build fails if a configured signing file is missing or incomplete.

## Web deployment

1. Confirm `web/dist/cc-remote-build.json` reports version `3.0.0` and protocol
   `19`, and that `deploy/release-metadata.json` matches the tag.
2. Copy `web/dist` to a staging directory beside the live static directory.
3. Rename the live directory to a timestamped backup.
4. Rename staging to the live `dist` path.
5. Fetch `/`, its hashed JavaScript asset, and `/cc-remote-build.json`; verify
   HTTP 200 and the `ccRemoteNative`/`bridgeInstanceId` symbols.
6. Keep the previous directory until the phone upgrade is verified.

## APK upgrade verification

Before distribution, use Android SDK `apksigner verify --print-certs` on the
previous and new APK and require identical signer SHA-256 digests. Then verify:

- application ID remains `dev.ccremote.lan`;
- `versionCode` is greater than the installed release;
- v2 signature verification succeeds;
- release Lint and R8 builds pass.

Install with Android's package installer, or with ADB when the device is
connected:

```powershell
adb install -r CC-Remote-Native-Pager-v3.0.0-pager.5.apk
```

## Rollback

The web rollback is a directory swap back to the timestamped backup. The APK
uses the existing signing identity, but Android normally blocks version-code
downgrades; reinstalling an older APK may require uninstalling first and would
clear WebView app data. Prefer web rollback while diagnosing, then publish a
new APK with a higher version code.
