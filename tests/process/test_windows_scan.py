"""RealWindowsBackend scan boundary: AccessDenied likely-Civ entries survive."""

from __future__ import annotations

from collections.abc import Callable, Iterable

import psutil

from civ4_turn_relay.process.guard import MachineScanOutcome, classify_scan_entries
from civ4_turn_relay.process.windows import RealWindowsBackend

_EXE = "C:\\Games\\Civ4\\Civ4BeyondSword.exe"


class _FakeProcess:
    def __init__(self, pid: int, info: dict[str, object]) -> None:
        self.pid = pid
        self.info = info


class _VanishingProcess:
    pid = 50

    @property
    def info(self) -> dict[str, object]:
        raise psutil.NoSuchProcess(pid=50)


def _iter_from(processes: tuple[object, ...]) -> Callable[..., Iterable[object]]:
    def iterator(
        *args: object,
        attrs: object = None,
        ad_value: object = "missing",
        **kwargs: object,
    ) -> Iterable[object]:
        assert ad_value is None
        assert attrs == ["pid", "name", "exe"]
        yield from processes

    return iterator


def test_inaccessible_likely_civ_is_preserved_as_nameless_path() -> None:
    processes = (
        _FakeProcess(4, {"pid": 4, "name": "System", "exe": None}),
        _FakeProcess(321, {"pid": 321, "name": "Civ4BeyondSword.exe", "exe": None}),
        _FakeProcess(
            9, {"pid": 9, "name": "notepad.exe", "exe": "C:\\Windows\\notepad.exe"}
        ),
    )
    backend = RealWindowsBackend(process_iter=_iter_from(processes))
    entries = backend.iter_process_entries()
    by_pid = {entry.pid: entry for entry in entries}
    assert by_pid[4].executable_path is None
    assert by_pid[4].name == "System"
    assert by_pid[321].executable_path is None
    assert by_pid[321].name == "Civ4BeyondSword.exe"
    assert by_pid[9].executable_path == "C:\\Windows\\notepad.exe"

    classified = classify_scan_entries(entries, executable_path=_EXE)
    assert classified.outcome is MachineScanOutcome.INDETERMINATE
    assert classified.pid == 321


def test_inaccessible_unrelated_process_does_not_block() -> None:
    processes = (
        _FakeProcess(4, {"pid": 4, "name": "System", "exe": None}),
        _FakeProcess(8, {"pid": 8, "name": "svchost.exe", "exe": None}),
        _FakeProcess(11, {"pid": 11, "name": None, "exe": None}),
    )
    backend = RealWindowsBackend(process_iter=_iter_from(processes))
    classified = classify_scan_entries(
        backend.iter_process_entries(), executable_path=_EXE
    )
    assert classified.outcome is MachineScanOutcome.NO_MATCH


def test_vanishing_process_is_omitted() -> None:
    processes = (
        _VanishingProcess(),
        _FakeProcess(12, {"pid": 12, "name": "okay.exe", "exe": "C:\\okay.exe"}),
    )
    backend = RealWindowsBackend(process_iter=_iter_from(processes))
    entries = backend.iter_process_entries()
    assert [entry.pid for entry in entries] == [12]


def test_exact_path_match_still_blocks_after_scan() -> None:
    processes = (
        _FakeProcess(4, {"pid": 4, "name": "System", "exe": None}),
        _FakeProcess(
            700,
            {"pid": 700, "name": "Civ4BeyondSword.exe", "exe": _EXE.upper()},
        ),
    )
    backend = RealWindowsBackend(process_iter=_iter_from(processes))
    classified = classify_scan_entries(
        backend.iter_process_entries(), executable_path=_EXE
    )
    assert classified.outcome is MachineScanOutcome.EXACT_MATCH
    assert classified.pid == 700
