"""Upload-lock lifecycle tests (PT-07–PT-12, PT-43)."""

from __future__ import annotations

import pytest

from civ4_turn_relay.domain import sha256_hex
from civ4_turn_relay.protocol import (
    LOCK_TTL_SECONDS,
    GamePaths,
    InMemoryOperationJournal,
    InProgressHandoff,
    LockAcquireOutcome,
    LockDocument,
    LockInspectionKind,
    LockRepairOutcome,
    LockRepairPreview,
    LockWrongKindTarget,
    acquire_or_resume_upload_lock,
    build_lock_document,
    inspect_upload_lock,
    preview_lock_repair,
    repair_abandoned_upload_lock,
)
from civ4_turn_relay.storage import FakeStorage, StorageCapabilities
from tests.protocol.helpers import (
    CLIENT_A,
    CLIENT_B,
    NOW_UTC,
    OP_ID,
    OP_ID_2,
    CountingStorage,
    initialize_ready_match,
)

NOW = NOW_UTC
SAVE = b"synthetic-outgoing-save-bytes-v1"
SAVE_OTHER = b"synthetic-outgoing-save-bytes-v2"


@pytest.fixture
def ready() -> tuple[FakeStorage, InMemoryOperationJournal, str]:
    storage, journal, game_id = initialize_ready_match()
    return storage, journal, game_id


def _digest(data: bytes = SAVE) -> str:
    return sha256_hex(data)


@pytest.mark.pt("PT-07")
def test_pt07_double_mkdir_lock_second_fails(
    ready: tuple[FakeStorage, InMemoryOperationJournal, str],
) -> None:
    storage, journal, game_id = ready
    first = acquire_or_resume_upload_lock(
        storage,
        game_id=game_id,
        operation_id=OP_ID,
        client_id=CLIENT_A,
        player_id="player_a",
        now_utc=NOW,
        journal=journal,
        sha256=_digest(),
    )
    assert first.outcome is LockAcquireOutcome.ACQUIRED
    assert first.owned is True

    other = InMemoryOperationJournal()
    second = acquire_or_resume_upload_lock(
        storage,
        game_id=game_id,
        operation_id=OP_ID_2,
        client_id=CLIENT_B,
        player_id="player_a",
        now_utc=NOW,
        journal=other,
        sha256=_digest(),
    )
    assert second.outcome is LockAcquireOutcome.FOREIGN_HELD
    assert second.owned is False
    paths = GamePaths(game_id)
    assert paths.upload_lock_dir in storage.snapshot().directories


@pytest.mark.pt("PT-08")
def test_pt08_own_operation_lock_resume(
    ready: tuple[FakeStorage, InMemoryOperationJournal, str],
) -> None:
    storage, journal, game_id = ready
    first = acquire_or_resume_upload_lock(
        storage,
        game_id=game_id,
        operation_id=OP_ID,
        client_id=CLIENT_A,
        player_id="player_a",
        now_utc=NOW,
        journal=journal,
        sha256=_digest(),
    )
    assert first.outcome is LockAcquireOutcome.ACQUIRED
    resumed = acquire_or_resume_upload_lock(
        storage,
        game_id=game_id,
        operation_id=OP_ID,
        client_id=CLIENT_A,
        player_id="player_a",
        now_utc=NOW,
        journal=journal,
        sha256=_digest(),
    )
    assert resumed.outcome is LockAcquireOutcome.RESUMED
    assert resumed.owned is True
    assert resumed.document == first.document


@pytest.mark.pt("PT-09")
def test_pt09_foreign_lock_older_than_ttl_not_auto_removed(
    ready: tuple[FakeStorage, InMemoryOperationJournal, str],
) -> None:
    storage, journal, game_id = ready
    foreign = build_lock_document(
        operation_id=OP_ID_2,
        client_id=CLIENT_B,
        player_id="player_b",
        now_utc="2020-01-01T00:00:00Z",
    )
    paths = GamePaths(game_id)
    storage.mkdir(paths.upload_lock_dir)
    storage.write_file(paths.upload_lock_json, foreign.to_json_bytes(), overwrite=False)

    result = acquire_or_resume_upload_lock(
        storage,
        game_id=game_id,
        operation_id=OP_ID,
        client_id=CLIENT_A,
        player_id="player_a",
        now_utc=NOW,
        journal=journal,
        sha256=_digest(),
    )
    assert result.outcome is LockAcquireOutcome.FOREIGN_HELD
    assert paths.upload_lock_json in storage.snapshot().files
    assert LOCK_TTL_SECONDS == 15 * 60


