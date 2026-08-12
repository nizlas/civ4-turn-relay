"""Tray menu and quit-flow tests: quitting never terminates Civilization."""

from __future__ import annotations

import threading
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget
from pytestqt.qtbot import QtBot

from civ4_turn_relay.app import RelayClient
from civ4_turn_relay.app.process_runtime import ProcessStatus
from civ4_turn_relay.domain import MatchConfig, OperationalState
from civ4_turn_relay.local import FakeClock, LocalStore
from civ4_turn_relay.process import FakeProcessSupervisor
from civ4_turn_relay.protocol import InitializeOutcome
from civ4_turn_relay.storage import FakeStorage
from civ4_turn_relay.ui.app import GatedQApplication, RelayApplication
from civ4_turn_relay.ui.controller import MatchUiSnapshot, RelayWorkerHub
from civ4_turn_relay.ui.tray import RelayTray
from tests.e2e_fake.helpers import GAME_ID, NOW_UTC, match_config
from tests.ui.helpers import client_snapshot, process_snapshot

LOCAL_UUID = uuid.UUID("41414141-4141-4141-8141-414141414141")
OP_A = "0c0c0c0c-0c0c-4c0c-8c0c-0c0c0c0c0c0c"


def test_tray_menu_has_open_and_quit(qapp: QApplication) -> None:
    del qapp
    opened: list[int] = []
    quits: list[int] = []
    tray = RelayTray(on_open=lambda: opened.append(1), on_quit=lambda: quits.append(1))
    menu = tray.contextMenu()
    assert menu is not None
    actions = {action.text(): action for action in menu.actions()}
    assert set(actions) == {"Open Relay", "Quit"}

    actions["Open Relay"].trigger()
    assert opened == [1]
    actions["Quit"].trigger()
    assert quits == [1]


@dataclass
class _Env:
    app: RelayApplication
    supervisor: FakeProcessSupervisor
    quits: list[int] = field(default_factory=list)


@pytest.fixture
def relay_env(tmp_path: Path, qapp: QApplication) -> Iterator[_Env]:
    del qapp
    storage = FakeStorage()
    clock = FakeClock()
    supervisor = FakeProcessSupervisor()
    root = tmp_path / "local"
    store = LocalStore(root)
    store.get_or_create_installation_identity(uuid_factory=lambda: LOCAL_UUID)
    client = RelayClient(
        store=store,
        storage=storage,
        clock=clock,
        poll_interval_seconds=0.1,
        now_utc_fn=lambda: NOW_UTC,
        process_supervisor=supervisor,
        civ4_executable=None,
    )
    config = match_config(root, local_player_id="player_a", pbem_name="pbem-a")
    created = client.initialize_or_join(config, operation_id=OP_A)
    assert created.outcome is InitializeOutcome.CREATED

    quits: list[int] = []
    app = RelayApplication(
        client=client,
        global_config=None,
        enable_tray=False,
        quit_fn=lambda: quits.append(1),
    )
    environment = _Env(app=app, supervisor=supervisor, quits=quits)
    yield environment
    environment.app.shutdown()


def _feed_running_civ(env: _Env) -> None:
    env.app.window.on_snapshot(
        MatchUiSnapshot(
            client=client_snapshot(OperationalState.CIV_RUNNING, game_id=GAME_ID),
            process=process_snapshot(ProcessStatus.RUNNING),
        )
    )


