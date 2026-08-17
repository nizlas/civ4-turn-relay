"""RelayClient process integration over the supervisor port (P7 part B)."""

from __future__ import annotations

import uuid
from pathlib import Path

from civ4_turn_relay.app import PendingUserAction, ProcessStatus, RelayClient
from civ4_turn_relay.app.relay_client import LAUNCH_LOG_FILENAME
from civ4_turn_relay.domain import MatchConfig, OperationalState, TurnHandlingMode
from civ4_turn_relay.local import FakeClock, LocalStore
from civ4_turn_relay.process import (
    CloseRequestOutcome,
    FakeMachine,
    FakeProcessSupervisor,
    FocusOutcome,
    LaunchPlanOutcome,
    ProbeOutcome,
    ProcessIdentity,
    ProcessScanEntry,
)
from civ4_turn_relay.protocol import (
    HandoffOutcome,
    InitializeOutcome,
    read_authoritative_manifest,
)
from civ4_turn_relay.storage import FakeStorage, FaultMoment, StorageOp
from tests.e2e_fake.helpers import (
    GAME_ID,
    GAME_ID_B,
    NOW_UTC,
    SAVE_A,
    SAVE_B,
    SAVE_NAME_A,
    SAVE_NAME_B,
    make_client,
    match_config,
    write_stable_save,
)

LOCAL_UUID = uuid.UUID("21212121-2121-4121-8121-212121212121")
OPPONENT_UUID = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
PROFILE_B_UUID = uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")


def _exe_path(tmp_path: Path) -> str:
    exe = tmp_path / "Civ4BeyondSword.exe"
    exe.write_bytes(b"placeholder-executable")
    return str(exe.resolve())


def _make_process_client(
    root: Path,
    storage: FakeStorage,
    clock: FakeClock,
    supervisor: FakeProcessSupervisor,
    *,
    civ4_executable: str | None,
    steam_app_id: str | None = None,
    steam_executable: str | None = None,
    client_uuid: uuid.UUID | None = None,
) -> RelayClient:
    store = LocalStore(root)
    fixed = client_uuid or LOCAL_UUID
    store.get_or_create_installation_identity(uuid_factory=lambda: fixed)
    return RelayClient(
        store=store,
        storage=storage,
        clock=clock,
        poll_interval_seconds=0.1,
        now_utc_fn=lambda: NOW_UTC,
        operation_id_factory=lambda: str(uuid.uuid4()),
        process_supervisor=supervisor,
        civ4_executable=civ4_executable,
        steam_app_id=steam_app_id,
        steam_executable=steam_executable,
    )


def _opponent_commits_first_save(
    tmp_path: Path, storage: FakeStorage, clock: FakeClock
) -> None:
    root = tmp_path / "opponent"
    opponent = make_client(root, storage, clock, client_uuid=OPPONENT_UUID)
    config = match_config(root, local_player_id="player_a", pbem_name="pbem-opponent")
    init = opponent.initialize_or_join(
        config, operation_id="a1a1a1a1-a1a1-4a1a-8a1a-a1a1a1a1a1a1"
    )
    assert init.outcome is InitializeOutcome.CREATED
    handoff = opponent.execute_handoff(
        GAME_ID,
        operation_id="a2a2a2a2-a2a2-4a2a-8a2a-a2a2a2a2a2a2",
        outgoing_bytes=SAVE_A,
        original_filename=SAVE_NAME_A,
    )
    assert handoff.outcome is HandoffOutcome.COMMITTED
    opponent.close()


def _local_client_after_opponent_commit(
    tmp_path: Path,
    storage: FakeStorage,
    clock: FakeClock,
    supervisor: FakeProcessSupervisor,
    *,
    mode: TurnHandlingMode = TurnHandlingMode.FULLY_MANAGED,
) -> tuple[RelayClient, MatchConfig, str]:
    _opponent_commits_first_save(tmp_path, storage, clock)
    exe = _exe_path(tmp_path)
    root = tmp_path / "local"
    client = _make_process_client(root, storage, clock, supervisor, civ4_executable=exe)
    config = match_config(
        root,
        local_player_id="player_b",
        mode=mode,
        pbem_name="pbem-local",
    )
    joined = client.initialize_or_join(
        config, operation_id="b1b1b1b1-b1b1-4b1b-8b1b-b1b1b1b1b1b1"
    )
    assert joined.outcome is InitializeOutcome.JOINED_EXISTING
    return client, config, exe


def _opener_client(
    tmp_path: Path,
    storage: FakeStorage,
    clock: FakeClock,
    supervisor: FakeProcessSupervisor,
    *,
    civ4_executable: str | None,
    mode: TurnHandlingMode = TurnHandlingMode.FULLY_MANAGED,
) -> tuple[RelayClient, MatchConfig]:
    root = tmp_path / "opener"
    client = _make_process_client(
        root, storage, clock, supervisor, civ4_executable=civ4_executable
    )
    config = match_config(
        root, local_player_id="player_a", mode=mode, pbem_name="pbem-opener"
    )
    created = client.initialize_or_join(
        config, operation_id="c1c1c1c1-c1c1-4c1c-8c1c-c1c1c1c1c1c1"
    )
    assert created.outcome is InitializeOutcome.CREATED
    return client, config


def _associated_identity(
    client: RelayClient, game_id: str = GAME_ID
) -> ProcessIdentity:
    records = client.store.load_match_state_or_empty(game_id)
    association = records.process_association
    assert association is not None
    return ProcessIdentity(
        pid=association.pid,
        process_start_time_utc=association.process_start_time_utc,
        process_create_time_ns=association.process_create_time_ns,
        executable_path=association.executable_path,
    )


def test_fully_managed_launches_once_and_survives_restart(tmp_path: Path) -> None:
    storage = FakeStorage()
    clock = FakeClock()
    supervisor = FakeProcessSupervisor()
    client, config, exe = _local_client_after_opponent_commit(
        tmp_path, storage, clock, supervisor
    )

    snap = client.tick(GAME_ID)
    assert snap.operational_state is OperationalState.CIV_RUNNING
    assert len(supervisor.launched) == 1
    records = client.store.load_match_state_or_empty(GAME_ID)
    assert records.downloaded_save is not None
    assert supervisor.launched[0].argv == (
        exe,
        f"/fxsload={records.downloaded_save.local_path}",
        "mod=\\AdvCiv",
    )
    assert records.launch_attempt is not None
    assert records.process_association is not None

    client.tick(GAME_ID)
    client.tick(GAME_ID)
    assert len(supervisor.launched) == 1
    assert client.process_status(GAME_ID).status is ProcessStatus.RUNNING
    client.close()

    restarted = _make_process_client(
        tmp_path / "local", storage, clock, supervisor, civ4_executable=exe
    )
    restarted.open_match(config)
    snap_restarted = restarted.tick(GAME_ID)
    assert len(supervisor.launched) == 1
    assert snap_restarted.operational_state is OperationalState.CIV_RUNNING
    status = restarted.process_status(GAME_ID)
    assert status.status is ProcessStatus.RUNNING
    assert status.identity == _associated_identity(restarted)
    restarted.close()


