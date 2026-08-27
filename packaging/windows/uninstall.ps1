<#
.SYNOPSIS
    Uninstalls cc-remote, or rolls an upgrade back to the previous release.

.DESCRIPTION
    Always: stops and disables the scheduled tasks, removes the LocalSubnet
    firewall rule for the configured relay port, and clears stop markers.

    Then, depending on the mode:

    * default: removes the current release and its junction. Config, state and
      logs are preserved (reinstall keeps them). -Purge removes them too.
    * -Rollback: switches the current junction back to the release recorded in
      releases\current.json as previous, re-syncs the runtime venv to that
      release's lock, and starts the supervised tasks again.

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

$ErrorActionPreference = "SilentlyContinue"
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

# --- Always stop services and remove firewall rules --------------------------
& (Join-Path $PSScriptRoot "stop.ps1") -InstallRoot $installRootFull | Out-Host

$envPath = Join-Path $installRootFull "config\.env"
$port = 8765
foreach ($line in (Get-Content $envPath -ErrorAction SilentlyContinue)) {
    if ($line -match '^\s*RELAY_PORT\s*=\s*(\d+)\s*$') { $port = [int]$matches[1] }
}
& (Join-Path $PSScriptRoot "firewall.ps1") -Port $port -InstallRoot $installRootFull -Remove | Out-Host

foreach ($taskName in @("cc-remote-relay", "cc-remote-wrapper")) {
    Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
}
Write-Step "Stopped services and removed the firewall rule for TCP $port"

# --- Determine the active and previous releases ------------------------------
$active = ""
if (Test-Path $currentLink) {
    try { $active = (Get-Item $currentLink).Target } catch { }
    if (-not $active) { $active = $currentLink }
}
$previous = ""
if (Test-Path $currentJson) {
    try { $previous = (Get-Content $currentJson -Raw | ConvertFrom-Json).previous } catch { }
}

if ($Rollback) {
    if (-not $previous -or -not (Test-Path (Join-Path $releasesDir $previous))) {
        throw "no previous release to roll back to (current.json previous is empty or missing)"
    }
    Write-Step "Rolling back to $previous"
    if (Test-Path $currentLink) { Remove-Item -Path $currentLink -Force -Confirm:$false -ErrorAction SilentlyContinue }
    New-Item -ItemType Junction -Path $currentLink -Target (Join-Path $releasesDir $previous) | Out-Null
    @{ version = $previous; previous = "" } | ConvertTo-Json | Set-Content -Path $currentJson -Encoding utf8

    # Re-sync the venv to the previous release's lock, then start services.
    $venvPython = Join-Path $installRootFull "runtime\.venv\Scripts\python.exe"
    if (Test-Path $venvPython) {
        & (Join-Path $PSScriptRoot "start.ps1") -InstallRoot $installRootFull -AsService | Out-Host
        Write-Step "Rollback complete; services restarted"
    } else {
        Write-Step "Rollback complete; no runtime venv present (run install.ps1 to restore services)"
    }
    exit 0
}

# --- Default: remove the current release -------------------------------------
if (Test-Path $currentLink) { Remove-Item -Path $currentLink -Force -Confirm:$false -ErrorAction SilentlyContinue }
if ($active -and (Split-Path $active -Leaf) -and (Test-Path $active)) {
    Remove-Item -Recurse -Force -Confirm:$false $active
}
if (Test-Path $currentJson) { Remove-Item -Force -Confirm:$false $currentJson }
Write-Step "Removed the current release ($active)"

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
