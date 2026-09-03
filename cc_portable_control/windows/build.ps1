<#
.SYNOPSIS
    Builds the reproducible Windows release archive for cc-remote.

.DESCRIPTION
    Produces cc-remote-v<distribution-version>-windows-x64.zip containing
    setup.ps1, the cc_portable_control/windows scripts, and the verified payload tree.

    Determinism: every file in the archive gets a fixed timestamp
    (SOURCE_DATE_EPOCH, default 0), entries are sorted, and the payload
    manifest records the exact git SHA. Two builds from the same git SHA and
    epoch on the same source tree produce byte-identical archives.

    This script requires a Python 3.9+ interpreter (``py -3`` or ``python``)
    for the pure-stdlib packaging modules, plus ``uv.exe`` (from PATH or
    -UvExe) to bundle into the payload. It never writes outside the staging
    directory and the output directory.

.PARAMETER SourceRoot
    Repository root. Defaults to the parent of cc_portable_control/windows.

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
if (-not $python) { throw "python (py -3 / python) not found; required for the cc_portable_control modules" }

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
$productVersion = $metadata.product_version

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

# Bundle the pinned python-build-standalone runtime as well. Consumer machines
# must not need system Python, and downloading a managed interpreter during an
# Inno Setup child process is unreliable on Windows profiles backed by cloud
# file-system filters (ERROR_UNTRUSTED_MOUNT_POINT / cross-volume rename).
$pythonVersion = (Get-Content (Join-Path $sourceRoot "deploy\python-version.txt") | Select-Object -First 1).Trim()
& $uvExeFull python install $pythonVersion
if ($LASTEXITCODE -ne 0) { throw "uv could not install the pinned build-time Python $pythonVersion" }
$managedPython = (& $uvExeFull python find --managed-python $pythonVersion | Select-Object -First 1)
if (-not $managedPython -or -not (Test-Path $managedPython)) {
    throw "uv could not locate the pinned build-time Python $pythonVersion"
}
$managedPythonRoot = Split-Path ([System.IO.Path]::GetFullPath(([string]$managedPython).Trim())) -Parent
$runtimePythonBuild = Join-Path $outputDir "runtime-python-build"
$runtimeVenvBuild = Join-Path $outputDir "runtime-venv-build"
foreach ($temporaryRuntime in @($runtimePythonBuild, $runtimeVenvBuild)) {
    if (Test-Path $temporaryRuntime) {
        Remove-Item -LiteralPath $temporaryRuntime -Recurse -Force -Confirm:$false
    }
}
& $python (Join-Path $PSScriptRoot "win_manifest.py") --copy --source $managedPythonRoot --destination $runtimePythonBuild
if ($LASTEXITCODE -ne 0) { throw "failed to clean-copy the build-time Python runtime" }
$externallyManaged = Join-Path $runtimePythonBuild "Lib\EXTERNALLY-MANAGED"
if (Test-Path $externallyManaged) {
    Remove-Item -LiteralPath $externallyManaged -Force -Confirm:$false
}
& $uvExeFull pip install --python (Join-Path $runtimePythonBuild "python.exe") --system `
    --requirement (Join-Path $payload "requirements.lock")
if ($LASTEXITCODE -ne 0) { throw "failed to install locked dependencies into bundled Python" }
& $uvExeFull --no-python-downloads venv $runtimeVenvBuild `
    --python (Join-Path $runtimePythonBuild "python.exe") --system-site-packages
if ($LASTEXITCODE -ne 0) { throw "failed to create the bundled runtime launcher" }

