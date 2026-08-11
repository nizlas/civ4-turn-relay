"""Low-level atomic byte writer used by LocalStore."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from civ4_turn_relay.local import AtomicJsonStore, LocalStoreIOError, atomic_write_bytes


def test_atomic_write_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "doc.json"
    atomic_write_bytes(path, b'{\n  "a": 1\n}\n')
    assert path.read_bytes() == b'{\n  "a": 1\n}\n'
    assert list(tmp_path.glob(".doc.json.*.tmp")) == []


def test_replace_failure_keeps_destination(tmp_path: Path) -> None:
    path = tmp_path / "doc.json"
    path.write_bytes(b"good")

    def boom(src: str, dst: str) -> None:
        raise OSError("no replace")

    with pytest.raises(LocalStoreIOError):
        atomic_write_bytes(path, b"bad", replace_fn=boom)
    assert path.read_bytes() == b"good"


def test_atomic_json_store_read_write(tmp_path: Path) -> None:
    store = AtomicJsonStore(tmp_path / "nested" / "x.json")
    assert store.read_bytes() is None
    store.write_bytes(b'{"ok": true}\n')
    assert store.read_bytes() == b'{"ok": true}\n'


def test_fsync_failure_does_not_replace(tmp_path: Path) -> None:
    path = tmp_path / "doc.json"
    path.write_bytes(b"prior")

    def boom_fsync(fd: int) -> None:
        raise OSError("fsync failed")

    with pytest.raises(LocalStoreIOError):
        atomic_write_bytes(path, b"next", fsync_fn=boom_fsync)
    assert path.read_bytes() == b"prior"
    # Owned temps cleaned when possible.
    assert list(tmp_path.glob(".doc.json.*.tmp")) == []


def test_directory_target_rejected(tmp_path: Path) -> None:
    with pytest.raises(LocalStoreIOError):
        AtomicJsonStore(tmp_path)


def test_successful_replace_uses_os_replace(tmp_path: Path) -> None:
    path = tmp_path / "doc.json"
    seen: list[tuple[str, str]] = []

    def tracking_replace(src: str, dst: str) -> None:
        seen.append((src, dst))
        os.replace(src, dst)

    atomic_write_bytes(path, b"data\n", replace_fn=tracking_replace)
    assert len(seen) == 1
    assert seen[0][1] == str(path)