def test_fully_managed_launch_carries_configured_steam_context(tmp_path: Path) -> None:
    storage = FakeStorage()
    clock = FakeClock()
    supervisor = FakeProcessSupervisor()
    _opponent_commits_first_save(tmp_path, storage, clock)
    exe = _exe_path(tmp_path)
    steam = tmp_path / "steam.exe"
    steam.write_bytes(b"placeholder-steam")
    root = tmp_path / "local-steam"
    client = _make_process_client(
        root,
        storage,
        clock,
        supervisor,
        civ4_executable=exe,
        steam_app_id="8800",
        steam_executable=str(steam),
    )
    config = match_config(
        root,
        local_player_id="player_b",
        mode=TurnHandlingMode.FULLY_MANAGED,
        pbem_name="pbem-steam",
    )
    joined = client.initialize_or_join(config, operation_id=str(uuid.uuid4()))
    assert joined.outcome is InitializeOutcome.JOINED_EXISTING
    assert client.tick(GAME_ID).operational_state is OperationalState.CIV_RUNNING
    command = supervisor.launched[0]
    assert command.environment == (("SteamAppId", "8800"), ("SteamGameId", "8800"))
    assert command.steam_executable_path == str(steam)
    client.close()


def test_sequence_zero_launches_without_save_argument(tmp_path: Path) -> None:
    storage = FakeStorage()
    clock = FakeClock()
    supervisor = FakeProcessSupervisor()
    exe = _exe_path(tmp_path)
    client, _config = _opener_client(
        tmp_path, storage, clock, supervisor, civ4_executable=exe
    )
    snap = client.tick(GAME_ID)
    assert snap.operational_state is OperationalState.CIV_RUNNING
    assert len(supervisor.launched) == 1
    assert supervisor.launched[0].argv == (exe, "mod=\\AdvCiv")
    client.close()


def test_missing_executable_blocks_launch_until_explicit_start(
    tmp_path: Path,
) -> None:
    storage = FakeStorage()
    clock = FakeClock()
    supervisor = FakeProcessSupervisor()
    client, config = _opener_client(
        tmp_path, storage, clock, supervisor, civ4_executable=None
    )
    snap = client.tick(GAME_ID)
    assert snap.operational_state is OperationalState.WAITING_FOR_MY_FIRST_SAVE
    assert supervisor.launched == []
    status = client.process_status(GAME_ID)
    assert status.status is ProcessStatus.LAUNCH_FAILED
    assert status.launch_blocked_reason is not None
    assert "executable" in status.launch_blocked_reason

    again = client.tick(GAME_ID)
    assert again.operational_state is not OperationalState.CIV_RUNNING
    assert supervisor.launched == []
    client.close()

    exe = _exe_path(tmp_path)
    retry = _make_process_client(
        tmp_path / "opener", storage, clock, supervisor, civ4_executable=exe
    )
    retry.open_match(config)
    result = retry.request_start(GAME_ID)
    assert result.operational_state is OperationalState.CIV_RUNNING
    assert len(supervisor.launched) == 1
    assert retry.process_status(GAME_ID).status is ProcessStatus.RUNNING
    retry.close()


def test_immediate_exit_fails_without_relaunch_loop(tmp_path: Path) -> None:
    storage = FakeStorage()
    clock = FakeClock()
    supervisor = FakeProcessSupervisor()
    supervisor.exit_immediately = True
    exe = _exe_path(tmp_path)
    client, _config = _opener_client(
        tmp_path, storage, clock, supervisor, civ4_executable=exe
    )
    snap = client.tick(GAME_ID)
    assert snap.operational_state is not OperationalState.CIV_RUNNING
    assert len(supervisor.launched) == 1
    status = client.process_status(GAME_ID)
    assert status.status is ProcessStatus.LAUNCH_FAILED
    records = client.store.load_match_state_or_empty(GAME_ID)
    assert records.process_association is None

    client.tick(GAME_ID)
    client.tick(GAME_ID)
    assert len(supervisor.launched) == 1

    supervisor.exit_immediately = False
    result = client.request_start(GAME_ID)
    assert result.operational_state is OperationalState.CIV_RUNNING
    assert len(supervisor.launched) == 2
    assert client.process_status(GAME_ID).status is ProcessStatus.RUNNING
    client.close()


def test_fully_managed_commit_terminates_directly_exactly_once(
    tmp_path: Path,
) -> None:
    """A committed handoff directly terminates the Relay-launched process.

    No graceful close request (WM_CLOSE) is ever sent first — Civ's modal
    PBEM confirmation blocks it — and no termination happens before the
    commit is authoritatively proven.
    """
    storage = FakeStorage()
    clock = FakeClock()
    supervisor = FakeProcessSupervisor()
    client, config, _exe = _local_client_after_opponent_commit(
        tmp_path, storage, clock, supervisor
    )
    client.tick(GAME_ID)
    identity = _associated_identity(client)
    # While the turn is still being played, nothing is ever closed.
    client.tick(GAME_ID)
    assert supervisor.terminations == []
    assert supervisor.close_requests == []

    write_stable_save(
        Path(config.pbem_save_directory),
        SAVE_NAME_B,
        SAVE_B,
        clock,
        client,
        GAME_ID,
    )
    snap = client.tick(
        GAME_ID, auto_handoff_operation_id="b2b2b2b2-b2b2-4b2b-8b2b-b2b2b2b2b2b2"
    )
    assert snap.protocol_sequence == 2
    # Terminate fired exactly once, only after the manifest commit, and no
    # graceful close was ever attempted first.
    assert supervisor.terminations == [identity]
    assert supervisor.close_requests == []
    assert client.process_status(GAME_ID).status is ProcessStatus.SAFELY_CLOSED
    records = client.store.load_match_state_or_empty(GAME_ID)
    assert records.pending_post_commit_close is None

    snap_after = client.tick(GAME_ID)
    assert snap_after.operational_state is OperationalState.WAITING_FOR_OTHER_PLAYER
    clock.advance(20.0)
    client.tick(GAME_ID)
    assert supervisor.terminations == [identity]
    assert supervisor.close_requests == []
    client.close()


