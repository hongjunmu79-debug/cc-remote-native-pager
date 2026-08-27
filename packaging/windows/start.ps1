<#
.SYNOPSIS
    Starts cc-remote. Portable mode runs the relay/wrapper in the foreground;
    installed mode enables and starts the scheduled-task supervisors.

.DESCRIPTION
    Two modes:

    * Portable (default): no scheduled tasks, no firewall rule. Runs the chosen
      process in the foreground of this console using the runtime venv. Ctrl+C
      stops it. Use this for evaluation or when you prefer manual control.

    * Installed (-AsService): enables the cc-remote-relay and cc-remote-wrapper
      scheduled tasks and starts them, so the supervised processes survive
      logout and crash-restart within the supervisor's bound.

    In both modes the runtime venv must already exist (install.ps1 creates it).

.PARAMETER Service
    Which process to run: "relay", "wrapper", or "both" (portable runs both in
    the foreground; the service tasks always cover both).

.PARAMETER InstallRoot
    Install root. Defaults to %LOCALAPPDATA%\cc-remote at runtime.

.PARAMETER AsService
    Use the installed scheduled-task supervisors instead of a foreground run.

.EXAMPLE
    & start.ps1 -Service both                  # portable: run both, foreground
    & start.ps1 -AsService                     # installed: start supervised tasks
#>
[CmdletBinding()]
param(
    [ValidateSet("relay", "wrapper", "both")][string]$Service = "both",
    [string]$InstallRoot,
    [switch]$AsService
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version 2.0

if (-not $InstallRoot) { $InstallRoot = Join-Path $env:LOCALAPPDATA "cc-remote" }

$venvPython = Join-Path $InstallRoot "runtime\.venv\Scripts\python.exe"
$configPath = Join-Path $InstallRoot "config\.env"

if (-not (Test-Path $venvPython)) { throw "runtime venv not found: $venvPython (run install.ps1 first)" }
if (-not (Test-Path $configPath)) { throw "config not found: $configPath (run install.ps1 first)" }

# Load the environment the same way the supervisor does.
foreach ($line in (Get-Content $configPath -ErrorAction SilentlyContinue)) {
    if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') {
        $key = $matches[1]
        $value = $matches[2].Trim().Trim('"').Trim("'")
        try { Set-Item -Path ("env:" + $key) -Value $value -ErrorAction Stop } catch { }
    }
}
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

if ($AsService) {
    foreach ($taskName in @("cc-remote-relay", "cc-remote-wrapper")) {
        $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        if (-not $task) { throw "scheduled task not found: $taskName (run install.ps1 first)" }
        Enable-ScheduledTask -TaskName $taskName | Out-Null
        Start-ScheduledTask -TaskName $taskName
        Write-Host "[cc-remote] started scheduled task $taskName"
    }
    exit 0
}

Write-Host "[cc-remote] portable mode (Ctrl+C to stop). Use -AsService for supervised startup." -ForegroundColor Yellow

if ($Service -in @("relay", "both")) {
    Write-Host "[cc-remote] starting relay ..."
    & $venvPython -m cc_remote.relay
}

if ($Service -in @("wrapper", "both")) {
    Write-Host "[cc-remote] starting wrapper ..."
    & $venvPython -m cc_remote.wrapper
}
