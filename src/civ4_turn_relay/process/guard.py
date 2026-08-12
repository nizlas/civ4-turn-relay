"""Cross-instance guarded Civ launch: interprocess guard port and algorithm.

Several Relay processes (multiple profiles, or one profile with several
matches) can share one Windows computer with one Civilization installation.
Before spawning Civ, Relay must atomically (a) serialize against other Relay
instances in the same interactive Windows session and (b) inspect the machine
for an already-running process of the exact configured Civ executable. The
check and the spawn are one protected operation — never two unprotected
application-level steps.

The guard only serializes *Relay* instances. It cannot stop a user or an
unrelated external program from starting Civ manually at the same moment;
that limitation is documented in ``docs/DESKTOP_CLIENT.md``.

Nothing in this module decides match ownership or advances match state.
"""

from __future__ import annotations

import hashlib
import ntpath
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from enum import Enum, unique
from typing import Protocol, runtime_checkable

from civ4_turn_relay.domain import DomainValidationError
from civ4_turn_relay.process.launch_config import CivLaunchCommand
from civ4_turn_relay.process.port import LaunchOutcome, LaunchResult, ProcessIdentity

_MUTEX_NAME_PREFIX = "Local\\civ4-turn-relay-launch-"


def normalize_windows_executable(path: str) -> str:
    """Normalize a Windows executable path for comparison only (never stored).

    ``ntpath`` treats both ``/`` and ``\\`` as separators and collapses
    dot-segments on every host, so Linux CI matches Windows production.
    """
    return ntpath.normpath(path).casefold()


def windows_executable_basename(path: str) -> str:
    """Case-folded basename of a Windows executable path, host-independent."""
    return ntpath.basename(normalize_windows_executable(path))


def launch_guard_name(executable_path: str) -> str:
    """Session-local guard name derived from the normalized executable path.

    A SHA-256 digest keeps the name valid regardless of path characters and
    ties the guard to one specific Civ installation: two different
    executables get independent guards.
    """
    digest = hashlib.sha256(
        normalize_windows_executable(executable_path).encode("utf-8")
    ).hexdigest()
    return f"{_MUTEX_NAME_PREFIX}{digest}"


@dataclass(frozen=True, slots=True)
class ProcessScanEntry:
    """One machine process as reported by a scan backend.

    ``executable_path`` is ``None`` when the backend could not resolve the
    process image (access denied, process exited mid-scan). ``name`` is the
    OS-reported short process name when available and is used only to decide
    whether an unresolvable entry might be the target executable.
    """

    pid: int
    executable_path: str | None = None
    name: str | None = None


@unique
class MachineScanOutcome(Enum):
    """Classification of one machine scan for the configured executable."""

    NO_MATCH = "no_match"
    EXACT_MATCH = "exact_match"
    INDETERMINATE = "indeterminate"


@dataclass(frozen=True, slots=True)
class MachineScanResult:
    """Scan classification with the blocking pid for diagnostics only."""

    outcome: MachineScanOutcome
    pid: int | None = None
    message: str = ""


def classify_scan_entries(
    entries: Iterable[ProcessScanEntry], *, executable_path: str
) -> MachineScanResult:
    """Classify a machine process scan against one exact configured executable.

    Rules (defensive by design):

    - Only a process whose executable resolves to the configured path
      (normalized, case-insensitive) counts as an exact blocker.
    - Unrelated processes whose executable could not be read (access denied,
      exited mid-scan) never block: their short name does not match the
      configured executable's file name.
    - A process that *looks like* the target executable by short name but
      whose full path cannot be verified fails closed as ``INDETERMINATE``:
      Relay refuses to launch rather than risk a second Civ instance.
    """
    target = normalize_windows_executable(executable_path)
    target_name = windows_executable_basename(executable_path)
    indeterminate: ProcessScanEntry | None = None
    for entry in entries:
        if entry.executable_path is not None:
            if normalize_windows_executable(entry.executable_path) == target:
                return MachineScanResult(
                    outcome=MachineScanOutcome.EXACT_MATCH,
                    pid=entry.pid,
                    message=(
                        "an existing process of the configured Civilization "
                        "executable is already running"
                    ),
                )
            continue
        name = (entry.name or "").casefold()
        if name and name == target_name and indeterminate is None:
            indeterminate = entry
    if indeterminate is not None:
        return MachineScanResult(
            outcome=MachineScanOutcome.INDETERMINATE,
            pid=indeterminate.pid,
            message=(
                "a process looks like the configured Civilization executable "
                "but its identity could not be verified; refusing to launch"
            ),
        )
    return MachineScanResult(outcome=MachineScanOutcome.NO_MATCH)


