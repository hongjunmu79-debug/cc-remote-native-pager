<# Opens the local cc-remote Web console without exposing credentials in a URL. #>
[CmdletBinding()]
param([string]$InstallRoot)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version 2.0

if (-not $InstallRoot) { $InstallRoot = Join-Path $env:LOCALAPPDATA "cc-remote" }
$installRootFull = [System.IO.Path]::GetFullPath($InstallRoot).TrimEnd('\')
$envPath = Join-Path $installRootFull "config\.env"
$port = 8765
if (Test-Path $envPath) {
    foreach ($line in (Get-Content $envPath)) {
        if ($line -match '^\s*RELAY_PORT\s*=\s*(\d+)\s*$') { $port = [int]$matches[1] }
    }
}
if ($port -lt 1 -or $port -gt 65535) { throw "invalid relay port in config: $port" }

# Starting registered tasks is idempotent and makes a shortcut useful after a
# manual stop. Failures are non-fatal: the browser still shows relay status.
foreach ($taskName in @("cc-remote-relay", "cc-remote-wrapper")) {
    try { Start-ScheduledTask -TaskName $taskName -ErrorAction Stop } catch { }
}

# Loopback is deliberate. It is the only unauthenticated origin allowed to
# issue a one-time client QR; the QR itself names PUBLIC_ORIGIN for the phone.
$consoleUrl = "http://127.0.0.1:$port/"
for ($attempt = 0; $attempt -lt 20; $attempt++) {
    try {
        $health = Invoke-WebRequest "${consoleUrl}healthz" -UseBasicParsing -TimeoutSec 1
        if ($health.StatusCode -eq 200) { break }
    } catch { }
    Start-Sleep -Milliseconds 500
}
Start-Process $consoleUrl