def test_termination_failure_is_truthful_and_never_rolls_back(
    tmp_path: Path,
) -> None:
    """A failed termination leaves the committed handoff intact and visible."""
    storage = FakeStorage()
    clock = FakeClock()
    supervisor = FakeProcessSupervisor()
    supervisor.terminate_fails = True
    client, config, _exe = _local_client_after_opponent_commit(
        tmp_path, storage, clock, supervisor
    )
    client.tick(GAME_ID)
    identity = _associated_identity(client)
    write_stable_save(
        Path(config.pbem_save_directory),
        SAVE_NAME_B,
        SAVE_B,
        clock,
        client,
        GAME_ID,
    )
    snap = client.tick(
        GAME_ID, auto_handoff_operation_id="b3b3b3b3-b3b3-4b3b-8b3b-b3b3b3b3b3b3"
    )
    # The commit stands regardless of the failed close.
    assert snap.protocol_sequence == 2
    assert snap.operational_state is OperationalState.WAITING_FOR_OTHER_PLAYER
    assert supervisor.terminations == [identity]
    status = client.process_status(GAME_ID)
    assert status.status is ProcessStatus.CLOSE_FAILED
    assert "could not be closed" in status.message

    # Never a retry loop: later ticks do not terminate again this session.
    client.tick(GAME_ID)
    clock.advance(20.0)
    client.tick(GAME_ID)
    assert supervisor.terminations == [identity]
    assert supervisor.close_requests == []
    records = client.store.load_match_state_or_empty(GAME_ID)
    pending = records.pending_post_commit_close
    assert pending is not None and pending.close_requested

    # The manual Close fallback retries the entitled termination directly.
    supervisor.terminate_fails = False
    result = client.request_civ_close(GAME_ID)
    assert result.outcome is CloseRequestOutcome.REQUESTED
    assert supervisor.terminations == [identity, identity]
    assert supervisor.close_requests == []
    assert client.process_status(GAME_ID).status is ProcessStatus.SAFELY_CLOSED
    records = client.store.load_match_state_or_empty(GAME_ID)
    assert records.pending_post_commit_close is None
    client.close()


def test_stale_close_entitlement_never_acts_on_same_second_reused_pid(
    tmp_path: Path,
) -> None:
    """A close entitlement must not close or terminate a reused pid.

    The reused process shares the pid and the whole-second UTC start time of
    the entitled one; only the precise creation token differs.
    """
    storage = FakeStorage()
    clock = FakeClock()
    supervisor = FakeProcessSupervisor()
    supervisor.terminate_fails = True
    client, config, exe = _local_client_after_opponent_commit(
        tmp_path, storage, clock, supervisor
    )
    client.tick(GAME_ID)
    identity = _associated_identity(client)
    write_stable_save(
        Path(config.pbem_save_directory),
        SAVE_NAME_B,
        SAVE_B,
        clock,
        client,
        GAME_ID,
    )
    client.tick(
        GAME_ID, auto_handoff_operation_id="b7b7b7b7-b7b7-4b7b-8b7b-b7b7b7b7b7b7"
    )
    # The first termination attempt failed; the entitlement stays pending.
    assert supervisor.terminations == [identity]
    client.close()

    # The entitled process exits on its own; an unrelated process reuses the
    # pid within the same wall-clock second.
    supervisor.mark_exited(identity)
    reused = ProcessIdentity(
        pid=identity.pid,
        process_start_time_utc=identity.process_start_time_utc,
        process_create_time_ns=identity.process_create_time_ns + 1,
        executable_path=identity.executable_path,
    )
    supervisor.spawn_external(reused)

    supervisor.terminate_fails = False
    restarted = _make_process_client(
        tmp_path / "local", storage, clock, supervisor, civ4_executable=exe
    )
    restarted.open_match(config)
    restarted.tick(GAME_ID)
    clock.advance(20.0)
    restarted.tick(GAME_ID)
    # The stale entitlement never escalates to the reused pid: the fresh
    # pre-termination probe reports a mismatch and nothing is touched.
    assert supervisor.terminations == [identity]
    assert supervisor.close_requests == []
    assert supervisor.probe(reused).outcome is ProbeOutcome.RUNNING_MATCH
    restarted.close()


def test_restart_after_commit_terminates_entitled_process_once(
    tmp_path: Path,
) -> None:
    """Commit, then crash/restart: the exact old process still gets closed.

    The at-most-once guard is session-local, so the restarted Relay makes
    exactly one fresh termination attempt against the re-verified identity
    and never loops.
    """
    storage = FakeStorage()
    clock = FakeClock()
    supervisor = FakeProcessSupervisor()
    supervisor.terminate_fails = True
    client, config, exe = _local_client_after_opponent_commit(
        tmp_path, storage, clock, supervisor
    )
    client.tick(GAME_ID)
    identity = _associated_identity(client)
    write_stable_save(
        Path(config.pbem_save_directory),
        SAVE_NAME_B,
        SAVE_B,
        clock,
        client,
        GAME_ID,
    )
    snap = client.tick(
        GAME_ID, auto_handoff_operation_id="b5b5b5b5-b5b5-4b5b-8b5b-b5b5b5b5b5b5"
    )
    assert snap.protocol_sequence == 2
    assert supervisor.terminations == [identity]
    client.close()

    supervisor.terminate_fails = False
    restarted = _make_process_client(
        tmp_path / "local", storage, clock, supervisor, civ4_executable=exe
    )
    restarted.open_match(config)
    restarted.tick(GAME_ID)
    # One fresh attempt against the re-verified identity; still no WM_CLOSE.
    assert supervisor.terminations == [identity, identity]
    assert supervisor.close_requests == []
    assert restarted.process_status(GAME_ID).status is ProcessStatus.SAFELY_CLOSED
    records = restarted.store.load_match_state_or_empty(GAME_ID)
    assert records.pending_post_commit_close is None

    restarted.tick(GAME_ID)
    clock.advance(20.0)
    restarted.tick(GAME_ID)
    assert supervisor.terminations == [identity, identity]
    restarted.close()


