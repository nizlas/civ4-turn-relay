"""Guarded launch: interprocess serialization, exact matching, lifecycle.

Two :class:`FakeProcessSupervisor` instances sharing one
:class:`FakeMachine` model two Relay clients (profiles) on one Windows
computer with one Civilization installation.
"""

from __future__ import annotations

import pytest

from civ4_turn_relay.process import (
    CivLaunchCommand,
    FakeMachine,
    FakeProcessSupervisor,
    GuardCleanupOutcome,
    GuardedLaunchOutcome,
    GuardedLaunchResult,
    LaunchResult,
    MachineScanOutcome,
    MachineScanResult,
    ProcessIdentity,
    ProcessScanEntry,
    classify_scan_entries,
    execute_guarded_launch,
    launch_guard_name,
    normalize_windows_executable,
    windows_executable_basename,
)

_EXE = "C:\\Games\\Civ4\\Civ4BeyondSword.exe"
_OTHER_EXE = "C:\\Games\\OtherGame\\OtherGame.exe"
_COMMAND = CivLaunchCommand(argv=(_EXE, "mod=Mods\\AdvCiv"), working_directory=None)


def _shared_pair() -> tuple[FakeMachine, FakeProcessSupervisor, FakeProcessSupervisor]:
    machine = FakeMachine()
    supervisor_a = FakeProcessSupervisor(machine=machine)
    supervisor_b = FakeProcessSupervisor(machine=machine)
    return machine, supervisor_a, supervisor_b


def _foreign_identity(
    *, pid: int = 9001, executable_path: str = _EXE
) -> ProcessIdentity:
    return ProcessIdentity(
        pid=pid,
        process_start_time_utc="2026-08-11T11:00:00Z",
        process_create_time_ns=1_755_000_000_000_000_000 + pid,
        executable_path=executable_path,
    )


# --- two clients, one machine ------------------------------------------------


def test_second_client_defers_while_the_first_civ_runs() -> None:
    machine, supervisor_a, supervisor_b = _shared_pair()
    first = supervisor_a.guarded_launch(_COMMAND)
    assert first.outcome is GuardedLaunchOutcome.LAUNCHED
    assert first.identity is not None

    second = supervisor_b.guarded_launch(_COMMAND)
    assert second.outcome is GuardedLaunchOutcome.EXISTING_CIV_DETECTED
    assert second.identity is None
    assert second.existing_pid == first.identity.pid
    assert supervisor_b.launched == []
    # Exactly one process exists on the machine.
    assert len(machine.scan_entries()) == 1


def test_second_client_launches_after_the_first_civ_exits() -> None:
    machine, supervisor_a, supervisor_b = _shared_pair()
    first = supervisor_a.guarded_launch(_COMMAND)
    assert first.identity is not None
    assert supervisor_b.guarded_launch(_COMMAND).deferred

    supervisor_a.mark_exited(first.identity)
    retried = supervisor_b.guarded_launch(_COMMAND)
    assert retried.outcome is GuardedLaunchOutcome.LAUNCHED
    assert retried.identity is not None
    assert retried.identity.pid != first.identity.pid
    assert len(supervisor_b.launched) == 1


def test_serialized_attempts_spawn_at_most_once() -> None:
    # Interprocess serialization means the attempts run one after the other
    # under the guard; the later attempt must observe the newly started
    # process and defer instead of spawning a second one.
    machine, supervisor_a, supervisor_b = _shared_pair()
    outcomes = [
        supervisor_a.guarded_launch(_COMMAND).outcome,
        supervisor_b.guarded_launch(_COMMAND).outcome,
    ]
    assert outcomes == [
        GuardedLaunchOutcome.LAUNCHED,
        GuardedLaunchOutcome.EXISTING_CIV_DETECTED,
    ]
    assert len(machine.scan_entries()) == 1
    assert machine.guard_held is False
    assert len(machine.guard_acquisitions) == 2
    assert len(machine.guard_releases) == 2


def test_concurrent_holder_makes_the_guard_busy() -> None:
    machine, _supervisor_a, supervisor_b = _shared_pair()
    machine.externally_held = True
    result = supervisor_b.guarded_launch(_COMMAND)
    assert result.outcome is GuardedLaunchOutcome.GUARD_BUSY
    assert supervisor_b.launched == []


# --- exact executable matching ----------------------------------------------


def test_case_only_path_difference_still_blocks() -> None:
    _machine, supervisor_a, supervisor_b = _shared_pair()
    supervisor_a.spawn_external(_foreign_identity(executable_path=_EXE.upper()))
    result = supervisor_b.guarded_launch(_COMMAND)
    assert result.outcome is GuardedLaunchOutcome.EXISTING_CIV_DETECTED


