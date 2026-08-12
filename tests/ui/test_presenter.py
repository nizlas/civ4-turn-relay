"""Exhaustive pure-mapping tests for the UI presenter (no Qt required)."""

from __future__ import annotations

import pytest

from civ4_turn_relay.app.process_runtime import (
    ProcessStatus,
    ProcessStatusSnapshot,
)
from civ4_turn_relay.app.snapshot import PendingUserAction
from civ4_turn_relay.domain import OperationalState, TurnHandlingMode
from civ4_turn_relay.ui.presenter import (
    MatchViewModel,
    PrimaryActionKind,
    SecondaryActionKind,
    build_view_model,
)
from tests.ui.helpers import client_snapshot, process_snapshot

STANDARD = TurnHandlingMode.STANDARD
MANAGED = TurnHandlingMode.FULLY_MANAGED


def test_waiting_for_other_player_shows_current_player() -> None:
    vm = build_view_model(
        client_snapshot(
            OperationalState.WAITING_FOR_OTHER_PLAYER,
            current_player_id="Ljunget",
        ),
        None,
    )
    assert vm.status_text == "Waiting for Ljunget"
    assert vm.primary_action is PrimaryActionKind.NONE
    assert vm.primary_label == "Nothing needs to be done"
    assert vm.primary_enabled is False
    assert vm.secondary_actions == ()
    assert vm.attention is False


def test_waiting_for_other_player_without_manifest_player() -> None:
    vm = build_view_model(
        client_snapshot(
            OperationalState.WAITING_FOR_OTHER_PLAYER, current_player_id=None
        ),
        None,
    )
    assert vm.status_text == "Waiting for the other player"


def test_waiting_close_requested_detail() -> None:
    vm = build_view_model(
        client_snapshot(OperationalState.WAITING_FOR_OTHER_PLAYER),
        process_snapshot(ProcessStatus.CLOSE_REQUESTED),
    )
    assert vm.detail_text == "Turn safely sent — waiting for Civilization to close"
    assert vm.secondary_actions == ()


@pytest.mark.parametrize(
    "status",
    [ProcessStatus.CLOSE_DEADLINE_ELAPSED, ProcessStatus.FORCE_CLOSE_ELIGIBLE],
)
def test_waiting_close_not_done_offers_focus_and_close(
    status: ProcessStatus,
) -> None:
    vm = build_view_model(
        client_snapshot(OperationalState.WAITING_FOR_OTHER_PLAYER),
        process_snapshot(status),
    )
    assert vm.detail_text == "Turn safely sent, but Civilization did not close."
    assert vm.secondary_actions == (
        SecondaryActionKind.FOCUS_CIV,
        SecondaryActionKind.CLOSE_CIV,
    )
    assert vm.primary_action is PrimaryActionKind.NONE


def test_my_turn_standard_start_button() -> None:
    vm = build_view_model(
        client_snapshot(
            OperationalState.MY_TURN_DOWNLOADED,
            mode=STANDARD,
            pending=PendingUserAction.START_OR_RESUME,
        ),
        None,
    )
    assert vm.status_text == "Your turn — save downloaded"
    assert vm.primary_action is PrimaryActionKind.START_CIV
    assert vm.primary_label == "Start Civilization and play"
    assert vm.primary_enabled is True
    assert vm.attention is True


def test_my_turn_fully_managed_explicit_start_after_exit() -> None:
    vm = build_view_model(
        client_snapshot(
            OperationalState.MY_TURN_DOWNLOADED,
            mode=MANAGED,
            pending=PendingUserAction.START_OR_RESUME,
        ),
        None,
    )
    assert vm.primary_action is PrimaryActionKind.START_CIV
    assert vm.primary_label == "Start / Resume Civilization"


def test_my_turn_fully_managed_automatic_launch_disables_button() -> None:
    vm = build_view_model(
        client_snapshot(OperationalState.MY_TURN_DOWNLOADED, mode=MANAGED),
        None,
    )
    assert vm.primary_action is PrimaryActionKind.NONE
    assert vm.primary_label == "Starting automatically…"
    assert vm.primary_enabled is False


def test_my_turn_fully_managed_waits_for_existing_civ() -> None:
    vm = build_view_model(
        client_snapshot(OperationalState.MY_TURN_DOWNLOADED, mode=MANAGED),
        process_snapshot(ProcessStatus.WAITING_FOR_EXISTING_CIV),
    )
    assert vm.status_text == "Your turn — save downloaded"
    assert vm.detail_text == "Your turn is ready — waiting for Civilization to close."
    assert vm.primary_action is PrimaryActionKind.NONE
    assert vm.primary_label == "Waiting for Civilization to close…"
    assert vm.primary_enabled is False


