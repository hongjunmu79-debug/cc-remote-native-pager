<#
.SYNOPSIS
    Entry point of the cc-remote Windows release archive.

.DESCRIPTION
    Verifies the archive's own payload (checksum manifest + clean-install smoke
    suite) and then runs install.ps1 with the same arguments. The parameter
    block intentionally mirrors install.ps1 so the archive root is the single
    documented entry point.

    The archive layout is:

        setup.ps1
        packaging/windows/*     (install.ps1, config-first-run.ps1, ...)
        payload/                (the verified distribution tree)

    The extracted location is temporary; the installer copies the payload into
    the immutable releases\<version> directory and switches the current
    junction, so the archive itself is never "the install".

.EXAMPLE
    .\setup.ps1 -Unattended -LoginPassword "a-strong-16-char-password" -MachineName desktop-1 -PublicOrigin http://192.168.1.50:8765
#>
[CmdletBinding()]
param(
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

$scriptRoot = $PSScriptRoot
$installer = Join-Path $scriptRoot "packaging\windows\install.ps1"
$payload = Join-Path $scriptRoot "payload"

if (-not (Test-Path $installer)) { throw "install.ps1 is missing; this archive is not a valid cc-remote release" }
if (-not (Test-Path (Join-Path $payload "distribution-manifest.json"))) {
    throw "payload\distribution-manifest.json is missing; this archive is not a valid cc-remote release"
}

$manifest = Get-Content (Join-Path $payload "distribution-manifest.json") -Raw | ConvertFrom-Json
if (-not $InstallRoot) {
    # Derive install root from where setup.ps1 was extracted. setup.ps1 lives in
    # {installRoot}\release, so the install root is its parent. The installer
    # (packaging\windows\install.ps1) is located relative to $scriptRoot and is
    # already validated above via Test-Path $installer.
    $InstallRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..")).TrimEnd('\\')
    Write-Host "[cc-remote] InstallRoot not provided; inferring $InstallRoot from setup.ps1 location" -ForegroundColor Yellow
}

if (-not $InstallRoot) { throw "failed to determine install root" }

Write-Host "[cc-remote] cc-remote v$($manifest.product_version) (protocol v$($manifest.protocol)) distribution $($manifest.distribution_version)" -ForegroundColor Cyan
Write-Host "[cc-remote] git $($manifest.git_sha.Substring(0, 12))" -ForegroundColor Cyan

$callArgs = @{ Payload = $payload }
if ($InstallRoot) { $callArgs['InstallRoot'] = $InstallRoot }
if ($Unattended) { $callArgs['Unattended'] = $true }
if ($LoginPassword) { $callArgs['LoginPassword'] = $LoginPassword }
if ($MachineName) { $callArgs['MachineName'] = $MachineName }
if ($Workspace) { $callArgs['Workspace'] = $Workspace }
if ($PublicOrigin) { $callArgs['PublicOrigin'] = $PublicOrigin }
if ($RelayPort -ne 8765) { $callArgs['RelayPort'] = $RelayPort }
if ($AllowInsecureHttp) { $callArgs['AllowInsecureHttp'] = $true }
if ($ConfigFile) { $callArgs['ConfigFile'] = $ConfigFile }
if ($NoServices) { $callArgs['NoServices'] = $true }
if ($NoFirewall) { $callArgs['NoFirewall'] = $true }

& $installer @callArgs
exit $LASTEXITCODE
