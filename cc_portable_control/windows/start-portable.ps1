<#
.SYNOPSIS
    Starts cc-remote from the portable archive (no installation required).

.DESCRIPTION
    The portable distribution is a plain folder you extract anywhere and run
    from the extracted root. On first use this script creates the private
    runtime venv next to it (runtime\.venv) with the bundled uv.exe and the
    pinned requirements.lock, wires the payload tree into the venv's import
    path, and ensures config\.env via config-first-run.ps1. It then delegates
    to the shared start.ps1 in portable (foreground) mode.

    Nothing is registered on the machine: no scheduled tasks, no firewall rule,
    no registry values. Delete the folder to uninstall.

    First run needs network access (uv downloads the pinned Python and the
    locked wheels once); later runs are offline.

.PARAMETER Service
    Which process to run: "relay", "wrapper", or "both".

.PARAMETER ConfigFile
    Optional seed file (.env-style key=value or JSON) whose non-secret values
    prefill the first-run wizard. Secrets are never read from it.

.EXAMPLE
    .\start-portable.ps1                  # run relay + wrapper, foreground
    .\start-portable.ps1 -Service relay   # run only the relay
#>
[CmdletBinding()]
param(
    [ValidateSet("relay", "wrapper", "both")][string]$Service = "both",
    [string]$ConfigFile = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version 2.0

function Write-Step { param([string]$Message) Write-Host "[cc-remote] $Message" -ForegroundColor Cyan }

$root = $PSScriptRoot
$payload = Join-Path $root "payload"
$venvDir = Join-Path $root "runtime\.venv"
$venvPython = Join-Path $venvDir "Scripts\python.exe"
$uvExe = Join-Path $payload "bin\uv.exe"
$pythonVersionFile = Join-Path $payload "deploy\python-version.txt"
$requirementsLock = Join-Path $payload "requirements.lock"
$packagingDir = Join-Path $root "cc_portable_control\windows"
$startScript = Join-Path $packagingDir "start.ps1"
$configFirstRun = Join-Path $packagingDir "config-first-run.ps1"

# --- Validate the extracted tree ---------------------------------------------
foreach ($required in @($payload, $uvExe, $pythonVersionFile, $requirementsLock, $startScript, $configFirstRun)) {
    if (-not (Test-Path $required)) { throw "portable distribution is incomplete; missing $required" }
}
if (-not (Test-Path (Join-Path $payload "distribution-manifest.json"))) {
    throw "portable distribution is not a verified release (payload\distribution-manifest.json is missing)"
}

# --- Bootstrap the private runtime venv on first use --------------------------
if (-not (Test-Path $venvPython)) {
    Write-Step "First run: creating the private runtime venv (network required)"
    $pythonVersion = (Get-Content $pythonVersionFile | Select-Object -First 1).Trim()
    & $uvExe venv $venvDir --python $pythonVersion --python-downloads auto
    if ($LASTEXITCODE -ne 0) { throw "failed to create the runtime venv ($venvDir)" }
    & $uvExe pip install --python $venvPython --requirement $requirementsLock
    if ($LASTEXITCODE -ne 0) { throw "failed to sync pinned requirements into the runtime venv" }

    # Make `python -m cc_remote.*` resolve to this folder, never to a global
    # install. The .pth lives inside the private venv's site-packages, so the
    # whole runtime is self-contained and deleting the folder removes it.
    $sitePackages = Join-Path $venvDir "Lib\site-packages"
    New-Item -ItemType Directory -Force -Path $sitePackages | Out-Null
    Set-Content -Path (Join-Path $sitePackages "cc_remote_portable.pth") -Value $payload -Encoding ascii
}

# --- Ensure config ------------------------------------------------------------
$envPath = Join-Path $root "config\.env"
if (-not (Test-Path $envPath)) {
    Write-Step "No config yet; running the first-run wizard"
    $wizardArgs = @(
        "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", $configFirstRun,
        "-VenvPython", $venvPython,
        "-InstallRoot", $root,
        "-StaticDir", (Join-Path $payload "web\dist")
    )
    if ($ConfigFile) { $wizardArgs += "-ConfigFile"; $wizardArgs += $ConfigFile }
    & powershell.exe $wizardArgs
    if ($LASTEXITCODE -ne 0) { throw "first-run configuration failed (exit $LASTEXITCODE)" }
}

# --- Delegate to the shared start script (portable foreground mode) -----------
& $startScript -Service $Service -InstallRoot $root
exit $LASTEXITCODE
