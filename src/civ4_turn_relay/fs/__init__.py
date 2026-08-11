"""Filesystem watching with watchdog primary and polling fallback."""

from __future__ import annotations

from civ4_turn_relay.fs.poller import PollingWatcher
from civ4_turn_relay.fs.port import FilesystemWatcher, WatchCallback, WatchEvent
from civ4_turn_relay.fs.watchdog_adapter import UnavailableWatcher, WatchdogWatcher
from civ4_turn_relay.local.clock import Clock

__all__ = [
    "FilesystemWatcher",
    "MatchMonitor",
    "PollingWatcher",
    "UnavailableWatcher",
    "WatchCallback",
    "WatchEvent",
    "WatchdogWatcher",
]


class MatchMonitor:
    """Watch one match PBEM directory with fallback polling."""

    def __init__(
        self,
        *,
        clock: Clock,
        poll_interval_seconds: float,
        primary: FilesystemWatcher | None = None,
        fallback: PollingWatcher | None = None,
    ) -> None:
        self._primary = primary if primary is not None else WatchdogWatcher()
        self._fallback = fallback or PollingWatcher(
            clock=clock,
            poll_interval_seconds=poll_interval_seconds,
        )
        self._active: FilesystemWatcher | None = None
        self._root: str | None = None
        self._callback: WatchCallback | None = None

    @property
    def active_watcher(self) -> FilesystemWatcher | None:
        return self._active

    def start(self, root: str, on_event: WatchCallback) -> None:
        self.stop()
        self._root = root
        self._callback = on_event
        if not isinstance(self._primary, UnavailableWatcher):
            try:
                self._primary.start(root, on_event)
                if self._primary.is_healthy():
                    self._active = self._primary
                    return
            except Exception:
                self._primary.stop()
        self._fallback.start(root, on_event)
        self._active = self._fallback

    def stop(self) -> None:
        if self._active is not None:
            self._active.stop()
        self._primary.stop()
        self._fallback.stop()
        self._active = None
        self._root = None
        self._callback = None

    def is_healthy(self) -> bool:
        if self._active is None:
            return False
        return self._active.is_healthy()

    def poll(self) -> None:
        """Drive fallback polling when the active watcher is a poller."""
        if isinstance(self._active, PollingWatcher):
            self._active.poll()
