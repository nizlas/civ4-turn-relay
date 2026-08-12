"""Deterministic in-memory fake implementing the process supervisor port.

No threads, no sleeping: every behavior is scripted through public flags so
tests exercise the exact outcome vocabulary of the port.

:class:`FakeMachine` models one Windows computer: the machine-wide process
table, the interprocess launch guard, and pid/creation-token allocation.
Several :class:`FakeProcessSupervisor` instances sharing one machine behave
like several Relay processes (profiles) on one PC: each sees the others'
Civ processes in scans and contends for the same launch guard.
"""

from __future__ import annotations

import os

from civ4_turn_relay.process.guard import (
    GuardAcquireOutcome,
    GuardAcquisition,
    GuardedLaunchOutcome,
    GuardedLaunchResult,
    MachineScanResult,
    ProcessScanEntry,
    classify_scan_entries,
    execute_guarded_launch,
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

_DEFAULT_UNAVAILABLE_REASON = "fake_unavailable: scripted as unavailable"


def _normalize(path: str) -> str:
    return os.path.normpath(path).casefold()


def _key(identity: ProcessIdentity) -> tuple[int, int, str]:
    """Exact identity key: pid + precise creation token + normalized exe.

    The second-resolution UTC timestamp is deliberately excluded so a reused
    pid within the same wall-clock second is still a mismatch.
    """
    return (
        identity.pid,
        identity.process_create_time_ns,
        _normalize(identity.executable_path),
    )


class FakeMachine:
    """One simulated computer: process table, launch guard, id allocation.

    Also implements the :class:`~civ4_turn_relay.process.guard.LaunchGuard`
    port so it can be handed directly to the guarded launch algorithm.
    Guard scripting: set ``guard_unavailable`` to refuse acquisition, set
    ``externally_held`` to simulate another holder (``GUARD_BUSY``), and
    call :meth:`abandon_guard` to simulate a crashed prior owner whose
    mutex the OS reports as abandoned.
    """

    def __init__(
        self,
        *,
        next_pid: int = 5000,
        start_time_ns_base: int = 1_760_000_000_000_000_000,
    ) -> None:
        self.next_pid = next_pid
        self.next_start_time_ns = start_time_ns_base
        self._processes: dict[tuple[int, int, str], ProcessIdentity] = {}
        self._extra_scan_entries: list[ProcessScanEntry] = []
        self.guard_unavailable = False
        self.externally_held = False
        self._abandoned = False
        self._held_handle: object | None = None
        self.guard_acquisitions: list[str] = []
        self.guard_releases: list[str] = []

    # --- process table -----------------------------------------------------

    def register(self, identity: ProcessIdentity) -> None:
        """Register a running process on this machine."""
        self._processes[_key(identity)] = identity

    def unregister(self, identity: ProcessIdentity) -> None:
        """Mark a previously running process as exited."""
        self._processes.pop(_key(identity), None)

    def is_running(self, identity: ProcessIdentity) -> bool:
        return _key(identity) in self._processes

    def pid_running(self, pid: int) -> bool:
        return any(key[0] == pid for key in self._processes)

    def add_scan_entry(self, entry: ProcessScanEntry) -> None:
        """Add a scan-only entry (e.g. an inaccessible foreign process)."""
        self._extra_scan_entries.append(entry)

    def scan_entries(self) -> tuple[ProcessScanEntry, ...]:
        """Machine scan: every registered process plus scripted entries."""
        entries = [
            ProcessScanEntry(
                pid=identity.pid,
                executable_path=identity.executable_path,
                name=os.path.basename(identity.executable_path),
            )
            for identity in self._processes.values()
        ]
        entries.extend(self._extra_scan_entries)
        return tuple(entries)

    # --- LaunchGuard port ----------------------------------------------------

    def abandon_guard(self) -> None:
        """Simulate a crashed prior owner: the OS hands the next waiter an
        abandoned mutex instead of leaving a stale lock behind."""
        self._abandoned = True

    def acquire(self, executable_path: str) -> GuardAcquisition:
        self.guard_acquisitions.append(executable_path)
        if self.guard_unavailable:
            return GuardAcquisition(
                outcome=GuardAcquireOutcome.UNAVAILABLE,
                message="scripted guard unavailable",
            )
        if self.externally_held or self._held_handle is not None:
            return GuardAcquisition(
                outcome=GuardAcquireOutcome.BUSY,
                message="the launch guard is held by another Relay instance",
            )
        handle = object()
        self._held_handle = handle
        if self._abandoned:
            self._abandoned = False
            return GuardAcquisition(
                outcome=GuardAcquireOutcome.ACQUIRED_ABANDONED,
                handle=handle,
                message="recovered a guard abandoned by a crashed Relay",
            )
        return GuardAcquisition(outcome=GuardAcquireOutcome.ACQUIRED, handle=handle)

    def release(self, acquisition: GuardAcquisition) -> None:
        if not acquisition.held:
            return
        self.guard_releases.append("released")
        if acquisition.handle is self._held_handle:
            self._held_handle = None

    @property
    def guard_held(self) -> bool:
        return self._held_handle is not None


class FakeProcessSupervisor:
    """Scriptable :class:`~civ4_turn_relay.process.port.ProcessSupervisor`.

    Pass a shared :class:`FakeMachine` to model several Relay clients on one
    computer; by default each supervisor gets its own private machine.
    """

    def __init__(
        self,
        *,
        available: bool = True,
        unavailable_reason: str = "",
        start_time_utc: str = "2026-08-11T12:00:00Z",
        next_pid: int = 5000,
        start_time_ns_base: int = 1_760_000_000_000_000_000,
        machine: FakeMachine | None = None,
    ) -> None:
        self._available = available
        self._unavailable_reason = unavailable_reason or _DEFAULT_UNAVAILABLE_REASON
        self._start_time_utc = start_time_utc
        self.machine = (
            machine
            if machine is not None
            else FakeMachine(next_pid=next_pid, start_time_ns_base=start_time_ns_base)
        )
        self.fail_spawn = False
        self.exit_immediately = False
        self.identity_unverified = False
        self.close_request_fails = False
        self.exit_on_close_request = True
        self.focus_fails = False
        self.terminate_fails = False
        self.launched: list[CivLaunchCommand] = []
        self.close_requests: list[ProcessIdentity] = []
        self.focus_requests: list[ProcessIdentity] = []
        self.terminations: list[ProcessIdentity] = []

    def availability(self) -> SupervisorAvailability:
        if not self._available:
            return SupervisorAvailability(
                available=False, reason=self._unavailable_reason
            )
        return SupervisorAvailability(available=True)

    def spawn_external(self, identity: ProcessIdentity) -> None:
        """Register a running process this supervisor did not launch."""
        self.machine.register(identity)

    def mark_exited(self, identity: ProcessIdentity) -> None:
        """Mark a previously running process as exited."""
        self.machine.unregister(identity)

    def launch(self, command: CivLaunchCommand) -> LaunchResult:
        self.launched.append(command)
        if not self._available:
            return LaunchResult(
                outcome=LaunchOutcome.ADAPTER_UNAVAILABLE,
                message=self._unavailable_reason,
            )
        if self.fail_spawn:
            return LaunchResult(
                outcome=LaunchOutcome.SPAWN_FAILURE, message="scripted spawn failure"
            )
        if self.exit_immediately:
            return LaunchResult(
                outcome=LaunchOutcome.EXITED_IMMEDIATELY,
                message="scripted immediate exit",
            )
        if self.identity_unverified:
            return LaunchResult(
                outcome=LaunchOutcome.IDENTITY_UNVERIFIED,
                message="scripted identity verification failure",
            )
        identity = ProcessIdentity(
            pid=self.machine.next_pid,
            process_start_time_utc=self._start_time_utc,
            process_create_time_ns=self.machine.next_start_time_ns,
            executable_path=command.argv[0],
        )
        self.machine.next_pid += 1
        self.machine.next_start_time_ns += 1
        self.machine.register(identity)
        return LaunchResult(outcome=LaunchOutcome.LAUNCHED, identity=identity)

    def guarded_launch(self, command: CivLaunchCommand) -> GuardedLaunchResult:
        """Guarded launch against this supervisor's (possibly shared) machine."""
        if not self._available:
            return GuardedLaunchResult(
                outcome=GuardedLaunchOutcome.ADAPTER_UNAVAILABLE,
                message=self._unavailable_reason,
            )
        return execute_guarded_launch(
            guard=self.machine,
            scan=self._scan,
            launch=self.launch,
            command=command,
        )

    def _scan(self, executable_path: str) -> MachineScanResult:
        return classify_scan_entries(
            self.machine.scan_entries(), executable_path=executable_path
        )

    def probe(self, identity: ProcessIdentity) -> ProbeResult:
        if not self._available:
            return ProbeResult(
                outcome=ProbeOutcome.ADAPTER_UNAVAILABLE,
                message=self._unavailable_reason,
            )
        if self.machine.is_running(identity):
            return ProbeResult(outcome=ProbeOutcome.RUNNING_MATCH)
        if self.machine.pid_running(identity.pid):
            return ProbeResult(
                outcome=ProbeOutcome.RUNNING_MISMATCH,
                message="the pid is running with a different identity",
            )
        return ProbeResult(outcome=ProbeOutcome.NOT_RUNNING)

    def request_graceful_close(self, identity: ProcessIdentity) -> CloseRequestResult:
        self.close_requests.append(identity)
        if not self._available:
            return CloseRequestResult(
                outcome=CloseRequestOutcome.ADAPTER_UNAVAILABLE,
                message=self._unavailable_reason,
            )
        probe = self.probe(identity)
        if probe.outcome is ProbeOutcome.NOT_RUNNING:
            return CloseRequestResult(outcome=CloseRequestOutcome.NOT_RUNNING)
        if probe.outcome is ProbeOutcome.RUNNING_MISMATCH:
            return CloseRequestResult(
                outcome=CloseRequestOutcome.IDENTITY_MISMATCH, message=probe.message
            )
        if self.close_request_fails:
            return CloseRequestResult(
                outcome=CloseRequestOutcome.REQUEST_FAILED,
                message="scripted close request failure",
            )
        if self.exit_on_close_request:
            self.machine.unregister(identity)
        return CloseRequestResult(outcome=CloseRequestOutcome.REQUESTED)

    def focus(self, identity: ProcessIdentity) -> FocusResult:
        self.focus_requests.append(identity)
        if not self._available:
            return FocusResult(
                outcome=FocusOutcome.ADAPTER_UNAVAILABLE,
                message=self._unavailable_reason,
            )
        probe = self.probe(identity)
        if probe.outcome is ProbeOutcome.NOT_RUNNING:
            return FocusResult(outcome=FocusOutcome.NOT_RUNNING)
        if probe.outcome is ProbeOutcome.RUNNING_MISMATCH:
            return FocusResult(
                outcome=FocusOutcome.IDENTITY_MISMATCH, message=probe.message
            )
        if self.focus_fails:
            return FocusResult(
                outcome=FocusOutcome.FOCUS_FAILED, message="scripted focus failure"
            )
        return FocusResult(outcome=FocusOutcome.FOCUSED)

    def terminate(self, identity: ProcessIdentity) -> TerminateResult:
        self.terminations.append(identity)
        if not self._available:
            return TerminateResult(
                outcome=TerminateOutcome.ADAPTER_UNAVAILABLE,
                message=self._unavailable_reason,
            )
        probe = self.probe(identity)
        if probe.outcome is ProbeOutcome.NOT_RUNNING:
            return TerminateResult(outcome=TerminateOutcome.NOT_RUNNING)
        if probe.outcome is ProbeOutcome.RUNNING_MISMATCH:
            return TerminateResult(
                outcome=TerminateOutcome.IDENTITY_MISMATCH, message=probe.message
            )
        if self.terminate_fails:
            return TerminateResult(
                outcome=TerminateOutcome.TERMINATE_FAILED,
                message="scripted terminate failure",
            )
        self.machine.unregister(identity)
        return TerminateResult(outcome=TerminateOutcome.TERMINATED)
