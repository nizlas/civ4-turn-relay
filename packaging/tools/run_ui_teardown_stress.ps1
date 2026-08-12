<#
.SYNOPSIS
  Re-run UI shutdown tests in separate Python processes (Windows stress aid).

.DESCRIPTION
  Addresses intermittent PySide6 interpreter-teardown heap corruption observed
  on Windows after otherwise-green pytest-qt runs. Each iteration launches a
  fresh Python process. A non-zero exit (including abort/crash) fails the gate.

  Usage (from repo root, with the project venv activated):

    powershell -File packaging\tools\run_ui_teardown_stress.ps1 -Iterations 20
#>
[CmdletBinding()]
param(
    [int]$Iterations = 10,
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
)

$ErrorActionPreference = "Stop"
$env:QT_QPA_PLATFORM = "offscreen"

if (-not (Test-Path -LiteralPath $RepoRoot)) {
    throw "RepoRoot does not exist: $RepoRoot"
}
$RepoRoot = (Resolve-Path -LiteralPath $RepoRoot).Path
$pyproject = Join-Path $RepoRoot "pyproject.toml"
if (-not (Test-Path -LiteralPath $pyproject)) {
    throw "RepoRoot must contain pyproject.toml (got: $RepoRoot)"
}

$nodeIds = @(
    "tests/ui/test_tray_and_quit.py::test_quit_when_idle_needs_no_confirmation",
    "tests/ui/test_tray_and_quit.py::test_shutdown_is_idempotent_and_blocks_further_commands",
    "tests/ui/test_controller.py::test_shutdown_stops_thread_and_is_idempotent",
    "tests/ui/test_tray_and_quit.py::test_successful_gated_quit_authorizes_application_exit"
)

Write-Host "RepoRoot   : $RepoRoot"
Write-Host "Iterations : $Iterations"

Push-Location -LiteralPath $RepoRoot
try {
    for ($i = 1; $i -le $Iterations; $i++) {
        Write-Host "==> Iteration $i / $Iterations"
        foreach ($node in $nodeIds) {
            & python -m pytest $node -q --tb=line
            if ($LASTEXITCODE -ne 0) {
                throw "UI teardown stress failed on iteration $i for $node (exit $LASTEXITCODE)"
            }
        }
    }
}
finally {
    Pop-Location
}

Write-Host "All $Iterations iterations completed with exit code 0."
