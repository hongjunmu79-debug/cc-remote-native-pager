<#
.SYNOPSIS
    Supervises the real cc-remote relay or wrapper process under Task Scheduler.

.DESCRIPTION
    This is the action of the cc-remote-relay / cc-remote-wrapper scheduled
    tasks. It stays alive as the parent of the real long-lived process (it never
    spawns untracked children and exits) and restarts the process on crash with
    a backoff, bounded by -MaxRestarts within a sliding -WindowSeconds window.
    After the bound is exceeded it exits non-zero, leaving the task stopped
    until a human runs start.ps1.

    A stop marker file (state\stop.<service>) is honored at startup so
    stop.ps1 can reliably keep the task from flapping, and checked between
    restarts so a stop lands promptly.

.PARAMETER Service
    Which process to supervise: "relay" or "wrapper".

.PARAMETER InstallRoot
    Install root. Defaults to %LOCALAPPDATA%\cc-remote at runtime.

.PARAMETER MaxRestarts
    Maximum restarts per window before giving up. Default 5.

.PARAMETER WindowSeconds
    Sliding window for the restart budget. Default 300.

.PARAMETER BackoffSeconds
    Sleep between restart attempts. Default 5.

.EXAMPLE
    & supervise.ps1 -Service relay -InstallRoot C:\Users\alice\cc-remote
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][ValidateSet("relay", "wrapper")][string]$Service,
    [string]$InstallRoot,
    [int]$MaxRestarts = 5,
    [int]$WindowSeconds = 300,
    [int]$BackoffSeconds = 5
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version 2.0

if (-not $InstallRoot) { $InstallRoot = Join-Path $env:LOCALAPPDATA "cc-remote" }

$venvPython = Join-Path $InstallRoot "runtime\.venv\Scripts\python.exe"
$configPath = Join-Path $InstallRoot "config\.env"
$logsDir = Join-Path $InstallRoot "logs"
$stateDir = Join-Path $InstallRoot "state"
$stopMarker = Join-Path $stateDir "stop.$Service"
$logFile = Join-Path $logsDir "$Service.log"

if (-not (Test-Path $venvPython)) { throw "runtime venv not found: $venvPython" }
if (-not (Test-Path $configPath)) { throw "config not found: $configPath" }
New-Item -ItemType Directory -Force -Path $logsDir | Out-Null

# Apply config values to this process's environment so the relay/wrapper read
# the same settings as a foreground run. python-dotenv quoting is handled by
# stripping one level of surrounding quotes.
foreach ($line in (Get-Content $configPath -Encoding UTF8 -ErrorAction SilentlyContinue)) {
    if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') {
        $key = $matches[1]
        $value = $matches[2].Trim().Trim('"').Trim("'")
        try { Set-Item -Path ("env:" + $key) -Value $value -ErrorAction Stop } catch { }
    }
}
# Never let the console encoding mangle logs.
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

$module = if ($Service -eq "relay") { "cc_remote.relay" } else { "cc_remote.wrapper" }

function Write-Log {
    param([string]$Message)
    $line = "$(Get-Date -Format o) $Message"
    Add-Content -Path $logFile -Value $line -Encoding utf8
}

function Invoke-SupervisedProcess {
    # Native stderr (including ordinary Python INFO logs) must not become a
    # terminating PowerShell 5.1 NativeCommandError under ErrorAction=Stop.
    $child = Start-Process $venvPython -ArgumentList @('-m', $module) -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logsDir "$Service.stdout.log") -RedirectStandardError (Join-Path $logsDir "$Service.stderr.log")
    $null = $child.Handle
    try {
        $child.WaitForExit()
        return $child.ExitCode
    } finally { $child.Dispose() }
}

if (Test-Path $stopMarker) {
    Write-Log "$Service supervisor refusing to start: stop marker present"
    exit 0
}

Write-Log "supervisor starting $Service (max $MaxRestarts restarts / ${WindowSeconds}s)"

$restarts = 0
$windowStart = Get-Date

while ($true) {
    if (Test-Path $stopMarker) {
        Write-Log "$Service supervisor stopping: stop marker present"
        exit 0
    }

    Write-Log "launching python -m $module"
    $code = Invoke-SupervisedProcess
    Write-Log "$module exited with code $code"

    if ($code -eq 0) {
        Write-Log "$module exited cleanly; supervisor exiting"
        exit 0
    }

    # Sliding restart budget.
    $now = Get-Date
    if (($now - $windowStart).TotalSeconds -gt $WindowSeconds) {
        $restarts = 0
        $windowStart = $now
        Write-Log "restart window reset"
    }
    $restarts++

    if ($restarts -gt $MaxRestarts) {
        Write-Log "restart budget exceeded ($restarts > $MaxRestarts); giving up"
        Write-Error "$module kept crashing; see $logFile"
        exit 1
    }

    Write-Log "restart $restarts/$MaxRestarts in ${BackoffSeconds}s"
    Start-Sleep -Seconds $BackoffSeconds
}
