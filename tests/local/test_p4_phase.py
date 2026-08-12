"""P4 local persistence, reconciliation, detection, watching, orchestration."""

from __future__ import annotations

import uuid
from dataclasses import replace
from pathlib import Path

import pytest

from civ4_turn_relay.domain import (
    REDACTED,
    DomainValidationError,
    MatchConfig,
    OperationalState,
    SaveMatchingRules,
    TurnHandlingMode,
    sha256_hex,
)
from civ4_turn_relay.fs import MatchMonitor, PollingWatcher
from civ4_turn_relay.fs.watchdog_adapter import UnavailableWatcher
from civ4_turn_relay.local import (
    STABILITY_INTERVAL_SECONDS,
    DetectionOutcome,
    DetectionResult,
    DurableHandoffJournal,
    FakeClock,
    HandoffEvidence,
    InstallationIdentity,
    LocalStore,
    MatchLocalRecords,
    OrchestrationIntentKind,
    PlaySessionBaseline,
    ProcessAssociationRecord,
    ProcessObservation,
    PromoteOutcome,
    ReconcileResult,
    capture_play_session_baseline,
    decide_intents,
    emit_diagnostic,
    observe_outgoing_candidates,
    promote_verified_download,
    reconcile_match,
)
from civ4_turn_relay.protocol import (
    GamePaths,
    HandoffOutcome,
    HandoffRequest,
    InMemoryOperationJournal,
    VerifiedDownloadArtifact,
    commit_handoff,
)
from civ4_turn_relay.storage import FakeStorage, FaultMoment, Storage, StorageOp
from tests.protocol.helpers import (
    CLIENT_A,
    HASH_1,
    NOW_UTC,
    OP_ID,
    SAVE_NAME,
    CountingStorage,
    initialize_ready_match,
    sample_players,
)

SAVE_A = b"synthetic-outgoing-save-bytes-player-a-v1"
SAVE_B = b"synthetic-outgoing-save-bytes-player-b-v2"
CREATE_NS = 1_760_184_000_000_004_242
FIXED_UUID = uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
CLIENT_UUID = str(FIXED_UUID)
GLOB = "*.CivBeyondSwordSave"


def _pbem_dir(tmp_path: Path) -> Path:
    pbem = tmp_path / "pbem"
    pbem.mkdir(parents=True, exist_ok=True)
    return pbem


def _match_config(
    tmp_path: Path,
    *,
    game_id: str = "example-match",
    local_player_id: str = "player_a",
    turn_handling_mode: TurnHandlingMode = TurnHandlingMode.FULLY_MANAGED,
    allow_force_close: bool = False,
    filename_glob: str = GLOB,
) -> MatchConfig:
    return MatchConfig(
        game_id=game_id,
        display_name="Example Match",
        players=sample_players()[:2],
        local_player_id=local_player_id,
        launch_profile=None,
        mod_name=None,
        pbem_save_directory=str(_pbem_dir(tmp_path)),
        save_matching=SaveMatchingRules(filename_glob=filename_glob),
        turn_handling_mode=turn_handling_mode,
        allow_force_close_after_commit=allow_force_close,
    )


def _local_store(tmp_path: Path) -> LocalStore:
    store = LocalStore(tmp_path)
    store.get_or_create_installation_identity(uuid_factory=lambda: FIXED_UUID)
    return store


def _journal(store: LocalStore, game_id: str) -> DurableHandoffJournal:
    return DurableHandoffJournal(store, game_id=game_id)


def _reconcile(
    storage: Storage,
    store: LocalStore,
    config: MatchConfig,
    *,
    clock: FakeClock | None = None,
    now_utc: str = NOW_UTC,
    process_observation: ProcessObservation | None = None,
    user_requested_start: bool = False,
    handoff_evidence: HandoffEvidence | None = None,
) -> ReconcileResult:
    resolved_clock = clock if clock is not None else FakeClock()
    return reconcile_match(
        storage,
        store,
        config,
        client_id=CLIENT_UUID,
        journal=_journal(store, config.game_id),
        clock=resolved_clock,
        now_utc=now_utc,
        process_observation=process_observation,
        user_requested_start=user_requested_start,
        handoff_evidence=handoff_evidence,
    )


def _commit_player_a_turn(storage: FakeStorage) -> tuple[str, str]:
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


def _write_save(pbem: Path, name: str, data: bytes) -> Path:
    path = pbem / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _baseline_for(
    pbem: Path,
    *,
    protocol_sequence: int = 0,
    accepted_sha256: str | None = None,
) -> PlaySessionBaseline:
    resolved_hash = accepted_sha256
    if protocol_sequence > 0 and resolved_hash is None:
        resolved_hash = HASH_1
    return capture_play_session_baseline(
        str(pbem),
        SaveMatchingRules(filename_glob=GLOB),
        protocol_sequence=protocol_sequence,
        accepted_sha256=resolved_hash,
        recorded_at=NOW_UTC,
        max_save_bytes=10_000_000,
    )


