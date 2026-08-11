"""Filesystem watching with watchdog primary and polling fallback."""

from __future__ import annotations

from enum import Enum, unique

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
    "WatchFallbackReason",
    "WatchdogWatcher",
]


@unique
class WatchFallbackReason(Enum):
    """Typed reason a MatchMonitor is using polling instead of the primary."""

    NONE = "none"
    PRIMARY_UNAVAILABLE_AT_START = "primary_unavailable_at_start"
    PRIMARY_UNHEALTHY_AT_RUNTIME = "primary_unhealthy_at_runtime"


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
        self._fallback_reason = WatchFallbackReason.NONE

    @property
    def active_watcher(self) -> FilesystemWatcher | None:
        return self._active

    @property
    def fell_back_at_runtime(self) -> bool:
        return self._fallback_reason is WatchFallbackReason.PRIMARY_UNHEALTHY_AT_RUNTIME

    @property
    def fallback_reason(self) -> WatchFallbackReason:
        return self._fallback_reason

    def start(self, root: str, on_event: WatchCallback) -> None:
        self.stop()
        self._root = root
        self._callback = on_event
        self._fallback_reason = WatchFallbackReason.NONE
        if not isinstance(self._primary, UnavailableWatcher):
            try:
                self._primary.start(root, on_event)
                if self._primary.is_healthy():
                    self._active = self._primary
                    return
            except Exception:
                try:
                    self._primary.stop()
                except Exception:
                    pass
        self._fallback.start(root, on_event)
        self._active = self._fallback
        self._fallback_reason = WatchFallbackReason.PRIMARY_UNAVAILABLE_AT_START

    def stop(self) -> None:
        if self._active is not None:
            try:
                self._active.stop()
            except Exception:
                pass
        try:
            self._primary.stop()
        except Exception:
            pass
        try:
            self._fallback.stop()
        except Exception:
            pass
        self._active = None
        self._root = None
        self._callback = None
        self._fallback_reason = WatchFallbackReason.NONE

    def is_healthy(self) -> bool:
        if self._active is None:
            return False
        try:
            return self._active.is_healthy()
        except Exception:
            return False

    def poll(self) -> None:
        """Drive fallback polling; switch from an unhealthy primary at runtime."""
        if (
            self._active is self._primary
            and self._root is not None
            and self._callback is not None
            and not isinstance(self._primary, UnavailableWatcher)
            and self._fallback_reason is WatchFallbackReason.NONE
        ):
            healthy = True
            try:
                healthy = self._primary.is_healthy()
            except Exception:
                healthy = False
            if not healthy:
                try:
                    self._primary.stop()
                except Exception:
                    pass
                try:
                    self._fallback.start(self._root, self._callback)
                    self._active = self._fallback
                    self._fallback_reason = (
                        WatchFallbackReason.PRIMARY_UNHEALTHY_AT_RUNTIME
                    )
                except Exception:
                    self._active = None
                    return
        if isinstance(self._active, PollingWatcher):
            self._active.poll()
