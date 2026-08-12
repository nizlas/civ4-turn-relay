"""Windows named-mutex ABI: pointer-sized HANDLE, close/release lifecycle.

These tests inject a fake Win32 backend so they run off Windows and so a
handle greater than ``0xffffffff`` would expose ctypes ``c_int`` truncation.
"""

from __future__ import annotations

import sys

import pytest

from civ4_turn_relay.process.guard import GuardAcquireOutcome, launch_guard_name
from civ4_turn_relay.process.windows import (
    WAIT_ABANDONED,
    WAIT_FAILED,
    WAIT_OBJECT_0,
    WAIT_TIMEOUT,
    CtypesWin32MutexApi,
    MutexReleaseError,
    WindowsNamedMutexLaunchGuard,
)

_EXE = "C:\\Games\\Civ4\\Civ4BeyondSword.exe"
_WIDE_HANDLE = 0x1_0000_00FF  # greater than 0xffffffff; truncation would drop this


class FakeWin32MutexApi:
    """Records pointer-sized handles and returns scripted Win32 results."""

    def __init__(self) -> None:
        self.create_handle: int | None = _WIDE_HANDLE
        self.wait_result = WAIT_OBJECT_0
        self.release_result = True
        self.close_result = True
        self.last_error = 0
        self.wait_error = 0
        self.close_error = 0
        self.release_error = 0
        self.calls: list[tuple[object, ...]] = []

    def create_mutex_w(self, name: str) -> int | None:
        self.calls.append(("CreateMutexW", name))
        return self.create_handle

    def wait_for_single_object(self, handle: int, timeout_ms: int) -> int:
        self.calls.append(("WaitForSingleObject", handle, timeout_ms))
        if self.wait_result == WAIT_FAILED and self.wait_error:
            self.last_error = self.wait_error
        return self.wait_result

    def release_mutex(self, handle: int) -> bool:
        self.calls.append(("ReleaseMutex", handle))
        if not self.release_result and self.release_error:
            self.last_error = self.release_error
        return self.release_result

    def close_handle(self, handle: int) -> bool:
        self.calls.append(("CloseHandle", handle))
        if not self.close_result:
            if self.close_error:
                self.last_error = self.close_error
        else:
            self.last_error = 0
        return self.close_result

    def get_last_error(self) -> int:
        return self.last_error

    def handles_for(self, name: str) -> list[int]:
        handles: list[int] = []
        for call in self.calls:
            if call[0] == name and len(call) > 1 and isinstance(call[1], int):
                handles.append(call[1])
        return handles


def _guard(
    api: FakeWin32MutexApi, *, timeout_ms: int = 250
) -> WindowsNamedMutexLaunchGuard:
    return WindowsNamedMutexLaunchGuard(wait_timeout_ms=timeout_ms, api=api)


def test_acquire_preserves_handle_wider_than_32_bits() -> None:
    api = FakeWin32MutexApi()
    result = _guard(api).acquire(_EXE)
    assert result.outcome is GuardAcquireOutcome.ACQUIRED
    assert result.handle == _WIDE_HANDLE
    assert api.handles_for("WaitForSingleObject") == [_WIDE_HANDLE]
    assert api.handles_for("CloseHandle") == []
    assert api.handles_for("ReleaseMutex") == []


def test_release_uses_the_full_handle_exactly_once() -> None:
    api = FakeWin32MutexApi()
    guard = _guard(api)
    acquired = guard.acquire(_EXE)
    guard.release(acquired)
    assert api.handles_for("ReleaseMutex") == [_WIDE_HANDLE]
    assert api.handles_for("CloseHandle") == [_WIDE_HANDLE]
    guard.release(acquired)
    assert api.handles_for("ReleaseMutex") == [_WIDE_HANDLE]
    assert api.handles_for("CloseHandle") == [_WIDE_HANDLE]


def test_busy_closes_the_full_handle_without_release() -> None:
    api = FakeWin32MutexApi()
    api.wait_result = WAIT_TIMEOUT
    result = _guard(api).acquire(_EXE)
    assert result.outcome is GuardAcquireOutcome.BUSY
    assert result.handle is None
    assert api.handles_for("WaitForSingleObject") == [_WIDE_HANDLE]
    assert api.handles_for("CloseHandle") == [_WIDE_HANDLE]
    assert api.handles_for("ReleaseMutex") == []


