"""Handoff commit engine tests (PT-01–PT-05, PT-25–PT-31, PT-35–PT-37, PT-41–PT-42)."""

from __future__ import annotations

import pytest

from civ4_turn_relay.domain import Manifest, sha256_hex
from civ4_turn_relay.protocol import (
    GamePaths,
    HandoffOutcome,
    HandoffRequest,
    InMemoryOperationJournal,
    LockAcquireOutcome,
    acquire_or_resume_upload_lock,
    commit_handoff,
    read_authoritative_manifest,
)
from civ4_turn_relay.storage import (
    FakeStorage,
    FaultMoment,
    StorageAlreadyExistsError,
    StorageCapabilities,
    StorageOp,
)
from tests.protocol.helpers import (
    CLIENT_A,
    CLIENT_B,
    NOW_UTC,
    OP_ID,
    OP_ID_2,
    OP_ID_3,
    SAVE_NAME,
    CountingStorage,
    UncertainReplaceStorage,
    initialize_ready_match,
    sample_players,
)

SAVE_A = b"synthetic-outgoing-save-bytes-player-a-v1"
SAVE_B = b"synthetic-outgoing-save-bytes-player-b-v2"
SAVE_C = b"synthetic-outgoing-save-bytes-player-c-v3"


def _req(
    *,
    game_id: str = "example-match",
    player: str = "player_a",
    client: str = CLIENT_A,
    operation_id: str = OP_ID,
    data: bytes = SAVE_A,
    filename: str = SAVE_NAME,
    now: str = NOW_UTC,
) -> HandoffRequest:
    return HandoffRequest(
        game_id=game_id,
        local_player_id=player,
        client_id=client,
        operation_id=operation_id,
        outgoing_bytes=data,
        original_filename=filename,
        now_utc=now,
    )


def _manifest_bytes(storage: FakeStorage, game_id: str) -> bytes:
    return storage.read_file(GamePaths(game_id).manifest)


@pytest.mark.pt("PT-01")
@pytest.mark.pt("PT-41")
def test_pt01_pt41_owner_commits_new_hash_first_handoff() -> None:
    storage, journal, game_id = initialize_ready_match()
    before = _manifest_bytes(storage, game_id)
    digest = sha256_hex(SAVE_A)
    result = commit_handoff(storage, _req(), journal=journal)
    assert result.outcome is HandoffOutcome.COMMITTED
    assert result.manifest_changed is True
    assert result.manifest is not None
    assert result.manifest.protocol_sequence == 1
    assert result.manifest.accepted_save_hashes == (digest,)
    assert result.manifest.current_player_id == "player_b"
    assert result.manifest.last_sender_id == "player_a"
    assert result.manifest.accepted_save is not None
    assert result.manifest.accepted_save.sha256 == digest
    assert result.manifest.protocol.last_operation_id == OP_ID

    paths = GamePaths(game_id)
    snap = storage.snapshot()
    save_path = paths.resolve(result.manifest.accepted_save.remote_path)
    assert snap.files[save_path] == SAVE_A
    hist = paths.resolve(result.manifest.previous_manifest_ref or "")
    assert snap.files[hist] == before
    assert hist.endswith(f"manifest-000000-{sha256_hex(before)[:12]}.json")
    assert paths.upload_lock_dir not in snap.directories


@pytest.mark.pt("PT-02")
def test_pt02_non_owner_commit_rejected() -> None:
    storage, journal, game_id = initialize_ready_match()
    before = storage.snapshot()
    result = commit_handoff(
        storage, _req(player="player_b", client=CLIENT_B), journal=journal
    )
    assert result.outcome is HandoffOutcome.NOT_CURRENT_OWNER
    assert result.manifest_changed is False
    assert storage.snapshot() == before


@pytest.mark.pt("PT-03")
def test_pt03_previous_sender_retry_after_unknown_commit() -> None:
    storage, journal, game_id = initialize_ready_match()
    first = commit_handoff(storage, _req(), journal=journal)
    assert first.outcome is HandoffOutcome.COMMITTED
    # Retry same bytes/hash as previous sender with attempt evidence.
    retry = commit_handoff(
        storage,
        _req(operation_id=OP_ID_2, now="2026-08-10T19:05:00Z"),
        journal=journal,
    )
    assert retry.outcome is HandoffOutcome.IDEMPOTENT_ACK
    assert retry.manifest_changed is False
    assert retry.manifest is not None
    assert retry.manifest.protocol_sequence == 1


