"""Complete two-client FakeStorage turn exchange (P5)."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from civ4_turn_relay.domain import (
    OperationalState,
    TurnHandlingMode,
    sha256_hex,
)
from civ4_turn_relay.local import (
    DetectionOutcome,
    FakeClock,
    OrchestrationIntentKind,
)
from civ4_turn_relay.protocol import (
    HandoffOutcome,
    InitializeOutcome,
    read_authoritative_manifest,
)
from civ4_turn_relay.protocol.manifest_access import ManifestReadOutcome
from civ4_turn_relay.storage import FakeStorage
from tests.e2e_fake.helpers import (
    GAME_ID,
    GAME_ID_B,
    SAVE_A,
    SAVE_B,
    SAVE_NAME_A,
    SAVE_NAME_B,
    make_client,
    match_config,
    running_process,
    stopped_process,
    write_stable_save,
)


@pytest.mark.pt("PT-01")
@pytest.mark.pt("PT-13")
@pytest.mark.pt("PT-36")
@pytest.mark.pt("PT-37")
@pytest.mark.pt("PT-41")
@pytest.mark.pt("PT-42")
def test_complete_two_client_standard_cycle(tmp_path: Path) -> None:
    storage = FakeStorage()
    clock = FakeClock()
    root_a = tmp_path / "client-a"
    root_b = tmp_path / "client-b"
    client_a = make_client(
        root_a,
        storage,
        clock,
        client_uuid=uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
    )
    client_b = make_client(
        root_b,
        storage,
        clock,
        client_uuid=uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
    )
    config_a = match_config(
        root_a, local_player_id="player_a", mode=TurnHandlingMode.STANDARD
    )
    config_b = match_config(
        root_b,
        local_player_id="player_b",
        mode=TurnHandlingMode.STANDARD,
        pbem_name="pbem-b",
    )

    init = client_a.initialize_or_join(
        config_a, operation_id="11111111-1111-4111-8111-111111111111"
    )
    assert init.outcome is InitializeOutcome.CREATED
    join = client_b.initialize_or_join(
        config_b, operation_id="22222222-2222-4222-8222-222222222222"
    )
    assert join.outcome is InitializeOutcome.JOINED_EXISTING

    snap_a = client_a.reconcile(GAME_ID)
    assert snap_a.operational_state is OperationalState.WAITING_FOR_MY_FIRST_SAVE
    assert snap_a.records.verified_remote is not None
    assert snap_a.records.verified_remote.protocol_sequence == 0
    assert snap_a.records.verified_remote.accepted_sha256 is None
    assert not any(
        intent.kind is OrchestrationIntentKind.START_CIV for intent in snap_a.intents
    )

    started = client_a.request_start(GAME_ID)
    assert any(
        intent.kind is OrchestrationIntentKind.START_CIV for intent in started.intents
    )
    assert started.records.play_session_baseline is not None
    client_a.set_process_observation(GAME_ID, running_process(1001))

    write_stable_save(
        Path(config_a.pbem_save_directory),
        SAVE_NAME_A,
        SAVE_A,
        clock,
        client_a,
        GAME_ID,
    )
    detected = client_a.reconcile(GAME_ID)
    assert detected.operational_state is OperationalState.OUTGOING_SAVE_DETECTED
    assert detected.records.outgoing_candidate is not None
    assert detected.records.outgoing_candidate.sha256 == sha256_hex(SAVE_A)
    handoff = client_a.execute_handoff(
        GAME_ID, operation_id="33333333-3333-4333-8333-333333333333"
    )
    assert handoff.outcome is HandoffOutcome.COMMITTED
    assert handoff.manifest_changed is True
    assert handoff.manifest is not None
    assert handoff.manifest.protocol.last_operation_id == (
        "33333333-3333-4333-8333-333333333333"
    )
    assert handoff.manifest.accepted_save is not None
    assert handoff.manifest.accepted_save.sha256 == sha256_hex(SAVE_A)
    state_a = client_a.store.load_match_state_or_empty(GAME_ID)
    assert sha256_hex(SAVE_A) in state_a.processed_outgoing_hashes
    assert state_a.in_progress_handoff is None

    remote = read_authoritative_manifest(storage, GAME_ID)
    assert remote.outcome is ManifestReadOutcome.OK
    assert remote.manifest is not None
    assert remote.manifest.protocol_sequence == 1
    assert remote.manifest.current_player_id == "player_b"
    assert remote.manifest.accepted_save is not None
    assert remote.manifest.accepted_save.sha256 == sha256_hex(SAVE_A)
    assert remote.manifest.accepted_save_hashes == (sha256_hex(SAVE_A),)

    # Former owner must not advance while waiting.
    denied = client_a.execute_handoff(
        GAME_ID,
        operation_id="39393939-3333-4333-8333-393939393939",
        outgoing_bytes=SAVE_B,
        original_filename=SAVE_NAME_B,
    )
    assert denied.outcome is HandoffOutcome.NOT_CURRENT_OWNER
    still = read_authoritative_manifest(storage, GAME_ID)
    assert still.manifest is not None
    assert still.manifest.protocol_sequence == 1

    snap_b = client_b.reconcile(GAME_ID)
    assert snap_b.operational_state in {
        OperationalState.MY_TURN_DOWNLOADED,
        OperationalState.OUTGOING_SAVE_DETECTED,
    }
    assert snap_b.records.downloaded_save is not None
    assert snap_b.records.downloaded_save.sha256 == sha256_hex(SAVE_A)
    assert Path(snap_b.records.downloaded_save.local_path).read_bytes() == SAVE_A

    started_b = client_b.request_start(GAME_ID)
    assert any(
        intent.kind is OrchestrationIntentKind.START_CIV for intent in started_b.intents
    )
    client_b.set_process_observation(GAME_ID, running_process(2002))
    write_stable_save(
        Path(config_b.pbem_save_directory),
        SAVE_NAME_B,
        SAVE_B,
        clock,
        client_b,
        GAME_ID,
    )
    handoff_b = client_b.execute_handoff(
        GAME_ID, operation_id="44444444-4444-4444-8444-444444444444"
    )
    assert handoff_b.outcome is HandoffOutcome.COMMITTED

    remote2 = read_authoritative_manifest(storage, GAME_ID)
    assert remote2.manifest is not None
    assert remote2.manifest.protocol_sequence == 2
    assert remote2.manifest.current_player_id == "player_a"
    assert remote2.manifest.accepted_save_hashes == (
        sha256_hex(SAVE_A),
        sha256_hex(SAVE_B),
    )

    snap_a2 = client_a.reconcile(GAME_ID)
    assert snap_a2.records.downloaded_save is not None
    assert snap_a2.records.downloaded_save.sha256 == sha256_hex(SAVE_B)

    # Second match on same storage remains isolated.
    config_a2 = match_config(
        root_a,
        game_id=GAME_ID_B,
        local_player_id="player_a",
        pbem_name="pbem-match-2",
    )
    init2 = client_a.initialize_or_join(
        config_a2, operation_id="55555555-5555-4555-8555-555555555555"
    )
    assert init2.outcome is InitializeOutcome.CREATED
    other = read_authoritative_manifest(storage, GAME_ID)
    assert other.manifest is not None
    assert other.manifest.protocol_sequence == 2

    client_a.close()
    client_b.close()


@pytest.mark.pt("PT-31")
def test_fully_managed_auto_launch_and_close(tmp_path: Path) -> None:
    storage = FakeStorage()
    clock = FakeClock()
    root = tmp_path / "managed"
    client = make_client(
        root,
        storage,
        clock,
        client_uuid=uuid.UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc"),
    )
    config = match_config(
        root,
        mode=TurnHandlingMode.FULLY_MANAGED,
    )
    client.initialize_or_join(
        config, operation_id="66666666-6666-4666-8666-666666666666"
    )
    first = client.reconcile(GAME_ID)
    start_intents = [
        intent
        for intent in first.intents
        if intent.kind is OrchestrationIntentKind.START_CIV
    ]
    assert len(start_intents) == 1
    assert first.records.play_session_baseline is not None
    second = client.reconcile(GAME_ID)
    assert not any(
        intent.kind is OrchestrationIntentKind.START_CIV for intent in second.intents
    )

    client.set_process_observation(GAME_ID, running_process(3003))
    write_stable_save(
        Path(config.pbem_save_directory),
        SAVE_NAME_A,
        SAVE_A,
        clock,
        client,
        GAME_ID,
    )
    snap = client.tick(
        GAME_ID, auto_handoff_operation_id="77777777-7777-4777-8777-777777777777"
    )
    assert snap.protocol_sequence == 1
    assert snap.current_player_id == "player_b"
    # Production path: tick → real execute_handoff → attributed evidence → close.
    close_intents = [
        intent
        for intent in snap.intents
        if intent.kind is OrchestrationIntentKind.CLOSE_CIV_AFTER_COMMIT
    ]
    assert len(close_intents) == 1
    close_payload = close_intents[0].payload
    assert close_payload is not None
    assert close_payload["pid"] == 3003
    records = client.store.load_match_state_or_empty(GAME_ID)
    assert sha256_hex(SAVE_A) in records.processed_outgoing_hashes
    assert records.in_progress_handoff is None
    assert records.pending_post_commit_close is not None
    assert records.pending_post_commit_close.close_requested is True
    # Close is not re-emitted after acknowledgment; mismatched PID never closes.
    again = client.reconcile(GAME_ID)
    assert not any(
        intent.kind is OrchestrationIntentKind.CLOSE_CIV_AFTER_COMMIT
        for intent in again.intents
    )
    client.set_process_observation(GAME_ID, running_process(9999))
    stale = client.reconcile(GAME_ID)
    assert not any(
        intent.kind is OrchestrationIntentKind.CLOSE_CIV_AFTER_COMMIT
        for intent in stale.intents
    )


@pytest.mark.pt("PT-24")
def test_multiple_candidates_require_selection(tmp_path: Path) -> None:
    storage = FakeStorage()
    clock = FakeClock()
    root = tmp_path / "multi"
    client = make_client(root, storage, clock)
    config = match_config(root, mode=TurnHandlingMode.STANDARD)
    client.initialize_or_join(
        config, operation_id="88888888-8888-4888-8888-888888888888"
    )
    client.request_start(GAME_ID)
    pbem = Path(config.pbem_save_directory)
    (pbem / "One.CivBeyondSwordSave").write_bytes(b"cand-one")
    (pbem / "Two.CivBeyondSwordSave").write_bytes(b"cand-two")
    client.observe_candidates(GAME_ID)
    clock.advance(1.0)
    client.observe_candidates(GAME_ID)
    clock.advance(1.0)
    detection = client.observe_candidates(GAME_ID)
    assert detection.outcome is DetectionOutcome.MULTIPLE_CANDIDATES
    result = client.reconcile(GAME_ID)
    assert any(
        intent.kind is OrchestrationIntentKind.REQUIRE_CANDIDATE_SELECTION
        for intent in result.intents
    )
    chosen = client.select_candidate(
        GAME_ID, str((pbem / "Two.CivBeyondSwordSave").resolve())
    )
    assert chosen.sha256 == sha256_hex(b"cand-two")


@pytest.mark.pt("PT-09")
def test_non_owner_cannot_upload(tmp_path: Path) -> None:
    storage = FakeStorage()
    clock = FakeClock()
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    client_a = make_client(
        root_a,
        storage,
        clock,
        client_uuid=uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
    )
    client_b = make_client(
        root_b,
        storage,
        clock,
        client_uuid=uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
    )
    config_a = match_config(root_a, local_player_id="player_a")
    config_b = match_config(root_b, local_player_id="player_b", pbem_name="pbem-b")
    client_a.initialize_or_join(
        config_a, operation_id="99999999-9999-4999-8999-999999999999"
    )
    client_b.initialize_or_join(
        config_b, operation_id="aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
    )
    client_a.request_start(GAME_ID)
    write_stable_save(
        Path(config_a.pbem_save_directory),
        SAVE_NAME_A,
        SAVE_A,
        clock,
        client_a,
        GAME_ID,
    )
    assert (
        client_a.execute_handoff(
            GAME_ID, operation_id="bbbbbbbb-cccc-4ddd-8eee-ffffffffffff"
        ).outcome
        is HandoffOutcome.COMMITTED
    )
    client_b.reconcile(GAME_ID)
    # Player B owns the turn; player A must not advance with a new save.
    write_stable_save(
        Path(config_a.pbem_save_directory),
        "Sneaky.CivBeyondSwordSave",
        b"sneaky-non-owner",
        clock,
        client_a,
        GAME_ID,
    )
    denied = client_a.execute_handoff(
        GAME_ID, operation_id="cccccccc-dddd-4eee-8fff-000000000000"
    )
    assert denied.outcome is HandoffOutcome.NOT_CURRENT_OWNER
    remote = read_authoritative_manifest(storage, GAME_ID)
    assert remote.manifest is not None
    assert remote.manifest.protocol_sequence == 1


@pytest.mark.pt("PT-06")
def test_duplicate_ticks_do_not_double_commit(tmp_path: Path) -> None:
    storage = FakeStorage()
    clock = FakeClock()
    root = tmp_path / "dup"
    client = make_client(root, storage, clock)
    config = match_config(root, mode=TurnHandlingMode.FULLY_MANAGED)
    client.initialize_or_join(
        config, operation_id="dddddddd-dddd-4ddd-8ddd-dddddddddddd"
    )
    client.reconcile(GAME_ID)
    client.set_process_observation(GAME_ID, running_process())
    write_stable_save(
        Path(config.pbem_save_directory),
        SAVE_NAME_A,
        SAVE_A,
        clock,
        client,
        GAME_ID,
    )
    op = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
    client.tick(GAME_ID, auto_handoff_operation_id=op)
    client.tick(GAME_ID, auto_handoff_operation_id=op)
    client.tick(GAME_ID, auto_handoff_operation_id=op)
    remote = read_authoritative_manifest(storage, GAME_ID)
    assert remote.manifest is not None
    assert remote.manifest.protocol_sequence == 1
    assert remote.manifest.accepted_save_hashes == (sha256_hex(SAVE_A),)


@pytest.mark.pt("PT-19")
@pytest.mark.pt("PT-20")
@pytest.mark.pt("PT-22")
def test_restart_preserves_baseline_and_ownership(tmp_path: Path) -> None:
    storage = FakeStorage()
    clock = FakeClock()
    root = tmp_path / "restart"
    client = make_client(
        root,
        storage,
        clock,
        client_uuid=uuid.UUID("ffffffff-ffff-4fff-8fff-ffffffffffff"),
    )
    config = match_config(root, mode=TurnHandlingMode.FULLY_MANAGED)
    client.initialize_or_join(
        config, operation_id="12121212-1212-4121-8121-121212121212"
    )
    client.reconcile(GAME_ID)
    assert client.store.load_match_state_or_empty(GAME_ID).play_session_baseline
    client.set_process_observation(GAME_ID, running_process(9))
    client.close()

    restarted = make_client(
        root,
        storage,
        clock,
        client_uuid=uuid.UUID("ffffffff-ffff-4fff-8fff-ffffffffffff"),
    )
    restarted.open_match(config)
    restarted.set_process_observation(GAME_ID, running_process(9))
    result = restarted.reconcile(GAME_ID)
    assert result.records.play_session_baseline is not None
    assert result.records.process_association is not None
    assert result.operational_state is OperationalState.CIV_RUNNING


def test_standard_never_emits_close_or_auto_launch(tmp_path: Path) -> None:
    storage = FakeStorage()
    clock = FakeClock()
    client = make_client(tmp_path / "std", storage, clock)
    config = match_config(tmp_path / "std", mode=TurnHandlingMode.STANDARD)
    client.initialize_or_join(
        config, operation_id="13131313-1313-4131-8131-131313131313"
    )
    result = client.reconcile(GAME_ID)
    assert not any(
        intent.kind is OrchestrationIntentKind.START_CIV for intent in result.intents
    )
    client.request_start(GAME_ID)
    write_stable_save(
        Path(config.pbem_save_directory),
        SAVE_NAME_A,
        SAVE_A,
        clock,
        client,
        GAME_ID,
    )
    handoff = client.execute_handoff(
        GAME_ID, operation_id="14141414-1414-4141-8141-141414141414"
    )
    assert handoff.outcome is HandoffOutcome.COMMITTED
    client.set_process_observation(GAME_ID, running_process())
    after = client.reconcile(GAME_ID)
    assert not any(
        intent.kind is OrchestrationIntentKind.CLOSE_CIV_AFTER_COMMIT
        for intent in after.intents
    )


def test_civ_exit_without_save_no_remote_mutation(tmp_path: Path) -> None:
    storage = FakeStorage()
    clock = FakeClock()
    client = make_client(tmp_path / "exit", storage, clock)
    config = match_config(tmp_path / "exit", mode=TurnHandlingMode.FULLY_MANAGED)
    client.initialize_or_join(
        config, operation_id="15151515-1515-4151-8151-151515151515"
    )
    client.reconcile(GAME_ID)
    client.set_process_observation(GAME_ID, running_process(77))
    client.set_process_observation(GAME_ID, stopped_process(77))
    result = client.reconcile(GAME_ID)
    assert any(
        intent.kind is OrchestrationIntentKind.REQUIRE_USER_ACTION
        for intent in result.intents
    )
    remote = read_authoritative_manifest(storage, GAME_ID)
    assert remote.manifest is not None
    assert remote.manifest.protocol_sequence == 0
    # No relaunch loop.
    again = client.reconcile(GAME_ID)
    assert not any(
        intent.kind is OrchestrationIntentKind.START_CIV for intent in again.intents
    )


def test_ai_players_not_in_human_order(tmp_path: Path) -> None:
    storage = FakeStorage()
    clock = FakeClock()
    client = make_client(tmp_path / "ai", storage, clock)
    config = match_config(tmp_path / "ai")
    assert [player.id for player in config.players] == ["player_a", "player_b"]
    client.initialize_or_join(
        config, operation_id="16161616-1616-4161-8161-161616161616"
    )
    client.request_start(GAME_ID)
    write_stable_save(
        Path(config.pbem_save_directory),
        SAVE_NAME_A,
        SAVE_A,
        clock,
        client,
        GAME_ID,
    )
    client.execute_handoff(GAME_ID, operation_id="17171717-1717-4171-8171-171717171717")
    remote = read_authoritative_manifest(storage, GAME_ID)
    assert remote.manifest is not None
    assert remote.manifest.current_player_id == "player_b"
    assert "ai" not in remote.manifest.current_player_id


def test_snapshot_is_explicit(tmp_path: Path) -> None:
    storage = FakeStorage()
    clock = FakeClock()
    client = make_client(tmp_path / "snap", storage, clock)
    config = match_config(tmp_path / "snap")
    client.initialize_or_join(
        config, operation_id="18181818-1818-4181-8181-181818181818"
    )
    snap = client.snapshot(GAME_ID)
    assert snap.game_id == GAME_ID
    assert snap.local_player_id == "player_a"
    assert snap.current_player_id == "player_a"
    assert snap.protocol_sequence == 0
    assert snap.operational_state is OperationalState.WAITING_FOR_MY_FIRST_SAVE
    assert snap.primary_status
    assert snap.storage_available is True
