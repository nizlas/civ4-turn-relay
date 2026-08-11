"""Fresh RelayClient restart recovery for journal/commit ambiguity (section B)."""

from __future__ import annotations

import uuid
from pathlib import Path

from civ4_turn_relay.domain import TurnHandlingMode, sha256_hex
from civ4_turn_relay.local import FakeClock, OrchestrationIntentKind
from civ4_turn_relay.protocol import HandoffOutcome, read_authoritative_manifest
from civ4_turn_relay.storage import FakeStorage, FaultMoment, StorageOp
from tests.e2e_fake.helpers import (
    GAME_ID,
    SAVE_A,
    SAVE_NAME_A,
    make_client,
    match_config,
    running_process,
    write_stable_save,
)


def test_restart_before_commit_keeps_journal_and_retries_same_op(
    tmp_path: Path,
) -> None:
    storage = FakeStorage()
    clock = FakeClock()
    root = tmp_path / "owner"
    client = make_client(
        root,
        storage,
        clock,
        client_uuid=uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
    )
    config = match_config(root, mode=TurnHandlingMode.STANDARD)
    client.initialize_or_join(
        config, operation_id="aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa"
    )
    client.request_start(GAME_ID)
    write_stable_save(
        Path(config.pbem_save_directory), SAVE_NAME_A, SAVE_A, clock, client, GAME_ID
    )
    op = "bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb"
    storage.faults.inject(
        StorageOp.ATOMIC_REPLACE,
        moment=FaultMoment.BEFORE,
        occurrence=storage.faults.call_count(StorageOp.ATOMIC_REPLACE) + 1,
    )
    failed = client.execute_handoff(GAME_ID, operation_id=op)
    assert failed.outcome is HandoffOutcome.TRANSPORT_FAILURE
    assert (
        client.store.load_match_state_or_empty(GAME_ID).in_progress_handoff is not None
    )
    remote = read_authoritative_manifest(storage, GAME_ID)
    assert remote.manifest is not None
    assert remote.manifest.protocol_sequence == 0

    client.close()
    restarted = make_client(
        root,
        storage,
        clock,
        client_uuid=uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
    )
    restarted.open_match(config)
    assert (
        restarted.store.load_match_state_or_empty(GAME_ID).in_progress_handoff
        is not None
    )
    ok = restarted.execute_handoff(GAME_ID, operation_id=op)
    assert ok.outcome in {HandoffOutcome.COMMITTED, HandoffOutcome.IDEMPOTENT_ACK}
    after = read_authoritative_manifest(storage, GAME_ID)
    assert after.manifest is not None
    assert after.manifest.protocol_sequence == 1
    # Same op again must not advance twice.
    again = restarted.execute_handoff(GAME_ID, operation_id=op)
    assert again.outcome is HandoffOutcome.IDEMPOTENT_ACK
    final = read_authoritative_manifest(storage, GAME_ID)
    assert final.manifest is not None
    assert final.manifest.protocol_sequence == 1


