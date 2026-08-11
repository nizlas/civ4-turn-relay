"""Verified download protocol tests (PT-32, PT-33)."""

from __future__ import annotations

import pytest

from civ4_turn_relay.domain import DomainValidationError, sha256_hex
from civ4_turn_relay.protocol import (
    DownloadOutcome,
    DownloadRequest,
    DownloadResult,
    GamePaths,
    HandoffRequest,
    InMemoryOperationJournal,
    VerifiedDownloadEvidence,
    commit_handoff,
    download_accepted_save,
)
from civ4_turn_relay.storage import (
    FakeStorage,
    FaultMoment,
    StorageCapabilities,
    StorageOp,
)
from tests.protocol.helpers import (
    CLIENT_A,
    NOW_UTC,
    OP_ID,
    SAVE_NAME,
    CountingStorage,
    initialize_ready_match,
)

SAVE_A = b"synthetic-outgoing-save-bytes-player-a-v1"


def _commit_owner_turn(
    storage: FakeStorage,
) -> tuple[InMemoryOperationJournal, str, str]:
    journal = InMemoryOperationJournal()
    _, _, game_id = initialize_ready_match(storage=storage, journal=journal)
    result = commit_handoff(
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
    assert result.outcome.value == "committed"
    return journal, game_id, sha256_hex(SAVE_A)


@pytest.mark.pt("PT-32")
def test_pt32_download_hash_mismatch_no_verified_artifact() -> None:
    storage = FakeStorage()
    _journal, game_id, digest = _commit_owner_turn(storage)
    paths = GamePaths(game_id)
    save_path = paths.resolve(
        paths.accepted_save_relative(1, digest, ".CivBeyondSwordSave")
    )
    # Corrupt remote bytes while keeping declared size.
    corrupted = b"x" + SAVE_A[1:]
    assert len(corrupted) == len(SAVE_A)
    storage.write_file(save_path, corrupted, overwrite=True)

    result = download_accepted_save(
        storage,
        DownloadRequest(game_id=game_id, local_player_id="player_b"),
    )
    assert result.outcome is DownloadOutcome.HASH_MISMATCH
    assert result.artifact is None


@pytest.mark.pt("PT-32")
def test_pt32_download_size_mismatch_no_verified_artifact() -> None:
    storage = FakeStorage()
    _journal, game_id, digest = _commit_owner_turn(storage)
    paths = GamePaths(game_id)
    save_path = paths.resolve(
        paths.accepted_save_relative(1, digest, ".CivBeyondSwordSave")
    )
    storage.write_file(save_path, SAVE_A + b"!", overwrite=True)

    result = download_accepted_save(
        storage,
        DownloadRequest(game_id=game_id, local_player_id="player_b"),
    )
    assert result.outcome is DownloadOutcome.SIZE_MISMATCH
    assert result.artifact is None


@pytest.mark.pt("PT-33")
def test_pt33_download_retry_same_evidence_skips_save_read() -> None:
    storage = FakeStorage()
    _journal, game_id, digest = _commit_owner_turn(storage)
    first = download_accepted_save(
        storage,
        DownloadRequest(game_id=game_id, local_player_id="player_b"),
    )
    assert first.outcome is DownloadOutcome.VERIFIED
    assert first.artifact is not None
    assert first.artifact.verified_bytes == SAVE_A

    class _ReadSpy(CountingStorage):
        def __init__(self, inner: FakeStorage) -> None:
            super().__init__(inner)
            self.save_reads = 0

        def read_file(self, path: str) -> bytes:
            self._record("read_file")
            if "/saves/" in path:
                self.save_reads += 1
            return self._inner.read_file(path)

    spy = _ReadSpy(storage)
    evidence = VerifiedDownloadEvidence(
        game_id=game_id,
        protocol_sequence=1,
        sha256=digest,
        size_bytes=len(SAVE_A),
    )
    second = download_accepted_save(
        spy,
        DownloadRequest(
            game_id=game_id,
            local_player_id="player_b",
            prior_evidence=evidence,
        ),
    )
    assert second.outcome is DownloadOutcome.ALREADY_VERIFIED
    assert second.artifact is None
    assert spy.save_reads == 0


def test_stale_evidence_does_not_suppress_download() -> None:
    storage = FakeStorage()
    _journal, game_id, digest = _commit_owner_turn(storage)
    stale = VerifiedDownloadEvidence(
        game_id=game_id,
        protocol_sequence=1,
        sha256="a" * 64,
        size_bytes=len(SAVE_A),
    )
    result = download_accepted_save(
        storage,
        DownloadRequest(
            game_id=game_id,
            local_player_id="player_b",
            prior_evidence=stale,
        ),
    )
    assert result.outcome is DownloadOutcome.VERIFIED
    assert result.artifact is not None
    assert result.artifact.sha256 == digest


def test_changed_sequence_invalidates_prior_evidence() -> None:
    storage = FakeStorage()
    _journal, game_id, digest = _commit_owner_turn(storage)
    evidence = VerifiedDownloadEvidence(
        game_id=game_id,
        protocol_sequence=2,
        sha256=digest,
        size_bytes=len(SAVE_A),
    )
    result = download_accepted_save(
        storage,
        DownloadRequest(
            game_id=game_id,
            local_player_id="player_b",
            prior_evidence=evidence,
        ),
    )
    assert result.outcome is DownloadOutcome.VERIFIED
    assert result.artifact is not None


def test_non_owner_download_rejected() -> None:
    storage = FakeStorage()
    _journal, game_id, _digest = _commit_owner_turn(storage)
    result = download_accepted_save(
        storage,
        DownloadRequest(game_id=game_id, local_player_id="player_a"),
    )
    assert result.outcome is DownloadOutcome.NOT_CURRENT_OWNER
    assert result.artifact is None


def test_sequence_zero_is_no_downloadable_turn() -> None:
    storage, _journal, game_id = initialize_ready_match()
    result = download_accepted_save(
        storage,
        DownloadRequest(game_id=game_id, local_player_id="player_a"),
    )
    assert result.outcome is DownloadOutcome.NO_DOWNLOADABLE_TURN
    assert result.artifact is None


def test_download_wrong_kind_and_transport_and_oversize() -> None:
    storage = FakeStorage()
    _journal, game_id, digest = _commit_owner_turn(storage)
    paths = GamePaths(game_id)
    save_path = paths.resolve(
        paths.accepted_save_relative(1, digest, ".CivBeyondSwordSave")
    )
    storage.remove_file(save_path)
    storage.mkdir(save_path)
    wrong = download_accepted_save(
        storage,
        DownloadRequest(game_id=game_id, local_player_id="player_b"),
    )
    assert wrong.outcome is DownloadOutcome.WRONG_KIND

    storage2 = FakeStorage()
    _journal2, game_id2, _d2 = _commit_owner_turn(storage2)
    storage2.faults.reset()
    # READ #1 manifest, #2 save object.
    storage2.faults.inject(StorageOp.READ, moment=FaultMoment.BEFORE, occurrence=2)
    transport = download_accepted_save(
        storage2,
        DownloadRequest(game_id=game_id2, local_player_id="player_b"),
    )
    assert transport.outcome is DownloadOutcome.TRANSPORT_FAILURE

    storage3 = FakeStorage()
    _journal3, game_id3, _d3 = _commit_owner_turn(storage3)
    oversize = download_accepted_save(
        storage3,
        DownloadRequest(
            game_id=game_id3,
            local_player_id="player_b",
            max_save_bytes=1,
        ),
    )
    assert oversize.outcome is DownloadOutcome.OVERSIZE


def test_download_result_rejects_impossible_artifact_combinations() -> None:
    # Constructing VERIFIED without artifact must fail.
    with pytest.raises(DomainValidationError):
        DownloadResult(DownloadOutcome.VERIFIED)
    # Failures must not carry an artifact (validated when one is supplied).
    storage = FakeStorage()
    _journal, game_id, digest = _commit_owner_turn(storage)
    ok = download_accepted_save(
        storage,
        DownloadRequest(game_id=game_id, local_player_id="player_b"),
    )
    assert ok.artifact is not None
    with pytest.raises(DomainValidationError):
        DownloadResult(DownloadOutcome.HASH_MISMATCH, artifact=ok.artifact)


def test_capability_failure_without_complete_readback() -> None:
    storage = FakeStorage()
    _journal, game_id, _digest = _commit_owner_turn(storage)

    class _NoReadback(CountingStorage):
        def capabilities(self) -> StorageCapabilities:
            return StorageCapabilities(
                exclusive_mkdir=True,
                atomic_replace=True,
                atomic_publish_no_replace=True,
                complete_readback=False,
            )

    result = download_accepted_save(
        _NoReadback(storage),
        DownloadRequest(game_id=game_id, local_player_id="player_b"),
    )
    assert result.outcome is DownloadOutcome.CAPABILITY_FAILURE
