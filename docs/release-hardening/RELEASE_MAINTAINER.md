# Release maintainer guide

This document describes how a maintainer cuts a cc-remote native-pager release.
It covers the canonical version source, the tag contract, the CI/build
pipeline, asset signing, checksums/attestations, rollback, and the pre-flight
checklist. Security guidance for operators lives in
[`SECURITY.md`](SECURITY.md); the two user journeys live in the
[`README`](../../README.md).

## Version and identity source of truth

Everything that varies per release lives in one file:
[`deploy/release-metadata.json`](../../deploy/release-metadata.json).

| Key | Current value | Meaning |
| --- | --- | --- |
| `product_version` | `3.0.0` | Base cc-remote backend/web codebase version. |
| `distribution_version` | `3.0.0-pager.5` | Android-pager release line; what release tags and installers target. |
| `protocol` | `19` | Wire protocol. Unchanged by the pager distribution. |
| `repository.owner/name` | `hongjunmu79-debug/cc-remote-native-pager` | Public repository identity used by URLs and install scripts. |
| `android.application_id` | `dev.ccremote.lan` | Android package id; do not change (signing identity). |
| `android.version_name` | `3.0.0-pager.5` | Mirrors the distribution version. |
| `android.version_code` | `30014` | Android version code; must strictly increase for every APK. |

Build scripts and CI consume this file; `python -m deploy.validate_release_metadata`
asserts the backend, web build manifest, `install.sh` defaults, Android Gradle
defaults, READMEs, and repository-identity scans all agree with it. **Never edit
a version in more than one place.** Change `release-metadata.json`, then run the
validator.

## Release tag contract

The release workflow accepts tags in the set `{v<product_version>,
v<distribution_version>}` — i.e. `v3.0.0` and `v3.0.0-pager.5` are both valid,
`v9.9.9` is rejected. A tag push of an unlisted version fails the `verify` job
and cannot publish. The tag must also match `web/package.json`,
`web/package-lock.json`, and the backend `__version__`.

The canonical distribution tag is `v3.0.0-pager.5`. Use the distribution tag
for user-facing releases so installers and Android `version_name` line up.

## Workflows

### `ci.yml` — push/PR gate

Runs on every push to any branch and on pull requests. It is the fast, no-publish
gate: canonical metadata/identity/secret scan, Python tests + Ruff on Ubuntu,
Windows packaging tests on `windows-2025`, web build/reliability/lint, and
Android unit tests + lint (JDK 17). All actions are SHA-pinned. A PR that would
fail a release is caught here first.

### `release.yml` — tag release

Triggered only by tag pushes matching `v*.*.*` or `v*.*.*-*`. Strictly gated:
`publish` `needs: [build, build-windows, build-android]`, and each build job
`needs: verify`. **A failure in any gate makes publication impossible.**

| Job | What it does |
| --- | --- |
| `verify` | Runs the canonical metadata validator, the tag/version contract check, full Python + web tests/lint, `shellcheck`, `bash -n`, and `git diff --check`. |
| `build` (matrix) | Builds deterministic Linux/macOS role bundles (`relay`/`wrapper` × `x86_64`/`arm64`) from source with `deploy.build_release`, then installs the bundle into a scratch venv and imports the packaged role. |
| `build-windows` | Builds the deterministic Windows archive with `packaging\windows\build.ps1` (source-built `web/dist`, no shipped `.venv`, no Node LAN proxy), plus its `.sha256`. |
| `build-android` | Runs Android unit tests + `lintDebug`, then `assembleRelease`. When the `PAGER_KEYSTORE_B64` and `PAGER_SIGNING_PROPERTIES` secrets are present the APK is signed; otherwise it assembles an unsigned release APK (CI gate only). |
| `publish` | Downloads all build artifacts, assembles `SHA256SUMS`, runs `actions/attest` for attestations, and creates/uploads/publishes the GitHub Release. Refuses to replace an already-published release. |

## Building release assets from source

- **Linux/macOS bundles:** built by `deploy/build_release.py` in CI. They embed a
  pinned `uv`, the hashed role lockfile, the MIT license, and the release
  manifest. Byte-identical for the same git SHA + `SOURCE_DATE_EPOCH` + sources.
- **Windows archive:** built by `packaging\windows\build.ps1` → deterministic
  zip + `distribution-manifest.json` (per-file SHA-256) + outer `.sha256`.
  See [`packaging/windows/README.md`](../../packaging/windows/README.md).
