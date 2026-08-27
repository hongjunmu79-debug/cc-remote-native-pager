<#
.SYNOPSIS
    Uninstalls cc-remote, or rolls an upgrade back to the previous release.

.DESCRIPTION
    Always: stops the supervised services (stop markers + task stop/disable) and
    removes the LocalSubnet firewall rule for the configured relay port. The
    scheduled tasks themselves are only unregistered on a real uninstall, never
    before the rollback decision, because -Rollback re-creates and re-starts
    them.

    Then, depending on the mode:

    * default: unregisters the tasks, removes the current release and its
      junction. Config, state and logs are preserved (reinstall keeps them).
      -Purge removes them too.
    * -Rollback: a failure-safe transaction that (1) re-syncs the runtime venv
      to the previous release's pinned requirements.lock (creating the venv if
      needed), (2) switches the current junction back to that release, (3)
      re-creates and starts the supervised tasks, and (4) health-checks them.
      If any step fails, the failed-active release is restored (junction, venv,
      tasks) and the error is rethrown.

.PARAMETER InstallRoot
    Install root. Defaults to %LOCALAPPDATA%\cc-remote at runtime.

.PARAMETER Rollback
    Restore the previous release instead of removing the current one.

.PARAMETER Purge
    Also delete config, state and logs (irreversible).

.EXAMPLE
    & .\uninstall.ps1                  # remove current release, keep data
    & .\uninstall.ps1 -Rollback        # revert to the previous release
    & .\uninstall.ps1 -Purge           # remove the whole install root
