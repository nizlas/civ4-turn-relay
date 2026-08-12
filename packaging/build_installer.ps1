<#
.SYNOPSIS
  Compile the per-user Inno Setup installer using the version from pyproject.toml.

.DESCRIPTION
  Requires a prior portable build from packaging\build_windows.ps1 so that
  dist\civ4-turn-relay-<version>-win64-portable\ exists. Reads the project
  version and invokes ISCC.exe with /DMyAppVersion=<version>. Does not download
  binaries, connect to SFTP, or launch Civilization.
#>
[CmdletBinding()]
param(
    [string]$RepoRoot = "",
    [string]$DistRoot = "",
    [string]$IsccPath = ""
)

$ErrorActionPreference = "Stop"

# Windows PowerShell may evaluate parameter defaults before $PSScriptRoot is
# populated. Resolve script-relative defaults after parameter binding instead.
if (-not $RepoRoot) {
    $RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
}
if (-not $DistRoot) {
    $DistRoot = Join-Path $RepoRoot "dist"
}

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

function Resolve-Iscc {
    param([string]$Explicit)
    if ($Explicit) {
        if (-not (Test-Path -LiteralPath $Explicit)) {
            throw "ISCC.exe not found at '$Explicit'."
        }
        return (Resolve-Path -LiteralPath $Explicit).Path
    }
    $cmd = Get-Command "ISCC.exe" -ErrorAction SilentlyContinue
    if ($cmd) {
        return $cmd.Source
    }
    $candidates = @("C:\Program Files (x86)\Inno Setup 6\ISCC.exe")
    if ($env:LOCALAPPDATA) {
        $candidates += Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe"
    }
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) {
            return $candidate
        }
    }
    throw "Inno Setup compiler (ISCC.exe) was not found on PATH or in a standard per-machine/per-user location. Install Inno Setup 6 or pass -IsccPath."
}

Write-Host "==> civ4-turn-relay installer build"
$version = Get-ProjectVersion -Root $RepoRoot
$portableDir = Join-Path $DistRoot "civ4-turn-relay-$version-win64-portable"
$issPath = Join-Path $RepoRoot "packaging\installer.iss"
$iscc = Resolve-Iscc -Explicit $IsccPath

Write-Host "RepoRoot    : $RepoRoot"
Write-Host "Version     : $version"
Write-Host "PortableDir : $portableDir"
Write-Host "ISCC        : $iscc"

if (-not (Test-Path -LiteralPath $issPath)) {
    throw "Missing Inno Setup script: $issPath"
}
if (-not (Test-Path -LiteralPath $portableDir)) {
    throw "Portable build output not found at '$portableDir'. Run packaging\build_windows.ps1 first."
}
$exe = Join-Path $portableDir "civ4-turn-relay.exe"
if (-not (Test-Path -LiteralPath $exe)) {
    throw "Expected GUI executable missing: $exe"
}

Write-Host "==> Compiling installer (MyAppVersion=$version)"
& $iscc "/DMyAppVersion=$version" $issPath
if ($LASTEXITCODE -ne 0) {
    throw "ISCC failed with exit code $LASTEXITCODE"
}

$setup = Join-Path $DistRoot "civ4-turn-relay-$version-win64-setup.exe"
if (-not (Test-Path -LiteralPath $setup)) {
    throw "Expected installer output missing: $setup"
}

Write-Host "==> Installer build complete"
Write-Host "Setup: $setup"
Write-Host "User data (NOT packaged; preserved on upgrade/uninstall): %APPDATA%\civ4-turn-relay"
