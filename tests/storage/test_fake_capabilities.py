"""Fake-only capability-negation tests.

Disabling capabilities via FakeStorage construction is not part of the reusable
StorageProvider contract. Real adapters expose whatever the server supports;
negative capability coverage therefore stays fake-specific.
"""

from __future__ import annotations

import pytest

from civ4_turn_relay.storage import (
    FakeStorage,
    StorageCapabilities,
    StorageCapabilityError,
    StorageNotFoundError,
)
from tests.storage.helpers import seed_tree


def test_unsupported_exclusive_mkdir_fails_before_mutation() -> None:
    storage = FakeStorage(
        capabilities=StorageCapabilities(
            exclusive_mkdir=False,
            atomic_replace=True,
            atomic_publish_no_replace=True,
            complete_readback=True,
        )
    )
    with pytest.raises(StorageCapabilityError):
        storage.mkdir("locks")
    with pytest.raises(StorageNotFoundError):
        storage.list_dir("locks")


def test_unsupported_publish_capability_fails_before_mutation() -> None:
    storage = FakeStorage(
        capabilities=StorageCapabilities(
            exclusive_mkdir=True,
            atomic_replace=True,
            atomic_publish_no_replace=False,
            complete_readback=True,
        )
    )
    seed_tree(storage, "temporary", "saves")
    storage.write_file("temporary/op.upload", b"x")
    with pytest.raises(StorageCapabilityError):
        storage.publish_no_replace("temporary/op.upload", "saves/final.sav")
    assert storage.read_file("temporary/op.upload") == b"x"
    with pytest.raises(StorageNotFoundError):
        storage.read_file("saves/final.sav")


def test_missing_atomic_replace_capability_fails_before_mutation() -> None:
    storage = FakeStorage(
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


def test_complete_readback_capability_required() -> None:
    storage = FakeStorage(
        capabilities=StorageCapabilities(
            exclusive_mkdir=True,
            atomic_replace=True,
            atomic_publish_no_replace=True,
            complete_readback=False,
        )
    )
    seed_tree(storage, "saves")
    storage.write_file("saves/object.bin", b"x")
    with pytest.raises(StorageCapabilityError):
        storage.read_file("saves/object.bin")
