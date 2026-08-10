"""Smoke tests for the P0 project skeleton."""

from importlib.metadata import version

import civ4_turn_relay


def test_package_importable() -> None:
    assert civ4_turn_relay.__version__ == "0.1.0"


def test_project_version_matches_package() -> None:
    assert version("civ4-turn-relay") == civ4_turn_relay.__version__