def test_lost_commit_response_terminates_exact_process_after_restart(
    tmp_path: Path,
) -> None:
    """Recovery: the commit landed but the response was lost before the
    crash. The restarted Relay attributes its journal (idempotent
    acknowledgement), gains the exact close entitlement, and terminates the
    still-running old Civ process — never a WM_CLOSE, never twice."""
    storage = FakeStorage()
    clock = FakeClock()
    supervisor = FakeProcessSupervisor()
    client, config, exe = _local_client_after_opponent_commit(
        tmp_path, storage, clock, supervisor
    )
    client.tick(GAME_ID)
    identity = _associated_identity(client)
    write_stable_save(
        Path(config.pbem_save_directory),
        SAVE_NAME_B,
        SAVE_B,
        clock,
        client,
        GAME_ID,
    )
    op = "b8b8b8b8-b8b8-4b8b-8b8b-b8b8b8b8b8b8"
    storage.faults.inject(
        StorageOp.ATOMIC_REPLACE,
        moment=FaultMoment.AFTER,
        occurrence=storage.faults.call_count(StorageOp.ATOMIC_REPLACE) + 1,
    )
    ambiguous = client.execute_handoff(GAME_ID, operation_id=op)
    remote = read_authoritative_manifest(storage, GAME_ID)
    assert remote.manifest is not None
    if remote.manifest.protocol_sequence != 2:
        # If the fake did not commit, this is not the lost-response case.
        assert ambiguous.outcome is HandoffOutcome.TRANSPORT_FAILURE
        return
    # Ambiguous evidence never authorizes a close before attribution.
    assert supervisor.terminations == []
    client.close()

    restarted = _make_process_client(
        tmp_path / "local", storage, clock, supervisor, civ4_executable=exe
    )
    restarted.open_match(config)
    restarted.tick(GAME_ID)
    assert supervisor.terminations == [identity]
    assert supervisor.close_requests == []
    assert restarted.process_status(GAME_ID).status is ProcessStatus.SAFELY_CLOSED
    records = restarted.store.load_match_state_or_empty(GAME_ID)
    assert records.pending_post_commit_close is None
    # The committed server state is untouched by the recovery close.
    remote_after = read_authoritative_manifest(storage, GAME_ID)
    assert remote_after.manifest is not None
    assert remote_after.manifest.protocol_sequence == 2

    restarted.tick(GAME_ID)
    clock.advance(20.0)
    restarted.tick(GAME_ID)
    assert supervisor.terminations == [identity]
    restarted.close()


def test_standard_mode_never_auto_launches_or_closes(tmp_path: Path) -> None:
    storage = FakeStorage()
    clock = FakeClock()
    supervisor = FakeProcessSupervisor()
    client, config, _exe = _local_client_after_opponent_commit(
        tmp_path, storage, clock, supervisor, mode=TurnHandlingMode.STANDARD
    )
    client.tick(GAME_ID)
    client.tick(GAME_ID)
    assert supervisor.launched == []
    assert client.process_status(GAME_ID).status is ProcessStatus.READY

    result = client.request_start(GAME_ID)
    assert result.operational_state is OperationalState.CIV_RUNNING
    assert len(supervisor.launched) == 1

    write_stable_save(
        Path(config.pbem_save_directory),
        SAVE_NAME_B,
        SAVE_B,
        clock,
        client,
        GAME_ID,
    )
    handoff = client.execute_handoff(
        GAME_ID, operation_id="b6b6b6b6-b6b6-4b6b-8b6b-b6b6b6b6b6b6"
    )
    assert handoff.outcome is HandoffOutcome.COMMITTED
    client.tick(GAME_ID)
    clock.advance(20.0)
    client.tick(GAME_ID)
    assert supervisor.close_requests == []
    assert supervisor.terminations == []
    client.close()


def test_pid_reuse_after_restart_never_targets_impostor(tmp_path: Path) -> None:
    storage = FakeStorage()
    clock = FakeClock()
    supervisor = FakeProcessSupervisor()
    client, config, exe = _local_client_after_opponent_commit(
        tmp_path, storage, clock, supervisor
    )
    client.tick(GAME_ID)
    identity = _associated_identity(client)
    client.close()

    supervisor.mark_exited(identity)
    # PID reused within the same whole second: identical pid, identical UTC
    # start-second string, different precise creation token.
    impostor = ProcessIdentity(
        pid=identity.pid,
        process_start_time_utc=identity.process_start_time_utc,
        process_create_time_ns=identity.process_create_time_ns + 1,
        executable_path=identity.executable_path,
    )
    supervisor.spawn_external(impostor)

    restarted = _make_process_client(
        tmp_path / "local", storage, clock, supervisor, civ4_executable=exe
    )
    restarted.open_match(config)
    snap = restarted.tick(GAME_ID)
    assert snap.operational_state is not OperationalState.CIV_RUNNING
    assert len(supervisor.launched) == 1
    assert supervisor.close_requests == []
    assert supervisor.terminations == []
    assert supervisor.focus_requests == []
    # The startup retry schedules a fresh guarded launch; the pre-launch scan
    # sees the impostor Civ and safely defers instead of spawning, adopting,
    # or targeting it.
    assert (
        restarted.process_status(GAME_ID).status
        is ProcessStatus.WAITING_FOR_EXISTING_CIV
    )
    restarted.tick(GAME_ID)
    assert len(supervisor.launched) == 1
    assert supervisor.close_requests == []
    assert supervisor.terminations == []

    # Once the impostor exits, the deferred launch proceeds with a brand-new
    # identity; the stale association is never adopted.
    supervisor.mark_exited(impostor)
    resolved = restarted.tick(GAME_ID)
    assert resolved.operational_state is OperationalState.CIV_RUNNING
    assert len(supervisor.launched) == 2
    fresh = _associated_identity(restarted)
    assert fresh != identity
    assert fresh != impostor
    assert supervisor.close_requests == []
    assert supervisor.terminations == []
    restarted.close()


def test_exit_without_outgoing_save_requires_explicit_start(
    tmp_path: Path,
) -> None:
    storage = FakeStorage()
    clock = FakeClock()
    supervisor = FakeProcessSupervisor()
    client, _config, _exe = _local_client_after_opponent_commit(
        tmp_path, storage, clock, supervisor
    )
    snap = client.tick(GAME_ID)
    assert snap.operational_state is OperationalState.CIV_RUNNING
    identity = _associated_identity(client)

    supervisor.mark_exited(identity)
    after = client.tick(GAME_ID)
    assert after.operational_state is OperationalState.MY_TURN_DOWNLOADED
    assert after.pending_user_action is PendingUserAction.START_OR_RESUME
    client.tick(GAME_ID)
    client.tick(GAME_ID)
    assert len(supervisor.launched) == 1
    client.close()


