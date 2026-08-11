"""Authoritative manifest reader coverage (PT-18, PT-38, mismatch)."""

from __future__ import annotations

import pytest

from civ4_turn_relay.domain import DomainValidationError, Manifest
from civ4_turn_relay.protocol import (
    GamePaths,
    ManifestReadOutcome,
    initialize_match,
    read_authoritative_manifest,
)
from civ4_turn_relay.storage import FakeStorage, FaultMoment, StorageOp
from tests.protocol.helpers import (
    OP_ID,
    CountingStorage,
    sample_match_config,
)


@pytest.mark.pt("PT-18")
def test_pt18_invalid_initial_manifest_rejected() -> None:
    storage = FakeStorage()
    config = sample_match_config()
    created = initialize_match(storage, config, operation_id=OP_ID)
    assert created.outcome.name == "CREATED"
    assert created.manifest is not None

    # Overwrite committed bytes with schema-invalid JSON (test-only mutation).
    storage.write_file(
        GamePaths(config.game_id).manifest,
        b'{"schema_version":1}',
        overwrite=True,
    )
    result = read_authoritative_manifest(storage, config.game_id)
    assert result.outcome is ManifestReadOutcome.INVALID
    assert result.manifest is None


@pytest.mark.pt("PT-38")
def test_pt38_invalid_manifest_schema_or_hash_list_no_state_advance() -> None:
    storage = FakeStorage()
    config = sample_match_config()
    created = initialize_match(storage, config, operation_id=OP_ID)
    assert created.manifest is not None
    before = storage.snapshot()

    bad = (
        b'{"accepted_save":null,"accepted_save_hashes":["not-a-hash"],'
        b'"current_player_id":"player_a","display_name":"Example Match",'
        b'"game_id":"example-match","last_sender_id":null,'
        b'"players":[{"display_name":"Player A","id":"player_a"},'
        b'{"display_name":"Player B","id":"player_b"}],'
        b'"previous_manifest_ref":null,'
        b'"protocol":{"last_operation_id":null,"min_client_protocol":1},'
        b'"protocol_sequence":0,"schema_version":1}\n'
    )
    storage.write_file(GamePaths(config.game_id).manifest, bad, overwrite=True)
    result = read_authoritative_manifest(storage, config.game_id)
    assert result.outcome is ManifestReadOutcome.INVALID
    after = storage.snapshot()
    # Reader must not mutate; only the intentional overwrite differs.
    assert after.directories == before.directories
    assert set(after.files) == set(before.files)


def test_directory_game_id_versus_manifest_game_id_mismatch() -> None:
    storage = FakeStorage()
    config = sample_match_config(game_id="example-match")
    created = initialize_match(storage, config, operation_id=OP_ID)
    assert created.manifest is not None

    # Rewrite manifest game_id while keeping directory name.
    mapping = created.manifest.to_mapping()
    mapping["game_id"] = "other-match"
    # Bypass Manifest validation for the mismatched bytes by hand-editing JSON
    # through a valid Manifest for other-match is impossible under example-match
    # directory constraint — write raw swapped id with otherwise valid seq0 body.
    raw = created.manifest.to_json_bytes().replace(
        b'"game_id": "example-match"', b'"game_id": "other-match"'
    )
    storage.write_file(GamePaths("example-match").manifest, raw, overwrite=True)
    # Confirm the swapped bytes would parse as a Manifest for other-match.
    assert Manifest.from_json_bytes(raw).game_id == "other-match"

    result = read_authoritative_manifest(storage, "example-match")
    assert result.outcome is ManifestReadOutcome.GAME_ID_MISMATCH
    assert result.manifest is None


def test_missing_manifest_outcome() -> None:
    storage = FakeStorage()
    storage.mkdir("example-match")
    result = read_authoritative_manifest(storage, "example-match")
    assert result.outcome is ManifestReadOutcome.MISSING


def test_transport_failure_on_manifest_read() -> None:
    storage = FakeStorage()
    config = sample_match_config()
    assert initialize_match(storage, config, operation_id=OP_ID).initialized
    storage.faults.inject(StorageOp.READ, moment=FaultMoment.BEFORE, occurrence=1)
    result = read_authoritative_manifest(storage, config.game_id)
    assert result.outcome is ManifestReadOutcome.TRANSPORT_FAILURE


