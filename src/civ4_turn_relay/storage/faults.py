"""Fake-only deterministic failure injection.

Faults are keyed by generic storage operation names (not protocol business
logic). Occurrence is the 1-based **complete operation-call number** for that
operation (each ``begin`` starts a call).

Lifecycle for call N of an operation:

1. ``begin`` increments the call counter to N and may fire a BEFORE fault.
2. If BEFORE fires, any AFTER / read-corruption scheduled for N is retired
   immediately (the call never reaches those moments).
3. If the operation later fails with an ordinary storage error, the fake MUST
   call ``abort``, which retires any AFTER / read-corruption still pending for N.
4. If the operation succeeds, ``finish`` may fire an AFTER fault for N.

Scheduling rules:

- Reject ``occurrence < 1``.
- Reject an occurrence that has already completed (``occurrence <= count``).
- Reject duplicate ``(operation, moment, occurrence)`` schedules.
- BEFORE and AFTER for the same occurrence are allowed; at most one of them
  fires for that call (BEFORE wins and retires AFTER).

Injection remains one-shot and fully resettable. Read-back corruption returns
altered bytes without mutating stored content.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, unique


@unique
class StorageOp(Enum):
    """Generic storage operations that may be faulted."""

    WRITE = "write"
    READ = "read"
    MKDIR = "mkdir"
    LIST = "list"
    REMOVE_FILE = "remove_file"
    REMOVE_DIR = "remove_dir"
    PUBLISH_NO_REPLACE = "publish_no_replace"
    ATOMIC_REPLACE = "atomic_replace"


@unique
class FaultMoment(Enum):
    """When a fault fires relative to the storage mutation."""

    BEFORE = "before"
    """Raise before mutation; state is unchanged."""

    AFTER = "after"
    """Complete the mutation, then raise (lost response / network interrupt)."""


class FaultScheduleError(ValueError):
    """Raised when a fault schedule is duplicate or can never fire."""


@dataclass(frozen=True, slots=True)
class FaultSpec:
    """One selectable, occurrence-addressable fault."""

    operation: StorageOp
    moment: FaultMoment
    occurrence: int = 1
    """1-based index of the call to ``operation`` that should fail."""

    def __post_init__(self) -> None:
        if self.occurrence < 1:
            raise FaultScheduleError("fault occurrence must be >= 1")


@dataclass(frozen=True, slots=True)
class ReadCorruptionSpec:
    """Return corrupted bytes on a successful read without mutating storage."""

    occurrence: int = 1

    def __post_init__(self) -> None:
        if self.occurrence < 1:
            raise FaultScheduleError("read-corruption occurrence must be >= 1")


class FaultController:
    """Mutable controller attached to a fake storage instance."""

    def __init__(self) -> None:
        self._faults: list[FaultSpec] = []
        self._read_corruptions: list[ReadCorruptionSpec] = []
        self._counts: dict[StorageOp, int] = {op: 0 for op in StorageOp}

    def inject(
        self,
        operation: StorageOp,
        *,
        moment: FaultMoment,
        occurrence: int = 1,
    ) -> None:
        """Schedule a one-shot fault for the given operation occurrence."""
        spec = FaultSpec(operation=operation, moment=moment, occurrence=occurrence)
        self._reject_if_already_passed(operation, occurrence)
        for existing in self._faults:
            if (
                existing.operation is operation
                and existing.moment is moment
                and existing.occurrence == occurrence
            ):
                raise FaultScheduleError(
                    "duplicate fault schedule for operation/moment/occurrence"
                )
        self._faults.append(spec)

    def inject_read_corruption(self, *, occurrence: int = 1) -> None:
        """Schedule a one-shot corrupted read at the given READ occurrence."""
        spec = ReadCorruptionSpec(occurrence=occurrence)
        self._reject_if_already_passed(StorageOp.READ, occurrence)
        for existing in self._read_corruptions:
            if existing.occurrence == occurrence:
                raise FaultScheduleError(
                    "duplicate read-corruption schedule for occurrence"
                )
        self._read_corruptions.append(spec)

    def reset(self) -> None:
        """Remove all configured faults and reset occurrence counters."""
        self._faults.clear()
        self._read_corruptions.clear()
        self._counts = {op: 0 for op in StorageOp}

    def pending_faults(self) -> tuple[FaultSpec, ...]:
        """Return currently scheduled transport faults (copy)."""
        return tuple(self._faults)

    def pending_read_corruptions(self) -> tuple[ReadCorruptionSpec, ...]:
        """Return currently scheduled read-corruption faults (copy)."""
        return tuple(self._read_corruptions)

    def call_count(self, operation: StorageOp) -> int:
        """Return how many calls to ``operation`` have begun."""
        return self._counts[operation]

    def begin(self, operation: StorageOp) -> FaultMoment | None:
        """Start call N and return BEFORE if that fault fires now.

        When BEFORE fires, AFTER/read-corruption for N are retired because the
        call cannot reach those moments. AFTER faults are otherwise reported by
        :meth:`finish` after a successful mutation, or retired by :meth:`abort`.
        """
        count = self._counts[operation] + 1
        self._counts[operation] = count
        fault = self._take_fault(operation, count, FaultMoment.BEFORE)
        if fault is not None:
            self._retire_unreachable_for_call(operation, count)
            return FaultMoment.BEFORE
        return None

    def finish(self, operation: StorageOp) -> bool:
        """Return True if an AFTER fault should raise for the current call."""
        count = self._counts[operation]
        return self._take_fault(operation, count, FaultMoment.AFTER) is not None

    def abort(self, operation: StorageOp) -> None:
        """Retire AFTER/read-corruption for the current call after a normal error.

        Call this when an operation began (``begin`` returned without BEFORE)
        but failed before a successful ``finish``.
        """
        count = self._counts[operation]
        self._retire_unreachable_for_call(operation, count)

    def corrupt_read_payload(self, data: bytes) -> bytes | None:
        """If a read-corruption fires for the current READ, return bad bytes."""
        count = self._counts[StorageOp.READ]
        for index, spec in enumerate(self._read_corruptions):
            if spec.occurrence == count:
                del self._read_corruptions[index]
                return _corrupt_bytes(data)
        return None

    def _reject_if_already_passed(self, operation: StorageOp, occurrence: int) -> None:
        if occurrence <= self._counts[operation]:
            raise FaultScheduleError(
                "fault occurrence already passed for this operation"
            )

    def _retire_unreachable_for_call(
        self, operation: StorageOp, occurrence: int
    ) -> None:
        self._faults = [
            spec
            for spec in self._faults
            if not (
                spec.operation is operation
                and spec.occurrence == occurrence
                and spec.moment is FaultMoment.AFTER
            )
        ]
        if operation is StorageOp.READ:
            self._read_corruptions = [
                spec for spec in self._read_corruptions if spec.occurrence != occurrence
            ]

    def _take_fault(
        self,
        operation: StorageOp,
        occurrence: int,
        moment: FaultMoment,
    ) -> FaultSpec | None:
        for index, spec in enumerate(self._faults):
            if (
                spec.operation is operation
                and spec.occurrence == occurrence
                and spec.moment is moment
            ):
                del self._faults[index]
                return spec
        return None


def _corrupt_bytes(data: bytes) -> bytes:
    """Return bytes that differ from ``data`` without resembling stored content."""
    if not data:
        return b"\x00"
    # Flip the first byte so the digest/size relationship changes for non-empty
    # payloads; preserve length so size-only checks cannot accidentally pass.
    first = data[0] ^ 0xFF
    return bytes((first,)) + data[1:]
