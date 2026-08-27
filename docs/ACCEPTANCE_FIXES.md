# ACCEPTANCE_FIXES.md — release-hardening rounds 2–3, 6

What was fixed in this round, the exact commands used to verify it, and the
honest limitations of the local verification. Everything here is zero-token
unless stated otherwise.

Branch: `codex/release-hardening`. No main merge, no tag, no release was
created by this work.

## Round 6: bundle-root derivation, strict-mode string paths, endpoint canonicalization, stale docs

The failed live release run (GitHub run 33097259865) exposed four concrete
defects; each is fixed here with a regression test that runs locally.

### 6.1 Linux/macOS smoke: bundle root is the canonical version, not the ref name

`release.yml`'s `build` smoke step addressed the extracted archive root as
`cc-remote-$RELEASE_ROLE-${GITHUB_REF_NAME}`. On a manual `workflow_dispatch`
from a slash branch (`codex/release-hardening`) the ref name contains a slash
and can never be an archive root, so every bundle smoke failed. The step now
derives the root from `deploy/release-metadata.json`'s `distribution_version`
with a `v` prefix — the exact prefix `deploy/build_release.py` writes. New
regression: `test_release_smoke_bundle_root_derives_from_canonical_distribution_version`.

### 6.2 Windows build.ps1: string paths under Set-StrictMode

The sha256 status loop in `packaging\windows\build.ps1` iterated archive paths
as plain strings but read `$path.Name`, which throws
`PropertyNotFoundException` under `Set-StrictMode -Version 2.0` on the real
windows-2025 runner. The loop now uses `Split-Path -Leaf` for both the status
line and the `.sha256` sidecar, matching the installer `.exe` block. New
regression: `test_build_ps1_archive_loop_uses_split_path_leaf_under_strict_mode`
executes the exact foreach-under-StrictMode idiom on a real host. The pipeline
still produces exactly 2 ZIPs + 1 genuine Inno `.exe`.

### 6.3 Android ServerEndpoint.parse: port bounds and host canonicalization

`ServerEndpoint.parse` accepted explicit ports outside 1..65535 (including 0
and 65536/70000) and preserved an uppercase or DNS-trailing-dot host in the
stored url/origin, so the bridge and `OriginPolicy` disagreed on the exact
origin. The parser now rejects any explicit port outside 1..65535 (0 included)
and canonicalizes the host lowercase with the trailing DNS root dot stripped in
BOTH the url and the origin (shared `canonicalDnsHost` helper), so bridge
navigation and OriginPolicy enforcement always agree. All existing security
rejections (public cleartext HTTP, userinfo, query/fragment, non-root paths,
unsupported schemes) are preserved. New JVM tests cover uppercase hosts,
trailing dots, explicit default ports, and ports 0 / 65536 / 70000.

### 6.4 Docs: a tag release can never publish an unsigned APK

`docs/release-hardening/RELEASE_MAINTAINER.md` claimed that when signing
material is unavailable to CI the release publishes an unsigned APK to be
signed locally. The truth is fail-closed: a tag push without both
`PAGER_KEYSTORE_B64` and `PAGER_SIGNING_PROPERTIES` stops `build-android`, so an
unsigned APK is never assembled and publication is impossible. Only a manual
`workflow_dispatch` validation build may assemble an unsigned APK, and it has
no publish path. The maintainer guide was corrected along with the adjacent
stale claims (only the canonical distribution tag may publish; the bare
`v3.0.0` is not a trigger; `workflow_dispatch` is a no-publish validation path).

### Verification (exact commands and results)

```
python deploy/validate_release_metadata.py --root .          # PASS — cc-remote 3.0.0 distribution 3.0.0-pager.5 protocol v19 dev.ccremote.lan vc30014
.venv\Scripts\python.exe -m pytest tests/test_release_distribution.py -q
# => 12 passed; 11 pre-existing Windows-only POSIX failures (fake-uv exec-bit,
#    symlink/atomic-rename, WSL bash) that run on Ubuntu CI only
.venv\Scripts\python.exe -m pytest `
  tests/test_windows_packaging.py tests/test_release_metadata.py tests/test_windows_import_compat.py -q
