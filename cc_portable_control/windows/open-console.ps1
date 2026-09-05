<# Open the native control window, including offline recovery and QR pairing. #>
[CmdletBinding()]
param([string]$InstallRoot)
$ErrorActionPreference = "Stop"
try {
    & (Join-Path $PSScriptRoot "desktop-console.ps1") -InstallRoot $InstallRoot
} catch {
    Add-Type -AssemblyName PresentationFramework
    [Windows.MessageBox]::Show("CC Remote could not open. Please reinstall to repair the console. " + $_.Exception.Message, "CC Remote") | Out-Null
    exit 1
}
