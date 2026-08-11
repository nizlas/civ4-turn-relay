"""Durable handoff journal survives LocalStore restart and preserves state."""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

import pytest

from civ4_turn_relay.domain import DomainValidationError, OperationalState, sha256_hex
from civ4_turn_relay.local import (
    DurableHandoffJournal,
    LocalStore,
    LocalStoreIOError,
    MatchLocalRecords,
)
from civ4_turn_relay.protocol import (
    HandoffOutcome,
    HandoffRequest,
    HashClassification,
    InProgressHandoff,
    classify_candidate_hash,
    commit_handoff,
)
from tests.protocol.helpers import (
    CLIENT_A,
    HASH_1,
    HASH_2,
    NOW_UTC,
    OP_ID,
    SAVE_NAME,
    initialize_ready_match,
)

SAVE_A = b"synthetic-outgoing-save-bytes-player-a-v1"


def _journal(root: Path, game_id: str = "example-match") -> DurableHandoffJournal:
    return DurableHandoffJournal(LocalStore(root), game_id=game_id)


def test_durable_journal_survives_reload(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    journal.begin(
        InProgressHandoff(
            game_id="example-match",
            operation_id=OP_ID,
            client_id=CLIENT_A,
            player_id="player_a",
            sha256=HASH_1,
            protocol_sequence=1,
            step_reached="lock_acquired",
        )
    )
    journal.record_historical_acceptance(game_id="example-match", sha256=HASH_2)

    reloaded = _journal(tmp_path)
    progress = reloaded.get_in_progress()
    assert progress is not None
    assert progress.sha256 == HASH_1
    assert progress.step_reached == "lock_acquired"
    assert progress.protocol_sequence == 1
    evidence = reloaded.evidence_for_hash(game_id="example-match", sha256=HASH_1)
    assert evidence.handoff_attempted_for_hash is True
    evidence2 = reloaded.evidence_for_hash(game_id="example-match", sha256=HASH_2)
    assert evidence2.historically_accepted_for_hash is True
    assert evidence2.handoff_attempted_for_hash is False

    reloaded.mark_step("temp_uploaded")
    advanced = _journal(tmp_path).get_in_progress()
    assert advanced is not None
    assert advanced.step_reached == "temp_uploaded"

    reloaded.clear()
    assert _journal(tmp_path).get_in_progress() is None
    still = _journal(tmp_path).evidence_for_hash(game_id="example-match", sha256=HASH_1)
    assert still.handoff_attempted_for_hash is True


def test_journal_mutation_preserves_unrelated_state(tmp_path: Path) -> None:
    store = LocalStore(tmp_path)
    store.write_match_state(
        MatchLocalRecords(
            game_id="example-match",
            retry_count=5,
            last_operational_state=OperationalState.UPLOADING,
            last_transition_reason="upload_started",
            processed_outgoing_hashes=(HASH_2,),
        )
    )
    journal = DurableHandoffJournal(store, game_id="example-match")
    journal.begin(
        InProgressHandoff(
            game_id="example-match",
            operation_id=OP_ID,
            client_id=CLIENT_A,
            player_id="player_a",
            sha256=HASH_1,
            protocol_sequence=0,
            step_reached="begin",
        )
    )
    state = store.load_match_state("example-match")
    assert state.retry_count == 5
    assert state.last_operational_state is OperationalState.UPLOADING
    assert state.last_transition_reason == "upload_started"
    assert state.processed_outgoing_hashes == (HASH_2,)
    assert state.in_progress_handoff is not None


def test_ordinary_state_mutation_preserves_journal(tmp_path: Path) -> None:
    store = LocalStore(tmp_path)
    journal = DurableHandoffJournal(store, game_id="example-match")
    journal.begin(
        InProgressHandoff(
            game_id="example-match",
            operation_id=OP_ID,
            client_id=CLIENT_A,
            player_id="player_a",
            sha256=HASH_1,
            protocol_sequence=1,
            step_reached="lock_acquired",
        )
    )
    store.update_match_state(
        "example-match",
        lambda records: replace(
            records,
            retry_count=7,
            last_operational_state=OperationalState.ERROR,
            last_error_class="TRANSPORT",
        ),
    )
    progress = DurableHandoffJournal(store, game_id="example-match").get_in_progress()
    assert progress is not None
    assert progress.sha256 == HASH_1
    assert progress.step_reached == "lock_acquired"
    state = store.load_match_state("example-match")
    assert state.retry_count == 7
    assert HASH_1 in state.attempted_handoff_hashes


def test_clear_removes_only_in_progress(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    journal.begin(
        InProgressHandoff(
            game_id="example-match",
            operation_id=OP_ID,
            client_id=CLIENT_A,
            player_id="player_a",
            sha256=HASH_1,
            protocol_sequence=1,
        )
    )
    journal.record_historical_acceptance(game_id="example-match", sha256=HASH_2)
    journal.clear()
    state = LocalStore(tmp_path).load_match_state("example-match")
    assert state.in_progress_handoff is None
    assert HASH_1 in state.attempted_handoff_hashes
    assert HASH_2 in state.historically_accepted_hashes


def test_failed_journal_mutation_preserves_last_valid_state(tmp_path: Path) -> None:
    calls = {"n": 0}

    def flaky_replace(src: str, dst: str) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            os.replace(src, dst)
            return
        raise OSError("simulated replace failure")

    store = LocalStore(tmp_path, replace_fn=flaky_replace)
    journal = DurableHandoffJournal(store, game_id="example-match")
    journal.begin(
        InProgressHandoff(
            game_id="example-match",
            operation_id=OP_ID,
            client_id=CLIENT_A,
            player_id="player_a",
            sha256=HASH_1,
            protocol_sequence=1,
            step_reached="begin",
        )
    )
    with pytest.raises(LocalStoreIOError):
        journal.mark_step("temp_uploaded")
    progress = DurableHandoffJournal(
        LocalStore(tmp_path), game_id="example-match"
    ).get_in_progress()
    assert progress is not None
    assert progress.step_reached == "begin"


def test_durable_journal_rejects_foreign_game_id(tmp_path: Path) -> None:
    journal = _journal(tmp_path, game_id="example-match")
    with pytest.raises(DomainValidationError) as exc_info:
        journal.record_handoff_attempt(game_id="other-match", sha256=HASH_1)
    assert exc_info.value.field_path == "game_id"


def test_durable_journal_builds_idempotent_ack_evidence(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    journal.record_handoff_attempt(game_id="example-match", sha256=HASH_1)
    evidence = journal.evidence_for_hash(game_id="example-match", sha256=HASH_1)
    result = classify_candidate_hash(
        candidate_sha256=HASH_1,
        accepted_save_hashes=(HASH_1,),
        local_player_id="player_a",
        current_player_id="player_b",
        last_sender_id="player_a",
        evidence=evidence,
    )
    assert result is HashClassification.IDEMPOTENT_ACK


def test_commit_handoff_persists_through_durable_journal(tmp_path: Path) -> None:
    storage, _, game_id = initialize_ready_match()
    journal = _journal(tmp_path, game_id=game_id)
    request = HandoffRequest(
        game_id=game_id,
        local_player_id="player_a",
        client_id=CLIENT_A,
        operation_id=OP_ID,
        outgoing_bytes=SAVE_A,
        original_filename=SAVE_NAME,
        now_utc=NOW_UTC,
    )
    result = commit_handoff(storage, request, journal=journal)
    assert result.outcome is HandoffOutcome.COMMITTED
    digest = sha256_hex(SAVE_A)
    reloaded = _journal(tmp_path, game_id=game_id)
    assert reloaded.get_in_progress() is None
    evidence = reloaded.evidence_for_hash(game_id=game_id, sha256=digest)
    assert evidence.handoff_attempted_for_hash is True
    assert evidence.historically_accepted_for_hash is True


def test_no_parallel_journal_file(tmp_path: Path) -> None:
    journal = _journal(tmp_path)
    journal.begin(
        InProgressHandoff(
            game_id="example-match",
            operation_id=OP_ID,
            client_id=CLIENT_A,
            player_id="player_a",
            sha256=HASH_1,
            protocol_sequence=0,
        )
    )
    match_dir = tmp_path / "matches" / "example-match"
    assert (match_dir / "state.json").is_file()
    assert not (match_dir / "handoff_journal.json").exists()
    assert list(match_dir.glob("*journal*")) == []
