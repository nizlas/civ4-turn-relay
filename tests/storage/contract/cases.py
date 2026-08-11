"""Reusable storage-contract cases (port + StorageProvider only).

These functions contain the assertions. Pytest modules invoke them through
every registered :class:`StorageProvider` so P6 can later bind Paramiko without
copying the case bodies.
"""

from __future__ import annotations

import pytest

from civ4_turn_relay.domain import sha256_hex
from civ4_turn_relay.storage import (
    ObjectComparisonResult,
    Storage,
    StorageAlreadyExistsError,
    StorageInvalidPathError,
    StorageNotEmptyError,
    StorageNotFoundError,
    StorageWrongKindError,
    compare_stored_object,
    fingerprint_bytes,
    read_fingerprint,
)
from tests.storage.helpers import seed_tree


def case_provider_reports_required_capabilities(storage: Storage) -> None:
    """Assert the adapter's actual capabilities cover the P2 contract surface."""
    caps = storage.capabilities()
    assert caps.exclusive_mkdir is True
    assert caps.atomic_replace is True
    assert caps.atomic_publish_no_replace is True
    assert caps.complete_readback is True


def case_write_read_round_trip(storage: Storage) -> None:
    seed_tree(storage, "temporary")
    storage.write_file("temporary/a.bin", b"synthetic-a")
    assert storage.read_file("temporary/a.bin") == b"synthetic-a"


def case_write_without_overwrite_refuses_existing_file(storage: Storage) -> None:
    seed_tree(storage, "temporary")
    storage.write_file("temporary/a.bin", b"one")
    with pytest.raises(StorageAlreadyExistsError):
        storage.write_file("temporary/a.bin", b"two", overwrite=False)
    assert storage.read_file("temporary/a.bin") == b"one"


def case_write_with_overwrite_replaces_file_bytes(storage: Storage) -> None:
    seed_tree(storage, "temporary")
    storage.write_file("temporary/a.bin", b"one")
    storage.write_file("temporary/a.bin", b"two", overwrite=True)
    assert storage.read_file("temporary/a.bin") == b"two"


def case_parent_not_found_on_write_and_mkdir(storage: Storage) -> None:
    with pytest.raises(StorageNotFoundError) as exc_info:
        storage.write_file("missing/child.bin", b"x")
    assert exc_info.value.path == "missing"
    with pytest.raises(StorageNotFoundError) as exc_info:
        storage.mkdir("missing/child")
    assert exc_info.value.path == "missing"


def case_file_versus_directory_errors(storage: Storage) -> None:
    seed_tree(storage, "temporary")
    storage.write_file("temporary/file.bin", b"x")
    with pytest.raises(StorageWrongKindError):
        storage.mkdir("temporary/file.bin")
    with pytest.raises(StorageWrongKindError):
        storage.read_file("temporary")
    with pytest.raises(StorageWrongKindError):
        storage.list_dir("temporary/file.bin")
    with pytest.raises(StorageWrongKindError):
        storage.remove_dir("temporary/file.bin")
    with pytest.raises(StorageWrongKindError):
        storage.remove_file("temporary")


def case_deterministic_immediate_child_listing(storage: Storage) -> None:
    seed_tree(storage, "rootish", "rootish/b-dir", "rootish/a-dir")
    storage.write_file("rootish/c-file", b"c")
    storage.write_file("rootish/a-file", b"a")
    seed_tree(storage, "rootish/a-dir/deeper")
    storage.write_file("rootish/a-dir/deeper/x.bin", b"x")
    names = [(entry.name, entry.kind.value) for entry in storage.list_dir("rootish")]
    assert names == [
        ("a-dir", "directory"),
        ("a-file", "file"),
        ("b-dir", "directory"),
        ("c-file", "file"),
    ]


def case_remove_file_and_empty_directory(storage: Storage) -> None:
    seed_tree(storage, "temporary", "temporary/sub")
    storage.write_file("temporary/a.bin", b"x")
    storage.remove_file("temporary/a.bin")
    with pytest.raises(StorageNotFoundError):
        storage.read_file("temporary/a.bin")
    storage.remove_dir("temporary/sub")
    with pytest.raises(StorageNotFoundError):
        storage.list_dir("temporary/sub")


def case_non_empty_directory_cannot_be_removed(storage: Storage) -> None:
    seed_tree(storage, "temporary", "temporary/sub")
    storage.write_file("temporary/sub/a.bin", b"x")
    with pytest.raises(StorageNotEmptyError):
        storage.remove_dir("temporary/sub")
    assert storage.read_file("temporary/sub/a.bin") == b"x"


def case_path_traversal_rejected_before_state_changes(
    storage: Storage, bad_path: str
) -> None:
    seed_tree(storage, "temporary")
    storage.write_file("temporary/keep.bin", b"keep")
    with pytest.raises(StorageInvalidPathError):
        storage.write_file(bad_path, b"evil")
    with pytest.raises(StorageInvalidPathError):
        storage.mkdir(bad_path)
    with pytest.raises(StorageInvalidPathError):
        storage.read_file(bad_path)
    assert storage.read_file("temporary/keep.bin") == b"keep"


