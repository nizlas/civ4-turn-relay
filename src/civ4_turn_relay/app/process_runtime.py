"""Per-match Civ process runtime coordination (P7 part B).

The coordinator drives real launches and closes through the process
supervisor port while never advancing remote protocol state; only the
existing commit path does that. Identity verification always combines a
fresh ``supervisor.probe`` with durable record comparison — a running PID
alone is never treated as evidence.

Restart recovery: all durable process state lives in the match records
(``launch_attempt``, ``process_association``, ``pending_post_commit_close``).
The Fully Managed post-commit close terminates the exact entitled process
directly (Civ's modal PBEM confirmation blocks a graceful close); the
at-most-once guard for that termination is deliberately session-local, so a
Relay restart after a committed handoff allows exactly one fresh attempt
against the re-verified entitled identity.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
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
    TerminateOutcome,
    TerminateResult,
    normalize_windows_executable,
)


@unique
class ProcessStatus(Enum):
    """Typed per-match process status for UI display."""

    UNAVAILABLE = "unavailable"
    READY = "ready"
    STARTING = "starting"
    RUNNING = "running"
    WAITING_FOR_EXISTING_CIV = "waiting_for_existing_civ"
    WAITING_FOR_LAUNCH_GUARD = "waiting_for_launch_guard"
    LAUNCH_SCAN_INDETERMINATE = "launch_scan_indeterminate"
    CLOSE_REQUESTED = "close_requested"
    CLOSING_AFTER_COMMIT = "closing_after_commit"
    CLOSE_FAILED = "close_failed"
    SAFELY_CLOSED = "safely_closed"
    LAUNCH_FAILED = "launch_failed"


@dataclass(frozen=True, slots=True)
class ProcessStatusSnapshot:
    """Immutable, secret-free process status snapshot for one match."""

    status: ProcessStatus
    message: str = ""
    identity: ProcessIdentity | None = None
    launch_blocked_reason: str | None = None
    cleanup_warning: str | None = None


def _identity_key(identity: ProcessIdentity) -> tuple[int, int, str]:
    """Exact comparable identity: pid, precise creation token, normalized exe.

    The second-resolution UTC timestamp is deliberately excluded: a reused
    pid within the same wall-clock second must still compare as different.
    """
    return (
        identity.pid,
        identity.process_create_time_ns,
        normalize_windows_executable(identity.executable_path),
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

    Session state (in-flight launch guard, close progress, the at-most-once
    post-commit termination guard, remembered identity mismatches) lives
    here; durable state stays in the match records. Every operation that
    could touch a process re-verifies the exact identity through
    :meth:`probe` first and, for post-commit termination, against the
    durable entitlement supplied by the caller.
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
        self._launch_deferred_outcome: GuardedLaunchOutcome | None = None
        self._launch_deferred_message: str | None = None
        self._mismatched_keys: set[tuple[int, int, str]] = set()
        self._mismatch_message: str | None = None
        self._close_identity: ProcessIdentity | None = None
        self._close_operation_id: str | None = None
        self._closing_after_commit = False
        self._close_failure_message: str | None = None
        self._terminate_acted_operation_id: str | None = None
        self._safely_closed = False
        self._cleanup_warning: str | None = None

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
            self._launch_deferred_outcome = None
            self._launch_deferred_message = None
            self._close_identity = None
            self._closing_after_commit = False
            self._close_failure_message = None
            self._safely_closed = False
        elif result.deferred:
            self._launch_deferred_outcome = result.outcome
            self._launch_deferred_message = result.message or result.outcome.value
            self._launch_failure_message = None
        else:
            self._launch_failure_message = result.message or result.outcome.value
            self._launch_deferred_outcome = None
            self._launch_deferred_message = None
        if result.cleanup_failed:
            self._cleanup_warning = result.cleanup_message or (
                result.cleanup_outcome.value
                if result.cleanup_outcome is not None
                else "launch-guard cleanup failed"
            )
        else:
            self._cleanup_warning = None
        return result

    def clear_launch_deferral(self) -> None:
        """Forget a deferred-launch status once no launch is wanted anymore."""
        self._launch_deferred_outcome = None
        self._launch_deferred_message = None

    def request_close(
        self,
        identity: ProcessIdentity,
        *,
        operation_id: str,
        allow_repeat: bool = False,
    ) -> CloseRequestResult | None:
        """Request a verified graceful close; ``None`` means refused locally.

        This is the manual, user-initiated close path (Standard mode keeps
        graceful semantics). Refusals: the operation was already acted on
        (unless ``allow_repeat``), the identity is a remembered mismatch,
        or a fresh probe did not return an exact running match.
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
            self._closing_after_commit = False
            self._close_failure_message = None
            self._safely_closed = False
        return result

    def note_safely_closed(self) -> None:
        """Record that the entitled process exited after the close action."""
        self._safely_closed = True
        self._close_failure_message = None
        self._session_running = False

    def drop_close_attempt(self, message: str) -> None:
        """Abandon the close attempt after an identity mismatch."""
        self._close_identity = None
        self._closing_after_commit = False
        self._close_failure_message = None
        self._mismatch_message = message

    def terminate_after_commit(
        self,
        identity: ProcessIdentity,
        pending: PostCommitCloseRecord,
        *,
        user_requested: bool = False,
    ) -> TerminateResult | None:
        """Directly terminate the exact entitled process after a handoff.

        Verified handoff first, then direct termination: the caller supplies
        the durable post-commit entitlement (written only for COMMITTED or
        exactly attributed IDEMPOTENT_ACK evidence). No graceful close is
        attempted and there is no waiting period — Civ's modal PBEM
        confirmation blocks WM_CLOSE, so a normal close can never succeed.

        Safety: the identity must match the entitlement exactly (pid,
        precise creation token, normalized executable path) and a fresh
        probe immediately before the call must return ``RUNNING_MATCH``.
        The termination fires at most once per operation per session unless
        ``user_requested`` explicitly retries. ``None`` means refused
        locally without touching any process.
        """
        if (
            not user_requested
            and pending.operation_id == self._terminate_acted_operation_id
        ):
            return None
        if not identity_matches_close_record(identity, pending):
            return None
        if self.is_mismatched(identity):
            return None
        probe = self.probe(identity)
        if probe.outcome is ProbeOutcome.NOT_RUNNING:
            # The entitled process is already gone: the close is complete
            # without any termination call.
            self._close_identity = identity
            self._close_operation_id = pending.operation_id
            self._closing_after_commit = True
            self.note_safely_closed()
            return TerminateResult(outcome=TerminateOutcome.NOT_RUNNING)
        if probe.outcome is ProbeOutcome.RUNNING_MISMATCH:
            self.drop_close_attempt(probe.message or "identity could not be verified")
            return TerminateResult(
                outcome=TerminateOutcome.IDENTITY_MISMATCH, message=probe.message
            )
        if probe.outcome is not ProbeOutcome.RUNNING_MATCH:
            return TerminateResult(
                outcome=TerminateOutcome.ADAPTER_UNAVAILABLE, message=probe.message
            )
        self._terminate_acted_operation_id = pending.operation_id
        self._close_identity = identity
        self._close_operation_id = pending.operation_id
        self._closing_after_commit = True
        self._close_failure_message = None
        self._safely_closed = False
        result = self._supervisor.terminate(identity)
        if result.outcome is TerminateOutcome.TERMINATED:
            # Never claim closed on the terminate result alone; only a
            # probe that no longer finds the process confirms the close.
            verify = self.probe(identity)
            if verify.outcome is ProbeOutcome.NOT_RUNNING:
                self.note_safely_closed()
        elif result.outcome is TerminateOutcome.NOT_RUNNING:
            self.note_safely_closed()
        elif result.outcome is TerminateOutcome.IDENTITY_MISMATCH:
            self.drop_close_attempt(result.message or "identity could not be verified")
        else:
            self._close_failure_message = result.message or result.outcome.value
        return result

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
        self, *, association: ProcessIdentity | None
    ) -> ProcessStatusSnapshot:
        """Compute the typed status; probes here are read-only fact checks."""
        snapshot = self._status_snapshot(association=association)
        if self._cleanup_warning:
            return replace(snapshot, cleanup_warning=self._cleanup_warning)
        return snapshot

    def _status_snapshot(
        self, *, association: ProcessIdentity | None
    ) -> ProcessStatusSnapshot:
        availability = self._supervisor.availability()
        if not availability.available:
            return ProcessStatusSnapshot(
                status=ProcessStatus.UNAVAILABLE,
                message=availability.reason,
            )
        if self._launch_blocked_reason is not None:
            return ProcessStatusSnapshot(
                status=ProcessStatus.LAUNCH_FAILED,
                message="Civilization was not launched",
                launch_blocked_reason=self._launch_blocked_reason,
            )
        if self._launch_failure_message is not None:
            return ProcessStatusSnapshot(
                status=ProcessStatus.LAUNCH_FAILED,
                message=self._launch_failure_message,
            )
        if self._launch_in_flight:
            return ProcessStatusSnapshot(
                status=ProcessStatus.STARTING,
                message="launching Civilization",
            )
        if self._launch_deferred_outcome is GuardedLaunchOutcome.EXISTING_CIV_DETECTED:
            return ProcessStatusSnapshot(
                status=ProcessStatus.WAITING_FOR_EXISTING_CIV,
                message=(
                    self._launch_deferred_message
                    or "Your turn is ready — waiting for Civilization to close."
                ),
            )
        if self._launch_deferred_outcome is GuardedLaunchOutcome.GUARD_BUSY:
            return ProcessStatusSnapshot(
                status=ProcessStatus.WAITING_FOR_LAUNCH_GUARD,
                message=(
                    self._launch_deferred_message
                    or (
                        "another Relay instance is currently checking or "
                        "launching Civilization"
                    )
                ),
            )
        if self._launch_deferred_outcome is GuardedLaunchOutcome.SCAN_INDETERMINATE:
            return ProcessStatusSnapshot(
                status=ProcessStatus.LAUNCH_SCAN_INDETERMINATE,
                message=(
                    self._launch_deferred_message
                    or (
                        "Relay cannot safely determine whether Civilization "
                        "is already running"
                    )
                ),
            )
        if self._close_identity is not None and not self._safely_closed:
            return self._close_status(self._close_identity)
        if self._safely_closed:
            return ProcessStatusSnapshot(
                status=ProcessStatus.SAFELY_CLOSED,
                message="Civilization closed after the committed turn",
            )
        identity = association if association is not None else self._session_identity
        if identity is not None and not self.is_mismatched(identity):
            probe = self.probe(identity)
            if probe.outcome is ProbeOutcome.RUNNING_MATCH:
                return ProcessStatusSnapshot(
                    status=ProcessStatus.RUNNING,
                    message="Civilization is running",
                    identity=identity,
                )
            if probe.outcome in {
                ProbeOutcome.ADAPTER_UNAVAILABLE,
                ProbeOutcome.PROBE_FAILED,
            }:
                return ProcessStatusSnapshot(
                    status=ProcessStatus.UNAVAILABLE,
                    message=probe.message or "the process adapter is unavailable",
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
        )

    def _close_status(self, identity: ProcessIdentity) -> ProcessStatusSnapshot:
        probe = self.probe(identity)
        if probe.outcome is ProbeOutcome.NOT_RUNNING:
            self.note_safely_closed()
            return ProcessStatusSnapshot(
                status=ProcessStatus.SAFELY_CLOSED,
                message="Civilization closed after the committed turn",
                identity=identity,
            )
        if probe.outcome is ProbeOutcome.RUNNING_MISMATCH:
            self.drop_close_attempt(probe.message or "identity could not be verified")
            return ProcessStatusSnapshot(
                status=ProcessStatus.SAFELY_CLOSED,
                message=(
                    "the Relay-launched process is no longer running; another "
                    "process reuses its pid and will never be touched"
                ),
            )
        if probe.outcome is not ProbeOutcome.RUNNING_MATCH:
            return ProcessStatusSnapshot(
                status=ProcessStatus.UNAVAILABLE,
                message=probe.message or "the process adapter is unavailable",
            )
        if self._close_failure_message is not None:
            return ProcessStatusSnapshot(
                status=ProcessStatus.CLOSE_FAILED,
                message=(
                    "the turn is safely sent, but Civilization could not be "
                    f"closed: {self._close_failure_message}"
                ),
                identity=identity,
            )
        if self._closing_after_commit:
            return ProcessStatusSnapshot(
                status=ProcessStatus.CLOSING_AFTER_COMMIT,
                message="the turn is safely sent — closing Civilization",
                identity=identity,
            )
        return ProcessStatusSnapshot(
            status=ProcessStatus.CLOSE_REQUESTED,
            message="waiting for Civilization to close the committed turn",
            identity=identity,
        )