@pytest.mark.pt("PT-12")
def test_pt12_missing_or_unreadable_lock_json_not_owned(
    ready: tuple[FakeStorage, InMemoryOperationJournal, str],
) -> None:
    storage, journal, game_id = ready
    paths = GamePaths(game_id)
    storage.mkdir(paths.upload_lock_dir)
    missing = acquire_or_resume_upload_lock(
        storage,
        game_id=game_id,
        operation_id=OP_ID,
        client_id=CLIENT_A,
        player_id="player_a",
        now_utc=NOW,
        journal=journal,
        sha256=_digest(),
    )
    assert missing.outcome is LockAcquireOutcome.UNREADABLE
    assert missing.owned is False
    assert inspect_upload_lock(storage, game_id).kind is (
        LockInspectionKind.MISSING_LOCK_JSON
    )

    storage.write_file(paths.upload_lock_json, b"{not-json", overwrite=False)
    bad = inspect_upload_lock(storage, game_id)
    assert bad.kind is LockInspectionKind.MALFORMED
    assert bad.raw_bytes == b"{not-json"
    denied = acquire_or_resume_upload_lock(
        storage,
        game_id=game_id,
        operation_id=OP_ID,
        client_id=CLIENT_A,
        player_id="player_a",
        now_utc=NOW,
        journal=journal,
        sha256=_digest(),
    )
    assert denied.outcome is LockAcquireOutcome.UNREADABLE
    assert denied.owned is False


@pytest.mark.pt("PT-12")
def test_pt12_missing_lock_json_repair_only_when_confirmed_unchanged(
    ready: tuple[FakeStorage, InMemoryOperationJournal, str],
) -> None:
    storage, _journal, game_id = ready
    paths = GamePaths(game_id)
    storage.mkdir(paths.upload_lock_dir)
    preview = preview_lock_repair(storage, game_id)
    assert preview.kind is LockInspectionKind.MISSING_LOCK_JSON

    denied = repair_abandoned_upload_lock(storage, preview=preview, confirmed=False)
    assert denied.outcome is LockRepairOutcome.NOT_CONFIRMED
    assert denied.audit.removed is False
    assert paths.upload_lock_dir in storage.snapshot().directories

    # Newly created lock.json between preview and repair → CHANGED, not removed.
    storage.write_file(paths.upload_lock_json, b'{"x":1}', overwrite=False)
    changed = repair_abandoned_upload_lock(storage, preview=preview, confirmed=True)
    assert changed.outcome is LockRepairOutcome.CHANGED
    assert changed.audit.removed is False
    assert paths.upload_lock_json in storage.snapshot().files

    storage.remove_file(paths.upload_lock_json)
    preview2 = preview_lock_repair(storage, game_id)
    removed = repair_abandoned_upload_lock(storage, preview=preview2, confirmed=True)
    assert removed.outcome is LockRepairOutcome.REMOVED
    assert removed.audit.removed is True
    assert paths.upload_lock_dir not in storage.snapshot().directories


@pytest.mark.pt("PT-12")
def test_pt12_malformed_lock_json_repair_only_when_confirmed_unchanged(
    ready: tuple[FakeStorage, InMemoryOperationJournal, str],
) -> None:
    storage, _journal, game_id = ready
    paths = GamePaths(game_id)
    storage.mkdir(paths.upload_lock_dir)
    malformed = b"{not-json-payload"
    storage.write_file(paths.upload_lock_json, malformed, overwrite=False)
    preview = preview_lock_repair(storage, game_id)
    assert preview.kind is LockInspectionKind.MALFORMED
    assert preview.raw_bytes == malformed

    denied = repair_abandoned_upload_lock(storage, preview=preview, confirmed=False)
    assert denied.outcome is LockRepairOutcome.NOT_CONFIRMED
    assert paths.upload_lock_json in storage.snapshot().files

    # Changed malformed payload between preview and repair → not removed.
    storage.write_file(paths.upload_lock_json, b"{different-malformed", overwrite=True)
    changed = repair_abandoned_upload_lock(storage, preview=preview, confirmed=True)
    assert changed.outcome is LockRepairOutcome.CHANGED
    assert changed.audit.removed is False
    assert storage.snapshot().files[paths.upload_lock_json] == b"{different-malformed"

    preview2 = preview_lock_repair(storage, game_id)
    removed = repair_abandoned_upload_lock(storage, preview=preview2, confirmed=True)
    assert removed.outcome is LockRepairOutcome.REMOVED
    assert removed.audit.removed is True
    assert paths.upload_lock_dir not in storage.snapshot().directories


