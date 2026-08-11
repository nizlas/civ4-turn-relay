"""Blocker regressions: handoff attribution, promote, stability, watch."""

from __future__ import annotations

import uuid
from dataclasses import replace
from pathlib import Path

import pytest

from civ4_turn_relay.domain import (
    MatchConfig,
    SaveMatchingRules,
    TurnHandlingMode,
    sha256_hex,
)
from civ4_turn_relay.fs import MatchMonitor, PollingWatcher, WatchFallbackReason
from civ4_turn_relay.local import (
    STABILITY_INTERVAL_SECONDS,
    DetectionOutcome,
    DurableHandoffJournal,
    FakeClock,
    HandoffEvidence,
    LocalStore,
    MatchLocalRecords,
    OrchestrationIntentKind,
    PlaySessionBaseline,
    ProcessAssociationRecord,
    ProcessObservation,
    PromoteOutcome,
    StabilityObservation,
    attribute_handoff_result,
    capture_play_session_baseline,
    observe_outgoing_candidates,
    promote_verified_download,
    reconcile_match,
    revalidate_candidate_file,
)
from civ4_turn_relay.local.json_store import publish_no_replace
from civ4_turn_relay.protocol import (
    HandoffOutcome,
    HandoffRequest,
    InMemoryOperationJournal,
    InProgressHandoff,
    VerifiedDownloadArtifact,
    commit_handoff,
)
from civ4_turn_relay.storage import FakeStorage, StorageOp
from tests.protocol.helpers import (
    CLIENT_A,
    NOW_UTC,
    OP_ID,
    SAVE_NAME,
    initialize_ready_match,
    sample_players,
)

SAVE_A = b"synthetic-outgoing-save-bytes-player-a-v1"
SAVE_B = b"synthetic-outgoing-save-bytes-player-b-v2"
FIXED_UUID = uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
GLOB = "*.CivBeyondSwordSave"
EXE = r"C:\Games\Civ4\BeyondSword.exe"


def _pbem(tmp_path: Path) -> Path:
    path = tmp_path / "pbem"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _store(tmp_path: Path) -> LocalStore:
    store = LocalStore(tmp_path)
    store.get_or_create_installation_identity(uuid_factory=lambda: FIXED_UUID)
    return store


def _config(tmp_path: Path, *, local_player_id: str = "player_a") -> MatchConfig:
    return MatchConfig(
        game_id="example-match",
        display_name="Example Match",
        players=sample_players()[:2],
        local_player_id=local_player_id,
        launch_profile=None,
        mod_name=None,
        pbem_save_directory=str(_pbem(tmp_path)),
        save_matching=SaveMatchingRules(filename_glob=GLOB),
        turn_handling_mode=TurnHandlingMode.FULLY_MANAGED,
        allow_force_close_after_commit=False,
    )


def _commit_a(storage: FakeStorage) -> tuple[str, str]:
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
    assert result.outcome is HandoffOutcome.COMMITTED
    return game_id, sha256_hex(SAVE_A)


def _baseline(
    pbem: Path, *, sequence: int = 0, digest: str | None = None
) -> PlaySessionBaseline:
    return capture_play_session_baseline(
        str(pbem),
        SaveMatchingRules(filename_glob=GLOB),
        protocol_sequence=sequence,
        accepted_sha256=digest,
        recorded_at=NOW_UTC,
        max_save_bytes=10_000_000,
    )


def test_stringly_outcome_name_rejected() -> None:
    with pytest.raises(TypeError):
        HandoffEvidence(  # type: ignore[call-arg]
            outcome_name="committed",
            sha256=sha256_hex(SAVE_A),
            protocol_sequence=1,
        )


def test_attribute_handoff_rejects_stale_manifest_sequence() -> None:
    storage = FakeStorage()
    game_id, digest = _commit_a(storage)
    request = HandoffRequest(
        game_id=game_id,
        local_player_id="player_a",
        client_id=CLIENT_A,
        operation_id=OP_ID,
        outgoing_bytes=SAVE_A,
        original_filename=SAVE_NAME,
        now_utc=NOW_UTC,
    )
    result = commit_handoff(
        storage,
        request,
        journal=InMemoryOperationJournal(),
    )
    # Idempotent ack is fine for source 0; wrong source must not attribute.
    assert (
        attribute_handoff_result(
            request=request,
            result=result,
            source_protocol_sequence=0,
        )
        is not None
    )
    assert (
        attribute_handoff_result(
            request=request,
            result=result,
            source_protocol_sequence=3,
        )
        is None
    )
    del digest