def test_a_different_executable_never_blocks_or_gets_touched() -> None:
    machine, supervisor_a, supervisor_b = _shared_pair()
    foreign = _foreign_identity(executable_path=_OTHER_EXE)
    supervisor_a.spawn_external(foreign)
    result = supervisor_b.guarded_launch(_COMMAND)
    assert result.outcome is GuardedLaunchOutcome.LAUNCHED
    # The unrelated process is still running and untouched.
    assert machine.is_running(foreign)
    assert supervisor_b.close_requests == []
    assert supervisor_b.terminations == []


def test_inaccessible_unrelated_process_does_not_block() -> None:
    machine, _supervisor_a, supervisor_b = _shared_pair()
    machine.add_scan_entry(ProcessScanEntry(pid=4, executable_path=None, name="System"))
    machine.add_scan_entry(ProcessScanEntry(pid=5, executable_path=None, name=None))
    result = supervisor_b.guarded_launch(_COMMAND)
    assert result.outcome is GuardedLaunchOutcome.LAUNCHED


def test_unverifiable_likely_civ_candidate_fails_closed() -> None:
    machine, _supervisor_a, supervisor_b = _shared_pair()
    machine.add_scan_entry(
        ProcessScanEntry(pid=321, executable_path=None, name="Civ4BeyondSword.exe")
    )
    result = supervisor_b.guarded_launch(_COMMAND)
    assert result.outcome is GuardedLaunchOutcome.SCAN_INDETERMINATE
    assert result.existing_pid == 321
    assert supervisor_b.launched == []
    assert machine.guard_held is False


def test_classification_prefers_exact_match_over_indeterminate() -> None:
    entries = (
        ProcessScanEntry(pid=1, executable_path=None, name="civ4beyondsword.exe"),
        ProcessScanEntry(pid=2, executable_path=_EXE, name=None),
    )
    result = classify_scan_entries(entries, executable_path=_EXE)
    assert result.outcome is MachineScanOutcome.EXACT_MATCH
    assert result.pid == 2


def test_classification_matches_short_name_case_insensitively() -> None:
    entries = (
        ProcessScanEntry(pid=7, executable_path=None, name="CIV4BEYONDSWORD.EXE"),
    )
    result = classify_scan_entries(entries, executable_path=_EXE)
    assert result.outcome is MachineScanOutcome.INDETERMINATE


# --- guard lifecycle ----------------------------------------------------------


def test_guard_released_after_success_blocker_and_failure() -> None:
    machine, supervisor_a, supervisor_b = _shared_pair()
    assert supervisor_a.guarded_launch(_COMMAND).outcome is (
        GuardedLaunchOutcome.LAUNCHED
    )
    assert machine.guard_held is False

    assert supervisor_b.guarded_launch(_COMMAND).outcome is (
        GuardedLaunchOutcome.EXISTING_CIV_DETECTED
    )
    assert machine.guard_held is False

    clean = FakeMachine()
    failing = FakeProcessSupervisor(machine=clean)
    failing.fail_spawn = True
    assert failing.guarded_launch(_COMMAND).outcome is (
        GuardedLaunchOutcome.SPAWN_FAILURE
    )
    assert clean.guard_held is False


def test_guard_released_when_the_launch_raises() -> None:
    machine = FakeMachine()

    def exploding_launch(_command: CivLaunchCommand) -> LaunchResult:
        raise RuntimeError("spawn exploded")

    def clear_scan(_executable: str) -> MachineScanResult:
        return MachineScanResult(outcome=MachineScanOutcome.NO_MATCH)

    with pytest.raises(RuntimeError, match="spawn exploded"):
        execute_guarded_launch(
            guard=machine,
            scan=clear_scan,
            launch=exploding_launch,
            command=_COMMAND,
        )
    assert machine.guard_held is False
    assert len(machine.guard_releases) == 1


def test_guard_released_when_the_scan_raises() -> None:
    machine = FakeMachine()

    def exploding_scan(_executable: str) -> MachineScanResult:
        raise RuntimeError("scan exploded")

    def never_launch(_command: CivLaunchCommand) -> LaunchResult:
        raise AssertionError("must not launch")

    with pytest.raises(RuntimeError, match="scan exploded"):
        execute_guarded_launch(
            guard=machine,
            scan=exploding_scan,
            launch=never_launch,
            command=_COMMAND,
        )
    assert machine.guard_held is False