def case_not_found_on_missing_file_and_directory(storage: Storage) -> None:
    with pytest.raises(StorageNotFoundError):
        storage.read_file("nope.bin")
    with pytest.raises(StorageNotFoundError):
        storage.list_dir("nope")
    with pytest.raises(StorageNotFoundError):
        storage.remove_file("nope.bin")
    with pytest.raises(StorageNotFoundError):
        storage.remove_dir("nope")


def case_first_exclusive_mkdir_succeeds(storage: Storage) -> None:
    storage.mkdir("locks")
    storage.mkdir("locks/upload.lock")
    assert [entry.name for entry in storage.list_dir("locks")] == ["upload.lock"]


def case_second_mkdir_same_path_already_exists(storage: Storage) -> None:
    seed_tree(storage, "locks")
    storage.mkdir("locks/upload.lock")
    with pytest.raises(StorageAlreadyExistsError) as exc_info:
        storage.mkdir("locks/upload.lock")
    assert exc_info.value.path == "locks/upload.lock"
    assert [entry.name for entry in storage.list_dir("locks")] == ["upload.lock"]


def case_mkdir_race_models_lock_contention(storage: Storage) -> None:
    seed_tree(storage, "locks")
    storage.mkdir("locks/upload.lock")
    with pytest.raises(StorageAlreadyExistsError):
        storage.mkdir("locks/upload.lock")


def case_missing_destination_publishes_atomically(storage: Storage) -> None:
    seed_tree(storage, "temporary", "saves")
    storage.write_file("temporary/op.upload", b"synthetic-save")
    storage.publish_no_replace("temporary/op.upload", "saves/000001_aabbccddeeff.sav")
    assert storage.read_file("saves/000001_aabbccddeeff.sav") == b"synthetic-save"
    with pytest.raises(StorageNotFoundError):
        storage.read_file("temporary/op.upload")


def case_existing_same_content_destination_not_overwritten(storage: Storage) -> None:
    seed_tree(storage, "temporary", "saves")
    payload = b"synthetic-same"
    storage.write_file("saves/000001_aabbccddeeff.sav", payload)
    storage.write_file("temporary/op.upload", payload)
    with pytest.raises(StorageAlreadyExistsError) as exc_info:
        storage.publish_no_replace(
            "temporary/op.upload", "saves/000001_aabbccddeeff.sav"
        )
    assert exc_info.value.path == "saves/000001_aabbccddeeff.sav"
    assert storage.read_file("temporary/op.upload") == payload
    assert storage.read_file("saves/000001_aabbccddeeff.sav") == payload


def case_existing_different_content_destination_not_overwritten(
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


def case_caller_verifies_existing_object_before_reuse(storage: Storage) -> None:
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


def case_replace_absent_destination(storage: Storage) -> None:
    seed_tree(storage, "temporary")
    storage.write_file("temporary/manifest-op.json", b'{"schema_version":1}')
    storage.atomic_replace("temporary/manifest-op.json", "manifest.json")
    assert storage.read_file("manifest.json") == b'{"schema_version":1}'
    with pytest.raises(StorageNotFoundError):
        storage.read_file("temporary/manifest-op.json")


def case_replace_existing_file(storage: Storage) -> None:
    seed_tree(storage, "temporary")
    storage.write_file("manifest.json", b'{"protocol_sequence":0}')
    storage.write_file("temporary/manifest-op.json", b'{"protocol_sequence":1}')
    storage.atomic_replace("temporary/manifest-op.json", "manifest.json")
    assert storage.read_file("manifest.json") == b'{"protocol_sequence":1}'
    with pytest.raises(StorageNotFoundError):
        storage.read_file("temporary/manifest-op.json")


def case_source_disappears_and_destination_has_exact_new_bytes(
    storage: Storage,
) -> None:
    seed_tree(storage, "temporary")
    new_bytes = b'{"protocol_sequence":2,"game_id":"example-match"}'
    storage.write_file("manifest.json", b"old")
    storage.write_file("temporary/manifest-op.json", new_bytes)
    storage.atomic_replace("temporary/manifest-op.json", "manifest.json")
    assert storage.read_file("manifest.json") == new_bytes
    with pytest.raises(StorageNotFoundError):
        storage.read_file("temporary/manifest-op.json")


def case_atomic_replace_refuses_directory_destination(storage: Storage) -> None:
    seed_tree(storage, "temporary", "history")
    storage.write_file("temporary/manifest-op.json", b"x")
    with pytest.raises(StorageWrongKindError):
        storage.atomic_replace("temporary/manifest-op.json", "history")
    assert storage.read_file("temporary/manifest-op.json") == b"x"


def case_exact_byte_count_and_sha256(storage: Storage, payload: bytes) -> None:
    seed_tree(storage, "saves")
    path = "saves/object.bin"
    storage.write_file(path, payload)
    fingerprint = read_fingerprint(storage, path)
    assert fingerprint.path == path
    assert fingerprint.size_bytes == len(payload)
    assert fingerprint.sha256 == sha256_hex(payload)
    assert fingerprint.content == payload
    assert fingerprint_bytes(path, payload) == fingerprint


def case_exact_match_versus_size_or_hash_mismatch(storage: Storage) -> None:
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
