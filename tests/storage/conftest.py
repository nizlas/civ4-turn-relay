"""Fake-only fixtures for non-contract storage tests."""

from __future__ import annotations

import pytest

from civ4_turn_relay.storage import FakeStorage


@pytest.fixture
def fake() -> FakeStorage:
    return FakeStorage()
