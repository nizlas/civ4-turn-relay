# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the civ4-turn-relay GUI (portable onedir layout).

Invoked by packaging/build_windows.ps1. Does not bundle .env, saves, local
match data, logs, SSH keys, known_hosts, or test fixtures.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from PyInstaller.utils.hooks import collect_all

SPECDIR = Path(SPECPATH).resolve()
ROOT = SPECDIR.parent
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
VERSION = PYPROJECT["project"]["version"]
APP_NAME = "civ4-turn-relay"

datas: list[tuple[str, str]] = []
binaries: list[tuple[str, str]] = []
hiddenimports: list[str] = [
    "civ4_turn_relay.ui.app",
    "civ4_turn_relay.process.windows",
    "dotenv",
]

for package in ("PySide6", "paramiko", "psutil", "watchdog", "dotenv"):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(package)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

# Ship the placeholder env template only (never a real .env).
env_example = ROOT / ".env.example"
if env_example.is_file():
    datas.append((str(env_example), "."))

a = Analysis(
    [str(ROOT / "src" / "civ4_turn_relay" / "ui" / "app.py")],
    pathex=[str(ROOT / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "pytest",
        "pytest_qt",
        "mypy",
        "ruff",
        "tests",
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=APP_NAME,
)
