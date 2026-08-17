"""Qt-free snapshot builders shared by the UI test modules."""

from __future__ import annotations

from civ4_turn_relay.app.process_runtime import ProcessStatus, ProcessStatusSnapshot
from civ4_turn_relay.app.snapshot import MatchClientSnapshot, PendingUserAction
from civ4_turn_relay.domain import OperationalState, TurnHandlingMode
from civ4_turn_relay.local.diagnostics import DiagnosticEvent
from civ4_turn_relay.local.intents import OrchestrationIntent

GAME_ID = "ui-match-01"
GAME_ID_B = "ui-match-02"


def client_snapshot(
    state: OperationalState,
    *,
    game_id: str = GAME_ID,
    display_name: str = "UI Match",
    pending: PendingUserAction = PendingUserAction.NONE,
    mode: TurnHandlingMode = TurnHandlingMode.STANDARD,
    current_player_id: str | None = "opponent",
    storage_available: bool = True,
    retry_required: bool = False,
    diagnostic_message: str | None = None,
    primary_status: str = "some_status",
    intents: tuple[OrchestrationIntent, ...] = (),
) -> MatchClientSnapshot:
    diagnostic: DiagnosticEvent | None = None
    if diagnostic_message is not None:
        diagnostic = DiagnosticEvent(
            name="event", fields={}, message=diagnostic_message
        )
    return MatchClientSnapshot(
        game_id=game_id,
        display_name=display_name,
        local_player_id="player_a",
        current_player_id=current_player_id,
        protocol_sequence=1,
        operational_state=state,
        turn_handling_mode=mode,
        primary_status=primary_status,
        pending_user_action=pending,
        intents=intents,
        latest_diagnostic=diagnostic,
        monitoring_available=True,
        storage_available=storage_available,
        retry_required=retry_required,
    )


def process_snapshot(
    status: ProcessStatus,
    *,
    message: str = "",
    launch_blocked_reason: str | None = None,
) -> ProcessStatusSnapshot:
    return ProcessStatusSnapshot(
        status=status,
        message=message,
        launch_blocked_reason=launch_blocked_reason,
    )
