#Requires -Version 5.1
<#
    Phase 7: reproducible portable Windows release build for OpenCode Widget.

    This is the single entry point:

        powershell -NoProfile -ExecutionPolicy Bypass -File scripts/build-release.ps1

    It is intentionally deterministic and self-contained:

      * single version source: ``electron/package.json`` -> ``version``
      * the Electron runtime is reused as-is from
        ``electron/node_modules/electron/dist`` (no download, no installer)
      * the Python backend stays a *system-Python*, stdlib-only program; nothing
        is frozen or bundled. The launcher prefers ``pythonw.exe`` from PATH and
        falls back to ``%LOCALAPPDATA%\Programs\Python\Python3*\pythonw.exe``.
      * user data is never touched: mutable state lives in
        ``%APPDATA%\opencode-widget`` at runtime, and this script only reads the
        repo and writes under ``dist/``.

    Outputs (all under ``dist/``):
      dist/opencode-widget-<version>/               staging tree
      dist/opencode-widget-<version>-win-x64.zip    portable artifact
      dist/BUILD_INFO.json                          version / commit / built_at

    The staged tree is scanned by ``scripts/release_sanitize.py``; the build
    aborts (and refuses to zip) if any forbidden artifact, secret-looking value
    or debug hook is found.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

function Write-Info([string]$Message) { Write-Host "[build] $Message" }
function Write-Warn([string]$Message) { Write-Host "[build] WARN: $Message" -ForegroundColor Yellow }
function Fail([string]$Message) {
    Write-Host "[build] ERROR: $Message" -ForegroundColor Red
    exit 1
}

# ---------------------------------------------------------------------------
# 0. locations
# ---------------------------------------------------------------------------
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = Split-Path -Parent $ScriptDir
Set-Location -LiteralPath $RepoRoot

Write-Info "repo root: $RepoRoot"

# ---------------------------------------------------------------------------
# 1. preflight
# ---------------------------------------------------------------------------
if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    Fail 'python was not found on PATH (the Python backend needs system Python 3.12+).'
}

$RequiredFiles = @(
    'data_server.py',
    'go-usage-widget.py',
    'paths.py',
    'electron/package.json',
    'electron/main.js',
    'electron/preload.js'
)
foreach ($rel in $RequiredFiles) {
    if (-not (Test-Path -LiteralPath (Join-Path $RepoRoot $rel) -PathType Leaf)) {
        Fail "missing required file: $rel"
    }
}

$ElectronDist = Join-Path $RepoRoot 'electron/node_modules/electron/dist'
$ElectronExe = Join-Path $ElectronDist 'electron.exe'
if (-not (Test-Path -LiteralPath $ElectronExe -PathType Leaf)) {
    Fail "Electron runtime not found at $ElectronExe (run 'npm install' in electron/)."
}

# Single version source.
$PackageJson = Join-Path $RepoRoot 'electron/package.json'
$Version = (Get-Content -LiteralPath $PackageJson -Raw | ConvertFrom-Json).version
if (-not $Version) { Fail 'electron/package.json does not define a version.' }
Write-Info "version (from electron/package.json): $Version"

# Working-tree note only: the build never fails on a dirty checkout.
$GitAvailable = $false
try {
    $null = & git rev-parse --is-inside-work-tree 2>$null
    if ($LASTEXITCODE -eq 0) {
        $GitAvailable = $true
        $Dirty = (& git status --porcelain) 2>$null
        if ($Dirty) { Write-Warn 'working tree is dirty; building from the current checkout.' }
    }
} catch {
    $GitAvailable = $false
}

# ---------------------------------------------------------------------------
# 2. verify: test suite + renderer syntax
# ---------------------------------------------------------------------------
Write-Info 'running test suite (python -m pytest -o addopts="" -q) ...'
& python -m pytest -o addopts="" -q
if ($LASTEXITCODE -ne 0) { Fail 'test suite failed; not building.' }
Write-Info 'tests passed.'

