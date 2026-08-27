<#
.SYNOPSIS
    Installs or upgrades the packaged cc-remote Windows distribution.

.DESCRIPTION
    The installer is transactional:

      1. Verifies the staged payload with the zero-token smoke suite
         (packaging\windows\win_smoke.py) BEFORE touching the target.
      2. Bootstraps the runtime venv with uv from payload\bin\uv.exe using
         deploy\python-version.txt and requirements.lock (no shipped .venv).
      3. Copies the payload into releases\<distribution-version> (immutable),
         switches the current junction, and wires a venv site-packages .pth so
         the app imports cc_remote from releases\current. The previous release
         is kept for rollback and recorded in releases\current.json.
      4. Config: on a fresh install runs config-first-run.ps1 (strong secrets,
         no placeholders). On an upgrade it validates the existing config with
         validate_preserved_config and keeps it byte-for-byte.
      5. Registers the cc-remote-relay and cc-remote-wrapper scheduled tasks
         whose actions are supervise.ps1 (bounded restart-on-failure, never
         untracked children).
      6. Adds a Windows Defender Firewall rule scoped to LocalSubnet and the
         selected relay port only.

    Nothing here reads or restarts any live instance. This installs into its
    own directory; it never touches %LOCALAPPDATA%\cc-remote from a different
    install or any pre-existing user instance.

.PARAMETER Payload
    Required: the extracted payload directory of the release (contains
    distribution-manifest.json and cc_remote\ at its top level).

.PARAMETER InstallRoot
    Install root. Defaults to %LOCALAPPDATA%\cc-remote at runtime.

.PARAMETER Unattended
    Do not prompt; fail on any missing/invalid first-run answer.

.PARAMETER LoginPassword
    Web login password (first run only).

.PARAMETER MachineName
    Machine id (first run only).

.PARAMETER Workspace
    Default workspace (first run only).

.PARAMETER PublicOrigin
    PUBLIC_ORIGIN (first run only).

.PARAMETER RelayPort
    Relay port (default 8765).

.PARAMETER AllowInsecureHttp
    Permit plain-http LAN origins (first run only). Default on.

.PARAMETER ConfigFile
    Seed file for first-run non-secret values.

.PARAMETER NoServices
    Skip scheduled tasks and firewall (portable-style install of the release).

.PARAMETER NoFirewall
    Skip the firewall rule (kept for installations that manage the firewall
    out-of-band). The default rule is LocalSubnet-scoped.

.EXAMPLE
    & install.ps1 -Payload C:\release\payload -InstallRoot C:\Users\alice\cc-remote -Unattended -LoginPassword "a-strong-16-char-password" -MachineName desktop-1 -PublicOrigin http://192.168.1.50:8765
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Payload,
    [string]$InstallRoot,
    [switch]$Unattended,
    [string]$LoginPassword = "",
    [string]$MachineName = "",
    [string]$Workspace = "",
    [string]$PublicOrigin = "",
    [int]$RelayPort = 8765,
    [switch]$AllowInsecureHttp,
    [string]$ConfigFile = "",
    [switch]$NoServices,
    [switch]$NoFirewall
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version 2.0

function Write-Step { param([string]$Message) Write-Host "[cc-remote] $Message" -ForegroundColor Cyan }
function Invoke-Check { param([string]$Message) Write-Host "[cc-remote] $Message" }