# => 89 passed (incl. the new strict-mode string-path regression)
.venv\Scripts\python.exe -m pytest <the 11 curated release-contract node IDs> -q
# => 12 passed (incl. the new bundle-root regression)
uvx --from ruff==0.15.13 ruff check cc_remote tests deploy   # All checks passed
git diff --check                                              # clean
cd android-native && gradlew.bat testDebugUnitTest            # BUILD SUCCESSFUL (JVM unit tests)
cd android-native && gradlew.bat lintDebug                    # BUILD SUCCESSFUL (Android lint)
```

### Honest limitations

- The release.yml smoke step itself runs only on a real Linux/macOS Actions
  runner; the regression test proves the workflow text derives the bundle root
  from the canonical metadata instead of the ref name.
- The Windows `build.ps1` archive loop is exercised by the executable
  strict-mode probe on this machine, but the full three-artifact `build.ps1`
  run (including the ISCC-compiled `.exe`) still happens on the windows-2025 CI
  runner; no Inno Setup is installed here.
- Android unit tests/lint ran on the local JDK 17. No APK was signed or
  installed; no signing material or secrets were touched, and no machine
  identity is recorded here.

## Round 3: the acceptance-run CI failures, fixed

The live CI run for the previous round's HEAD failed for exactly two root
causes (Web and Android passed). This round fixes only those two; the security
scanner and the metadata validator are not altered.

### 3.1 Machine-identity literals removed from this document

`docs/ACCEPTANCE_FIXES.md` still carried the build machine's username and an
absolute Windows user-home path in the "Windows packaging pipeline" evidence
paragraph. Those are now written with neutral, descriptive wording: the smoke
gate in `win_smoke.py` deliberately guards against the builder's username, and
the same payload passes when the smoke's temp root is outside a user home
directory. No username, user-home path, or other machine-specific literal
remains. The scanner (`deploy/release_scan.py`) and the validator
(`deploy/validate_release_metadata.py`) are unchanged, and the gate's
definition line in `win_smoke.py` keeps its existing allow-marker.

Verified with the canonical gate and its tests:

```
python deploy/validate_release_metadata.py --root .    # passes, summary printed
.venv\Scripts\python.exe -m pytest tests/test_release_metadata.py -q
# => 14 passed
```

### 3.2 Windows CI installs the canonical runtime lock, not just pytest

The `python-windows` job installed only `pytest`, but
`test_release_distribution.py` imports `cc_remote.protocol`, which imports
`typing_extensions` at module load — so collection aborted with
`ModuleNotFoundError: typing_extensions` before any test ran. The job now
mirrors the Ubuntu python job: `setup-uv` (same pinned action SHA and uv
version) creates a `.venv`, `uv pip sync` installs the hash-verified universal
`requirements.lock` (`--require-hashes --only-binary=:all:
--no-binary=http-ece`), and `uv pip install -r requirements-dev.txt` adds the
dev set. The selected tests run with `.venv\Scripts\python.exe`. No test file
was modified to hide the collection failure.

Verified in a clean-dependency-equivalent environment — a fresh uv venv on
Python 3.11 with nothing installed beyond the canonical lock and the dev set.
The `http-ece` sdist (pure Python, no wheel) builds from source on Windows,
matching the Ubuntu job's behaviour:

```
.venv\Scripts\python.exe -m pytest `
  tests/test_windows_packaging.py `
  tests/test_windows_import_compat.py `
  tests/test_release_metadata.py `
  (the 10 Windows-compatible test_release_distribution.py node IDs shown in
   the round-2 local verification block below) `
  -q
# => 99 passed
```

Both workflows still parse as valid YAML.

## 1. Windows distribution: two genuinely distinct deliverables

The Windows release now produces **three** artifacts, all named from the
canonical `distribution_version` in `deploy/release-metadata.json`
(`3.0.0-pager.5`):

| artifact | purpose |
|---|---|
| `cc-remote-v3.0.0-pager.5-windows-x64.zip` | **installer archive**: `setup.ps1` at root, runs the transactional install |
| `cc-remote-v3.0.0-pager.5-windows-x64-portable.zip` | **portable archive**: `start-portable.ps1` at root, self-contained venv, no tasks/firewall/registry |
| `cc-remote-v3.0.0-pager.5-windows-x64-setup.exe` | **real installer executable** compiled by Inno Setup (ISCC) |

A ZIP containing `setup.ps1` alone is not an installer; the `.exe` is compiled
by `build-installer.ps1` from `inno/cc-remote.iss`, which runs `setup.ps1` with
`-InstallRoot {app}` and `PrivilegesRequired=lowest`. `build-installer.ps1`
fails closed (throws, never emits a fake installer) when ISCC is absent.

Each artifact gets a sibling `.sha256` file.

### Fixes found while validating the pipeline
- **`build.ps1` referenced an undefined `$productVersion`** when calling
  `build-installer.ps1` (would throw under `Set-StrictMode`). Fixed by reading
  it from the metadata object.
