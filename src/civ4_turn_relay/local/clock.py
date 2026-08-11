"""Injectable clock port for stability sampling and polling (no wall-clock I/O)."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Clock(Protocol):
    """Monotonic or wall time source for pure local logic."""

    def now(self) -> float:
        """Return current time in seconds (float)."""
        ...


class SystemClock:
    """Production clock backed by :func:`time.time`."""

    def now(self) -> float:
        import time

        return time.time()


class FakeClock:
    """Deterministic clock for tests; advance without sleeping."""

    def __init__(self, start: float = 0.0) -> None:
        if isinstance(start, bool) or not isinstance(start, int | float):
            raise TypeError("start must be a numeric timestamp in seconds")
        self._now = float(start)

    def now(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        if isinstance(seconds, bool) or not isinstance(seconds, int | float):
            raise TypeError("seconds must be numeric")
        if seconds < 0:
            raise ValueError("seconds must be non-negative")
        self._now += float(seconds)
