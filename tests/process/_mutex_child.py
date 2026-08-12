"""Spawn-safe child helpers for Windows named-mutex cross-process tests."""

from __future__ import annotations

import os
from typing import Any

from civ4_turn_relay.process.windows import WindowsNamedMutexLaunchGuard


def acquire_and_signal(
    executable_path: str,
    wait_timeout_ms: int,
    ready: Any,
    proceed: Any,
    result_queue: Any,
    mode: str,
) -> None:
    """Acquire the named mutex, report the outcome, then release or crash.

    ``mode`` is ``release`` (wait, then release) or ``crash`` (wait, then
    ``os._exit(1)`` without releasing so the OS reports abandonment).
    """
    guard = WindowsNamedMutexLaunchGuard(wait_timeout_ms=wait_timeout_ms)
    acquisition = guard.acquire(executable_path)
    result_queue.put(
        {
            "outcome": acquisition.outcome.value,
            "handle": acquisition.handle,
            "held": acquisition.held,
        }
    )
    ready.set()
    proceed.wait(timeout=20)
    if mode == "crash":
        os._exit(1)
    if acquisition.held:
        guard.release(acquisition)
