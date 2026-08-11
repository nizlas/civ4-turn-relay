"""In-memory operation-journal port for prior-attempt and lock-resume evidence.

Durable disk persistence is deferred to P4. This module provides a small
protocol surface and a test/in-process implementation that handoff uses for
:class:`~civ4_turn_relay.protocol.hash_classify.PriorAttemptEvidence` and
own-lock resume (§7.1).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from civ4_turn_relay.domain import (
    validate_client_id,
    validate_game_id,
    validate_operation_id,
    validate_player_id,
    validate_sha256_hex,
)
from civ4_turn_relay.protocol.hash_classify import PriorAttemptEvidence


@dataclass(frozen=True, slots=True)
class InProgressHandoff:
    """Journal evidence required to resume an owned upload lock (§7.1)."""

    game_id: str
    operation_id: str
    client_id: str
    player_id: str
    sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "game_id", validate_game_id(self.game_id, field_path="game_id")
        )
        object.__setattr__(
            self,
            "operation_id",
            validate_operation_id(self.operation_id, field_path="operation_id"),
        )
        object.__setattr__(
            self,
            "client_id",
            validate_client_id(self.client_id, field_path="client_id"),
        )
        object.__setattr__(
            self,
            "player_id",
            validate_player_id(self.player_id, field_path="player_id"),
        )
        object.__setattr__(
            self, "sha256", validate_sha256_hex(self.sha256, field_path="sha256")
        )


@runtime_checkable
class OperationJournal(Protocol):
    """Minimal journal facts needed by hash classification."""

    def evidence_for_hash(self, *, game_id: str, sha256: str) -> PriorAttemptEvidence:
        """Return explicit prior-attempt facts for ``sha256`` in ``game_id``."""


@runtime_checkable
class HandoffJournal(OperationJournal, Protocol):
    """Journal facts required by lock resume and handoff commit."""

    def in_progress_handoff(self, *, game_id: str) -> InProgressHandoff | None:
        """Return the in-progress handoff for ``game_id``, if any."""

    def begin_handoff(self, record: InProgressHandoff) -> None:
        """Record an in-progress handoff and its attempt evidence."""

    def clear_in_progress(self, *, game_id: str) -> None:
        """Clear in-progress handoff state for ``game_id``."""

    def record_handoff_attempt(self, *, game_id: str, sha256: str) -> None:
        """Record that a handoff was attempted for ``sha256``."""

    def record_historical_acceptance(self, *, game_id: str, sha256: str) -> None:
        """Record that ``sha256`` was historically accepted for this client."""


@dataclass
class InMemoryOperationJournal:
    """Process-local journal suitable for tests and later in-process engines."""

    _attempts: set[tuple[str, str]] = field(default_factory=set)
    _historical_acceptances: set[tuple[str, str]] = field(default_factory=set)
    _in_progress: dict[str, InProgressHandoff] = field(default_factory=dict)

    def record_handoff_attempt(self, *, game_id: str, sha256: str) -> None:
        key = _hash_key(game_id, sha256)
        self._attempts.add(key)

    def record_historical_acceptance(self, *, game_id: str, sha256: str) -> None:
        key = _hash_key(game_id, sha256)
        self._historical_acceptances.add(key)

    def evidence_for_hash(self, *, game_id: str, sha256: str) -> PriorAttemptEvidence:
        key = _hash_key(game_id, sha256)
        return PriorAttemptEvidence(
            handoff_attempted_for_hash=key in self._attempts,
            historically_accepted_for_hash=key in self._historical_acceptances,
        )

    def in_progress_handoff(self, *, game_id: str) -> InProgressHandoff | None:
        validated = validate_game_id(game_id, field_path="game_id")
        return self._in_progress.get(validated)

    def begin_handoff(self, record: InProgressHandoff) -> None:
        if not isinstance(record, InProgressHandoff):
            raise TypeError("record must be an InProgressHandoff instance")
        self._in_progress[record.game_id] = record
        self.record_handoff_attempt(game_id=record.game_id, sha256=record.sha256)

    def clear_in_progress(self, *, game_id: str) -> None:
        validated = validate_game_id(game_id, field_path="game_id")
        self._in_progress.pop(validated, None)


def _hash_key(game_id: str, sha256: str) -> tuple[str, str]:
    return (
        validate_game_id(game_id, field_path="game_id"),
        validate_sha256_hex(sha256, field_path="sha256"),
    )