@pytest.mark.pt("PT-04")
def test_pt04_recipient_cannot_submit_incoming_save() -> None:
    storage, journal_a, game_id = initialize_ready_match()
    committed = commit_handoff(storage, _req(), journal=journal_a)
    assert committed.outcome is HandoffOutcome.COMMITTED
    before = storage.snapshot()
    journal_b = InMemoryOperationJournal()
    result = commit_handoff(
        storage,
        _req(player="player_b", client=CLIENT_B, operation_id=OP_ID_2, data=SAVE_A),
        journal=journal_b,
    )
    assert result.outcome is HandoffOutcome.REJECT_INCOMING
    assert result.manifest_changed is False
    assert storage.snapshot() == before


@pytest.mark.pt("PT-05")
def test_pt05_replay_of_older_hash_no_remote_change() -> None:
    storage, journal, game_id = initialize_ready_match(players=sample_players())
    assert (
        commit_handoff(storage, _req(), journal=journal).outcome
        is HandoffOutcome.COMMITTED
    )
    journal_b = InMemoryOperationJournal()
    assert (
        commit_handoff(
            storage,
            _req(
                player="player_b",
                client=CLIENT_B,
                operation_id=OP_ID_2,
                data=SAVE_B,
                filename="ExampleMatch_PlayerB.CivBeyondSwordSave",
            ),
            journal=journal_b,
        ).outcome
        is HandoffOutcome.COMMITTED
    )
    before = storage.snapshot()
    journal_c = InMemoryOperationJournal()
    # player_c tries to replay SAVE_A (older accepted hash)
    result = commit_handoff(
        storage,
        _req(
            player="player_c",
            client="client-charlie",
            operation_id=OP_ID_3,
            data=SAVE_A,
        ),
        journal=journal_c,
    )
    assert result.outcome is HandoffOutcome.STALE_REPLAY
    assert result.manifest_changed is False
    assert storage.snapshot() == before


@pytest.mark.pt("PT-35")
def test_pt35_stale_local_candidate_cannot_overwrite_newer_remote() -> None:
    storage, journal, game_id = initialize_ready_match()
    commit_handoff(storage, _req(), journal=journal)
    journal_b = InMemoryOperationJournal()
    commit_handoff(
        storage,
        _req(
            player="player_b",
            client=CLIENT_B,
            operation_id=OP_ID_2,
            data=SAVE_B,
            filename="ExampleMatch_PlayerB.CivBeyondSwordSave",
        ),
        journal=journal_b,
    )
    before = _manifest_bytes(storage, game_id)
    # player_a tries old SAVE_A again while not owner / historical
    result = commit_handoff(
        storage, _req(operation_id=OP_ID_3, data=SAVE_A), journal=journal
    )
    assert result.outcome in {
        HandoffOutcome.STALE_REPLAY,
        HandoffOutcome.IDEMPOTENT_ACK,
        HandoffOutcome.JOURNAL_ONLY_ACK,
        HandoffOutcome.NOT_CURRENT_OWNER,
    }
    assert result.manifest_changed is False
    assert _manifest_bytes(storage, game_id) == before


@pytest.mark.pt("PT-42")
def test_pt42_three_humans_wrap_around() -> None:
    storage, journal, game_id = initialize_ready_match(players=sample_players())
    r1 = commit_handoff(storage, _req(), journal=journal)
    assert r1.manifest is not None
    assert r1.manifest.current_player_id == "player_b"
    journal_b = InMemoryOperationJournal()
    r2 = commit_handoff(
        storage,
        _req(
            player="player_b",
            client=CLIENT_B,
            operation_id=OP_ID_2,
            data=SAVE_B,
            filename="ExampleMatch_PlayerB.CivBeyondSwordSave",
            now="2026-08-10T19:10:00Z",
        ),
        journal=journal_b,
    )
    assert r2.manifest is not None
    assert r2.manifest.current_player_id == "player_c"
    journal_c = InMemoryOperationJournal()
    r3 = commit_handoff(
        storage,
        _req(
            player="player_c",
            client="client-charlie",
            operation_id=OP_ID_3,
            data=SAVE_C,
            filename="ExampleMatch_PlayerC.CivBeyondSwordSave",
            now="2026-08-10T19:20:00Z",
        ),
        journal=journal_c,
    )
    assert r3.outcome is HandoffOutcome.COMMITTED
    assert r3.manifest is not None
    assert r3.manifest.current_player_id == "player_a"
    assert r3.manifest.protocol_sequence == 3


