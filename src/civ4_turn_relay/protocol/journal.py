"""Operation-journal port for prior-attempt and lock-resume evidence.

:class:`InMemoryOperationJournal` is suitable for tests and in-process engines.
Durable disk persistence lives in :mod:`civ4_turn_relay.local.journal` (P4).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from civ4_turn_relay.domain import (
    DomainValidationError,
    validate_client_id,
    validate_game_id,
    validate_operation_id,
    validate_player_id,
    validate_sha256_hex,
)
from civ4_turn_relay.domain.construction import require_optional_string
from civ4_turn_relay.domain.serialization import (
    check_exact_keys,
    get_integer,
    get_optional_string,
    get_string,
)
from civ4_turn_relay.protocol.hash_classify import PriorAttemptEvidence

_IN_PROGRESS_KEYS = (
    "client_id",
    "game_id",
    "operation_id",
    "player_id",
    "sha256",
)


@dataclass(frozen=True, slots=True)
class InProgressHandoff:
    """Journal evidence required to resume an owned upload lock (§7.1)."""

    game_id: str
    operation_id: str
    client_id: str
    player_id: str
    sha256: str
    step_reached: str | None = None
    protocol_sequence: int | None = None

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
        step = require_optional_string(self.step_reached, field_path="step_reached")
        object.__setattr__(self, "step_reached", step)
        if step is not None and not step:
            raise DomainValidationError(
                "must be omitted instead of empty", field_path="step_reached"
            )
        if self.protocol_sequence is not None:
            if isinstance(self.protocol_sequence, bool) or not isinstance(
                self.protocol_sequence, int
            ):
                raise DomainValidationError(
                    "expected an integer (booleans are not integers)",
                    field_path="protocol_sequence",
                )
            if self.protocol_sequence < 0:
                raise DomainValidationError(
                    "expected a non-negative integer",
                    field_path="protocol_sequence",
                )

    def to_mapping(self) -> dict[str, object]:
        return {
            "client_id": self.client_id,
            "game_id": self.game_id,
            "operation_id": self.operation_id,
            "player_id": self.player_id,
            "protocol_sequence": self.protocol_sequence,
            "sha256": self.sha256,
            "step_reached": self.step_reached,
        }

    @classmethod
    def from_mapping(
        cls, mapping: Mapping[str, object], *, path: str = ""
    ) -> InProgressHandoff:
        check_exact_keys(
            mapping,
            _IN_PROGRESS_KEYS,
            optional=("protocol_sequence", "step_reached"),
            path=path,
        )
        game_id = get_string(mapping, "game_id", path=path)
        operation_id = get_string(mapping, "operation_id", path=path)
        client_id = get_string(mapping, "client_id", path=path)
        player_id = get_string(mapping, "player_id", path=path)
        sha256 = get_string(mapping, "sha256", path=path)
        step_reached = (
            get_optional_string(mapping, "step_reached", path=path)
            if "step_reached" in mapping
            else None
        )
        if "protocol_sequence" not in mapping:
            protocol_sequence: int | None = None
        else:
            raw_sequence = mapping["protocol_sequence"]
            if raw_sequence is None:
                protocol_sequence = None
            else:
                protocol_sequence = get_integer(mapping, "protocol_sequence", path=path)
        try:
            return cls(
                game_id=game_id,
                operation_id=operation_id,
                client_id=client_id,
                player_id=player_id,
                sha256=sha256,
                step_reached=step_reached,
                protocol_sequence=protocol_sequence,
            )
        except DomainValidationError as error:
            raise error.with_prefix(path) from None


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