@pytest.mark.pt("PT-11")
def test_pt11_confirmed_abandoned_lock_repair_logged(
    ready: tuple[FakeStorage, InMemoryOperationJournal, str],
) -> None:
    storage, _journal, game_id = ready
    foreign = build_lock_document(
        operation_id=OP_ID_2,
        client_id=CLIENT_B,
        player_id="player_b",
        now_utc=NOW,
    )
    paths = GamePaths(game_id)
    storage.mkdir(paths.upload_lock_dir)
    storage.write_file(paths.upload_lock_json, foreign.to_json_bytes(), overwrite=False)

    preview = preview_lock_repair(storage, game_id)
    assert preview.kind is LockInspectionKind.READABLE
    denied = repair_abandoned_upload_lock(storage, preview=preview, confirmed=False)
    assert denied.outcome is LockRepairOutcome.NOT_CONFIRMED
    assert denied.audit.removed is False
    assert paths.upload_lock_json in storage.snapshot().files

    removed = repair_abandoned_upload_lock(storage, preview=preview, confirmed=True)
    assert removed.outcome is LockRepairOutcome.REMOVED
    assert removed.audit.removed is True
    assert removed.audit.confirmed is True
    assert removed.audit.observed_operation_id == OP_ID_2
    assert paths.upload_lock_dir not in storage.snapshot().directories


@pytest.mark.pt("PT-11")
def test_confirmed_repair_refuses_when_lock_metadata_changed(
    ready: tuple[FakeStorage, InMemoryOperationJournal, str],
) -> None:
    storage, _journal, game_id = ready
    preview_doc = build_lock_document(
        operation_id=OP_ID_2,
        client_id=CLIENT_B,
        player_id="player_b",
        now_utc=NOW,
    )
    changed = LockDocument(
        operation_id=OP_ID_2,
        client_id=CLIENT_B,
        player_id="player_b",
        created_at=NOW,
        expires_at="2026-08-10T19:30:00Z",
    )
    paths = GamePaths(game_id)
    storage.mkdir(paths.upload_lock_dir)
    storage.write_file(paths.upload_lock_json, changed.to_json_bytes(), overwrite=False)

    preview = LockRepairPreview(
        game_id=game_id,
        kind=LockInspectionKind.READABLE,
        document=preview_doc,
        raw_bytes=preview_doc.to_json_bytes(),
    )
    result = repair_abandoned_upload_lock(storage, preview=preview, confirmed=True)
    assert result.outcome is LockRepairOutcome.CHANGED
    assert result.audit.removed is False
    assert paths.upload_lock_json in storage.snapshot().files


@pytest.mark.pt("PT-43")
def test_pt43_live_foreign_lock_within_ttl_no_break(
    ready: tuple[FakeStorage, InMemoryOperationJournal, str],
) -> None:
    storage, journal, game_id = ready
    foreign = build_lock_document(
        operation_id=OP_ID_2,
        client_id=CLIENT_B,
        player_id="player_b",
        now_utc=NOW,
    )
    paths = GamePaths(game_id)
    storage.mkdir(paths.upload_lock_dir)
    storage.write_file(paths.upload_lock_json, foreign.to_json_bytes(), overwrite=False)
    result = acquire_or_resume_upload_lock(
        storage,
        game_id=game_id,
        operation_id=OP_ID,
        client_id=CLIENT_A,
        player_id="player_a",
        now_utc=NOW,
        journal=journal,
        sha256=_digest(),
    )
    assert result.outcome is LockAcquireOutcome.FOREIGN_HELD


