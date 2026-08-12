<#
.SYNOPSIS
  Build the portable Windows distribution (PyInstaller onedir + versioned ZIP).

.DESCRIPTION
  Creates a clean PyInstaller build of civ4-turn-relay-ui, packages a versioned
  portable ZIP, and runs a non-destructive smoke check (process starts, then
  is stopped). Requires neither Civilization nor a real SFTP server.

  Does NOT bundle .env, saves, local match data, logs, SSH keys, or known_hosts.
  Civ launching remains argv-only (see process/launch_config.py); this script
  never constructs a shell command string for Civilization.
#>
[CmdletBinding()]
param(
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
    [string]$DistRoot = (Join-Path (Resolve-Path (Join-Path $PSScriptRoot "..")).Path "dist"),
    [switch]$SkipZip,
    [switch]$SkipSmoke
)

$ErrorActionPreference = "Stop"

function Get-ProjectVersion {
    param([string]$Root)
    $pyproject = Join-Path $Root "pyproject.toml"
    if (-not (Test-Path $pyproject)) {
        throw "pyproject.toml not found at $pyproject"
    }
    $text = Get-Content -Raw -Path $pyproject
    if ($text -match '(?m)^\s*version\s*=\s*"([^"]+)"') {
        return $Matches[1]
    }
    throw "Could not parse project.version from pyproject.toml"
}

function Require-Command {
    param([string]$Name, [string]$Hint)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required tool '$Name' was not found on PATH. $Hint"
    }
}

Write-Host "==> civ4-turn-relay portable build"
Require-Command -Name "python" -Hint "Install Python 3.12+ and ensure it is on PATH."
Require-Command -Name "pyinstaller" -Hint "pip install 'pyinstaller>=6,<8' into the build environment."

$version = Get-ProjectVersion -Root $RepoRoot
$appName = "civ4-turn-relay"
$specPath = Join-Path $RepoRoot "packaging\civ4-turn-relay.spec"
$workPath = Join-Path $DistRoot "pyinstaller-work"
$buildPath = Join-Path $DistRoot "pyinstaller-build"
$portableDir = Join-Path $DistRoot "$appName-$version-win64-portable"
$zipPath = Join-Path $DistRoot "$appName-$version-win64-portable.zip"

Write-Host "RepoRoot   : $RepoRoot"
Write-Host "Version    : $version"
Write-Host "Spec       : $specPath"

if (-not (Test-Path $specPath)) {
    throw "Missing PyInstaller spec: $specPath"
}

# Clean previous portable outputs (never touch %APPDATA%\civ4-turn-relay).
foreach ($path in @($workPath, $buildPath, $portableDir, $zipPath)) {
    if (Test-Path $path) {
        Remove-Item -Recurse -Force $path
    }
}
New-Item -ItemType Directory -Force -Path $DistRoot | Out-Null

Write-Host "==> Running PyInstaller (clean)"
& pyinstaller `
    --noconfirm `
    --clean `
    --distpath $DistRoot `
    --workpath $workPath `
    $specPath
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE"
}

$collected = Join-Path $DistRoot $appName
if (-not (Test-Path (Join-Path $collected "$appName.exe"))) {
    throw "Expected GUI executable not found under $collected"
}

# Relocate into a versioned portable folder.
Move-Item -Force $collected $portableDir

# Refuse to ship secrets or local data if they somehow appear in the tree.
$forbiddenNames = @(
    ".env",
    "known_hosts",
    "id_rsa",
    "id_ed25519",
    "installation.json",
    "*.CivBeyondSwordSave",
    "*.pem",
    "*.ppk"
)
$hits = @()
foreach ($pattern in $forbiddenNames) {
    $hits += Get-ChildItem -Path $portableDir -Recurse -Force -File -Filter $pattern -ErrorAction SilentlyContinue
}
# Also reject any nested matches/ state directories that look like LocalStore data.
$hits += Get-ChildItem -Path $portableDir -Recurse -Force -Directory -Filter "matches" -ErrorAction SilentlyContinue |
    Where-Object { Test-Path (Join-Path $_.FullName "..\installation.json") -ErrorAction SilentlyContinue }
if ($hits.Count -gt 0) {
    $list = ($hits | ForEach-Object { $_.FullName }) -join "`n"
    throw "Portable build contains forbidden secret/local-data paths:`n$list"
}

if (-not $SkipSmoke) {
    Write-Host "==> Non-destructive smoke: start then stop the GUI executable"
    $exe = Join-Path $portableDir "$appName.exe"
    $proc = Start-Process -FilePath $exe -PassThru -WindowStyle Hidden
    Start-Sleep -Seconds 3
    if ($proc.HasExited -and $proc.ExitCode -ne 0) {
        throw "Packaged executable exited early with code $($proc.ExitCode)"
    }
    if (-not $proc.HasExited) {
        Stop-Process -Id $proc.Id -Force
        Wait-Process -Id $proc.Id -Timeout 10 -ErrorAction SilentlyContinue
    }
    Write-Host "Smoke check OK (process started; stopped by the build script)."
}

if (-not $SkipZip) {
    Write-Host "==> Creating portable ZIP"
    Compress-Archive -Path (Join-Path $portableDir "*") -DestinationPath $zipPath -Force
    Write-Host "Wrote $zipPath"
}

Write-Host "==> Portable build complete"
Write-Host "Folder: $portableDir"
if (-not $SkipZip) {
    Write-Host "ZIP   : $zipPath"
}
Write-Host "User data (NOT packaged; preserved by installer): %APPDATA%\civ4-turn-relay"