def _records_with_baseline(
    game_id: str,
    pbem: Path,
    *,
    protocol_sequence: int = 1,
    accepted_sha256: str | None = None,
) -> MatchLocalRecords:
    return MatchLocalRecords(
        game_id=game_id,
        play_session_baseline=_baseline_for(
            pbem,
            protocol_sequence=protocol_sequence,
            accepted_sha256=accepted_sha256,
        ),
    )


def _stable_candidate(
    pbem: Path,
    records: MatchLocalRecords,
    clock: FakeClock,
    *,
    name: str = "Fresh.CivBeyondSwordSave",
    data: bytes = SAVE_B,
    accepted_hashes: tuple[str, ...] = (),
) -> tuple[MatchLocalRecords, DetectionResult]:
    _write_save(pbem, name, data)
    first = observe_outgoing_candidates(
        str(pbem),
        SaveMatchingRules(filename_glob=GLOB),
        records,
        accepted_hashes,
        clock=clock,
        max_save_bytes=10_000_000,
    )
    assert first.outcome is DetectionOutcome.STABILIZING
    clock.advance(STABILITY_INTERVAL_SECONDS)
    second = observe_outgoing_candidates(
        str(pbem),
        SaveMatchingRules(filename_glob=GLOB),
        replace(records, stability_observations=first.observations),
        accepted_hashes,
        clock=clock,
        max_save_bytes=10_000_000,
    )
    return replace(records, stability_observations=second.observations), second


def _process_obs(*, running: bool = True) -> ProcessObservation:
    return ProcessObservation(
        pid=4242,
        process_start_time_utc=NOW_UTC,
        process_create_time_ns=CREATE_NS,
        executable_path=r"C:\Games\Civ4\BeyondSword.exe",
        running=running,
    )


def _artifact(
    game_id: str,
    digest: str,
    *,
    data: bytes = SAVE_A,
    filename: str = SAVE_NAME,
    sequence: int = 1,
) -> VerifiedDownloadArtifact:
    return VerifiedDownloadArtifact(
        game_id=game_id,
        protocol_sequence=sequence,
        sha256=digest,
        size_bytes=len(data),
        remote_path=f"saves/000001_{digest[:12]}.CivBeyondSwordSave",
        original_filename=filename,
        verified_bytes=data,
    )


# --- Reconciliation ---------------------------------------------------------


def test_reconcile_seq0_first_turn(tmp_path: Path) -> None:
    storage = FakeStorage()
    initialize_ready_match(storage=storage)
    store = _local_store(tmp_path)
    config = _match_config(tmp_path)
    store.write_match_config(config)

    result = _reconcile(storage, store, config)

    assert result.operational_state is OperationalState.WAITING_FOR_MY_FIRST_SAVE
    assert result.records.last_transition_reason == "awaiting_first_save"
    assert result.retry_required is False


def test_reconcile_waiting_for_other_player(tmp_path: Path) -> None:
    storage = FakeStorage()
    _commit_player_a_turn(storage)
    store = _local_store(tmp_path)
    config = _match_config(tmp_path, local_player_id="player_a")
    store.write_match_config(config)

    result = _reconcile(storage, store, config)

    assert result.operational_state is OperationalState.WAITING_FOR_OTHER_PLAYER
    assert result.records.last_transition_reason == "not_current_owner"


def test_reconcile_download_and_promote(tmp_path: Path) -> None:
    storage = FakeStorage()
    game_id, digest = _commit_player_a_turn(storage)
    store = _local_store(tmp_path)
    config = _match_config(tmp_path, local_player_id="player_b")
    store.write_match_config(config)
    pbem = Path(config.pbem_save_directory)

    result = _reconcile(storage, store, config)

    assert result.operational_state is OperationalState.MY_TURN_DOWNLOADED
    assert result.records.downloaded_save is not None
    assert result.records.downloaded_save.sha256 == digest
    promoted = pbem / SAVE_NAME
    assert promoted.is_file()
    assert promoted.read_bytes() == SAVE_A


def test_reconcile_already_verified_skips_save_read(tmp_path: Path) -> None:
    storage = FakeStorage()
    game_id, digest = _commit_player_a_turn(storage)
    store = _local_store(tmp_path)
    config = _match_config(tmp_path, local_player_id="player_b")
    store.write_match_config(config)

    first = _reconcile(storage, store, config)
    assert first.records.downloaded_save is not None

    paths = GamePaths(game_id)
    save_path = paths.resolve(
        paths.accepted_save_relative(1, digest, ".CivBeyondSwordSave")
    )

    class _ReadSpy(CountingStorage):
        def __init__(self, inner: FakeStorage) -> None:
            super().__init__(inner)
            self.save_reads = 0

        def read_file(self, path: str) -> bytes:
            self._record("read_file")
            if path == save_path:
                self.save_reads += 1
            return self._inner.read_file(path)

    spy = _ReadSpy(storage)
    second = _reconcile(spy, store, config)
    assert second.operational_state is OperationalState.MY_TURN_DOWNLOADED
    assert spy.save_reads == 0