def test_fully_managed_retries_once_when_relay_restarts_after_civ_exit(
    tmp_path: Path,
) -> None:
    storage = FakeStorage()
    clock = FakeClock()
    supervisor = FakeProcessSupervisor()
    client, config, exe = _local_client_after_opponent_commit(
        tmp_path, storage, clock, supervisor
    )
    client.tick(GAME_ID)
    first_identity = _associated_identity(client)
    supervisor.mark_exited(first_identity)
    client.tick(GAME_ID)
    client.close()

    restarted = _make_process_client(
        tmp_path / "local", storage, clock, supervisor, civ4_executable=exe
    )
    restarted.open_match(config)
    retried = restarted.tick(GAME_ID)

    assert retried.operational_state is OperationalState.CIV_RUNNING
    assert len(supervisor.launched) == 2

    # A failed launch can be retried once per Relay startup, never on every
    # periodic tick.
    restarted.tick(GAME_ID)
    restarted.tick(GAME_ID)
    assert len(supervisor.launched) == 2
    restarted.close()


def test_launch_preview_is_a_dry_run(tmp_path: Path) -> None:
    storage = FakeStorage()
    clock = FakeClock()
    supervisor = FakeProcessSupervisor()
    exe = _exe_path(tmp_path)
    client, _config = _opener_client(
        tmp_path, storage, clock, supervisor, civ4_executable=exe
    )
    plan = client.launch_preview(GAME_ID)
    assert plan.outcome is LaunchPlanOutcome.READY
    assert plan.command is not None
    assert exe in plan.command.dry_run_preview()
    assert supervisor.launched == []
    client.close()

    supervisor_b = FakeProcessSupervisor()
    root_b = tmp_path / "no-exe"
    missing = _make_process_client(
        root_b, storage, clock, supervisor_b, civ4_executable=None
    )
    config_b = match_config(
        root_b, game_id=GAME_ID_B, local_player_id="player_a", pbem_name="pbem-b"
    )
    missing.initialize_or_join(
        config_b, operation_id="d1d1d1d1-d1d1-4d1d-8d1d-d1d1d1d1d1d1"
    )
    plan_b = missing.launch_preview(GAME_ID_B)
    assert plan_b.outcome is LaunchPlanOutcome.EXECUTABLE_NOT_CONFIGURED
    assert supervisor_b.launched == []
    missing.close()


def test_unavailable_supervisor_reports_unavailable(tmp_path: Path) -> None:
    storage = FakeStorage()
    clock = FakeClock()
    supervisor = FakeProcessSupervisor(available=False)
    exe = _exe_path(tmp_path)
    client, _config = _opener_client(
        tmp_path, storage, clock, supervisor, civ4_executable=exe
    )
    snap = client.tick(GAME_ID)
    assert snap.operational_state is not OperationalState.CIV_RUNNING
    assert supervisor.launched == []
    status = client.process_status(GAME_ID)
    assert status.status is ProcessStatus.UNAVAILABLE
    assert status.message
    client.close()

    plain_root = tmp_path / "plain"
    plain = make_client(plain_root, storage, clock)
    config_b = match_config(
        plain_root, game_id=GAME_ID_B, local_player_id="player_a", pbem_name="pbem-b"
    )
    plain.initialize_or_join(
        config_b, operation_id="d2d2d2d2-d2d2-4d2d-8d2d-d2d2d2d2d2d2"
    )
    no_adapter = plain.process_status(GAME_ID_B)
    assert no_adapter.status is ProcessStatus.UNAVAILABLE
    assert no_adapter.message == "no process adapter configured"
    plain.close()


# --- cross-instance launch guard: two Relay profiles on one computer --------


def _two_profile_setup(
    tmp_path: Path,
    storage: FakeStorage,
    clock: FakeClock,
    machine: FakeMachine,
    *,
    mode_b: TurnHandlingMode = TurnHandlingMode.FULLY_MANAGED,
) -> tuple[
    RelayClient,
    RelayClient,
    FakeProcessSupervisor,
    FakeProcessSupervisor,
    ProcessIdentity,
]:
    """Two Relay profiles (A and B) sharing one PC and one Civ install.

    Profile A launches Civ at sequence 0 and commits its first turn, but its
    Civ process survives the post-commit termination attempt (scripted
    failure) and is still open while profile B already owns the next turn.
    """
    exe = _exe_path(tmp_path)
    supervisor_a = FakeProcessSupervisor(machine=machine)
    supervisor_a.terminate_fails = True
    supervisor_b = FakeProcessSupervisor(machine=machine)

    root_a = tmp_path / "profile-a"
    client_a = _make_process_client(
        root_a, storage, clock, supervisor_a, civ4_executable=exe
    )
    config_a = match_config(
        root_a,
        local_player_id="player_a",
        mode=TurnHandlingMode.FULLY_MANAGED,
        pbem_name="pbem-a",
    )
    created = client_a.initialize_or_join(
        config_a, operation_id="e1e1e1e1-e1e1-4e1e-8e1e-e1e1e1e1e1e1"
    )
    assert created.outcome is InitializeOutcome.CREATED

    root_b = tmp_path / "profile-b"
    client_b = _make_process_client(
        root_b,
        storage,
        clock,
        supervisor_b,
        civ4_executable=exe,
        client_uuid=PROFILE_B_UUID,
    )
    config_b = match_config(
        root_b, local_player_id="player_b", mode=mode_b, pbem_name="pbem-b"
    )
    joined = client_b.initialize_or_join(
        config_b, operation_id="e2e2e2e2-e2e2-4e2e-8e2e-e2e2e2e2e2e2"
    )
    assert joined.outcome is InitializeOutcome.JOINED_EXISTING

    snap_a = client_a.tick(GAME_ID)
    assert snap_a.operational_state is OperationalState.CIV_RUNNING
    write_stable_save(
        Path(config_a.pbem_save_directory),
        SAVE_NAME_A,
        SAVE_A,
        clock,
        client_a,
        GAME_ID,
    )
    commit = client_a.tick(
        GAME_ID, auto_handoff_operation_id="e3e3e3e3-e3e3-4e3e-8e3e-e3e3e3e3e3e3"
    )
    assert commit.protocol_sequence == 1
    identity_a = _associated_identity(client_a)
    assert supervisor_a.terminations == [identity_a]
    assert supervisor_a.close_requests == []
    assert machine.is_running(identity_a)
    return client_a, client_b, supervisor_a, supervisor_b, identity_a


