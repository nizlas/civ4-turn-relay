"""WindowsProcessSupervisor against a scripted WindowsProcessBackend."""

from __future__ import annotations

import sys
import threading
from datetime import UTC, datetime

import pytest

from civ4_turn_relay.process import (
    CivLaunchCommand,
    CloseRequestOutcome,
    FocusOutcome,
    GuardAcquireOutcome,
    GuardAcquisition,
    GuardedLaunchOutcome,
    LaunchOutcome,
    ProbeOutcome,
    ProcessIdentity,
    ProcessInfo,
    ProcessScanEntry,
    ProcessSupervisor,
    TerminateOutcome,
    WindowsNamedMutexLaunchGuard,
    WindowsProcessSupervisor,
    launch_guard_name,
)

_EXE = "C:\\Games\\Civ4\\Civ4BeyondSword.exe"
_COMMAND = CivLaunchCommand(argv=(_EXE, "mod=Mods\\AdvCiv"), working_directory=None)
_START_UTC = "2026-08-11T12:00:00Z"
_START_EPOCH = datetime(2026, 8, 11, 12, 0, 0, tzinfo=UTC).timestamp()
_LAUNCH_OFFSET = 0.25
_CREATE_NS = int(round((_START_EPOCH + _LAUNCH_OFFSET) * 1_000_000_000))
_IDENTITY = ProcessIdentity(
    pid=4321,
    process_start_time_utc=_START_UTC,
    process_create_time_ns=_CREATE_NS,
    executable_path=_EXE,
)