def test_reconcile_stale_evidence_redownloads(tmp_path: Path) -> None:
    storage = FakeStorage()
    game_id, digest = _commit_player_a_turn(storage)
    store = _local_store(tmp_path)
    config = _match_config(tmp_path, local_player_id="player_b")
    store.write_match_config(config)
    _reconcile(storage, store, config)

    state = store.load_match_state(game_id)
    assert state.downloaded_save is not None
    store.write_match_state(
        replace(
            state,
            downloaded_save=replace(state.downloaded_save, sha256=HASH_1),
        )
    )

    result = _reconcile(storage, store, config)
    assert result.records.downloaded_save is not None
    assert result.records.downloaded_save.sha256 == digest


def _handoff_evidence(
    *,
    game_id: str,
    digest: str,
    data: bytes,
    outcome: HandoffOutcome,
    local_player_id: str = "player_a",
    operation_id: str = OP_ID,
    source_protocol_sequence: int = 0,
) -> HandoffEvidence:
    return HandoffEvidence(
        outcome=outcome,
        game_id=game_id,
        operation_id=operation_id,
        local_player_id=local_player_id,
        sha256=digest,
        size_bytes=len(data),
        source_protocol_sequence=source_protocol_sequence,
        result_protocol_sequence=source_protocol_sequence + 1,
    )


def test_reconcile_committed_handoff_waiting_with_processed_hash(
    tmp_path: Path,
) -> None:
    storage = FakeStorage()
    game_id, digest = _commit_player_a_turn(storage)
    store = _local_store(tmp_path)
    config = _match_config(tmp_path, local_player_id="player_a")
    store.write_match_config(config)

    evidence = _handoff_evidence(
        game_id=game_id,
        digest=digest,
        data=SAVE_A,
        outcome=HandoffOutcome.COMMITTED,
    )
    result = _reconcile(storage, store, config, handoff_evidence=evidence)

    assert result.operational_state is OperationalState.WAITING_FOR_OTHER_PLAYER
    assert digest in result.records.processed_outgoing_hashes
    assert result.records.outgoing_candidate is None


def test_reconcile_idempotent_handoff_evidence(tmp_path: Path) -> None:
    storage = FakeStorage()
    game_id, digest = _commit_player_a_turn(storage)
    store = _local_store(tmp_path)
    config = _match_config(tmp_path, local_player_id="player_a")
    store.write_match_config(config)
    evidence = _handoff_evidence(
        game_id=game_id,
        digest=digest,
        data=SAVE_A,
        outcome=HandoffOutcome.IDEMPOTENT_ACK,
    )

    first = _reconcile(storage, store, config, handoff_evidence=evidence)
    second = _reconcile(storage, store, config, handoff_evidence=evidence)

    assert first.records.processed_outgoing_hashes == (digest,)
    assert second.records.processed_outgoing_hashes == (digest,)
    assert second.operational_state is OperationalState.WAITING_FOR_OTHER_PLAYER


def test_reconcile_transport_retry_then_success(tmp_path: Path) -> None:
    storage = FakeStorage()
    _commit_player_a_turn(storage)
    store = _local_store(tmp_path)
    config = _match_config(tmp_path, local_player_id="player_b")
    store.write_match_config(config)
    storage.faults.reset()
    storage.faults.inject(StorageOp.READ, moment=FaultMoment.BEFORE, occurrence=2)

    failed = _reconcile(storage, store, config)
    assert failed.operational_state is OperationalState.DOWNLOADING
    assert failed.retry_required is True

    storage.faults.reset()
    recovered = _reconcile(storage, store, config)
    assert recovered.operational_state is OperationalState.MY_TURN_DOWNLOADED
    assert recovered.retry_required is False


def test_reconcile_repeated_idempotent(tmp_path: Path) -> None:
    storage = FakeStorage()
    _commit_player_a_turn(storage)
    store = _local_store(tmp_path)
    config = _match_config(tmp_path, local_player_id="player_b")
    store.write_match_config(config)

    first = _reconcile(storage, store, config)
    second = _reconcile(storage, store, config)
    third = _reconcile(storage, store, config)

    assert first.operational_state is OperationalState.MY_TURN_DOWNLOADED
    assert second.operational_state is OperationalState.MY_TURN_DOWNLOADED
    assert third.operational_state is OperationalState.MY_TURN_DOWNLOADED
    assert first.records.downloaded_save == second.records.downloaded_save


# --- Promotion --------------------------------------------------------------


