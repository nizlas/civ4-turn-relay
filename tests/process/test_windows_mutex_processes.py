"""Windows-only cross-process named-mutex tests (separate processes, not threads)."""

from __future__ import annotations

import multiprocessing
import sys
import uuid
from typing import Any

import pytest

from civ4_turn_relay.process.guard import (
    GuardAcquireOutcome,
    GuardedLaunchOutcome,
    ProcessScanEntry,
    launch_guard_name,
)
from civ4_turn_relay.process.launch_config import CivLaunchCommand
from civ4_turn_relay.process.windows import (
    CtypesWin32MutexApi,
    WindowsNamedMutexLaunchGuard,
    WindowsProcessSupervisor,
)
from tests.process._mutex_child import acquire_and_signal
from tests.process.test_windows_supervisor import ScriptedBackend, _running_info

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="requires a real Windows named mutex across processes",
)

_CHILD_JOIN_SECONDS = 15.0
_READY_SECONDS = 10.0


def _unique_exe() -> str:
    return f"C:\\Games\\Civ4\\relay-mutex-test-{uuid.uuid4().hex}\\Civ4BeyondSword.exe"


def _spawn_child(
    exe: str,
    *,
    mode: str,
    wait_timeout_ms: int = 0,
) -> tuple[Any, Any, Any, Any]:
    ctx = multiprocessing.get_context("spawn")
    ready = ctx.Event()
    proceed = ctx.Event()
    queue: multiprocessing.queues.Queue[dict[str, object]] = ctx.Queue()
    process = ctx.Process(
        target=acquire_and_signal,
        args=(exe, wait_timeout_ms, ready, proceed, queue, mode),
    )
    process.start()
    return process, ready, proceed, queue


def _stop_child(process: Any, proceed: Any) -> None:
    proceed.set()
    process.join(timeout=_CHILD_JOIN_SECONDS)
    if process.is_alive():
        process.terminate()
        process.join(timeout=5)
    if process.is_alive():
        process.kill()
        process.join(timeout=5)


def test_child_owner_makes_parent_busy_then_acquirable_after_release() -> None:
    exe = _unique_exe()
    child, ready, proceed, queue = _spawn_child(exe, mode="release")
    try:
        assert ready.wait(timeout=_READY_SECONDS)
        child_result = queue.get(timeout=5)
        assert child_result["outcome"] == GuardAcquireOutcome.ACQUIRED.value
        assert isinstance(child_result["handle"], int)
        assert child_result["handle"] > 0

        parent = WindowsNamedMutexLaunchGuard(wait_timeout_ms=0)
        busy = parent.acquire(exe)
        assert busy.outcome is GuardAcquireOutcome.BUSY

        proceed.set()
        child.join(timeout=_CHILD_JOIN_SECONDS)
        assert child.exitcode == 0

        acquired = parent.acquire(exe)
        assert acquired.outcome is GuardAcquireOutcome.ACQUIRED
        assert isinstance(acquired.handle, int)
        assert acquired.handle > 0
        parent.release(acquired)
    finally:
        _stop_child(child, proceed)


def test_child_crash_yields_abandoned_then_parent_still_scans() -> None:
    exe = _unique_exe()
    # Keep a handle open in this process so the named mutex object is not
    # destroyed when the child exits. Otherwise the next CreateMutexW would
    # create a fresh unowned mutex and return WAIT_OBJECT_0 instead of
    # WAIT_ABANDONED.
    api = CtypesWin32MutexApi()
    keepalive = api.create_mutex_w(launch_guard_name(exe))
    assert keepalive is not None
    child, ready, proceed, queue = _spawn_child(exe, mode="crash")
    try:
        assert ready.wait(timeout=_READY_SECONDS)
        child_result = queue.get(timeout=5)
        assert child_result["outcome"] == GuardAcquireOutcome.ACQUIRED.value
        assert isinstance(child_result["handle"], int)
        assert child_result["handle"] > 0

        proceed.set()
        child.join(timeout=_CHILD_JOIN_SECONDS)
        assert child.exitcode != 0

        backend = ScriptedBackend()
        backend.scan_entries = (
            ProcessScanEntry(pid=777, executable_path=exe, name=None),
        )
        backend.info = _running_info()
        supervisor = WindowsProcessSupervisor(
            backend=backend,
            platform="win32",
            sleep_fn=lambda _seconds: None,
            guard=WindowsNamedMutexLaunchGuard(wait_timeout_ms=5000),
        )
        result = supervisor.guarded_launch(
            CivLaunchCommand(argv=(exe, "mod=Mods\\AdvCiv"), working_directory=None)
        )
        assert result.outcome is GuardedLaunchOutcome.EXISTING_CIV_DETECTED
        assert result.recovered_abandoned_guard is True
        assert "spawn" not in backend.call_names()
    finally:
        _stop_child(child, proceed)
        api.close_handle(keepalive)


def test_pointer_sized_handle_survives_cross_process_acquire() -> None:
    exe = _unique_exe()
    guard = WindowsNamedMutexLaunchGuard(wait_timeout_ms=0)
    acquired = guard.acquire(exe)
    try:
        assert acquired.outcome is GuardAcquireOutcome.ACQUIRED
        assert isinstance(acquired.handle, int)
        assert acquired.handle > 0
        # A truncated signed 32-bit HANDLE would be negative for high values;
        # a truncated unsigned 32-bit value would not round-trip as this int.
        assert acquired.handle.bit_length() <= 64
    finally:
        if acquired.held:
            guard.release(acquired)