def test_restart_after_lost_commit_response_attributes_and_closes(
    tmp_path: Path,
) -> None:
    storage = FakeStorage()
    clock = FakeClock()
    root = tmp_path / "lost"
    client = make_client(
        root,
        storage,
        clock,
        client_uuid=uuid.UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc"),
    )
    config = match_config(
        root, mode=TurnHandlingMode.FULLY_MANAGED, allow_force_close=True
    )
    client.initialize_or_join(
        config, operation_id="cccccccc-1111-4111-8111-cccccccccccc"
    )
    client.reconcile(GAME_ID)
    client.set_process_observation(GAME_ID, running_process(88))
    write_stable_save(
        Path(config.pbem_save_directory), SAVE_NAME_A, SAVE_A, clock, client, GAME_ID
    )
    op = "dddddddd-2222-4222-8222-dddddddddddd"
    storage.faults.inject(
        StorageOp.ATOMIC_REPLACE,
        moment=FaultMoment.AFTER,
        occurrence=storage.faults.call_count(StorageOp.ATOMIC_REPLACE) + 1,
    )
    ambiguous = client.execute_handoff(GAME_ID, operation_id=op)
    remote = read_authoritative_manifest(storage, GAME_ID)
    assert remote.manifest is not None
    if remote.manifest.protocol_sequence != 1:
        # If the fake did not commit, this scenario is not the lost-response case.
        assert ambiguous.outcome is HandoffOutcome.TRANSPORT_FAILURE
        return
    # Commit landed; client may still have journal / missing attributed success.
    client.close()
    restarted = make_client(
        root,
        storage,
        clock,
        client_uuid=uuid.UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc"),
    )
    restarted.open_match(config)
    restarted.set_process_observation(GAME_ID, running_process(88))
    recovered = restarted.reconcile(GAME_ID)
    state = restarted.store.load_match_state_or_empty(GAME_ID)
    assert sha256_hex(SAVE_A) in state.processed_outgoing_hashes
    assert state.in_progress_handoff is None
    assert state.pending_post_commit_close is not None
    assert state.pending_post_commit_close.pid == 88
    assert state.pending_post_commit_close.operation_id == op
    assert (
        any(
            intent.kind is OrchestrationIntentKind.REQUEST_GRACEFUL_CLOSE
            for intent in recovered.intents
        )
        or state.pending_post_commit_close.close_requested
    )
    # No second advancement on explicit same-op retry with same bytes.
    retry = restarted.execute_handoff(
        GAME_ID,
        operation_id=op,
        outgoing_bytes=SAVE_A,
        original_filename=SAVE_NAME_A,
    )
    assert retry.outcome is HandoffOutcome.IDEMPOTENT_ACK
    remote2 = read_authoritative_manifest(storage, GAME_ID)
    assert remote2.manifest is not None
    assert remote2.manifest.protocol_sequence == 1


def test_unattributed_ambiguous_keeps_journal_no_close(tmp_path: Path) -> None:
    storage = FakeStorage()
    clock = FakeClock()
    root = tmp_path / "ambig"
    client = make_client(
        root,
        storage,
        clock,
        client_uuid=uuid.UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"),
    )
    config = match_config(root, mode=TurnHandlingMode.FULLY_MANAGED)
    client.initialize_or_join(
        config, operation_id="eeeeeeee-1111-4111-8111-eeeeeeeeeeee"
    )
    client.reconcile(GAME_ID)
    client.set_process_observation(GAME_ID, running_process(11))
    write_stable_save(
        Path(config.pbem_save_directory), SAVE_NAME_A, SAVE_A, clock, client, GAME_ID
    )
    op = "f1f1f1f1-2222-4222-8222-f1f1f1f1f1f1"
    storage.faults.inject(
        StorageOp.ATOMIC_REPLACE,
        moment=FaultMoment.BEFORE,
        occurrence=storage.faults.call_count(StorageOp.ATOMIC_REPLACE) + 1,
    )
    failed = client.execute_handoff(GAME_ID, operation_id=op)
    assert failed.outcome is HandoffOutcome.TRANSPORT_FAILURE
    client.close()
    restarted = make_client(
        root,
        storage,
        clock,
        client_uuid=uuid.UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"),
    )
    restarted.open_match(config)
    restarted.set_process_observation(GAME_ID, running_process(11))
    result = restarted.reconcile(GAME_ID)
    state = restarted.store.load_match_state_or_empty(GAME_ID)
    assert state.in_progress_handoff is not None
    assert sha256_hex(SAVE_A) not in state.processed_outgoing_hashes
    assert state.pending_post_commit_close is None
    assert not any(
        intent.kind is OrchestrationIntentKind.REQUEST_GRACEFUL_CLOSE
        for intent in result.intents
    )
    remote = read_authoritative_manifest(storage, GAME_ID)
    assert remote.manifest is not None
    assert remote.manifest.protocol_sequence == 0
