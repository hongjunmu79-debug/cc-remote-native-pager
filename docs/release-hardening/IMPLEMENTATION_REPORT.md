# Implementation report — CC Remote Pager production release hardening

**Date:** 2026-08-27
**Branch:** `codex/release-hardening`
**Base:** `c43a0d9bdf8f4158c7171df78fdea712add62468`
**Pushed implementation tip:** `0db4bd11bbf0c93fffba01e361d84119c8c21689`

This report records the implementation of the production-release hardening for
the cc-remote native pager. The parent agent performs acceptance; this document
separates completed checks from checks that require the parent's clean VM, ADB
device, signing key, certificate, or GitHub administrative action.

## Scope summary

| Scope | Result |
| --- | --- |
| A — canonical release metadata + repository identity | Implemented and verified. |
| B — reproducible Windows distribution | Implemented; Python suite green; install/rollback needs a clean-VM run. |
| C — generic Android endpoint + security | Implemented; unit tests + lint green; on-device verification pending. |
| D — CI and release workflow | Implemented; YAML validated; tag contract simulated; signing needs the real keystore. |
| E — documentation and handoff | Implemented. |

No wire-protocol change was made (still protocol 19). The live instance under
`%LOCALAPPDATA%\cc-remote` was not modified or restarted. No credentials,
`.env` values, cookies, signing material, keystores, or GitHub tokens were used
or exposed.

## Files changed

### Scope A — canonical release metadata and repository identity

- `deploy/release-metadata.json` (new) — canonical source: product `3.0.0`,
  distribution `3.0.0-pager.5`, protocol `19`, repo
  `hongjunmu79-debug/cc-remote-native-pager`, Android `dev.ccremote.lan` /
  `30014`.
- `deploy/release_metadata.py` (new) — typed loader/validator.
- `deploy/release_scan.py` (new) — forbidden-literal and high-confidence-secret
  scans; self-exempts its own definitions.
- `deploy/validate_release_metadata.py` (new) — end-to-end release gate.
- `deploy/build_release.py`, `deploy/release_manifest.py` — consume canonical
  metadata; `sys.path` bootstrap for script invocation.
- `deploy/install.sh` — VERSION/REPOSITORY defaults → distribution + public repo.
- `docs/UPSTREAM.md` (new) — provenance of `muggle-stack/cc-remote` and the
  pager adaptation; MIT attribution preserved.
- `docs/native-pager/ARCHITECTURE.md`, `docs/native-pager/DEPLOYMENT.md` —
  generic-endpoint + canonical-metadata refresh.
- `tests/test_release_metadata.py` (new), `tests/test_product_version.py`,
  `tests/test_release_distribution.py`, `tests/test_wrapper_core_fixes.py`
  (path sanitization) — metadata/identity/version/scan tests.

### Scope B — reproducible Windows distribution

- `packaging/__init__.py`, `packaging/windows/` — win_config, win_layout,
  win_manifest, win_smoke, win_build, build.ps1, install.ps1, setup.ps1,
  uninstall.ps1, start.ps1, stop.ps1, supervise.ps1, firewall.ps1,
  config-first-run.ps1, README.md.
- `tests/test_windows_packaging.py` (new) — 60 zero-token tests.

### Scope C — generic Android endpoint and security

- `android-native/app/build.gradle.kts` — source defaults from
  `deploy/release-metadata.json`; no stale literals.
- `android-native/.../AppPreferences.kt`, `PagerDashboard.kt`,
  `PagerViewModel.kt`, `SecureWebViewController.kt`,
  `res/xml/network_security_config.xml` — endpoint entry, HTTPS/private-HTTP
  rules, origin enforcement, cleartext scoping.
- `android-native/.../ServerEndpointTest.kt` — endpoint unit tests.

### Scope D — CI and release workflow

- `.github/workflows/ci.yml` (new) — push/PR gate.
- `.github/workflows/release.yml` — tag contract, source-built Windows/Android
  jobs, publish gating, SHA-256 + attestation, secret-driven Android signing.

### Scope E — documentation and handoff

- `README.md`, `README_en.md` — two-journey quickstart + operations.
- `CHANGELOG.md`, `CHANGELOG_zh.md` — `v3.0.0-pager.5` entry.
- `docs/release-hardening/RELEASE_MAINTAINER.md`,
  `docs/release-hardening/SECURITY.md`,
  `docs/release-hardening/DEV_HISTORY_INDEX.md` (new).
- `docs/release-hardening/IMPLEMENTATION_REPORT.md` (this document).