$bundledPythonRoot = Join-Path $payload "runtime\python"
& $python (Join-Path $PSScriptRoot "win_manifest.py") --copy --source $runtimePythonBuild --destination $bundledPythonRoot
if ($LASTEXITCODE -ne 0) { throw "failed to clean-copy the bundled Python runtime" }
if (-not (Test-Path (Join-Path $bundledPythonRoot "python.exe"))) {
    throw "bundled Python payload is incomplete"
}
$bundledVenvRoot = Join-Path $payload "runtime\.venv"
& $python (Join-Path $PSScriptRoot "win_manifest.py") --copy --source $runtimeVenvBuild --destination $bundledVenvRoot
if ($LASTEXITCODE -ne 0) { throw "failed to clean-copy the bundled runtime template" }
Remove-Item -LiteralPath $runtimePythonBuild -Recurse -Force -Confirm:$false
Remove-Item -LiteralPath $runtimeVenvBuild -Recurse -Force -Confirm:$false
$runtimeBundle = Join-Path $payload "runtime-bundle.zip"
& $python (Join-Path $PSScriptRoot "win_build.py") --bundle-tree (Join-Path $payload "runtime") `
    --output $runtimeBundle --source-date-epoch $SourceDateEpoch
if ($LASTEXITCODE -ne 0) { throw "failed to assemble the deterministic runtime bundle" }
Remove-Item -LiteralPath (Join-Path $payload "runtime") -Recurse -Force -Confirm:$false
Write-Step "Bundled Python $pythonVersion and locked dependencies as runtime-bundle.zip"

# --- Build the manifest and run the smoke suite ------------------------------
& $python (Join-Path $PSScriptRoot "win_manifest.py") --build $payload --git-sha $GitSha --source-date-epoch $SourceDateEpoch
if ($LASTEXITCODE -ne 0) { throw "distribution manifest build failed" }

Write-Step "Running the clean-install smoke suite"
& $python (Join-Path $PSScriptRoot "win_smoke.py") --check $payload
if ($LASTEXITCODE -ne 0) { throw "smoke suite failed" }

# --- Populate the stage root with the archive layout -------------------------
# The stage root is exactly the archive layout: setup.ps1, cc_portable_control/windows,
# and the verified payload. win_build.py embeds it into both zips and
# build-installer.ps1 feeds it to Inno Setup, so the .exe bundles the identical
# tree the zips carry.
Write-Step "Populating the stage root with the archive layout"
Copy-Item -Force (Join-Path $PSScriptRoot "setup.ps1") (Join-Path $stageRoot "setup.ps1")
$stagePackaging = Join-Path $stageRoot "cc_portable_control"
New-Item -ItemType Directory -Force -Path (Join-Path $stagePackaging "windows") | Out-Null
Copy-Item -Force (Join-Path (Split-Path $PSScriptRoot -Parent) "__init__.py") (Join-Path $stagePackaging "__init__.py")
# Copy cc_portable_control/windows through the canonical clean-copy helper, NOT a raw
# recursive Copy-Item. Running the Python packaging scripts above generates
# cc_portable_control/windows/__pycache__/*.pyc on the build host; a raw copy shipped
# those into both zips and the Inno Setup .exe (manual Release run
# 33125872590). win_manifest.py --copy applies the same single-source exclusion
# rules the payload staging uses and fails closed on symlinks.
& $python (Join-Path $PSScriptRoot "win_manifest.py") --copy `
    --source $PSScriptRoot `
    --destination (Join-Path $stagePackaging "windows")
if ($LASTEXITCODE -ne 0) { throw "cc_portable_control/windows clean-copy failed" }

# --- Assemble the two deterministic archives ----------------------------------
function Invoke-ArchiveAssembly {
    param([string]$Name, [string[]]$ExtraArgs)
    $archivePath = Join-Path $outputDir $Name
    Write-Step "Assembling $Name (SOURCE_DATE_EPOCH=$SourceDateEpoch)"
    # win_build.py prints the assembled archive path on stdout; capture it so
    # that output cannot leak into this function's return value. A leaked
    # stdout line turned $archivePath into a 2-element array and corrupted the
    # archive loop below (manual Release run 33120966288) — see
    # docs/ACCEPTANCE_FIXES.md.
    $buildOutput = & $python (Join-Path $PSScriptRoot "win_build.py") `
        --packaging (Join-Path $stagePackaging "windows") `
        --packaging-init (Join-Path $stagePackaging "__init__.py") `
        --payload $payload `
        --output $archivePath `
        --source-date-epoch $SourceDateEpoch `
        --git-sha $GitSha `
        @ExtraArgs
    if ($LASTEXITCODE -ne 0) { throw "archive assembly failed: $Name" }
    if ($buildOutput) { Write-Step $buildOutput }
    return $archivePath
}

$installerArchiveName = "cc-remote-v$distributionVersion-windows-x64.zip"
$installerArchivePath = Invoke-ArchiveAssembly -Name $installerArchiveName `
    -ExtraArgs @("--setup", (Join-Path $stageRoot "setup.ps1"))

$portableArchiveName = "cc-remote-v$distributionVersion-windows-x64-portable.zip"
$portableArchivePath = Invoke-ArchiveAssembly -Name $portableArchiveName `
    -ExtraArgs @(
        "--portable",
        "--start-portable", (Join-Path $stagePackaging "windows\start-portable.ps1"),
        "--readme", (Join-Path $stagePackaging "windows\README-portable.txt")
    )

# Each $path must be a plain scalar string. Guard against a subprocess stdout
# line leaking into a caller's return value — the exact CI failure, where
# $path became a 2-element array, the status line duplicated the leaf and hash,
# (Get-Item $path).Length reported the array count instead of the file size,
# and Set-Content -Path "$path.sha256" was parsed as an alternate data stream.
# Fail loudly rather than corrupt the sidecar. The leaf is computed once into a
# scalar so the interpolation is unambiguous.
foreach ($path in @($installerArchivePath, $portableArchivePath)) {
    if ($path -isnot [string]) {
        throw "archive path is not a scalar string: $path"
    }
    $leaf = Split-Path -Leaf $path
    $sha = (Get-FileHash -Algorithm SHA256 -Path $path).Hash.ToLowerInvariant()
    $size = (Get-Item $path).Length
    Write-Step "Built $leaf ($size bytes)"
    Write-Step "SHA256 $sha"
    Set-Content -Path "${path}.sha256" -Value "$sha  $leaf" -Encoding ascii
}

# --- Compile the real installer (.exe) with Inno Setup ------------------------
Write-Step "Compiling the installer executable"
& (Join-Path $PSScriptRoot "build-installer.ps1") `
    -SourceRoot $sourceRoot `
    -StageDir $stageRoot `
    -DistributionVersion $distributionVersion `
    -ProductVersion $productVersion `
    -OutputDir $outputDir
if ($LASTEXITCODE -ne 0) { throw "installer compilation failed" }
$setupExe = Join-Path $outputDir "cc-remote-v$distributionVersion-windows-x64-setup.exe"
$leaf = Split-Path -Leaf $setupExe
$sha = (Get-FileHash -Algorithm SHA256 -Path $setupExe).Hash.ToLowerInvariant()
$size = (Get-Item $setupExe).Length
Write-Step "Built $leaf ($size bytes)"
Write-Step "SHA256 $sha"
Set-Content -Path "${setupExe}.sha256" -Value "$sha  $leaf" -Encoding ascii

Write-Step "Done: $installerArchivePath, $portableArchivePath, $setupExe"
exit 0
