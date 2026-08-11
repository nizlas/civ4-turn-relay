"""Durable local record schemas: validation and JSON round-trips."""

from __future__ import annotations

from typing import Any

import pytest

from civ4_turn_relay.domain import DomainValidationError, OperationalState
from civ4_turn_relay.local import (
    BaselineEntry,
    DownloadedSaveRecord,
    LaunchAttemptRecord,
    MatchLocalRecords,
    OutgoingCandidateRecord,
    PlaySessionBaseline,
    ProcessAssociationRecord,
    VerifiedRemoteRecord,
)
from civ4_turn_relay.protocol import InProgressHandoff
from tests.protocol.helpers import CLIENT_A, HASH_1, HASH_2, OP_ID

HASH_3 = "c" * 64
PATH = r"C:\Placeholder\Saves\pbem\turn.CivBeyondSwordSave"
EXE = r"C:\Placeholder\Civ4BeyondSword.exe"


def _full_mapping() -> dict[str, Any]:
    return {
        "attempted_handoff_hashes": [HASH_1],
        "downloaded_save": {
            "local_path": PATH,
            "protocol_sequence": 1,
            "sha256": HASH_1,
            "size_bytes": 12,
        },
        "game_id": "example-match",
        "historically_accepted_hashes": [HASH_1],
        "in_progress_handoff": {
            "client_id": CLIENT_A,
            "game_id": "example-match",
            "operation_id": OP_ID,
            "player_id": "player_a",
            "protocol_sequence": 1,
            "sha256": HASH_3,
            "step_reached": "lock_acquired",
        },
        "last_error_class": "TRANSPORT",
        "last_operational_state": "CIV_RUNNING",
        "last_transition_reason": "civ_still_running_after_restart",
        "launch_attempt": {
            "accepted_sha256": HASH_1,
            "attempted_at": "2026-08-11T12:00:00Z",
            "protocol_sequence": 1,
        },
        "outgoing_candidate": {
            "path": PATH,
            "sha256": HASH_2,
            "size_bytes": 34,
        },
        "play_session_baseline": {
            "accepted_sha256": HASH_1,
            "entries": [
                {"path": PATH, "sha256": HASH_1, "size_bytes": 12},
            ],
            "protocol_sequence": 1,
            "recorded_at": "2026-08-11T11:59:00Z",
        },
        "process_association": {
            "accepted_sha256": HASH_1,
            "associated_at": "2026-08-11T12:00:01Z",
            "executable_path": EXE,
            "pid": 4242,
            "process_start_time_utc": "2026-08-11T12:00:01Z",
            "protocol_sequence": 1,
        },
        "processed_outgoing_hashes": [HASH_2],
        "retry_count": 1,
        "schema_version": 1,
        "verified_remote": {
            "accepted_sha256": HASH_1,
            "protocol_sequence": 1,
        },
    }


def test_match_local_records_round_trip() -> None:
    records = MatchLocalRecords.from_mapping(_full_mapping())
    assert records.last_operational_state is OperationalState.CIV_RUNNING
    assert records.in_progress_handoff is not None
    assert records.in_progress_handoff.step_reached == "lock_acquired"
    assert records.last_transition_reason == "civ_still_running_after_restart"
    data = records.to_json_bytes()
    assert data == records.to_json_bytes()
    assert MatchLocalRecords.from_json_bytes(data) == records


def test_minimal_records_defaults() -> None:
    records = MatchLocalRecords(game_id="example-match")
    assert records.processed_outgoing_hashes == ()
    assert records.attempted_handoff_hashes == ()
    assert records.in_progress_handoff is None
    assert records.retry_count == 0
    restored = MatchLocalRecords.from_json_bytes(records.to_json_bytes())
    assert restored == records


def test_nested_immutability() -> None:
    mutable = [HASH_1]
    records = MatchLocalRecords(
        game_id="example-match",
        processed_outgoing_hashes=mutable,  # type: ignore[arg-type]
        attempted_handoff_hashes=mutable,  # type: ignore[arg-type]
    )
    assert isinstance(records.processed_outgoing_hashes, tuple)
    mutable.append(HASH_2)
    assert records.processed_outgoing_hashes == (HASH_1,)
    assert records.attempted_handoff_hashes == (HASH_1,)


