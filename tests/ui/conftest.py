"""Headless Qt setup: must run before any Qt module is imported."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from civ4_turn_relay.ui.app import GatedQApplication  # noqa: E402


@pytest.fixture(scope="session")
def qapp_cls() -> type[GatedQApplication]:
    """Use the production quit-gated QApplication for all UI tests."""
    return GatedQApplication


@pytest.fixture(autouse=True)
def _reset_quit_gate(qapp: GatedQApplication) -> object:
    """Clear quit authorization between tests so gates stay independent."""
    qapp.reset_quit_authorization()
    qapp.setQuitOnLastWindowClosed(False)
    yield
    qapp.reset_quit_authorization()
    qapp.setQuitOnLastWindowClosed(False)