def test_promote_race_exact_match_already_present(tmp_path: Path) -> None:
    storage = FakeStorage()
    game_id, digest = _commit_a(storage)
    pbem = _pbem(tmp_path)
    artifact = VerifiedDownloadArtifact(
        game_id=game_id,
        protocol_sequence=1,
        sha256=digest,
        size_bytes=len(SAVE_A),
        remote_path=f"saves/000001_{digest[:12]}.CivBeyondSwordSave",
        original_filename=SAVE_NAME,
        verified_bytes=SAVE_A,
    )
    destination = pbem / SAVE_NAME

    def racing_publish(source: str, dest: str) -> bool:
        # Destination appears after the initial existence check, before publish.
        Path(dest).write_bytes(SAVE_A)
        return publish_no_replace(source, dest)

    result = promote_verified_download(
        artifact,
        str(pbem),
        publish_no_replace_fn=racing_publish,
    )
    assert result.outcome is PromoteOutcome.ALREADY_PRESENT
    assert destination.read_bytes() == SAVE_A


def test_promote_race_conflict_never_overwrites(tmp_path: Path) -> None:
    storage = FakeStorage()
    game_id, digest = _commit_a(storage)
    pbem = _pbem(tmp_path)
    artifact = VerifiedDownloadArtifact(
        game_id=game_id,
        protocol_sequence=1,
        sha256=digest,
        size_bytes=len(SAVE_A),
        remote_path=f"saves/000001_{digest[:12]}.CivBeyondSwordSave",
        original_filename=SAVE_NAME,
        verified_bytes=SAVE_A,
    )
    winner = b"other-writer-won-the-race!!!!"

    def racing_publish(source: str, dest: str) -> bool:
        Path(dest).write_bytes(winner)
        return False

    result = promote_verified_download(
        artifact,
        str(pbem),
        publish_no_replace_fn=racing_publish,
    )
    assert result.outcome is PromoteOutcome.CONFLICT
    assert (pbem / SAVE_NAME).read_bytes() == winner


def test_prior_download_invalidated_when_local_missing_or_changed(
    tmp_path: Path,
) -> None:
    storage = FakeStorage()
    game_id, digest = _commit_a(storage)
    store = _store(tmp_path)
    config = _config(tmp_path, local_player_id="player_b")
    store.write_match_config(config)
    first = reconcile_match(
        storage,
        store,
        config,
        client_id=str(FIXED_UUID),
        journal=DurableHandoffJournal(store, game_id=game_id),
        clock=FakeClock(),
        now_utc=NOW_UTC,
    )
    assert first.records.downloaded_save is not None
    local_path = Path(first.records.downloaded_save.local_path)
    assert local_path.is_file()

    # Deleted local save forces a real remote re-download.
    local_path.unlink()
    reads_before = storage.faults.call_count(StorageOp.READ)
    recovered = reconcile_match(
        storage,
        store,
        config,
        client_id=str(FIXED_UUID),
        journal=DurableHandoffJournal(store, game_id=game_id),
        clock=FakeClock(),
        now_utc=NOW_UTC,
    )
    assert recovered.records.downloaded_save is not None
    assert Path(recovered.records.downloaded_save.local_path).read_bytes() == SAVE_A
    assert storage.faults.call_count(StorageOp.READ) > reads_before

    # Truncated file invalidates prior evidence and forces a remote read.
    # Safe promotion refuses to overwrite the corrupt destination (CONFLICT).
    Path(recovered.records.downloaded_save.local_path).write_bytes(SAVE_A[:4])
    reads_mid = storage.faults.call_count(StorageOp.READ)
    truncated = reconcile_match(
        storage,
        store,
        config,
        client_id=str(FIXED_UUID),
        journal=DurableHandoffJournal(store, game_id=game_id),
        clock=FakeClock(),
        now_utc=NOW_UTC,
    )
    assert storage.faults.call_count(StorageOp.READ) > reads_mid
    assert truncated.records.downloaded_save is None
    assert truncated.operational_state.value == "ERROR"
    assert Path(local_path).read_bytes() == SAVE_A[:4]

    # Restore a clean local file, then same-size rewrite → remote read again,
    # still no overwrite of the conflicting bytes.
    local_path.write_bytes(SAVE_A)
    store.write_match_state(
        replace(
            recovered.records,
            downloaded_save=recovered.records.downloaded_save,
            last_operational_state=recovered.operational_state,
        )
    )
    altered = b"X" * len(SAVE_A)
    local_path.write_bytes(altered)
    reads_end = storage.faults.call_count(StorageOp.READ)
    modified = reconcile_match(
        storage,
        store,
        config,
        client_id=str(FIXED_UUID),
        journal=DurableHandoffJournal(store, game_id=game_id),
        clock=FakeClock(),
        now_utc=NOW_UTC,
    )
    assert storage.faults.call_count(StorageOp.READ) > reads_end
    assert modified.records.downloaded_save is None
    assert local_path.read_bytes() == altered

    # Escaping path invalidates evidence; after removing the in-tree conflict,
    # a contained promotion succeeds from a real remote read.
    local_path.unlink()
    escape = tmp_path / "outside.CivBeyondSwordSave"
    escape.write_bytes(SAVE_A)
    store.write_match_state(
        replace(
            recovered.records,
            downloaded_save=replace(
                recovered.records.downloaded_save,
                local_path=str(escape.resolve()),
            ),
        )
    )
    reads_escape = storage.faults.call_count(StorageOp.READ)
    escaped = reconcile_match(
        storage,
        store,
        config,
        client_id=str(FIXED_UUID),
        journal=DurableHandoffJournal(store, game_id=game_id),
        clock=FakeClock(),
        now_utc=NOW_UTC,
    )
    assert storage.faults.call_count(StorageOp.READ) > reads_escape
    assert escaped.records.downloaded_save is not None
    promoted = Path(escaped.records.downloaded_save.local_path)
    assert promoted.is_relative_to(Path(config.pbem_save_directory).resolve())
    assert promoted.read_bytes() == SAVE_A


