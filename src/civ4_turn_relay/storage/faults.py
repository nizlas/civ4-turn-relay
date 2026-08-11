"""Fake-only deterministic failure injection.

Faults are keyed by generic storage operation names (not protocol business
logic), addressable by occurrence, and may fire before or after the
underlying mutation. Read-back corruption returns altered bytes without
changing stored content.
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


@dataclass(frozen=True, slots=True)
class FaultSpec:
    """One selectable, occurrence-addressable fault."""

    operation: StorageOp
    moment: FaultMoment
    occurrence: int = 1
    """1-based index of the call to ``operation`` that should fail."""

    def __post_init__(self) -> None:
        if self.occurrence < 1:
            raise ValueError("fault occurrence must be >= 1")


@dataclass(frozen=True, slots=True)
class ReadCorruptionSpec:
    """Return corrupted bytes on a successful read without mutating storage."""

    occurrence: int = 1

    def __post_init__(self) -> None:
        if self.occurrence < 1:
            raise ValueError("read-corruption occurrence must be >= 1")


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
        self._faults.append(
            FaultSpec(operation=operation, moment=moment, occurrence=occurrence)
        )

    def inject_read_corruption(self, *, occurrence: int = 1) -> None:
        """Schedule a one-shot corrupted read at the given READ occurrence."""
        self._read_corruptions.append(ReadCorruptionSpec(occurrence=occurrence))

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

    def begin(self, operation: StorageOp) -> FaultMoment | None:
        """Count a call and return a BEFORE fault moment if one fires now.

        AFTER faults are reported by :meth:`finish` after the mutation.
        """
        count = self._counts[operation] + 1
        self._counts[operation] = count
        fault = self._take_fault(operation, count, FaultMoment.BEFORE)
        return FaultMoment.BEFORE if fault is not None else None

    def finish(self, operation: StorageOp) -> bool:
        """Return True if an AFTER fault should raise for the current call."""
        count = self._counts[operation]
        return self._take_fault(operation, count, FaultMoment.AFTER) is not None

    def corrupt_read_payload(self, data: bytes) -> bytes | None:
        """If a read-corruption fires for the current READ, return bad bytes."""
        count = self._counts[StorageOp.READ]
        for index, spec in enumerate(self._read_corruptions):
            if spec.occurrence == count:
                del self._read_corruptions[index]
                return _corrupt_bytes(data)
        return None

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
