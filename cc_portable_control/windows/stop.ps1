<#
.SYNOPSIS
    Stops cc-remote: disables the scheduled-task supervisors and drops stop
    markers so a disabled task cannot flap on the next logon.

.DESCRIPTION
    Writes state\stop.<service> markers, stops and disables the cc-remote-relay
    and cc-remote-wrapper scheduled tasks, and stops any portable foreground
    python processes bound to the install root's runtime venv.

    Because supervise.ps1 honors the marker at startup, re-enabling the tasks
    later (start.ps1 -AsService) starts cleanly without an accidental restart
    of a session you meant to keep stopped.

.PARAMETER InstallRoot
    Install root. Defaults to %LOCALAPPDATA%\cc-remote at runtime.

.EXAMPLE
    & stop.ps1
#>
[CmdletBinding()]
param([string]$InstallRoot)

$ErrorActionPreference = "SilentlyContinue"
Set-StrictMode -Version 2.0

if (-not $InstallRoot) { $InstallRoot = Join-Path $env:LOCALAPPDATA "cc-remote" }

$stateDir = Join-Path $InstallRoot "state"
New-Item -ItemType Directory -Force -Path $stateDir | Out-Null

foreach ($service in @("relay", "wrapper")) {
    Set-Content -Path (Join-Path $stateDir "stop.$service") -Value "stopped" -Encoding utf8
}

foreach ($taskName in @("cc-remote-relay", "cc-remote-wrapper")) {
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($task -and (($task.Actions.Arguments -join ' ').Contains('"' + $InstallRoot.TrimEnd('\') + '"'))) {
        Stop-ScheduledTask -TaskName $taskName
        Disable-ScheduledTask -TaskName $taskName
        Write-Host "[cc-remote] stopped and disabled scheduled task $taskName"
    }
}

# Stop any portable foreground processes that use this install's venv.
$venvRoot = Join-Path $InstallRoot "runtime\.venv\"
$runtimeExe = Join-Path $InstallRoot "runtime\python\python.exe"
$stopped = @()
Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" | ForEach-Object {
    $cmdline = $_.CommandLine
    if ($cmdline -and ($cmdline.Contains($venvRoot) -or
        ($_.ExecutablePath -eq $runtimeExe -and $cmdline -match '-m\s+cc_remote\.(relay|wrapper)(\s|$)'))) {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        $stopped += $_.ProcessId
    }
}
if ($stopped.Count -gt 0) {
    Write-Host "[cc-remote] stopped portable processes: $($stopped -join ', ')"
} else {
    Write-Host "[cc-remote] no portable foreground processes were running"
}

# Clear the markers after a successful stop so the next start is clean.
foreach ($service in @("relay", "wrapper")) {
    Remove-Item -Path (Join-Path $stateDir "stop.$service") -Force -ErrorAction SilentlyContinue
}
Write-Host "[cc-remote] stopped"
