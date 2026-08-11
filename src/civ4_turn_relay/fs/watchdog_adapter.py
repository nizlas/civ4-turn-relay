"""Watchdog-backed filesystem watcher with graceful degradation."""

from __future__ import annotations

from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING, Any

from civ4_turn_relay.fs.port import WatchCallback, WatchEvent

if TYPE_CHECKING:
    from watchdog.events import FileSystemEvent, FileSystemEventHandler
    from watchdog.observers import Observer as WatchdogObserver

    _WATCHDOG_AVAILABLE = True
else:
    try:
        from watchdog.events import FileSystemEvent, FileSystemEventHandler
        from watchdog.observers import Observer as WatchdogObserver

        _WATCHDOG_AVAILABLE = True
    except ImportError:  # pragma: no cover - exercised when dependency absent
        _WATCHDOG_AVAILABLE = False

        class FileSystemEvent:
            pass

        class FileSystemEventHandler:
            pass

        WatchdogObserver = None


class UnavailableWatcher:
    """Placeholder watcher when ``watchdog`` is not installed."""

    def start(self, root: str, on_event: WatchCallback) -> None:
        del root, on_event

    def stop(self) -> None:
        return None

    def is_healthy(self) -> bool:
        return False


def _event_path(src_path: object) -> str:
    if isinstance(src_path, bytes):
        text = src_path.decode("utf-8", errors="replace")
    else:
        text = str(src_path)
    return str(Path(text).resolve(strict=False))


if _WATCHDOG_AVAILABLE:

    class _CoalescingHandler(FileSystemEventHandler):  # type: ignore[misc, unused-ignore]
        def __init__(self, callback: WatchCallback) -> None:
            super().__init__()
            self._callback = callback
            self._pending: set[str] = set()
            self._lock = Lock()

        def on_any_event(self, event: FileSystemEvent) -> None:  # type: ignore[misc, unused-ignore]
            if getattr(event, "is_directory", False):
                return
            path = _event_path(getattr(event, "src_path", ""))
            with self._lock:
                self._pending.add(path)
            kind = str(getattr(event, "event_type", None) or "modified")
            self._callback(WatchEvent(path=path, kind=kind))

    class WatchdogWatcher:
        """Recursive ``watchdog`` observer for a PBEM directory."""

        def __init__(self) -> None:
            self._observer: Any = None
            self._healthy = False

        def start(self, root: str, on_event: WatchCallback) -> None:
            self.stop()
            handler = _CoalescingHandler(on_event)
            observer = WatchdogObserver()
            try:
                observer.schedule(handler, root, recursive=True)
                observer.start()
            except OSError:
                self._healthy = False
                self._observer = None
                return
            self._observer = observer
            self._healthy = True

        def stop(self) -> None:
            if self._observer is not None:
                try:
                    self._observer.stop()
                    self._observer.join(timeout=1.0)
                except Exception:
                    self._healthy = False
                self._observer = None

        def is_healthy(self) -> bool:
            if self._observer is None:
                return False
            return bool(self._healthy and self._observer.is_alive())

else:
    WatchdogWatcher = UnavailableWatcher  # type: ignore[misc, assignment]