def test_promote_success(tmp_path: Path) -> None:
    storage = FakeStorage()
    game_id, digest = _commit_player_a_turn(storage)
    pbem = _pbem_dir(tmp_path)
    artifact = _artifact(game_id, digest)

    result = promote_verified_download(artifact, str(pbem))

    assert result.outcome is PromoteOutcome.PROMOTED
    assert result.record is not None
    dest = pbem / SAVE_NAME
    assert dest.read_bytes() == SAVE_A


def test_promote_exact_reuse(tmp_path: Path) -> None:
    storage = FakeStorage()
    game_id, digest = _commit_player_a_turn(storage)
    pbem = _pbem_dir(tmp_path)
    artifact = _artifact(game_id, digest)
    first = promote_verified_download(artifact, str(pbem))
    second = promote_verified_download(artifact, str(pbem))

    assert first.outcome is PromoteOutcome.PROMOTED
    assert second.outcome is PromoteOutcome.ALREADY_PRESENT
    assert second.record is not None
    assert second.record.sha256 == digest


def test_promote_conflict(tmp_path: Path) -> None:
    storage = FakeStorage()
    game_id, digest = _commit_player_a_turn(storage)
    pbem = _pbem_dir(tmp_path)
    dest = pbem / SAVE_NAME
    dest.write_bytes(b"other-bytes")

    result = promote_verified_download(_artifact(game_id, digest), str(pbem))

    assert result.outcome is PromoteOutcome.CONFLICT


@pytest.mark.parametrize(
    "bad_name",
    [
        "../evil.CivBeyondSwordSave",
        "folder/file.CivBeyondSwordSave",
        "folder\\file.CivBeyondSwordSave",
    ],
)
def test_promote_rejects_traversal_filename(tmp_path: Path, bad_name: str) -> None:
    storage = FakeStorage()
    game_id, digest = _commit_player_a_turn(storage)
    pbem = _pbem_dir(tmp_path)
    artifact = _artifact(game_id, digest)
    object.__setattr__(artifact, "original_filename", bad_name)

    result = promote_verified_download(artifact, str(pbem))

    assert result.outcome is PromoteOutcome.PATH_VIOLATION


# --- Baseline / detection ---------------------------------------------------


def test_baseline_captured_before_start_civ_intent(tmp_path: Path) -> None:
    storage = FakeStorage()
    _commit_player_a_turn(storage)
    store = _local_store(tmp_path)
    config = _match_config(tmp_path, local_player_id="player_b")
    store.write_match_config(config)
    _write_save(Path(config.pbem_save_directory), "Existing.CivBeyondSwordSave", SAVE_A)

    result = _reconcile(storage, store, config)

    kinds = {intent.kind for intent in result.intents}
    assert OrchestrationIntentKind.START_CIV in kinds
    assert result.records.play_session_baseline is not None
    assert len(result.records.play_session_baseline.entries) >= 1
    assert result.records.launch_attempt is not None


@pytest.mark.pt("PT-19")
def test_pt19_pre_existing_excluded_after_baseline(tmp_path: Path) -> None:
    pbem = _pbem_dir(tmp_path)
    stale_name = "StaleBeforeLaunch.CivBeyondSwordSave"
    _write_save(pbem, stale_name, SAVE_A)
    records = _records_with_baseline("example-match", pbem)
    clock = FakeClock()

    result = observe_outgoing_candidates(
        str(pbem),
        SaveMatchingRules(filename_glob=GLOB),
        records,
        (),
        clock=clock,
        max_save_bytes=10_000_000,
    )

    assert result.outcome is DetectionOutcome.NO_CANDIDATE


@pytest.mark.pt("PT-20")
def test_pt20_new_file_after_baseline_is_candidate(tmp_path: Path) -> None:
    pbem = _pbem_dir(tmp_path)
    records = _records_with_baseline("example-match", pbem)
    clock = FakeClock()

    _records, detection = _stable_candidate(pbem, records, clock)

    assert detection.outcome is DetectionOutcome.ONE_CANDIDATE
    assert detection.candidates[0].sha256 == sha256_hex(SAVE_B)


@pytest.mark.pt("PT-21")
def test_pt21_path_overwrite_new_hash_eligible(tmp_path: Path) -> None:
    pbem = _pbem_dir(tmp_path)
    name = "OverwriteMe.CivBeyondSwordSave"
    path = _write_save(pbem, name, SAVE_A)
    records = _records_with_baseline("example-match", pbem)
    path.write_bytes(SAVE_B)
    clock = FakeClock()

    first = observe_outgoing_candidates(
        str(pbem),
        SaveMatchingRules(filename_glob=GLOB),
        records,
        (),
        clock=clock,
        max_save_bytes=10_000_000,
    )
    assert first.outcome is DetectionOutcome.STABILIZING
    clock.advance(STABILITY_INTERVAL_SECONDS)
    second = observe_outgoing_candidates(
        str(pbem),
        SaveMatchingRules(filename_glob=GLOB),
        replace(records, stability_observations=first.observations),
        (),
        clock=clock,
        max_save_bytes=10_000_000,
    )

    assert second.outcome is DetectionOutcome.ONE_CANDIDATE
    assert second.candidates[0].sha256 == sha256_hex(SAVE_B)