#>
[CmdletBinding()]
param(
    [string]$InstallRoot,
    [switch]$Rollback,
    [switch]$Purge
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version 2.0

function Write-Step { param([string]$Message) Write-Host "[cc-remote] $Message" -ForegroundColor Cyan }

if (-not $InstallRoot) { $InstallRoot = Join-Path $env:LOCALAPPDATA "cc-remote" }
$installRootFull = [System.IO.Path]::GetFullPath($InstallRoot).TrimEnd('\')
if (-not (Test-Path $installRootFull)) {
    Write-Step "Install root $installRootFull does not exist; nothing to do"
    exit 0
}

$releasesDir = Join-Path $installRootFull "releases"
$currentJson = Join-Path $releasesDir "current.json"
$currentLink = Join-Path $releasesDir "current"
$venvDir = Join-Path $installRootFull "runtime\.venv"
$venvPython = Join-Path $venvDir "Scripts\python.exe"

# --- Always stop services and remove firewall rules --------------------------
& (Join-Path $PSScriptRoot "stop.ps1") -InstallRoot $installRootFull | Out-Host

$envPath = Join-Path $installRootFull "config\.env"
$port = 8765
foreach ($line in (Get-Content $envPath -ErrorAction SilentlyContinue)) {
    if ($line -match '^\s*RELAY_PORT\s*=\s*(\d+)\s*$') { $port = [int]$matches[1] }
}
& (Join-Path $PSScriptRoot "firewall.ps1") -Port $port -InstallRoot $installRootFull -Remove 2>&1 | Out-Null
Write-Step "Stopped services and removed the firewall rule for TCP $port"

# The scheduled tasks are deliberately NOT unregistered here: -Rollback needs
# them (register-tasks.ps1 re-creates and starts them). Only a real uninstall
# unregisters them below.

# --- Determine the active and previous releases ------------------------------
$activeVersion = ""
$previousVersion = ""
if (Test-Path $currentJson) {
    try {
        $state = Get-Content $currentJson -Raw | ConvertFrom-Json
        $activeVersion = [string]$state.version
        $previousVersion = [string]$state.previous
    } catch { }
}
$activeDir = ""
if (Test-Path $currentLink) {
    try { $activeDir = (Get-Item $currentLink).Target } catch { }
    if (-not $activeDir) { $activeDir = $currentLink }
}
if (-not $activeVersion -and $activeDir) { $activeVersion = Split-Path $activeDir -Leaf }

# --- Failure-safe restore helper ----------------------------------------------
function Restore-ActiveRelease {
    # Puts the failed-active release back as current and re-syncs the venv to
    # it, so a failed rollback leaves the machine on the release it was on.
    if (Test-Path $currentLink) { Remove-Item -Path $currentLink -Force -Confirm:$false -ErrorAction SilentlyContinue }
    if ($activeDir -and (Test-Path $activeDir)) {
        New-Item -ItemType Junction -Path $currentLink -Target $activeDir | Out-Null
    }
    @{ version = $activeVersion; previous = $previousVersion } | ConvertTo-Json | Set-Content -Path $currentJson -Encoding utf8
    $uv = Join-Path $activeDir "bin\uv.exe"
    if ((Test-Path $venvPython) -and $activeDir -and (Test-Path $uv)) {
        & $uv pip install --python $venvPython --requirement (Join-Path $activeDir "requirements.lock") 2>&1 | Out-Null
    }
}

function Test-SupervisedHealth {
    # Both supervised modules must be running under the runtime venv within the
    # deadline. The supervisor keeps the real process as its tracked child, so a
    # live python -m cc_remote.<module> is the health signal.
    $deadline = (Get-Date).AddSeconds(30)
    do {
        $healthy = $true
        foreach ($module in @("cc_remote.relay", "cc_remote.wrapper")) {
            $proc = Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" -ErrorAction SilentlyContinue |
                Where-Object { $_.CommandLine -and $_.CommandLine.Contains($module) }
            if (-not $proc) { $healthy = $false; break }
        }
        if ($healthy) { return $true }
        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $deadline)
    return $false
}

if ($Rollback) {
    if (-not $previousVersion -or -not (Test-Path (Join-Path $releasesDir $previousVersion))) {
        throw "no previous release to roll back to (current.json previous is empty or the release is missing)"
    }
    $prevDir = Join-Path $releasesDir $previousVersion
    $prevUv = Join-Path $prevDir "bin\uv.exe"
    $prevPythonVersion = ""
    if (Test-Path (Join-Path $prevDir "deploy\python-version.txt")) {
        $prevPythonVersion = (Get-Content (Join-Path $prevDir "deploy\python-version.txt") -ErrorAction Stop | Select-Object -First 1).Trim()
    }
    Write-Step "Rolling back to $previousVersion"
    try {
        # 1. Pinned venv re-sync to the previous release's lock. The junction
        #    is still on the failed-active release, so nothing has switched yet
        #    when this step fails.
        if (Test-Path $prevUv) {
            if ($prevPythonVersion) {
                & $prevUv python install $prevPythonVersion 2>&1 | Out-Host
                if ($LASTEXITCODE -ne 0) { throw "uv python install failed for $prevPythonVersion" }
            }
            if (-not (Test-Path $venvPython)) {
                & $prevUv venv $venvDir --python $prevPythonVersion 2>&1 | Out-Host
                if ($LASTEXITCODE -ne 0) { throw "uv venv creation failed" }
            }
            & $prevUv pip install --python $venvPython --requirement (Join-Path $prevDir "requirements.lock") 2>&1 | Out-Host
            if ($LASTEXITCODE -ne 0) { throw "uv pip install failed for the previous release lock" }
        } elseif (-not (Test-Path $venvPython)) {
            throw "previous release has no bundled uv.exe and no runtime venv exists"
        }

        # 2. Switch the current junction to the previous release and record it.
        if (Test-Path $currentLink) { Remove-Item -Path $currentLink -Force -Confirm:$false -ErrorAction SilentlyContinue }
        New-Item -ItemType Junction -Path $currentLink -Target $prevDir | Out-Null
        @{ version = $previousVersion; previous = "" } | ConvertTo-Json | Set-Content -Path $currentJson -Encoding utf8

        # 3. Re-create the supervised tasks (idempotent) and start them.
        & (Join-Path $PSScriptRoot "register-tasks.ps1") -InstallRoot $installRootFull
        if ($LASTEXITCODE -ne 0) { throw "failed to register or start the scheduled tasks" }

        # 4. Health-check the supervised services.
        if (-not (Test-SupervisedHealth)) { throw "supervised services did not come up within 30s" }

        Write-Step "Rollback complete; services restarted"
        exit 0
    } catch {
        Write-Host "[cc-remote] rollback failed: $($_.Exception.Message). Restoring the failed-active release." -ForegroundColor Yellow
        try {
            Restore-ActiveRelease
            & (Join-Path $PSScriptRoot "register-tasks.ps1") -InstallRoot $installRootFull 2>&1 | Out-Null
        } catch {
            Write-Host "[cc-remote] warning: could not fully restore the failed-active release: $($_.Exception.Message)" -ForegroundColor Yellow
        }
        throw
    }
}

# --- Default: real uninstall ---------------------------------------------------
Write-Step "Removing the current release ($activeVersion)"
foreach ($taskName in @("cc-remote-relay", "cc-remote-wrapper")) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
}
if (Test-Path $currentLink) { Remove-Item -Path $currentLink -Force -Confirm:$false -ErrorAction SilentlyContinue }
if ($activeDir -and (Split-Path $activeDir -Leaf) -and (Test-Path $activeDir)) {
    Remove-Item -Recurse -Force -Confirm:$false $activeDir
}
if (Test-Path $currentJson) { Remove-Item -Force -Confirm:$false $currentJson }
Write-Step "Removed the current release"

if ($Purge) {
    Write-Step "Purging config, state and logs (irreversible)"
    foreach ($dir in @("config", "state", "logs", "runtime")) {
        Remove-Item -Recurse -Force -Confirm:$false (Join-Path $installRootFull $dir)
    }
    Remove-Item -Recurse -Force -Confirm:$false $installRootFull
    Write-Step "Removed $installRootFull entirely"
} else {
    Write-Step "Kept config, state and logs under $installRootFull for reinstall"
}
Write-Step "Uninstall complete"
exit 0
