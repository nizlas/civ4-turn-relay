"""Tests for the local operational state enum (design spec §6 names)."""

from civ4_turn_relay.domain import OperationalState

APPROVED_STATE_NAMES = [
    "RECONCILING",
    "WAITING_FOR_MY_FIRST_SAVE",
    "WAITING_FOR_OTHER_PLAYER",
    "DOWNLOADING",
    "MY_TURN_DOWNLOADED",
    "CIV_RUNNING",
    "OUTGOING_SAVE_DETECTED",
    "UPLOADING",
    "ERROR",
]


def test_enum_contains_exactly_the_approved_states() -> None:
    assert [state.name for state in OperationalState] == APPROVED_STATE_NAMES


def test_values_equal_names() -> None:
    for state in OperationalState:
        assert state.value == state.name


def test_lookup_by_value() -> None:
    assert OperationalState("MY_TURN_DOWNLOADED") is (
        OperationalState.MY_TURN_DOWNLOADED
    )
