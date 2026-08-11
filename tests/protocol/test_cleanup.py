"""Temporary-orphan cleanup tests (PT-40)."""

from __future__ import annotations

import pytest

from civ4_turn_relay.domain import sha256_hex
from civ4_turn_relay.protocol import (
    GamePaths,
    HandoffRequest,
    TemporaryCandidateAction,
    TemporaryCleanupOutcome,
    acquire_or_resume_upload_lock,
    cleanup_temporary_orphans,
    commit_handoff,
    inspect_upload_lock,
)
from civ4_turn_relay.protocol.lock import LockInspectionKind
from civ4_turn_relay.storage import FakeStorage, FaultMoment, StorageOp
from tests.protocol.helpers import (
    CLIENT_A,
    NOW_UTC,
    OP_ID,
    OP_ID_2,
    SAVE_NAME,
    initialize_ready_match,
)

SAVE_A = b"synthetic-outgoing-save-bytes-player-a-v1"


def _manifest_bytes(storage: FakeStorage, game_id: str) -> bytes:
    return storage.read_file(GamePaths(game_id).manifest)


@pytest.mark.pt("PT-40")
def test_pt40_orphan_temp_cleanup_leaves_manifest_bytes_unchanged() -> None:
    storage, journal, game_id = initialize_ready_match()
    commit_handoff(
        storage,
        HandoffRequest(
            game_id=game_id,
            local_player_id="player_a",
            client_id=CLIENT_A,
            operation_id=OP_ID,
            outgoing_bytes=SAVE_A,
            original_filename=SAVE_NAME,
            now_utc=NOW_UTC,
        ),
        journal=journal,
    )
    before = _manifest_bytes(storage, game_id)
    paths = GamePaths(game_id)
    orphan = paths.temporary_upload(OP_ID_2, ".CivBeyondSwordSave")
    storage.write_file(orphan, b"orphan-temp-bytes", overwrite=False)

    result = cleanup_temporary_orphans(
        storage,
        game_id=game_id,
        candidates=(f"temporary/{OP_ID_2}.upload.CivBeyondSwordSave",),
    )
    assert result.outcome is TemporaryCleanupOutcome.COMPLETED
    assert result.items[0].action is TemporaryCandidateAction.REMOVED
    assert orphan not in storage.snapshot().files
    assert _manifest_bytes(storage, game_id) == before
    read_after = storage.read_file(paths.manifest)
    assert read_after == before


@pytest.mark.pt("PT-40")
def test_pt40_active_operation_temps_are_protected() -> None:
    storage, journal, game_id = initialize_ready_match()
    before = _manifest_bytes(storage, game_id)
    paths = GamePaths(game_id)
    acquire_or_resume_upload_lock(
        storage,
        game_id=game_id,
        operation_id=OP_ID,
        client_id=CLIENT_A,
        player_id="player_a",
        now_utc=NOW_UTC,
        journal=journal,
        sha256=sha256_hex(SAVE_A),
    )
    protected = paths.temporary_upload(OP_ID, ".CivBeyondSwordSave")
    orphan = paths.temporary_upload(OP_ID_2, ".CivBeyondSwordSave")
    storage.write_file(protected, b"active-op-temp", overwrite=False)
    storage.write_file(orphan, b"orphan-temp", overwrite=False)

    result = cleanup_temporary_orphans(
        storage,
        game_id=game_id,
        candidates=(
            f"temporary/{OP_ID}.upload.CivBeyondSwordSave",
            f"temporary/{OP_ID_2}.upload.CivBeyondSwordSave",
        ),
    )
    assert result.outcome is TemporaryCleanupOutcome.COMPLETED
    actions = {item.candidate: item.action for item in result.items}
    assert (
        actions[f"temporary/{OP_ID}.upload.CivBeyondSwordSave"]
        is TemporaryCandidateAction.PROTECTED
    )
    assert (
        actions[f"temporary/{OP_ID_2}.upload.CivBeyondSwordSave"]
        is TemporaryCandidateAction.REMOVED
    )
    assert protected in storage.snapshot().files
    assert orphan not in storage.snapshot().files
    assert _manifest_bytes(storage, game_id) == before


def test_cleanup_refuses_path_traversal_and_non_temporary() -> None:
    storage, _journal, game_id = initialize_ready_match()
    before = _manifest_bytes(storage, game_id)
    result = cleanup_temporary_orphans(
        storage,
        game_id=game_id,
        candidates=(
            "saves/000001_deadbeefcafe.CivBeyondSwordSave",
            "../other/temporary/x.bin",
            "temporary/../manifest.json",
        ),
    )
    assert result.outcome is TemporaryCleanupOutcome.PATH_VIOLATION
    assert all(
        item.action is TemporaryCandidateAction.PATH_VIOLATION for item in result.items
    )
    assert _manifest_bytes(storage, game_id) == before
    assert GamePaths(game_id).manifest in storage.snapshot().files


def test_cleanup_ambiguous_lock_refuses_all_candidates() -> None:
    storage, _journal, game_id = initialize_ready_match()
    before = _manifest_bytes(storage, game_id)
    paths = GamePaths(game_id)
    storage.mkdir(paths.upload_lock_dir)
    orphan = paths.temporary_upload(OP_ID_2, ".CivBeyondSwordSave")
    storage.write_file(orphan, b"orphan", overwrite=False)
    assert inspect_upload_lock(storage, game_id).kind is (
        LockInspectionKind.MISSING_LOCK_JSON
    )

    result = cleanup_temporary_orphans(
        storage,
        game_id=game_id,
        candidates=(f"temporary/{OP_ID_2}.upload.CivBeyondSwordSave",),
    )
    assert result.outcome is TemporaryCleanupOutcome.LOCK_UNSAFE
    assert result.items[0].action is TemporaryCandidateAction.SKIPPED
    assert orphan in storage.snapshot().files
    assert _manifest_bytes(storage, game_id) == before


def test_cleanup_wrong_kind_and_missing_are_typed() -> None:
    storage, _journal, game_id = initialize_ready_match()
    paths = GamePaths(game_id)
    dir_path = f"{paths.temporary}/not-a-file"
    storage.mkdir(dir_path)
    result = cleanup_temporary_orphans(
        storage,
        game_id=game_id,
        candidates=(
            "temporary/not-a-file",
            "temporary/already-gone.bin",
        ),
    )
    assert result.outcome is TemporaryCleanupOutcome.COMPLETED
    by_name = {item.candidate: item.action for item in result.items}
    assert by_name["temporary/not-a-file"] is TemporaryCandidateAction.WRONG_KIND
    assert by_name["temporary/already-gone.bin"] is TemporaryCandidateAction.MISSING


def test_cleanup_transport_failure_is_typed() -> None:
    storage, _journal, game_id = initialize_ready_match()
    paths = GamePaths(game_id)
    orphan = paths.temporary_upload(OP_ID_2, ".CivBeyondSwordSave")
    storage.write_file(orphan, b"orphan", overwrite=False)
    storage.faults.inject(
        StorageOp.REMOVE_FILE, moment=FaultMoment.BEFORE, occurrence=1
    )
    result = cleanup_temporary_orphans(
        storage,
        game_id=game_id,
        candidates=(f"temporary/{OP_ID_2}.upload.CivBeyondSwordSave",),
    )
    assert result.outcome is TemporaryCleanupOutcome.TRANSPORT_FAILURE
    assert result.items[0].action is TemporaryCandidateAction.TRANSPORT_FAILURE
    assert orphan in storage.snapshot().files
