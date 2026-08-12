"""Deterministic FakeProcessSupervisor behavior."""

from __future__ import annotations

import pytest

from civ4_turn_relay.process import (
    CivLaunchCommand,
    CloseRequestOutcome,
    FakeProcessSupervisor,
    FocusOutcome,
    LaunchOutcome,
    ProbeOutcome,
    ProcessIdentity,
    ProcessSupervisor,
    TerminateOutcome,
    observation_from_identity,
)

_EXE = "C:\\Games\\Civ4\\Civ4BeyondSword.exe"
_COMMAND = CivLaunchCommand(argv=(_EXE, "mod=Mods\\AdvCiv"), working_directory=None)


def _launched_identity(supervisor: FakeProcessSupervisor) -> ProcessIdentity:
    result = supervisor.launch(_COMMAND)
    assert result.outcome is LaunchOutcome.LAUNCHED
    assert result.identity is not None
    return result.identity


def test_satisfies_the_supervisor_protocol() -> None:
    assert isinstance(FakeProcessSupervisor(), ProcessSupervisor)


def test_launch_registers_running_process() -> None:
    supervisor = FakeProcessSupervisor()
    identity = _launched_identity(supervisor)
    assert identity.pid == 5000
    assert identity.process_start_time_utc == "2026-08-11T12:00:00Z"
    assert identity.executable_path == _EXE
    assert supervisor.launched == [_COMMAND]
    assert supervisor.probe(identity).outcome is ProbeOutcome.RUNNING_MATCH


def test_launch_allocates_increasing_pids() -> None:
    supervisor = FakeProcessSupervisor(next_pid=7000)
    first = _launched_identity(supervisor)
    second = _launched_identity(supervisor)
    assert (first.pid, second.pid) == (7000, 7001)


def test_scripted_spawn_failure() -> None:
    supervisor = FakeProcessSupervisor()
    supervisor.fail_spawn = True
    result = supervisor.launch(_COMMAND)
    assert result.outcome is LaunchOutcome.SPAWN_FAILURE
    assert result.identity is None
    assert supervisor.launched == [_COMMAND]


def test_scripted_immediate_exit() -> None:
    supervisor = FakeProcessSupervisor()
    supervisor.exit_immediately = True
    assert supervisor.launch(_COMMAND).outcome is LaunchOutcome.EXITED_IMMEDIATELY


def test_scripted_identity_unverified() -> None:
    supervisor = FakeProcessSupervisor()
    supervisor.identity_unverified = True
    assert supervisor.launch(_COMMAND).outcome is LaunchOutcome.IDENTITY_UNVERIFIED


def test_probe_not_running_for_unknown_identity() -> None:
    supervisor = FakeProcessSupervisor()
    identity = ProcessIdentity(
        pid=99,
        process_start_time_utc="2026-08-11T12:00:00Z",
        process_create_time_ns=1_760_000_000_000_000_099,
        executable_path=_EXE,
    )
    assert supervisor.probe(identity).outcome is ProbeOutcome.NOT_RUNNING


def test_reused_pid_yields_mismatch_everywhere() -> None:
    supervisor = FakeProcessSupervisor()
    identity = _launched_identity(supervisor)
    supervisor.mark_exited(identity)
    reused = ProcessIdentity(
        pid=identity.pid,
        process_start_time_utc="2026-08-11T13:30:00Z",
        process_create_time_ns=identity.process_create_time_ns + 5_000_000_000,
        executable_path=identity.executable_path,
    )
    supervisor.spawn_external(reused)
    assert supervisor.probe(identity).outcome is ProbeOutcome.RUNNING_MISMATCH
    close = supervisor.request_graceful_close(identity)
    assert close.outcome is CloseRequestOutcome.IDENTITY_MISMATCH
    assert supervisor.focus(identity).outcome is FocusOutcome.IDENTITY_MISMATCH
    terminate = supervisor.terminate(identity)
    assert terminate.outcome is TerminateOutcome.IDENTITY_MISMATCH
    # The unrelated instance is untouched by the mismatched requests.
    assert supervisor.probe(reused).outcome is ProbeOutcome.RUNNING_MATCH


def test_reused_pid_within_same_second_yields_mismatch() -> None:
    # Same pid, same whole-second UTC start time, different precise
    # creation token: never a match, never touchable.
    supervisor = FakeProcessSupervisor()
    identity = _launched_identity(supervisor)
    supervisor.mark_exited(identity)
    same_second = ProcessIdentity(
        pid=identity.pid,
        process_start_time_utc=identity.process_start_time_utc,
        process_create_time_ns=identity.process_create_time_ns + 1,
        executable_path=identity.executable_path,
    )
    supervisor.spawn_external(same_second)
    assert supervisor.probe(identity).outcome is ProbeOutcome.RUNNING_MISMATCH
    close = supervisor.request_graceful_close(identity)
    assert close.outcome is CloseRequestOutcome.IDENTITY_MISMATCH
    assert supervisor.focus(identity).outcome is FocusOutcome.IDENTITY_MISMATCH
    terminate = supervisor.terminate(identity)
    assert terminate.outcome is TerminateOutcome.IDENTITY_MISMATCH
    assert supervisor.probe(same_second).outcome is ProbeOutcome.RUNNING_MATCH


def test_wrong_executable_with_matching_pid_and_time_yields_mismatch() -> None:
    supervisor = FakeProcessSupervisor()
    identity = _launched_identity(supervisor)
    wrong_exe = ProcessIdentity(
        pid=identity.pid,
        process_start_time_utc=identity.process_start_time_utc,
        process_create_time_ns=identity.process_create_time_ns,
        executable_path="C:\\Other\\imposter.exe",
    )
    assert supervisor.probe(wrong_exe).outcome is ProbeOutcome.RUNNING_MISMATCH
    close = supervisor.request_graceful_close(wrong_exe)
    assert close.outcome is CloseRequestOutcome.IDENTITY_MISMATCH