def test_seq0_verified_remote_requires_null_hash() -> None:
    VerifiedRemoteRecord(protocol_sequence=0, accepted_sha256=None)
    with pytest.raises(DomainValidationError) as exc_info:
        VerifiedRemoteRecord(protocol_sequence=0, accepted_sha256=HASH_1)
    assert exc_info.value.field_path == "accepted_sha256"


def test_baseline_rejects_duplicate_paths() -> None:
    entry = BaselineEntry(path=PATH, sha256=HASH_1, size_bytes=1)
    with pytest.raises(DomainValidationError) as exc_info:
        PlaySessionBaseline(
            recorded_at="2026-08-11T11:59:00Z",
            protocol_sequence=1,
            accepted_sha256=HASH_1,
            entries=(entry, entry),
        )
    assert exc_info.value.field_path == "entries[1].path"


def test_process_association_requires_positive_pid() -> None:
    with pytest.raises(DomainValidationError) as exc_info:
        ProcessAssociationRecord(
            protocol_sequence=1,
            accepted_sha256=HASH_1,
            pid=0,
            process_start_time_utc="2026-08-11T12:00:01Z",
            executable_path=EXE,
            associated_at="2026-08-11T12:00:01Z",
        )
    assert exc_info.value.field_path == "pid"


def test_unsupported_schema_version_rejected() -> None:
    mapping = _full_mapping() | {"schema_version": 2}
    with pytest.raises(DomainValidationError) as exc_info:
        MatchLocalRecords.from_mapping(mapping)
    assert exc_info.value.field_path == "schema_version"
    assert "unsupported" in exc_info.value.message


@pytest.mark.parametrize(
    ("expected_path", "overrides"),
    [
        ("game_id", {"game_id": "Bad"}),
        ("retry_count", {"retry_count": -1}),
        ("last_operational_state", {"last_operational_state": "NOT_A_STATE"}),
        ("processed_outgoing_hashes[0]", {"processed_outgoing_hashes": ["zz"]}),
        ("surprise", {"surprise": True}),
        (
            "in_progress_handoff.game_id",
            {
                "in_progress_handoff": {
                    "client_id": CLIENT_A,
                    "game_id": "other-match",
                    "operation_id": OP_ID,
                    "player_id": "player_a",
                    "protocol_sequence": 1,
                    "sha256": HASH_1,
                    "step_reached": None,
                }
            },
        ),
    ],
)
def test_invalid_match_local_records(
    expected_path: str, overrides: dict[str, Any]
) -> None:
    mapping = _full_mapping() | overrides
    with pytest.raises(DomainValidationError) as exc_info:
        MatchLocalRecords.from_mapping(mapping)
    assert exc_info.value.field_path == expected_path


def test_diagnostics_have_no_secret_shaped_fields() -> None:
    records = MatchLocalRecords.from_mapping(_full_mapping())
    rendered = repr(records) + str(records) + str(records.to_mapping())
    assert "password" not in rendered.lower()
    assert "sftp" not in rendered.lower()
    assert "private_key" not in rendered.lower()


def test_downloaded_and_outgoing_helpers() -> None:
    downloaded = DownloadedSaveRecord(
        local_path=PATH, sha256=HASH_1, size_bytes=10, protocol_sequence=2
    )
    outgoing = OutgoingCandidateRecord(path=PATH, sha256=HASH_2, size_bytes=11)
    launch = LaunchAttemptRecord(
        protocol_sequence=0,
        accepted_sha256=None,
        attempted_at="2026-08-11T12:00:00Z",
    )
    progress = InProgressHandoff(
        game_id="example-match",
        operation_id=OP_ID,
        client_id=CLIENT_A,
        player_id="player_a",
        sha256=HASH_1,
        protocol_sequence=0,
        step_reached="begin",
    )
    assert downloaded.to_mapping()["protocol_sequence"] == 2
    assert outgoing.sha256 == HASH_2
    assert launch.accepted_sha256 is None
    assert progress.protocol_sequence == 0