## Commits (branch `codex/release-hardening`)

```
0db4bd11bbf0c93fffba01e361d84119c8c21689 docs: two-journey quickstart and release documentation
d7ccbad85e831a0d764ce4058123469bb274b48f ci: push/PR gate and hardened tag release
1242fe3fdd6294f61c36f8c8a13deff227ecc527 fix(android): generic server endpoint and origin enforcement
9f18ef5dc23e6a5aecb7eca9ea5e732d0c6df085 feat(packaging): reproducible Windows distribution
d4f6a5310f8c1e3625bbf63684816602553017a2 feat(deploy): canonical release metadata and repo identity
```

## Tests and checks run locally (zero-token)

| Check | Result |
| --- | --- |
| `pytest tests/test_release_metadata.py tests/test_product_version.py tests/test_protocol_input_bounds.py tests/test_web_protocol_mirror.py tests/test_windows_import_compat.py tests/test_windows_packaging.py tests/test_auth.py tests/test_relay_forward.py` | 182 passed |
| `tests/test_windows_packaging.py` (Windows packaging suite) | 60/60 passed |
| `python deploy/validate_release_metadata.py --root .` | PASS — cc-remote 3.0.0, distribution 3.0.0-pager.5, protocol v19, dev.ccremote.lan vc30014 |
| `uv tool run --from ruff==0.15.13 ruff check cc_remote tests deploy` | All checks passed |
| `git diff --check` | clean |
| `git fsck --full` | healthy (only harmless dangling blobs) |
| Android `:app:testDebugUnitTest :app:lintDebug` | BUILD SUCCESSFUL |
| release.yml tag contract (simulated `v3.0.0-pager.5`, `v3.0.0`, `v9.9.9`) | ACCEPT / ACCEPT / REJECT |
| Both workflow YAMLs (PyYAML parse) | valid |
| All third-party action SHA pins vs claimed tags (`gh api`) | all match |
| release.yml heredoc inline Python | compiles |
| Push of `codex/release-hardening` to origin | succeeded |

### Pre-existing Windows-only failures (not regressions)

The following `tests/test_release_distribution.py` failures occur on this
Windows checkout and reproduce identically at the base commit `c43a0d9`
(verified in a clean worktree): bundle-build tests fail on the POSIX
executable-bit check of the fake `uv` (Windows `stat().st_mode` reports no
`0o111` bits); bootstrap/installer tests fail because WSL `bash` mangles the
Windows path. These pass on the Ubuntu CI runners.

## Scope B/C evidence and what still needs the parent agent

- Windows packaging tests prove: no fixed usernames, no fixed old LAN IP, no
  `.venv` relocation dependency, no mandatory Node dependency; config rejects
  placeholders and preserves existing config on upgrade.
- Android endpoint unit tests cover `10/8`, `172.16/12`, `192.168/16`,
  loopback, HTTPS, and rejected public HTTP.
- Release workflow cannot publish after any failed gate (`publish` `needs:`
  all build jobs, which `needs: verify`).

### Still requires the parent agent / external action

- Clean-VM Windows install, upgrade (config preservation), rollback, uninstall,
  scheduled-task supervision, and LocalSubnet firewall verification.
- On-device Android verification with a real ADB device (first-launch endpoint
  entry, bridge origin/resource enforcement, IME handling, terminal-card
  centering, engine filters, writer handoff).
- Android release signing: the keystore/alias/passwords are **not** in this
  repository. Provision `PAGER_KEYSTORE_B64` and `PAGER_SIGNING_PROPERTIES` as
  secrets, or sign locally with the existing certificate — never substitute a
  different key. Verify signer SHA-256 parity before distributing.
- Windows Authenticode signing (optional, CI-secret-driven); local builds are
  documented as unsigned.
- GitHub administrative actions: repository secrets, branch protection,
  environment approval, final release publication rights.
- Live CI runs on GitHub (this environment could only simulate the tag
  contract and validate the YAML/action pins locally).

## Limitations

- The packaging Python modules are intentionally dependency-free and are **not**
  in CI's `ruff` scope (`ruff check cc_remote tests deploy`); `packaging/` is
  isolated from runtime/domain code by design.
- GitHub Actions could not be executed here; the workflow files were validated
  structurally (YAML parse, action SHA verification against upstream tags,
  heredoc Python compile, tag-contract simulation) and must be confirmed by a
  real run.
- Protocol 19 is unchanged; all three tiers must still be upgraded together in
  one maintenance window.