def test_second_profile_waits_for_existing_civ_then_launches_once(
    tmp_path: Path,
) -> None:
    storage = FakeStorage()
    clock = FakeClock()
    machine = FakeMachine()
    client_a, client_b, supervisor_a, supervisor_b, identity_a = _two_profile_setup(
        tmp_path, storage, clock, machine
    )
    acquisitions_before = len(machine.guard_acquisitions)

    # B's Fully Managed tick defers: A's Civ is still closing.
    snap_b = client_b.tick(GAME_ID)
    assert snap_b.operational_state is OperationalState.MY_TURN_DOWNLOADED
    assert supervisor_b.launched == []
    status = client_b.process_status(GAME_ID)
    assert status.status is ProcessStatus.WAITING_FOR_EXISTING_CIV
    records_b = client_b.store.load_match_state_or_empty(GAME_ID)
    # The launch-attempt key is not consumed and the foreign Civ is never
    # adopted as this match's process.
    assert records_b.launch_attempt is None
    assert records_b.process_association is None
    assert records_b.downloaded_save is not None

    # One guarded attempt per ordinary tick — no busy retry loop.
    assert len(machine.guard_acquisitions) == acquisitions_before + 1
    client_b.tick(GAME_ID)
    assert len(machine.guard_acquisitions) == acquisitions_before + 2
    assert supervisor_b.launched == []

    # Safety: the blocking foreign process is never focused, closed,
    # terminated, or associated; ownership stays with B on the server.
    assert supervisor_b.focus_requests == []
    assert supervisor_b.close_requests == []
    assert supervisor_b.terminations == []
    assert machine.is_running(identity_a)
    assert snap_b.protocol_sequence == 1
    assert snap_b.current_player_id == "player_b"

    # After A's Civ finally exits, B's next ordinary tick launches once.
    supervisor_a.mark_exited(identity_a)
    snap_launched = client_b.tick(GAME_ID)
    assert snap_launched.operational_state is OperationalState.CIV_RUNNING
    assert len(supervisor_b.launched) == 1
    assert client_b.process_status(GAME_ID).status is ProcessStatus.RUNNING
    records_after = client_b.store.load_match_state_or_empty(GAME_ID)
    assert records_after.process_association is not None
    assert records_after.launch_attempt is not None

    # Exactly once: further ticks never spawn a duplicate.
    client_b.tick(GAME_ID)
    client_b.tick(GAME_ID)
    assert len(supervisor_b.launched) == 1
    client_a.close()
    client_b.close()


def test_fully_managed_auto_launches_a_later_turn_in_the_same_session(
    tmp_path: Path,
) -> None:
    """A successful first turn must not consume auto-launch for all later turns."""
    storage = FakeStorage()
    clock = FakeClock()
    machine = FakeMachine()
    client_a, client_b, supervisor_a, supervisor_b, identity_a = _two_profile_setup(
        tmp_path, storage, clock, machine
    )

    # B takes sequence 1 after A's first Civ instance has closed.
    supervisor_a.mark_exited(identity_a)
    client_b.tick(GAME_ID)
    identity_b = _associated_identity(client_b)
    assert len(supervisor_b.launched) == 1

    # B commits sequence 1, making sequence 2 A's turn again.
    config_b = client_b.store.load_match_config(GAME_ID)
    write_stable_save(
        Path(config_b.pbem_save_directory),
        SAVE_NAME_B,
        SAVE_B,
        clock,
        client_b,
        GAME_ID,
    )
    committed = client_b.tick(
        GAME_ID, auto_handoff_operation_id="f1f1f1f1-f1f1-4f1f-8f1f-f1f1f1f1f1f1"
    )
    assert committed.protocol_sequence == 2
    supervisor_b.mark_exited(identity_b)

    # A is still the same RelayClient session that already auto-launched its
    # sequence-0 game. The received sequence-2 turn must nevertheless get
    # one automatic launch attempt.
    resumed = client_a.tick(GAME_ID)
    assert resumed.operational_state is OperationalState.CIV_RUNNING
    assert len(supervisor_a.launched) == 2
    client_a.close()
    client_b.close()


def test_standard_profile_blocked_start_never_surprise_launches(
    tmp_path: Path,
) -> None:
    storage = FakeStorage()
    clock = FakeClock()
    machine = FakeMachine()
    client_a, client_b, supervisor_a, supervisor_b, identity_a = _two_profile_setup(
        tmp_path, storage, clock, machine, mode_b=TurnHandlingMode.STANDARD
    )

    client_b.tick(GAME_ID)
    assert supervisor_b.launched == []

    # An explicit Start while A's Civ still runs is reported and deferred.
    deferred = client_b.request_start(GAME_ID)
    assert deferred.operational_state is OperationalState.MY_TURN_DOWNLOADED
    assert supervisor_b.launched == []
    status = client_b.process_status(GAME_ID)
    assert status.status is ProcessStatus.WAITING_FOR_EXISTING_CIV
    records_b = client_b.store.load_match_state_or_empty(GAME_ID)
    assert records_b.launch_attempt is None

    # No silently scheduled auto-launch on later ticks — not even after
    # the existing Civ exits.
    client_b.tick(GAME_ID)
    client_b.tick(GAME_ID)
    supervisor_a.mark_exited(identity_a)
    client_b.tick(GAME_ID)
    assert supervisor_b.launched == []

    # A second explicit Start now launches normally.
    started = client_b.request_start(GAME_ID)
    assert started.operational_state is OperationalState.CIV_RUNNING
    assert len(supervisor_b.launched) == 1
    assert client_b.process_status(GAME_ID).status is ProcessStatus.RUNNING
    client_a.close()
    client_b.close()


def test_fully_managed_retries_guard_busy_without_consuming_launch_key(
    tmp_path: Path,
) -> None:
    storage = FakeStorage()
    clock = FakeClock()
    supervisor = FakeProcessSupervisor()
    supervisor.machine.externally_held = True
    client, _config, _exe = _local_client_after_opponent_commit(
        tmp_path, storage, clock, supervisor
    )
    snap = client.tick(GAME_ID)
    assert snap.operational_state is OperationalState.MY_TURN_DOWNLOADED
    assert supervisor.launched == []
    status = client.process_status(GAME_ID)
    assert status.status is ProcessStatus.WAITING_FOR_LAUNCH_GUARD
    assert "another Relay instance" in status.message
    records = client.store.load_match_state_or_empty(GAME_ID)
    assert records.launch_attempt is None
    assert records.process_association is None

    client.tick(GAME_ID)
    assert supervisor.launched == []

    supervisor.machine.externally_held = False
    launched = client.tick(GAME_ID)
    assert launched.operational_state is OperationalState.CIV_RUNNING
    assert len(supervisor.launched) == 1
    client.close()