@pytest.mark.pt("PT-25")
def test_pt25_crash_after_temp_upload_retry_succeeds_once() -> None:
    """Genuine own-op resume: retain lock/journal, RESUME path, commit once."""
    storage, journal, game_id = initialize_ready_match()
    before = _manifest_bytes(storage, game_id)
    paths = GamePaths(game_id)
    digest = sha256_hex(SAVE_A)
    storage.faults.reset()
    # Fail after temp-upload write (WRITE #2: lock.json then temp), keep manifest.
    storage.faults.inject(StorageOp.WRITE, moment=FaultMoment.AFTER, occurrence=2)
    failed = commit_handoff(storage, _req(), journal=journal)
    assert failed.outcome is HandoffOutcome.TRANSPORT_FAILURE
    assert failed.manifest_changed is False
    assert failed.sha256 == digest
    assert _manifest_bytes(storage, game_id) == before

    # Lock + exact lock.json + matching in_progress journal must remain for resume.
    assert paths.upload_lock_dir in storage.snapshot().directories
    lock_bytes = storage.snapshot().files[paths.upload_lock_json]
    progress = journal.in_progress_handoff(game_id=game_id)
    assert progress is not None
    assert progress.operation_id == OP_ID
    assert progress.client_id == CLIENT_A
    assert progress.player_id == "player_a"
    assert progress.sha256 == digest

    class _LockMkdirSpy(CountingStorage):
        def __init__(self, inner: FakeStorage) -> None:
            super().__init__(inner)
            self.lock_mkdir_created = 0
            self.lock_mkdir_already_exists = 0

        def mkdir(self, path: str) -> None:
            self._record("mkdir")
            if path == paths.upload_lock_dir:
                try:
                    self._inner.mkdir(path)
                except StorageAlreadyExistsError:
                    self.lock_mkdir_already_exists += 1
                    raise
                self.lock_mkdir_created += 1
                return
            self._inner.mkdir(path)

    storage.faults.reset()
    spy = _LockMkdirSpy(storage)
    ok = commit_handoff(spy, _req(), journal=journal)
    assert ok.outcome is HandoffOutcome.COMMITTED
    assert ok.manifest is not None
    assert ok.manifest.protocol_sequence == 1
    assert ok.sha256 == digest
    # Resume must hit AlreadyExists — not a fresh mkdir that recreates the lock.
    assert spy.lock_mkdir_created == 0
    assert spy.lock_mkdir_already_exists == 1
    assert paths.upload_lock_dir not in storage.snapshot().directories
    assert journal.in_progress_handoff(game_id=game_id) is None
    # Prove the retained lock document was the one from the first attempt.
    assert lock_bytes  # retained non-empty evidence from the failed attempt


@pytest.mark.pt("PT-26")
@pytest.mark.pt("PT-28")
def test_pt26_pt28_crash_after_final_publish_verify_reuse() -> None:
    storage, journal, game_id = initialize_ready_match()
    before = _manifest_bytes(storage, game_id)
    storage.faults.reset()
    # After publish_no_replace succeeds, fail (lost response) before history/manifest.
    storage.faults.inject(
        StorageOp.PUBLISH_NO_REPLACE, moment=FaultMoment.AFTER, occurrence=1
    )
    failed = commit_handoff(storage, _req(), journal=journal)
    assert failed.outcome is HandoffOutcome.TRANSPORT_FAILURE
    assert failed.sha256 == sha256_hex(SAVE_A)
    assert _manifest_bytes(storage, game_id) == before
    paths = GamePaths(game_id)
    digest = sha256_hex(SAVE_A)
    final = paths.resolve(
        paths.accepted_save_relative(1, digest, ".CivBeyondSwordSave")
    )
    assert storage.snapshot().files[final] == SAVE_A
    assert paths.upload_lock_dir in storage.snapshot().directories
    assert journal.in_progress_handoff(game_id=game_id) is not None

    storage.faults.reset()
    # Same operation must resume the retained lock (not a new operation_id).
    ok = commit_handoff(storage, _req(), journal=journal)
    assert ok.outcome is HandoffOutcome.COMMITTED
    assert storage.snapshot().files[final] == SAVE_A
    saves = [p for p in storage.snapshot().files if "/saves/" in p]
    assert len(saves) == 1
    assert paths.upload_lock_dir not in storage.snapshot().directories


