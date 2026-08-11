"""Atomic local JSON byte writes (temp + fsync + replace).

Used by :class:`~civ4_turn_relay.local.store.LocalStore`. Small injectable
hooks support deterministic failure tests without a large FS framework.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO

from civ4_turn_relay.local.errors import LocalStoreIOError

ReplaceFn = Callable[[str, str], None]
FsyncFn = Callable[[int], None]
UuidFactory = Callable[[], uuid.UUID]


def atomic_write_bytes(
    path: Path,
    data: bytes,
    *,
    replace_fn: ReplaceFn = os.replace,
    fsync_fn: FsyncFn = os.fsync,
    uuid_factory: UuidFactory = uuid.uuid4,
) -> None:
    """Atomically replace ``path`` with ``data``.

    Writes a uniquely named temporary file in the same directory, flushes and
    fsyncs it, then replaces the destination. The previous destination remains
    when writing/syncing/replacement fails. Only the owned temporary file is
    cleaned up.
    """
    if not isinstance(data, bytes):
        raise TypeError("data must be bytes")
    parent = path.parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise LocalStoreIOError(
            "failed to create parent directories",
            path=str(path),
        ) from error

    temporary = parent / f".{path.name}.{uuid_factory().hex}.tmp"
    try:
        try:
            with temporary.open("wb") as handle:
                _write_fsync(handle, data, fsync_fn=fsync_fn)
        except OSError as error:
            raise LocalStoreIOError(
                "failed to write temporary document",
                path=str(temporary),
            ) from error
        try:
            replace_fn(str(temporary), str(path))
        except OSError as error:
            raise LocalStoreIOError(
                "failed to replace destination document",
                path=str(path),
            ) from error
    finally:
        if temporary.exists():
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def exclusive_create_bytes(
    path: Path,
    data: bytes,
    *,
    fsync_fn: FsyncFn = os.fsync,
) -> bool:
    """Create ``path`` exclusively with ``data``.

    Returns ``True`` when this caller created the file. Returns ``False`` when
    the destination already existed (another writer won the race). Raises
    :class:`LocalStoreIOError` on other filesystem failures. A partial create
    is removed when writing fails after exclusive open.
    """
    if not isinstance(data, bytes):
        raise TypeError("data must be bytes")
    parent = path.parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise LocalStoreIOError(
            "failed to create parent directories",
            path=str(path),
        ) from error

    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    try:
        fd = os.open(str(path), flags)
    except FileExistsError:
        return False
    except OSError as error:
        raise LocalStoreIOError(
            "failed to exclusively create document",
            path=str(path),
        ) from error

    try:
        with os.fdopen(fd, "wb") as handle:
            _write_fsync(handle, data, fsync_fn=fsync_fn)
    except OSError as error:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        raise LocalStoreIOError(
            "failed to write exclusively created document",
            path=str(path),
        ) from error
    except Exception:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return True


def _write_fsync(handle: BinaryIO, data: bytes, *, fsync_fn: FsyncFn) -> None:
    handle.write(data)
    handle.flush()
    fsync_fn(handle.fileno())


class AtomicJsonStore:
    """Read and atomically replace a single file on disk."""

    def __init__(
        self,
        path: Path | str,
        *,
        replace_fn: ReplaceFn = os.replace,
        fsync_fn: FsyncFn = os.fsync,
        uuid_factory: UuidFactory = uuid.uuid4,
    ) -> None:
        resolved = Path(path)
        if resolved.exists() and resolved.is_dir():
            raise LocalStoreIOError(
                "expected a file path, not a directory",
                path=str(resolved),
            )
        self._path = resolved
        self._replace_fn = replace_fn
        self._fsync_fn = fsync_fn
        self._uuid_factory = uuid_factory

    @property
    def path(self) -> Path:
        return self._path

    def exists(self) -> bool:
        return self._path.is_file()

    def read_bytes(self) -> bytes | None:
        if not self._path.is_file():
            return None
        try:
            return self._path.read_bytes()
        except OSError as error:
            raise LocalStoreIOError(
                "failed to read document",
                path=str(self._path),
            ) from error

    def write_bytes(self, data: bytes) -> None:
        atomic_write_bytes(
            self._path,
            data,
            replace_fn=self._replace_fn,
            fsync_fn=self._fsync_fn,
            uuid_factory=self._uuid_factory,
        )
