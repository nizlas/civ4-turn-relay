"""Pure pre-upload hash classification (protocol §6.3).

Operates only on explicit verified facts. Does not read storage or files.

Precedence (highest first):

1. Latest accepted hash submitted by the current recipient → ``REJECT_INCOMING``
2. Latest accepted hash from ``last_sender_id`` with prior-attempt evidence →
   ``IDEMPOTENT_ACK``
3. Any candidate with historical journal acceptance not covered above →
   ``JOURNAL_ONLY_ACK``
4. Older accepted-history hash without historical journal evidence →
   ``STALE_REPLAY``
5. Hash absent from accepted history and absent historical journal evidence →
   ``NEW_HANDOFF_CANDIDATE``
6. Any other known latest hash → ``STALE_REPLAY``
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

    def __post_init__(self) -> None:
        _require_exact_bool(
            self.handoff_attempted_for_hash,
            field_path="handoff_attempted_for_hash",
        )
        _require_exact_bool(
            self.historically_accepted_for_hash,
            field_path="historically_accepted_for_hash",
        )


def _require_exact_bool(value: object, *, field_path: str) -> None:
    if type(value) is not bool:
        raise DomainValidationError(
            "expected an exact boolean",
            field_path=field_path,
        )


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

    latest = hashes[-1] if hashes else None
    is_latest = latest is not None and digest == latest
    in_history = digest in hashes
    is_older = in_history and not is_latest

    # 1. Current recipient submitting the latest accepted hash.
    if is_latest and local_player_id == current_player_id:
        return HashClassification.REJECT_INCOMING

    # 2. Previous sender with matching prior handoff-attempt evidence.
    if (
        is_latest
        and last_sender_id is not None
        and local_player_id == last_sender_id
        and evidence.handoff_attempted_for_hash
    ):
        return HashClassification.IDEMPOTENT_ACK

    # 3. Historical journal acceptance (including hashes absent remotely).
    if evidence.historically_accepted_for_hash:
        return HashClassification.JOURNAL_ONLY_ACK

    # 4. Older accepted-history entry without historical journal evidence.
    if is_older:
        return HashClassification.STALE_REPLAY

    # 5. Absent from remote history and absent historical journal evidence.
    if not in_history:
        return HashClassification.NEW_HANDOFF_CANDIDATE

    # 6. Any other known latest hash.
    return HashClassification.STALE_REPLAY