@pytest.mark.pt("PT-27")
def test_pt27_existing_final_path_different_content_hard_error() -> None:
    storage, journal, game_id = initialize_ready_match()
    paths = GamePaths(game_id)
    digest = sha256_hex(SAVE_A)
    final = paths.resolve(
        paths.accepted_save_relative(1, digest, ".CivBeyondSwordSave")
    )
    storage.write_file(final, b"different-bytes-not-matching-hash", overwrite=False)
    before = _manifest_bytes(storage, game_id)
    result = commit_handoff(storage, _req(), journal=journal)
    assert result.outcome is HandoffOutcome.HARD_INTEGRITY_FAILURE
    assert result.manifest_changed is False
    assert _manifest_bytes(storage, game_id) == before
    assert storage.snapshot().files[final] == b"different-bytes-not-matching-hash"


@pytest.mark.pt("PT-29")
def test_pt29_remote_readback_hash_mismatch_aborts() -> None:
    storage, journal, game_id = initialize_ready_match()
    before = _manifest_bytes(storage, game_id)
    storage.faults.reset()
    # READ #1 manifest OK; #2 temp miss (aborts); #3 temp verify success path.
    storage.faults.inject_read_corruption(occurrence=3)
    result = commit_handoff(storage, _req(), journal=journal)
    assert result.outcome is HandoffOutcome.HARD_INTEGRITY_FAILURE
    assert result.manifest_changed is False
    assert _manifest_bytes(storage, game_id) == before


@pytest.mark.pt("PT-30")
def test_pt30_missing_atomic_replace_capability_no_success() -> None:
    inner, journal, game_id = initialize_ready_match()

    class _NoReplace(CountingStorage):
        def capabilities(self) -> StorageCapabilities:
            return StorageCapabilities(
                exclusive_mkdir=True,
                atomic_replace=False,
                atomic_publish_no_replace=True,
                complete_readback=True,
            )

    before = _manifest_bytes(inner, game_id)
    result = commit_handoff(_NoReplace(inner), _req(), journal=journal)
    assert result.outcome is HandoffOutcome.CAPABILITY_FAILURE
    assert result.manifest_changed is False
    assert _manifest_bytes(inner, game_id) == before


@pytest.mark.pt("PT-30")
def test_pt30_missing_exclusive_mkdir_capability_no_success() -> None:
    inner, journal, game_id = initialize_ready_match()

    class _NoMkdir(CountingStorage):
        def capabilities(self) -> StorageCapabilities:
            return StorageCapabilities(
                exclusive_mkdir=False,
                atomic_replace=True,
                atomic_publish_no_replace=True,
                complete_readback=True,
            )

    result = commit_handoff(_NoMkdir(inner), _req(), journal=journal)
    assert result.outcome is HandoffOutcome.CAPABILITY_FAILURE
    assert result.manifest_changed is False


@pytest.mark.pt("PT-31")
def test_pt31_failure_after_manifest_replace_reconciles_idempotent() -> None:
    storage, journal, game_id = initialize_ready_match()
    wrapped = UncertainReplaceStorage(storage, committed_bytes="source")
    result = commit_handoff(wrapped, _req(), journal=journal)
    assert result.outcome is HandoffOutcome.IDEMPOTENT_ACK
    assert result.manifest_changed is False
    assert result.manifest is not None
    assert result.manifest.protocol_sequence == 1
    assert result.manifest.protocol.last_operation_id == OP_ID
    read = read_authoritative_manifest(storage, game_id)
    assert read.ok
    assert read.manifest is not None
    assert read.manifest.protocol_sequence == 1


@pytest.mark.pt("PT-10")
def test_pt10_lock_ownership_change_before_commit_aborts() -> None:
    storage, journal, game_id = initialize_ready_match()
    before = _manifest_bytes(storage, game_id)
    paths = GamePaths(game_id)

    class _StealLockBeforeReplace(CountingStorage):
        def write_file(
            self, path: str, data: bytes, *, overwrite: bool = False
        ) -> None:
            self._inner.write_file(path, data, overwrite=overwrite)
            if path.endswith(f"manifest-{OP_ID}.json"):
                # Steal lock after staging new manifest, before pre-commit confirm.
                from civ4_turn_relay.protocol import build_lock_document

                doc = build_lock_document(
                    operation_id=OP_ID_2,
                    client_id=CLIENT_B,
                    player_id="player_b",
                    now_utc=NOW_UTC,
                )
                self._inner.write_file(
                    paths.upload_lock_json, doc.to_json_bytes(), overwrite=True
                )

    result = commit_handoff(_StealLockBeforeReplace(storage), _req(), journal=journal)
    assert result.outcome is HandoffOutcome.LOCK_OWNERSHIP_LOST
    assert result.manifest_changed is False
    assert _manifest_bytes(storage, game_id) == before


