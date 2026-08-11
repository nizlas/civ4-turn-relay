"""Fault injection and restart scenarios for the headless Relay client."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from civ4_turn_relay.app import RelayClient
from civ4_turn_relay.domain import MatchConfig, OperationalState, sha256_hex
from civ4_turn_relay.local import FakeClock, OrchestrationIntentKind
from civ4_turn_relay.protocol import HandoffOutcome, read_authoritative_manifest
from civ4_turn_relay.protocol.manifest_access import ManifestReadOutcome
from civ4_turn_relay.storage import FakeStorage, FaultMoment, StorageOp
from tests.e2e_fake.helpers import (
    GAME_ID,
    SAVE_A,
    SAVE_NAME_A,
    make_client,
    match_config,
    running_process,
    stopped_process,
    write_stable_save,
)


def _inject_next(
    storage: FakeStorage, operation: StorageOp, moment: FaultMoment
) -> None:
    storage.faults.inject(
        operation,
        moment=moment,
        occurrence=storage.faults.call_count(operation) + 1,
    )


def _ready_owner(
    tmp_path: Path,
) -> tuple[FakeStorage, FakeClock, RelayClient, MatchConfig]:
    storage = FakeStorage()
    clock = FakeClock()
    root = tmp_path / "owner"
    client = make_client(
        root,
        storage,
        clock,
        client_uuid=uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
    )
    config = match_config(root)
    client.initialize_or_join(
        config, operation_id="aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa"
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
    return storage, clock, client, config


@pytest.mark.pt("PT-03")
def test_failure_before_temp_upload_then_retry(tmp_path: Path) -> None:
    storage, clock, client, config = _ready_owner(tmp_path)
    # Next WRITE in this handoff is the staged save upload after lock creation.
    storage.faults.inject(
        StorageOp.WRITE,
        moment=FaultMoment.BEFORE,
        occurrence=storage.faults.call_count(StorageOp.WRITE) + 2,
    )
    failed = client.execute_handoff(
        GAME_ID, operation_id="bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb"
    )
    assert failed.outcome is HandoffOutcome.TRANSPORT_FAILURE
    remote = read_authoritative_manifest(storage, GAME_ID)
    assert remote.manifest is not None
    assert remote.manifest.protocol_sequence == 0
    result = client.execute_handoff(
        GAME_ID, operation_id="bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb"
    )
    assert result.outcome in {
        HandoffOutcome.COMMITTED,
        HandoffOutcome.IDEMPOTENT_ACK,
    }
    del clock, config


def test_failure_before_immutable_publish(tmp_path: Path) -> None:
    storage, _clock, client, _config = _ready_owner(tmp_path)
    _inject_next(storage, StorageOp.PUBLISH_NO_REPLACE, FaultMoment.BEFORE)
    failed = client.execute_handoff(
        GAME_ID, operation_id="cccccccc-3333-4333-8333-cccccccccccc"
    )
    assert failed.outcome is HandoffOutcome.TRANSPORT_FAILURE
    remote = read_authoritative_manifest(storage, GAME_ID)
    assert remote.manifest is not None
    assert remote.manifest.protocol_sequence == 0
    assert (
        client.execute_handoff(
            GAME_ID, operation_id="cccccccc-3333-4333-8333-cccccccccccc"
        ).outcome
        is HandoffOutcome.COMMITTED
    )


def test_lost_response_after_manifest_replace(tmp_path: Path) -> None:
    storage, _clock, client, _config = _ready_owner(tmp_path)
    _inject_next(storage, StorageOp.ATOMIC_REPLACE, FaultMoment.AFTER)
    result = client.execute_handoff(
        GAME_ID, operation_id="dddddddd-4444-4444-8444-dddddddddddd"
    )
    # Ambiguous after-commit becomes transport or idempotent on reconcile.
    assert result.outcome in {
        HandoffOutcome.COMMITTED,
        HandoffOutcome.IDEMPOTENT_ACK,
        HandoffOutcome.TRANSPORT_FAILURE,
        HandoffOutcome.LOCK_CLEANUP_AMBIGUOUS,
    }
    remote = read_authoritative_manifest(storage, GAME_ID)
    assert remote.outcome is ManifestReadOutcome.OK
    assert remote.manifest is not None
    # Either committed once or still retryable at seq 0 — never double-advanced.
    assert remote.manifest.protocol_sequence in {0, 1}
    if remote.manifest.protocol_sequence == 0:
        retry = client.execute_handoff(
            GAME_ID, operation_id="dddddddd-4444-4444-8444-dddddddddddd"
        )
        assert retry.outcome in {
            HandoffOutcome.COMMITTED,
            HandoffOutcome.IDEMPOTENT_ACK,
        }
    else:
        retry = client.execute_handoff(
            GAME_ID, operation_id="dddddddd-4444-4444-8444-dddddddddddd"
        )
        assert retry.outcome is HandoffOutcome.IDEMPOTENT_ACK
        remote2 = read_authoritative_manifest(storage, GAME_ID)
        assert remote2.manifest is not None
        assert remote2.manifest.protocol_sequence == 1


def test_restart_with_in_progress_journal(tmp_path: Path) -> None:
    storage, clock, client, config = _ready_owner(tmp_path)
    _inject_next(storage, StorageOp.ATOMIC_REPLACE, FaultMoment.BEFORE)
    assert (
        client.execute_handoff(
            GAME_ID, operation_id="eeeeeeee-5555-4555-8555-eeeeeeeeeeee"
        ).outcome
        is HandoffOutcome.TRANSPORT_FAILURE
    )
    root = Path(client.store.root)
    client.close()
    restarted = make_client(
        root,
        storage,
        clock,
        client_uuid=uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
    )
    restarted.open_match(config)
    result = restarted.execute_handoff(
        GAME_ID, operation_id="eeeeeeee-5555-4555-8555-eeeeeeeeeeee"
    )
    assert result.outcome in {
        HandoffOutcome.COMMITTED,
        HandoffOutcome.IDEMPOTENT_ACK,
    }


def test_transport_failure_while_waiting(tmp_path: Path) -> None:
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
    config_a = match_config(root_a)
    config_b = match_config(root_b, local_player_id="player_b", pbem_name="pbem-b")
    client_a.initialize_or_join(
        config_a, operation_id="ffffffff-6666-4666-8666-ffffffffffff"
    )
    client_b.initialize_or_join(
        config_b, operation_id="12121212-7777-4777-8777-121212121212"
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
            GAME_ID, operation_id="13131313-8888-4888-8888-131313131313"
        ).outcome
        is HandoffOutcome.COMMITTED
    )
    _inject_next(storage, StorageOp.READ, FaultMoment.BEFORE)
    waiting = client_b.reconcile(GAME_ID)
    assert waiting.retry_required or waiting.operational_state in {
        OperationalState.ERROR,
        OperationalState.DOWNLOADING,
        OperationalState.WAITING_FOR_OTHER_PLAYER,
    }
    recovered = client_b.reconcile(GAME_ID)
    assert recovered.records.downloaded_save is not None or recovered.retry_required


def test_same_hash_never_advances_twice(tmp_path: Path) -> None:
    storage, _clock, client, _config = _ready_owner(tmp_path)
    op = "14141414-9999-4999-8999-141414141414"
    first = client.execute_handoff(GAME_ID, operation_id=op)
    assert first.outcome is HandoffOutcome.COMMITTED
    second = client.execute_handoff(GAME_ID, operation_id=op)
    assert second.outcome is HandoffOutcome.IDEMPOTENT_ACK
    remote = read_authoritative_manifest(storage, GAME_ID)
    assert remote.manifest is not None
    assert remote.manifest.protocol_sequence == 1


def test_stale_replay_rejected(tmp_path: Path) -> None:
    storage, clock, client, config = _ready_owner(tmp_path)
    assert (
        client.execute_handoff(
            GAME_ID, operation_id="15151515-aaaa-4aaa-8aaa-151515151515"
        ).outcome
        is HandoffOutcome.COMMITTED
    )
    # After ownership moved, replaying the same bytes must not advance.
    root_b = tmp_path / "b"
    client_b = make_client(
        root_b,
        storage,
        clock,
        client_uuid=uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
    )
    config_b = match_config(root_b, local_player_id="player_b", pbem_name="pbem-b")
    client_b.initialize_or_join(
        config_b, operation_id="16161616-bbbb-4bbb-8bbb-161616161616"
    )
    client_b.reconcile(GAME_ID)
    client_b.request_start(GAME_ID)
    # Accepted hashes are excluded from detection; inject bytes for stale replay.
    replay = client_b.execute_handoff(
        GAME_ID,
        operation_id="17171717-cccc-4ccc-8ccc-171717171717",
        outgoing_bytes=SAVE_A,
        original_filename="Replay.CivBeyondSwordSave",
    )
    assert replay.outcome in {
        HandoffOutcome.STALE_REPLAY,
        HandoffOutcome.REJECT_INCOMING,
        HandoffOutcome.JOURNAL_ONLY_ACK,
    }
    remote = read_authoritative_manifest(storage, GAME_ID)
    assert remote.manifest is not None
    assert remote.manifest.protocol_sequence == 1
    del config


def test_post_commit_close_once(tmp_path: Path) -> None:
    storage, clock, client, config = _ready_owner(tmp_path)
    from civ4_turn_relay.domain import TurnHandlingMode

    managed = match_config(
        tmp_path / "managed-close",
        mode=TurnHandlingMode.FULLY_MANAGED,
        allow_force_close=True,
    )
    # Re-bind managed mode on a fresh match using same storage root semantics.
    client.close()
    storage = FakeStorage()
    clock = FakeClock()
    root = tmp_path / "managed-close"
    client = make_client(root, storage, clock)
    client.initialize_or_join(
        managed, operation_id="18181818-dddd-4ddd-8ddd-181818181818"
    )
    client.reconcile(GAME_ID)
    client.set_process_observation(GAME_ID, running_process(55))
    write_stable_save(
        Path(managed.pbem_save_directory),
        SAVE_NAME_A,
        SAVE_A,
        clock,
        client,
        GAME_ID,
    )
    op = "19191919-eeee-4eee-8eee-191919191919"
    # Process association exists before commit so execute_handoff's reconcile
    # can emit the exact post-commit close intent from attributed evidence.
    assert client.execute_handoff(GAME_ID, operation_id=op).outcome is (
        HandoffOutcome.COMMITTED
    )
    records = client.store.load_match_state_or_empty(GAME_ID)
    assert sha256_hex(SAVE_A) in records.processed_outgoing_hashes
    assert records.pending_post_commit_close is not None
    assert records.pending_post_commit_close.operation_id == op
    assert records.pending_post_commit_close.pid == 55
    assert records.pending_post_commit_close.close_requested is True
    second = client.reconcile(GAME_ID)
    assert not any(
        intent.kind is OrchestrationIntentKind.REQUEST_GRACEFUL_CLOSE
        for intent in second.intents
    )
    client.set_process_observation(GAME_ID, stopped_process(55))
    after_exit = client.reconcile(GAME_ID)
    assert after_exit.records.pending_post_commit_close is None
    assert not any(
        intent.kind is OrchestrationIntentKind.REQUEST_GRACEFUL_CLOSE
        for intent in after_exit.intents
    )
    del config