@pytest.mark.pt("PT-22")
def test_pt22_baseline_survives_restart_while_civ_running(tmp_path: Path) -> None:
    storage = FakeStorage()
    game_id, digest = _commit_player_a_turn(storage)
    store = _local_store(tmp_path)
    config = _match_config(tmp_path, local_player_id="player_b")
    store.write_match_config(config)

    launched = _reconcile(storage, store, config)
    baseline = launched.records.play_session_baseline
    assert baseline is not None
    assert any(i.kind is OrchestrationIntentKind.START_CIV for i in launched.intents)

    obs = _process_obs()
    store.write_match_state(
        replace(
            launched.records,
            process_association=ProcessAssociationRecord(
                protocol_sequence=1,
                accepted_sha256=digest,
                pid=obs.pid,
                process_start_time_utc=obs.process_start_time_utc,
                process_create_time_ns=obs.process_create_time_ns,
                executable_path=obs.executable_path,
                associated_at=NOW_UTC,
            ),
        )
    )
    running = _reconcile(storage, store, config, process_observation=obs)
    assert running.operational_state is OperationalState.CIV_RUNNING
    assert running.records.play_session_baseline == baseline

    reloaded_store = LocalStore(tmp_path)
    restarted = _reconcile(
        storage,
        reloaded_store,
        config,
        process_observation=obs,
    )

    assert restarted.operational_state is OperationalState.CIV_RUNNING
    assert restarted.records.play_session_baseline == baseline


@pytest.mark.pt("PT-23")
def test_pt23_missing_baseline_disables_auto_send(tmp_path: Path) -> None:
    pbem = _pbem_dir(tmp_path)
    _write_save(pbem, "Orphan.CivBeyondSwordSave", SAVE_B)
    records = MatchLocalRecords(game_id="example-match")
    clock = FakeClock()

    detection = observe_outgoing_candidates(
        str(pbem),
        SaveMatchingRules(filename_glob=GLOB),
        records,
        (),
        clock=clock,
        max_save_bytes=10_000_000,
    )
    intents = decide_intents(
        TurnHandlingMode.FULLY_MANAGED,
        False,
        OperationalState.MY_TURN_DOWNLOADED,
        records,
        detection,
        None,
        None,
        False,
    )

    assert detection.outcome is DetectionOutcome.MISSING_BASELINE
    assert OrchestrationIntentKind.REQUIRE_USER_ACTION in {i.kind for i in intents}
    assert OrchestrationIntentKind.PREPARE_OR_SEND_HANDOFF not in {
        i.kind for i in intents
    }


@pytest.mark.pt("PT-24")
def test_pt24_multiple_candidates_require_selection(tmp_path: Path) -> None:
    pbem = _pbem_dir(tmp_path)
    records = _records_with_baseline("example-match", pbem)
    clock = FakeClock()
    _write_save(pbem, "One.CivBeyondSwordSave", SAVE_A)
    _write_save(pbem, "Two.CivBeyondSwordSave", SAVE_B)

    observed = observe_outgoing_candidates(
        str(pbem),
        SaveMatchingRules(filename_glob=GLOB),
        records,
        (),
        clock=clock,
        max_save_bytes=10_000_000,
    )
    assert observed.outcome is DetectionOutcome.STABILIZING
    clock.advance(STABILITY_INTERVAL_SECONDS)
    observed = observe_outgoing_candidates(
        str(pbem),
        SaveMatchingRules(filename_glob=GLOB),
        replace(records, stability_observations=observed.observations),
        (),
        clock=clock,
        max_save_bytes=10_000_000,
    )
    records = replace(records, stability_observations=observed.observations)

    assert observed.outcome is DetectionOutcome.MULTIPLE_CANDIDATES
    intents = decide_intents(
        TurnHandlingMode.FULLY_MANAGED,
        False,
        OperationalState.OUTGOING_SAVE_DETECTED,
        records,
        observed,
        None,
        None,
        False,
    )
    assert OrchestrationIntentKind.REQUIRE_CANDIDATE_SELECTION in {
        i.kind for i in intents
    }


