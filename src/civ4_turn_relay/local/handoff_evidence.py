"""Strict completed-handoff attribution derived only from P3 results."""

from __future__ import annotations

from dataclasses import dataclass

from civ4_turn_relay.domain import (
    DomainValidationError,
    Manifest,
    sha256_hex,
    validate_game_id,
    validate_operation_id,
    validate_player_id,
    validate_sha256_hex,
)
from civ4_turn_relay.protocol.handoff import (
    HandoffOutcome,
    HandoffRequest,
    HandoffResult,
)


@dataclass(frozen=True, slots=True)
class HandoffEvidence:
    """Authoritative proof that one exact handoff operation completed.

    Construct only via :func:`attribute_handoff_result` or
    :func:`attribute_journal_against_manifest`. Arbitrary outcome-name strings
    are rejected.
    """

    outcome: HandoffOutcome
    game_id: str
    operation_id: str
    local_player_id: str
    sha256: str
    size_bytes: int
    source_protocol_sequence: int
    result_protocol_sequence: int

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, HandoffOutcome):
            raise DomainValidationError(
                "expected a HandoffOutcome",
                field_path="outcome",
            )
        if self.outcome not in {
            HandoffOutcome.COMMITTED,
            HandoffOutcome.IDEMPOTENT_ACK,
        }:
            raise DomainValidationError(
                "only committed or idempotent_ack evidence is accepted",
                field_path="outcome",
            )
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
            "local_player_id",
            validate_player_id(self.local_player_id, field_path="local_player_id"),
        )
        object.__setattr__(
            self, "sha256", validate_sha256_hex(self.sha256, field_path="sha256")
        )
        if isinstance(self.size_bytes, bool) or not isinstance(self.size_bytes, int):
            raise DomainValidationError(
                "expected an integer size_bytes",
                field_path="size_bytes",
            )
        if self.size_bytes <= 0:
            raise DomainValidationError(
                "size_bytes must be positive",
                field_path="size_bytes",
            )
        for field_name, value in (
            ("source_protocol_sequence", self.source_protocol_sequence),
            ("result_protocol_sequence", self.result_protocol_sequence),
        ):
            if isinstance(value, bool) or not isinstance(value, int):
                raise DomainValidationError(
                    "expected an integer sequence",
                    field_path=field_name,
                )
            if value < 0:
                raise DomainValidationError(
                    "sequence must be non-negative",
                    field_path=field_name,
                )
        if self.result_protocol_sequence != self.source_protocol_sequence + 1:
            raise DomainValidationError(
                "result sequence must be source sequence + 1",
                field_path="result_protocol_sequence",
            )

    @property
    def outcome_name(self) -> str:
        """Compatibility alias for diagnostics only."""
        return self.outcome.value


def _manifest_proves_exact_handoff(
    manifest: Manifest,
    *,
    game_id: str,
    operation_id: str,
    local_player_id: str,
    sha256: str,
    size_bytes: int,
    source_protocol_sequence: int,
) -> bool:
    if manifest.game_id != game_id:
        return False
    if manifest.accepted_save is None:
        return False
    if manifest.accepted_save.sha256 != sha256:
        return False
    if manifest.accepted_save.size_bytes != size_bytes:
        return False
    if manifest.protocol_sequence != source_protocol_sequence + 1:
        return False
    if manifest.last_sender_id != local_player_id:
        return False
    if manifest.protocol.last_operation_id != operation_id:
        return False
    if sha256 not in manifest.accepted_save_hashes:
        return False
    return True


def attribute_handoff_result(
    *,
    request: HandoffRequest,
    result: HandoffResult,
    source_protocol_sequence: int,
) -> HandoffEvidence | None:
    """Return typed evidence only when the P3 result proves this exact request."""
    if not isinstance(request, HandoffRequest):
        raise TypeError("request must be a HandoffRequest")
    if not isinstance(result, HandoffResult):
        raise TypeError("result must be a HandoffResult")
    if result.outcome not in {
        HandoffOutcome.COMMITTED,
        HandoffOutcome.IDEMPOTENT_ACK,
    }:
        return None
    if result.manifest is None:
        return None
    digest = sha256_hex(request.outgoing_bytes)
    if result.sha256 != digest:
        return None
    if not _manifest_proves_exact_handoff(
        result.manifest,
        game_id=request.game_id,
        operation_id=request.operation_id,
        local_player_id=request.local_player_id,
        sha256=digest,
        size_bytes=len(request.outgoing_bytes),
        source_protocol_sequence=source_protocol_sequence,
    ):
        return None
    return HandoffEvidence(
        outcome=result.outcome,
        game_id=request.game_id,
        operation_id=request.operation_id,
        local_player_id=request.local_player_id,
        sha256=digest,
        size_bytes=len(request.outgoing_bytes),
        source_protocol_sequence=source_protocol_sequence,
        result_protocol_sequence=result.manifest.protocol_sequence,
    )


def attribute_journal_against_manifest(
    *,
    manifest: Manifest,
    game_id: str,
    operation_id: str,
    client_id: str,
    local_player_id: str,
    sha256: str,
    size_bytes: int | None,
    source_protocol_sequence: int | None,
    journal_client_id: str,
    journal_player_id: str,
    journal_game_id: str,
) -> HandoffEvidence | None:
    """Attribute an in-progress journal to a committed authoritative manifest."""
    if journal_game_id != game_id:
        return None
    if journal_player_id != local_player_id:
        return None
    if journal_client_id != client_id:
        return None
    if source_protocol_sequence is None:
        if (
            manifest.protocol.last_operation_id == operation_id
            and manifest.protocol_sequence >= 1
            and manifest.accepted_save is not None
            and manifest.accepted_save.sha256 == sha256
        ):
            source_protocol_sequence = manifest.protocol_sequence - 1
        else:
            return None
    if size_bytes is None or size_bytes <= 0:
        # Size may be recovered from the accepted save when journal lacks it.
        if manifest.accepted_save is None:
            return None
        size_bytes = manifest.accepted_save.size_bytes
    if not _manifest_proves_exact_handoff(
        manifest,
        game_id=game_id,
        operation_id=operation_id,
        local_player_id=local_player_id,
        sha256=sha256,
        size_bytes=size_bytes,
        source_protocol_sequence=source_protocol_sequence,
    ):
        return None
    return HandoffEvidence(
        outcome=HandoffOutcome.IDEMPOTENT_ACK,
        game_id=game_id,
        operation_id=operation_id,
        local_player_id=local_player_id,
        sha256=sha256,
        size_bytes=size_bytes,
        source_protocol_sequence=source_protocol_sequence,
        result_protocol_sequence=manifest.protocol_sequence,
    )