if (-not $InstallRoot) { $InstallRoot = Join-Path $env:LOCALAPPDATA "cc-remote" }
$payload = [System.IO.Path]::GetFullPath($Payload).TrimEnd('\')
$installRootFull = [System.IO.Path]::GetFullPath($InstallRoot).TrimEnd('\')

# --- Layout ----------------------------------------------------------------
$configDir = Join-Path $installRootFull "config"
$logsDir = Join-Path $installRootFull "logs"
$stateDir = Join-Path $installRootFull "state"
$releasesDir = Join-Path $installRootFull "releases"
$runtimeDir = Join-Path $installRootFull "runtime"
$venvDir = Join-Path $runtimeDir ".venv"
$venvPython = Join-Path $venvDir "Scripts\python.exe"
$currentJson = Join-Path $releasesDir "current.json"
$envPath = Join-Path $configDir ".env"

foreach ($dir in @($configDir, $logsDir, $stateDir, $releasesDir, $runtimeDir)) {
    New-Item -ItemType Directory -Force -Path $dir | Out-Null
}

# --- Resolve uv and a python for the smoke suite ----------------------------
$uvExe = Join-Path $payload "bin\uv.exe"
if (-not (Test-Path $uvExe)) {
    $uvExe = (Get-Command uv -ErrorAction SilentlyContinue).Source
}
if (-not $uvExe) { throw "bundled uv.exe missing in payload and no uv on PATH" }

$pythonVersion = (Get-Content (Join-Path $payload "deploy\python-version.txt") -ErrorAction Stop | Select-Object -First 1).Trim()

function Invoke-HostPython {
    # Prefer the runtime venv; fall back to any system python for the smoke
    # suite (pure stdlib, so any Python 3.9+ works for verification).
    param([string[]]$Arguments)
    if (Test-Path $venvPython) {
        & $venvPython @Arguments 2>&1
        return $LASTEXITCODE
    }
    foreach ($candidate in @((Get-Command py -ErrorAction SilentlyContinue).Source, (Get-Command python -ErrorAction SilentlyContinue).Source)) {
        if (-not $candidate) { continue }
        & $candidate @Arguments 2>&1
        if ($LASTEXITCODE -eq 0) { return 0 }
    }
    return 1
}

# --- 1. Verify the payload before touching the target -----------------------
Write-Step "Verifying staged payload at $payload"
$smoke = Join-Path $PSScriptRoot "win_smoke.py"
if (-not (Test-Path $smoke)) { throw "win_smoke.py not found next to install.ps1" }
$verifyArgs = @($smoke, "--check", $payload)
$verifyCode = Invoke-HostPython $verifyArgs
if ($verifyCode -ne 0) {
    throw "payload verification failed; refusing to install (see errors above)"
}
$manifest = Get-Content (Join-Path $payload "distribution-manifest.json") -Raw | ConvertFrom-Json
$distributionVersion = $manifest.distribution_version
Write-Step "Payload verified: cc-remote v$($manifest.product_version) (protocol v$($manifest.protocol)) distribution $distributionVersion"

# --- 2. Bootstrap / re-sync the runtime venv --------------------------------
Write-Step "Bootstrapping runtime venv with uv $($uvExe)"
& $uvExe python install $pythonVersion 2>&1 | Out-Host
if ($LASTEXITCODE -ne 0) { throw "uv python install failed for $pythonVersion" }

if (-not (Test-Path $venvPython)) {
    & $uvExe venv $venvDir --python $pythonVersion 2>&1 | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "uv venv creation failed" }
}
& $uvExe pip install --python $venvPython --requirement (Join-Path $payload "requirements.lock") 2>&1 | Out-Host
if ($LASTEXITCODE -ne 0) { throw "uv pip install failed" }

# --- 3. Copy the payload into an immutable release and switch current -------
Write-Step "Installing release $distributionVersion"
$releaseDir = Join-Path $releasesDir $distributionVersion
if (Test-Path $releaseDir) {
    Remove-Item -Recurse -Force -Confirm:$false $releaseDir
}
& $venvPython (Join-Path $PSScriptRoot "win_manifest.py") --copy --source $payload --destination $releaseDir 2>&1 | Out-Host
if ($LASTEXITCODE -ne 0) { throw "failed to stage release into $releaseDir" }

# Record previous release for rollback, then switch the current junction.
$previous = ""
if (Test-Path $currentJson) {
    try { $previous = (Get-Content $currentJson -Raw | ConvertFrom-Json).version } catch { }
}
if (Test-Path (Join-Path $releasesDir "current")) {
    Remove-Item -Path (Join-Path $releasesDir "current") -Force -Confirm:$false -ErrorAction SilentlyContinue
}
New-Item -ItemType Junction -Path (Join-Path $releasesDir "current") -Target $releaseDir | Out-Null
@{ version = $distributionVersion; previous = $previous } | ConvertTo-Json | Set-Content -Path $currentJson -Encoding utf8

# Make the cc_remote package importable by the runtime venv from the current
# release. A site-packages .pth file (not PYTHONPATH) keeps the app's sys.path
# minimal and versioned. The release root IS the payload content (win_manifest
# --copy copies payload/* -> releases\<version>/*), so the .pth adds
# releases\current; the junction retargets it on upgrade and rollback. The
# release tree contains no ``packaging`` package, so the venv's installed
# ``packaging`` distribution is never shadowed by the app's sys.path.
$sitePackages = Join-Path $venvDir "Lib\site-packages"
New-Item -ItemType Directory -Force -Path $sitePackages | Out-Null
Set-Content -Path (Join-Path $sitePackages "cc_remote_release.pth") -Value (Join-Path $releasesDir "current") -Encoding ascii

# --- 4. Config: preserve on upgrade, first-run wizard on fresh install ------
if (Test-Path $envPath) {
    Write-Step "Preserving existing config at $envPath"
    $preserveArgs = @((Join-Path $PSScriptRoot "win_config.py"), "validate-preserved", "--file", $envPath)
    $preserveCode = Invoke-HostPython $preserveArgs
    if ($preserveCode -ne 0) {
        throw "existing config failed the preserved-config gate; refusing to upgrade (fix or back up config\.env)"
    }
} else {
    Write-Step "No config found; running the first-run wizard"
    $wizardArgs = @(
        "-File", (Join-Path $PSScriptRoot "config-first-run.ps1"),
        "-VenvPython", $venvPython,
        "-InstallRoot", $installRootFull,
        "-RelayPort", "$RelayPort"
    )
    if ($Unattended) { $wizardArgs += "-Unattended" }
    if ($LoginPassword) { $wizardArgs += "-LoginPassword"; $wizardArgs += $LoginPassword }
    if ($MachineName) { $wizardArgs += "-MachineName"; $wizardArgs += $MachineName }
    if ($Workspace) { $wizardArgs += "-Workspace"; $wizardArgs += $Workspace }
    if ($PublicOrigin) { $wizardArgs += "-PublicOrigin"; $wizardArgs += $PublicOrigin }
    if ($AllowInsecureHttp) { $wizardArgs += "-AllowInsecureHttp" }
    if ($ConfigFile) { $wizardArgs += "-ConfigFile"; $wizardArgs += $ConfigFile }
    & powershell.exe $wizardArgs
    if ($LASTEXITCODE -ne 0) {
        # First-run failed; roll the release back so a broken config never stays.
        if ($previous -and (Test-Path (Join-Path $releasesDir "current"))) {
            Remove-Item -Path (Join-Path $releasesDir "current") -Force -Confirm:$false -ErrorAction SilentlyContinue
            New-Item -ItemType Junction -Path (Join-Path $releasesDir "current") -Target (Join-Path $releasesDir $previous) | Out-Null
        }
        throw "first-run configuration failed; install aborted (no config was written)"
    }
}

# Read the effective relay port for the firewall rule (the wizard may have
# changed it from -RelayPort).
$effectivePort = $RelayPort
foreach ($line in (Get-Content $envPath -ErrorAction SilentlyContinue)) {
    if ($line -match '^\s*RELAY_PORT\s*=\s*(\d+)\s*$') { $effectivePort = [int]$matches[1] }
}

# --- 5. Scheduled tasks (unless -NoServices) --------------------------------
# Registration is delegated to register-tasks.ps1 (also used by
# uninstall.ps1 -Rollback) so the two paths can never drift.
if (-not $NoServices) {
    if (-not (Get-Command Register-ScheduledTask -ErrorAction SilentlyContinue)) {
        throw "Task Scheduler module is not available on this machine; re-run with -NoServices"
    }
    Write-Step "Registering supervised scheduled tasks"
    & (Join-Path $PSScriptRoot "register-tasks.ps1") -InstallRoot $installRootFull
    if ($LASTEXITCODE -ne 0) { Write-Host "[cc-remote] warning: scheduled tasks registered but could not start" -ForegroundColor Yellow }
} else {
    Write-Step "Skipping scheduled tasks (-NoServices); run start.ps1 -AsService later if needed"
}

# --- 6. Firewall (unless -NoFirewall or -NoServices) ------------------------
if (-not $NoServices -and -not $NoFirewall) {
    Write-Step "Configuring LocalSubnet firewall rule for TCP $effectivePort"
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "firewall.ps1") -Port $effectivePort -InstallRoot $installRootFull
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[cc-remote] warning: firewall rule could not be added (may require an elevated shell)" -ForegroundColor Yellow
    }
} elseif (-not $NoServices -and $NoFirewall) {
    Write-Step "Skipping firewall rule (-NoFirewall)"
}

Write-Step "Install complete: cc-remote v$($manifest.product_version) -> $installRootFull"
if (Test-Path $envPath) {
    $machineId = ""
    $origin = ""
    foreach ($line in (Get-Content $envPath)) {
        if ($line -match '^\s*CC_REMOTE_MACHINE_ID\s*=\s*(.*)$') { $machineId = $matches[1].Trim('"') }
        if ($line -match '^\s*PUBLIC_ORIGIN\s*=\s*(.*)$') { $origin = $matches[1].Trim('"') }
    }
    Write-Step "Machine id: $machineId  |  Public origin: $origin"
}
Write-Step "Logs: $logsDir  |  Config: $envPath (restricted to $env:USERNAME only)"
exit 0
