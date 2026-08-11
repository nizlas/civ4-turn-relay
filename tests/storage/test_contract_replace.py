"""Reusable contract: atomic replace (posix-rename equivalent)."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from civ4_turn_relay.storage import (
    FakeStorage,
    Storage,
    StorageCapabilities,
    StorageCapabilityError,
    StorageNotFoundError,
    StorageWrongKindError,
)
from tests.storage.helpers import seed_tree


def test_replace_absent_destination(storage: Storage) -> None:
    seed_tree(storage, "temporary")
    storage.write_file("temporary/manifest-op.json", b'{"schema_version":1}')
    storage.atomic_replace("temporary/manifest-op.json", "manifest.json")
    assert storage.read_file("manifest.json") == b'{"schema_version":1}'
    with pytest.raises(StorageNotFoundError):
        storage.read_file("temporary/manifest-op.json")


def test_replace_existing_file(storage: Storage) -> None:
    seed_tree(storage, "temporary")
    storage.write_file("manifest.json", b'{"protocol_sequence":0}')
    storage.write_file("temporary/manifest-op.json", b'{"protocol_sequence":1}')
    storage.atomic_replace("temporary/manifest-op.json", "manifest.json")
    assert storage.read_file("manifest.json") == b'{"protocol_sequence":1}'
    with pytest.raises(StorageNotFoundError):
        storage.read_file("temporary/manifest-op.json")


def test_source_disappears_and_destination_has_exact_new_bytes(
    storage: Storage,
) -> None:
    seed_tree(storage, "temporary")
    new_bytes = b'{"protocol_sequence":2,"game_id":"example-match"}'
    storage.write_file("manifest.json", b"old")
    storage.write_file("temporary/manifest-op.json", new_bytes)
    storage.atomic_replace("temporary/manifest-op.json", "manifest.json")
    assert storage.read_file("manifest.json") == new_bytes
    snap = storage.snapshot() if isinstance(storage, FakeStorage) else None
    if snap is not None:
        assert "temporary/manifest-op.json" not in snap.files


def test_missing_atomic_replace_capability_fails_before_mutation(
    make_storage: Callable[..., FakeStorage],
) -> None:
    storage = make_storage(
        capabilities=StorageCapabilities(
            exclusive_mkdir=True,
            atomic_replace=False,
            atomic_publish_no_replace=True,
            complete_readback=True,
        )
    )
    seed_tree(storage, "temporary")
    storage.write_file("manifest.json", b"old-manifest")
    storage.write_file("temporary/manifest-op.json", b"new-manifest")
    with pytest.raises(StorageCapabilityError):
        storage.atomic_replace("temporary/manifest-op.json", "manifest.json")
    assert storage.read_file("manifest.json") == b"old-manifest"
    assert storage.read_file("temporary/manifest-op.json") == b"new-manifest"


def test_atomic_replace_refuses_directory_destination(storage: Storage) -> None:
    seed_tree(storage, "temporary", "history")
    storage.write_file("temporary/manifest-op.json", b"x")
    with pytest.raises(StorageWrongKindError):
        storage.atomic_replace("temporary/manifest-op.json", "history")
    assert storage.read_file("temporary/manifest-op.json") == b"x"