def test_repeated_join_reads_do_not_mutate_storage() -> None:
    storage = FakeStorage()
    config = sample_match_config()
    assert initialize_match(storage, config, operation_id=OP_ID).initialized
    before = storage.snapshot()
    for _ in range(3):
        result = read_authoritative_manifest(storage, config.game_id)
        assert result.outcome is ManifestReadOutcome.OK
    assert storage.snapshot() == before


def test_validation_before_io_rejects_bad_game_id() -> None:
    inner = FakeStorage()
    storage = CountingStorage(inner)
    with pytest.raises(DomainValidationError):
        read_authoritative_manifest(storage, "../evil")
    assert storage.calls == []


def test_manifest_path_as_directory_is_invalid_not_missing() -> None:
    storage = FakeStorage()
    paths = GamePaths("example-match")
    storage.mkdir(paths.root)
    storage.mkdir(paths.manifest)
    before = storage.snapshot()
    result = read_authoritative_manifest(storage, "example-match")
    assert result.outcome is ManifestReadOutcome.INVALID
    assert result.manifest is None
    assert result.raw_bytes is None
    assert storage.snapshot() == before


def test_read_exposes_exact_storage_raw_bytes() -> None:
    import json

    storage = FakeStorage()
    config = sample_match_config()
    created = initialize_match(storage, config, operation_id=OP_ID)
    assert created.manifest is not None
    paths = GamePaths(config.game_id)

    # Overwrite with noncanonical but valid JSON for the same semantic manifest.
    mapping = created.manifest.to_mapping()
    noncanonical_obj = {key: mapping[key] for key in reversed(list(mapping))}
    noncanonical = json.dumps(
        noncanonical_obj, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    storage.write_file(paths.manifest, noncanonical, overwrite=True)

    result = read_authoritative_manifest(storage, config.game_id)
    assert result.outcome is ManifestReadOutcome.OK
    assert result.raw_bytes == noncanonical
    assert result.raw_bytes == storage.read_file(paths.manifest)
    assert result.raw_bytes != created.manifest.to_json_bytes()
    assert result.manifest == created.manifest
    assert type(result.raw_bytes) is bytes


def test_manifest_read_result_invariants_for_non_ok_outcomes() -> None:
    from civ4_turn_relay.protocol import ManifestReadResult

    storage = FakeStorage()
    missing = read_authoritative_manifest(storage, "example-match")
    assert missing.outcome is ManifestReadOutcome.MISSING
    assert missing.manifest is None
    assert missing.raw_bytes is None

    storage.mkdir("example-match")
    storage.write_file("example-match/manifest.json", b"{not-json")
    invalid = read_authoritative_manifest(storage, "example-match")
    assert invalid.outcome is ManifestReadOutcome.INVALID
    assert invalid.manifest is None
    assert invalid.raw_bytes == b"{not-json"

    good = initialize_match(
        FakeStorage(), sample_match_config(game_id="other-match"), operation_id=OP_ID
    )
    assert good.manifest is not None
    mismatched_bytes = good.manifest.to_json_bytes()
    storage.write_file("example-match/manifest.json", mismatched_bytes, overwrite=True)
    mismatch = read_authoritative_manifest(storage, "example-match")
    assert mismatch.outcome is ManifestReadOutcome.GAME_ID_MISMATCH
    assert mismatch.manifest is None
    assert mismatch.raw_bytes == mismatched_bytes

    next_read = storage.faults.call_count(StorageOp.READ) + 1
    storage.faults.inject(
        StorageOp.READ, moment=FaultMoment.BEFORE, occurrence=next_read
    )
    transport = read_authoritative_manifest(storage, "example-match")
    assert transport.outcome is ManifestReadOutcome.TRANSPORT_FAILURE
    assert transport.manifest is None
    assert transport.raw_bytes is None

    with pytest.raises(DomainValidationError):
        ManifestReadResult(ManifestReadOutcome.OK, manifest=None, raw_bytes=b"{}")
    with pytest.raises(DomainValidationError):
        ManifestReadResult(ManifestReadOutcome.MISSING, raw_bytes=b"unexpected")
    with pytest.raises(DomainValidationError):
        ManifestReadResult(
            ManifestReadOutcome.INVALID,
            manifest=good.manifest,
            raw_bytes=b"{}",
        )
    with pytest.raises(DomainValidationError):
        ManifestReadResult(
            ManifestReadOutcome.INVALID,
            raw_bytes=bytearray(b"{}"),  # type: ignore[arg-type]
        )
