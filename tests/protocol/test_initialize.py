"""Match initialization and join classification (PT-13–PT-17, PT-39)."""

from __future__ import annotations

import pytest

from civ4_turn_relay.domain import DomainValidationError, Manifest
from civ4_turn_relay.protocol import (
    GamePaths,
    InitializeOutcome,
    initialize_match,
    read_authoritative_manifest,
)
from civ4_turn_relay.storage import (
    FakeStorage,
    FaultMoment,
    StorageCapabilities,
    StorageOp,
)
from tests.protocol.helpers import (
    OP_ID,
    OP_ID_2,
    CountingStorage,
    sample_match_config,
)


@pytest.mark.pt("PT-13")
def test_pt13_successful_initialization() -> None:
    storage = FakeStorage()
    config = sample_match_config()
    result = initialize_match(storage, config, operation_id=OP_ID)
    assert result.outcome is InitializeOutcome.CREATED
    assert result.initialized
    assert result.manifest is not None
    assert result.manifest.protocol_sequence == 0
    assert result.manifest.accepted_save_hashes == ()
    assert result.manifest.accepted_save is None
    assert result.manifest.last_sender_id is None
    assert result.manifest.previous_manifest_ref is None
    assert result.manifest.protocol.last_operation_id is None
    assert result.manifest.current_player_id == "player_a"
    assert result.manifest.game_id == "example-match"

    paths = GamePaths(config.game_id)
    snap = storage.snapshot()
    assert paths.root in snap.directories
    assert paths.saves in snap.directories
    assert paths.temporary in snap.directories
    assert paths.locks in snap.directories
    assert paths.history in snap.directories
    assert paths.manifest in snap.files
    assert paths.temporary_manifest(OP_ID) not in snap.files
    assert snap.files[paths.manifest] == result.manifest.to_json_bytes()


@pytest.mark.pt("PT-14")
def test_pt14_duplicate_game_id_create_does_not_overwrite() -> None:
    storage = FakeStorage()
    config = sample_match_config()
    first = initialize_match(storage, config, operation_id=OP_ID)
    assert first.outcome is InitializeOutcome.CREATED
    assert first.manifest is not None
    before = storage.snapshot()

    second = initialize_match(storage, config, operation_id=OP_ID_2)
    assert second.outcome is InitializeOutcome.JOINED_EXISTING
    assert second.manifest == first.manifest
    assert storage.snapshot() == before


@pytest.mark.pt("PT-15")
def test_pt15_existing_valid_match_joined_without_reinitialization() -> None:
    storage = FakeStorage()
    config = sample_match_config()
    created = initialize_match(storage, config, operation_id=OP_ID)
    assert created.manifest is not None
    before = storage.snapshot()

    # Different local config values must not wipe the remote match.
    other_local = sample_match_config(local_player_id="player_b")
    joined = initialize_match(storage, other_local, operation_id=OP_ID_2)
    assert joined.outcome is InitializeOutcome.JOINED_EXISTING
    assert joined.manifest == created.manifest
    assert storage.snapshot() == before


@pytest.mark.pt("PT-16")
def test_pt16_crash_before_init_commit_no_valid_match() -> None:
    storage = FakeStorage()
    config = sample_match_config()
    paths = GamePaths(config.game_id)

    # Fail before atomic replace: temp may exist; no valid committed match.
    storage.faults.inject(
        StorageOp.ATOMIC_REPLACE, moment=FaultMoment.BEFORE, occurrence=1
    )
    failed = initialize_match(storage, config, operation_id=OP_ID)
    assert failed.outcome is InitializeOutcome.TRANSPORT_FAILURE
    assert failed.manifest is None
    read = read_authoritative_manifest(storage, config.game_id)
    assert read.outcome.name == "MISSING"
    assert paths.root in storage.snapshot().directories

    # Retry must require repair — not silently finish the incomplete tree.
    retry = initialize_match(storage, config, operation_id=OP_ID_2)
    assert retry.outcome is InitializeOutcome.INCOMPLETE_OR_CONFLICTING
    assert retry.manifest is None
    assert paths.manifest not in storage.snapshot().files


@pytest.mark.pt("PT-17")
def test_pt17_incomplete_existing_directory_requires_repair() -> None:
    storage = FakeStorage()
    storage.mkdir("example-match")
    storage.mkdir("example-match/saves")
    result = initialize_match(storage, sample_match_config(), operation_id=OP_ID)
    assert result.outcome is InitializeOutcome.INCOMPLETE_OR_CONFLICTING
    assert result.manifest is None
    assert "example-match/manifest.json" not in storage.snapshot().files
    assert "example-match/temporary" not in storage.snapshot().directories


@pytest.mark.pt("PT-39")
def test_pt39_traversal_game_id_rejected_before_any_storage_call() -> None:
    inner = FakeStorage()
    storage = CountingStorage(inner)

    with pytest.raises(DomainValidationError):
        sample_match_config(game_id="../evil")
    assert storage.calls == []

    with pytest.raises(DomainValidationError):
        GamePaths("a/../b")
    assert storage.calls == []

    with pytest.raises(DomainValidationError):
        read_authoritative_manifest(storage, "..")
    assert storage.calls == []

    with pytest.raises(DomainValidationError):
        initialize_match(storage, sample_match_config(), operation_id="not-a-uuid")
    assert storage.calls == []


