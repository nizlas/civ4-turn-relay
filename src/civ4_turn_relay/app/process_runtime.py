"""Per-match Civ process runtime coordination (P7 part B).

The coordinator drives real launches and closes through the process
supervisor port while never advancing remote protocol state; only the
existing commit path does that. Identity verification always combines a
fresh ``supervisor.probe`` with durable record comparison — a running PID
alone is never treated as evidence.

Restart recovery: all durable process state lives in the match records
(``launch_attempt``, ``process_association``, ``pending_post_commit_close``).
The graceful-close deadline epoch is deliberately not durable (timers are
not authoritative): when a persisted close request is found after a Relay
restart and the exact entitled process still runs, the 15-second deadline
is re-armed once from the current clock.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum, unique

from civ4_turn_relay.local.clock import Clock
from civ4_turn_relay.local.records import PostCommitCloseRecord
from civ4_turn_relay.process import (
    CloseRequestOutcome,
    CloseRequestResult,
    FocusOutcome,
    FocusResult,
    GuardedLaunchOutcome,
    GuardedLaunchResult,
    LaunchPlan,
    LaunchPlanOutcome,
    ProbeOutcome,
    ProbeResult,
    ProcessIdentity,
    ProcessSupervisor,
    SupervisorAvailability,
    TerminateResult,
)

GRACEFUL_CLOSE_DEADLINE_SECONDS: float = 15.0


@unique
class ProcessStatus(Enum):
    """Typed per-match process status for UI display."""

    UNAVAILABLE = "unavailable"
    READY = "ready"
    STARTING = "starting"
    RUNNING = "running"
    WAITING_FOR_EXISTING_CIV = "waiting_for_existing_civ"
    CLOSE_REQUESTED = "close_requested"
    CLOSE_DEADLINE_ELAPSED = "close_deadline_elapsed"
    SAFELY_CLOSED = "safely_closed"
    FORCE_CLOSE_ELIGIBLE = "force_close_eligible"
    LAUNCH_FAILED = "launch_failed"


@dataclass(frozen=True, slots=True)
class ProcessStatusSnapshot:
    """Immutable, secret-free process status snapshot for one match."""

    status: ProcessStatus
    message: str = ""
    identity: ProcessIdentity | None = None
    launch_blocked_reason: str | None = None
    force_close_allowed: bool = False
    close_deadline_remaining_seconds: float | None = None


def _identity_key(identity: ProcessIdentity) -> tuple[int, int, str]:
    """Exact comparable identity: pid, precise creation token, normalized exe.

    The second-resolution UTC timestamp is deliberately excluded: a reused
    pid within the same wall-clock second must still compare as different.
    """
    return (
        identity.pid,
        identity.process_create_time_ns,
        os.path.normpath(identity.executable_path).casefold(),
    )


def identity_from_close_record(record: PostCommitCloseRecord) -> ProcessIdentity:
    """Build the entitled process identity from a durable close record."""
    return ProcessIdentity(
        pid=record.pid,
        process_start_time_utc=record.process_start_time_utc,
        process_create_time_ns=record.process_create_time_ns,
        executable_path=record.executable_path,
    )


def identity_matches_close_record(
    identity: ProcessIdentity, record: PostCommitCloseRecord
) -> bool:
    """True only for an exact match against the durable entitlement."""
    return _identity_key(identity) == _identity_key(identity_from_close_record(record))


def close_payload_matches_record(
    payload: Mapping[str, object], record: PostCommitCloseRecord
) -> bool:
    """True only when a close-intent payload matches the durable entitlement."""
    return (
        payload.get("pid") == record.pid
        and payload.get("process_start_time_utc") == record.process_start_time_utc
        and payload.get("process_create_time_ns") == record.process_create_time_ns
        and payload.get("executable_path") == record.executable_path
        and payload.get("operation_id") == record.operation_id
        and payload.get("sha256") == record.sha256
        and payload.get("source_protocol_sequence") == record.source_protocol_sequence
    )


class ProcessCoordinator:
    """Per-match runtime state and guarded operations over the supervisor.

    Session state (in-flight launch guard, close deadline, force-close
    attempt flag, remembered identity mismatches) lives here; durable state
    stays in the match records. Every operation that could touch a process
    re-verifies the exact identity through :meth:`probe` first and, for
    force termination, against the durable post-commit entitlement supplied
    by the caller.
    """

    def __init__(
        self,
        *,
        supervisor: ProcessSupervisor,
        clock: Clock,
        now_utc_fn: Callable[[], str],
        civ4_executable: str | None,
    ) -> None:
        self._supervisor = supervisor
        self._clock = clock
        self._now_utc_fn = now_utc_fn
        self._civ4_executable = civ4_executable
        self._launch_in_flight = False
        self._session_identity: ProcessIdentity | None = None
        self._session_running = False
        self._launch_failure_message: str | None = None
        self._launch_blocked_reason: str | None = None
        self._launch_deferred_message: str | None = None
        self._mismatched_keys: set[tuple[int, int, str]] = set()
        self._mismatch_message: str | None = None
        self._close_identity: ProcessIdentity | None = None
        self._close_operation_id: str | None = None
        self._close_deadline: float | None = None
        self._force_close_attempted = False
        self._safely_closed = False

    @property
    def civ4_executable(self) -> str | None:
        return self._civ4_executable

    @property
    def session_identity(self) -> ProcessIdentity | None:
        return self._session_identity

    @property
    def close_identity(self) -> ProcessIdentity | None:
        return self._close_identity

    @property
    def safely_closed(self) -> bool:
        return self._safely_closed

    def is_mismatched(self, identity: ProcessIdentity) -> bool:
        """True when a probe previously proved this identity was reused."""
        return _identity_key(identity) in self._mismatched_keys

    def availability(self) -> SupervisorAvailability:
        return self._supervisor.availability()

    def probe(self, identity: ProcessIdentity) -> ProbeResult:
        """Probe one exact identity, recording mismatch and running facts."""
        result = self._supervisor.probe(identity)
        if result.outcome is ProbeOutcome.RUNNING_MISMATCH:
            self._mismatched_keys.add(_identity_key(identity))
            self._mismatch_message = (
                result.message or "a different process reuses this pid"
            )
        if result.outcome is ProbeOutcome.RUNNING_MATCH:
            self._session_identity = identity
            self._session_running = True
        elif result.outcome in {
            ProbeOutcome.NOT_RUNNING,
            ProbeOutcome.RUNNING_MISMATCH,
        }:
            if self._session_identity is not None and _identity_key(
                self._session_identity
            ) == _identity_key(identity):
                self._session_running = False
        return result

    def attempt_launch(self, plan: LaunchPlan) -> GuardedLaunchResult | None:
        """Run the guarded launch for a READY plan unless refused locally.

        ``None`` means the launch was refused locally without spawning:
        either the plan was not READY (the refusal reason is recorded for
        the status snapshot) or a launch is already in flight / the
        session's process is already running.

        Every actual launch goes through the supervisor's guarded launch:
        an interprocess guard serializes Relay instances and a machine scan
        for the exact configured executable runs before the spawn. A
        deferred outcome (existing Civ, busy guard, indeterminate scan) is
        not a failure: it is recorded as a typed waiting status and a later
        ordinary tick may retry.
        """
        if plan.outcome is not LaunchPlanOutcome.READY or plan.command is None:
            self._launch_blocked_reason = plan.reason
            return None
        if self._launch_in_flight or self._session_running:
            return None
        self._launch_in_flight = True
        try:
            result = self._supervisor.guarded_launch(plan.command)
        finally:
            self._launch_in_flight = False
        if (
            result.outcome is GuardedLaunchOutcome.LAUNCHED
            and result.identity is not None
        ):
            self._session_identity = result.identity
            self._session_running = True
            self._launch_failure_message = None
            self._launch_blocked_reason = None
            self._launch_deferred_message = None
            self._close_identity = None
            self._close_deadline = None
            self._force_close_attempted = False
            self._safely_closed = False
        elif result.deferred:
            self._launch_deferred_message = result.message or result.outcome.value
            self._launch_failure_message = None
        else:
            self._launch_failure_message = result.message or result.outcome.value
            self._launch_deferred_message = None
        return result

    def clear_launch_deferral(self) -> None:
        """Forget a deferred-launch status once no launch is wanted anymore."""
        self._launch_deferred_message = None

    def request_close(
        self,
        identity: ProcessIdentity,
        *,
        operation_id: str,
        allow_repeat: bool = False,
    ) -> CloseRequestResult | None:
        """Request a verified graceful close; ``None`` means refused locally.

        Refusals: the operation was already acted on (unless
        ``allow_repeat``), the identity is a remembered mismatch, or a
        fresh probe did not return an exact running match.
        """
        if not allow_repeat and operation_id == self._close_operation_id:
            return None
        if self.is_mismatched(identity):
            return None
        probe = self.probe(identity)
        if probe.outcome is not ProbeOutcome.RUNNING_MATCH:
            return None
        result = self._supervisor.request_graceful_close(identity)
        if result.outcome is CloseRequestOutcome.REQUESTED:
            self._close_identity = identity
            self._close_operation_id = operation_id
            self._close_deadline = self._clock.now() + GRACEFUL_CLOSE_DEADLINE_SECONDS
            self._force_close_attempted = False
            self._safely_closed = False
        return result

    def rearm_close_after_restart(self, pending: PostCommitCloseRecord) -> None:
        """Re-arm the graceful deadline once for a persisted close request.

        The deadline epoch is not durable; after a Relay restart the
        15-second window restarts from the current clock (recorded design
        choice — timers are never authoritative). No second WM_CLOSE is
        sent; only tracking resumes.
        """
        if pending.operation_id == self._close_operation_id:
            return
        self._close_identity = identity_from_close_record(pending)
        self._close_operation_id = pending.operation_id
        self._close_deadline = self._clock.now() + GRACEFUL_CLOSE_DEADLINE_SECONDS
        self._force_close_attempted = False
        self._safely_closed = False

    def close_deadline_elapsed(self) -> bool:
        return (
            self._close_deadline is not None
            and self._clock.now() >= self._close_deadline
        )

    def note_safely_closed(self) -> None:
        """Record that the entitled process exited after the close request."""
        self._safely_closed = True
        self._close_deadline = None
        self._session_running = False

    def drop_close_attempt(self, message: str) -> None:
        """Abandon the close attempt after an identity mismatch."""
        self._close_identity = None
        self._close_deadline = None
        self._mismatch_message = message

    def terminate_entitled(
        self, identity: ProcessIdentity, pending: PostCommitCloseRecord
    ) -> TerminateResult | None:
        """Force-terminate at most once, after full re-verification.

        Requires: no prior attempt, elapsed graceful deadline, a persisted
        close request whose operation matches the one acted on, an exact
        identity match against the durable entitlement, and a fresh probe
        returning ``RUNNING_MATCH``. ``None`` means refused locally.
        """
        if self._force_close_attempted or not self.close_deadline_elapsed():
            return None
        if not pending.close_requested:
            return None
        if pending.operation_id != self._close_operation_id:
            return None
        if not identity_matches_close_record(identity, pending):
            return None
        if self.is_mismatched(identity):
            return None
        probe = self.probe(identity)
        if probe.outcome is not ProbeOutcome.RUNNING_MATCH:
            return None
        self._force_close_attempted = True
        return self._supervisor.terminate(identity)

    def focus(self, identity: ProcessIdentity) -> FocusResult:
        """Focus after probe verification; never touches a mismatched pid."""
        if self.is_mismatched(identity):
            return FocusResult(
                outcome=FocusOutcome.IDENTITY_MISMATCH,
                message=self._mismatch_message or "identity could not be verified",
            )
        probe = self.probe(identity)
        if probe.outcome is ProbeOutcome.RUNNING_MISMATCH:
            return FocusResult(
                outcome=FocusOutcome.IDENTITY_MISMATCH, message=probe.message
            )
        if probe.outcome is ProbeOutcome.NOT_RUNNING:
            return FocusResult(outcome=FocusOutcome.NOT_RUNNING)
        if probe.outcome is not ProbeOutcome.RUNNING_MATCH:
            return FocusResult(
                outcome=FocusOutcome.ADAPTER_UNAVAILABLE, message=probe.message
            )
        return self._supervisor.focus(identity)

    def status_snapshot(
        self,
        *,
        association: ProcessIdentity | None,
        force_close_allowed: bool,
    ) -> ProcessStatusSnapshot:
        """Compute the typed status; probes here are read-only fact checks."""
        availability = self._supervisor.availability()
        if not availability.available:
            return ProcessStatusSnapshot(
                status=ProcessStatus.UNAVAILABLE,
                message=availability.reason,
                force_close_allowed=force_close_allowed,
            )
        if self._launch_blocked_reason is not None:
            return ProcessStatusSnapshot(
                status=ProcessStatus.LAUNCH_FAILED,
                message="Civilization was not launched",
                launch_blocked_reason=self._launch_blocked_reason,
                force_close_allowed=force_close_allowed,
            )
        if self._launch_failure_message is not None:
            return ProcessStatusSnapshot(
                status=ProcessStatus.LAUNCH_FAILED,
                message=self._launch_failure_message,
                force_close_allowed=force_close_allowed,
            )
        if self._launch_in_flight:
            return ProcessStatusSnapshot(
                status=ProcessStatus.STARTING,
                message="launching Civilization",
                force_close_allowed=force_close_allowed,
            )
        if self._launch_deferred_message is not None:
            return ProcessStatusSnapshot(
                status=ProcessStatus.WAITING_FOR_EXISTING_CIV,
                message=self._launch_deferred_message,
                force_close_allowed=force_close_allowed,
            )
        if (
            self._close_identity is not None
            and self._close_deadline is not None
            and not self._safely_closed
        ):
            return self._close_status(
                self._close_identity, self._close_deadline, force_close_allowed
            )
        if self._safely_closed:
            return ProcessStatusSnapshot(
                status=ProcessStatus.SAFELY_CLOSED,
                message="Civilization closed after the committed turn",
                force_close_allowed=force_close_allowed,
            )
        identity = association if association is not None else self._session_identity
        if identity is not None and not self.is_mismatched(identity):
            probe = self.probe(identity)
            if probe.outcome is ProbeOutcome.RUNNING_MATCH:
                return ProcessStatusSnapshot(
                    status=ProcessStatus.RUNNING,
                    message="Civilization is running",
                    identity=identity,
                    force_close_allowed=force_close_allowed,
                )
            if probe.outcome in {
                ProbeOutcome.ADAPTER_UNAVAILABLE,
                ProbeOutcome.PROBE_FAILED,
            }:
                return ProcessStatusSnapshot(
                    status=ProcessStatus.UNAVAILABLE,
                    message=probe.message or "the process adapter is unavailable",
                    force_close_allowed=force_close_allowed,
                )
        message = "ready to launch Civilization"
        if identity is not None and self.is_mismatched(identity):
            message = (
                "ready to launch Civilization; the previously associated "
                "process identity could not be verified and will not be touched"
            )
        return ProcessStatusSnapshot(
            status=ProcessStatus.READY,
            message=message,
            force_close_allowed=force_close_allowed,
        )

    def _close_status(
        self,
        identity: ProcessIdentity,
        deadline: float,
        force_close_allowed: bool,
    ) -> ProcessStatusSnapshot:
        probe = self.probe(identity)
        if probe.outcome is ProbeOutcome.NOT_RUNNING:
            self.note_safely_closed()
            return ProcessStatusSnapshot(
                status=ProcessStatus.SAFELY_CLOSED,
                message="Civilization closed after the committed turn",
                identity=identity,
                force_close_allowed=force_close_allowed,
            )
        if probe.outcome is ProbeOutcome.RUNNING_MISMATCH:
            self.drop_close_attempt(probe.message or "identity could not be verified")
            return ProcessStatusSnapshot(
                status=ProcessStatus.SAFELY_CLOSED,
                message=(
                    "the Relay-launched process is no longer running; another "
                    "process reuses its pid and will never be touched"
                ),
                force_close_allowed=force_close_allowed,
            )
        if probe.outcome is not ProbeOutcome.RUNNING_MATCH:
            return ProcessStatusSnapshot(
                status=ProcessStatus.UNAVAILABLE,
                message=probe.message or "the process adapter is unavailable",
                force_close_allowed=force_close_allowed,
            )
        remaining = max(0.0, deadline - self._clock.now())
        if remaining > 0.0:
            return ProcessStatusSnapshot(
                status=ProcessStatus.CLOSE_REQUESTED,
                message="waiting for Civilization to close the committed turn",
                identity=identity,
                force_close_allowed=force_close_allowed,
                close_deadline_remaining_seconds=remaining,
            )
        if force_close_allowed:
            return ProcessStatusSnapshot(
                status=ProcessStatus.FORCE_CLOSE_ELIGIBLE,
                message=(
                    "Civilization did not close within the graceful deadline; "
                    "force close is permitted for this match"
                ),
                identity=identity,
                force_close_allowed=True,
                close_deadline_remaining_seconds=0.0,
            )
        return ProcessStatusSnapshot(
            status=ProcessStatus.CLOSE_DEADLINE_ELAPSED,
            message=(
                "Civilization did not close within the graceful deadline; "
                "close it manually when convenient"
            ),
            identity=identity,
            force_close_allowed=False,
            close_deadline_remaining_seconds=0.0,
        )
