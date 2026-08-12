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
from enum import Enum, unique

from PySide6.QtCore import (
    QDeadlineTimer,
    QObject,
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
from civ4_turn_relay.protocol import InitializeResult
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


@unique
class WorkerShutdownOutcome(Enum):
    """Result of an orderly :meth:`RelayWorkerHub.shutdown` attempt."""

    SUCCEEDED = "succeeded"
    ALREADY_SHUT_DOWN = "already_shut_down"
    JOIN_TIMED_OUT = "join_timed_out"


@dataclass(frozen=True, slots=True)
class WorkerShutdownResult:
    """Typed shutdown result for the coordinator / UI."""

    outcome: WorkerShutdownOutcome

    @property
    def succeeded(self) -> bool:
        return self.outcome in {
            WorkerShutdownOutcome.SUCCEEDED,
            WorkerShutdownOutcome.ALREADY_SHUT_DOWN,
        }

    @property
    def join_timed_out(self) -> bool:
        return self.outcome is WorkerShutdownOutcome.JOIN_TIMED_OUT


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
        self._shutting_down = False

    @property
    def open_game_ids(self) -> tuple[str, ...]:
        return tuple(self._open_ids)

    @Slot(int)
    def start_polling(self, interval_ms: int) -> None:
        """Create (in-thread) and start the poll timer."""
        if self._shutting_down:
            return
        if self._timer is None:
            self._timer = QTimer(self)
            self._timer.timeout.connect(self.poll)
        self._timer.start(max(1, interval_ms))

    @Slot()
    def stop_polling(self) -> None:
        """Stop the poll timer; safe to call repeatedly."""
        if self._timer is not None:
            self._timer.stop()

    @Slot()
    def prepare_shutdown(self) -> None:
        """Stop timers and refuse further RelayClient work on this thread."""
        self._shutting_down = True
        if self._timer is not None:
            self._timer.stop()
            self._timer.deleteLater()
            self._timer = None

    @Slot()
    def poll(self) -> None:
        """Tick every open match and emit a fresh snapshot for each."""
        if self._shutting_down:
            return
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
            config.game_id,
            lambda: self._client.initialize_or_join(config),
            accept_result=lambda result: (
                isinstance(result, InitializeResult) and result.initialized
            ),
            rejected_result_text=lambda result: (
                f"{config.game_id}: remote match initialization did not "
                f"complete ({result.outcome.value})"
                if isinstance(result, InitializeResult)
                else f"{config.game_id}: remote match initialization returned "
                "an unexpected result"
            ),
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

    def _run_command(
        self,
        game_id: str,
        command: Callable[[], object],
        *,
        accept_result: Callable[[object], bool] | None = None,
        rejected_result_text: Callable[[object], str] | None = None,
    ) -> None:
        if self._shutting_down:
            self.error.emit(f"{game_id}: the worker is shut down; command ignored")
            return
        if game_id in self._busy:
            self.error.emit(
                f"{game_id}: a command is already running; the duplicate "
                "request was ignored"
            )
            return
        self._busy.add(game_id)
        try:
            result = command()
            accepted = accept_result is None or accept_result(result)
            if accepted:
                if game_id not in self._open_ids:
                    self._open_ids.append(game_id)
            else:
                message = (
                    rejected_result_text(result)
                    if rejected_result_text is not None
                    else f"{game_id}: command did not complete"
                )
                self.error.emit(message)
        except Exception as exc:
            self.error.emit(_safe_error_text(game_id, exc))
        finally:
            self._busy.discard(game_id)
            # Finish first so the hub-side busy flag is cleared before any
            # observer reacts to the fresh snapshot (queued in-order).
            self.command_finished.emit(game_id)
            if not self._shutting_down and game_id in self._open_ids:
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
    _prepare_shutdown_requested = Signal()
    _poll_requested = Signal()
    _request_start_requested = Signal(str)
    _send_save_requested = Signal(str)
    _select_candidate_requested = Signal(str, str)
    _retry_requested = Signal(str)
    _focus_requested = Signal(str)
    _close_civ_requested = Signal(str)

    def __init__(
        self,
        client: RelayClient,
        parent: QObject | None = None,
        *,
        shutdown_wait_ms: int = _SHUTDOWN_WAIT_MS,
    ) -> None:
        super().__init__(parent)
        self._client = client
        self._busy: set[str] = set()
        self._shut_down = False
        self._client_closed = False
        self._join_timed_out = False
        self._shutdown_quit_posted = False
        self._shutdown_wait_ms = max(1, int(shutdown_wait_ms))
        self._thread = QThread()
        self._thread.setObjectName("relay-worker")
        self._worker = MatchWorker(client)
        self._worker.moveToThread(self._thread)
        # Standard Qt ownership: delete the worker when its thread finishes.
        # Do not call deleteLater() from the GUI thread after join.
        self._thread.finished.connect(self._worker.deleteLater)

        self._worker.snapshot_ready.connect(self.snapshot_ready)
        self._worker.error.connect(self.error)
        self._worker.command_finished.connect(self._on_command_finished)

        self._open_match_requested.connect(self._worker.open_match)
        self._initialize_requested.connect(self._worker.initialize_match)
        self._start_polling_requested.connect(self._worker.start_polling)
        # Queued (not blocking): a long in-flight RelayClient call must not
        # freeze the GUI thread; shutdown uses wait() with a typed timeout.
        self._prepare_shutdown_requested.connect(self._worker.prepare_shutdown)
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

    @property
    def join_timed_out(self) -> bool:
        """True when the last shutdown attempt timed out joining the worker."""
        return self._join_timed_out

    @property
    def client_closed(self) -> bool:
        """True after a successful shutdown closed the RelayClient."""
        return self._client_closed

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

    def shutdown(self) -> WorkerShutdownResult:
        """Stop polling, join the worker, then close the client.

        Successful shutdown is idempotent. A join timeout is observable, never
        uses ``QThread.terminate()``, never closes ``RelayClient`` while the
        worker may still be running, and may be retried later.
        """
        if self._client_closed:
            return WorkerShutdownResult(WorkerShutdownOutcome.ALREADY_SHUT_DOWN)

        # Refuse new GUI dispatch for this and any concurrent quit attempts.
        self._shut_down = True
        self._join_timed_out = False

        if self._thread.isRunning():
            # Post prepare+quit once; retries only wait again so we never race
            # a second quit against an already-deleting worker.
            if not self._shutdown_quit_posted:
                # Queue prepare_shutdown first; the queued call remains even
                # after disconnect. Disconnect while the worker is still alive
                # so we never touch it after finished→deleteLater.
                self._prepare_shutdown_requested.emit()
                self._disconnect_worker_signals()
                self._thread.quit()
                self._shutdown_quit_posted = True
            joined = self._thread.wait(QDeadlineTimer(self._shutdown_wait_ms))
            if not joined:
                self._join_timed_out = True
                # Leave the worker and RelayClient intact for a later retry.
                return WorkerShutdownResult(WorkerShutdownOutcome.JOIN_TIMED_OUT)
        else:
            # Thread already finished (e.g. after a prior timed-out quit).
            self._disconnect_worker_signals()

        self._client.close()
        self._client_closed = True
        return WorkerShutdownResult(WorkerShutdownOutcome.SUCCEEDED)

    def _worker_is_alive(self) -> bool:
        try:
            from shiboken6 import isValid

            return bool(isValid(self._worker))
        except Exception:
            try:
                self._worker.objectName()
            except RuntimeError:
                return False
            return True

    def _disconnect_worker_signals(self) -> None:
        if not self._worker_is_alive():
            return
        try:
            worker = self._worker
            pairs: tuple[tuple[object, object], ...] = (
                (worker.snapshot_ready, self.snapshot_ready),
                (worker.error, self.error),
                (worker.command_finished, self._on_command_finished),
                (self._open_match_requested, worker.open_match),
                (self._initialize_requested, worker.initialize_match),
                (self._start_polling_requested, worker.start_polling),
                (self._prepare_shutdown_requested, worker.prepare_shutdown),
                (self._poll_requested, worker.poll),
                (self._request_start_requested, worker.request_start),
                (self._send_save_requested, worker.send_save),
                (self._select_candidate_requested, worker.select_candidate),
                (self._retry_requested, worker.retry),
                (self._focus_requested, worker.focus_civ),
                (self._close_civ_requested, worker.close_civ),
            )
        except RuntimeError:
            # Worker QObject already deleted via finished→deleteLater.
            return
        for signal, slot in pairs:
            if not self._worker_is_alive():
                return
            disconnect = getattr(signal, "disconnect", None)
            if disconnect is None:
                continue
            try:
                disconnect(slot)
            except (RuntimeError, TypeError, SystemError):
                pass

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