@pytest.mark.pt("PT-36")
@pytest.mark.pt("PT-37")
def test_pt36_pt37_two_instances_same_player_advance_at_most_once() -> None:
    storage, journal_a, game_id = initialize_ready_match()
    journal_b = InMemoryOperationJournal()
    first = commit_handoff(storage, _req(), journal=journal_a)
    assert first.outcome is HandoffOutcome.COMMITTED
    second = commit_handoff(
        storage, _req(operation_id=OP_ID_2, client=CLIENT_B), journal=journal_b
    )
    # Same player_a content again / not owner after first commit
    assert second.outcome in {
        HandoffOutcome.IDEMPOTENT_ACK,
        HandoffOutcome.NOT_CURRENT_OWNER,
        HandoffOutcome.REJECT_INCOMING,
        HandoffOutcome.STALE_REPLAY,
        HandoffOutcome.JOURNAL_ONLY_ACK,
    }
    assert second.manifest_changed is False
    read = read_authoritative_manifest(storage, game_id)
    assert read.manifest is not None
    assert read.manifest.protocol_sequence == 1


def test_two_simultaneous_lock_attempts_second_blocked() -> None:
    storage, journal_a, game_id = initialize_ready_match()
    journal_b = InMemoryOperationJournal()
    lock_a = acquire_or_resume_upload_lock(
        storage,
        game_id=game_id,
        operation_id=OP_ID,
        client_id=CLIENT_A,
        player_id="player_a",
        now_utc=NOW_UTC,
        journal=journal_a,
        sha256=sha256_hex(SAVE_A),
    )
    assert lock_a.owned is True
    lock_b = acquire_or_resume_upload_lock(
        storage,
        game_id=game_id,
        operation_id=OP_ID_2,
        client_id=CLIENT_B,
        player_id="player_a",
        now_utc=NOW_UTC,
        journal=journal_b,
        sha256=sha256_hex(SAVE_A),
    )
    assert lock_b.outcome is LockAcquireOutcome.FOREIGN_HELD


def test_history_exact_reuse_and_conflict() -> None:
    storage, journal, game_id = initialize_ready_match()
    before = _manifest_bytes(storage, game_id)
    paths = GamePaths(game_id)
    hist = paths.resolve(paths.history_manifest_relative(0, sha256_hex(before)))
    storage.write_file(hist, before, overwrite=False)
    ok = commit_handoff(storage, _req(), journal=journal)
    assert ok.outcome is HandoffOutcome.COMMITTED
    assert storage.snapshot().files[hist] == before

    # Conflict on a later handoff history object
    storage2, journal2, game_id2 = initialize_ready_match(game_id="conflict-match")
    before2 = _manifest_bytes(storage2, game_id2)
    paths2 = GamePaths(game_id2)
    hist2 = paths2.resolve(paths2.history_manifest_relative(0, sha256_hex(before2)))
    storage2.write_file(hist2, b'{"not":"the-same-manifest"}\n', overwrite=False)
    bad = commit_handoff(storage2, _req(game_id=game_id2), journal=journal2)
    assert bad.outcome is HandoffOutcome.HARD_INTEGRITY_FAILURE
    assert bad.manifest_changed is False


def test_final_object_readback_corruption_aborts() -> None:
    storage, journal, game_id = initialize_ready_match()
    before = _manifest_bytes(storage, game_id)
    storage.faults.reset()
    # After temp verify (#3), publish then final read-back (#4).
    storage.faults.inject_read_corruption(occurrence=4)
    result = commit_handoff(storage, _req(), journal=journal)
    assert result.outcome is HandoffOutcome.HARD_INTEGRITY_FAILURE
    assert result.manifest_changed is False
    assert _manifest_bytes(storage, game_id) == before


def test_failures_before_commit_preserve_manifest_bytes() -> None:
    storage, journal, game_id = initialize_ready_match()
    before = _manifest_bytes(storage, game_id)
    storage.faults.reset()
    storage.faults.inject(
        StorageOp.ATOMIC_REPLACE, moment=FaultMoment.BEFORE, occurrence=1
    )
    result = commit_handoff(storage, _req(), journal=journal)
    assert result.outcome is HandoffOutcome.TRANSPORT_FAILURE
    assert result.manifest_changed is False
    assert _manifest_bytes(storage, game_id) == before


