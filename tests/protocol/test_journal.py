"""In-memory journal supplies prior-attempt evidence without disk I/O."""

from __future__ import annotations

from civ4_turn_relay.protocol import (
    HashClassification,
    InMemoryOperationJournal,
    classify_candidate_hash,
)
from tests.protocol.helpers import HASH_1


def test_in_memory_journal_builds_idempotent_ack_evidence() -> None:
    journal = InMemoryOperationJournal()
    journal.record_handoff_attempt(game_id="example-match", sha256=HASH_1)
    evidence = journal.evidence_for_hash(game_id="example-match", sha256=HASH_1)
    assert evidence.handoff_attempted_for_hash is True
    result = classify_candidate_hash(
        candidate_sha256=HASH_1,
        accepted_save_hashes=(HASH_1,),
        local_player_id="player_a",
        current_player_id="player_b",
        last_sender_id="player_a",
        evidence=evidence,
    )
    assert result is HashClassification.IDEMPOTENT_ACK