- **Android APK:** built by Gradle in `android-native/`. Release assets come
  from CI only — do not manually upload a machine-built APK.

## Signing

### Android

The release APK must keep the existing signing identity (package
`dev.ccremote.lan`, same signer SHA-256 digest). `assembleRelease` reads signing
configuration from a properties file whose path is exported as
`PAGER_SIGNING_PROPERTIES`; CI decodes the keystore from the
`PAGER_KEYSTORE_B64` secret and co-locates both under `$RUNNER_TEMP/pager-keys/`.

> **Workflow gotcha:** the `secrets` context is **not available in `if:`
> conditions**. Referencing it there makes GitHub fail the whole run at startup
> with `Unrecognized named-value: 'secrets'` and schedules zero jobs. The
> signing step always runs and the bash guard decides from the
> `PAGER_KEYSTORE_B64` / `PAGER_SIGNING_PROPERTIES` environment variables;
> absent secrets yield an unsigned release APK.

> **External dependency:** the keystore, alias, and passwords are not in this
> repository and cannot be reconstructed here. They must be provisioned as
> repository/environment secrets. If that signing material is unavailable to CI,
> the release publishes an **unsigned** APK and the maintainer must sign locally
> with the real keystore before distribution — never substitute a different key.

Before distributing an APK, verify with Android SDK `apksigner verify --print-certs`
that the previous and new APKs have identical signer SHA-256 digests, the
application id is `dev.ccremote.lan`, and the version code strictly increased.

### Windows

Authenticode signing is **optional and CI-secret-driven**. The workflow builds
and verifies the archive unsigned; if a code-signing certificate is later made
available as a secret, sign the produced `.zip` (and `setup.ps1`) in a
follow-up CI step. Never commit or invent a certificate. Local builds are
explicitly unsigned and `win_smoke` documents that in the distribution
manifest/notes.

## Checksums and attestations

`publish` computes `SHA256SUMS` over every release asset (`cc-remote-*.tar.gz`,
`cc-remote-*.zip`, `cc-remote-*.zip.sha256`, `*.apk`, `install.sh`) and attaches
`actions/attest` provenance. Users verify downloads with `sha256sum -c` /
`shasum -a 256 -c`.

## Version bump procedure

1. On `main`, bump `deploy/release-metadata.json` (and, when it changes, the
   backend `cc_remote/__init__.py`, `web/package.json`,
   `web/package-lock.json`, and `web/public/cc-remote-build.json`).
2. Run `python deploy/validate_release_metadata.py --root .` and the full
   zero-token test suite.
3. Open the PR through `ci.yml`. Merge to `main`.
4. Tag the merge commit with the distribution tag (`v3.0.0-pager.N`), then
   push the tag. `release.yml` handles the rest.

## Rollback

- **Linux/macOS:** each release keeps the previous immutable release directory
  and `current` symlink; a failed activation restores `current` and the service
  definitions.
- **Windows:** `uninstall.ps1 -Rollback` restores the previous release tree
  recorded in `releases\current.json`; configuration is preserved.
- **Android:** Android blocks version-code downgrades. Roll the web/relay/wrapper
  back first, then publish an APK with a higher version code. See
  [`docs/native-pager/DEPLOYMENT.md`](../native-pager/DEPLOYMENT.md).

## Pre-release checklist

- [ ] `deploy/release-metadata.json` is the only place versions changed, and it
      matches the intended tag.
- [ ] `python deploy/validate_release_metadata.py --root .` passes.
- [ ] `pytest`, `npm --prefix web run test:reliability`, `npm --prefix web run lint`,
      `npm --prefix web run build`, and `tests/test_windows_packaging.py` all pass.
- [ ] Android `./gradlew testDebugUnitTest lintDebug` pass.
- [ ] `git diff --check` passes and `git fsck` is healthy.
- [ ] Release tag pushed; `release.yml` runs `verify` → all `build*` jobs →
      `publish`; no gate fails.
- [ ] GitHub Release contains all assets + `SHA256SUMS`, attestation succeeded,
      and the APK is signed with the real certificate.

## What still requires a human / external action

These cannot be verified inside CI and need the parent acceptance owner or a
maintainer:

- A clean-VM install/upgrade/rollback run of the Windows archive.
- On-device Android verification (bridge behavior, endpoint entry, IME, WebView
  enforcement) with a real ADB device.
- Android keystore / signing-password provisioning and a signed-APK verify.
- Windows Authenticode certificate provisioning, if used.
- GitHub repository administration (branch protection, secrets, environment
  approval, release publication rights).
