"""Filesystem watch port and event types."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class WatchEvent:
    """One filesystem notification under a watched root."""

    path: str
    kind: str


WatchCallback = Callable[[WatchEvent], None]


@runtime_checkable
class FilesystemWatcher(Protocol):
    """Adapter port for filesystem change notifications."""

    def start(self, root: str, on_event: WatchCallback) -> None:
        """Begin watching ``root``; invoke ``on_event`` for changes."""
        ...

    def stop(self) -> None:
        """Stop watching and release resources."""
        ...

    def is_healthy(self) -> bool:
        """Return whether the watcher is operating normally."""
        ...