@pytest.mark.pt("PT-34")
def test_pt34_stabilizing_then_stable_with_clock_advance(tmp_path: Path) -> None:
    pbem = _pbem_dir(tmp_path)
    records = _records_with_baseline("example-match", pbem)
    clock = FakeClock()
    _write_save(pbem, "Growing.CivBeyondSwordSave", SAVE_B)

    first = observe_outgoing_candidates(
        str(pbem),
        SaveMatchingRules(filename_glob=GLOB),
        records,
        (),
        clock=clock,
        max_save_bytes=10_000_000,
    )
    assert first.outcome is DetectionOutcome.STABILIZING

    second = observe_outgoing_candidates(
        str(pbem),
        SaveMatchingRules(filename_glob=GLOB),
        replace(records, stability_observations=first.observations),
        (),
        clock=clock,
        max_save_bytes=10_000_000,
    )
    assert second.outcome is DetectionOutcome.STABILIZING

    clock.advance(STABILITY_INTERVAL_SECONDS)
    third = observe_outgoing_candidates(
        str(pbem),
        SaveMatchingRules(filename_glob=GLOB),
        replace(records, stability_observations=second.observations),
        (),
        clock=clock,
        max_save_bytes=10_000_000,
    )
    assert third.outcome is DetectionOutcome.ONE_CANDIDATE


def test_detection_recursive_subfolder(tmp_path: Path) -> None:
    pbem = _pbem_dir(tmp_path)
    records = _records_with_baseline("example-match", pbem)
    clock = FakeClock()
    _write_save(pbem, "nested/deep/Turn.CivBeyondSwordSave", SAVE_B)

    _records, detection = _stable_candidate(
        pbem, records, clock, name="nested/deep/Turn.CivBeyondSwordSave"
    )

    assert detection.outcome is DetectionOutcome.ONE_CANDIDATE


@pytest.mark.pt("PT-06")
def test_pt06_duplicate_observe_no_duplicate_candidate(tmp_path: Path) -> None:
    pbem = _pbem_dir(tmp_path)
    records = _records_with_baseline("example-match", pbem)
    clock = FakeClock()
    _write_save(pbem, "Dup.CivBeyondSwordSave", SAVE_B)

    first = observe_outgoing_candidates(
        str(pbem),
        SaveMatchingRules(filename_glob=GLOB),
        records,
        (),
        clock=clock,
        max_save_bytes=10_000_000,
    )
    clock.advance(STABILITY_INTERVAL_SECONDS)
    second = observe_outgoing_candidates(
        str(pbem),
        SaveMatchingRules(filename_glob=GLOB),
        replace(records, stability_observations=first.observations),
        (),
        clock=clock,
        max_save_bytes=10_000_000,
    )
    third = observe_outgoing_candidates(
        str(pbem),
        SaveMatchingRules(filename_glob=GLOB),
        replace(records, stability_observations=second.observations),
        (),
        clock=clock,
        max_save_bytes=10_000_000,
    )

    assert second.outcome is DetectionOutcome.ONE_CANDIDATE
    assert third.outcome is DetectionOutcome.ONE_CANDIDATE
    assert len(second.candidates) == 1
    assert len(third.candidates) == 1
    assert second.candidates[0] == third.candidates[0]


# --- Watcher / polling ------------------------------------------------------


def test_polling_watcher_discovers_new_file(tmp_path: Path) -> None:
    pbem = _pbem_dir(tmp_path)
    clock = FakeClock()
    watcher = PollingWatcher(clock=clock, poll_interval_seconds=0.5)
    events: list[object] = []
    watcher.start(str(pbem), lambda event: events.append(event))

    _write_save(pbem, "Watched.CivBeyondSwordSave", SAVE_A)
    clock.advance(0.5)
    watcher.poll()

    assert len(events) == 1
    assert events[0].kind == "created"  # type: ignore[attr-defined]


def test_match_monitor_falls_back_to_polling(tmp_path: Path) -> None:
    pbem = _pbem_dir(tmp_path)
    clock = FakeClock()
    events: list[object] = []
    monitor = MatchMonitor(
        clock=clock,
        poll_interval_seconds=0.5,
        primary=UnavailableWatcher(),
    )
    monitor.start(str(pbem), lambda event: events.append(event))
    assert isinstance(monitor.active_watcher, PollingWatcher)

    _write_save(pbem, "Fallback.CivBeyondSwordSave", SAVE_A)
    clock.advance(0.5)
    monitor.poll()

    assert len(events) == 1


def test_two_local_store_matches_isolated(tmp_path: Path) -> None:
    storage = FakeStorage()
    initialize_ready_match(storage=storage, game_id="match-alpha")
    initialize_ready_match(storage=storage, game_id="match-bravo")
    store = _local_store(tmp_path)

    config_a = _match_config(
        tmp_path, game_id="match-alpha", local_player_id="player_a"
    )
    config_b = _match_config(
        tmp_path, game_id="match-bravo", local_player_id="player_a"
    )
    store.write_match_config(config_a)
    store.write_match_config(config_b)

    result_a = _reconcile(storage, store, config_a)
    store.write_match_state(
        replace(result_a.records, retry_count=99, last_transition_reason="alpha-only")
    )

    result_b = _reconcile(storage, store, config_b)

    assert result_a.operational_state is OperationalState.WAITING_FOR_MY_FIRST_SAVE
    assert result_b.operational_state is OperationalState.WAITING_FOR_MY_FIRST_SAVE
    reloaded_b = store.load_match_state("match-bravo")
    assert reloaded_b.retry_count == 0
    assert reloaded_b.last_transition_reason != "alpha-only"