def test_my_turn_standard_waiting_for_existing_civ_keeps_start_button() -> None:
    vm = build_view_model(
        client_snapshot(OperationalState.MY_TURN_DOWNLOADED, mode=STANDARD),
        process_snapshot(ProcessStatus.WAITING_FOR_EXISTING_CIV),
    )
    assert vm.detail_text == "Your turn is ready — waiting for Civilization to close."
    assert vm.primary_action is PrimaryActionKind.START_CIV
    assert vm.primary_label == "Start Civilization and play"
    assert vm.primary_enabled is True


def test_first_save_fully_managed_waits_for_existing_civ() -> None:
    vm = build_view_model(
        client_snapshot(OperationalState.WAITING_FOR_MY_FIRST_SAVE, mode=MANAGED),
        process_snapshot(ProcessStatus.WAITING_FOR_EXISTING_CIV),
    )
    assert vm.status_text == "Waiting for your first save (sequence 0)"
    assert vm.detail_text == "Your turn is ready — waiting for Civilization to close."
    assert vm.primary_enabled is False


def test_first_save_standard_create_button() -> None:
    vm = build_view_model(
        client_snapshot(
            OperationalState.WAITING_FOR_MY_FIRST_SAVE,
            mode=STANDARD,
            pending=PendingUserAction.START_OR_RESUME,
        ),
        None,
    )
    assert vm.status_text == "Waiting for your first save (sequence 0)"
    assert vm.primary_action is PrimaryActionKind.START_CIV
    assert vm.primary_label == "Start Civilization and create the game"


def test_first_save_fully_managed_pending_start_resume() -> None:
    vm = build_view_model(
        client_snapshot(
            OperationalState.WAITING_FOR_MY_FIRST_SAVE,
            mode=MANAGED,
            pending=PendingUserAction.START_OR_RESUME,
        ),
        None,
    )
    assert vm.primary_label == "Start / Resume Civilization"


def test_civ_running_with_verified_process_offers_focus() -> None:
    vm = build_view_model(
        client_snapshot(OperationalState.CIV_RUNNING),
        process_snapshot(ProcessStatus.RUNNING),
    )
    assert vm.status_text == "Civilization is running"
    assert vm.primary_action is PrimaryActionKind.FOCUS_CIV
    assert vm.primary_label == "Focus Civilization"
    assert vm.primary_enabled is True


@pytest.mark.parametrize("status", [None, ProcessStatus.READY])
def test_civ_running_without_verified_process_is_disabled(
    status: ProcessStatus | None,
) -> None:
    process = None if status is None else process_snapshot(status)
    vm = build_view_model(client_snapshot(OperationalState.CIV_RUNNING), process)
    assert vm.primary_action is PrimaryActionKind.NONE
    assert vm.primary_label == "Civilization is running"
    assert vm.primary_enabled is False


def test_outgoing_standard_confirm_send() -> None:
    vm = build_view_model(
        client_snapshot(
            OperationalState.OUTGOING_SAVE_DETECTED,
            mode=STANDARD,
            pending=PendingUserAction.CONFIRM_SEND,
        ),
        None,
    )
    assert vm.primary_action is PrimaryActionKind.SEND_SAVE
    assert vm.primary_label == "Send save"
    assert vm.attention is True


def test_outgoing_fully_managed_sends_automatically() -> None:
    vm = build_view_model(
        client_snapshot(OperationalState.OUTGOING_SAVE_DETECTED, mode=MANAGED),
        None,
    )
    assert vm.primary_action is PrimaryActionKind.NONE
    assert vm.primary_label == "Sending verified save…"


@pytest.mark.parametrize(
    ("state", "label"),
    [
        (OperationalState.UPLOADING, "Sending verified save…"),
        (OperationalState.DOWNLOADING, "Downloading save…"),
        (OperationalState.RECONCILING, "Checking game state…"),
    ],
)
def test_transient_states_disable_primary(state: OperationalState, label: str) -> None:
    vm = build_view_model(client_snapshot(state), None)
    assert vm.primary_action is PrimaryActionKind.NONE
    assert vm.primary_label == label
    assert vm.primary_enabled is False


def test_error_with_storage_down_is_connection_problem() -> None:
    vm = build_view_model(
        client_snapshot(
            OperationalState.ERROR,
            storage_available=False,
            retry_required=True,
            pending=PendingUserAction.RETRY,
        ),
        None,
    )
    assert vm.status_text == "Connection problem — retrying"
    assert vm.primary_action is PrimaryActionKind.RETRY
    assert vm.primary_label == "Retry now"
    assert vm.primary_enabled is True
    assert vm.attention is True