def test_quit_with_active_civ_requires_confirmation(
    relay_env: _Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = relay_env
    _feed_running_civ(env)
    questions: list[str] = []

    def fake_question(
        parent: QWidget | None, title: str, text: str, *args: object
    ) -> QMessageBox.StandardButton:
        del parent, args
        questions.append(f"{title}: {text}")
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", staticmethod(fake_question))
    close_requests_before = list(env.supervisor.close_requests)

    env.app.request_quit()

    assert len(questions) == 1
    assert "NOT close Civilization" in questions[0]
    assert env.quits == [1]
    assert env.app.hub.worker_thread.isFinished()
    # Quitting never terminates Civ and never touches match state.
    assert env.supervisor.terminations == []
    assert env.supervisor.close_requests == close_requests_before


def test_quit_declined_keeps_relay_running(
    relay_env: _Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = relay_env
    _feed_running_civ(env)

    def fake_question(
        parent: QWidget | None, title: str, text: str, *args: object
    ) -> QMessageBox.StandardButton:
        del parent, title, text, args
        return QMessageBox.StandardButton.No

    monkeypatch.setattr(QMessageBox, "question", staticmethod(fake_question))
    env.app.request_quit()

    assert env.quits == []
    assert env.app.hub.worker_thread.isRunning()
    assert env.supervisor.terminations == []


def test_quit_when_idle_needs_no_confirmation(
    relay_env: _Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = relay_env

    def fail_question(*args: object, **kwargs: object) -> QMessageBox.StandardButton:
        raise AssertionError("no confirmation should be requested while idle")

    monkeypatch.setattr(QMessageBox, "question", staticmethod(fail_question))
    env.app.request_quit()

    assert env.quits == [1]
    assert env.app.hub.worker_thread.isFinished()
    assert env.app.hub.join_timed_out is False
    assert env.supervisor.terminations == []


def test_shutdown_is_idempotent_and_blocks_further_commands(
    relay_env: _Env, qtbot: QtBot
) -> None:
    env = relay_env
    env.app.hub.start_polling(50)
    env.app.shutdown()
    assert env.app.hub.worker_thread.isFinished()
    assert env.app.hub.join_timed_out is False
    env.app.shutdown()
    env.app.request_quit()
    # Already shut down: authorize exit again (idempotent gated quit).
    assert env.quits == [1]
    with qtbot.waitSignal(env.app.hub.error, timeout=2_000) as errored:
        env.app.hub.request_start(GAME_ID)
    assert errored.args is not None
    assert "shut down" in errored.args[0]
    assert env.supervisor.terminations == []


def test_tray_dispose_clears_menu(qapp: QApplication, qtbot: QtBot) -> None:
    del qapp
    tray = RelayTray(on_open=lambda: None, on_quit=lambda: None)
    assert tray.contextMenu() is not None
    tray.dispose()
    assert tray.contextMenu() is None
    tray.dispose()  # idempotent
    qtbot.wait(10)


def test_about_to_quit_without_orderly_shutdown_does_not_close_client(
    relay_env: _Env, qapp: GatedQApplication, qtbot: QtBot
) -> None:
    """aboutToQuit must not join/close while orderly shutdown never completed."""
    env = relay_env
    env.app.start()
    qtbot.wait(20)
    assert env.app.hub.worker_thread.isRunning()
    qapp.aboutToQuit.emit()
    assert env.app.hub.worker_thread.isRunning()
    assert env.app.hub.client_closed is False
    assert env.app.orderly_shutdown_complete is False
    assert env.supervisor.terminations == []


def test_last_window_close_does_not_bypass_quit_gate(
    relay_env: _Env,
    qapp: GatedQApplication,
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Last-window close uses the gated quit path (not implicit app quit)."""
    env = relay_env
    assert isinstance(qapp, GatedQApplication)
    assert QApplication.quitOnLastWindowClosed() is False

    def fail_question(*args: object, **kwargs: object) -> QMessageBox.StandardButton:
        raise AssertionError("idle close must not ask Civ confirmation")

    monkeypatch.setattr(QMessageBox, "question", staticmethod(fail_question))
    env.app.window.close()
    qtbot.waitUntil(lambda: env.quits == [1], timeout=2_000)
    assert qapp.quit_authorized is True
    assert env.app.orderly_shutdown_complete is True
    assert env.app.hub.client_closed is True


def test_menu_quit_routes_through_gated_request_quit(
    relay_env: _Env,
    qapp: GatedQApplication,
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env = relay_env
    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(
            lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("idle Quit needs no confirmation")
            )
        ),
    )
    from PySide6.QtWidgets import QMenu

    file_action = next(
        a
        for a in env.app.window.menuBar().actions()
        if a.text().replace("&", "") == "File"
    )
    file_menu = file_action.menu()
    assert isinstance(file_menu, QMenu)
    quit_action = next(a for a in file_menu.actions() if a.text() == "Quit")
    quit_action.trigger()
    qtbot.waitUntil(lambda: env.quits == [1], timeout=2_000)
    assert qapp.quit_authorized is True
    assert env.app.orderly_shutdown_complete is True


def test_ordinary_quit_event_timeout_defers_exit_then_retries(
    tmp_path: Path,
    qapp: GatedQApplication,
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert isinstance(qapp, GatedQApplication)
    storage = FakeStorage()
    clock = FakeClock()
    supervisor = FakeProcessSupervisor()
    root = tmp_path / "local"
    store = LocalStore(root)
    store.get_or_create_installation_identity(uuid_factory=lambda: LOCAL_UUID)
    client = RelayClient(
        store=store,
        storage=storage,
        clock=clock,
        poll_interval_seconds=0.1,
        now_utc_fn=lambda: NOW_UTC,
        process_supervisor=supervisor,
        civ4_executable=None,
    )
    config = match_config(root, local_player_id="player_a", pbem_name="pbem-a")
    created = client.initialize_or_join(config, operation_id=OP_A)
    assert created.outcome is InitializeOutcome.CREATED

    started = threading.Event()
    release = threading.Event()
    real_open_match = client.open_match

    def blocking_open(match_config_arg: MatchConfig) -> None:
        started.set()
        if not release.wait(timeout=30):
            raise TimeoutError("test release event was never set")
        real_open_match(match_config_arg)

    warnings: list[str] = []

    def fake_warning(
        parent: QWidget | None, title: str, text: str, *args: object
    ) -> QMessageBox.StandardButton:
        del parent, args
        warnings.append(f"{title}: {text}")
        return QMessageBox.StandardButton.Ok

    monkeypatch.setattr(QMessageBox, "warning", staticmethod(fake_warning))
    monkeypatch.setattr(
        QThread,
        "terminate",
        lambda self: (_ for _ in ()).throw(
            AssertionError("QThread.terminate must never be used")
        ),
    )

    hub = RelayWorkerHub(client, shutdown_wait_ms=150)
    monkeypatch.setattr(client, "open_match", blocking_open)
    authorized_quits: list[int] = []
    app = RelayApplication(
        client=client,
        global_config=None,
        enable_tray=False,
        hub=hub,
        quit_fn=lambda: authorized_quits.append(1),
    )
    try:
        app.start()
        app.hub.open_match(config)
        qtbot.waitUntil(started.is_set, timeout=5_000)

        assert qapp.quit_authorized is False
        qapp.quit()
        qtbot.waitUntil(lambda: len(warnings) == 1, timeout=2_000)

        assert qapp.quit_authorized is False
        assert authorized_quits == []
        assert "background operation" in warnings[0]
        assert app.hub.worker_thread.isRunning()
        assert app.hub.client_closed is False
        assert app.hub.join_timed_out is True
        assert client.snapshot(GAME_ID).game_id == GAME_ID
        assert supervisor.terminations == []

        release.set()
        qtbot.waitUntil(lambda: not app.hub.worker_thread.isRunning(), timeout=5_000)
        app.request_quit()
        assert authorized_quits == [1]
        assert qapp.quit_authorized is True
        assert app.hub.client_closed is True
        assert app.orderly_shutdown_complete is True
        assert supervisor.terminations == []
    finally:
        release.set()
        app.shutdown()


def test_successful_gated_quit_authorizes_application_exit(
    relay_env: _Env, qapp: GatedQApplication, monkeypatch: pytest.MonkeyPatch
) -> None:
    env = relay_env
    env.app.start()
    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(
            lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("idle quit needs no confirmation")
            )
        ),
    )
    assert qapp.quit_authorized is False
    env.app.request_quit()
    assert env.quits == [1]
    assert qapp.quit_authorized is True
    assert env.app.orderly_shutdown_complete is True
    assert env.app.hub.worker_thread.isFinished()
    env.app.finalize_after_exec()
    assert env.app.hub.client_closed is True


def test_finalize_after_exec_skips_when_shutdown_incomplete(
    relay_env: _Env,
) -> None:
    env = relay_env
    assert env.app.orderly_shutdown_complete is False
    env.app.finalize_after_exec()
    assert env.app.hub.client_closed is False
    assert env.app.hub.worker_thread.isRunning()


def test_request_quit_timeout_keeps_relay_open_then_retries(
    tmp_path: Path,
    qapp: QApplication,
    qtbot: QtBot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del qapp
    storage = FakeStorage()
    clock = FakeClock()
    supervisor = FakeProcessSupervisor()
    root = tmp_path / "local"
    store = LocalStore(root)
    store.get_or_create_installation_identity(uuid_factory=lambda: LOCAL_UUID)
    client = RelayClient(
        store=store,
        storage=storage,
        clock=clock,
        poll_interval_seconds=0.1,
        now_utc_fn=lambda: NOW_UTC,
        process_supervisor=supervisor,
        civ4_executable=None,
    )
    config = match_config(root, local_player_id="player_a", pbem_name="pbem-a")
    created = client.initialize_or_join(config, operation_id=OP_A)
    assert created.outcome is InitializeOutcome.CREATED

    started = threading.Event()
    release = threading.Event()
    real_open_match = client.open_match

    def blocking_open(match_config_arg: MatchConfig) -> None:
        started.set()
        if not release.wait(timeout=30):
            raise TimeoutError("test release event was never set")
        real_open_match(match_config_arg)

    quits: list[int] = []
    warnings: list[str] = []

    def fake_warning(
        parent: QWidget | None, title: str, text: str, *args: object
    ) -> QMessageBox.StandardButton:
        del parent, args
        warnings.append(f"{title}: {text}")
        return QMessageBox.StandardButton.Ok

    monkeypatch.setattr(QMessageBox, "warning", staticmethod(fake_warning))
    monkeypatch.setattr(
        QThread,
        "terminate",
        lambda self: (_ for _ in ()).throw(
            AssertionError("QThread.terminate must never be used")
        ),
    )

    hub = RelayWorkerHub(client, shutdown_wait_ms=150)
    monkeypatch.setattr(client, "open_match", blocking_open)
    app = RelayApplication(
        client=client,
        global_config=None,
        enable_tray=False,
        hub=hub,
        quit_fn=lambda: quits.append(1),
    )
    try:
        app.hub.open_match(config)
        qtbot.waitUntil(started.is_set, timeout=5_000)

        app.request_quit()
        assert quits == []
        assert len(warnings) == 1
        assert "background operation" in warnings[0]
        assert app.hub.worker_thread.isRunning()
        assert app.hub.client_closed is False
        assert client.snapshot(GAME_ID).game_id == GAME_ID
        assert supervisor.terminations == []

        release.set()
        qtbot.waitUntil(lambda: not app.hub.worker_thread.isRunning(), timeout=5_000)
        app.request_quit()
        assert quits == [1]
        assert app.hub.client_closed is True
        assert supervisor.terminations == []
    finally:
        release.set()
        app.shutdown()
