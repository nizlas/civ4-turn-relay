"""Minimal PySide6 desktop UI (P8): presentation and command dispatch only.

The presenter is pure Python and importable without Qt; every other module
is loaded lazily so ``import civ4_turn_relay.ui`` (and the presenter) never
pulls PySide6 in unless a Qt component is actually used.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

from civ4_turn_relay.ui.presenter import (
    MatchViewModel,
    PrimaryActionKind,
    SecondaryActionKind,
    build_view_model,
)

if TYPE_CHECKING:
    from civ4_turn_relay.ui.app import GatedQApplication, RelayApplication, main
    from civ4_turn_relay.ui.controller import (
        MatchUiSnapshot,
        MatchWorker,
        RelayWorkerHub,
    )
    from civ4_turn_relay.ui.main_window import MainWindow
    from civ4_turn_relay.ui.match_dialog import MatchEditDialog
    from civ4_turn_relay.ui.settings_dialog import GlobalSettingsDialog
    from civ4_turn_relay.ui.tray import RelayTray

__all__ = [
    "GatedQApplication",
    "GlobalSettingsDialog",
    "MainWindow",
    "MatchEditDialog",
    "MatchUiSnapshot",
    "MatchViewModel",
    "MatchWorker",
    "PrimaryActionKind",
    "RelayApplication",
    "RelayTray",
    "RelayWorkerHub",
    "SecondaryActionKind",
    "build_view_model",
    "main",
]

_LAZY_MODULES = {
    "MainWindow": "civ4_turn_relay.ui.main_window",
    "MatchEditDialog": "civ4_turn_relay.ui.match_dialog",
    "MatchUiSnapshot": "civ4_turn_relay.ui.controller",
    "MatchWorker": "civ4_turn_relay.ui.controller",
    "GlobalSettingsDialog": "civ4_turn_relay.ui.settings_dialog",
    "RelayApplication": "civ4_turn_relay.ui.app",
    "RelayTray": "civ4_turn_relay.ui.tray",
    "GatedQApplication": "civ4_turn_relay.ui.app",
    "RelayWorkerHub": "civ4_turn_relay.ui.controller",
    "main": "civ4_turn_relay.ui.app",
}


def __getattr__(name: str) -> object:
    module_name = _LAZY_MODULES.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    return getattr(importlib.import_module(module_name), name)
