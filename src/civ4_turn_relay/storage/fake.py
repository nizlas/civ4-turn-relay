"""Deterministic in-memory fake implementing the storage port.

Models OpenSSH/SFTP semantics closely enough for protocol commit tests:
exclusive mkdir, parent-must-exist, file/directory distinction, immutable
no-replace publication, and posix-rename-equivalent atomic replace.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import PurePosixPath
from types import MappingProxyType

from civ4_turn_relay.domain.errors import DomainValidationError
from civ4_turn_relay.domain.paths import validate_remote_relative_path
from civ4_turn_relay.storage.errors import (
    StorageAlreadyExistsError,
    StorageCapabilityError,
    StorageError,
    StorageInvalidPathError,
    StorageNotEmptyError,
    StorageNotFoundError,
    StorageTransportError,
    StorageWrongKindError,
)
from civ4_turn_relay.storage.faults import FaultController, FaultMoment, StorageOp
from civ4_turn_relay.storage.port import (
    StorageCapabilities,
    StorageEntry,
    StorageEntryKind,
)

_DEFAULT_CAPABILITIES = StorageCapabilities(
    exclusive_mkdir=True,
    atomic_replace=True,
    atomic_publish_no_replace=True,
    complete_readback=True,
)


@dataclass(frozen=True, slots=True)
class StorageSnapshot:
    """Immutable copy of fake storage state for safe test inspection."""

    directories: frozenset[str]
    files: MappingProxyType[str, bytes]


class FakeStorage:
    """In-memory :class:`~civ4_turn_relay.storage.port.Storage` with faults."""

    def __init__(
        self,
        *,
        capabilities: StorageCapabilities | None = None,
    ) -> None:
        self._capabilities = (
            capabilities if capabilities is not None else _DEFAULT_CAPABILITIES
        )
        self._directories: set[str] = set()
        self._files: dict[str, bytes] = {}
        self._faults = FaultController()

    @property
    def faults(self) -> FaultController:
        """Fake-only deterministic failure-injection controls."""
        return self._faults

    def capabilities(self) -> StorageCapabilities:
        return self._capabilities

    def snapshot(self) -> StorageSnapshot:
        """Return an immutable copy of the current tree (no shared mutables)."""
        files = {path: bytes(content) for path, content in self._files.items()}
        return StorageSnapshot(
            directories=frozenset(self._directories),
            files=MappingProxyType(files),
        )

    def mkdir(self, path: str) -> None:
        path = _require_path(path)
        self._require_capability("exclusive_mkdir", self._capabilities.exclusive_mkdir)
        with self._fault_scope(StorageOp.MKDIR, path=path):
            self._ensure_parent(path)
            if path in self._files:
                raise StorageWrongKindError("path is a file", path=path)
            if path in self._directories:
                raise StorageAlreadyExistsError("path already exists", path=path)
            self._directories.add(path)

    def write_file(self, path: str, data: bytes, *, overwrite: bool = False) -> None:
        path = _require_path(path)
        if not isinstance(data, bytes):
            raise TypeError("data must be bytes")
        with self._fault_scope(StorageOp.WRITE, path=path):
            self._ensure_parent(path)
            if path in self._directories:
                raise StorageWrongKindError("path is a directory", path=path)
            if path in self._files and not overwrite:
                raise StorageAlreadyExistsError("file already exists", path=path)
            self._files[path] = bytes(data)

    def read_file(self, path: str) -> bytes:
        path = _require_path(path)
        result: bytes | None = None
        with self._fault_scope(StorageOp.READ, path=path):
            if path in self._directories:
                raise StorageWrongKindError("path is a directory", path=path)
            if path not in self._files:
                raise StorageNotFoundError("file not found", path=path)
            if not self._capabilities.complete_readback:
                raise StorageCapabilityError(
                    "complete object read-back is unavailable", path=path
                )
            data = bytes(self._files[path])
            corrupted = self._faults.corrupt_read_payload(data)
            result = data if corrupted is None else corrupted
        assert result is not None
        return result

    def list_dir(self, path: str) -> tuple[StorageEntry, ...]:
        path = _require_path(path)
        result: tuple[StorageEntry, ...] | None = None
        with self._fault_scope(StorageOp.LIST, path=path):
            if path in self._files:
                raise StorageWrongKindError("path is a file", path=path)
            if path not in self._directories:
                raise StorageNotFoundError("directory not found", path=path)
            result = self._list_children(path)
        assert result is not None
        return result

    def remove_file(self, path: str) -> None:
        path = _require_path(path)
        with self._fault_scope(StorageOp.REMOVE_FILE, path=path):
            if path in self._directories:
                raise StorageWrongKindError("path is a directory", path=path)
            if path not in self._files:
                raise StorageNotFoundError("file not found", path=path)
            del self._files[path]

    def remove_dir(self, path: str) -> None:
        path = _require_path(path)
        with self._fault_scope(StorageOp.REMOVE_DIR, path=path):
            if path in self._files:
                raise StorageWrongKindError("path is a file", path=path)
            if path not in self._directories:
                raise StorageNotFoundError("directory not found", path=path)
            if self._list_children(path):
                raise StorageNotEmptyError("directory is not empty", path=path)
            self._directories.remove(path)

    def publish_no_replace(self, source: str, destination: str) -> None:
        source = _require_path(source)
        destination = _require_path(destination)
        self._require_capability(
            "atomic_publish_no_replace",
            self._capabilities.atomic_publish_no_replace,
        )
        with self._fault_scope(StorageOp.PUBLISH_NO_REPLACE, path=destination):
            self._require_file(source)
            self._ensure_parent(destination)
            if destination in self._directories:
                raise StorageWrongKindError(
                    "destination is a directory", path=destination
                )
            if destination in self._files:
                raise StorageAlreadyExistsError(
                    "destination already exists", path=destination
                )
            if source == destination:
                raise StorageAlreadyExistsError(
                    "destination already exists", path=destination
                )
            self._files[destination] = self._files.pop(source)

    def atomic_replace(self, source: str, destination: str) -> None:
        source = _require_path(source)
        destination = _require_path(destination)
        self._require_capability("atomic_replace", self._capabilities.atomic_replace)
        with self._fault_scope(StorageOp.ATOMIC_REPLACE, path=destination):
            self._require_file(source)
            self._ensure_parent(destination)
            if destination in self._directories:
                raise StorageWrongKindError(
                    "destination is a directory", path=destination
                )
            if source != destination:
                content = self._files.pop(source)
                self._files[destination] = content

    @contextmanager
    def _fault_scope(self, operation: StorageOp, *, path: str | None) -> Iterator[None]:
        if self._faults.begin(operation) is FaultMoment.BEFORE:
            raise StorageTransportError(
                f"injected failure before {operation.value}", path=path
            )
        try:
            yield
        except StorageError:
            self._faults.abort(operation)
            raise
        if self._faults.finish(operation):
            raise StorageTransportError(
                f"injected failure after {operation.value}", path=path
            )

    def _require_capability(self, name: str, available: bool) -> None:
        if not available:
            raise StorageCapabilityError(f"required capability unavailable: {name}")

    def _ensure_parent(self, path: str) -> None:
        parent = _parent_path(path)
        if parent is None:
            return
        if parent in self._files:
            raise StorageWrongKindError("parent path is a file", path=parent)
        if parent not in self._directories:
            raise StorageNotFoundError("parent directory not found", path=parent)

    def _require_file(self, path: str) -> None:
        if path in self._directories:
            raise StorageWrongKindError("path is a directory", path=path)
        if path not in self._files:
            raise StorageNotFoundError("file not found", path=path)

    def _list_children(self, path: str) -> tuple[StorageEntry, ...]:
        prefix = path + "/"
        children: dict[str, StorageEntryKind] = {}
        for directory in self._directories:
            if not directory.startswith(prefix):
                continue
            rest = directory[len(prefix) :]
            if rest and "/" not in rest:
                children[rest] = StorageEntryKind.DIRECTORY
        for file_path in self._files:
            if not file_path.startswith(prefix):
                continue
            rest = file_path[len(prefix) :]
            if rest and "/" not in rest:
                children[rest] = StorageEntryKind.FILE
        return tuple(
            StorageEntry(name=name, kind=children[name]) for name in sorted(children)
        )


def _require_path(path: str) -> str:
    try:
        return validate_remote_relative_path(path, field_path="path")
    except DomainValidationError as error:
        raise StorageInvalidPathError(error.message, path=None) from error


def _parent_path(path: str) -> str | None:
    parent = PurePosixPath(path).parent
    if str(parent) == ".":
        return None
    return parent.as_posix()
