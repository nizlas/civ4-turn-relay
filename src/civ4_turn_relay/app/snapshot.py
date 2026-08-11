"""Immutable client snapshots for UI and diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, unique

from civ4_turn_relay.domain import (
    DomainValidationError,
    OperationalState,
    TurnHandlingMode,
)
from civ4_turn_relay.local.diagnostics import DiagnosticEvent
from civ4_turn_relay.local.intents import OrchestrationIntent


@unique
class PendingUserAction(Enum):
    """Explicit pending user actions (no localization yet)."""

    NONE = "none"
    START_OR_RESUME = "start_or_resume"
    SELECT_CANDIDATE = "select_candidate"
    CONFIRM_SEND = "confirm_send"
    RETRY = "retry"
    WAIT = "wait"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class MatchClientSnapshot:
    """Typed immutable view of one match suitable for a future UI."""

    game_id: str
    display_name: str
    local_player_id: str
    current_player_id: str | None
    protocol_sequence: int | None
    operational_state: OperationalState
    turn_handling_mode: TurnHandlingMode
    primary_status: str
    pending_user_action: PendingUserAction
    intents: tuple[OrchestrationIntent, ...]
    latest_diagnostic: DiagnosticEvent | None
    monitoring_available: bool
    storage_available: bool
    retry_required: bool

    def __post_init__(self) -> None:
        if not isinstance(self.game_id, str) or not self.game_id:
            raise DomainValidationError(
                "expected a non-empty game_id",
                field_path="game_id",
            )
        if not isinstance(self.display_name, str) or not self.display_name:
            raise DomainValidationError(
                "expected a non-empty display_name",
                field_path="display_name",
            )
        if not isinstance(self.operational_state, OperationalState):
            raise DomainValidationError(
                "expected an OperationalState",
                field_path="operational_state",
            )
        if not isinstance(self.pending_user_action, PendingUserAction):
            raise DomainValidationError(
                "expected a PendingUserAction",
                field_path="pending_user_action",
            )
        if not isinstance(self.primary_status, str) or not self.primary_status:
            raise DomainValidationError(
                "expected a non-empty primary_status",
                field_path="primary_status",
            )
