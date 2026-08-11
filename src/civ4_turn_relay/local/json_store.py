"""Atomic local JSON byte writes (temp + fsync + replace / no-replace).

Used by :class:`~civ4_turn_relay.local.store.LocalStore`. Small injectable
hooks support deterministic failure tests without a large FS framework.
"""

from __future__ import annotations

import os
import sys
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO

from civ4_turn_relay.local.errors import LocalStoreIOError

ReplaceFn = Callable[[str, str], None]
FsyncFn = Callable[[int], None]
UuidFactory = Callable[[], uuid.UUID]
PublishNoReplaceFn = Callable[[str, str], bool]


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


def publish_no_replace(source: str, destination: str) -> bool:
    """Publish ``source`` onto ``destination`` only when destination is absent.

    Returns ``True`` when published, ``False`` when destination already exists.
    Never overwrites an existing destination. Windows uses ``os.rename``
    (fails if destination exists). POSIX uses ``os.link`` then unlinks the
    source so a complete inode is published without replace-on-exist.
    """
    if Path(destination).exists():
        return False
    if sys.platform == "win32":
        try:
            os.rename(source, destination)
            return True
        except FileExistsError:
            return False
        except OSError as error:
            if Path(destination).exists():
                return False
            raise LocalStoreIOError(
                "failed to publish document without replace",
                path=destination,
            ) from error
    try:
        os.link(source, destination)
    except FileExistsError:
        return False
    except OSError as error:
        if Path(destination).exists():
            return False
        raise LocalStoreIOError(
            "failed to publish document without replace",
            path=destination,
        ) from error
    try:
        os.unlink(source)
    except OSError:
        pass
    return True


def atomic_publish_no_replace_bytes(
    path: Path,
    data: bytes,
    *,
    fsync_fn: FsyncFn = os.fsync,
    uuid_factory: UuidFactory = uuid.uuid4,
    publish_fn: PublishNoReplaceFn = publish_no_replace,
) -> bool:
    """Write+fsync a temp file, then publish with no-replace semantics.

    Returns ``True`` when this caller published ``path``. Returns ``False``
    when another complete destination already won. A crash before successful
    publication leaves no corrupt destination; owned temps are cleaned when
    safely possible and never become authoritative.
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

    if path.exists():
        return False

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
        return publish_fn(str(temporary), str(path))
    finally:
        if temporary.exists():
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


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