def test_stability_session_scoped_and_bounded(tmp_path: Path) -> None:
    pbem = _pbem(tmp_path)
    clock = FakeClock()
    records = MatchLocalRecords(
        game_id="example-match",
        play_session_baseline=_baseline(pbem, sequence=0),
    )
    path = pbem / "Turn.CivBeyondSwordSave"
    path.write_bytes(SAVE_A)
    first = observe_outgoing_candidates(
        str(pbem),
        SaveMatchingRules(filename_glob=GLOB),
        records,
        (),
        clock=clock,
        max_save_bytes=10_000_000,
    )
    for _ in range(20):
        clock.advance(0.05)
        first = observe_outgoing_candidates(
            str(pbem),
            SaveMatchingRules(filename_glob=GLOB),
            replace(records, stability_observations=first.observations),
            (),
            clock=clock,
            max_save_bytes=10_000_000,
        )
    assert len(first.observations) <= 2

    clock.advance(STABILITY_INTERVAL_SECONDS)
    stable = observe_outgoing_candidates(
        str(pbem),
        SaveMatchingRules(filename_glob=GLOB),
        replace(records, stability_observations=first.observations),
        (),
        clock=clock,
        max_save_bytes=10_000_000,
    )
    assert stable.outcome is DetectionOutcome.ONE_CANDIDATE
    candidate = stable.candidates[0]

    # Same path/size rewrite with new mtime must not reuse old stability alone.
    path.write_bytes(b"Z" * len(SAVE_A))
    assert (
        revalidate_candidate_file(
            candidate,
            pbem_save_directory=str(pbem),
            max_save_bytes=10_000_000,
        )
        is None
    )

    # Next-turn baseline (new session) ignores prior observations.
    next_records = MatchLocalRecords(
        game_id="example-match",
        play_session_baseline=_baseline(pbem, sequence=1, digest=sha256_hex(SAVE_A)),
        stability_observations=stable.observations,
    )
    next_obs = observe_outgoing_candidates(
        str(pbem),
        SaveMatchingRules(filename_glob=GLOB),
        next_records,
        (sha256_hex(SAVE_A),),
        clock=clock,
        max_save_bytes=10_000_000,
    )
    assert all(item.session_protocol_sequence == 1 for item in next_obs.observations)
    assert next_obs.outcome is not DetectionOutcome.ONE_CANDIDATE


def test_stale_observations_after_restart_belong_to_old_baseline(
    tmp_path: Path,
) -> None:
    pbem = _pbem(tmp_path)
    baseline = _baseline(pbem, sequence=1, digest=sha256_hex(SAVE_A))
    path = pbem / "Turn.CivBeyondSwordSave"
    path.write_bytes(SAVE_B)
    old = StabilityObservation(
        path=str(path.resolve()),
        size_bytes=len(SAVE_B),
        observed_at_seconds=0.0,
        mtime_ns=path.stat().st_mtime_ns,
        session_protocol_sequence=0,
        session_accepted_sha256=None,
        session_baseline_recorded_at="2026-01-01T00:00:00Z",
    )
    records = MatchLocalRecords(
        game_id="example-match",
        play_session_baseline=baseline,
        stability_observations=(old, replace(old, observed_at_seconds=2.0)),
    )
    result = observe_outgoing_candidates(
        str(pbem),
        SaveMatchingRules(filename_glob=GLOB),
        records,
        (sha256_hex(SAVE_A),),
        clock=FakeClock(start=10.0),
        max_save_bytes=10_000_000,
    )
    assert result.outcome is DetectionOutcome.STABILIZING
    assert all(item.session_protocol_sequence == 1 for item in result.observations)


