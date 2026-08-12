"""Worker-hub tests: threading, serialization, routing, shutdown."""

from __future__ import annotations

import threading
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from PySide6.QtCore import QThread
from PySide6.QtWidgets import QApplication
from pytestqt.qtbot import QtBot

from civ4_turn_relay.app import RelayClient
from civ4_turn_relay.domain import MatchConfig
from civ4_turn_relay.local import FakeClock, LocalStore
from civ4_turn_relay.process import FakeProcessSupervisor
from civ4_turn_relay.protocol import InitializeOutcome, InitializeResult
from civ4_turn_relay.storage import FakeStorage
from civ4_turn_relay.ui.controller import (
    MatchUiSnapshot,
    MatchWorker,
    RelayWorkerHub,
    WorkerShutdownOutcome,
)
from tests.e2e_fake.helpers import GAME_ID, GAME_ID_B, NOW_UTC, match_config

LOCAL_UUID = uuid.UUID("31313131-3131-4131-8131-313131313131")
OP_A = "0a0a0a0a-0a0a-4a0a-8a0a-0a0a0a0a0a0a"
OP_B = "0b0b0b0b-0b0b-4b0b-8b0b-0b0b0b0b0b0b"
TIMEOUT_MS = 5000


@dataclass
class _Env:
    client: RelayClient
    hub: RelayWorkerHub
    supervisor: FakeProcessSupervisor
    configs: dict[str, MatchConfig] = field(default_factory=dict)


@pytest.fixture
def env(tmp_path: Path, qapp: QApplication) -> Iterator[_Env]:
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
    configs = {
        GAME_ID: match_config(root, local_player_id="player_a", pbem_name="pbem-a"),
        GAME_ID_B: match_config(
            root, game_id=GAME_ID_B, local_player_id="player_a", pbem_name="pbem-b"
        ),
    }
    created = client.initialize_or_join(configs[GAME_ID], operation_id=OP_A)
    assert created.outcome is InitializeOutcome.CREATED
    created_b = client.initialize_or_join(configs[GAME_ID_B], operation_id=OP_B)
    assert created_b.outcome is InitializeOutcome.CREATED
    hub = RelayWorkerHub(client)
    environment = _Env(client=client, hub=hub, supervisor=supervisor, configs=configs)
    yield environment
    environment.hub.shutdown()


def _open(env: _Env, qtbot: QtBot, game_id: str) -> None:
    with qtbot.waitSignal(env.hub.snapshot_ready, timeout=TIMEOUT_MS):
        env.hub.open_match(env.configs[game_id])


def test_open_match_and_poll_emit_snapshots(env: _Env, qtbot: QtBot) -> None:
    with qtbot.waitSignal(env.hub.snapshot_ready, timeout=TIMEOUT_MS) as opened:
        env.hub.open_match(env.configs[GAME_ID])
    assert opened.args is not None
    payload = opened.args[0]
    assert isinstance(payload, MatchUiSnapshot)
    assert payload.game_id == GAME_ID
    assert payload.process is not None

    with qtbot.waitSignal(env.hub.snapshot_ready, timeout=TIMEOUT_MS) as polled:
        env.hub.poll_now()
    assert polled.args is not None
    polled_payload = polled.args[0]
    assert isinstance(polled_payload, MatchUiSnapshot)
    assert polled_payload.game_id == GAME_ID


def test_polling_timer_emits_snapshots(env: _Env, qtbot: QtBot) -> None:
    _open(env, qtbot, GAME_ID)
    with qtbot.waitSignal(env.hub.snapshot_ready, timeout=TIMEOUT_MS):
        env.hub.start_polling(10)


