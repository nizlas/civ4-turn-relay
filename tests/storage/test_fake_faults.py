"""Fake-only failure injection and fault-occurrence lifecycle tests."""

from __future__ import annotations

import pytest

from civ4_turn_relay.storage import (
    FakeStorage,
    FaultMoment,
    FaultScheduleError,
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
    assert fake.faults.pending_faults() == ()

    # One-shot: previous schedule is gone; next call succeeds.
    fake.write_file("temporary/a.bin", b"x")
    assert fake.faults.call_count(StorageOp.WRITE) == 2

    # Occurrence 1 already passed; cannot reschedule it without reset.
    with pytest.raises(FaultScheduleError, match="already passed"):
        fake.faults.inject(StorageOp.WRITE, moment=FaultMoment.BEFORE, occurrence=1)

    # Schedule the next complete-call number instead.
    fake.faults.inject(StorageOp.WRITE, moment=FaultMoment.BEFORE, occurrence=3)
    with pytest.raises(StorageTransportError):
        fake.write_file("temporary/b.bin", b"y")
    assert fake.faults.pending_faults() == ()

    # Reset clears counters and schedules so occurrence 1 is valid again.
    fake.faults.reset()
    assert fake.faults.pending_faults() == ()
    assert fake.faults.call_count(StorageOp.WRITE) == 0
    fake.faults.inject(StorageOp.WRITE, moment=FaultMoment.BEFORE, occurrence=1)
    with pytest.raises(StorageTransportError):
        fake.write_file("temporary/c.bin", b"z")
    fake.write_file("temporary/c.bin", b"z")
    assert fake.read_file("temporary/c.bin") == b"z"


def test_ordinary_error_retires_scheduled_after_fault(fake: FakeStorage) -> None:
    """AFTER for call N must not linger when call N fails normally first."""
    seed_tree(fake, "temporary")
    fake.faults.inject(StorageOp.WRITE, moment=FaultMoment.AFTER, occurrence=1)
    with pytest.raises(StorageNotFoundError):
        # Parent missing → ordinary error after begin, before mutation.
        fake.write_file("missing/a.bin", b"x")
    assert fake.faults.pending_faults() == ()
    # Next WRITE is occurrence 2 and is not faulted.
    fake.write_file("temporary/a.bin", b"ok")
    assert fake.read_file("temporary/a.bin") == b"ok"


def test_before_and_after_same_occurrence_before_wins(fake: FakeStorage) -> None:
    seed_tree(fake, "temporary")
    fake.faults.inject(StorageOp.WRITE, moment=FaultMoment.BEFORE, occurrence=1)
    fake.faults.inject(StorageOp.WRITE, moment=FaultMoment.AFTER, occurrence=1)
    assert len(fake.faults.pending_faults()) == 2
    with pytest.raises(StorageTransportError):
        fake.write_file("temporary/a.bin", b"x")
    # BEFORE fired; AFTER for the same call was retired and never pending.
    assert fake.faults.pending_faults() == ()
    assert "temporary/a.bin" not in fake.snapshot().files
    fake.write_file("temporary/a.bin", b"x")
    assert fake.read_file("temporary/a.bin") == b"x"


def test_duplicate_schedule_rejected(fake: FakeStorage) -> None:
    fake.faults.inject(StorageOp.MKDIR, moment=FaultMoment.BEFORE, occurrence=1)
    with pytest.raises(FaultScheduleError):
        fake.faults.inject(StorageOp.MKDIR, moment=FaultMoment.BEFORE, occurrence=1)
    assert len(fake.faults.pending_faults()) == 1


def test_already_passed_occurrence_rejected(fake: FakeStorage) -> None:
    fake.mkdir("locks")
    assert fake.faults.call_count(StorageOp.MKDIR) == 1
    with pytest.raises(FaultScheduleError):
        fake.faults.inject(StorageOp.MKDIR, moment=FaultMoment.AFTER, occurrence=1)
    assert fake.faults.pending_faults() == ()
    fake.faults.inject(StorageOp.MKDIR, moment=FaultMoment.AFTER, occurrence=2)
    with pytest.raises(StorageTransportError):
        fake.mkdir("locks/upload.lock")
    assert "locks/upload.lock" in fake.snapshot().directories
    assert fake.faults.pending_faults() == ()


def test_pending_faults_after_ordinary_error_and_next_call(fake: FakeStorage) -> None:
    seed_tree(fake, "temporary")
    fake.write_file("temporary/exists.bin", b"1")
    fake.faults.inject(StorageOp.WRITE, moment=FaultMoment.AFTER, occurrence=2)
    with pytest.raises(StorageAlreadyExistsError):
        fake.write_file("temporary/exists.bin", b"2", overwrite=False)
    assert fake.faults.pending_faults() == ()
    fake.write_file("temporary/next.bin", b"3")
    assert fake.read_file("temporary/exists.bin") == b"1"
    assert fake.read_file("temporary/next.bin") == b"3"


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


def test_corrupted_injected_read_differs_while_stored_bytes_remain(
    fake: FakeStorage,
) -> None:
    seed_tree(fake, "saves")
    path = "saves/object.bin"
    payload = b"stored-intact"
    fake.write_file(path, payload)
    fake.faults.inject_read_corruption(occurrence=1)
    corrupted = fake.read_file(path)
    assert corrupted != payload
    assert fake.snapshot().files[path] == payload
    assert fake.read_file(path) == payload


def test_before_read_fault_leaves_state_unchanged(fake: FakeStorage) -> None:
    seed_tree(fake, "saves")
    fake.write_file("saves/object.bin", b"x")
    fake.faults.inject(StorageOp.READ, moment=FaultMoment.BEFORE, occurrence=1)
    with pytest.raises(StorageTransportError):
        fake.read_file("saves/object.bin")
    assert fake.snapshot().files["saves/object.bin"] == b"x"
