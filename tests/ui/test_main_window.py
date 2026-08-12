"""Main-window rendering and single-command dispatch tests."""

from __future__ import annotations

from pytestqt.qtbot import QtBot

from civ4_turn_relay.app.process_runtime import ProcessStatus
from civ4_turn_relay.app.snapshot import PendingUserAction
from civ4_turn_relay.domain import OperationalState
from civ4_turn_relay.ui.controller import MatchUiSnapshot
from civ4_turn_relay.ui.main_window import MainWindow
from tests.ui.helpers import GAME_ID, GAME_ID_B, client_snapshot, process_snapshot


class RecordingDispatcher:
    """Structural MatchCommandDispatcher fake recording every dispatch."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, ...]] = []

    def request_start(self, game_id: str) -> None:
        self.calls.append(("request_start", game_id))

    def send_save(self, game_id: str) -> None:
        self.calls.append(("send_save", game_id))

    def select_candidate(self, game_id: str, path: str) -> None:
        self.calls.append(("select_candidate", game_id, path))

    def retry(self, game_id: str) -> None:
        self.calls.append(("retry", game_id))

    def focus_civ(self, game_id: str) -> None:
        self.calls.append(("focus_civ", game_id))

    def close_civ(self, game_id: str) -> None:
        self.calls.append(("close_civ", game_id))


def _window(qtbot: QtBot) -> tuple[MainWindow, RecordingDispatcher]:
    dispatcher = RecordingDispatcher()
    window = MainWindow(dispatcher)
    qtbot.addWidget(window)
    return window, dispatcher


def _my_turn_payload(game_id: str = GAME_ID) -> MatchUiSnapshot:
    return MatchUiSnapshot(
        client=client_snapshot(
            OperationalState.MY_TURN_DOWNLOADED,
            game_id=game_id,
            pending=PendingUserAction.START_OR_RESUME,
        ),
        process=process_snapshot(ProcessStatus.READY),
    )


def _waiting_payload(game_id: str = GAME_ID) -> MatchUiSnapshot:
    return MatchUiSnapshot(
        client=client_snapshot(
            OperationalState.WAITING_FOR_OTHER_PLAYER,
            game_id=game_id,
            current_player_id="Ljunget",
        ),
        process=None,
    )


def test_renders_status_detail_and_primary(qtbot: QtBot) -> None:
    window, _dispatcher = _window(qtbot)
    window.on_snapshot(_my_turn_payload())
    assert window.name_label.text() == "UI Match"
    assert window.status_label.text() == "Your turn — save downloaded"
    assert window.primary_button.text() == "Start Civilization and play"
    assert window.primary_button.isEnabled()

    window.on_snapshot(_waiting_payload())
    assert window.status_label.text() == "Waiting for Ljunget"
    assert window.primary_button.text() == "Nothing needs to be done"
    assert not window.primary_button.isEnabled()


def test_primary_click_dispatches_exactly_one_command(qtbot: QtBot) -> None:
    window, dispatcher = _window(qtbot)
    window.on_snapshot(_my_turn_payload())
    window.primary_button.click()
    assert dispatcher.calls == [("request_start", GAME_ID)]

    # A repeated identical snapshot must not re-trigger anything by itself.
    window.on_snapshot(_my_turn_payload())
    assert dispatcher.calls == [("request_start", GAME_ID)]


def test_disabled_primary_never_dispatches(qtbot: QtBot) -> None:
    window, dispatcher = _window(qtbot)
    window.on_snapshot(_waiting_payload())
    window.primary_button.click()
    assert dispatcher.calls == []


def test_secondary_focus_close_only_when_present(qtbot: QtBot) -> None:
    window, dispatcher = _window(qtbot)
    window.on_snapshot(_waiting_payload())
    assert window.focus_button.isHidden()
    assert window.close_button.isHidden()

    stuck = MatchUiSnapshot(
        client=client_snapshot(OperationalState.WAITING_FOR_OTHER_PLAYER),
        process=process_snapshot(ProcessStatus.CLOSE_DEADLINE_ELAPSED),
    )
    window.on_snapshot(stuck)
    assert not window.focus_button.isHidden()
    assert not window.close_button.isHidden()
    assert (
        window.detail_label.text()
        == "Turn safely sent, but Civilization did not close."
    )

    window.focus_button.click()
    window.close_button.click()
    assert dispatcher.calls == [("focus_civ", GAME_ID), ("close_civ", GAME_ID)]


def test_match_list_switching_updates_main_area(qtbot: QtBot) -> None:
    window, _dispatcher = _window(qtbot)
    window.on_snapshot(_my_turn_payload(GAME_ID))
    window.on_snapshot(
        MatchUiSnapshot(
            client=client_snapshot(
                OperationalState.WAITING_FOR_OTHER_PLAYER,
                game_id=GAME_ID_B,
                display_name="Second Match",
                current_player_id="opponent",
            ),
            process=None,
        )
    )
    assert window.match_list.count() == 2
    assert window.current_game_id() == GAME_ID

    window.match_list.setCurrentRow(1)
    assert window.current_game_id() == GAME_ID_B
    assert window.name_label.text() == "Second Match"
    assert window.status_label.text() == "Waiting for opponent"

    window.match_list.setCurrentRow(0)
    assert window.status_label.text() == "Your turn — save downloaded"


def test_attention_marker_in_match_list(qtbot: QtBot) -> None:
    window, _dispatcher = _window(qtbot)
    window.on_snapshot(_my_turn_payload())
    item = window.match_list.item(0)
    assert item is not None
    assert item.text().endswith("\u25cf")

    window.on_snapshot(_waiting_payload())
    assert item.text() == "UI Match"


def test_diagnostics_appended_once_per_message(qtbot: QtBot) -> None:
    window, _dispatcher = _window(qtbot)
    payload = MatchUiSnapshot(
        client=client_snapshot(
            OperationalState.WAITING_FOR_OTHER_PLAYER,
            diagnostic_message="save uploaded",
        ),
        process=None,
    )
    window.on_snapshot(payload)
    window.on_snapshot(payload)
    text = window.details_view.toPlainText()
    assert text.count("save uploaded") == 1


def test_close_event_hides_to_tray_when_tray_available(qtbot: QtBot) -> None:
    window, _dispatcher = _window(qtbot)
    window.on_snapshot(_waiting_payload())
    window.set_tray_available(True)
    window.show()
    assert window.close() is False
    assert window.isHidden()


def test_close_event_hides_while_match_active_without_tray(qtbot: QtBot) -> None:
    window, _dispatcher = _window(qtbot)
    window.set_tray_available(False)
    window.on_snapshot(
        MatchUiSnapshot(
            client=client_snapshot(OperationalState.CIV_RUNNING),
            process=process_snapshot(ProcessStatus.RUNNING),
        )
    )
    assert window.has_active_match()
    window.show()
    assert window.close() is False
    assert window.isHidden()


def test_close_event_allows_close_when_idle_without_tray(qtbot: QtBot) -> None:
    window, _dispatcher = _window(qtbot)
    window.set_tray_available(False)
    window.on_snapshot(_waiting_payload())
    assert not window.has_active_match()
    window.show()
    assert window.close() is True


def test_allow_quit_bypasses_hide_to_tray(qtbot: QtBot) -> None:
    window, _dispatcher = _window(qtbot)
    window.set_tray_available(True)
    window.show()
    window.allow_quit()
    assert window.close() is True
