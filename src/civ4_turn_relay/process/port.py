"""Process supervisor port: identity, results, and the supervisor protocol.

Adapters implementing this port report process facts only. They never decide
match ownership and never advance match state (design §8; AGENTS.md rules).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, unique
from typing import Protocol, runtime_checkable

from civ4_turn_relay.domain import (
    DomainValidationError,
    validate_utc_timestamp,
    validate_windows_local_path,
)
from civ4_turn_relay.local import ProcessObservation
from civ4_turn_relay.process.launch_config import CivLaunchCommand


@dataclass(frozen=True, slots=True)
class ProcessIdentity:
    """Durable identity of one specific launched process.

    Equality-relevant identity is pid + ``process_create_time_ns`` (the
    precise creation token reported by the process backend) + the normalized
    executable path. The second-resolution UTC start timestamp is kept for
    diagnostics and human-readable records only; it is never the sole
    equality check, so a reused pid within the same wall-clock second is
    still detected as a different process.
    """

    pid: int
    process_start_time_utc: str
    process_create_time_ns: int
    executable_path: str

    def __post_init__(self) -> None:
        if isinstance(self.pid, bool) or not isinstance(self.pid, int):
            raise DomainValidationError("expected an integer pid", field_path="pid")
        if self.pid <= 0:
            raise DomainValidationError(
                "expected a positive process id", field_path="pid"
            )
        object.__setattr__(
            self,
            "process_start_time_utc",
            validate_utc_timestamp(
                self.process_start_time_utc, field_path="process_start_time_utc"
            ),
        )
        if isinstance(self.process_create_time_ns, bool) or not isinstance(
            self.process_create_time_ns, int
        ):
            raise DomainValidationError(
                "expected an integer creation token",
                field_path="process_create_time_ns",
            )
        if self.process_create_time_ns <= 0:
            raise DomainValidationError(
                "expected a positive creation token",
                field_path="process_create_time_ns",
            )
        validate_windows_local_path(self.executable_path, field_path="executable_path")


@dataclass(frozen=True, slots=True)
class SupervisorAvailability:
    """Whether the process adapter can operate on this host."""

    available: bool
    reason: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.available, bool):
            raise DomainValidationError(
                "expected a boolean availability flag", field_path="available"
            )
        if not isinstance(self.reason, str):
            raise DomainValidationError("expected a string", field_path="reason")
        if not self.available and not self.reason:
            raise DomainValidationError(
                "an unavailable adapter requires a reason", field_path="reason"
            )


@unique
class LaunchOutcome(Enum):
    """Result classification for :meth:`ProcessSupervisor.launch`."""

    LAUNCHED = "launched"
    ADAPTER_UNAVAILABLE = "adapter_unavailable"
    SPAWN_FAILURE = "spawn_failure"
    EXITED_IMMEDIATELY = "exited_immediately"
    IDENTITY_UNVERIFIED = "identity_unverified"


@dataclass(frozen=True, slots=True)
class LaunchResult:
    """Launch outcome with the verified identity on success only."""

    outcome: LaunchOutcome
    identity: ProcessIdentity | None = None
    message: str = ""

    def __post_init__(self) -> None:
        launched = self.outcome is LaunchOutcome.LAUNCHED
        if launched and self.identity is None:
            raise DomainValidationError(
                "a launched result requires an identity", field_path="identity"
            )
        if not launched and self.identity is not None:
            raise DomainValidationError(
                "only a launched result may carry an identity",
                field_path="identity",
            )


@unique
class ProbeOutcome(Enum):
    """Result classification for :meth:`ProcessSupervisor.probe`."""

    RUNNING_MATCH = "running_match"
    RUNNING_MISMATCH = "running_mismatch"
    NOT_RUNNING = "not_running"
    ADAPTER_UNAVAILABLE = "adapter_unavailable"
    PROBE_FAILED = "probe_failed"


@dataclass(frozen=True, slots=True)
class ProbeResult:
    """Probe outcome for one exact process identity."""

    outcome: ProbeOutcome
    message: str = ""


@unique
class CloseRequestOutcome(Enum):
    """Result classification for graceful close requests."""

    REQUESTED = "requested"
    NOT_RUNNING = "not_running"
    IDENTITY_MISMATCH = "identity_mismatch"
    ADAPTER_UNAVAILABLE = "adapter_unavailable"
    REQUEST_FAILED = "request_failed"


@dataclass(frozen=True, slots=True)
class CloseRequestResult:
    """Graceful close request outcome."""

    outcome: CloseRequestOutcome
    message: str = ""


@unique
class FocusOutcome(Enum):
    """Result classification for window focus requests."""

    FOCUSED = "focused"
    NO_WINDOW = "no_window"
    NOT_RUNNING = "not_running"
    IDENTITY_MISMATCH = "identity_mismatch"
    ADAPTER_UNAVAILABLE = "adapter_unavailable"
    FOCUS_FAILED = "focus_failed"


@dataclass(frozen=True, slots=True)
class FocusResult:
    """Window focus request outcome."""

    outcome: FocusOutcome
    message: str = ""


@unique
class TerminateOutcome(Enum):
    """Result classification for forced termination."""

    TERMINATED = "terminated"
    NOT_RUNNING = "not_running"
    IDENTITY_MISMATCH = "identity_mismatch"
    ADAPTER_UNAVAILABLE = "adapter_unavailable"
    TERMINATE_FAILED = "terminate_failed"


@dataclass(frozen=True, slots=True)
class TerminateResult:
    """Forced termination outcome."""

    outcome: TerminateOutcome
    message: str = ""


@runtime_checkable
class ProcessSupervisor(Protocol):
    """Adapter port for launching and supervising the Civ process.

    Contract: :meth:`probe`, :meth:`request_graceful_close`, :meth:`focus`,
    and :meth:`terminate` MUST verify the exact identity — pid, the precise
    ``process_create_time_ns`` creation token, and case-insensitively
    normalized executable path — and return ``IDENTITY_MISMATCH`` /
    ``RUNNING_MISMATCH`` rather than acting on a reused PID. A running PID
    alone is never sufficient evidence, and neither is a matching
    second-resolution timestamp.

    :meth:`terminate` is only ever invoked by higher layers after post-commit
    entitlement re-verification; the supervisor still re-verifies identity.
    """

    def availability(self) -> SupervisorAvailability:
        """Report whether this adapter can operate on the current host."""
        ...

    def launch(self, command: CivLaunchCommand) -> LaunchResult:
        """Spawn the command and verify the resulting process identity."""
        ...

    def probe(self, identity: ProcessIdentity) -> ProbeResult:
        """Report whether exactly ``identity`` is still running."""
        ...

    def request_graceful_close(self, identity: ProcessIdentity) -> CloseRequestResult:
        """Ask exactly ``identity`` to close (WM_CLOSE); never force it."""
        ...

    def focus(self, identity: ProcessIdentity) -> FocusResult:
        """Bring the main window of exactly ``identity`` to the foreground."""
        ...

    def terminate(self, identity: ProcessIdentity) -> TerminateResult:
        """Forcibly terminate exactly ``identity`` after re-verification."""
        ...


def observation_from_identity(
    identity: ProcessIdentity, *, running: bool
) -> ProcessObservation:
    """Build the orchestration-facing observation for one identity."""
    return ProcessObservation(
        pid=identity.pid,
        process_start_time_utc=identity.process_start_time_utc,
        process_create_time_ns=identity.process_create_time_ns,
        executable_path=identity.executable_path,
        running=running,
    )