@unique
class GuardAcquireOutcome(Enum):
    """Result classification for acquiring the interprocess launch guard."""

    ACQUIRED = "acquired"
    ACQUIRED_ABANDONED = "acquired_abandoned"
    BUSY = "busy"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class GuardAcquisition:
    """One guard acquisition attempt; pass back to ``release`` when held."""

    outcome: GuardAcquireOutcome
    handle: object | None = None
    message: str = ""

    @property
    def held(self) -> bool:
        return self.outcome in {
            GuardAcquireOutcome.ACQUIRED,
            GuardAcquireOutcome.ACQUIRED_ABANDONED,
        }


@runtime_checkable
class LaunchGuard(Protocol):
    """Interprocess launch guard port (one guard per executable path).

    Implementations must be OS-backed so a crashed holder cannot leave a
    stale lock: the Windows implementation uses a named mutex whose
    abandonment is reported by the OS and recovered by the next waiter.
    """

    def acquire(self, executable_path: str) -> GuardAcquisition:
        """Try to acquire the guard for ``executable_path`` without blocking
        longer than a short bounded wait; never queue indefinitely."""
        ...

    def release(self, acquisition: GuardAcquisition) -> None:
        """Release a held acquisition; must be safe to call exactly once."""
        ...


@unique
class GuardedLaunchOutcome(Enum):
    """Result classification for one guarded launch operation."""

    LAUNCHED = "launched"
    EXISTING_CIV_DETECTED = "existing_civ_detected"
    GUARD_BUSY = "guard_busy"
    SCAN_INDETERMINATE = "scan_indeterminate"
    SPAWN_FAILURE = "spawn_failure"
    EXITED_IMMEDIATELY = "exited_immediately"
    IDENTITY_UNVERIFIED = "identity_unverified"
    GUARD_UNAVAILABLE = "guard_unavailable"
    ADAPTER_UNAVAILABLE = "adapter_unavailable"


LAUNCH_DEFERRED_OUTCOMES: frozenset[GuardedLaunchOutcome] = frozenset(
    {
        GuardedLaunchOutcome.EXISTING_CIV_DETECTED,
        GuardedLaunchOutcome.GUARD_BUSY,
        GuardedLaunchOutcome.SCAN_INDETERMINATE,
    }
)
"""Outcomes that defer the launch without spawning anything.

A deferred launch is not a failure: the verified downloaded turn stays
ready, no launch-attempt key may be consumed, and a later ordinary tick may
retry the guarded launch.
"""


@unique
class GuardCleanupOutcome(Enum):
    """Result of releasing a held launch guard after scan/launch."""

    RELEASED = "released"
    RELEASE_FAILED = "release_failed"
    CLOSE_FAILED = "close_failed"


@dataclass(frozen=True, slots=True)
class GuardedLaunchResult:
    """Guarded launch outcome; identity is present on success only.

    ``cleanup_outcome`` is ``None`` when the guard was never held (busy or
    unavailable). A cleanup failure never changes ``outcome`` or drops a
    verified ``LAUNCHED`` identity.
    """

    outcome: GuardedLaunchOutcome
    identity: ProcessIdentity | None = None
    message: str = ""
    existing_pid: int | None = None
    recovered_abandoned_guard: bool = False
    cleanup_outcome: GuardCleanupOutcome | None = None
    cleanup_message: str = ""

    def __post_init__(self) -> None:
        launched = self.outcome is GuardedLaunchOutcome.LAUNCHED
        if launched and self.identity is None:
            raise DomainValidationError(
                "a launched result requires an identity", field_path="identity"
            )
        if not launched and self.identity is not None:
            raise DomainValidationError(
                "only a launched result may carry an identity",
                field_path="identity",
            )

    @property
    def deferred(self) -> bool:
        return self.outcome in LAUNCH_DEFERRED_OUTCOMES

    @property
    def cleanup_failed(self) -> bool:
        return self.cleanup_outcome in {
            GuardCleanupOutcome.RELEASE_FAILED,
            GuardCleanupOutcome.CLOSE_FAILED,
        }