# --- Orchestration ----------------------------------------------------------


def test_fully_managed_emits_one_start_civ(tmp_path: Path) -> None:
    storage = FakeStorage()
    _commit_player_a_turn(storage)
    store = _local_store(tmp_path)
    config = _match_config(tmp_path, local_player_id="player_b")
    store.write_match_config(config)

    result = _reconcile(storage, store, config)
    start_intents = [
        i for i in result.intents if i.kind is OrchestrationIntentKind.START_CIV
    ]

    assert len(start_intents) == 1


def test_second_reconcile_no_second_start_civ(tmp_path: Path) -> None:
    storage = FakeStorage()
    _commit_player_a_turn(storage)
    store = _local_store(tmp_path)
    config = _match_config(tmp_path, local_player_id="player_b")
    store.write_match_config(config)

    first = _reconcile(storage, store, config)
    assert any(i.kind is OrchestrationIntentKind.START_CIV for i in first.intents)

    second = _reconcile(storage, store, config)
    assert not any(i.kind is OrchestrationIntentKind.START_CIV for i in second.intents)


def test_process_association_suppresses_relaunch(tmp_path: Path) -> None:
    storage = FakeStorage()
    game_id, digest = _commit_player_a_turn(storage)
    store = _local_store(tmp_path)
    standard = _match_config(
        tmp_path,
        local_player_id="player_b",
        turn_handling_mode=TurnHandlingMode.STANDARD,
    )
    store.write_match_config(standard)
    downloaded = _reconcile(storage, store, standard)
    assert downloaded.records.launch_attempt is None

    associated = replace(
        downloaded.records,
        process_association=ProcessAssociationRecord(
            protocol_sequence=1,
            accepted_sha256=digest,
            pid=4242,
            process_start_time_utc=NOW_UTC,
            process_create_time_ns=CREATE_NS,
            executable_path=r"C:\Games\Civ4\BeyondSword.exe",
            associated_at=NOW_UTC,
        ),
    )
    store.write_match_state(associated)

    managed = _match_config(tmp_path, local_player_id="player_b")
    store.write_match_config(managed)
    # Association without a matching live observation suppresses relaunch
    # and must not invent RESUME/FOCUS for an unverified process.
    second = _reconcile(storage, store, managed)
    kinds = {intent.kind for intent in second.intents}
    assert OrchestrationIntentKind.START_CIV not in kinds
    assert OrchestrationIntentKind.RESUME_OR_FOCUS_CIV not in kinds

    resumed = _reconcile(
        storage, store, managed, process_observation=_process_obs(running=True)
    )
    assert OrchestrationIntentKind.RESUME_OR_FOCUS_CIV in {
        intent.kind for intent in resumed.intents
    }


def test_prepare_or_send_on_one_candidate(tmp_path: Path) -> None:
    pbem = _pbem_dir(tmp_path)
    records = _records_with_baseline("example-match", pbem)
    clock = FakeClock()
    _records, detection = _stable_candidate(pbem, records, clock)

    intents = decide_intents(
        TurnHandlingMode.FULLY_MANAGED,
        False,
        OperationalState.OUTGOING_SAVE_DETECTED,
        _records,
        detection,
        None,
        None,
        False,
    )

    assert OrchestrationIntentKind.PREPARE_OR_SEND_HANDOFF in {i.kind for i in intents}


def test_no_close_before_commit(tmp_path: Path) -> None:
    storage = FakeStorage()
    _commit_player_a_turn(storage)
    store = _local_store(tmp_path)
    config = _match_config(tmp_path, local_player_id="player_b")
    store.write_match_config(config)

    result = _reconcile(storage, store, config, process_observation=_process_obs())

    assert OrchestrationIntentKind.REQUEST_GRACEFUL_CLOSE not in {
        i.kind for i in result.intents
    }