def test_wait_failed_closes_the_full_handle_without_release() -> None:
    api = FakeWin32MutexApi()
    api.wait_result = WAIT_FAILED
    api.wait_error = 5
    result = _guard(api).acquire(_EXE)
    assert result.outcome is GuardAcquireOutcome.UNAVAILABLE
    assert "error 5" in result.message
    assert api.handles_for("CloseHandle") == [_WIDE_HANDLE]
    assert api.handles_for("ReleaseMutex") == []


def test_wait_timeout_failed_close_is_unavailable_not_busy() -> None:
    api = FakeWin32MutexApi()
    api.wait_result = WAIT_TIMEOUT
    api.close_result = False
    api.close_error = 6
    result = _guard(api).acquire(_EXE)
    assert result.outcome is GuardAcquireOutcome.UNAVAILABLE
    assert "CloseHandle failed" in result.message
    assert "error 6" in result.message
    assert result.handle is None
    assert api.handles_for("CloseHandle") == [_WIDE_HANDLE]
    assert api.handles_for("ReleaseMutex") == []


def test_wait_failed_failed_close_reports_close_error() -> None:
    api = FakeWin32MutexApi()
    api.wait_result = WAIT_FAILED
    api.wait_error = 5
    api.close_result = False
    api.close_error = 6
    result = _guard(api).acquire(_EXE)
    assert result.outcome is GuardAcquireOutcome.UNAVAILABLE
    assert "CloseHandle failed" in result.message
    assert "error 6" in result.message
    assert "error 5" not in result.message
    assert api.handles_for("CloseHandle") == [_WIDE_HANDLE]
    assert api.handles_for("ReleaseMutex") == []


def test_abandoned_acquire_still_owns_the_full_handle() -> None:
    api = FakeWin32MutexApi()
    api.wait_result = WAIT_ABANDONED
    guard = _guard(api)
    result = guard.acquire(_EXE)
    assert result.outcome is GuardAcquireOutcome.ACQUIRED_ABANDONED
    assert result.handle == _WIDE_HANDLE
    assert api.handles_for("CloseHandle") == []
    guard.release(result)
    assert api.handles_for("ReleaseMutex") == [_WIDE_HANDLE]
    assert api.handles_for("CloseHandle") == [_WIDE_HANDLE]


def test_create_failure_does_not_wait_or_close() -> None:
    api = FakeWin32MutexApi()
    api.create_handle = None
    api.last_error = 5
    result = _guard(api).acquire(_EXE)
    assert result.outcome is GuardAcquireOutcome.UNAVAILABLE
    assert "error 5" in result.message
    assert api.handles_for("WaitForSingleObject") == []
    assert api.handles_for("CloseHandle") == []


def test_failed_release_is_not_silent_success() -> None:
    api = FakeWin32MutexApi()
    api.release_result = False
    api.last_error = 6
    guard = _guard(api)
    acquired = guard.acquire(_EXE)
    with pytest.raises(MutexReleaseError, match="ReleaseMutex failed"):
        guard.release(acquired)
    assert api.handles_for("CloseHandle") == [_WIDE_HANDLE]


def test_failed_close_after_release_is_not_silent_success() -> None:
    api = FakeWin32MutexApi()
    api.close_result = False
    api.last_error = 6
    guard = _guard(api)
    acquired = guard.acquire(_EXE)
    with pytest.raises(MutexReleaseError, match="CloseHandle failed"):
        guard.release(acquired)
    assert api.handles_for("ReleaseMutex") == [_WIDE_HANDLE]


def test_create_mutex_name_is_session_local_digest() -> None:
    api = FakeWin32MutexApi()
    _guard(api).acquire(_EXE)
    assert api.calls[0] == ("CreateMutexW", launch_guard_name(_EXE))
    assert str(api.calls[0][1]).startswith("Local\\civ4-turn-relay-launch-")


@pytest.mark.skipif(sys.platform != "win32", reason="requires real kernel32")
def test_ctypes_api_declares_pointer_sized_handle_restype() -> None:
    import ctypes
    from ctypes import wintypes

    api = CtypesWin32MutexApi()
    kernel32 = api._kernel32
    assert kernel32.CreateMutexW.restype is wintypes.HANDLE
    assert kernel32.WaitForSingleObject.restype is wintypes.DWORD
    assert kernel32.ReleaseMutex.restype is wintypes.BOOL
    assert kernel32.CloseHandle.restype is wintypes.BOOL
    assert kernel32.CreateMutexW.argtypes[2] is wintypes.LPCWSTR
    assert kernel32.WaitForSingleObject.argtypes[0] is wintypes.HANDLE
    assert ctypes.sizeof(wintypes.HANDLE) == ctypes.sizeof(ctypes.c_void_p)
