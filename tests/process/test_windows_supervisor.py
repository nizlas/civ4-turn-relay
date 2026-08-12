"""WindowsProcessSupervisor against a scripted WindowsProcessBackend."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from civ4_turn_relay.process import (
    CivLaunchCommand,
    CloseRequestOutcome,
    FocusOutcome,
    LaunchOutcome,
    ProbeOutcome,
    ProcessIdentity,
    ProcessInfo,
    ProcessSupervisor,
    TerminateOutcome,
    WindowsProcessSupervisor,
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

    def call_names(self) -> list[str]:
        return [name for name, _argument in self.calls]


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
