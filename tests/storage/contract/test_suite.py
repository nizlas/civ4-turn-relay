"""Run every reusable contract case through each StorageProvider.

The case bodies live in :mod:`tests.storage.contract.cases`. This module only
binds providers; it does not duplicate assertions. Parametrization via the
``provider`` / ``storage`` fixtures proves the suite is rebindable (fake and
delegating-wrapper bindings in P2; Paramiko later in P6).
"""

from __future__ import annotations

import pytest

from civ4_turn_relay.storage import Storage
from tests.storage.contract import cases
from tests.storage.contract.provider import StorageProvider


def test_provider_reports_required_capabilities(storage: Storage) -> None:
    cases.case_provider_reports_required_capabilities(storage)


def test_write_read_round_trip(storage: Storage) -> None:
    cases.case_write_read_round_trip(storage)


def test_write_without_overwrite_refuses_existing_file(storage: Storage) -> None:
    cases.case_write_without_overwrite_refuses_existing_file(storage)


def test_write_with_overwrite_replaces_file_bytes(storage: Storage) -> None:
    cases.case_write_with_overwrite_replaces_file_bytes(storage)


def test_parent_not_found_on_write_and_mkdir(storage: Storage) -> None:
    cases.case_parent_not_found_on_write_and_mkdir(storage)


def test_file_versus_directory_errors(storage: Storage) -> None:
    cases.case_file_versus_directory_errors(storage)


def test_deterministic_immediate_child_listing(storage: Storage) -> None:
    cases.case_deterministic_immediate_child_listing(storage)


def test_remove_file_and_empty_directory(storage: Storage) -> None:
    cases.case_remove_file_and_empty_directory(storage)


def test_non_empty_directory_cannot_be_removed(storage: Storage) -> None:
    cases.case_non_empty_directory_cannot_be_removed(storage)


@pytest.mark.parametrize(
    "bad_path",
    ["", "/absolute", "a/../b", "..", "./a", "a//b", "a\\b", "a/"],
)
def test_path_traversal_rejected_before_state_changes(
    storage: Storage, bad_path: str
) -> None:
    cases.case_path_traversal_rejected_before_state_changes(storage, bad_path)


def test_not_found_on_missing_file_and_directory(storage: Storage) -> None:
    cases.case_not_found_on_missing_file_and_directory(storage)


def test_first_exclusive_mkdir_succeeds(storage: Storage) -> None:
    cases.case_first_exclusive_mkdir_succeeds(storage)


def test_second_mkdir_same_path_already_exists(storage: Storage) -> None:
    cases.case_second_mkdir_same_path_already_exists(storage)


def test_mkdir_race_models_lock_contention(storage: Storage) -> None:
    cases.case_mkdir_race_models_lock_contention(storage)


def test_missing_destination_publishes_atomically(storage: Storage) -> None:
    cases.case_missing_destination_publishes_atomically(storage)


def test_existing_same_content_destination_not_overwritten(storage: Storage) -> None:
    cases.case_existing_same_content_destination_not_overwritten(storage)


def test_existing_different_content_destination_not_overwritten(
    storage: Storage,
) -> None:
    cases.case_existing_different_content_destination_not_overwritten(storage)


def test_caller_verifies_existing_object_before_reuse(storage: Storage) -> None:
    cases.case_caller_verifies_existing_object_before_reuse(storage)


def test_replace_absent_destination(storage: Storage) -> None:
    cases.case_replace_absent_destination(storage)


def test_replace_existing_file(storage: Storage) -> None:
    cases.case_replace_existing_file(storage)


def test_source_disappears_and_destination_has_exact_new_bytes(
    storage: Storage,
) -> None:
    cases.case_source_disappears_and_destination_has_exact_new_bytes(storage)


def test_atomic_replace_refuses_directory_destination(storage: Storage) -> None:
    cases.case_atomic_replace_refuses_directory_destination(storage)


@pytest.mark.parametrize(
    "payload",
    [b"", b"synthetic", b"\x00\x01\x02binary-synthetic"],
)
def test_exact_byte_count_and_sha256(storage: Storage, payload: bytes) -> None:
    cases.case_exact_byte_count_and_sha256(storage, payload)


def test_exact_match_versus_size_or_hash_mismatch(storage: Storage) -> None:
    cases.case_exact_match_versus_size_or_hash_mismatch(storage)


def test_same_case_body_runs_on_every_provider(provider: StorageProvider) -> None:
    """Proof of reuse: identical case function, alternate provider binding."""
    cases.case_provider_reports_required_capabilities(provider.create())
    cases.case_write_read_round_trip(provider.create())
    cases.case_first_exclusive_mkdir_succeeds(provider.create())
    cases.case_missing_destination_publishes_atomically(provider.create())


def test_delegating_provider_hides_fake_only_apis() -> None:
    from civ4_turn_relay.storage import FakeStorage
    from tests.storage.contract.provider import DelegatingStorageProvider

    storage = DelegatingStorageProvider().create()
    assert not isinstance(storage, FakeStorage)
    assert not hasattr(storage, "snapshot")
    assert not hasattr(storage, "faults")
    cases.case_replace_existing_file(storage)