_LAUNCH_TO_GUARDED: dict[LaunchOutcome, GuardedLaunchOutcome] = {
    LaunchOutcome.LAUNCHED: GuardedLaunchOutcome.LAUNCHED,
    LaunchOutcome.ADAPTER_UNAVAILABLE: GuardedLaunchOutcome.ADAPTER_UNAVAILABLE,
    LaunchOutcome.SPAWN_FAILURE: GuardedLaunchOutcome.SPAWN_FAILURE,
    LaunchOutcome.EXITED_IMMEDIATELY: GuardedLaunchOutcome.EXITED_IMMEDIATELY,
    LaunchOutcome.IDENTITY_UNVERIFIED: GuardedLaunchOutcome.IDENTITY_UNVERIFIED,
}


def _classify_cleanup_error(error: BaseException) -> tuple[GuardCleanupOutcome, str]:
    """Map a release/close failure onto a typed cleanup outcome."""
    operation = getattr(error, "operation", None)
    if operation == "CloseHandle":
        return GuardCleanupOutcome.CLOSE_FAILED, str(error)
    return GuardCleanupOutcome.RELEASE_FAILED, str(error)


def execute_guarded_launch(
    *,
    guard: LaunchGuard,
    scan: Callable[[str], MachineScanResult],
    launch: Callable[[CivLaunchCommand], LaunchResult],
    command: CivLaunchCommand,
) -> GuardedLaunchResult:
    """Run the atomic guarded launch: acquire, scan, spawn, verify, release.

    The primary scan/launch result is preserved before cleanup. Release runs
    exactly once on this thread. A cleanup failure is attached to the result
    (or chained onto a primary exception) and never retries the spawn. A
    verified ``LAUNCHED`` identity is returned even when cleanup fails.

    If the OS reports the previous guard owner abandoned the guard (a crashed
    Relay), ownership is recovered but a fresh process scan still runs before
    any spawn; the recovery is recorded on the result as a diagnostic.
    """
    executable = command.argv[0]
    acquisition = guard.acquire(executable)
    if acquisition.outcome is GuardAcquireOutcome.UNAVAILABLE:
        return GuardedLaunchResult(
            outcome=GuardedLaunchOutcome.GUARD_UNAVAILABLE,
            message=acquisition.message or "the launch guard is unavailable",
        )
    if acquisition.outcome is GuardAcquireOutcome.BUSY:
        return GuardedLaunchResult(
            outcome=GuardedLaunchOutcome.GUARD_BUSY,
            message=(
                acquisition.message
                or (
                    "another Relay instance is currently checking or "
                    "launching Civilization"
                )
            ),
        )
    recovered = acquisition.outcome is GuardAcquireOutcome.ACQUIRED_ABANDONED

    def scan_and_launch() -> GuardedLaunchResult:
        scan_result = scan(executable)
        if scan_result.outcome is MachineScanOutcome.EXACT_MATCH:
            return GuardedLaunchResult(
                outcome=GuardedLaunchOutcome.EXISTING_CIV_DETECTED,
                message=scan_result.message,
                existing_pid=scan_result.pid,
                recovered_abandoned_guard=recovered,
            )
        if scan_result.outcome is MachineScanOutcome.INDETERMINATE:
            return GuardedLaunchResult(
                outcome=GuardedLaunchOutcome.SCAN_INDETERMINATE,
                message=scan_result.message,
                existing_pid=scan_result.pid,
                recovered_abandoned_guard=recovered,
            )
        result = launch(command)
        return GuardedLaunchResult(
            outcome=_LAUNCH_TO_GUARDED[result.outcome],
            identity=result.identity,
            message=result.message,
            recovered_abandoned_guard=recovered,
        )

    primary: GuardedLaunchResult | None = None
    primary_error: Exception | None = None
    try:
        primary = scan_and_launch()
    except Exception as error:
        primary_error = error

    cleanup_outcome = GuardCleanupOutcome.RELEASED
    cleanup_message = ""
    try:
        guard.release(acquisition)
    except Exception as cleanup_error:
        cleanup_outcome, cleanup_message = _classify_cleanup_error(cleanup_error)
        if primary_error is not None:
            primary_error.add_note(f"launch-guard cleanup also failed: {cleanup_error}")
            raise primary_error from cleanup_error
        if primary is None:
            raise
        return replace(
            primary,
            cleanup_outcome=cleanup_outcome,
            cleanup_message=cleanup_message,
        )

    if primary_error is not None:
        raise primary_error
    if primary is None:
        raise RuntimeError("guarded launch produced no result")
    return replace(
        primary,
        cleanup_outcome=cleanup_outcome,
        cleanup_message=cleanup_message,
    )