def test_executable_comparison_is_case_insensitive() -> None:
    supervisor = FakeProcessSupervisor()
    supervisor.spawn_external(
        ProcessIdentity(
            pid=123,
            process_start_time_utc="2026-08-11T12:00:00Z",
            process_create_time_ns=1_760_000_000_000_000_123,
            executable_path=_EXE.upper(),
        )
    )
    lowered = ProcessIdentity(
        pid=123,
        process_start_time_utc="2026-08-11T12:00:00Z",
        process_create_time_ns=1_760_000_000_000_000_123,
        executable_path=_EXE.lower(),
    )
    assert supervisor.probe(lowered).outcome is ProbeOutcome.RUNNING_MATCH


def test_graceful_close_exits_process_by_default() -> None:
    supervisor = FakeProcessSupervisor()
    identity = _launched_identity(supervisor)
    result = supervisor.request_graceful_close(identity)
    assert result.outcome is CloseRequestOutcome.REQUESTED
    assert supervisor.close_requests == [identity]
    assert supervisor.probe(identity).outcome is ProbeOutcome.NOT_RUNNING


def test_graceful_close_can_leave_process_running() -> None:
    supervisor = FakeProcessSupervisor()
    supervisor.exit_on_close_request = False
    identity = _launched_identity(supervisor)
    result = supervisor.request_graceful_close(identity)
    assert result.outcome is CloseRequestOutcome.REQUESTED
    assert supervisor.probe(identity).outcome is ProbeOutcome.RUNNING_MATCH


def test_graceful_close_failure_keeps_process_running() -> None:
    supervisor = FakeProcessSupervisor()
    supervisor.close_request_fails = True
    identity = _launched_identity(supervisor)
    result = supervisor.request_graceful_close(identity)
    assert result.outcome is CloseRequestOutcome.REQUEST_FAILED
    assert supervisor.probe(identity).outcome is ProbeOutcome.RUNNING_MATCH


def test_graceful_close_of_exited_process() -> None:
    supervisor = FakeProcessSupervisor()
    identity = _launched_identity(supervisor)
    supervisor.mark_exited(identity)
    result = supervisor.request_graceful_close(identity)
    assert result.outcome is CloseRequestOutcome.NOT_RUNNING


def test_focus_success_and_failure() -> None:
    supervisor = FakeProcessSupervisor()
    identity = _launched_identity(supervisor)
    assert supervisor.focus(identity).outcome is FocusOutcome.FOCUSED
    supervisor.focus_fails = True
    assert supervisor.focus(identity).outcome is FocusOutcome.FOCUS_FAILED
    supervisor.mark_exited(identity)
    supervisor.focus_fails = False
    assert supervisor.focus(identity).outcome is FocusOutcome.NOT_RUNNING
    assert supervisor.focus_requests == [identity, identity, identity]


def test_terminate_removes_process_from_registry() -> None:
    supervisor = FakeProcessSupervisor()
    identity = _launched_identity(supervisor)
    assert supervisor.terminate(identity).outcome is TerminateOutcome.TERMINATED
    assert supervisor.terminations == [identity]
    assert supervisor.probe(identity).outcome is ProbeOutcome.NOT_RUNNING
    assert supervisor.terminate(identity).outcome is TerminateOutcome.NOT_RUNNING


def test_terminate_failure_keeps_process_running() -> None:
    supervisor = FakeProcessSupervisor()
    supervisor.terminate_fails = True
    identity = _launched_identity(supervisor)
    result = supervisor.terminate(identity)
    assert result.outcome is TerminateOutcome.TERMINATE_FAILED
    assert supervisor.probe(identity).outcome is ProbeOutcome.RUNNING_MATCH


def test_unavailable_mode_refuses_every_operation() -> None:
    supervisor = FakeProcessSupervisor(
        available=False, unavailable_reason="scripted maintenance"
    )
    availability = supervisor.availability()
    assert availability.available is False
    assert availability.reason == "scripted maintenance"
    identity = ProcessIdentity(
        pid=1,
        process_start_time_utc="2026-08-11T12:00:00Z",
        process_create_time_ns=1_760_000_000_000_000_001,
        executable_path=_EXE,
    )
    assert supervisor.launch(_COMMAND).outcome is LaunchOutcome.ADAPTER_UNAVAILABLE
    assert supervisor.probe(identity).outcome is ProbeOutcome.ADAPTER_UNAVAILABLE
    close = supervisor.request_graceful_close(identity)
    assert close.outcome is CloseRequestOutcome.ADAPTER_UNAVAILABLE
    assert supervisor.focus(identity).outcome is FocusOutcome.ADAPTER_UNAVAILABLE
    terminate = supervisor.terminate(identity)
    assert terminate.outcome is TerminateOutcome.ADAPTER_UNAVAILABLE


def test_unavailable_mode_has_default_reason() -> None:
    supervisor = FakeProcessSupervisor(available=False)
    availability = supervisor.availability()
    assert availability.available is False
    assert availability.reason


@pytest.mark.parametrize("running", [True, False])
def test_observation_from_identity(running: bool) -> None:
    identity = ProcessIdentity(
        pid=4242,
        process_start_time_utc="2026-08-11T12:00:00Z",
        process_create_time_ns=1_760_000_000_000_004_242,
        executable_path=_EXE,
    )
    observation = observation_from_identity(identity, running=running)
    assert observation.pid == 4242
    assert observation.process_start_time_utc == "2026-08-11T12:00:00Z"
    assert observation.process_create_time_ns == 1_760_000_000_000_004_242
    assert observation.executable_path == _EXE
    assert observation.running is running