def test_close_after_committed_or_idempotent(tmp_path: Path) -> None:
    storage = FakeStorage()
    game_id, digest = _commit_player_a_turn(storage)
    records = MatchLocalRecords(
        game_id=game_id,
        process_association=ProcessAssociationRecord(
            protocol_sequence=0,
            accepted_sha256=None,
            pid=4242,
            process_start_time_utc=NOW_UTC,
            process_create_time_ns=CREATE_NS,
            executable_path=r"C:\Games\Civ4\BeyondSword.exe",
            associated_at=NOW_UTC,
        ),
    )

    for outcome in (HandoffOutcome.COMMITTED, HandoffOutcome.IDEMPOTENT_ACK):
        evidence = _handoff_evidence(
            game_id=game_id,
            digest=digest,
            data=SAVE_A,
            outcome=outcome,
            local_player_id="player_a",
            source_protocol_sequence=0,
        )
        intents = decide_intents(
            TurnHandlingMode.FULLY_MANAGED,
            False,
            OperationalState.WAITING_FOR_OTHER_PLAYER,
            records,
            None,
            _process_obs(running=True),
            evidence,
            False,
        )
        assert OrchestrationIntentKind.REQUEST_GRACEFUL_CLOSE in {
            i.kind for i in intents
        }

    mismatched = decide_intents(
        TurnHandlingMode.FULLY_MANAGED,
        False,
        OperationalState.WAITING_FOR_OTHER_PLAYER,
        records,
        None,
        ProcessObservation(
            pid=9999,
            process_start_time_utc=NOW_UTC,
            process_create_time_ns=CREATE_NS,
            executable_path=r"C:\Games\Civ4\BeyondSword.exe",
            running=True,
        ),
        _handoff_evidence(
            game_id=game_id,
            digest=digest,
            data=SAVE_A,
            outcome=HandoffOutcome.COMMITTED,
        ),
        False,
    )
    assert OrchestrationIntentKind.REQUEST_GRACEFUL_CLOSE not in {
        i.kind for i in mismatched
    }


def test_standard_never_auto_start_civ(tmp_path: Path) -> None:
    storage = FakeStorage()
    _commit_player_a_turn(storage)
    store = _local_store(tmp_path)
    config = _match_config(
        tmp_path,
        local_player_id="player_b",
        turn_handling_mode=TurnHandlingMode.STANDARD,
    )
    store.write_match_config(config)

    result = _reconcile(storage, store, config)

    assert OrchestrationIntentKind.START_CIV not in {i.kind for i in result.intents}


def test_allow_force_close_never_emits_terminate(tmp_path: Path) -> None:
    storage = FakeStorage()
    game_id, digest = _commit_player_a_turn(storage)
    evidence = _handoff_evidence(
        game_id=game_id,
        digest=digest,
        data=SAVE_A,
        outcome=HandoffOutcome.COMMITTED,
    )
    records = MatchLocalRecords(
        game_id=game_id,
        process_association=ProcessAssociationRecord(
            protocol_sequence=0,
            accepted_sha256=None,
            pid=4242,
            process_start_time_utc=NOW_UTC,
            process_create_time_ns=CREATE_NS,
            executable_path=r"C:\Games\Civ4\BeyondSword.exe",
            associated_at=NOW_UTC,
        ),
    )
    intents = decide_intents(
        TurnHandlingMode.FULLY_MANAGED,
        True,
        OperationalState.WAITING_FOR_OTHER_PLAYER,
        records,
        None,
        _process_obs(running=True),
        evidence,
        False,
    )

    kinds = {intent.kind for intent in intents}
    assert OrchestrationIntentKind.REQUEST_GRACEFUL_CLOSE in kinds
    assert not any("terminate" in kind.value for kind in kinds)


def test_civ_exit_without_save_requires_user_action_no_relaunch(tmp_path: Path) -> None:
    storage = FakeStorage()
    _commit_player_a_turn(storage)
    store = _local_store(tmp_path)
    config = _match_config(tmp_path, local_player_id="player_b")
    store.write_match_config(config)
    launched = _reconcile(storage, store, config)
    assert launched.records.launch_attempt is not None

    exited = _reconcile(
        storage,
        store,
        config,
        process_observation=_process_obs(running=False),
    )
    kinds = {intent.kind for intent in exited.intents}
    assert OrchestrationIntentKind.REQUIRE_USER_ACTION in kinds
    assert OrchestrationIntentKind.START_CIV not in kinds
    user_action = next(
        i
        for i in exited.intents
        if i.kind is OrchestrationIntentKind.REQUIRE_USER_ACTION
    )
    assert user_action.payload == {"reason": "civ_exited_without_outgoing"}


# --- Diagnostics / installation ---------------------------------------------


def test_emit_diagnostic_redacts_password_like_fields() -> None:
    event = emit_diagnostic(
        "transport_failure",
        fields={
            "host": "sftp.example.invalid",
            "password": "placeholder-secret",
            "sftp_password": "another-secret",
            "private_key": "key-material",
            "sha256": "a" * 64,
        },
        message="connect failed password=placeholder-secret",
        secret_values=("placeholder-secret",),
    )

    assert event.fields["password"] == REDACTED
    assert event.fields["sftp_password"] == REDACTED
    assert event.fields["private_key"] == REDACTED
    assert event.fields["sha256"] == f"{'a' * 12}…"
    assert "placeholder-secret" not in event.message
    assert REDACTED in event.message


def test_installation_uuid_accepted() -> None:
    identity = InstallationIdentity(client_id=CLIENT_UUID)
    assert identity.client_id == CLIENT_UUID


def test_installation_legacy_client_alpha_rejected() -> None:
    with pytest.raises(DomainValidationError):
        InstallationIdentity(client_id="client-alpha")
