"""Deterministic in-memory fake implementing the process supervisor port.

No threads, no sleeping: every behavior is scripted through public flags so
tests exercise the exact outcome vocabulary of the port.
"""

from __future__ import annotations

import os

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


class FakeProcessSupervisor:
    """Scriptable :class:`~civ4_turn_relay.process.port.ProcessSupervisor`."""

    def __init__(
        self,
        *,
        available: bool = True,
        unavailable_reason: str = "",
        start_time_utc: str = "2026-08-11T12:00:00Z",
        next_pid: int = 5000,
        start_time_ns_base: int = 1_760_000_000_000_000_000,
    ) -> None:
        self._available = available
        self._unavailable_reason = unavailable_reason or _DEFAULT_UNAVAILABLE_REASON
        self._start_time_utc = start_time_utc
        self._next_pid = next_pid
        self._next_start_time_ns = start_time_ns_base
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
        self._running: set[tuple[int, int, str]] = set()

    def availability(self) -> SupervisorAvailability:
        if not self._available:
            return SupervisorAvailability(
                available=False, reason=self._unavailable_reason
            )
        return SupervisorAvailability(available=True)

    def spawn_external(self, identity: ProcessIdentity) -> None:
        """Register a running process this supervisor did not launch."""
        self._running.add(_key(identity))

    def mark_exited(self, identity: ProcessIdentity) -> None:
        """Mark a previously running process as exited."""
        self._running.discard(_key(identity))

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
            pid=self._next_pid,
            process_start_time_utc=self._start_time_utc,
            process_create_time_ns=self._next_start_time_ns,
            executable_path=command.argv[0],
        )
        self._next_pid += 1
        self._next_start_time_ns += 1
        self._running.add(_key(identity))
        return LaunchResult(outcome=LaunchOutcome.LAUNCHED, identity=identity)

    def probe(self, identity: ProcessIdentity) -> ProbeResult:
        if not self._available:
            return ProbeResult(
                outcome=ProbeOutcome.ADAPTER_UNAVAILABLE,
                message=self._unavailable_reason,
            )
        if _key(identity) in self._running:
            return ProbeResult(outcome=ProbeOutcome.RUNNING_MATCH)
        if any(pid == identity.pid for pid, _, _ in self._running):
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
            self._running.discard(_key(identity))
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
        self._running.discard(_key(identity))
        return TerminateResult(outcome=TerminateOutcome.TERMINATED)
