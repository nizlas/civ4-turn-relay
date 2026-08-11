"""Durable :class:`~civ4_turn_relay.protocol.journal.HandoffJournal` on LocalStore.

Journal fields live inside ``matches/{game_id}/state.json`` as part of
:class:`~civ4_turn_relay.local.records.MatchLocalRecords` so ordinary state
updates and journal mutations share one coherent source of truth.
"""

from __future__ import annotations

from dataclasses import replace

from civ4_turn_relay.domain import (
    DomainValidationError,
    validate_game_id,
    validate_sha256_hex,
)
from civ4_turn_relay.local.records import MatchLocalRecords
from civ4_turn_relay.local.store import LocalStore
from civ4_turn_relay.protocol.hash_classify import PriorAttemptEvidence
from civ4_turn_relay.protocol.journal import InProgressHandoff


class DurableHandoffJournal:
    """Filesystem-backed handoff journal implementing the protocol port."""

    def __init__(self, store: LocalStore, *, game_id: str) -> None:
        if not isinstance(store, LocalStore):
            raise TypeError("store must be a LocalStore instance")
        self._store = store
        self._game_id = validate_game_id(game_id, field_path="game_id")

    @property
    def game_id(self) -> str:
        return self._game_id

    @property
    def store(self) -> LocalStore:
        return self._store

    def begin(self, record: InProgressHandoff) -> None:
        """Atomically persist an in-progress handoff (and attempt evidence)."""
        self.begin_handoff(record)

    def get_in_progress(self) -> InProgressHandoff | None:
        """Return the persisted in-progress handoff for this match, if any."""
        return self.in_progress_handoff(game_id=self._game_id)

    def mark_step(self, step_reached: str) -> None:
        """Atomically advance the persisted in-progress step."""
        if not isinstance(step_reached, str) or not step_reached:
            raise DomainValidationError(
                "expected a non-empty step name",
                field_path="step_reached",
            )

        def mutate(records: MatchLocalRecords) -> MatchLocalRecords:
            current = records.in_progress_handoff
            if current is None:
                raise DomainValidationError(
                    "no in-progress handoff to advance",
                    field_path="in_progress_handoff",
                )
            return replace(
                records,
                in_progress_handoff=InProgressHandoff(
                    game_id=current.game_id,
                    operation_id=current.operation_id,
                    client_id=current.client_id,
                    player_id=current.player_id,
                    sha256=current.sha256,
                    step_reached=step_reached,
                    protocol_sequence=current.protocol_sequence,
                ),
            )

        self._store.update_match_state(self._game_id, mutate)

    def clear(self) -> None:
        """Atomically clear only the in-progress handoff record."""
        self.clear_in_progress(game_id=self._game_id)

    def evidence_for_hash(self, *, game_id: str, sha256: str) -> PriorAttemptEvidence:
        self._require_game_id(game_id)
        digest = validate_sha256_hex(sha256, field_path="sha256")
        records = self._store.load_match_state_or_empty(self._game_id)
        return PriorAttemptEvidence(
            handoff_attempted_for_hash=digest in records.attempted_handoff_hashes,
            historically_accepted_for_hash=(
                digest in records.historically_accepted_hashes
            ),
        )

    def in_progress_handoff(self, *, game_id: str) -> InProgressHandoff | None:
        self._require_game_id(game_id)
        return self._store.load_match_state_or_empty(self._game_id).in_progress_handoff

    def begin_handoff(self, record: InProgressHandoff) -> None:
        if not isinstance(record, InProgressHandoff):
            raise TypeError("record must be an InProgressHandoff instance")
        self._require_game_id(record.game_id)

        def mutate(records: MatchLocalRecords) -> MatchLocalRecords:
            attempted = _with_hash(records.attempted_handoff_hashes, record.sha256)
            return replace(
                records,
                in_progress_handoff=record,
                attempted_handoff_hashes=attempted,
            )

        self._store.update_match_state(self._game_id, mutate)

    def clear_in_progress(self, *, game_id: str) -> None:
        self._require_game_id(game_id)

        def mutate(records: MatchLocalRecords) -> MatchLocalRecords:
            if records.in_progress_handoff is None:
                return records
            return replace(records, in_progress_handoff=None)

        self._store.update_match_state(self._game_id, mutate)

    def record_handoff_attempt(self, *, game_id: str, sha256: str) -> None:
        self._require_game_id(game_id)
        digest = validate_sha256_hex(sha256, field_path="sha256")

        def mutate(records: MatchLocalRecords) -> MatchLocalRecords:
            if digest in records.attempted_handoff_hashes:
                return records
            return replace(
                records,
                attempted_handoff_hashes=_with_hash(
                    records.attempted_handoff_hashes, digest
                ),
            )

        self._store.update_match_state(self._game_id, mutate)

    def record_historical_acceptance(self, *, game_id: str, sha256: str) -> None:
        self._require_game_id(game_id)
        digest = validate_sha256_hex(sha256, field_path="sha256")

        def mutate(records: MatchLocalRecords) -> MatchLocalRecords:
            if digest in records.historically_accepted_hashes:
                return records
            return replace(
                records,
                historically_accepted_hashes=_with_hash(
                    records.historically_accepted_hashes, digest
                ),
            )

        self._store.update_match_state(self._game_id, mutate)

    def _require_game_id(self, game_id: str) -> str:
        validated = validate_game_id(game_id, field_path="game_id")
        if validated != self._game_id:
            raise DomainValidationError(
                "journal is scoped to a different game_id",
                field_path="game_id",
            )
        return validated


def _with_hash(existing: tuple[str, ...], digest: str) -> tuple[str, ...]:
    if digest in existing:
        return existing
    return (*existing, digest)
