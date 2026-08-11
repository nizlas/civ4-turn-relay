"""Reusable contract: exclusive atomic mkdir."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from civ4_turn_relay.storage import (
    FakeStorage,
    Storage,
    StorageAlreadyExistsError,
    StorageCapabilities,
    StorageCapabilityError,
)
from tests.storage.helpers import seed_tree


def test_first_exclusive_mkdir_succeeds(storage: Storage) -> None:
    storage.mkdir("locks")
    storage.mkdir("locks/upload.lock")
    assert [entry.name for entry in storage.list_dir("locks")] == ["upload.lock"]


def test_second_mkdir_same_path_already_exists(storage: Storage) -> None:
    seed_tree(storage, "locks")
    storage.mkdir("locks/upload.lock")
    with pytest.raises(StorageAlreadyExistsError) as exc_info:
        storage.mkdir("locks/upload.lock")
    assert exc_info.value.path == "locks/upload.lock"
    # No split/partial state: still exactly one lock directory child.
    assert [entry.name for entry in storage.list_dir("locks")] == ["upload.lock"]


def test_mkdir_race_models_lock_contention(storage: Storage) -> None:
    """A second exclusive mkdir against an existing lock path is the race."""
    seed_tree(storage, "locks")
    storage.mkdir("locks/upload.lock")
    with pytest.raises(StorageAlreadyExistsError):
        storage.mkdir("locks/upload.lock")


def test_unsupported_exclusive_mkdir_fails_safely(
    make_storage: Callable[..., FakeStorage],
) -> None:
    storage = make_storage(
        capabilities=StorageCapabilities(
            exclusive_mkdir=False,
            atomic_replace=True,
            atomic_publish_no_replace=True,
            complete_readback=True,
        )
    )
    with pytest.raises(StorageCapabilityError):
        storage.mkdir("locks")
    # No partial directory was created.
    assert storage.snapshot().directories == frozenset()