def test_resume_requires_journal_agreement(
    ready: tuple[FakeStorage, InMemoryOperationJournal, str],
) -> None:
    storage, journal, game_id = ready
    acquire_or_resume_upload_lock(
        storage,
        game_id=game_id,
        operation_id=OP_ID,
        client_id=CLIENT_A,
        player_id="player_a",
        now_utc=NOW,
        journal=journal,
        sha256=_digest(),
    )
    empty = InMemoryOperationJournal()
    result = acquire_or_resume_upload_lock(
        storage,
        game_id=game_id,
        operation_id=OP_ID,
        client_id=CLIENT_A,
        player_id="player_a",
        now_utc=NOW,
        journal=empty,
        sha256=_digest(),
    )
    assert result.outcome is LockAcquireOutcome.FOREIGN_HELD


def test_resume_rejects_mismatched_lock_player_without_mutation(
    ready: tuple[FakeStorage, InMemoryOperationJournal, str],
) -> None:
    storage, journal, game_id = ready
    first = acquire_or_resume_upload_lock(
        storage,
        game_id=game_id,
        operation_id=OP_ID,
        client_id=CLIENT_A,
        player_id="player_a",
        now_utc=NOW,
        journal=journal,
        sha256=_digest(),
    )
    assert first.owned is True
    paths = GamePaths(game_id)
    # Same op/client/journal, but lock.json player_id disagrees with the request.
    altered = LockDocument(
        operation_id=OP_ID,
        client_id=CLIENT_A,
        player_id="player_b",
        created_at=first.document.created_at if first.document else NOW,
        expires_at=first.document.expires_at if first.document else NOW,
    )
    storage.write_file(paths.upload_lock_json, altered.to_json_bytes(), overwrite=True)
    before = storage.snapshot().files[paths.upload_lock_json]
    result = acquire_or_resume_upload_lock(
        storage,
        game_id=game_id,
        operation_id=OP_ID,
        client_id=CLIENT_A,
        player_id="player_a",
        now_utc=NOW,
        journal=journal,
        sha256=_digest(),
    )
    assert result.outcome is LockAcquireOutcome.FOREIGN_HELD
    assert result.owned is False
    assert storage.snapshot().files[paths.upload_lock_json] == before


def test_resume_rejects_mismatched_hash_without_mutation(
    ready: tuple[FakeStorage, InMemoryOperationJournal, str],
) -> None:
    storage, journal, game_id = ready
    first = acquire_or_resume_upload_lock(
        storage,
        game_id=game_id,
        operation_id=OP_ID,
        client_id=CLIENT_A,
        player_id="player_a",
        now_utc=NOW,
        journal=journal,
        sha256=_digest(),
    )
    assert first.owned is True
    paths = GamePaths(game_id)
    before = storage.snapshot().files[paths.upload_lock_json]
    result = acquire_or_resume_upload_lock(
        storage,
        game_id=game_id,
        operation_id=OP_ID,
        client_id=CLIENT_A,
        player_id="player_a",
        now_utc=NOW,
        journal=journal,
        sha256=_digest(SAVE_OTHER),
    )
    assert result.outcome is LockAcquireOutcome.FOREIGN_HELD
    assert result.owned is False
    assert storage.snapshot().files[paths.upload_lock_json] == before
    progress = journal.in_progress_handoff(game_id=game_id)
    assert progress is not None
    assert progress.sha256 == _digest()


def test_transport_failure_preview_never_repairable(
    ready: tuple[FakeStorage, InMemoryOperationJournal, str],
) -> None:
    storage, _journal, game_id = ready
    preview = LockRepairPreview(
        game_id=game_id,
        kind=LockInspectionKind.TRANSPORT_FAILURE,
    )
    result = repair_abandoned_upload_lock(storage, preview=preview, confirmed=True)
    assert result.outcome is LockRepairOutcome.NOT_REPAIRABLE
    assert result.audit.removed is False