class ScriptedBackend:
    """Recording WindowsProcessBackend with scriptable results."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        self.spawn_pid = 4321
        self.spawn_error: Exception | None = None
        self.info: ProcessInfo | None = None
        self.info_error: Exception | None = None
        self.close_result = True
        self.close_error: Exception | None = None
        self.focus_result = True
        self.focus_error: Exception | None = None
        self.terminate_result = True
        self.terminate_error: Exception | None = None
        self.scan_entries: tuple[ProcessScanEntry, ...] = ()
        self.scan_error: Exception | None = None

    def spawn(self, argv: tuple[str, ...], working_directory: str | None) -> int:
        self.calls.append(("spawn", argv))
        if self.spawn_error is not None:
            raise self.spawn_error
        return self.spawn_pid

    def process_info(self, pid: int) -> ProcessInfo | None:
        self.calls.append(("process_info", pid))
        if self.info_error is not None:
            raise self.info_error
        return self.info

    def post_close_to_windows(self, pid: int) -> bool:
        self.calls.append(("post_close", pid))
        if self.close_error is not None:
            raise self.close_error
        return self.close_result

    def focus_window(self, pid: int) -> bool:
        self.calls.append(("focus", pid))
        if self.focus_error is not None:
            raise self.focus_error
        return self.focus_result

    def terminate_process(self, pid: int) -> bool:
        self.calls.append(("terminate", pid))
        if self.terminate_error is not None:
            raise self.terminate_error
        return self.terminate_result

    def iter_process_entries(self) -> tuple[ProcessScanEntry, ...]:
        self.calls.append(("scan", None))
        if self.scan_error is not None:
            raise self.scan_error
        return self.scan_entries

    def call_names(self) -> list[str]:
        return [name for name, _argument in self.calls]


class ScriptedGuard:
    """Recording in-process LaunchGuard double for supervisor tests."""

    def __init__(
        self, *, outcome: GuardAcquireOutcome = GuardAcquireOutcome.ACQUIRED
    ) -> None:
        self.outcome = outcome
        self.acquired: list[str] = []
        self.released: list[GuardAcquisition] = []

    def acquire(self, executable_path: str) -> GuardAcquisition:
        self.acquired.append(executable_path)
        held = self.outcome in {
            GuardAcquireOutcome.ACQUIRED,
            GuardAcquireOutcome.ACQUIRED_ABANDONED,
        }
        return GuardAcquisition(outcome=self.outcome, handle=object() if held else None)

    def release(self, acquisition: GuardAcquisition) -> None:
        self.released.append(acquisition)


def _supervisor(backend: ScriptedBackend) -> WindowsProcessSupervisor:
    return WindowsProcessSupervisor(
        backend=backend, platform="win32", sleep_fn=lambda _seconds: None
    )


def _running_info(
    *, epoch_offset: float = _LAUNCH_OFFSET, executable_path: str = _EXE
) -> ProcessInfo:
    return ProcessInfo(
        pid=4321,
        create_time_epoch=_START_EPOCH + epoch_offset,
        executable_path=executable_path,
    )


def test_satisfies_the_supervisor_protocol() -> None:
    assert isinstance(_supervisor(ScriptedBackend()), ProcessSupervisor)


def test_launch_builds_identity_from_observed_info() -> None:
    backend = ScriptedBackend()
    backend.info = _running_info(epoch_offset=0.75)
    result = _supervisor(backend).launch(_COMMAND)
    assert result.outcome is LaunchOutcome.LAUNCHED
    assert result.identity is not None
    assert result.identity.pid == 4321
    assert result.identity.process_start_time_utc == _START_UTC
    assert result.identity.process_create_time_ns == int(
        round((_START_EPOCH + 0.75) * 1_000_000_000)
    )
    assert result.identity.executable_path == _EXE
    assert backend.call_names() == ["spawn", "process_info"]
    assert backend.calls[1] == ("process_info", 4321)


def test_launch_sleeps_for_the_verify_delay() -> None:
    backend = ScriptedBackend()
    backend.info = _running_info()
    sleeps: list[float] = []
    supervisor = WindowsProcessSupervisor(
        backend=backend,
        platform="win32",
        sleep_fn=sleeps.append,
        verify_delay_seconds=0.25,
    )
    supervisor.launch(_COMMAND)
    assert sleeps == [0.25]


def test_launch_spawn_oserror_maps_to_spawn_failure() -> None:
    backend = ScriptedBackend()
    backend.spawn_error = OSError("access denied")
    result = _supervisor(backend).launch(_COMMAND)
    assert result.outcome is LaunchOutcome.SPAWN_FAILURE
    assert result.identity is None
    assert "access denied" in result.message
    assert backend.call_names() == ["spawn"]


def test_launch_missing_info_maps_to_exited_immediately() -> None:
    backend = ScriptedBackend()
    backend.info = None
    result = _supervisor(backend).launch(_COMMAND)
    assert result.outcome is LaunchOutcome.EXITED_IMMEDIATELY
    assert result.identity is None


def test_launch_executable_mismatch_maps_to_identity_unverified() -> None:
    backend = ScriptedBackend()
    backend.info = _running_info(executable_path="C:\\Other\\imposter.exe")
    result = _supervisor(backend).launch(_COMMAND)
    assert result.outcome is LaunchOutcome.IDENTITY_UNVERIFIED
    assert result.identity is None


def test_launch_accepts_case_only_executable_difference() -> None:
    backend = ScriptedBackend()
    backend.info = _running_info(executable_path=_EXE.upper())
    result = _supervisor(backend).launch(_COMMAND)
    assert result.outcome is LaunchOutcome.LAUNCHED
    assert result.identity is not None
    # The stored value is what the OS reported, not a normalized form.
    assert result.identity.executable_path == _EXE.upper()


def test_probe_matches_exact_creation_token() -> None:
    backend = ScriptedBackend()
    backend.info = _running_info()
    result = _supervisor(backend).probe(_IDENTITY)
    assert result.outcome is ProbeOutcome.RUNNING_MATCH


def test_probe_mismatch_for_reused_pid_within_the_same_second() -> None:
    # Same pid, same whole-second start time, different precise creation
    # token: this must be a mismatch, never a match.
    backend = ScriptedBackend()
    backend.info = _running_info(epoch_offset=0.9)
    result = _supervisor(backend).probe(_IDENTITY)
    assert result.outcome is ProbeOutcome.RUNNING_MISMATCH


def test_probe_mismatch_on_different_start_second() -> None:
    backend = ScriptedBackend()
    backend.info = _running_info(epoch_offset=5.0)
    result = _supervisor(backend).probe(_IDENTITY)
    assert result.outcome is ProbeOutcome.RUNNING_MISMATCH


def test_probe_mismatch_on_different_executable() -> None:
    backend = ScriptedBackend()
    backend.info = _running_info(executable_path="C:\\Other\\imposter.exe")
    result = _supervisor(backend).probe(_IDENTITY)
    assert result.outcome is ProbeOutcome.RUNNING_MISMATCH


def test_probe_matches_case_insensitively() -> None:
    backend = ScriptedBackend()
    backend.info = _running_info(executable_path=_EXE.upper())
    result = _supervisor(backend).probe(_IDENTITY)
    assert result.outcome is ProbeOutcome.RUNNING_MATCH


def test_probe_not_running() -> None:
    backend = ScriptedBackend()
    backend.info = None
    assert _supervisor(backend).probe(_IDENTITY).outcome is ProbeOutcome.NOT_RUNNING


def test_probe_backend_exception_maps_to_probe_failed() -> None:
    backend = ScriptedBackend()
    backend.info_error = RuntimeError("boom")
    result = _supervisor(backend).probe(_IDENTITY)
    assert result.outcome is ProbeOutcome.PROBE_FAILED
    assert "boom" in result.message


def test_close_requests_after_successful_verification() -> None:
    backend = ScriptedBackend()
    backend.info = _running_info()
    result = _supervisor(backend).request_graceful_close(_IDENTITY)
    assert result.outcome is CloseRequestOutcome.REQUESTED
    assert backend.call_names() == ["process_info", "post_close"]
    assert backend.calls[1] == ("post_close", 4321)


def test_close_identity_mismatch_never_touches_windows() -> None:
    backend = ScriptedBackend()
    backend.info = _running_info(epoch_offset=5.0)
    result = _supervisor(backend).request_graceful_close(_IDENTITY)
    assert result.outcome is CloseRequestOutcome.IDENTITY_MISMATCH
    assert "post_close" not in backend.call_names()


def test_close_mismatch_for_reused_pid_within_the_same_second() -> None:
    backend = ScriptedBackend()
    backend.info = _running_info(epoch_offset=0.9)
    result = _supervisor(backend).request_graceful_close(_IDENTITY)
    assert result.outcome is CloseRequestOutcome.IDENTITY_MISMATCH
    assert "post_close" not in backend.call_names()


def test_terminate_mismatch_for_reused_pid_within_the_same_second() -> None:
    backend = ScriptedBackend()
    backend.info = _running_info(epoch_offset=0.9)
    result = _supervisor(backend).terminate(_IDENTITY)
    assert result.outcome is TerminateOutcome.IDENTITY_MISMATCH
    assert "terminate" not in backend.call_names()


def test_close_not_running() -> None:
    backend = ScriptedBackend()
    backend.info = None
    result = _supervisor(backend).request_graceful_close(_IDENTITY)
    assert result.outcome is CloseRequestOutcome.NOT_RUNNING
    assert "post_close" not in backend.call_names()


def test_close_without_reached_window_maps_to_request_failed() -> None:
    backend = ScriptedBackend()
    backend.info = _running_info()
    backend.close_result = False
    result = _supervisor(backend).request_graceful_close(_IDENTITY)
    assert result.outcome is CloseRequestOutcome.REQUEST_FAILED


def test_close_backend_exception_maps_to_request_failed() -> None:
    backend = ScriptedBackend()
    backend.info = _running_info()
    backend.close_error = RuntimeError("no windows")
    result = _supervisor(backend).request_graceful_close(_IDENTITY)
    assert result.outcome is CloseRequestOutcome.REQUEST_FAILED
    assert "no windows" in result.message


def test_focus_after_successful_verification() -> None:
    backend = ScriptedBackend()
    backend.info = _running_info()
    result = _supervisor(backend).focus(_IDENTITY)
    assert result.outcome is FocusOutcome.FOCUSED
    assert backend.call_names() == ["process_info", "focus"]


def test_focus_identity_mismatch_never_touches_windows() -> None:
    backend = ScriptedBackend()
    backend.info = _running_info(executable_path="C:\\Other\\imposter.exe")
    result = _supervisor(backend).focus(_IDENTITY)
    assert result.outcome is FocusOutcome.IDENTITY_MISMATCH
    assert "focus" not in backend.call_names()


def test_focus_without_window_maps_to_no_window() -> None:
    backend = ScriptedBackend()
    backend.info = _running_info()
    backend.focus_result = False
    assert _supervisor(backend).focus(_IDENTITY).outcome is FocusOutcome.NO_WINDOW


def test_focus_not_running_and_backend_exception() -> None:
    backend = ScriptedBackend()
    backend.info = None
    assert _supervisor(backend).focus(_IDENTITY).outcome is FocusOutcome.NOT_RUNNING
    backend.info = _running_info()
    backend.focus_error = RuntimeError("focus denied")
    result = _supervisor(backend).focus(_IDENTITY)
    assert result.outcome is FocusOutcome.FOCUS_FAILED
    assert "focus denied" in result.message


def test_terminate_after_successful_verification() -> None:
    backend = ScriptedBackend()
    backend.info = _running_info()
    result = _supervisor(backend).terminate(_IDENTITY)
    assert result.outcome is TerminateOutcome.TERMINATED
    assert backend.call_names() == ["process_info", "terminate"]
    assert backend.calls[1] == ("terminate", 4321)


def test_terminate_identity_mismatch_never_touches_process() -> None:
    backend = ScriptedBackend()
    backend.info = _running_info(epoch_offset=5.0)
    result = _supervisor(backend).terminate(_IDENTITY)
    assert result.outcome is TerminateOutcome.IDENTITY_MISMATCH
    assert "terminate" not in backend.call_names()


def test_terminate_not_running() -> None:
    backend = ScriptedBackend()
    backend.info = None
    result = _supervisor(backend).terminate(_IDENTITY)
    assert result.outcome is TerminateOutcome.NOT_RUNNING
    assert "terminate" not in backend.call_names()


def test_terminate_failure_and_backend_exception() -> None:
    backend = ScriptedBackend()
    backend.info = _running_info()
    backend.terminate_result = False
    result = _supervisor(backend).terminate(_IDENTITY)
    assert result.outcome is TerminateOutcome.TERMINATE_FAILED
    backend.terminate_error = RuntimeError("kill blocked")
    result = _supervisor(backend).terminate(_IDENTITY)
    assert result.outcome is TerminateOutcome.TERMINATE_FAILED
    assert "kill blocked" in result.message


def test_non_windows_platform_is_unavailable_without_backend_calls() -> None:
    backend = ScriptedBackend()
    backend.info = _running_info()
    supervisor = WindowsProcessSupervisor(
        backend=backend, platform="linux", sleep_fn=lambda _seconds: None
    )
    availability = supervisor.availability()
    assert availability.available is False
    assert "windows_only" in availability.reason
    assert supervisor.launch(_COMMAND).outcome is LaunchOutcome.ADAPTER_UNAVAILABLE
    assert supervisor.probe(_IDENTITY).outcome is ProbeOutcome.ADAPTER_UNAVAILABLE
    close = supervisor.request_graceful_close(_IDENTITY)
    assert close.outcome is CloseRequestOutcome.ADAPTER_UNAVAILABLE
    assert supervisor.focus(_IDENTITY).outcome is FocusOutcome.ADAPTER_UNAVAILABLE
    terminate = supervisor.terminate(_IDENTITY)
    assert terminate.outcome is TerminateOutcome.ADAPTER_UNAVAILABLE
    assert backend.calls == []


@pytest.mark.parametrize("platform", ["darwin", "cygwin"])
def test_other_non_windows_platforms_are_unavailable(platform: str) -> None:
    supervisor = WindowsProcessSupervisor(
        backend=ScriptedBackend(), platform=platform, sleep_fn=lambda _seconds: None
    )
    assert supervisor.availability().available is False


# --- guarded launch through the Windows supervisor --------------------------


def _guarded_supervisor(
    backend: ScriptedBackend, guard: ScriptedGuard
) -> WindowsProcessSupervisor:
    return WindowsProcessSupervisor(
        backend=backend, platform="win32", sleep_fn=lambda _seconds: None, guard=guard
    )


def test_guarded_launch_scans_then_spawns_and_releases() -> None:
    backend = ScriptedBackend()
    backend.info = _running_info()
    guard = ScriptedGuard()
    result = _guarded_supervisor(backend, guard).guarded_launch(_COMMAND)
    assert result.outcome is GuardedLaunchOutcome.LAUNCHED
    assert result.identity is not None
    assert backend.call_names() == ["scan", "spawn", "process_info"]
    assert guard.acquired == [_EXE]
    assert len(guard.released) == 1


def test_guarded_launch_existing_civ_blocks_without_spawn() -> None:
    backend = ScriptedBackend()
    backend.scan_entries = (
        ProcessScanEntry(pid=777, executable_path=_EXE.upper(), name=None),
    )
    guard = ScriptedGuard()
    result = _guarded_supervisor(backend, guard).guarded_launch(_COMMAND)
    assert result.outcome is GuardedLaunchOutcome.EXISTING_CIV_DETECTED
    assert result.existing_pid == 777
    assert "spawn" not in backend.call_names()
    assert len(guard.released) == 1


def test_guarded_launch_busy_guard_defers_without_scan_or_spawn() -> None:
    backend = ScriptedBackend()
    guard = ScriptedGuard(outcome=GuardAcquireOutcome.BUSY)
    result = _guarded_supervisor(backend, guard).guarded_launch(_COMMAND)
    assert result.outcome is GuardedLaunchOutcome.GUARD_BUSY
    assert backend.calls == []
    assert guard.released == []


def test_guarded_launch_scan_failure_fails_closed_and_releases() -> None:
    backend = ScriptedBackend()
    backend.scan_error = RuntimeError("scan blew up")
    guard = ScriptedGuard()
    result = _guarded_supervisor(backend, guard).guarded_launch(_COMMAND)
    assert result.outcome is GuardedLaunchOutcome.SCAN_INDETERMINATE
    assert "spawn" not in backend.call_names()
    assert len(guard.released) == 1


def test_guarded_launch_spawn_failure_still_releases() -> None:
    backend = ScriptedBackend()
    backend.spawn_error = OSError("access denied")
    guard = ScriptedGuard()
    result = _guarded_supervisor(backend, guard).guarded_launch(_COMMAND)
    assert result.outcome is GuardedLaunchOutcome.SPAWN_FAILURE
    assert len(guard.released) == 1


def test_guarded_launch_identity_failure_still_releases() -> None:
    backend = ScriptedBackend()
    backend.info = _running_info(executable_path="C:\\Other\\imposter.exe")
    guard = ScriptedGuard()
    result = _guarded_supervisor(backend, guard).guarded_launch(_COMMAND)
    assert result.outcome is GuardedLaunchOutcome.IDENTITY_UNVERIFIED
    assert len(guard.released) == 1


def test_guarded_launch_abandoned_owner_still_scans_before_spawn() -> None:
    backend = ScriptedBackend()
    backend.scan_entries = (ProcessScanEntry(pid=555, executable_path=_EXE, name=None),)
    guard = ScriptedGuard(outcome=GuardAcquireOutcome.ACQUIRED_ABANDONED)
    result = _guarded_supervisor(backend, guard).guarded_launch(_COMMAND)
    assert result.outcome is GuardedLaunchOutcome.EXISTING_CIV_DETECTED
    assert result.recovered_abandoned_guard is True
    assert "spawn" not in backend.call_names()
    assert len(guard.released) == 1


def test_guarded_launch_unavailable_off_windows() -> None:
    backend = ScriptedBackend()
    guard = ScriptedGuard()
    supervisor = WindowsProcessSupervisor(
        backend=backend, platform="linux", sleep_fn=lambda _seconds: None, guard=guard
    )
    result = supervisor.guarded_launch(_COMMAND)
    assert result.outcome is GuardedLaunchOutcome.ADAPTER_UNAVAILABLE
    assert backend.calls == []
    assert guard.acquired == []


def test_guarded_launch_without_guard_is_typed_unavailable() -> None:
    backend = ScriptedBackend()
    supervisor = WindowsProcessSupervisor(
        backend=backend, platform="win32", sleep_fn=lambda _seconds: None
    )
    supervisor._guard = None  # simulate a host without a usable guard
    result = supervisor.guarded_launch(_COMMAND)
    assert result.outcome is GuardedLaunchOutcome.GUARD_UNAVAILABLE
    assert backend.calls == []


# --- real Windows named mutex (only runs on Windows hosts) ------------------

_windows_only = pytest.mark.skipif(
    sys.platform != "win32", reason="requires a real Windows named mutex"
)


def test_launch_guard_name_is_stable_and_per_executable() -> None:
    name = launch_guard_name(_EXE)
    assert name.startswith("Local\\civ4-turn-relay-launch-")
    assert launch_guard_name(_EXE.upper()) == name
    assert launch_guard_name("C:\\Other\\Civ4BeyondSword.exe") != name


@_windows_only
def test_named_mutex_acquire_release_and_reacquire() -> None:
    guard = WindowsNamedMutexLaunchGuard(wait_timeout_ms=0)
    first = guard.acquire(_EXE)
    assert first.outcome is GuardAcquireOutcome.ACQUIRED
    guard.release(first)
    second = guard.acquire(_EXE)
    assert second.outcome is GuardAcquireOutcome.ACQUIRED
    guard.release(second)


@_windows_only
def test_named_mutex_busy_for_a_concurrent_holder() -> None:
    guard = WindowsNamedMutexLaunchGuard(wait_timeout_ms=0)
    held = guard.acquire(_EXE)
    assert held.outcome is GuardAcquireOutcome.ACQUIRED
    observed: dict[str, GuardAcquisition] = {}

    def contender() -> None:
        observed["result"] = guard.acquire(_EXE)

    thread = threading.Thread(target=contender)
    thread.start()
    thread.join()
    assert observed["result"].outcome is GuardAcquireOutcome.BUSY
    guard.release(held)


@_windows_only
def test_named_mutex_abandoned_by_dead_holder_is_recovered() -> None:
    # A holder whose thread dies without releasing must not leave a stale
    # lock: the OS hands the next waiter WAIT_ABANDONED instead.
    guard = WindowsNamedMutexLaunchGuard(wait_timeout_ms=0)

    def crashing_holder() -> None:
        acquisition = guard.acquire(_EXE)
        assert acquisition.outcome is GuardAcquireOutcome.ACQUIRED
        # Thread ends while owning the mutex — a simulated Relay crash.

    thread = threading.Thread(target=crashing_holder)
    thread.start()
    thread.join()
    # join() can return a moment before the OS thread fully terminates; a
    # bounded wait deterministically observes WAIT_ABANDONED once it does.
    waiting_guard = WindowsNamedMutexLaunchGuard(wait_timeout_ms=5000)
    recovered = waiting_guard.acquire(_EXE)
    assert recovered.outcome is GuardAcquireOutcome.ACQUIRED_ABANDONED
    waiting_guard.release(recovered)
