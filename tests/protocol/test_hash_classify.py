"""Foundation coverage for §6.3 hash classification (PT-03–PT-05)."""

from __future__ import annotations

import pytest

from civ4_turn_relay.protocol import (
    HashClassification,
    PriorAttemptEvidence,
    classify_candidate_hash,
)
from tests.protocol.helpers import HASH_1, HASH_2, HASH_3


@pytest.mark.pt("PT-03")
def test_pt03_foundation_previous_sender_idempotent_ack() -> None:
    result = classify_candidate_hash(
        candidate_sha256=HASH_1,
        accepted_save_hashes=(HASH_1,),
        local_player_id="player_a",
        current_player_id="player_b",
        last_sender_id="player_a",
        evidence=PriorAttemptEvidence(handoff_attempted_for_hash=True),
    )
    assert result is HashClassification.IDEMPOTENT_ACK


@pytest.mark.pt("PT-04")
def test_pt04_foundation_recipient_rejects_incoming_hash() -> None:
    result = classify_candidate_hash(
        candidate_sha256=HASH_1,
        accepted_save_hashes=(HASH_1,),
        local_player_id="player_b",
        current_player_id="player_b",
        last_sender_id="player_a",
        evidence=PriorAttemptEvidence(),
    )
    assert result is HashClassification.REJECT_INCOMING


@pytest.mark.pt("PT-05")
def test_pt05_foundation_older_accepted_hash_is_stale_replay() -> None:
    result = classify_candidate_hash(
        candidate_sha256=HASH_1,
        accepted_save_hashes=(HASH_1, HASH_2),
        local_player_id="player_a",
        current_player_id="player_c",
        last_sender_id="player_b",
        evidence=PriorAttemptEvidence(),
    )
    assert result is HashClassification.STALE_REPLAY


def test_absent_hash_is_new_handoff_candidate() -> None:
    result = classify_candidate_hash(
        candidate_sha256=HASH_3,
        accepted_save_hashes=(HASH_1, HASH_2),
        local_player_id="player_c",
        current_player_id="player_c",
        last_sender_id="player_b",
        evidence=PriorAttemptEvidence(),
    )
    assert result is HashClassification.NEW_HANDOFF_CANDIDATE


def test_journal_only_historical_acknowledgement() -> None:
    result = classify_candidate_hash(
        candidate_sha256=HASH_1,
        accepted_save_hashes=(HASH_1, HASH_2),
        local_player_id="player_a",
        current_player_id="player_c",
        last_sender_id="player_b",
        evidence=PriorAttemptEvidence(historically_accepted_for_hash=True),
    )
    assert result is HashClassification.JOURNAL_ONLY_ACK


def test_previous_sender_without_journal_is_not_idempotent_ack() -> None:
    result = classify_candidate_hash(
        candidate_sha256=HASH_1,
        accepted_save_hashes=(HASH_1,),
        local_player_id="player_a",
        current_player_id="player_b",
        last_sender_id="player_a",
        evidence=PriorAttemptEvidence(handoff_attempted_for_hash=False),
    )
    assert result is HashClassification.STALE_REPLAY
