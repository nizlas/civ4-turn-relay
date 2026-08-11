"""Reusable contract: immutable no-replace publication."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from civ4_turn_relay.domain import sha256_hex
from civ4_turn_relay.storage import (
    FakeStorage,
    ObjectComparisonResult,
    Storage,
    StorageAlreadyExistsError,
    StorageCapabilities,
    StorageCapabilityError,
    StorageNotFoundError,
    compare_stored_object,
    read_fingerprint,
)
from tests.storage.helpers import seed_tree


def test_missing_destination_publishes_atomically(storage: Storage) -> None:
    seed_tree(storage, "temporary", "saves")
    storage.write_file("temporary/op.upload", b"synthetic-save")
    storage.publish_no_replace("temporary/op.upload", "saves/000001_aabbccddeeff.sav")
    assert storage.read_file("saves/000001_aabbccddeeff.sav") == b"synthetic-save"
    with pytest.raises(StorageNotFoundError):
        storage.read_file("temporary/op.upload")


def test_existing_same_content_destination_not_overwritten(storage: Storage) -> None:
    seed_tree(storage, "temporary", "saves")
    payload = b"synthetic-same"
    storage.write_file("saves/000001_aabbccddeeff.sav", payload)
    storage.write_file("temporary/op.upload", payload)
    with pytest.raises(StorageAlreadyExistsError) as exc_info:
        storage.publish_no_replace(
            "temporary/op.upload", "saves/000001_aabbccddeeff.sav"
        )
    assert exc_info.value.path == "saves/000001_aabbccddeeff.sav"
    # Source remains; destination unchanged (caller may verify/reuse).
    assert storage.read_file("temporary/op.upload") == payload
    assert storage.read_file("saves/000001_aabbccddeeff.sav") == payload


def test_existing_different_content_destination_not_overwritten(
    storage: Storage,
) -> None:
    seed_tree(storage, "temporary", "saves")
    storage.write_file("saves/000001_aabbccddeeff.sav", b"existing-different")
    storage.write_file("temporary/op.upload", b"outgoing-new")
    with pytest.raises(StorageAlreadyExistsError):
        storage.publish_no_replace(
            "temporary/op.upload", "saves/000001_aabbccddeeff.sav"
        )
    assert storage.read_file("saves/000001_aabbccddeeff.sav") == b"existing-different"
    assert storage.read_file("temporary/op.upload") == b"outgoing-new"


def test_caller_verifies_existing_object_before_reuse(storage: Storage) -> None:
    seed_tree(storage, "saves")
    payload = b"orphan-final-save"
    path = "saves/000001_aabbccddeeff.sav"
    storage.write_file(path, payload)
    fingerprint = read_fingerprint(storage, path)
    assert fingerprint.size_bytes == len(payload)
    assert fingerprint.sha256 == sha256_hex(payload)
    assert (
        compare_stored_object(
            storage,
            path,
            expected_size=len(payload),
            expected_sha256=sha256_hex(payload),
        )
        is ObjectComparisonResult.EXACT_MATCH
    )
    assert (
        compare_stored_object(
            storage,
            path,
            expected_size=len(payload),
            expected_sha256=sha256_hex(b"other"),
        )
        is ObjectComparisonResult.MISMATCH
    )


def test_unsupported_publish_capability_fails_before_mutation(
    make_storage: Callable[..., FakeStorage],
) -> None:
    storage = make_storage(
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
    snap = storage.snapshot()
    assert "saves/final.sav" not in snap.files