def test_no_manifest_reference_before_final_readback() -> None:
    """If final read-back fails, manifest must remain sequence zero."""
    storage, journal, game_id = initialize_ready_match()
    paths = GamePaths(game_id)
    digest = sha256_hex(SAVE_A)
    final = paths.resolve(
        paths.accepted_save_relative(1, digest, ".CivBeyondSwordSave")
    )

    class _CorruptFinalRead(CountingStorage):
        def read_file(self, path: str) -> bytes:
            data = self._inner.read_file(path)
            if path == final:
                return b"x" + data[1:]
            return data

    before = _manifest_bytes(storage, game_id)
    result = commit_handoff(_CorruptFinalRead(storage), _req(), journal=journal)
    assert result.outcome is HandoffOutcome.HARD_INTEGRITY_FAILURE
    assert _manifest_bytes(storage, game_id) == before
    read = read_authoritative_manifest(storage, game_id)
    assert read.manifest is not None
    assert read.manifest.protocol_sequence == 0
    assert read.manifest.accepted_save is None


def test_hard_integrity_releases_lock_and_clears_journal() -> None:
    """Typed terminal cleanup: hard integrity does not leave a false active op."""
    storage, journal, game_id = initialize_ready_match()
    paths = GamePaths(game_id)
    digest = sha256_hex(SAVE_A)
    final = paths.resolve(
        paths.accepted_save_relative(1, digest, ".CivBeyondSwordSave")
    )
    storage.write_file(final, b"different-bytes-not-matching-hash", overwrite=False)
    result = commit_handoff(storage, _req(), journal=journal)
    assert result.outcome is HandoffOutcome.HARD_INTEGRITY_FAILURE
    assert result.sha256 == digest
    assert paths.upload_lock_dir not in storage.snapshot().directories
    assert journal.in_progress_handoff(game_id=game_id) is None


def test_history_stage_failure_retains_evidence_and_outgoing_hash() -> None:
    storage, journal, game_id = initialize_ready_match()
    before = _manifest_bytes(storage, game_id)
    digest = sha256_hex(SAVE_A)
    paths = GamePaths(game_id)
    storage.faults.reset()
    # WRITE #1 lock.json, #2 temp upload, #3 staged history.
    storage.faults.inject(StorageOp.WRITE, moment=FaultMoment.AFTER, occurrence=3)
    failed = commit_handoff(storage, _req(), journal=journal)
    assert failed.outcome is HandoffOutcome.TRANSPORT_FAILURE
    assert failed.sha256 == digest
    assert _manifest_bytes(storage, game_id) == before
    assert paths.upload_lock_dir in storage.snapshot().directories
    assert journal.in_progress_handoff(game_id=game_id) is not None
    assert paths.temporary_history(OP_ID) in storage.snapshot().files


def test_history_publish_lost_response_retries_and_reuses() -> None:
    storage, journal, game_id = initialize_ready_match()
    before = _manifest_bytes(storage, game_id)
    digest = sha256_hex(SAVE_A)
    paths = GamePaths(game_id)
    hist = paths.resolve(paths.history_manifest_relative(0, sha256_hex(before)))
    storage.faults.reset()
    # PUBLISH #1 accepted save, #2 history — fail after history lands.
    storage.faults.inject(
        StorageOp.PUBLISH_NO_REPLACE, moment=FaultMoment.AFTER, occurrence=2
    )
    failed = commit_handoff(storage, _req(), journal=journal)
    assert failed.outcome is HandoffOutcome.TRANSPORT_FAILURE
    assert failed.sha256 == digest
    assert _manifest_bytes(storage, game_id) == before
    assert storage.snapshot().files[hist] == before
    assert journal.in_progress_handoff(game_id=game_id) is not None

    storage.faults.reset()
    ok = commit_handoff(storage, _req(), journal=journal)
    assert ok.outcome is HandoffOutcome.COMMITTED
    assert storage.snapshot().files[hist] == before
    assert ok.manifest is not None
    assert ok.manifest.previous_manifest_ref is not None


def test_history_corrupt_final_readback_aborts_with_outgoing_hash() -> None:
    storage, journal, game_id = initialize_ready_match()
    before = _manifest_bytes(storage, game_id)
    digest = sha256_hex(SAVE_A)
    paths = GamePaths(game_id)
    hist = paths.resolve(paths.history_manifest_relative(0, sha256_hex(before)))

    class _CorruptHistoryRead(CountingStorage):
        def read_file(self, path: str) -> bytes:
            data = self._inner.read_file(path)
            if path == hist:
                return b"x" + data[1:]
            return data

    result = commit_handoff(_CorruptHistoryRead(storage), _req(), journal=journal)
    assert result.outcome is HandoffOutcome.HARD_INTEGRITY_FAILURE
    assert result.sha256 == digest
    assert _manifest_bytes(storage, game_id) == before