def test_deterministic_initial_manifest_bytes() -> None:
    storage = FakeStorage()
    config = sample_match_config()
    result = initialize_match(storage, config, operation_id=OP_ID)
    assert result.manifest is not None
    expected = Manifest(
        schema_version=1,
        game_id="example-match",
        display_name="Example Match",
        players=config.players,
        protocol_sequence=0,
        current_player_id="player_a",
        last_sender_id=None,
        accepted_save=None,
        accepted_save_hashes=(),
        previous_manifest_ref=None,
        protocol=result.manifest.protocol,
    )
    assert result.manifest.to_json_bytes() == expected.to_json_bytes()
    stored = storage.read_file(GamePaths(config.game_id).manifest)
    assert stored == expected.to_json_bytes()


def test_fault_before_temporary_write() -> None:
    storage = FakeStorage()
    config = sample_match_config()
    storage.faults.inject(StorageOp.WRITE, moment=FaultMoment.BEFORE, occurrence=1)
    result = initialize_match(storage, config, operation_id=OP_ID)
    assert result.outcome is InitializeOutcome.TRANSPORT_FAILURE
    assert GamePaths(config.game_id).manifest not in storage.snapshot().files
    retry = initialize_match(storage, config, operation_id=OP_ID_2)
    assert retry.outcome is InitializeOutcome.INCOMPLETE_OR_CONFLICTING


def test_fault_after_temporary_write_before_replace() -> None:
    storage = FakeStorage()
    config = sample_match_config()
    paths = GamePaths(config.game_id)
    storage.faults.inject(StorageOp.WRITE, moment=FaultMoment.AFTER, occurrence=1)
    result = initialize_match(storage, config, operation_id=OP_ID)
    assert result.outcome is InitializeOutcome.TRANSPORT_FAILURE
    assert paths.manifest not in storage.snapshot().files
    assert paths.temporary_manifest(OP_ID) in storage.snapshot().files
    retry = initialize_match(storage, config, operation_id=OP_ID_2)
    assert retry.outcome is InitializeOutcome.INCOMPLETE_OR_CONFLICTING


def test_fault_before_atomic_replace() -> None:
    storage = FakeStorage()
    config = sample_match_config()
    storage.faults.inject(
        StorageOp.ATOMIC_REPLACE, moment=FaultMoment.BEFORE, occurrence=1
    )
    result = initialize_match(storage, config, operation_id=OP_ID)
    assert result.outcome is InitializeOutcome.TRANSPORT_FAILURE
    assert not result.initialized


def test_fault_after_atomic_replace_recovers_created_match() -> None:
    storage = FakeStorage()
    config = sample_match_config()
    storage.faults.inject(
        StorageOp.ATOMIC_REPLACE, moment=FaultMoment.AFTER, occurrence=1
    )
    result = initialize_match(storage, config, operation_id=OP_ID)
    # Mutation committed; caller learns the match exists.
    assert result.outcome is InitializeOutcome.CREATED
    assert result.manifest is not None
    assert result.manifest.protocol_sequence == 0

    before = storage.snapshot()
    retry = initialize_match(storage, config, operation_id=OP_ID_2)
    assert retry.outcome is InitializeOutcome.JOINED_EXISTING
    assert retry.manifest == result.manifest
    assert storage.snapshot() == before


def test_capability_failure_causes_no_false_initialized_result() -> None:
    storage = FakeStorage(
        capabilities=StorageCapabilities(
            exclusive_mkdir=True,
            atomic_replace=False,
            atomic_publish_no_replace=True,
            complete_readback=True,
        )
    )
    config = sample_match_config()
    result = initialize_match(storage, config, operation_id=OP_ID)
    assert result.outcome is InitializeOutcome.CAPABILITY_FAILURE
    assert result.manifest is None
    assert not result.initialized
    assert GamePaths(config.game_id).manifest not in storage.snapshot().files


def test_invalid_manifest_on_existing_root_is_typed() -> None:
    storage = FakeStorage()
    config = sample_match_config()
    paths = GamePaths(config.game_id)
    storage.mkdir(paths.root)
    for name in ("saves", "temporary", "locks", "history"):
        storage.mkdir(paths.resolve(name))
    storage.write_file(paths.manifest, b"{not-json", overwrite=False)
    before = storage.snapshot()
    result = initialize_match(storage, config, operation_id=OP_ID)
    assert result.outcome is InitializeOutcome.INVALID_MANIFEST
    assert storage.snapshot() == before


def test_game_id_mismatch_on_existing_root_preserves_remote_state() -> None:
    storage = FakeStorage()
    config = sample_match_config()
    created = initialize_match(storage, config, operation_id=OP_ID)
    assert created.manifest is not None
    raw = created.manifest.to_json_bytes().replace(
        b'"game_id": "example-match"', b'"game_id": "other-match"'
    )
    storage.write_file(GamePaths(config.game_id).manifest, raw, overwrite=True)
    before = storage.snapshot()
    result = initialize_match(storage, config, operation_id=OP_ID_2)
    assert result.outcome is InitializeOutcome.GAME_ID_MISMATCH
    assert storage.snapshot() == before


def test_all_failure_results_preserve_authoritative_remote_state() -> None:
    storage = FakeStorage()
    storage.mkdir("example-match")
    before = storage.snapshot()
    result = initialize_match(storage, sample_match_config(), operation_id=OP_ID)
    assert result.outcome is InitializeOutcome.INCOMPLETE_OR_CONFLICTING
    assert storage.snapshot() == before
