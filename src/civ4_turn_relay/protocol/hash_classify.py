"""Pure pre-upload hash classification (protocol §6.3).

Operates only on explicit verified facts. Does not read storage or files.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum, unique

from civ4_turn_relay.domain import (
    DomainValidationError,
    validate_player_id,
    validate_sha256_hex,
)


@unique
class HashClassification(Enum):
    """Classification of a candidate hash against accepted history."""

    NEW_HANDOFF_CANDIDATE = "new_handoff_candidate"
    """Hash absent from accepted history; ownership checks apply later."""

    REJECT_INCOMING = "reject_incoming"
    """Latest accepted hash submitted by the current recipient."""

    IDEMPOTENT_ACK = "idempotent_ack"
    """Latest hash from previous sender with prior handoff-attempt evidence."""

    STALE_REPLAY = "stale_replay"
    """Hash found at an older history index; no remote change."""

    JOURNAL_ONLY_ACK = "journal_only_ack"
    """Historical journal acknowledgement only; no remote ownership change."""


@dataclass(frozen=True, slots=True)
class PriorAttemptEvidence:
    """Explicit prior-attempt facts supplied by the caller (no I/O)."""

    handoff_attempted_for_hash: bool = False
    """True when the local journal records a handoff attempt for this hash."""

    historically_accepted_for_hash: bool = False
    """True when the journal notes this hash was historically accepted."""


def classify_candidate_hash(
    *,
    candidate_sha256: str,
    accepted_save_hashes: Sequence[str],
    local_player_id: str,
    current_player_id: str,
    last_sender_id: str | None,
    evidence: PriorAttemptEvidence,
) -> HashClassification:
    """Classify a candidate SHA-256 against accepted-save history (§6.3)."""
    digest = validate_sha256_hex(candidate_sha256, field_path="candidate_sha256")
    local_player_id = validate_player_id(local_player_id, field_path="local_player_id")
    current_player_id = validate_player_id(
        current_player_id, field_path="current_player_id"
    )
    if last_sender_id is not None:
        last_sender_id = validate_player_id(last_sender_id, field_path="last_sender_id")
    if not isinstance(evidence, PriorAttemptEvidence):
        raise DomainValidationError(
            "expected a PriorAttemptEvidence instance", field_path="evidence"
        )

    hashes = tuple(accepted_save_hashes)
    for index, item in enumerate(hashes):
        validate_sha256_hex(item, field_path=f"accepted_save_hashes[{index}]")

    if digest not in hashes:
        return HashClassification.NEW_HANDOFF_CANDIDATE

    latest = hashes[-1]
    if digest == latest:
        if local_player_id == current_player_id:
            return HashClassification.REJECT_INCOMING
        if (
            last_sender_id is not None
            and local_player_id == last_sender_id
            and evidence.handoff_attempted_for_hash
        ):
            return HashClassification.IDEMPOTENT_ACK
        # Known latest hash without ack/reject role: never a new handoff.
        return HashClassification.STALE_REPLAY

    # Older history entry.
    if evidence.historically_accepted_for_hash:
        return HashClassification.JOURNAL_ONLY_ACK
    return HashClassification.STALE_REPLAY