$RendererJs = @('electron/app/app.js')
$DashboardJs = Join-Path $RepoRoot 'electron/app/dashboard'
if (Test-Path -LiteralPath $DashboardJs -PathType Container) {
    $RendererJs += Get-ChildItem -LiteralPath $DashboardJs -Filter '*.js' -File |
        Sort-Object Name | ForEach-Object { "electron/app/dashboard/$($_.Name)" }
}
if (Get-Command node -ErrorAction SilentlyContinue) {
    foreach ($rel in $RendererJs) {
        $full = Join-Path $RepoRoot $rel
        if (-not (Test-Path -LiteralPath $full -PathType Leaf)) { Fail "renderer script missing: $rel" }
        & node --check $full
        if ($LASTEXITCODE -ne 0) { Fail "node --check failed: $rel" }
    }
    Write-Info "renderer syntax ok ($($RendererJs.Count) files)."
} else {
    Write-Warn 'node not found; skipping renderer syntax check.'
}

# ---------------------------------------------------------------------------
# 3. stage
# ---------------------------------------------------------------------------
$DistDir = Join-Path $RepoRoot 'dist'
New-Item -ItemType Directory -Force -Path $DistDir | Out-Null
$StageName = "opencode-widget-$Version"
$StagingDir = Join-Path $DistDir $StageName
if (Test-Path -LiteralPath $StagingDir) {
    Remove-Item -LiteralPath $StagingDir -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $StagingDir | Out-Null
Write-Info "staging: $StagingDir"

# 3a. Electron runtime payload (dlls, locales, resources, ...).
Copy-Item -Path (Join-Path $ElectronDist '*') -Destination $StagingDir -Recurse -Force
# Electron occasionally leaves a debug.log behind; it must never ship.
Get-ChildItem -Path (Join-Path $StagingDir '*') -Recurse -File -Include '*.log' |
    Remove-Item -Force -ErrorAction SilentlyContinue

# 3b. Electron app sources under resources/app.
$AppDest = Join-Path $StagingDir 'resources/app'
New-Item -ItemType Directory -Force -Path $AppDest | Out-Null
$ElectronRoot = Join-Path $RepoRoot 'electron'
$AppFiles = @(
    'package.json',
    'main.js',
    'preload.js',
    'runtime_env.js',
    'runtime_manager.js',
    'notification_policy.js',
    'notification_manager.js',
    'tray_manager.js'
)
foreach ($name in $AppFiles) {
    Copy-Item -LiteralPath (Join-Path $ElectronRoot $name) -Destination (Join-Path $AppDest $name) -Force
}
Copy-Item -LiteralPath (Join-Path $ElectronRoot 'app') -Destination (Join-Path $AppDest 'app') -Recurse -Force
Copy-Item -LiteralPath (Join-Path $ElectronRoot 'assets') -Destination (Join-Path $AppDest 'assets') -Recurse -Force

# 3c. rename the shell executable.
$BundledExe = Join-Path $StagingDir 'electron.exe'
if (-not (Test-Path -LiteralPath $BundledExe -PathType Leaf)) {
    Fail "expected $BundledExe after copying the Electron runtime."
}
Rename-Item -LiteralPath $BundledExe -NewName 'opencode-widget.exe'

# 3d. Python backend at the staging root (system-Python, stdlib only).
$PythonFiles = @(
    'data_server.py',
    'go-usage-widget.py',
    'views.py',
    'observability.py',
    'forecasting.py',
    'timeline.py',
    'formula_registry.py',
    'server_data.py',
    'usage_remote.py',
    'secret_store.py',
    'runtime_lifecycle.py',
    'browser_cookie.py',
    'paths.py'
)
foreach ($name in $PythonFiles) {
    Copy-Item -LiteralPath (Join-Path $RepoRoot $name) -Destination (Join-Path $StagingDir $name) -Force
}

# 3e. top-level docs when present.
foreach ($doc in @('README.md', 'CHANGELOG.md')) {
    $src = Join-Path $RepoRoot $doc
    if (Test-Path -LiteralPath $src -PathType Leaf) {
        Copy-Item -LiteralPath $src -Destination (Join-Path $StagingDir $doc) -Force
    }
}

# 3f. launchers. The Chinese prefix is built from code points so this script
# stays pure ASCII (Windows PowerShell 5.1 would otherwise mis-decode it).
$LaunchPrefix = -join @([char]0x542F, [char]0x52A8)   # U+542F U+52A8
$CmdName = "$LaunchPrefix OpenCode Widget.cmd"
$VbsName = "$LaunchPrefix OpenCode Widget.vbs"

$CmdLines = @(
    '@echo off',
    'setlocal enableextensions',
    'set "BASE=%~dp0"',
    'set "PYW="',
    'for %%P in (pythonw.exe) do if not defined PYW set "PYW=%%~$PATH:P"',
    'if not defined PYW for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do if not defined PYW if exist "%%~fD\pythonw.exe" set "PYW=%%~fD\pythonw.exe"',
    'if not defined PYW (',
    '  echo [opencode-widget] Python 3 (pythonw.exe) was not found.',
    '  echo Install Python 3.12+ and add it to PATH, or install it under',
    '  echo   %LOCALAPPDATA%\Programs\Python\Python3xx',
    '  pause',
    '  exit /b 1',
    ')',
    'start "" "%PYW%" "%BASE%data_server.py"',
    'start "" "%BASE%opencode-widget.exe"',
    'endlocal'
)
Set-Content -LiteralPath (Join-Path $StagingDir $CmdName) -Value $CmdLines -Encoding Ascii

$VbsLines = @(
    "' Launch the OpenCode Widget backend, then the Electron shell.",
    'Option Explicit',
    'Dim fso, sh, base, cmdPath',
    'Set fso = CreateObject("Scripting.FileSystemObject")',
    'Set sh = CreateObject("WScript.Shell")',
    'base = fso.GetParentFolderName(WScript.ScriptFullName)',
    'cmdPath = base & "\" & ChrW(&H542F) & ChrW(&H52A8) & " OpenCode Widget.cmd"',
    'sh.Run """" & cmdPath & """", 1, False'
)
Set-Content -LiteralPath (Join-Path $StagingDir $VbsName) -Value $VbsLines -Encoding Ascii

# ---------------------------------------------------------------------------
# 4. sanitation gate
# ---------------------------------------------------------------------------
$Sanitizer = Join-Path $ScriptDir 'release_sanitize.py'
if (-not (Test-Path -LiteralPath $Sanitizer -PathType Leaf)) { Fail "missing $Sanitizer" }
Write-Info 'scanning staging tree ...'
$SanitizeOutput = & python $Sanitizer $StagingDir 2>&1
$SanitizeExit = $LASTEXITCODE
Write-Host ($SanitizeOutput -join [Environment]::NewLine)
if ($SanitizeExit -ne 0) {
    Fail 'sanitation failed; refusing to package the staging tree.'
}
Write-Info 'sanitation clean.'

# ---------------------------------------------------------------------------
# 5. build info + zip
# ---------------------------------------------------------------------------
$Commit = 'unknown'
if ($GitAvailable) {
    try {
        $rev = (& git rev-parse --short HEAD 2>$null)
        if ($LASTEXITCODE -eq 0 -and $rev) { $Commit = "$rev".Trim() }
    } catch {
        $Commit = 'unknown'
    }
}
$BuildInfo = [ordered]@{
    version  = $Version
    commit   = $Commit
    built_at = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
}
$BuildInfoPath = Join-Path $DistDir 'BUILD_INFO.json'
$BuildInfo | ConvertTo-Json | Set-Content -LiteralPath $BuildInfoPath -Encoding UTF8
Write-Info "build info: $BuildInfoPath"

$ZipPath = Join-Path $DistDir "opencode-widget-$Version-win-x64.zip"
if (Test-Path -LiteralPath $ZipPath) { Remove-Item -LiteralPath $ZipPath -Force }
Write-Info 'compressing artifact ...'
Compress-Archive -LiteralPath $StagingDir -DestinationPath $ZipPath -CompressionLevel Optimal -Force

$Size = (Get-Item -LiteralPath $ZipPath).Length
$Hash = (Get-FileHash -LiteralPath $ZipPath -Algorithm SHA256).Hash

Write-Info '--- build complete ---'
Write-Info "artifact: $ZipPath"
Write-Info ("size:     {0:N0} bytes ({1:N2} MB)" -f $Size, ($Size / 1MB))
Write-Info "sha256:   $Hash"
Write-Info "sanitizer: 0 forbidden / 0 secret / 0 debug-hook findings"