def test_error_with_storage_up_names_the_problem() -> None:
    vm = build_view_model(
        client_snapshot(
            OperationalState.ERROR,
            storage_available=True,
            diagnostic_message="manifest rejected the handoff",
            pending=PendingUserAction.RETRY,
        ),
        None,
    )
    assert vm.status_text == "Action needed: manifest rejected the handoff"
    assert vm.primary_action is PrimaryActionKind.RETRY


def test_retry_required_outside_error_state_still_maps_to_retry() -> None:
    vm = build_view_model(
        client_snapshot(OperationalState.WAITING_FOR_OTHER_PLAYER, retry_required=True),
        None,
    )
    assert vm.primary_action is PrimaryActionKind.RETRY
    assert vm.attention is True


@pytest.mark.parametrize(
    "state",
    [
        OperationalState.OUTGOING_SAVE_DETECTED,
        OperationalState.ERROR,
        OperationalState.WAITING_FOR_OTHER_PLAYER,
    ],
)
def test_select_candidate_overrides_primary_in_any_state(
    state: OperationalState,
) -> None:
    vm = build_view_model(
        client_snapshot(state, pending=PendingUserAction.SELECT_CANDIDATE),
        None,
    )
    assert vm.status_text == "Action needed: multiple new save files found"
    assert vm.primary_action is PrimaryActionKind.CHOOSE_SAVE
    assert vm.primary_label == "Choose save…"
    assert vm.primary_enabled is True
    assert vm.attention is True


def test_launch_failed_shows_reason_and_keeps_start_available() -> None:
    vm = build_view_model(
        client_snapshot(OperationalState.MY_TURN_DOWNLOADED, mode=MANAGED),
        process_snapshot(
            ProcessStatus.LAUNCH_FAILED,
            message="Civilization was not launched",
            launch_blocked_reason="the executable is not configured",
        ),
    )
    assert vm.attention is True
    assert vm.detail_text == "the executable is not configured"
    assert vm.primary_action is PrimaryActionKind.START_CIV
    assert vm.primary_label == "Start / Resume Civilization"
    assert vm.primary_enabled is True


def test_launch_failed_falls_back_to_message() -> None:
    vm = build_view_model(
        client_snapshot(OperationalState.WAITING_FOR_MY_FIRST_SAVE),
        process_snapshot(ProcessStatus.LAUNCH_FAILED, message="scripted spawn failure"),
    )
    assert vm.detail_text == "scripted spawn failure"
    assert vm.attention is True


def test_detail_defaults_to_latest_diagnostic_message() -> None:
    vm = build_view_model(
        client_snapshot(
            OperationalState.WAITING_FOR_OTHER_PLAYER,
            diagnostic_message="save uploaded",
        ),
        None,
    )
    assert vm.detail_text == "save uploaded"


@pytest.mark.parametrize(
    ("pending", "expected"),
    [
        (PendingUserAction.NONE, False),
        (PendingUserAction.WAIT, False),
        (PendingUserAction.START_OR_RESUME, True),
        (PendingUserAction.SELECT_CANDIDATE, True),
        (PendingUserAction.CONFIRM_SEND, True),
        (PendingUserAction.RETRY, True),
        (PendingUserAction.OTHER, True),
    ],
)
def test_attention_follows_pending_user_action(
    pending: PendingUserAction, expected: bool
) -> None:
    vm = build_view_model(
        client_snapshot(OperationalState.WAITING_FOR_OTHER_PLAYER, pending=pending),
        None,
    )
    assert vm.attention is expected


def test_totality_sweep_over_all_combinations() -> None:
    """Any state/pending/mode/process combination yields a valid view model."""
    processes: list[ProcessStatusSnapshot | None] = [None]
    processes.extend(process_snapshot(status) for status in ProcessStatus)
    for state in OperationalState:
        for pending in PendingUserAction:
            for mode in TurnHandlingMode:
                for process in processes:
                    vm = build_view_model(
                        client_snapshot(state, pending=pending, mode=mode),
                        process,
                    )
                    assert isinstance(vm, MatchViewModel)
                    assert vm.status_text
                    assert vm.primary_label
                    if vm.primary_enabled:
                        assert vm.primary_action is not PrimaryActionKind.NONE


def test_odd_combination_falls_back_to_snapshot_status() -> None:
    """An unusual pending/state pair still renders without invented state."""
    vm = build_view_model(
        client_snapshot(
            OperationalState.RECONCILING,
            pending=PendingUserAction.CONFIRM_SEND,
            primary_status="reconcile_in_progress",
        ),
        None,
    )
    assert vm.primary_action is PrimaryActionKind.NONE
    assert vm.status_text == "Checking game state…"