def test_abandoned_owner_recovery_scans_before_launching() -> None:
    machine, supervisor_a, supervisor_b = _shared_pair()
    first = supervisor_a.guarded_launch(_COMMAND)
    assert first.identity is not None
    machine.abandon_guard()

    result = supervisor_b.guarded_launch(_COMMAND)
    assert result.outcome is GuardedLaunchOutcome.EXISTING_CIV_DETECTED
    assert result.recovered_abandoned_guard is True
    assert supervisor_b.launched == []
    assert machine.guard_held is False


def test_abandoned_owner_recovery_launches_when_the_scan_is_clear() -> None:
    machine = FakeMachine()
    supervisor = FakeProcessSupervisor(machine=machine)
    machine.abandon_guard()
    result = supervisor.guarded_launch(_COMMAND)
    assert result.outcome is GuardedLaunchOutcome.LAUNCHED
    assert result.recovered_abandoned_guard is True
    assert machine.guard_held is False


def test_unavailable_guard_is_a_typed_outcome() -> None:
    machine = FakeMachine()
    supervisor = FakeProcessSupervisor(machine=machine)
    machine.guard_unavailable = True
    result = supervisor.guarded_launch(_COMMAND)
    assert result.outcome is GuardedLaunchOutcome.GUARD_UNAVAILABLE
    assert supervisor.launched == []


def test_result_invariant_identity_only_on_launched() -> None:
    with pytest.raises(Exception, match="identity"):
        GuardedLaunchResult(
            outcome=GuardedLaunchOutcome.EXISTING_CIV_DETECTED,
            identity=_foreign_identity(),
        )
    with pytest.raises(Exception, match="identity"):
        GuardedLaunchResult(outcome=GuardedLaunchOutcome.LAUNCHED)


# --- cleanup must not forget a verified launch --------------------------------


def test_launched_result_survives_release_mutex_cleanup_failure() -> None:
    machine = FakeMachine()
    machine.release_failure_operation = "ReleaseMutex"
    supervisor = FakeProcessSupervisor(machine=machine)
    result = supervisor.guarded_launch(_COMMAND)
    assert result.outcome is GuardedLaunchOutcome.LAUNCHED
    assert result.identity is not None
    assert result.cleanup_outcome is GuardCleanupOutcome.RELEASE_FAILED
    assert result.cleanup_failed
    assert "ReleaseMutex" in result.cleanup_message
    assert machine.guard_held is False
    assert machine.guard_releases == ["released"]
    assert len(supervisor.launched) == 1

    second = supervisor.guarded_launch(_COMMAND)
    assert second.outcome is GuardedLaunchOutcome.EXISTING_CIV_DETECTED
    assert len(supervisor.launched) == 1


def test_launched_result_survives_close_handle_cleanup_failure() -> None:
    machine = FakeMachine()
    machine.release_failure_operation = "CloseHandle"
    supervisor = FakeProcessSupervisor(machine=machine)
    result = supervisor.guarded_launch(_COMMAND)
    assert result.outcome is GuardedLaunchOutcome.LAUNCHED
    assert result.identity is not None
    assert result.cleanup_outcome is GuardCleanupOutcome.CLOSE_FAILED
    assert result.cleanup_failed
    assert "CloseHandle" in result.cleanup_message
    assert machine.guard_held is False
    assert machine.guard_releases == ["released"]

    second = supervisor.guarded_launch(_COMMAND)
    assert second.outcome is GuardedLaunchOutcome.EXISTING_CIV_DETECTED
    assert len(supervisor.launched) == 1


def test_deferred_result_keeps_primary_outcome_when_cleanup_fails() -> None:
    machine = FakeMachine()
    machine.release_failure_operation = "CloseHandle"
    supervisor = FakeProcessSupervisor(machine=machine)
    machine.add_scan_entry(
        ProcessScanEntry(pid=321, executable_path=None, name="Civ4BeyondSword.exe")
    )
    result = supervisor.guarded_launch(_COMMAND)
    assert result.outcome is GuardedLaunchOutcome.SCAN_INDETERMINATE
    assert result.deferred
    assert result.cleanup_failed
    assert result.cleanup_outcome is GuardCleanupOutcome.CLOSE_FAILED
    assert supervisor.launched == []
    assert machine.guard_held is False
    assert machine.guard_releases == ["released"]


