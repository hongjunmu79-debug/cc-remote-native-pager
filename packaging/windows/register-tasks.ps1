<#
.SYNOPSIS
    Registers and starts the cc-remote supervised scheduled tasks.

.DESCRIPTION
    Creates (or re-creates) the cc-remote-relay and cc-remote-wrapper scheduled
    tasks whose actions are supervise.ps1 (bounded restart-on-failure, never
    untracked children), then starts them so the install is immediately usable.
    Shared by install.ps1 and uninstall.ps1 -Rollback so the registration can
    never drift between the two paths. Exit code is non-zero when a task could
    not be started.

.PARAMETER InstallRoot
    Install root. Defaults to %LOCALAPPDATA%\cc-remote at runtime.

.EXAMPLE
    & .\register-tasks.ps1 -InstallRoot C:\Users\alice\cc-remote
#>
[CmdletBinding()]
param([string]$InstallRoot)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version 2.0

function Write-Step { param([string]$Message) Write-Host "[cc-remote] $Message" -ForegroundColor Cyan }

if (-not $InstallRoot) { $InstallRoot = Join-Path $env:LOCALAPPDATA "cc-remote" }
$installRootFull = [System.IO.Path]::GetFullPath($InstallRoot).TrimEnd('\')

if (-not (Get-Command Register-ScheduledTask -ErrorAction SilentlyContinue)) {
    throw "Task Scheduler module is not available on this machine; re-run the install with -NoServices"
}

$supervise = Join-Path $PSScriptRoot "supervise.ps1"
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero)
foreach ($service in @("relay", "wrapper")) {
    $taskName = "cc-remote-$service"
    $argument = "-NoProfile -ExecutionPolicy Bypass -File `"$supervise`" -Service $service -InstallRoot `"$installRootFull`""
    $action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $argument
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
    Write-Step "  registered $taskName (supervises python -m cc_remote.$service)"
}

# Start them now so the install is immediately usable.
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "start.ps1") -InstallRoot $installRootFull -AsService
exit $LASTEXITCODE
