"""Polling filesystem watcher fallback."""

from __future__ import annotations

from pathlib import Path

from civ4_turn_relay.fs.port import FilesystemWatcher, WatchCallback, WatchEvent
from civ4_turn_relay.local.clock import Clock


class PollingWatcher:
    """Tracks mtimes and emits events when files change."""

    def __init__(
        self,
        *,
        clock: Clock,
        poll_interval_seconds: float,
    ) -> None:
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")
        self._clock = clock
        self._poll_interval_seconds = poll_interval_seconds
        self._root: Path | None = None
        self._callback: WatchCallback | None = None
        self._mtimes: dict[str, float] = {}
        self._last_poll = 0.0
        self._healthy = True

    def start(self, root: str, on_event: WatchCallback) -> None:
        self._root = Path(root)
        self._callback = on_event
        self._mtimes = {}
        self._last_poll = self._clock.now()
        self._healthy = True
        self._scan(initial=True)

    def stop(self) -> None:
        self._root = None
        self._callback = None
        self._mtimes = {}
        self._healthy = True

    def is_healthy(self) -> bool:
        return self._healthy

    def poll(self) -> None:
        """Advance one polling cycle when ``now - last_poll >= interval``."""
        if self._root is None or self._callback is None:
            return
        now = self._clock.now()
        if now - self._last_poll < self._poll_interval_seconds:
            return
        self._last_poll = now
        self._scan(initial=False)

    def _scan(self, *, initial: bool) -> None:
        assert self._root is not None
        assert self._callback is not None
        current: dict[str, float] = {}
        try:
            for path in self._root.rglob("*"):
                if not path.is_file() or path.is_symlink():
                    continue
                key = str(path.resolve(strict=False))
                try:
                    mtime = path.stat().st_mtime
                except OSError:
                    self._healthy = False
                    continue
                current[key] = mtime
                previous = self._mtimes.get(key)
                if previous is None:
                    if not initial:
                        self._callback(WatchEvent(path=key, kind="created"))
                elif mtime != previous:
                    self._callback(WatchEvent(path=key, kind="modified"))
            for removed in set(self._mtimes) - set(current):
                self._callback(WatchEvent(path=removed, kind="deleted"))
            self._mtimes = current
        except OSError:
            self._healthy = False


def satisfies_filesystem_watcher(obj: object) -> bool:
    return isinstance(obj, FilesystemWatcher)
