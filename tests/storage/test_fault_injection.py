"""Deterministic fake-only failure injection."""

from __future__ import annotations

import pytest

from civ4_turn_relay.storage import (
    FakeStorage,
    FaultMoment,
    StorageAlreadyExistsError,
    StorageNotFoundError,
    StorageOp,
    StorageTransportError,
)
from tests.storage.helpers import seed_tree


def test_before_effect_fault_leaves_state_unchanged(fake: FakeStorage) -> None:
    seed_tree(fake, "temporary")
    before = fake.snapshot()
    fake.faults.inject(StorageOp.WRITE, moment=FaultMoment.BEFORE, occurrence=1)
    with pytest.raises(StorageTransportError):
        fake.write_file("temporary/a.bin", b"payload")
    after = fake.snapshot()
    assert after.files == before.files
    assert after.directories == before.directories


def test_after_write_fault_leaves_written_bytes(fake: FakeStorage) -> None:
    seed_tree(fake, "temporary")
    fake.faults.inject(StorageOp.WRITE, moment=FaultMoment.AFTER, occurrence=1)
    with pytest.raises(StorageTransportError):
        fake.write_file("temporary/a.bin", b"temp-bytes")
    assert fake.snapshot().files["temporary/a.bin"] == b"temp-bytes"


def test_before_publication_fault_preserves_source_destination_absent(
    fake: FakeStorage,
) -> None:
    seed_tree(fake, "temporary", "saves")
    fake.write_file("temporary/op.upload", b"save-bytes")
    fake.faults.inject(
        StorageOp.PUBLISH_NO_REPLACE, moment=FaultMoment.BEFORE, occurrence=1
    )
    with pytest.raises(StorageTransportError):
        fake.publish_no_replace("temporary/op.upload", "saves/final.sav")
    snap = fake.snapshot()
    assert snap.files["temporary/op.upload"] == b"save-bytes"
    assert "saves/final.sav" not in snap.files


def test_after_publication_fault_leaves_destination(fake: FakeStorage) -> None:
    seed_tree(fake, "temporary", "saves")
    fake.write_file("temporary/op.upload", b"save-bytes")
    fake.faults.inject(
        StorageOp.PUBLISH_NO_REPLACE, moment=FaultMoment.AFTER, occurrence=1
    )
    with pytest.raises(StorageTransportError):
        fake.publish_no_replace("temporary/op.upload", "saves/final.sav")
    snap = fake.snapshot()
    assert "temporary/op.upload" not in snap.files
    assert snap.files["saves/final.sav"] == b"save-bytes"


def test_before_replace_fault_preserves_old_manifest(fake: FakeStorage) -> None:
    seed_tree(fake, "temporary")
    fake.write_file("manifest.json", b"old-manifest")
    fake.write_file("temporary/manifest-op.json", b"new-manifest")
    fake.faults.inject(
        StorageOp.ATOMIC_REPLACE, moment=FaultMoment.BEFORE, occurrence=1
    )
    with pytest.raises(StorageTransportError):
        fake.atomic_replace("temporary/manifest-op.json", "manifest.json")
    snap = fake.snapshot()
    assert snap.files["manifest.json"] == b"old-manifest"
    assert snap.files["temporary/manifest-op.json"] == b"new-manifest"


def test_after_replace_fault_leaves_new_manifest_despite_error(
    fake: FakeStorage,
) -> None:
    seed_tree(fake, "temporary")
    fake.write_file("manifest.json", b"old-manifest")
    fake.write_file("temporary/manifest-op.json", b"new-manifest")
    fake.faults.inject(StorageOp.ATOMIC_REPLACE, moment=FaultMoment.AFTER, occurrence=1)
    with pytest.raises(StorageTransportError):
        fake.atomic_replace("temporary/manifest-op.json", "manifest.json")
    snap = fake.snapshot()
    assert snap.files["manifest.json"] == b"new-manifest"
    assert "temporary/manifest-op.json" not in snap.files


def test_nth_occurrence_selection_is_deterministic(fake: FakeStorage) -> None:
    seed_tree(fake, "temporary")
    fake.faults.inject(StorageOp.WRITE, moment=FaultMoment.AFTER, occurrence=2)
    fake.write_file("temporary/one.bin", b"1")
    with pytest.raises(StorageTransportError):
        fake.write_file("temporary/two.bin", b"2")
    # Third write is unaffected once the one-shot fault is consumed.
    fake.write_file("temporary/three.bin", b"3")
    snap = fake.snapshot()
    assert snap.files["temporary/one.bin"] == b"1"
    assert snap.files["temporary/two.bin"] == b"2"
    assert snap.files["temporary/three.bin"] == b"3"


def test_one_shot_behavior_and_reset(fake: FakeStorage) -> None:
    seed_tree(fake, "temporary")
    fake.faults.inject(StorageOp.WRITE, moment=FaultMoment.BEFORE, occurrence=1)
    with pytest.raises(StorageTransportError):
        fake.write_file("temporary/a.bin", b"x")
    # Consumed: next write succeeds.
    fake.write_file("temporary/a.bin", b"x")
    fake.faults.inject(StorageOp.WRITE, moment=FaultMoment.BEFORE, occurrence=1)
    fake.faults.reset()
    assert fake.faults.pending_faults() == ()
    fake.write_file("temporary/b.bin", b"y")
    assert fake.read_file("temporary/b.bin") == b"y"


def test_mkdir_already_exists_still_available_without_fault(fake: FakeStorage) -> None:
    seed_tree(fake, "locks")
    fake.mkdir("locks/upload.lock")
    with pytest.raises(StorageAlreadyExistsError):
        fake.mkdir("locks/upload.lock")


def test_snapshot_is_immutable_copy(fake: FakeStorage) -> None:
    seed_tree(fake, "temporary")
    fake.write_file("temporary/a.bin", b"abc")
    snap = fake.snapshot()
    with pytest.raises(TypeError):
        snap.files["temporary/a.bin"] = b"mutated"  # type: ignore[index]
    # Mutating the live fake does not alter a prior snapshot.
    fake.write_file("temporary/a.bin", b"xyz", overwrite=True)
    assert snap.files["temporary/a.bin"] == b"abc"
    assert fake.snapshot().files["temporary/a.bin"] == b"xyz"


def test_before_mkdir_fault_creates_nothing(fake: FakeStorage) -> None:
    fake.faults.inject(StorageOp.MKDIR, moment=FaultMoment.BEFORE, occurrence=1)
    with pytest.raises(StorageTransportError):
        fake.mkdir("locks")
    assert fake.snapshot().directories == frozenset()
    with pytest.raises(StorageNotFoundError):
        fake.list_dir("locks")