def test_fully_managed_retries_indeterminate_scan_without_claiming_existing_civ(
    tmp_path: Path,
) -> None:
    storage = FakeStorage()
    clock = FakeClock()
    supervisor = FakeProcessSupervisor()
    supervisor.machine.add_scan_entry(
        ProcessScanEntry(pid=321, executable_path=None, name="Civ4BeyondSword.exe")
    )
    client, _config, _exe = _local_client_after_opponent_commit(
        tmp_path, storage, clock, supervisor
    )
    snap = client.tick(GAME_ID)
    assert snap.operational_state is OperationalState.MY_TURN_DOWNLOADED
    assert supervisor.launched == []
    status = client.process_status(GAME_ID)
    assert status.status is ProcessStatus.LAUNCH_SCAN_INDETERMINATE
    assert "could not be verified" in status.message
    records = client.store.load_match_state_or_empty(GAME_ID)
    assert records.launch_attempt is None
    assert records.process_association is None
    assert supervisor.focus_requests == []
    assert supervisor.close_requests == []
    assert supervisor.terminations == []

    client.tick(GAME_ID)
    assert supervisor.launched == []

    supervisor.machine.clear_extra_scan_entries()
    launched = client.tick(GAME_ID)
    assert launched.operational_state is OperationalState.CIV_RUNNING
    assert len(supervisor.launched) == 1
    client.close()


def test_standard_indeterminate_start_never_surprise_launches(tmp_path: Path) -> None:
    storage = FakeStorage()
    clock = FakeClock()
    supervisor = FakeProcessSupervisor()
    supervisor.machine.add_scan_entry(
        ProcessScanEntry(pid=321, executable_path=None, name="Civ4BeyondSword.exe")
    )
    client, _config, _exe = _local_client_after_opponent_commit(
        tmp_path, storage, clock, supervisor, mode=TurnHandlingMode.STANDARD
    )
    deferred = client.request_start(GAME_ID)
    assert deferred.operational_state is OperationalState.MY_TURN_DOWNLOADED
    assert supervisor.launched == []
    assert (
        client.process_status(GAME_ID).status is ProcessStatus.LAUNCH_SCAN_INDETERMINATE
    )
    assert client.store.load_match_state_or_empty(GAME_ID).launch_attempt is None

    supervisor.machine.clear_extra_scan_entries()
    client.tick(GAME_ID)
    client.tick(GAME_ID)
    assert supervisor.launched == []

    started = client.request_start(GAME_ID)
    assert started.operational_state is OperationalState.CIV_RUNNING
    assert len(supervisor.launched) == 1
    client.close()


def test_focus_verifies_identity_before_acting(tmp_path: Path) -> None:
    storage = FakeStorage()
    clock = FakeClock()
    supervisor = FakeProcessSupervisor()
    client, _config, _exe = _local_client_after_opponent_commit(
        tmp_path, storage, clock, supervisor
    )
    client.tick(GAME_ID)
    identity = _associated_identity(client)

    focused = client.focus_civ(GAME_ID)
    assert focused.outcome is FocusOutcome.FOCUSED
    assert supervisor.focus_requests == [identity]

    supervisor.mark_exited(identity)
    impostor = ProcessIdentity(
        pid=identity.pid,
        process_start_time_utc=identity.process_start_time_utc,
        process_create_time_ns=identity.process_create_time_ns + 1,
        executable_path=identity.executable_path,
    )
    supervisor.spawn_external(impostor)
    mismatch = client.focus_civ(GAME_ID)
    assert mismatch.outcome is FocusOutcome.IDENTITY_MISMATCH
    assert supervisor.focus_requests == [identity]
    client.close()


def _assert_persisted_launch_survives_cleanup_failure(
    tmp_path: Path, operation: str
) -> None:
    storage = FakeStorage()
    clock = FakeClock()
    supervisor = FakeProcessSupervisor()
    supervisor.machine.release_failure_operation = operation
    client, _config, exe = _local_client_after_opponent_commit(
        tmp_path, storage, clock, supervisor
    )
    snap = client.tick(GAME_ID)
    assert snap.operational_state is OperationalState.CIV_RUNNING
    assert len(supervisor.launched) == 1
    records = client.store.load_match_state_or_empty(GAME_ID)
    assert records.process_association is not None
    identity = _associated_identity(client)
    assert identity.executable_path == exe
    assert identity.pid == records.process_association.pid
    assert (
        identity.process_create_time_ns
        == records.process_association.process_create_time_ns
    )
    status = client.process_status(GAME_ID)
    assert status.status is ProcessStatus.RUNNING
    assert status.identity == identity
    assert status.cleanup_warning is not None
    assert operation in status.cleanup_warning
    assert snap.latest_diagnostic is not None
    assert snap.latest_diagnostic.name == "launch_guard_cleanup_failed"
    assert operation in snap.latest_diagnostic.message

    client.tick(GAME_ID)
    client.tick(GAME_ID)
    assert len(supervisor.launched) == 1
    assert client.process_status(GAME_ID).status is ProcessStatus.RUNNING
    later_records = client.store.load_match_state_or_empty(GAME_ID)
    assert later_records.process_association is not None
    later = _associated_identity(client)
    assert later == identity
    client.close()


def test_verified_launch_persists_when_release_mutex_cleanup_fails(
    tmp_path: Path,
) -> None:
    _assert_persisted_launch_survives_cleanup_failure(tmp_path, "ReleaseMutex")


def test_verified_launch_persists_when_close_handle_cleanup_fails(
    tmp_path: Path,
) -> None:
    _assert_persisted_launch_survives_cleanup_failure(tmp_path, "CloseHandle")