def test_runtime_watcher_fallback_discovers_without_primary_events(
    tmp_path: Path,
) -> None:
    class FlakyPrimary:
        def __init__(self) -> None:
            self._healthy = True
            self.events = 0

        def start(self, root: str, on_event: object) -> None:
            del root, on_event
            self._healthy = True

        def stop(self) -> None:
            return None

        def is_healthy(self) -> bool:
            return self._healthy

        def fail(self) -> None:
            self._healthy = False

        def poll(self) -> None:
            raise AssertionError("primary must not be polled after fallback")

    pbem = _pbem(tmp_path)
    clock = FakeClock()
    primary = FlakyPrimary()
    seen: list[object] = []
    monitor = MatchMonitor(
        clock=clock,
        poll_interval_seconds=0.1,
        primary=primary,
        fallback=PollingWatcher(clock=clock, poll_interval_seconds=0.1),
    )
    monitor.start(str(pbem), seen.append)
    assert monitor.fallback_reason.value == WatchFallbackReason.NONE.value
    assert monitor.active_watcher is not None

    primary.fail()
    monitor.poll()
    assert monitor.fell_back_at_runtime is True
    assert (
        monitor.fallback_reason.value
        == WatchFallbackReason.PRIMARY_UNHEALTHY_AT_RUNTIME.value
    )
    assert isinstance(monitor.active_watcher, PollingWatcher)

    target = pbem / "Detected.CivBeyondSwordSave"
    target.write_bytes(b"discovered-by-polling")
    clock.advance(0.2)
    monitor.poll()
    assert seen, "polling fallback must discover the save without primary events"
    monitor.stop()
    monitor.stop()  # idempotent


def test_foreign_journal_not_applied(tmp_path: Path) -> None:
    storage = FakeStorage()
    game_id, digest = _commit_a(storage)
    store = _store(tmp_path)
    config = _config(tmp_path, local_player_id="player_b")
    store.write_match_config(config)
    store.write_match_state(
        MatchLocalRecords(
            game_id=game_id,
            in_progress_handoff=InProgressHandoff(
                game_id=game_id,
                operation_id=OP_ID,
                client_id=CLIENT_A,
                player_id="player_a",
                sha256=digest,
                protocol_sequence=0,
            ),
        )
    )
    result = reconcile_match(
        storage,
        store,
        config,
        client_id=str(FIXED_UUID),
        journal=DurableHandoffJournal(store, game_id=game_id),
        clock=FakeClock(),
        now_utc=NOW_UTC,
    )
    assert result.records.in_progress_handoff is not None
    assert digest not in result.records.processed_outgoing_hashes
    assert OrchestrationIntentKind.REQUEST_GRACEFUL_CLOSE not in {
        intent.kind for intent in result.intents
    }
    assert any(d.name == "foreign_or_stale_journal" for d in result.diagnostics)


def test_waiting_still_emits_post_commit_close() -> None:
    evidence = HandoffEvidence(
        outcome=HandoffOutcome.COMMITTED,
        game_id="example-match",
        operation_id=OP_ID,
        local_player_id="player_a",
        sha256=sha256_hex(SAVE_A),
        size_bytes=len(SAVE_A),
        source_protocol_sequence=0,
        result_protocol_sequence=1,
    )
    records = MatchLocalRecords(
        game_id="example-match",
        pending_post_commit_close=None,
        process_association=ProcessAssociationRecord(
            protocol_sequence=0,
            accepted_sha256=None,
            pid=7,
            process_start_time_utc=NOW_UTC,
            executable_path=EXE,
            associated_at=NOW_UTC,
        ),
    )
    from civ4_turn_relay.domain import OperationalState
    from civ4_turn_relay.local import decide_intents

    intents = decide_intents(
        TurnHandlingMode.FULLY_MANAGED,
        False,
        OperationalState.WAITING_FOR_OTHER_PLAYER,
        records,
        None,
        ProcessObservation(
            pid=7,
            process_start_time_utc=NOW_UTC,
            executable_path=EXE,
            running=True,
        ),
        evidence,
        False,
    )
    assert OrchestrationIntentKind.REQUEST_GRACEFUL_CLOSE in {
        intent.kind for intent in intents
    }
