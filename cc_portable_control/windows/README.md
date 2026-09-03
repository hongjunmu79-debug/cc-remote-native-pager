# cc-remote Windows packaging

A tracked, documented, reproducible Windows distribution for the two-journey
model: relay + wrapper on one LAN machine, driven from a phone/browser (and the
native Android pager). The distribution is built, verified, and installed with
zero token cost to the end user.

## What this project contains

| File | Purpose |
| --- | --- |
| `win_config.py` | Pure first-run config logic: validation, dotenv rendering, secret policy, preserved-config gate. Runs under any Python 3.9+; the PowerShell wizard and the unit tests share it. |
| `win_layout.py` | Install layout (`config`, `state`, `logs`, `releases`, `runtime`), icacls ACL commands, checksum helpers. |
| `win_manifest.py` | Distribution manifest (per-file SHA-256), verify/build/copy/stage commands, `.venv` and symlink rejection. |
| `win_smoke.py` | Zero-token clean-install smoke suite: verify payload → no `.venv` → render config → refuse placeholders → preserve on upgrade → no dev-machine paths. |
| `win_build.py` | Deterministic release archive (fixed timestamps, sorted entries). |
| `config-first-run.ps1` | Interactive/unattended first-run wizard. Generates strong secrets, renders `.env`, restricts ACLs. |
| `install.ps1` | Transactional install/upgrade: verify → bootstrap venv → immutable release + `current` junction → config → scheduled tasks → firewall. |
| `setup.ps1` | Release-archive entry point; verifies the payload then calls `install.ps1`. |
| `uninstall.ps1` | Stop + uninstall, or `-Rollback` to the previous release, or `-Purge`. |
| `start.ps1` / `stop.ps1` | Foreground (portable) run and supervised-service control. |
| `supervise.ps1` | Scheduled-task action: parents the real process, bounded restart-on-failure, stop markers. |
| `firewall.ps1` | LocalSubnet-scoped inbound rule for the relay port only. |
| `open-console.ps1` | Starts registered tasks if needed and opens the loopback Web console used to issue a QR. |

## How a release is built

```powershell
# From a checked-out source tree (web/dist must already be built):
.\cc_portable_control\windows\build.ps1 -SourceRoot <repo> -UvExe <path\to\uv.exe>
# Produces dist\cc-remote-v<distribution-version>-windows-x64.zip + .sha256
```

`build.ps1` validates the canonical metadata against the backend, bundles a
pinned `uv.exe`, stages only the runtime tree, hashes every file into
`distribution-manifest.json`, runs the smoke suite, and assembles the archive
with a fixed `SOURCE_DATE_EPOCH`. Identical git SHA + epoch + sources produce a
byte-identical zip.

## How a user installs

```powershell
# Recommended: download and double-click the setup.exe from GitHub Releases.
# It includes pinned Python and dependencies; no system Python or package
# download is required during first install.
# Archive fallback (also zero-config):
Expand-Archive cc-remote-v3.0.0-pager.7-windows-x64.zip
cd cc-remote-v3.0.0-pager.7-windows-x64
.\setup.ps1 -Unattended -AllowInsecureHttp
```

The installer:

1. **Verifies** the payload with the smoke suite before touching anything.
2. **Bootstraps** the runtime venv with the bundled uv and the pinned
   `requirements.lock` (hashed). A machine-created `.venv` is never shipped.
3. Copies the payload into `releases\<version>` (immutable) and switches the
   `current` junction. The previous release is kept for rollback.
4. On a fresh install, runs `config-first-run.ps1` (strong secrets, no
   placeholders). On an upgrade, validates the existing `.env` with the
   preserved-config gate and keeps it byte-for-byte.
5. Registers `cc-remote-relay` / `cc-remote-wrapper` scheduled tasks whose
   action is `supervise.ps1` (bounded restart-on-failure, never untracked
   children).
6. Adds a firewall rule for **LocalSubnet** and the relay port only.

The compiled `...-windows-x64-setup.exe` supplies safe unattended defaults,
creates Start Menu/Desktop **cc-remote 控制台** shortcuts, and opens the
console after install. `LOGIN_PASSWORD` is optional; set it only when a password
fallback is desired in addition to QR pairing.

The first-run selector follows the active private default-gateway adapter
instead of the first RFC1918 address returned by Windows, so WSL/Hyper-V and VPN
interfaces do not leak into the phone QR. The local wrapper always connects to
the relay over `127.0.0.1`; only the QR uses the selected LAN address.

`-NoServices` performs a portable-style install (release + config + venv, no
tasks, no firewall); `start.ps1` then runs the processes in the foreground.

## Layout

```
%LOCALAPPDATA%\cc-remote\
  config\.env                  # secrets; restricted to the installing user
  releases\<version>\          # immutable releases
  releases\current             # junction to the active release
  releases\current.json        # {version, previous} for rollback
  runtime\.venv\               # uv-created venv (never shipped)
  state\                       # cc-remote state + work roots
  logs\relay.log, wrapper.log  # supervised-process logs
```

## Why a repo-local `cc_portable_control` package

The build/install toolchain lives at the repo root under `cc_portable_control/windows/`.
That directory name collides with the PyPI `packaging` distribution that the
backend's dependencies pull into the venv. To keep `import cc_portable_control.windows`
resolving (a regular package wins over a namespace package at the same
`sys.path` position), the archive embeds `cc_portable_control/__init__.py` so
the extracted `cc_portable_control` is a *regular* package.

Consequence: in any process whose `sys.path[0]` is the release tree, the
repo-local `cc_portable_control` package is used from the release tree. This is contained:

- The **app processes** (relay/wrapper) never have the release tree on
  `sys.path`. `install.ps1` wires a venv site-packages `.pth` that adds only
  `releases\current\payload`, and `payload` contains no `packaging` package —
  so the venv's installed `packaging` distribution (used by pydantic etc.) is
  never shadowed.
- The **installer script processes** (`win_manifest.py --copy`, `win_smoke.py
  --check`, ...) do insert the release root at `sys.path[0]`, but they only
  import `cc_portable_control.windows.*` and never the PyPI distribution.

If the toolchain ever needs PyPI `packaging` in the same process as
`cc_portable_control.windows`, rename the repo-local package rather than importing
both.

## Security posture

- Secrets are generated by a CSPRNG and never appear on a command line; they
  cross process boundaries via the environment.
- `validate_login_password` / `validate_preserved_config` refuse placeholders.
- `config\` gets `/inheritance:r` + `/grant:r <user>` (no deny entry — a deny
  on BUILTIN\Users would lock the principal out of their own files).
- Firewall scope is `LocalSubnet`, Private/Domain profiles only, bound to the
  relay port and the venv `python.exe`.
- The web login path, WS upgrade bearer token, and cookie session are the same
  URL-secret-free auth as the server distribution; see `docs/` for the full
  security notes.

## Testing

The Python modules are pure and zero-token. From the repo root:

```powershell
$env:PYTHONUTF8 = "1"          # avoids GBK console issues on Windows
python -m pytest tests/test_windows_packaging.py -q
python cc_portable_control\windows\win_smoke.py --check <staged-payload>
```