def test_deferred_launch_restores_key_when_cleanup_fails(tmp_path: Path) -> None:
    storage = FakeStorage()
    clock = FakeClock()
    supervisor = FakeProcessSupervisor()
    supervisor.machine.release_failure_operation = "CloseHandle"
    supervisor.machine.add_scan_entry(
        ProcessScanEntry(pid=321, executable_path=None, name="Civ4BeyondSword.exe")
    )
    client, _config, _exe = _local_client_after_opponent_commit(
        tmp_path, storage, clock, supervisor
    )
    snap = client.tick(GAME_ID)
    assert snap.operational_state is OperationalState.MY_TURN_DOWNLOADED
    assert supervisor.launched == []
    status = client.process_status(GAME_ID)
    assert status.status is ProcessStatus.LAUNCH_SCAN_INDETERMINATE
    assert status.cleanup_warning is not None
    assert "CloseHandle" in status.cleanup_warning
    records = client.store.load_match_state_or_empty(GAME_ID)
    assert records.launch_attempt is None
    assert records.process_association is None
    assert snap.latest_diagnostic is not None
    assert snap.latest_diagnostic.name == "launch_guard_cleanup_failed"

    supervisor.machine.clear_extra_scan_entries()
    supervisor.machine.release_failure_operation = None
    launched = client.tick(GAME_ID)
    assert launched.operational_state is OperationalState.CIV_RUNNING
    assert len(supervisor.launched) == 1
    records_after = client.store.load_match_state_or_empty(GAME_ID)
    assert records_after.process_association is not None
    assert records_after.launch_attempt is not None
    client.close()


def _opponent_commits_followup_save(
    tmp_path: Path,
    storage: FakeStorage,
    clock: FakeClock,
    *,
    operation_id: str,
    data: bytes,
    filename: str,
) -> None:
    """The opponent plays their downloaded turn and commits the next save."""
    root = tmp_path / "opponent"
    opponent = make_client(root, storage, clock, client_uuid=OPPONENT_UUID)
    config = match_config(root, local_player_id="player_a", pbem_name="pbem-opponent")
    opponent.open_match(config)
    handoff = opponent.execute_handoff(
        GAME_ID,
        operation_id=operation_id,
        outgoing_bytes=data,
        original_filename=filename,
    )
    assert handoff.outcome is HandoffOutcome.COMMITTED
    opponent.close()


def _read_launch_log_lines(client: RelayClient) -> list[str]:
    log = client.store.root / LAUNCH_LOG_FILENAME
    if not log.exists():
        return []
    return log.read_text(encoding="utf-8").splitlines()


def test_next_turn_launches_despite_prior_outgoing_in_pbem_folder(
    tmp_path: Path,
) -> None:
    """Regression (field failure): after a full turn cycle, the previous
    outgoing save still sits in the PBEM folder while the persisted session
    baseline predates it. The next verified turn must auto-launch instead of
    being held in a perpetual STABILIZING detection that silently suppressed
    every launch."""
    storage = FakeStorage()
    clock = FakeClock()
    supervisor = FakeProcessSupervisor()
    client, config, _exe = _local_client_after_opponent_commit(
        tmp_path, storage, clock, supervisor
    )
    assert client.tick(GAME_ID).operational_state is OperationalState.CIV_RUNNING
    assert len(supervisor.launched) == 1

    write_stable_save(
        Path(config.pbem_save_directory),
        SAVE_NAME_B,
        SAVE_B,
        clock,
        client,
        GAME_ID,
    )
    committed = client.tick(
        GAME_ID, auto_handoff_operation_id="d1d1d1d1-d1d1-4d1d-8d1d-d1d1d1d1d1d1"
    )
    assert committed.protocol_sequence == 2
    closed = client.tick(GAME_ID)
    assert closed.operational_state is OperationalState.WAITING_FOR_OTHER_PLAYER

    _opponent_commits_followup_save(
        tmp_path,
        storage,
        clock,
        operation_id="d2d2d2d2-d2d2-4d2d-8d2d-d2d2d2d2d2d2",
        data=SAVE_A + b"-turn-3",
        filename="E2E_PlayerA_Turn2.CivBeyondSwordSave",
    )
    # The already-sent outgoing save is still in the folder and is absent
    # from the persisted (stale) session baseline.
    assert (Path(config.pbem_save_directory) / SAVE_NAME_B).exists()

    snap = client.tick(GAME_ID)
    assert snap.operational_state is OperationalState.CIV_RUNNING
    assert len(supervisor.launched) == 2
    client.close()


def test_launch_diagnostics_log_records_real_attempt_once(tmp_path: Path) -> None:
    storage = FakeStorage()
    clock = FakeClock()
    supervisor = FakeProcessSupervisor()
    client, _config, _exe = _local_client_after_opponent_commit(
        tmp_path, storage, clock, supervisor
    )
    assert client.tick(GAME_ID).operational_state is OperationalState.CIV_RUNNING

    lines = _read_launch_log_lines(client)
    attempts = [line for line in lines if " launch_attempted " in line]
    assert len(attempts) == 1
    assert '"plan_outcome": "ready"' in attempts[0]
    assert '"guarded_outcome": "launched"' in attempts[0]
    assert '"identity_persisted": true' in attempts[0]
    assert "/fxsload=" in attempts[0]

    # Healthy running ticks add nothing: no per-poll spam.
    client.tick(GAME_ID)
    client.tick(GAME_ID)
    assert _read_launch_log_lines(client) == lines
    client.close()


def test_suppressed_launch_logged_once_until_a_real_attempt(tmp_path: Path) -> None:
    storage = FakeStorage()
    clock = FakeClock()
    supervisor = FakeProcessSupervisor()
    supervisor.exit_immediately = True
    exe = _exe_path(tmp_path)
    client, _config = _opener_client(
        tmp_path, storage, clock, supervisor, civ4_executable=exe
    )

    client.tick(GAME_ID)
    assert len(supervisor.launched) == 1
    lines = _read_launch_log_lines(client)
    assert len([line for line in lines if " launch_attempted " in line]) == 1

    # Repeated polls while the launch stays suppressed log exactly one
    # deduplicated suppression entry, not one per interval.
    client.tick(GAME_ID)
    client.tick(GAME_ID)
    client.tick(GAME_ID)
    suppressed = [
        line
        for line in _read_launch_log_lines(client)
        if " auto_launch_suppressed " in line
    ]
    assert len(suppressed) == 1
    assert '"reason": "launch_already_attempted"' in suppressed[0]

    # An explicit user Start is a real attempt and is always logged.
    supervisor.exit_immediately = False
    result = client.request_start(GAME_ID)
    assert result.operational_state is OperationalState.CIV_RUNNING
    attempts = [
        line for line in _read_launch_log_lines(client) if " launch_attempted " in line
    ]
    assert len(attempts) == 2
    client.close()
