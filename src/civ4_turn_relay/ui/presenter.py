"""Pure snapshot-to-view-model mapping for the minimal UI (no Qt imports).

All status wording and primary/secondary action selection lives here so it
is exhaustively testable without a Qt runtime. The mapping only reads
evidence carried by the immutable snapshots; it never invents state and
never labels anything "safe" or "sent" without snapshot evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, unique

from civ4_turn_relay.app.process_runtime import ProcessStatus, ProcessStatusSnapshot
from civ4_turn_relay.app.snapshot import MatchClientSnapshot, PendingUserAction
from civ4_turn_relay.domain import OperationalState, TurnHandlingMode

_DETAIL_MAX_CHARS = 120

_ATTENTION_PENDING = frozenset(
    {
        PendingUserAction.START_OR_RESUME,
        PendingUserAction.SELECT_CANDIDATE,
        PendingUserAction.CONFIRM_SEND,
        PendingUserAction.RETRY,
        PendingUserAction.OTHER,
    }
)

_CLOSE_NOT_DONE = frozenset(
    {
        ProcessStatus.CLOSE_DEADLINE_ELAPSED,
        ProcessStatus.FORCE_CLOSE_ELIGIBLE,
    }
)

_DEFERRED_LAUNCH = frozenset(
    {
        ProcessStatus.WAITING_FOR_EXISTING_CIV,
        ProcessStatus.WAITING_FOR_LAUNCH_GUARD,
        ProcessStatus.LAUNCH_SCAN_INDETERMINATE,
    }
)


@unique
class PrimaryActionKind(Enum):
    """What the single context-sensitive primary button does."""

    NONE = "none"
    START_CIV = "start_civ"
    SEND_SAVE = "send_save"
    CHOOSE_SAVE = "choose_save"
    RETRY = "retry"
    FOCUS_CIV = "focus_civ"


@unique
class SecondaryActionKind(Enum):
    """Optional secondary controls next to the primary button."""

    FOCUS_CIV = "focus_civ"
    CLOSE_CIV = "close_civ"


@dataclass(frozen=True, slots=True)
class MatchViewModel:
    """Immutable, render-ready description of one match row/panel."""

    game_id: str
    display_name: str
    status_text: str
    detail_text: str
    primary_action: PrimaryActionKind
    primary_label: str
    primary_enabled: bool
    secondary_actions: tuple[SecondaryActionKind, ...]
    attention: bool


def _shorten(text: str) -> str:
    if len(text) <= _DETAIL_MAX_CHARS:
        return text
    return text[: _DETAIL_MAX_CHARS - 1] + "…"


def _default_detail(snapshot: MatchClientSnapshot) -> str:
    diagnostic = snapshot.latest_diagnostic
    if diagnostic is not None and diagnostic.message:
        return _shorten(diagnostic.message)
    return ""


def _start_resume_pending(snapshot: MatchClientSnapshot) -> bool:
    return snapshot.pending_user_action is PendingUserAction.START_OR_RESUME


@dataclass(slots=True)
class _Draft:
    """Mutable working copy assembled by the state mapping."""

    status_text: str
    detail_text: str
    primary_action: PrimaryActionKind = PrimaryActionKind.NONE
    primary_label: str = ""
    primary_enabled: bool = False
    secondary_actions: tuple[SecondaryActionKind, ...] = ()
    attention: bool = False


def _disabled(draft: _Draft, label: str) -> _Draft:
    draft.primary_action = PrimaryActionKind.NONE
    draft.primary_label = label
    draft.primary_enabled = False
    return draft


def _action(draft: _Draft, kind: PrimaryActionKind, label: str) -> _Draft:
    draft.primary_action = kind
    draft.primary_label = label
    draft.primary_enabled = True
    return draft


def _map_waiting_for_other(
    draft: _Draft,
    snapshot: MatchClientSnapshot,
    process: ProcessStatusSnapshot | None,
) -> _Draft:
    who = snapshot.current_player_id or "the other player"
    draft.status_text = f"Waiting for {who}"
    _disabled(draft, "Nothing needs to be done")
    if process is not None:
        if process.status is ProcessStatus.CLOSE_REQUESTED:
            draft.detail_text = "Turn safely sent — waiting for Civilization to close"
        elif process.status in _CLOSE_NOT_DONE:
            draft.detail_text = "Turn safely sent, but Civilization did not close."
            draft.secondary_actions = (
                SecondaryActionKind.FOCUS_CIV,
                SecondaryActionKind.CLOSE_CIV,
            )
    return draft


def _map_my_turn_downloaded(
    draft: _Draft,
    snapshot: MatchClientSnapshot,
    process: ProcessStatusSnapshot | None,
) -> _Draft:
    draft.status_text = "Your turn — save downloaded"
    if process is not None and process.status in _DEFERRED_LAUNCH:
        return _map_deferred_launch(draft, snapshot, process)
    if snapshot.turn_handling_mode is TurnHandlingMode.STANDARD:
        return _action(
            draft, PrimaryActionKind.START_CIV, "Start Civilization and play"
        )
    if _start_resume_pending(snapshot):
        return _action(
            draft, PrimaryActionKind.START_CIV, "Start / Resume Civilization"
        )
    return _disabled(draft, "Starting automatically…")


def _map_deferred_launch(
    draft: _Draft,
    snapshot: MatchClientSnapshot,
    process: ProcessStatusSnapshot,
) -> _Draft:
    """A guarded launch was deferred; keep the turn ready without claiming a launch.

    Fully Managed retries automatically on later ticks, so the button stays
    disabled; Standard mode never auto-launches, so the user may explicitly
    retry Start. The three deferred statuses must stay distinct: an existing
    Civ, a busy sibling Relay, and an unverifiable scan are not the same.
    """
    if process.status is ProcessStatus.WAITING_FOR_EXISTING_CIV:
        draft.detail_text = "Your turn is ready — waiting for Civilization to close."
        waiting_label = "Waiting for Civilization to close…"
    elif process.status is ProcessStatus.WAITING_FOR_LAUNCH_GUARD:
        draft.detail_text = _shorten(
            process.message
            or (
                "Another Relay instance is currently checking or launching "
                "Civilization."
            )
        )
        waiting_label = "Waiting for another Relay instance…"
    else:
        draft.detail_text = _shorten(
            process.message
            or (
                "Relay cannot safely determine whether Civilization is already running."
            )
        )
        draft.attention = True
        waiting_label = "Cannot safely launch Civilization…"
    if snapshot.turn_handling_mode is TurnHandlingMode.STANDARD:
        return _action(
            draft, PrimaryActionKind.START_CIV, "Start Civilization and play"
        )
    return _disabled(draft, waiting_label)


def _map_first_save(draft: _Draft, snapshot: MatchClientSnapshot) -> _Draft:
    draft.status_text = "Waiting for your first save (sequence 0)"
    if (
        snapshot.turn_handling_mode is TurnHandlingMode.FULLY_MANAGED
        and _start_resume_pending(snapshot)
    ):
        return _action(
            draft, PrimaryActionKind.START_CIV, "Start / Resume Civilization"
        )
    return _action(
        draft,
        PrimaryActionKind.START_CIV,
        "Start Civilization and create the game",
    )


def _map_civ_running(draft: _Draft, process: ProcessStatusSnapshot | None) -> _Draft:
    draft.status_text = "Civilization is running"
    if process is not None and process.status is ProcessStatus.RUNNING:
        return _action(draft, PrimaryActionKind.FOCUS_CIV, "Focus Civilization")
    return _disabled(draft, "Civilization is running")


def _map_outgoing_detected(draft: _Draft, snapshot: MatchClientSnapshot) -> _Draft:
    if (
        snapshot.turn_handling_mode is TurnHandlingMode.STANDARD
        and snapshot.pending_user_action is PendingUserAction.CONFIRM_SEND
    ):
        draft.status_text = "New save detected — ready to send"
        return _action(draft, PrimaryActionKind.SEND_SAVE, "Send save")
    draft.status_text = "Sending verified save…"
    return _disabled(draft, "Sending verified save…")


def _map_error(draft: _Draft, snapshot: MatchClientSnapshot) -> _Draft:
    if not snapshot.storage_available:
        draft.status_text = "Connection problem — retrying"
    else:
        diagnostic = _default_detail(snapshot) or snapshot.primary_status
        draft.status_text = f"Action needed: {_shorten(diagnostic)}"
    draft.attention = True
    return _action(draft, PrimaryActionKind.RETRY, "Retry now")


def _map_state(
    draft: _Draft,
    snapshot: MatchClientSnapshot,
    process: ProcessStatusSnapshot | None,
) -> _Draft:
    state = snapshot.operational_state
    if state is OperationalState.WAITING_FOR_OTHER_PLAYER:
        return _map_waiting_for_other(draft, snapshot, process)
    if state is OperationalState.MY_TURN_DOWNLOADED:
        return _map_my_turn_downloaded(draft, snapshot, process)
    if state is OperationalState.WAITING_FOR_MY_FIRST_SAVE:
        if process is not None and process.status in _DEFERRED_LAUNCH:
            draft.status_text = "Waiting for your first save (sequence 0)"
            return _map_deferred_launch(draft, snapshot, process)
        return _map_first_save(draft, snapshot)
    if state is OperationalState.CIV_RUNNING:
        return _map_civ_running(draft, process)
    if state is OperationalState.OUTGOING_SAVE_DETECTED:
        return _map_outgoing_detected(draft, snapshot)
    if state is OperationalState.UPLOADING:
        draft.status_text = "Sending verified save…"
        return _disabled(draft, "Sending verified save…")
    if state is OperationalState.DOWNLOADING:
        draft.status_text = "Downloading save…"
        return _disabled(draft, "Downloading save…")
    if state is OperationalState.RECONCILING:
        draft.status_text = "Checking game state…"
        return _disabled(draft, "Checking game state…")
    # Total fallback: never invent state for unmapped combinations.
    draft.status_text = snapshot.primary_status
    return _disabled(draft, "Nothing needs to be done")


def build_view_model(
    snapshot: MatchClientSnapshot,
    process: ProcessStatusSnapshot | None,
) -> MatchViewModel:
    """Map one match snapshot (plus process status) to a view model.

    Total over every snapshot combination: unmapped states fall back to the
    snapshot's ``primary_status`` with a disabled primary button.
    """
    draft = _Draft(
        status_text=snapshot.primary_status,
        detail_text=_default_detail(snapshot),
        attention=snapshot.pending_user_action in _ATTENTION_PENDING,
    )

    if snapshot.pending_user_action is PendingUserAction.SELECT_CANDIDATE:
        draft.status_text = "Action needed: multiple new save files found"
        draft.attention = True
        _action(draft, PrimaryActionKind.CHOOSE_SAVE, "Choose save…")
    elif (
        snapshot.operational_state is OperationalState.ERROR or snapshot.retry_required
    ):
        _map_error(draft, snapshot)
    else:
        _map_state(draft, snapshot, process)
        if process is not None and process.status is ProcessStatus.LAUNCH_FAILED:
            draft.attention = True
            draft.detail_text = _shorten(
                process.launch_blocked_reason or process.message
            )
            _action(draft, PrimaryActionKind.START_CIV, "Start / Resume Civilization")

    return MatchViewModel(
        game_id=snapshot.game_id,
        display_name=snapshot.display_name,
        status_text=draft.status_text,
        detail_text=draft.detail_text,
        primary_action=draft.primary_action,
        primary_label=draft.primary_label,
        primary_enabled=draft.primary_enabled,
        secondary_actions=draft.secondary_actions,
        attention=draft.attention,
    )