def test_upload_lock_as_file_never_auto_deleted_but_confirmed_repair_works(
    ready: tuple[FakeStorage, InMemoryOperationJournal, str],
) -> None:
    storage, journal, game_id = ready
    paths = GamePaths(game_id)
    storage.write_file(paths.upload_lock_dir, b"not-a-directory", overwrite=False)
    inspection = inspect_upload_lock(storage, game_id)
    assert inspection.kind is LockInspectionKind.WRONG_KIND
    assert inspection.wrong_kind_target is LockWrongKindTarget.UPLOAD_LOCK

    denied = acquire_or_resume_upload_lock(
        storage,
        game_id=game_id,
        operation_id=OP_ID,
        client_id=CLIENT_A,
        player_id="player_a",
        now_utc=NOW,
        journal=journal,
        sha256=_digest(),
    )
    assert denied.outcome is LockAcquireOutcome.UNREADABLE
    assert paths.upload_lock_dir in storage.snapshot().files

    preview = preview_lock_repair(storage, game_id)
    assert preview.kind is LockInspectionKind.WRONG_KIND
    unconfirmed = repair_abandoned_upload_lock(
        storage, preview=preview, confirmed=False
    )
    assert unconfirmed.outcome is LockRepairOutcome.NOT_CONFIRMED
    assert paths.upload_lock_dir in storage.snapshot().files

    removed = repair_abandoned_upload_lock(storage, preview=preview, confirmed=True)
    assert removed.outcome is LockRepairOutcome.REMOVED
    assert removed.audit.removed is True
    assert paths.upload_lock_dir not in storage.snapshot().files
    assert paths.upload_lock_dir not in storage.snapshot().directories


def test_lock_json_as_directory_empty_repair_and_nonempty_manual(
    ready: tuple[FakeStorage, InMemoryOperationJournal, str],
) -> None:
    storage, journal, game_id = ready
    paths = GamePaths(game_id)
    storage.mkdir(paths.upload_lock_dir)
    storage.mkdir(paths.upload_lock_json)
    inspection = inspect_upload_lock(storage, game_id)
    assert inspection.kind is LockInspectionKind.WRONG_KIND
    assert inspection.wrong_kind_target is LockWrongKindTarget.LOCK_JSON

    denied = acquire_or_resume_upload_lock(
        storage,
        game_id=game_id,
        operation_id=OP_ID,
        client_id=CLIENT_A,
        player_id="player_a",
        now_utc=NOW,
        journal=journal,
        sha256=_digest(),
    )
    assert denied.outcome is LockAcquireOutcome.UNREADABLE

    preview = preview_lock_repair(storage, game_id)
    removed = repair_abandoned_upload_lock(storage, preview=preview, confirmed=True)
    assert removed.outcome is LockRepairOutcome.REMOVED
    assert paths.upload_lock_dir not in storage.snapshot().directories

    # Non-empty lock.json directory requires manual repair.
    storage.mkdir(paths.upload_lock_dir)
    storage.mkdir(paths.upload_lock_json)
    storage.write_file(
        f"{paths.upload_lock_json}/nested.bin", b"stuck", overwrite=False
    )
    preview2 = preview_lock_repair(storage, game_id)
    manual = repair_abandoned_upload_lock(storage, preview=preview2, confirmed=True)
    assert manual.outcome is LockRepairOutcome.MANUAL_REPAIR_REQUIRED
    assert manual.audit.removed is False
    assert paths.upload_lock_json in storage.snapshot().directories


def test_capability_failure_without_exclusive_mkdir(
    ready: tuple[FakeStorage, InMemoryOperationJournal, str],
) -> None:
    inner, journal, game_id = ready

    class _NoExclusiveMkdir(CountingStorage):
        def capabilities(self) -> StorageCapabilities:
            return StorageCapabilities(
                exclusive_mkdir=False,
                atomic_replace=True,
                atomic_publish_no_replace=True,
                complete_readback=True,
            )

    storage = _NoExclusiveMkdir(inner)
    result = acquire_or_resume_upload_lock(
        storage,
        game_id=game_id,
        operation_id=OP_ID,
        client_id=CLIENT_A,
        player_id="player_a",
        now_utc=NOW,
        journal=journal,
        sha256=_digest(),
    )
    assert result.outcome is LockAcquireOutcome.CAPABILITY_FAILURE


def test_in_progress_handoff_model_roundtrip() -> None:
    record = InProgressHandoff(
        game_id="example-match",
        operation_id=OP_ID,
        client_id=CLIENT_A,
        player_id="player_a",
        sha256=_digest(),
    )
    journal = InMemoryOperationJournal()
    journal.begin_handoff(record)
    assert journal.in_progress_handoff(game_id="example-match") == record