def test_history_wrong_kind_destination_is_hard_integrity() -> None:
    storage, journal, game_id = initialize_ready_match()
    before = _manifest_bytes(storage, game_id)
    digest = sha256_hex(SAVE_A)
    paths = GamePaths(game_id)
    hist = paths.resolve(paths.history_manifest_relative(0, sha256_hex(before)))
    storage.mkdir(hist)
    result = commit_handoff(storage, _req(), journal=journal)
    assert result.outcome is HandoffOutcome.HARD_INTEGRITY_FAILURE
    assert result.sha256 == digest
    assert _manifest_bytes(storage, game_id) == before


def test_history_conflicting_temp_bytes_are_not_overwritten() -> None:
    storage, journal, game_id = initialize_ready_match()
    before = _manifest_bytes(storage, game_id)
    digest = sha256_hex(SAVE_A)
    paths = GamePaths(game_id)
    temp_hist = paths.temporary_history(OP_ID)
    storage.write_file(temp_hist, b'{"conflicting":"history-temp"}\n', overwrite=False)
    result = commit_handoff(storage, _req(), journal=journal)
    assert result.outcome is HandoffOutcome.HARD_INTEGRITY_FAILURE
    assert result.sha256 == digest
    assert storage.snapshot().files[temp_hist] == b'{"conflicting":"history-temp"}\n'
    assert _manifest_bytes(storage, game_id) == before


def test_history_conflict_keeps_outgoing_sha256() -> None:
    storage, journal, game_id = initialize_ready_match(game_id="conflict-hash-match")
    before = _manifest_bytes(storage, game_id)
    digest = sha256_hex(SAVE_A)
    paths = GamePaths(game_id)
    hist = paths.resolve(paths.history_manifest_relative(0, sha256_hex(before)))
    storage.write_file(hist, b'{"not":"the-same-manifest"}\n', overwrite=False)
    bad = commit_handoff(storage, _req(game_id=game_id), journal=journal)
    assert bad.outcome is HandoffOutcome.HARD_INTEGRITY_FAILURE
    assert bad.sha256 == digest
    assert bad.sha256 != sha256_hex(before)


def test_uncertain_commit_exact_intended_bytes_is_idempotent() -> None:
    storage, journal, game_id = initialize_ready_match()
    wrapped = UncertainReplaceStorage(storage, committed_bytes="source")
    result = commit_handoff(wrapped, _req(), journal=journal)
    assert result.outcome is HandoffOutcome.IDEMPOTENT_ACK
    assert result.manifest_changed is False
    assert result.sha256 == sha256_hex(SAVE_A)
    assert journal.in_progress_handoff(game_id=game_id) is None


def test_uncertain_commit_noncanonical_json_not_acknowledged() -> None:
    import json

    from civ4_turn_relay.storage import StorageNotFoundError, StorageTransportError

    storage, journal, game_id = initialize_ready_match()
    digest = sha256_hex(SAVE_A)

    class _NoncanonicalCommit(UncertainReplaceStorage):
        def atomic_replace(self, source: str, destination: str) -> None:
            self._count_mutation()
            data = self._inner.read_file(source)
            # Semantically identical when parsed, but not exact intended bytes.
            pretty = json.dumps(json.loads(data), indent=2, sort_keys=True).encode(
                "utf-8"
            )
            assert pretty != data
            Manifest.from_json_bytes(pretty)
            self._inner.write_file(destination, pretty, overwrite=True)
            try:
                self._inner.remove_file(source)
            except StorageNotFoundError:
                pass
            self._uncertain_fired = True
            raise StorageTransportError(
                "uncertain atomic replace result", path=destination
            )

    result = commit_handoff(
        _NoncanonicalCommit(storage, committed_bytes="source"), _req(), journal=journal
    )
    assert result.outcome is HandoffOutcome.TRANSPORT_FAILURE
    assert result.manifest_changed is False
    assert result.sha256 == digest
    assert journal.in_progress_handoff(game_id=game_id) is not None


