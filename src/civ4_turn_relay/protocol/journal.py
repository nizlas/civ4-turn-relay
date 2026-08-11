"""In-memory operation-journal port for prior-attempt evidence (P3 foundation).

Durable disk persistence is deferred to P4. This module only provides a small
protocol surface and a test/in-process implementation that later slices can use
to supply :class:`~civ4_turn_relay.protocol.hash_classify.PriorAttemptEvidence`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from civ4_turn_relay.domain import validate_game_id, validate_sha256_hex
from civ4_turn_relay.protocol.hash_classify import PriorAttemptEvidence


@runtime_checkable
class OperationJournal(Protocol):
    """Minimal journal facts needed by hash classification."""

    def evidence_for_hash(self, *, game_id: str, sha256: str) -> PriorAttemptEvidence:
        """Return explicit prior-attempt facts for ``sha256`` in ``game_id``."""


@dataclass
class InMemoryOperationJournal:
    """Process-local journal suitable for tests and later in-process engines."""

    _attempts: set[tuple[str, str]] = field(default_factory=set)
    _historical_acceptances: set[tuple[str, str]] = field(default_factory=set)

    def record_handoff_attempt(self, *, game_id: str, sha256: str) -> None:
        key = _key(game_id, sha256)
        self._attempts.add(key)

    def record_historical_acceptance(self, *, game_id: str, sha256: str) -> None:
        key = _key(game_id, sha256)
        self._historical_acceptances.add(key)

    def evidence_for_hash(self, *, game_id: str, sha256: str) -> PriorAttemptEvidence:
        key = _key(game_id, sha256)
        return PriorAttemptEvidence(
            handoff_attempted_for_hash=key in self._attempts,
            historically_accepted_for_hash=key in self._historical_acceptances,
        )


def _key(game_id: str, sha256: str) -> tuple[str, str]:
    return (
        validate_game_id(game_id, field_path="game_id"),
        validate_sha256_hex(sha256, field_path="sha256"),
    )