def test_commands_run_on_worker_thread(
    env: _Env, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    _open(env, qtbot, GAME_ID)
    seen_threads: list[QThread] = []

    def fake_request_start(game_id: str, **_kwargs: object) -> None:
        del game_id
        seen_threads.append(QThread.currentThread())

    monkeypatch.setattr(env.client, "request_start", fake_request_start)
    with qtbot.waitSignal(env.hub.snapshot_ready, timeout=TIMEOUT_MS):
        env.hub.request_start(GAME_ID)
    assert seen_threads == [env.hub.worker_thread]
    assert QThread.currentThread() is not env.hub.worker_thread


def test_busy_flag_suppresses_duplicate_dispatch(
    env: _Env, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    _open(env, qtbot, GAME_ID)
    calls: list[str] = []
    monkeypatch.setattr(
        env.client,
        "request_start",
        lambda game_id, **_kwargs: calls.append(game_id),
    )
    errors: list[str] = []
    env.hub.error.connect(errors.append)

    with qtbot.waitSignal(env.hub.snapshot_ready, timeout=TIMEOUT_MS):
        env.hub.request_start(GAME_ID)
        env.hub.request_start(GAME_ID)  # duplicate while the first is in flight
    assert any("ignored" in message for message in errors)

    # Flush the worker queue; a wrongly queued duplicate would have run first.
    with qtbot.waitSignal(env.hub.snapshot_ready, timeout=TIMEOUT_MS):
        env.hub.poll_now()
    assert calls == [GAME_ID]


def test_two_matches_stay_isolated(
    env: _Env, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    received: list[MatchUiSnapshot] = []
    env.hub.snapshot_ready.connect(received.append)
    _open(env, qtbot, GAME_ID)
    _open(env, qtbot, GAME_ID_B)

    received.clear()
    env.hub.poll_now()
    qtbot.waitUntil(
        lambda: {payload.game_id for payload in received} >= {GAME_ID, GAME_ID_B},
        timeout=TIMEOUT_MS,
    )
    for payload in received:
        assert payload.client.game_id == payload.game_id

    focused: list[str] = []
    monkeypatch.setattr(
        env.client, "focus_civ", lambda game_id: focused.append(game_id)
    )
    with qtbot.waitSignal(env.hub.snapshot_ready, timeout=TIMEOUT_MS):
        env.hub.focus_civ(GAME_ID_B)
    assert focused == [GAME_ID_B]


def test_command_errors_are_reported_not_raised(
    env: _Env, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    _open(env, qtbot, GAME_ID)

    def boom(game_id: str, **_kwargs: object) -> None:
        raise RuntimeError(f"scripted failure for {game_id}")

    monkeypatch.setattr(env.client, "request_start", boom)
    with qtbot.waitSignal(env.hub.error, timeout=TIMEOUT_MS) as errored:
        env.hub.request_start(GAME_ID)
    assert errored.args is not None
    message = errored.args[0]
    assert GAME_ID in message
    # Unknown exception types surface as their class name only (secret-free).
    assert "scripted failure" not in message


def test_failed_initialize_does_not_open_or_snapshot_missing_local_config(
    env: _Env,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = match_config(
        tmp_path,
        game_id="failed-initialize",
        local_player_id="player_a",
        pbem_name="failed-pbem",
    )
    monkeypatch.setattr(
        env.client,
        "initialize_or_join",
        lambda _config: InitializeResult(InitializeOutcome.CAPABILITY_FAILURE),
    )
    worker = MatchWorker(env.client)
    errors: list[str] = []
    snapshots: list[MatchUiSnapshot] = []
    worker.error.connect(errors.append)
    worker.snapshot_ready.connect(snapshots.append)

    worker.initialize_match(config)

    assert worker.open_game_ids == ()
    assert snapshots == []
    assert errors == [
        "failed-initialize: remote match initialization did not complete "
        "(capability_failure)"
    ]
    assert "LocalStoreMissingError" not in errors[0]


def test_shutdown_stops_thread_and_is_idempotent(env: _Env, qtbot: QtBot) -> None:
    _open(env, qtbot, GAME_ID)
    env.hub.start_polling(25)
    first = env.hub.shutdown()
    assert first.outcome is WorkerShutdownOutcome.SUCCEEDED
    assert env.hub.worker_thread.isFinished()
    assert env.hub.join_timed_out is False
    assert env.hub.client_closed is True
    second = env.hub.shutdown()  # idempotent
    assert second.outcome is WorkerShutdownOutcome.ALREADY_SHUT_DOWN
    with pytest.raises(RuntimeError):
        env.client.snapshot(GAME_ID)
    with qtbot.waitSignal(env.hub.error, timeout=TIMEOUT_MS) as errored:
        env.hub.request_start(GAME_ID)
    assert errored.args is not None
    assert "shut down" in errored.args[0]


def test_successful_shutdown_uses_finished_deleteLater_not_gui_delete(
    env: _Env, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Worker destruction is owned by QThread.finished, not a post-join GUI call."""
    _open(env, qtbot, GAME_ID)
    terminate_calls: list[str] = []

    def forbid_terminate(self: QThread) -> None:
        terminate_calls.append(self.objectName() or "thread")
        raise AssertionError("QThread.terminate must never be used")

    monkeypatch.setattr(QThread, "terminate", forbid_terminate)

    result = env.hub.shutdown()
    assert result.outcome is WorkerShutdownOutcome.SUCCEEDED
    assert env.hub.worker_thread.isFinished()
    assert env.hub.worker_thread.isRunning() is False
    assert env.hub.client_closed is True
    assert terminate_calls == []
    # finished→deleteLater eventually invalidates the worker QObject.
    qtbot.waitUntil(
        lambda: _worker_deleted(env.hub),
        timeout=TIMEOUT_MS,
    )


def _worker_deleted(hub: RelayWorkerHub) -> bool:
    try:
        hub._worker.objectName()
    except RuntimeError:
        return True
    return False


def test_shutdown_join_timeout_keeps_client_open_and_allows_retry(
    tmp_path: Path, qapp: QApplication, qtbot: QtBot, monkeypatch: pytest.MonkeyPatch
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

    def blocking_request_start(game_id: str, **_kwargs: object) -> None:
        del game_id
        started.set()
        if not release.wait(timeout=30):
            raise TimeoutError("test release event was never set")

    monkeypatch.setattr(client, "request_start", blocking_request_start)

    terminate_calls: list[str] = []

    def forbid_terminate(self: QThread) -> None:
        terminate_calls.append(self.objectName() or "thread")
        raise AssertionError("QThread.terminate must never be used")

    monkeypatch.setattr(QThread, "terminate", forbid_terminate)

    hub = RelayWorkerHub(client, shutdown_wait_ms=150)
    try:
        with qtbot.waitSignal(hub.snapshot_ready, timeout=TIMEOUT_MS):
            hub.open_match(config)
        hub.request_start(GAME_ID)
        qtbot.waitUntil(started.is_set, timeout=TIMEOUT_MS)

        timed_out = hub.shutdown()
        assert timed_out.outcome is WorkerShutdownOutcome.JOIN_TIMED_OUT
        assert timed_out.join_timed_out is True
        assert hub.join_timed_out is True
        assert hub.worker_thread.isRunning()
        assert hub.client_closed is False
        assert terminate_calls == []
        # Premature close would make snapshot raise; it must still work.
        assert client.snapshot(GAME_ID).game_id == GAME_ID

        release.set()
        qtbot.waitUntil(lambda: not hub.worker_thread.isRunning(), timeout=TIMEOUT_MS)

        retried = hub.shutdown()
        assert retried.outcome is WorkerShutdownOutcome.SUCCEEDED
        assert hub.client_closed is True
        assert hub.join_timed_out is False
        assert hub.worker_thread.isFinished()
        assert terminate_calls == []
        with pytest.raises(RuntimeError):
            client.snapshot(GAME_ID)
    finally:
        release.set()
        hub.shutdown()