def test_primary_launch_exception_is_not_masked_by_cleanup_failure() -> None:
    machine = FakeMachine()
    machine.release_failure_operation = "ReleaseMutex"

    def exploding_launch(_command: CivLaunchCommand) -> LaunchResult:
        raise RuntimeError("spawn exploded")

    def clear_scan(_executable: str) -> MachineScanResult:
        return MachineScanResult(outcome=MachineScanOutcome.NO_MATCH)

    with pytest.raises(RuntimeError, match="spawn exploded") as caught:
        execute_guarded_launch(
            guard=machine,
            scan=clear_scan,
            launch=exploding_launch,
            command=_COMMAND,
        )
    notes = "".join(caught.value.__notes__)
    assert "cleanup also failed" in notes
    assert caught.value.__cause__ is not None
    assert "ReleaseMutex" in str(caught.value.__cause__)
    assert machine.guard_held is False
    assert machine.guard_releases == ["released"]


def test_guard_release_is_exactly_once_on_the_same_thread() -> None:
    machine = FakeMachine()
    supervisor = FakeProcessSupervisor(machine=machine)
    result = supervisor.guarded_launch(_COMMAND)
    assert result.cleanup_outcome is GuardCleanupOutcome.RELEASED
    assert not result.cleanup_failed
    assert machine.guard_releases == ["released"]
    assert machine.guard_acquire_threads == machine.guard_release_threads
    assert len(machine.guard_release_threads) == 1

    assert result.identity is not None
    supervisor.mark_exited(result.identity)
    machine.release_failure_operation = "ReleaseMutex"
    failed = supervisor.guarded_launch(_COMMAND)
    assert failed.outcome is GuardedLaunchOutcome.LAUNCHED
    assert failed.cleanup_failed
    assert len(machine.guard_releases) == 2
    assert machine.guard_acquire_threads == machine.guard_release_threads


def test_fake_machine_release_is_idempotent_after_cleanup_failure() -> None:
    machine = FakeMachine()
    machine.release_failure_operation = "CloseHandle"
    acquired = machine.acquire(_EXE)
    with pytest.raises(OSError, match="CloseHandle failed"):
        machine.release(acquired)
    machine.release(acquired)
    assert machine.guard_releases == ["released"]
    assert machine.guard_held is False


# --- host-independent Windows path semantics ----------------------------------


def test_windows_basename_from_backslash_path() -> None:
    assert (
        windows_executable_basename(r"C:\Games\Civ4\Civ4BeyondSword.exe")
        == "civ4beyondsword.exe"
    )


def test_windows_path_case_insensitive_equality() -> None:
    assert normalize_windows_executable(_EXE) == normalize_windows_executable(
        _EXE.upper()
    )


def test_slash_and_backslash_spellings_are_equivalent() -> None:
    slashed = "C:/Games/Civ4/Civ4BeyondSword.exe"
    assert normalize_windows_executable(_EXE) == normalize_windows_executable(slashed)
    assert windows_executable_basename(slashed) == "civ4beyondsword.exe"


def test_dot_segment_normalization() -> None:
    dotted = r"C:\Games\Civ4\.\Civ4BeyondSword.exe"
    parent = r"C:\Games\Civ4\foo\..\Civ4BeyondSword.exe"
    assert normalize_windows_executable(dotted) == normalize_windows_executable(_EXE)
    assert normalize_windows_executable(parent) == normalize_windows_executable(_EXE)


def test_inaccessible_civ_short_name_is_indeterminate_on_every_host() -> None:
    entries = (
        ProcessScanEntry(pid=321, executable_path=None, name="Civ4BeyondSword.exe"),
    )
    result = classify_scan_entries(entries, executable_path=_EXE)
    assert result.outcome is MachineScanOutcome.INDETERMINATE
    assert result.pid == 321


def test_equivalent_executable_spellings_share_mutex_name() -> None:
    name = launch_guard_name(_EXE)
    assert launch_guard_name(_EXE.upper()) == name
    assert launch_guard_name("C:/Games/Civ4/Civ4BeyondSword.exe") == name
    assert launch_guard_name(r"C:\Games\Civ4\.\Civ4BeyondSword.exe") == name
    assert launch_guard_name(r"C:\Games\Civ4\foo\..\Civ4BeyondSword.exe") == name
    assert launch_guard_name(r"C:\Other\Civ4BeyondSword.exe") != name


def test_slash_spelling_of_running_exe_still_blocks() -> None:
    _machine, supervisor_a, supervisor_b = _shared_pair()
    supervisor_a.spawn_external(
        _foreign_identity(executable_path="C:/Games/Civ4/Civ4BeyondSword.exe")
    )
    result = supervisor_b.guarded_launch(_COMMAND)
    assert result.outcome is GuardedLaunchOutcome.EXISTING_CIV_DETECTED
    assert supervisor_b.launched == []
