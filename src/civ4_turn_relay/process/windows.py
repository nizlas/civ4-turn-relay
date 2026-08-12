"""Windows process supervisor adapter.

:class:`RealWindowsBackend` is the only layer touching real OS calls (spawn,
psutil inspection, Win32 window messaging). :class:`WindowsProcessSupervisor`
turns backend facts into port results and enforces exact identity
verification before every close/focus/terminate action. Nothing here decides
match ownership.
"""

from __future__ import annotations

import math
import os
import subprocess
import sys
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

import psutil

from civ4_turn_relay.domain import DomainValidationError
from civ4_turn_relay.process.guard import (
    GuardAcquireOutcome,
    GuardAcquisition,
    GuardedLaunchOutcome,
    GuardedLaunchResult,
    LaunchGuard,
    MachineScanOutcome,
    MachineScanResult,
    ProcessScanEntry,
    classify_scan_entries,
    execute_guarded_launch,
    launch_guard_name,
)
from civ4_turn_relay.process.launch_config import CivLaunchCommand
from civ4_turn_relay.process.port import (
    CloseRequestOutcome,
    CloseRequestResult,
    FocusOutcome,
    FocusResult,
    LaunchOutcome,
    LaunchResult,
    ProbeOutcome,
    ProbeResult,
    ProcessIdentity,
    SupervisorAvailability,
    TerminateOutcome,
    TerminateResult,
)

_WM_CLOSE = 0x0010
_SW_RESTORE = 9
_UNAVAILABLE_REASON = "windows_only: process adapter requires Windows"

WAIT_OBJECT_0 = 0x00000000
WAIT_ABANDONED = 0x00000080
WAIT_TIMEOUT = 0x00000102
WAIT_FAILED = 0xFFFFFFFF
_GUARD_WAIT_TIMEOUT_MS = 250


@dataclass(frozen=True, slots=True)
class ProcessInfo:
    """OS-reported facts about one running process."""

    pid: int
    create_time_epoch: float
    executable_path: str


@runtime_checkable
class WindowsProcessBackend(Protocol):
    """The only layer touching real OS calls; everything above is pure."""

    def spawn(self, argv: tuple[str, ...], working_directory: str | None) -> int:
        """Spawn ``argv`` without a shell; return the pid. Raises OSError."""
        ...

    def process_info(self, pid: int) -> ProcessInfo | None:
        """Return facts about ``pid``, or None if it is not running."""
        ...

    def post_close_to_windows(self, pid: int) -> bool:
        """Post WM_CLOSE to visible top-level windows; True if >= 1 posted."""
        ...

    def focus_window(self, pid: int) -> bool:
        """Restore and foreground a top-level window of ``pid``."""
        ...

    def terminate_process(self, pid: int) -> bool:
        """Terminate ``pid`` (graceful terminate, short wait); True if exited."""
        ...

    def iter_process_entries(self) -> tuple[ProcessScanEntry, ...]:
        """Snapshot every visible machine process for the launch scan.

        Entries whose executable could not be resolved carry ``None`` and
        the short process name when known; processes that disappear during
        the snapshot are simply omitted.
        """
        ...


def _epoch_to_utc_seconds(epoch: float) -> str:
    """Format an epoch to exact second-resolution ``YYYY-MM-DDTHH:MM:SSZ``."""
    moment = datetime.fromtimestamp(math.floor(epoch), tz=UTC)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _epoch_to_create_time_ns(epoch: float) -> int:
    """Derive the stable high-resolution creation token from the backend epoch.

    psutil reports the OS process creation time as a float; converting it
    with one fixed rule yields the same integer for the same process on
    every probe, while two processes created within the same wall-clock
    second still get different tokens.
    """
    return int(round(epoch * 1_000_000_000))


def _normalize_executable(path: str) -> str:
    """Normalize an executable path for comparison only (never stored)."""
    return os.path.normpath(path).casefold()


