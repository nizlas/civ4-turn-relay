"""Synchronous storage port and value types.

Paths are non-empty relative POSIX paths below the adapter root. Atomic
``mkdir``, immutable no-replace publication, and posix-rename-equivalent
replace are distinct operations with explicit capability flags. The port
reports facts only; it never decides handoff ownership.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, unique
from typing import Protocol, runtime_checkable


@unique
class StorageEntryKind(Enum):
    """Kind of an immediate directory child."""

    FILE = "file"
    DIRECTORY = "directory"


@dataclass(frozen=True, slots=True)
class StorageEntry:
    """One immediate child of a directory listing."""

    name: str
    kind: StorageEntryKind


@dataclass(frozen=True, slots=True)
class StorageCapabilities:
    """Adapter capabilities required by the sync protocol.

    When a required flag is false, the corresponding operation MUST raise
    :class:`~civ4_turn_relay.storage.errors.StorageCapabilityError` before
    mutating any state.
    """

    exclusive_mkdir: bool
    atomic_replace: bool
    atomic_publish_no_replace: bool
    complete_readback: bool


@runtime_checkable
class Storage(Protocol):
    """Synchronous byte/object store used by the protocol engine."""

    def capabilities(self) -> StorageCapabilities:
        """Return the adapter's capability flags."""

    def mkdir(self, path: str) -> None:
        """Atomically create an empty directory.

        Fails if ``path`` already exists as a file or directory. Requires
        ``exclusive_mkdir``.
        """

    def write_file(self, path: str, data: bytes, *, overwrite: bool = False) -> None:
        """Write ``data`` to a file at ``path``.

        When ``overwrite`` is false and the file exists, raise already-exists.
        Never silently replaces a directory.
        """

    def read_file(self, path: str) -> bytes:
        """Return the complete bytes of the file at ``path``."""

    def list_dir(self, path: str) -> tuple[StorageEntry, ...]:
        """List immediate children of ``path`` in deterministic name order."""

    def remove_file(self, path: str) -> None:
        """Remove the file at ``path``."""

    def remove_dir(self, path: str) -> None:
        """Remove the empty directory at ``path``."""

    def publish_no_replace(self, source: str, destination: str) -> None:
        """Atomically publish ``source`` to ``destination`` only if absent.

        Used for immutable save/history objects. Requires
        ``atomic_publish_no_replace``. Never overwrites an existing
        destination, even when content is identical — callers verify and
        reuse explicitly.
        """

    def atomic_replace(self, source: str, destination: str) -> None:
        """Atomically replace ``destination`` with ``source`` (posix-rename).

        Used for ``manifest.json`` commit. Requires ``atomic_replace``.
        May replace an existing file destination; must not replace a directory.
        """
