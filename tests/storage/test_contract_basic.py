"""Reusable contract: create/read/list/remove and path containment."""

from __future__ import annotations

import pytest

from civ4_turn_relay.storage import (
    Storage,
    StorageAlreadyExistsError,
    StorageInvalidPathError,
    StorageNotEmptyError,
    StorageNotFoundError,
    StorageWrongKindError,
)
from tests.storage.helpers import seed_tree


def test_write_read_round_trip(storage: Storage) -> None:
    seed_tree(storage, "temporary")
    storage.write_file("temporary/a.bin", b"synthetic-a")
    assert storage.read_file("temporary/a.bin") == b"synthetic-a"


def test_write_without_overwrite_refuses_existing_file(storage: Storage) -> None:
    seed_tree(storage, "temporary")
    storage.write_file("temporary/a.bin", b"one")
    with pytest.raises(StorageAlreadyExistsError):
        storage.write_file("temporary/a.bin", b"two", overwrite=False)
    assert storage.read_file("temporary/a.bin") == b"one"


def test_write_with_overwrite_replaces_file_bytes(storage: Storage) -> None:
    seed_tree(storage, "temporary")
    storage.write_file("temporary/a.bin", b"one")
    storage.write_file("temporary/a.bin", b"two", overwrite=True)
    assert storage.read_file("temporary/a.bin") == b"two"


def test_parent_not_found_on_write_and_mkdir(storage: Storage) -> None:
    with pytest.raises(StorageNotFoundError) as exc_info:
        storage.write_file("missing/child.bin", b"x")
    assert exc_info.value.path == "missing"
    with pytest.raises(StorageNotFoundError) as exc_info:
        storage.mkdir("missing/child")
    assert exc_info.value.path == "missing"


def test_file_versus_directory_errors(storage: Storage) -> None:
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


def test_deterministic_immediate_child_listing(storage: Storage) -> None:
    seed_tree(storage, "rootish", "rootish/b-dir", "rootish/a-dir")
    storage.write_file("rootish/c-file", b"c")
    storage.write_file("rootish/a-file", b"a")
    # Deeper entries must not appear in the immediate listing.
    seed_tree(storage, "rootish/a-dir/deeper")
    storage.write_file("rootish/a-dir/deeper/x.bin", b"x")
    names = [(entry.name, entry.kind.value) for entry in storage.list_dir("rootish")]
    assert names == [
        ("a-dir", "directory"),
        ("a-file", "file"),
        ("b-dir", "directory"),
        ("c-file", "file"),
    ]


def test_remove_file_and_empty_directory(storage: Storage) -> None:
    seed_tree(storage, "temporary", "temporary/sub")
    storage.write_file("temporary/a.bin", b"x")
    storage.remove_file("temporary/a.bin")
    with pytest.raises(StorageNotFoundError):
        storage.read_file("temporary/a.bin")
    storage.remove_dir("temporary/sub")
    with pytest.raises(StorageNotFoundError):
        storage.list_dir("temporary/sub")


def test_non_empty_directory_cannot_be_removed(storage: Storage) -> None:
    seed_tree(storage, "temporary", "temporary/sub")
    storage.write_file("temporary/sub/a.bin", b"x")
    with pytest.raises(StorageNotEmptyError):
        storage.remove_dir("temporary/sub")
    assert storage.read_file("temporary/sub/a.bin") == b"x"


@pytest.mark.parametrize(
    "bad_path",
    [
        "",
        "/absolute",
        "a/../b",
        "..",
        "./a",
        "a//b",
        "a\\b",
        "a/",
    ],
)
def test_path_traversal_rejected_before_state_changes(
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


def test_not_found_on_missing_file_and_directory(storage: Storage) -> None:
    with pytest.raises(StorageNotFoundError):
        storage.read_file("nope.bin")
    with pytest.raises(StorageNotFoundError):
        storage.list_dir("nope")
    with pytest.raises(StorageNotFoundError):
        storage.remove_file("nope.bin")
    with pytest.raises(StorageNotFoundError):
        storage.remove_dir("nope")
