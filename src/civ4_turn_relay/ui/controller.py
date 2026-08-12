"""Qt worker layer: every RelayClient call runs on one worker thread.

``RelayClient`` shares one storage connection across matches and is not
thread-safe, so a single :class:`MatchWorker` living on a single ``QThread``
serializes *all* matches: per-match serialization holds trivially and
cross-match SFTP races are impossible. The GUI thread only ever dispatches
queued commands through :class:`RelayWorkerHub` and receives immutable
:class:`MatchUiSnapshot` payloads back via queued signals.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import (
    QDeadlineTimer,
    QObject,
    Qt,
    QThread,
    QTimer,
    Signal,
    Slot,
)

from civ4_turn_relay.app.process_runtime import ProcessStatusSnapshot
from civ4_turn_relay.app.relay_client import RelayClient
from civ4_turn_relay.app.snapshot import MatchClientSnapshot
from civ4_turn_relay.domain import DomainValidationError, MatchConfig
from civ4_turn_relay.local.errors import LocalStoreError
from civ4_turn_relay.storage import StorageError

_SHUTDOWN_WAIT_MS = 10_000


@dataclass(frozen=True, slots=True)
class MatchUiSnapshot:
    """Immutable per-match payload emitted to the GUI thread."""

    client: MatchClientSnapshot
    process: ProcessStatusSnapshot | None

    @property
    def game_id(self) -> str:
        return self.client.game_id


def _safe_error_text(game_id: str, error: BaseException) -> str:
    """Secret-free error line: full text only for known-redacted types."""
    if isinstance(error, DomainValidationError | StorageError | LocalStoreError):
        return f"{game_id}: {type(error).__name__}: {error}"
    return f"{game_id}: {type(error).__name__}"


class MatchWorker(QObject):
    """Executes all RelayClient work on its owning (worker) thread.

    One instance serves every open match; slots take the target ``game_id``.
    A per-match busy set guards against re-entrancy inside the worker; the
    hub additionally suppresses duplicate dispatch on the GUI side.
    """

    snapshot_ready = Signal(object)
    error = Signal(str)
    command_finished = Signal(str)

    def __init__(self, client: RelayClient) -> None:
        super().__init__()
        self._client = client
        self._open_ids: list[str] = []
        self._busy: set[str] = set()
        self._timer: QTimer | None = None

    @property
    def open_game_ids(self) -> tuple[str, ...]:
        return tuple(self._open_ids)

    @Slot(int)
    def start_polling(self, interval_ms: int) -> None:
        """Create (in-thread) and start the poll timer."""
        if self._timer is None:
            self._timer = QTimer(self)
            self._timer.timeout.connect(self.poll)
        self._timer.start(max(1, interval_ms))

    @Slot()
    def stop_polling(self) -> None:
        if self._timer is not None:
            self._timer.stop()

    @Slot()
    def poll(self) -> None:
        """Tick every open match and emit a fresh snapshot for each."""
        for game_id in list(self._open_ids):
            if game_id in self._busy:
                continue
            try:
                client_snapshot = self._client.tick(game_id)
                process = self._client.process_status(game_id)
            except Exception as exc:
                self.error.emit(_safe_error_text(game_id, exc))
                continue
            self.snapshot_ready.emit(
                MatchUiSnapshot(client=client_snapshot, process=process)
            )

    @Slot(object)
    def open_match(self, config: object) -> None:
        """Register an already-configured match (local only, no remote writes)."""
        if not isinstance(config, MatchConfig):
            self.error.emit("open_match: expected a MatchConfig payload")
            return
        self._run_command(config.game_id, lambda: self._client.open_match(config))

    @Slot(object)
    def initialize_match(self, config: object) -> None:
        """Initialize or join the remote match for a new local config."""
        if not isinstance(config, MatchConfig):
            self.error.emit("initialize_match: expected a MatchConfig payload")
            return
        self._run_command(
            config.game_id, lambda: self._client.initialize_or_join(config)
        )

    @Slot(str)
    def request_start(self, game_id: str) -> None:
        self._run_command(game_id, lambda: self._client.request_start(game_id))

    @Slot(str)
    def send_save(self, game_id: str) -> None:
        self._run_command(game_id, lambda: self._client.execute_handoff(game_id))

    @Slot(str, str)
    def select_candidate(self, game_id: str, path: str) -> None:
        self._run_command(game_id, lambda: self._client.select_candidate(game_id, path))

    @Slot(str)
    def retry(self, game_id: str) -> None:
        self._run_command(game_id, lambda: self._client.tick(game_id))

    @Slot(str)
    def focus_civ(self, game_id: str) -> None:
        self._run_command(game_id, lambda: self._client.focus_civ(game_id))

    @Slot(str)
    def close_civ(self, game_id: str) -> None:
        self._run_command(game_id, lambda: self._client.request_civ_close(game_id))

    def _run_command(self, game_id: str, command: Callable[[], object]) -> None:
        if game_id in self._busy:
            self.error.emit(
                f"{game_id}: a command is already running; the duplicate "
                "request was ignored"
            )
            return
        self._busy.add(game_id)
        try:
            command()
            if game_id not in self._open_ids:
                self._open_ids.append(game_id)
        except Exception as exc:
            self.error.emit(_safe_error_text(game_id, exc))
        finally:
            self._busy.discard(game_id)
            # Finish first so the hub-side busy flag is cleared before any
            # observer reacts to the fresh snapshot (queued in-order).
            self.command_finished.emit(game_id)
            self._emit_snapshot(game_id)

    def _emit_snapshot(self, game_id: str) -> None:
        try:
            client_snapshot = self._client.snapshot(game_id)
            process = self._client.process_status(game_id)
        except Exception as exc:
            self.error.emit(_safe_error_text(game_id, exc))
            return
        self.snapshot_ready.emit(
            MatchUiSnapshot(client=client_snapshot, process=process)
        )


class RelayWorkerHub(QObject):
    """GUI-side facade: one QThread + one worker serializing every match.

    All public methods must be called from the GUI thread. Commands are
    forwarded through queued signal connections; a per-match busy flag on
    this side suppresses duplicate dispatch while a command is in flight
    (the suppressed duplicate is reported through :attr:`error`).
    """

    snapshot_ready = Signal(object)
    error = Signal(str)

    _open_match_requested = Signal(object)
    _initialize_requested = Signal(object)
    _start_polling_requested = Signal(int)
    _stop_polling_requested = Signal()
    _poll_requested = Signal()
    _request_start_requested = Signal(str)
    _send_save_requested = Signal(str)
    _select_candidate_requested = Signal(str, str)
    _retry_requested = Signal(str)
    _focus_requested = Signal(str)
    _close_civ_requested = Signal(str)

    def __init__(self, client: RelayClient, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._client = client
        self._busy: set[str] = set()
        self._shut_down = False
        self._thread = QThread()
        self._thread.setObjectName("relay-worker")
        self._worker = MatchWorker(client)
        self._worker.moveToThread(self._thread)

        self._worker.snapshot_ready.connect(self.snapshot_ready)
        self._worker.error.connect(self.error)
        self._worker.command_finished.connect(self._on_command_finished)

        self._open_match_requested.connect(self._worker.open_match)
        self._initialize_requested.connect(self._worker.initialize_match)
        self._start_polling_requested.connect(self._worker.start_polling)
        # Blocking so shutdown() can stop the worker-owned timer in-thread.
        self._stop_polling_requested.connect(
            self._worker.stop_polling,
            Qt.ConnectionType.BlockingQueuedConnection,
        )
        self._poll_requested.connect(self._worker.poll)
        self._request_start_requested.connect(self._worker.request_start)
        self._send_save_requested.connect(self._worker.send_save)
        self._select_candidate_requested.connect(self._worker.select_candidate)
        self._retry_requested.connect(self._worker.retry)
        self._focus_requested.connect(self._worker.focus_civ)
        self._close_civ_requested.connect(self._worker.close_civ)

        self._thread.start()

    @property
    def worker_thread(self) -> QThread:
        return self._thread

    def start_polling(self, interval_ms: int) -> None:
        self._start_polling_requested.emit(interval_ms)

    def poll_now(self) -> None:
        self._poll_requested.emit()

    def open_match(self, config: MatchConfig) -> None:
        if self._dispatchable(config.game_id):
            self._open_match_requested.emit(config)

    def initialize_match(self, config: MatchConfig) -> None:
        if self._dispatchable(config.game_id):
            self._initialize_requested.emit(config)

    def request_start(self, game_id: str) -> None:
        if self._dispatchable(game_id):
            self._request_start_requested.emit(game_id)

    def send_save(self, game_id: str) -> None:
        if self._dispatchable(game_id):
            self._send_save_requested.emit(game_id)

    def select_candidate(self, game_id: str, path: str) -> None:
        if self._dispatchable(game_id):
            self._select_candidate_requested.emit(game_id, path)

    def retry(self, game_id: str) -> None:
        if self._dispatchable(game_id):
            self._retry_requested.emit(game_id)

    def focus_civ(self, game_id: str) -> None:
        if self._dispatchable(game_id):
            self._focus_requested.emit(game_id)

    def close_civ(self, game_id: str) -> None:
        if self._dispatchable(game_id):
            self._close_civ_requested.emit(game_id)

    def shutdown(self) -> None:
        """Stop polling, quit and join the thread, close the client. Idempotent."""
        if self._shut_down:
            return
        self._shut_down = True
        if self._thread.isRunning():
            self._stop_polling_requested.emit()
            self._thread.quit()
            self._thread.wait(QDeadlineTimer(_SHUTDOWN_WAIT_MS))
        self._client.close()

    def _dispatchable(self, game_id: str) -> bool:
        if self._shut_down:
            self.error.emit(f"{game_id}: the worker is shut down; command ignored")
            return False
        if game_id in self._busy:
            self.error.emit(
                f"{game_id}: a command is already in flight; the duplicate "
                "request was ignored"
            )
            return False
        self._busy.add(game_id)
        return True

    @Slot(str)
    def _on_command_finished(self, game_id: str) -> None:
        self._busy.discard(game_id)