def _visible_top_level_windows(pid: int) -> list[int]:
    """Enumerate visible top-level window handles owned by ``pid``."""
    import ctypes
    from ctypes import wintypes

    # windll / WINFUNCTYPE exist only on Windows; stubs omit them on Linux.
    user32 = getattr(ctypes, "windll").user32
    handles: list[int] = []

    def _collect(hwnd: int, _lparam: int) -> bool:
        owner = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            handles.append(hwnd)
        return True

    win_func_type = getattr(ctypes, "WINFUNCTYPE")
    enum_callback = win_func_type(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(
        _collect
    )
    user32.EnumWindows(enum_callback, 0)
    return handles


class RealWindowsBackend:
    """Production :class:`WindowsProcessBackend`; only constructed on win32.

    ``process_iter`` is injectable so scan-boundary tests can feed a
    deterministic iterator without touching the live process table.
    """

    def __init__(
        self,
        *,
        process_iter: Callable[..., Iterable[object]] | None = None,
    ) -> None:
        self._process_iter = (
            process_iter if process_iter is not None else psutil.process_iter
        )

    def spawn(self, argv: tuple[str, ...], working_directory: str | None) -> int:
        # An argv list, never a shell.
        process = subprocess.Popen(list(argv), cwd=working_directory, close_fds=True)
        return process.pid

    def process_info(self, pid: int) -> ProcessInfo | None:
        try:
            process = psutil.Process(pid)
            if not process.is_running():
                return None
            return ProcessInfo(
                pid=pid,
                create_time_epoch=process.create_time(),
                executable_path=process.exe(),
            )
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return None

    def post_close_to_windows(self, pid: int) -> bool:
        import ctypes

        user32 = getattr(ctypes, "windll").user32
        posted = False
        for hwnd in _visible_top_level_windows(pid):
            if user32.PostMessageW(hwnd, _WM_CLOSE, 0, 0):
                posted = True
        return posted

    def focus_window(self, pid: int) -> bool:
        import ctypes

        user32 = getattr(ctypes, "windll").user32
        handles = _visible_top_level_windows(pid)
        if not handles:
            return False
        hwnd = handles[0]
        user32.ShowWindow(hwnd, _SW_RESTORE)
        return bool(user32.SetForegroundWindow(hwnd))

    def terminate_process(self, pid: int) -> bool:
        try:
            process = psutil.Process(pid)
            process.terminate()
            process.wait(timeout=2)
        except psutil.NoSuchProcess:
            return True
        except (psutil.AccessDenied, psutil.TimeoutExpired):
            return False
        return True

    def iter_process_entries(self) -> tuple[ProcessScanEntry, ...]:
        """Snapshot processes, preserving inaccessible likely-Civ candidates.

        ``psutil.process_iter(..., ad_value=None)`` keeps PID and short name
        when ``exe`` is AccessDenied, so an unverifiable ``Civ4BeyondSword.exe``
        becomes ``SCAN_INDETERMINATE`` instead of disappearing. Processes that
        vanish mid-scan are omitted. Completely nameless unreadable processes
        are emitted with ``name=None`` and never count as likely Civ.
        """
        entries: list[ProcessScanEntry] = []
        for process in self._process_iter(attrs=["pid", "name", "exe"], ad_value=None):
            try:
                info = getattr(process, "info", None)
                if not isinstance(info, dict):
                    continue
                raw_pid = info.get("pid")
                if not isinstance(raw_pid, int) or isinstance(raw_pid, bool):
                    raw_pid = getattr(process, "pid", 0)
                if (
                    not isinstance(raw_pid, int)
                    or isinstance(raw_pid, bool)
                    or raw_pid <= 0
                ):
                    continue
                exe = info.get("exe")
                name = info.get("name")
                entries.append(
                    ProcessScanEntry(
                        pid=raw_pid,
                        executable_path=exe if isinstance(exe, str) and exe else None,
                        name=name if isinstance(name, str) and name else None,
                    )
                )
            except psutil.NoSuchProcess:
                continue
        return tuple(entries)


def _handle_from_win32(value: object) -> int | None:
    """Preserve a pointer-sized HANDLE as a Python int; None if NULL."""
    if value is None:
        return None
    if isinstance(value, int):
        return None if value == 0 else value
    inner = getattr(value, "value", None)
    if inner is None or inner == 0:
        return None
    return int(inner)


class MutexReleaseError(OSError):
    """Raised when ReleaseMutex or CloseHandle fails for an owned mutex."""


@runtime_checkable
class Win32MutexApi(Protocol):
    """Injectable Win32 named-mutex ABI; HANDLE values are pointer-sized ints."""

    def create_mutex_w(self, name: str) -> int | None:
        """Create or open ``name``; ``None`` if the call failed."""
        ...

    def wait_for_single_object(self, handle: int, timeout_ms: int) -> int:
        """Wait on ``handle``; returns a Win32 wait code (DWORD)."""
        ...

    def release_mutex(self, handle: int) -> bool:
        """Release ownership of ``handle``. False means the Win32 call failed."""
        ...

    def close_handle(self, handle: int) -> bool:
        """Close ``handle``. False means the Win32 call failed."""
        ...

    def get_last_error(self) -> int:
        """Thread-local last error from the preceding Win32 call."""
        ...


class CtypesWin32MutexApi:
    """Real kernel32 named-mutex calls with an explicit pointer-sized ABI.

    ``ctypes.windll`` defaults ``restype`` to ``c_int``, which truncates a
    64-bit ``HANDLE``. This wrapper uses ``WinDLL(..., use_last_error=True)``
    and declares ``HANDLE`` as ``wintypes.HANDLE`` (``c_void_p``).
    """

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [
            wintypes.LPVOID,
            wintypes.BOOL,
            wintypes.LPCWSTR,
        ]
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.ReleaseMutex.argtypes = [wintypes.HANDLE]
        kernel32.ReleaseMutex.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        self._kernel32 = kernel32
        self._handle_type = wintypes.HANDLE

    def _handle_arg(self, handle: int) -> object:
        return self._handle_type(handle)

    def create_mutex_w(self, name: str) -> int | None:
        return _handle_from_win32(self._kernel32.CreateMutexW(None, False, name))

    def wait_for_single_object(self, handle: int, timeout_ms: int) -> int:
        return int(
            self._kernel32.WaitForSingleObject(self._handle_arg(handle), timeout_ms)
        )

    def release_mutex(self, handle: int) -> bool:
        return bool(self._kernel32.ReleaseMutex(self._handle_arg(handle)))

    def close_handle(self, handle: int) -> bool:
        return bool(self._kernel32.CloseHandle(self._handle_arg(handle)))

    def get_last_error(self) -> int:
        import ctypes

        return int(ctypes.get_last_error())


class WindowsNamedMutexLaunchGuard:
    """OS-backed interprocess launch guard using a Windows named mutex.

    The mutex name is ``Local\\civ4-turn-relay-launch-<sha256(normalized
    executable path)>``, so the guard serializes every Relay instance in the
    same interactive Windows session that targets the same Civ executable.
    Windows releases the mutex automatically when a holder crashes; the next
    waiter then observes ``WAIT_ABANDONED`` and recovers ownership, so no
    stale lock can persist. The guard cannot serialize a user or unrelated
    external program starting Civ outside Relay.

    Acquisition and release MUST run on the same calling thread (Win32 mutex
    ownership is thread-affine). Only construct the real API on Windows; tests
    inject :class:`Win32MutexApi`.
    """

    def __init__(
        self,
        *,
        wait_timeout_ms: int = _GUARD_WAIT_TIMEOUT_MS,
        api: Win32MutexApi | None = None,
    ) -> None:
        self._wait_timeout_ms = wait_timeout_ms
        self._api: Win32MutexApi = CtypesWin32MutexApi() if api is None else api
        self._owned: set[int] = set()

    def acquire(self, executable_path: str) -> GuardAcquisition:
        name = launch_guard_name(executable_path)
        handle = self._api.create_mutex_w(name)
        if handle is None:
            return GuardAcquisition(
                outcome=GuardAcquireOutcome.UNAVAILABLE,
                message=(f"CreateMutexW failed (error {self._api.get_last_error()})"),
            )
        wait = self._api.wait_for_single_object(handle, self._wait_timeout_ms)
        if wait == WAIT_OBJECT_0:
            self._owned.add(handle)
            return GuardAcquisition(outcome=GuardAcquireOutcome.ACQUIRED, handle=handle)
        if wait == WAIT_ABANDONED:
            self._owned.add(handle)
            return GuardAcquisition(
                outcome=GuardAcquireOutcome.ACQUIRED_ABANDONED,
                handle=handle,
                message=(
                    "recovered a launch guard abandoned by a crashed Relay; "
                    "a fresh process scan still runs before any launch"
                ),
            )
        self._api.close_handle(handle)
        if wait == WAIT_TIMEOUT:
            return GuardAcquisition(
                outcome=GuardAcquireOutcome.BUSY,
                message=(
                    "another Relay instance is currently checking or launching "
                    "Civilization"
                ),
            )
        return GuardAcquisition(
            outcome=GuardAcquireOutcome.UNAVAILABLE,
            message=f"WaitForSingleObject failed (status {wait})",
        )

    def release(self, acquisition: GuardAcquisition) -> None:
        handle = acquisition.handle
        if not acquisition.held or not isinstance(handle, int):
            return
        if handle not in self._owned:
            return
        self._owned.discard(handle)
        released = self._api.release_mutex(handle)
        release_error = 0 if released else self._api.get_last_error()
        closed = self._api.close_handle(handle)
        close_error = 0 if closed else self._api.get_last_error()
        if not released:
            raise MutexReleaseError(
                release_error, f"ReleaseMutex failed (error {release_error})"
            )
        if not closed:
            raise MutexReleaseError(
                close_error, f"CloseHandle failed (error {close_error})"
            )


class WindowsProcessSupervisor:
    """Windows :class:`~civ4_turn_relay.process.port.ProcessSupervisor`.

    On non-Windows platforms the adapter reports itself unavailable and every
    operation returns its unavailable outcome without touching the backend.
    """

    def __init__(
        self,
        *,
        backend: WindowsProcessBackend | None = None,
        platform: str | None = None,
        sleep_fn: Callable[[float], None] | None = None,
        verify_delay_seconds: float = 0.5,
        guard: LaunchGuard | None = None,
    ) -> None:
        self._platform = platform if platform is not None else sys.platform
        if backend is None and self._platform == "win32":
            backend = RealWindowsBackend()
        if guard is None and self._platform == "win32":
            guard = WindowsNamedMutexLaunchGuard()
        self._backend = backend
        self._guard = guard
        self._sleep = sleep_fn if sleep_fn is not None else time.sleep
        self._verify_delay_seconds = verify_delay_seconds

    def availability(self) -> SupervisorAvailability:
        if self._platform != "win32":
            return SupervisorAvailability(available=False, reason=_UNAVAILABLE_REASON)
        return SupervisorAvailability(available=True)

    def _usable_backend(self) -> WindowsProcessBackend | None:
        if self._platform != "win32":
            return None
        return self._backend

    def launch(self, command: CivLaunchCommand) -> LaunchResult:
        backend = self._usable_backend()
        if backend is None:
            return LaunchResult(
                outcome=LaunchOutcome.ADAPTER_UNAVAILABLE,
                message=_UNAVAILABLE_REASON,
            )
        try:
            pid = backend.spawn(command.argv, command.working_directory)
        except Exception as error:
            return LaunchResult(outcome=LaunchOutcome.SPAWN_FAILURE, message=str(error))
        self._sleep(self._verify_delay_seconds)
        try:
            info = backend.process_info(pid)
        except Exception as error:
            return LaunchResult(
                outcome=LaunchOutcome.IDENTITY_UNVERIFIED, message=str(error)
            )
        if info is None:
            return LaunchResult(
                outcome=LaunchOutcome.EXITED_IMMEDIATELY,
                message="the spawned process exited before verification",
            )
        if _normalize_executable(info.executable_path) != _normalize_executable(
            command.argv[0]
        ):
            return LaunchResult(
                outcome=LaunchOutcome.IDENTITY_UNVERIFIED,
                message="the observed executable does not match the launched one",
            )
        try:
            identity = ProcessIdentity(
                pid=info.pid,
                process_start_time_utc=_epoch_to_utc_seconds(info.create_time_epoch),
                process_create_time_ns=_epoch_to_create_time_ns(info.create_time_epoch),
                executable_path=info.executable_path,
            )
        except DomainValidationError as error:
            return LaunchResult(
                outcome=LaunchOutcome.IDENTITY_UNVERIFIED, message=str(error)
            )
        return LaunchResult(outcome=LaunchOutcome.LAUNCHED, identity=identity)

    def guarded_launch(self, command: CivLaunchCommand) -> GuardedLaunchResult:
        """Serialize with other Relay instances, scan, then spawn and verify.

        The named mutex, the machine scan, and the spawn+verification form
        one protected operation; the mutex is always released afterwards
        (success, blocker, indeterminate scan, spawn failure, verification
        failure, or exception).
        """
        backend = self._usable_backend()
        if backend is None:
            return GuardedLaunchResult(
                outcome=GuardedLaunchOutcome.ADAPTER_UNAVAILABLE,
                message=_UNAVAILABLE_REASON,
            )
        if self._guard is None:
            return GuardedLaunchResult(
                outcome=GuardedLaunchOutcome.GUARD_UNAVAILABLE,
                message="no interprocess launch guard is configured",
            )

        def scan(executable_path: str) -> MachineScanResult:
            try:
                entries = backend.iter_process_entries()
            except Exception as error:
                # Fail closed: an unreadable machine scan must never allow
                # a second Civ instance.
                return MachineScanResult(
                    outcome=MachineScanOutcome.INDETERMINATE,
                    message=f"the machine process scan failed: {error}",
                )
            return classify_scan_entries(entries, executable_path=executable_path)

        return execute_guarded_launch(
            guard=self._guard,
            scan=scan,
            launch=self.launch,
            command=command,
        )

    def _matches(self, identity: ProcessIdentity, info: ProcessInfo) -> bool:
        """Exact identity: precise creation token plus normalized executable.

        The second-resolution UTC timestamp is diagnostic only and is never
        used as the equality check.
        """
        return _epoch_to_create_time_ns(
            info.create_time_epoch
        ) == identity.process_create_time_ns and _normalize_executable(
            info.executable_path
        ) == _normalize_executable(identity.executable_path)

    def probe(self, identity: ProcessIdentity) -> ProbeResult:
        backend = self._usable_backend()
        if backend is None:
            return ProbeResult(
                outcome=ProbeOutcome.ADAPTER_UNAVAILABLE, message=_UNAVAILABLE_REASON
            )
        try:
            info = backend.process_info(identity.pid)
        except Exception as error:
            return ProbeResult(outcome=ProbeOutcome.PROBE_FAILED, message=str(error))
        if info is None:
            return ProbeResult(outcome=ProbeOutcome.NOT_RUNNING)
        if not self._matches(identity, info):
            return ProbeResult(
                outcome=ProbeOutcome.RUNNING_MISMATCH,
                message="the pid is running with a different start time or exe",
            )
        return ProbeResult(outcome=ProbeOutcome.RUNNING_MATCH)

    def request_graceful_close(self, identity: ProcessIdentity) -> CloseRequestResult:
        backend = self._usable_backend()
        if backend is None:
            return CloseRequestResult(
                outcome=CloseRequestOutcome.ADAPTER_UNAVAILABLE,
                message=_UNAVAILABLE_REASON,
            )
        probe = self.probe(identity)
        if probe.outcome is ProbeOutcome.NOT_RUNNING:
            return CloseRequestResult(outcome=CloseRequestOutcome.NOT_RUNNING)
        if probe.outcome is ProbeOutcome.RUNNING_MISMATCH:
            return CloseRequestResult(
                outcome=CloseRequestOutcome.IDENTITY_MISMATCH, message=probe.message
            )
        if probe.outcome is ProbeOutcome.PROBE_FAILED:
            return CloseRequestResult(
                outcome=CloseRequestOutcome.REQUEST_FAILED, message=probe.message
            )
        try:
            posted = backend.post_close_to_windows(identity.pid)
        except Exception as error:
            return CloseRequestResult(
                outcome=CloseRequestOutcome.REQUEST_FAILED, message=str(error)
            )
        if not posted:
            return CloseRequestResult(
                outcome=CloseRequestOutcome.REQUEST_FAILED,
                message="no visible top-level window accepted WM_CLOSE",
            )
        return CloseRequestResult(outcome=CloseRequestOutcome.REQUESTED)

    def focus(self, identity: ProcessIdentity) -> FocusResult:
        backend = self._usable_backend()
        if backend is None:
            return FocusResult(
                outcome=FocusOutcome.ADAPTER_UNAVAILABLE, message=_UNAVAILABLE_REASON
            )
        probe = self.probe(identity)
        if probe.outcome is ProbeOutcome.NOT_RUNNING:
            return FocusResult(outcome=FocusOutcome.NOT_RUNNING)
        if probe.outcome is ProbeOutcome.RUNNING_MISMATCH:
            return FocusResult(
                outcome=FocusOutcome.IDENTITY_MISMATCH, message=probe.message
            )
        if probe.outcome is ProbeOutcome.PROBE_FAILED:
            return FocusResult(outcome=FocusOutcome.FOCUS_FAILED, message=probe.message)
        try:
            focused = backend.focus_window(identity.pid)
        except Exception as error:
            return FocusResult(outcome=FocusOutcome.FOCUS_FAILED, message=str(error))
        if not focused:
            return FocusResult(
                outcome=FocusOutcome.NO_WINDOW,
                message="no visible top-level window could be focused",
            )
        return FocusResult(outcome=FocusOutcome.FOCUSED)

    def terminate(self, identity: ProcessIdentity) -> TerminateResult:
        """Forcibly terminate exactly ``identity``.

        Callers must hold post-commit entitlement before invoking this; the
        supervisor re-verifies identity regardless.
        """
        backend = self._usable_backend()
        if backend is None:
            return TerminateResult(
                outcome=TerminateOutcome.ADAPTER_UNAVAILABLE,
                message=_UNAVAILABLE_REASON,
            )
        probe = self.probe(identity)
        if probe.outcome is ProbeOutcome.NOT_RUNNING:
            return TerminateResult(outcome=TerminateOutcome.NOT_RUNNING)
        if probe.outcome is ProbeOutcome.RUNNING_MISMATCH:
            return TerminateResult(
                outcome=TerminateOutcome.IDENTITY_MISMATCH, message=probe.message
            )
        if probe.outcome is ProbeOutcome.PROBE_FAILED:
            return TerminateResult(
                outcome=TerminateOutcome.TERMINATE_FAILED, message=probe.message
            )
        try:
            exited = backend.terminate_process(identity.pid)
        except Exception as error:
            return TerminateResult(
                outcome=TerminateOutcome.TERMINATE_FAILED, message=str(error)
            )
        if not exited:
            return TerminateResult(
                outcome=TerminateOutcome.TERMINATE_FAILED,
                message="the process did not exit within the termination wait",
            )
        return TerminateResult(outcome=TerminateOutcome.TERMINATED)
