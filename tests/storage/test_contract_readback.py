"""Reusable contract: full read-back fingerprints and comparison."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from civ4_turn_relay.domain import sha256_hex
from civ4_turn_relay.storage import (
    FakeStorage,
    FaultMoment,
    ObjectComparisonResult,
    Storage,
    StorageCapabilities,
    StorageCapabilityError,
    StorageOp,
    compare_stored_object,
    fingerprint_bytes,
    read_fingerprint,
)
from tests.storage.helpers import seed_tree


@pytest.mark.parametrize(
    "payload",
    [b"", b"synthetic", b"\x00\x01\x02binary-synthetic"],
)
def test_exact_byte_count_and_sha256(storage: Storage, payload: bytes) -> None:
    seed_tree(storage, "saves")
    path = "saves/object.bin"
    storage.write_file(path, payload)
    fingerprint = read_fingerprint(storage, path)
    assert fingerprint.path == path
    assert fingerprint.size_bytes == len(payload)
    assert fingerprint.sha256 == sha256_hex(payload)
    assert fingerprint.content == payload
    assert fingerprint_bytes(path, payload) == fingerprint


def test_exact_match_versus_size_or_hash_mismatch(storage: Storage) -> None:
    seed_tree(storage, "saves")
    path = "saves/object.bin"
    payload = b"verify-me"
    storage.write_file(path, payload)
    digest = sha256_hex(payload)
    assert (
        compare_stored_object(
            storage, path, expected_size=len(payload), expected_sha256=digest
        )
        is ObjectComparisonResult.EXACT_MATCH
    )
    assert (
        compare_stored_object(
            storage, path, expected_size=len(payload) + 1, expected_sha256=digest
        )
        is ObjectComparisonResult.MISMATCH
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


def test_corrupted_injected_read_differs_while_stored_bytes_remain(
    fake: FakeStorage,
) -> None:
    seed_tree(fake, "saves")
    path = "saves/object.bin"
    payload = b"stored-intact"
    fake.write_file(path, payload)
    fake.faults.inject_read_corruption(occurrence=1)
    corrupted = fake.read_file(path)
    assert corrupted != payload
    assert sha256_hex(corrupted) != sha256_hex(payload)
    # Stored bytes unchanged; a subsequent clean read matches the original.
    assert fake.snapshot().files[path] == payload
    assert fake.read_file(path) == payload


def test_complete_readback_capability_required(
    make_storage: Callable[..., FakeStorage],
) -> None:
    storage = make_storage(
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


def test_before_read_fault_leaves_state_unchanged(fake: FakeStorage) -> None:
    seed_tree(fake, "saves")
    fake.write_file("saves/object.bin", b"x")
    fake.faults.inject(StorageOp.READ, moment=FaultMoment.BEFORE, occurrence=1)
    with pytest.raises(Exception):
        fake.read_file("saves/object.bin")
    assert fake.snapshot().files["saves/object.bin"] == b"x"
