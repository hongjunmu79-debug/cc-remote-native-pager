<#
.SYNOPSIS
    Builds the reproducible Windows release archive for cc-remote.

.DESCRIPTION
    Produces cc-remote-v<distribution-version>-windows-x64.zip containing
    setup.ps1, the packaging/windows scripts, and the verified payload tree.

    Determinism: every file in the archive gets a fixed timestamp
    (SOURCE_DATE_EPOCH, default 0), entries are sorted, and the payload
    manifest records the exact git SHA. Two builds from the same git SHA and
    epoch on the same source tree produce byte-identical archives.

    This script requires a Python 3.9+ interpreter (``py -3`` or ``python``)
    for the pure-stdlib packaging modules, plus ``uv.exe`` (from PATH or
    -UvExe) to bundle into the payload. It never writes outside the staging
    directory and the output directory.

.PARAMETER SourceRoot
    Repository root. Defaults to the parent of packaging/windows.

.PARAMETER OutputDir
    Where the archive lands. Default <SourceRoot>\dist.

.PARAMETER UvExe
    Path to uv.exe to bundle as payload\bin\uv.exe. Default: uv on PATH.

.PARAMETER SourceDateEpoch
    Epoch used as the archive timestamp. Default 0 (fixed date).

.PARAMETER GitSha
    git SHA recorded in the manifest. Default: git rev-parse HEAD.

.EXAMPLE
    & .\build.ps1 -SourceRoot C:\repo -OutputDir C:\out -UvExe C:\tools\uv.exe
#>
[CmdletBinding()]
param(
    [string]$SourceRoot,
    [string]$OutputDir,
    [string]$UvExe,
    [int]$SourceDateEpoch = 0,
    [string]$GitSha
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version 2.0

function Write-Step { param([string]$Message) Write-Host "[cc-remote] $Message" -ForegroundColor Cyan }

if (-not $SourceRoot) { $SourceRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path }
$sourceRoot = [System.IO.Path]::GetFullPath($SourceRoot).TrimEnd('\')
if (-not $OutputDir) { $OutputDir = Join-Path $sourceRoot "dist" }
$outputDir = [System.IO.Path]::GetFullPath($OutputDir).TrimEnd('\')
New-Item -ItemType Directory -Force -Path $outputDir | Out-Null

# --- Resolve python and uv ---------------------------------------------------
$python = (Get-Command py -ErrorAction SilentlyContinue).Source
if (-not $python) { $python = (Get-Command python -ErrorAction SilentlyContinue).Source }
if (-not $python) { throw "python (py -3 / python) not found; required for the packaging modules" }

if (-not $UvExe) { $UvExe = (Get-Command uv -ErrorAction SilentlyContinue).Source }
if (-not $UvExe) { throw "uv not found; pass -UvExe to bundle a pinned uv.exe" }
$uvExeFull = [System.IO.Path]::GetFullPath($UvExe)

if (-not $GitSha) {
    $GitSha = (git -C $sourceRoot rev-parse HEAD 2>$null).Trim()
    if (-not $GitSha) { throw "could not resolve the git SHA of $sourceRoot" }
}
if ($GitSha -notmatch '^[0-9a-f]{40}$') { throw "git SHA must be 40 lowercase hex chars: $GitSha" }

# --- Validate the canonical metadata against the backend ---------------------
Write-Step "Validating canonical release metadata"
& $python (Join-Path $sourceRoot "deploy\validate_release_metadata.py") --root $sourceRoot
if ($LASTEXITCODE -ne 0) { throw "release metadata validation failed" }
$metadata = Get-Content (Join-Path $sourceRoot "deploy\release-metadata.json") -Raw | ConvertFrom-Json
$distributionVersion = $metadata.distribution_version

# --- Verify the bundled uv version matches the pin ---------------------------
$uvVersionText = (Get-Content (Join-Path $sourceRoot "deploy\uv-version.txt") | Select-Object -First 1).Trim()
$uvOutput = & $uvExeFull --version 2>&1
if ($uvOutput -notmatch "uv ([0-9]+\.[0-9]+\.[0-9]+)") {
    throw "uv could not report a version: $uvOutput"
}
$uvActual = $Matches[1]
if ($uvActual -ne $uvVersionText) {
    throw "uv version mismatch: bundle is $uvActual, deploy/uv-version.txt pins $uvVersionText"
}

# --- Stage the payload -------------------------------------------------------
$stageRoot = Join-Path $outputDir "stage"
$payload = Join-Path $stageRoot "payload"
if (Test-Path $stageRoot) { Remove-Item -Recurse -Force -Confirm:$false $stageRoot }
New-Item -ItemType Directory -Force -Path $stageRoot | Out-Null

Write-Step "Staging payload ($distributionVersion)"
& $python (Join-Path $PSScriptRoot "win_manifest.py") --stage --source $sourceRoot --destination $payload
if ($LASTEXITCODE -ne 0) { throw "payload staging failed" }

New-Item -ItemType Directory -Force -Path (Join-Path $payload "bin") | Out-Null
Copy-Item $uvExeFull (Join-Path $payload "bin\uv.exe") -Force
Write-Step "Bundled uv $uvVersionText as payload\bin\uv.exe"

# --- Build the manifest and run the smoke suite ------------------------------
& $python (Join-Path $PSScriptRoot "win_manifest.py") --build $payload --git-sha $GitSha --source-date-epoch $SourceDateEpoch
if ($LASTEXITCODE -ne 0) { throw "distribution manifest build failed" }

Write-Step "Running the clean-install smoke suite"
& $python (Join-Path $PSScriptRoot "win_smoke.py") --check $payload
if ($LASTEXITCODE -ne 0) { throw "smoke suite failed" }

# --- Assemble the deterministic archive --------------------------------------
$archiveName = "cc-remote-v$distributionVersion-windows-x64.zip"
$archivePath = Join-Path $outputDir $archiveName
Write-Step "Assembling $archiveName (SOURCE_DATE_EPOCH=$SourceDateEpoch)"
& $python (Join-Path $PSScriptRoot "win_build.py") `
    --setup (Join-Path $PSScriptRoot "setup.ps1") `
    --packaging (Join-Path $PSScriptRoot ".") `
    --packaging-init (Join-Path (Split-Path $PSScriptRoot -Parent) "__init__.py") `
    --payload $payload `
    --output $archivePath `
    --source-date-epoch $SourceDateEpoch `
    --git-sha $GitSha
if ($LASTEXITCODE -ne 0) { throw "archive assembly failed" }

$sha = (Get-FileHash -Algorithm SHA256 -Path $archivePath).Hash.ToLowerInvariant()
$size = (Get-Item $archivePath).Length
Write-Step "Built $archiveName ($size bytes)"
Write-Step "SHA256 $sha"
Write-Step "Recording SHA256SUMS"
Set-Content -Path (Join-Path $outputDir "$archiveName.sha256") -Value "$sha  $archiveName" -Encoding ascii

Write-Step "Done: $archivePath"
exit 0