- **Manifest self-reference corruption**: `win_build.py main()` regenerates
  the distribution manifest on every archive assembly, and `build_manifest`
  hashed `SHA256SUMS` and `distribution-manifest.json` themselves. Assembling
  two archives (installer + portable) from ONE staged payload therefore wrote
  stale self-checksums into the manifest, and the *extracted* payload failed
  `verify_distribution` — meaning `install.ps1`'s payload gate would refuse
  the shipped artifact. Fixed in `win_manifest.py`: the two derived files are
  excluded from the manifest's file map, so rebuilds are idempotent. New
  regression test: `test_rebuilding_manifest_over_built_distribution_is_idempotent`.

## 2. Windows lifecycle: rollback and dual-process start

- **`uninstall.ps1 -Rollback`** is now a failure-safe transaction in the
  required order: (1) pinned uv re-sync of the runtime venv to the previous
  release's `requirements.lock`, (2) junction switch to the previous release,
  (3) `register-tasks.ps1` re-creates and starts the supervised tasks,
  (4) `Test-SupervisedHealth` health-check. On any failure the failed-active
  release is restored (junction, venv, tasks) and the error rethrown.
- **`start.ps1 -Service both`** launches relay and wrapper concurrently (relay
  does not block wrapper startup), waits for the first child to exit, stops the
  remaining one, propagates the exit code, and cleans up on Ctrl+C.
- **`start.ps1` exit-code bug**: `Start-Process -PassThru` returns an EMPTY
  `ExitCode` in Windows PowerShell 5.1 once the child has exited, so a failing
  child (e.g. `sys.exit(7)`) was reported as success. `Start-RemoteChild` now
  uses a raw .NET `Process`/`ProcessStartInfo` (`UseShellExecute=$false`,
  `CreateNoWindow=$false`), which reaps the real exit status. Reproduced in
  isolation first: `powershell -File` reported `ExitCode=[]` for `Start-Process`,
  then `ExitCode=[7]` for the raw Process.
- **`install.ps1`** wires the venv `.pth` to `releases\current` (the junction),
  and task registration is delegated to the shared `register-tasks.ps1` so
  install and rollback cannot drift.

## 3. Release fail-closed: signing, signer fingerprint, tags, pins

- Android tag-push builds are fail-closed: missing `PAGER_KEYSTORE_B64` /
  `PAGER_SIGNING_PROPERTIES` on a tag push stops the build (an unsigned release
  APK is never produced); manual `workflow_dispatch` validation runs may build
  unsigned.
- On tag pushes the assembled APK's signer certificate SHA-256 must equal
  `deploy/release-metadata.json` → `android.signer_sha256` (via `apksigner
  verify --print-certs`); otherwise the job fails and publish is impossible.
- Only the canonical distribution tag (`v3.0.0-pager.5`, enforced by
  `deploy/tag_contract.py`) is a release; the bare product tag is not a trigger
  and is rejected. Publication is tag-push-only.
- GitHub Actions are pinned by commit SHA with `# vN` annotations
  (checkout `@d23441a4… # v6`, setup-python `@ece7cb06… # v6`, setup-node
  `@24997072… # v6`, setup-uv `@08807647… # v8.1.0`, setup-java
  `@b6effb05… # v5`, upload/download-artifact `@ea165f8d…`/`@d3f86a10… # v4`).

## 4. Android WebView exact-origin allowlisting

`SecureWebViewController.kt` allows only the exact origin the app is meant to
reach (host, scheme, and port), via the new `OriginPolicy.kt` + `OriginPolicyTest.kt`.

## 5. CI wiring

- **`ci.yml` python-windows job** now runs the production-relevant set on a
  Windows runner: the full `test_windows_packaging.py` (packaging, install,
  rollback, dual-process smoke), `test_windows_import_compat.py`,
  `test_release_metadata.py`, and the Windows-compatible release-workflow
  contracts from `test_release_distribution.py` (fail-closed signing, signer
  SHA-256, tag contract, immutable pins, web-before-python). POSIX-only tests
  (chmod, symlinks, atomic-rename, shell installers) deliberately run on Ubuntu
  only — none are marked skipped.
- **`release.yml` build-windows** installs Inno Setup (ISCC) via chocolatey,
  builds all three artifacts, then verifies both zips extract to the correct
  layout and pass the clean-install smoke (`win_smoke.py --check`) on the
  extracted payload, plus the exe + checksum presence.
- **`release.yml` publish** now checksums and asserts 2 zips, 1 exe, and their
  `.sha256` files alongside the 6 role bundles.

## Local verification (exact commands and results)

All commands run on this Windows 11 machine from the repo root, Python from the
repo venv:

```
# Full Windows packaging + lifecycle + release-contract suite (98 tests)
.venv\Scripts\python.exe -m pytest `
  tests/test_windows_packaging.py tests/test_windows_import_compat.py tests/test_release_metadata.py `
  tests/test_release_distribution.py::test_release_workflow_materializes_web_before_python_bundle_tests `
  tests/test_release_distribution.py::test_ci_python_job_materializes_web_before_python_tests `
  tests/test_release_distribution.py::test_workflow_if_conditions_never_reference_secrets `
  tests/test_release_distribution.py::test_role_installers_avoid_ambiguous_and_or_guards `
  tests/test_release_distribution.py::test_role_locks_and_release_workflow_are_versioned_inputs `
  tests/test_release_distribution.py::test_release_locks_keep_intel_macos_cryptography_wheel `
  tests/test_release_distribution.py::test_release_metadata_android_signer_sha256_is_validated `
  tests/test_release_distribution.py::test_release_tag_contract_accepts_only_canonical_distribution_tag `
  tests/test_release_distribution.py::test_release_workflow_android_signing_is_fail_closed `
  tests/test_release_distribution.py::test_release_workflow_publish_is_tag_push_only -q
# => 98 passed

# Web client
npm --prefix web ci                       # ok
npm --prefix web run build                # ok (web@3.0.0)
npm --prefix web run lint                 # ok
npm --prefix web run test:reliability     # ok (native pager bridge tests passed, etc.)

# Android (JDK 17)
cd android-native
gradlew.bat testDebugUnitTest             # BUILD SUCCESSFUL in 23s
gradlew.bat lintDebug                     # BUILD SUCCESSFUL in 2m 27s
gradlew.bat assembleRelease               # (unsigned validation build; see limitation below)
```

### Windows packaging pipeline (with pinned uv 0.11.16)

`build.ps1` was exercised with the pinned `uv.exe` (downloaded from the uv
release for the version `deploy/uv-version.txt` pins):

```
powershell -NoProfile -ExecutionPolicy Bypass -File packaging\windows\build.ps1 `
  -SourceRoot . -OutputDir <tmp> -UvExe <pinned uv.exe> `
  -GitSha 28b2c8a59b3e115b407994fede4e17e363a84da1 -SourceDateEpoch 0
```

Result: metadata validation passed, payload staged, uv bundled, and the
clean-install smoke ran. The smoke **failed on the dev machine by design** —
`FORBIDDEN_DEV_PATH_MARKERS` in `win_smoke.py` hardcodes the build machine's
username, and the smoke renders a first-run config whose workspace lives under
the builder's home temp directory, so a local build always trips the gate. The
same payload passes when the smoke's temp root is neutral (outside a user home
directory), and CI's runner user is not the developer's, so the gate is exactly
the "never ship a config with the builder's path" guard it is meant to be. The
ISCC step then fails closed locally because Inno Setup is not installed on this
machine.

Both archives were assembled from one staged payload (the two-invocation flow
`build.ps1` uses), extracted, and their payloads smoke-verified:

```
cc-remote-v3.0.0-pager.5-windows-x64.zip:           PASS (setup.ps1 at root)
cc-remote-v3.0.0-pager.5-windows-x64-portable.zip:  PASS (start-portable.ps1 at root)
```

## Honest limitations

- **The installer `.exe` was NOT compiled locally.** ISCC (Inno Setup) is not
  installed on this machine, and `build-installer.ps1` fails closed by design
  rather than emit a fake installer. The `.iss` and wrapper are validated by
  unit tests (`test_inno_installer_is_a_real_installer_and_build_fails_closed`,
  `test_build_ps1_produces_three_artifacts`) and the real exe is compiled on
  CI's windows-2025 runner (chocolatey `innosetup`). The exe's presence,
  non-trivial size, and `MZ` PE header are verified by `build-installer.ps1`
  immediately after compilation.
- **`build.ps1` did not run to completion locally** for the two reasons above
  (dev-machine smoke gate + no ISCC); its constituent steps (stage → manifest →
  smoke → both zips) were validated individually with the pinned uv, and the
  zips were smoke-verified after extraction. The full three-artifact run
  happens in CI.
- **Android `assembleRelease`** runs locally only as an unsigned validation
  build (no signing secrets on this machine — they must never leave the CI
  secret store). The exact `apksigner` SHA-256 gate is executed by the CI
  release job on tag pushes only.
- **CI workflows were not executed** from this branch in this session (no push
  was made while the branch was uncommitted, and the environment may be
  network-limited). The YAML is exercised by the release-contract unit tests
  (`test_release_workflow_*`, `test_ci_python_job_*`) that parse it.
- No push of the branch was confirmed at the time of writing; see the session
  report for the actual push result / any network limitation.