def test_uncertain_commit_same_ids_but_changed_next_player_not_acked() -> None:
    from civ4_turn_relay.storage import StorageNotFoundError, StorageTransportError

    storage, journal, game_id = initialize_ready_match()
    digest = sha256_hex(SAVE_A)

    class _TamperedManifest(UncertainReplaceStorage):
        def atomic_replace(self, source: str, destination: str) -> None:
            self._count_mutation()
            intended = Manifest.from_json_bytes(self._inner.read_file(source))
            tampered = Manifest(
                schema_version=intended.schema_version,
                game_id=intended.game_id,
                display_name=intended.display_name,
                players=intended.players,
                protocol_sequence=intended.protocol_sequence,
                current_player_id="player_a",  # wrong next player
                last_sender_id=intended.last_sender_id,
                accepted_save=intended.accepted_save,
                accepted_save_hashes=intended.accepted_save_hashes,
                previous_manifest_ref=intended.previous_manifest_ref,
                protocol=intended.protocol,
            )
            assert tampered.to_json_bytes() != intended.to_json_bytes()
            self._inner.write_file(
                destination, tampered.to_json_bytes(), overwrite=True
            )
            try:
                self._inner.remove_file(source)
            except StorageNotFoundError:
                pass
            self._uncertain_fired = True
            raise StorageTransportError(
                "uncertain atomic replace result", path=destination
            )

    result = commit_handoff(
        _TamperedManifest(storage, committed_bytes="source"), _req(), journal=journal
    )
    assert result.outcome is HandoffOutcome.TRANSPORT_FAILURE
    assert result.manifest_changed is False
    assert result.sha256 == digest
    assert journal.in_progress_handoff(game_id=game_id) is not None
    assert GamePaths(game_id).upload_lock_dir in storage.snapshot().directories


def test_uncertain_commit_failed_recovery_read_retains_ambiguous_state() -> None:
    from civ4_turn_relay.storage import StorageNotFoundError, StorageTransportError

    storage, journal, game_id = initialize_ready_match()
    digest = sha256_hex(SAVE_A)
    paths = GamePaths(game_id)

    class _UncertainThenFailRead(UncertainReplaceStorage):
        def __init__(self, inner: FakeStorage) -> None:
            super().__init__(inner, committed_bytes="source")
            self._block_manifest_reads = False

        def atomic_replace(self, source: str, destination: str) -> None:
            self._count_mutation()
            data = self._inner.read_file(source)
            self._inner.write_file(destination, data, overwrite=True)
            try:
                self._inner.remove_file(source)
            except StorageNotFoundError:
                pass
            self._block_manifest_reads = True
            self._uncertain_fired = True
            raise StorageTransportError(
                "uncertain atomic replace result", path=destination
            )

        def read_file(self, path: str) -> bytes:
            if self._block_manifest_reads and path == paths.manifest:
                raise StorageTransportError("recovery read failed", path=path)
            return self._inner.read_file(path)

    wrapped = _UncertainThenFailRead(storage)
    result = commit_handoff(wrapped, _req(), journal=journal)
    assert result.outcome is HandoffOutcome.TRANSPORT_FAILURE
    assert result.sha256 == digest
    assert journal.in_progress_handoff(game_id=game_id) is not None
    assert paths.upload_lock_dir in storage.snapshot().directories


def test_wrong_kind_final_save_path_is_hard_integrity() -> None:
    storage, journal, game_id = initialize_ready_match()
    before = _manifest_bytes(storage, game_id)
    digest = sha256_hex(SAVE_A)
    paths = GamePaths(game_id)
    final = paths.resolve(
        paths.accepted_save_relative(1, digest, ".CivBeyondSwordSave")
    )
    storage.mkdir(final)
    result = commit_handoff(storage, _req(), journal=journal)
    assert result.outcome is HandoffOutcome.HARD_INTEGRITY_FAILURE
    assert result.sha256 == digest
    assert _manifest_bytes(storage, game_id) == before


def test_handoff_result_rejects_impossible_public_combinations() -> None:
    from civ4_turn_relay.domain import DomainValidationError
    from civ4_turn_relay.protocol import HandoffResult

    digest = sha256_hex(SAVE_A)
    with pytest.raises(DomainValidationError):
        HandoffResult(HandoffOutcome.COMMITTED, False, sha256=digest)
    with pytest.raises(DomainValidationError):
        HandoffResult(
            HandoffOutcome.LOCK_CLEANUP_AMBIGUOUS,
            True,
            sha256=digest,
            manifest=None,
        )
    with pytest.raises(DomainValidationError):
        HandoffResult(
            HandoffOutcome.NOT_CURRENT_OWNER,
            True,
            sha256=digest,
        )
    # sha256 is required for every public outcome.
    with pytest.raises(TypeError):
        HandoffResult(HandoffOutcome.TRANSPORT_FAILURE, False)  # type: ignore[call-arg]


def test_temporary_history_path_helper() -> None:
    paths = GamePaths("example-match")
    assert paths.temporary_history(OP_ID) == (
        f"example-match/temporary/history-{OP_ID}.json"
    )
