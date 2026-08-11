"""Shared helpers for storage contract tests (synthetic bytes only)."""

from __future__ import annotations

from civ4_turn_relay.storage import Storage


def seed_tree(storage: Storage, *directories: str) -> None:
    """Create directories in order (parents before children)."""
    for path in directories:
        storage.mkdir(path)
