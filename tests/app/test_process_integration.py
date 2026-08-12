"""RelayClient process integration over the supervisor port (P7 part B)."""

from __future__ import annotations

import uuid
from pathlib import Path

from civ4_turn_relay.app import ProcessStatus, RelayClient
from civ4_turn_relay.domain import MatchConfig, OperationalState, TurnHandlingMode
from civ4_turn_relay.local import FakeClock, LocalStore
from civ4_turn_relay.process import (
    FakeProcessSupervisor,
    FocusOutcome,
    LaunchPlanOutcome,
    ProbeOutcome,
    ProcessIdentity,
)
from civ4_turn_relay.protocol import HandoffOutcome, InitializeOutcome
from civ4_turn_relay.storage import FakeStorage
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
    allow_force_close: bool = False,
) -> tuple[RelayClient, MatchConfig, str]:
    _opponent_commits_first_save(tmp_path, storage, clock)
    exe = _exe_path(tmp_path)
    root = tmp_path / "local"
    client = _make_process_client(root, storage, clock, supervisor, civ4_executable=exe)
    config = match_config(
        root,
        local_player_id="player_b",
        mode=mode,
        allow_force_close=allow_force_close,
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
        "mod=Mods\\AdvCiv",
        f"/fxsload={records.downloaded_save.local_path}",
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
    assert supervisor.launched[0].argv == (exe, "mod=Mods\\AdvCiv")
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


def test_fully_managed_commit_closes_gracefully(tmp_path: Path) -> None:
    storage = FakeStorage()
    clock = FakeClock()
    supervisor = FakeProcessSupervisor()
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
        GAME_ID, auto_handoff_operation_id="b2b2b2b2-b2b2-4b2b-8b2b-b2b2b2b2b2b2"
    )
    assert snap.protocol_sequence == 2
    assert supervisor.close_requests == [identity]
    assert supervisor.terminations == []

    snap_after = client.tick(GAME_ID)
    assert client.process_status(GAME_ID).status is ProcessStatus.SAFELY_CLOSED
    records = client.store.load_match_state_or_empty(GAME_ID)
    assert records.pending_post_commit_close is None
    assert snap_after.operational_state is OperationalState.WAITING_FOR_OTHER_PLAYER
    assert supervisor.close_requests == [identity]
    client.close()


def test_close_deadline_without_force_close(tmp_path: Path) -> None:
    storage = FakeStorage()
    clock = FakeClock()
    supervisor = FakeProcessSupervisor()
    supervisor.exit_on_close_request = False
    client, config, _exe = _local_client_after_opponent_commit(
        tmp_path, storage, clock, supervisor, allow_force_close=False
    )
    client.tick(GAME_ID)
    write_stable_save(
        Path(config.pbem_save_directory),
        SAVE_NAME_B,
        SAVE_B,
        clock,
        client,
        GAME_ID,
    )
    client.tick(
        GAME_ID, auto_handoff_operation_id="b3b3b3b3-b3b3-4b3b-8b3b-b3b3b3b3b3b3"
    )
    assert len(supervisor.close_requests) == 1
    status = client.process_status(GAME_ID)
    assert status.status is ProcessStatus.CLOSE_REQUESTED
    assert status.close_deadline_remaining_seconds is not None
    assert 0.0 < status.close_deadline_remaining_seconds <= 15.0

    clock.advance(16.0)
    client.tick(GAME_ID)
    status = client.process_status(GAME_ID)
    assert status.status is ProcessStatus.CLOSE_DEADLINE_ELAPSED
    assert status.force_close_allowed is False
    assert supervisor.terminations == []
    assert len(supervisor.close_requests) == 1
    client.close()


def test_force_close_after_deadline_exactly_once(tmp_path: Path) -> None:
    storage = FakeStorage()
    clock = FakeClock()
    supervisor = FakeProcessSupervisor()
    supervisor.exit_on_close_request = False
    client, config, _exe = _local_client_after_opponent_commit(
        tmp_path, storage, clock, supervisor, allow_force_close=True
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
        GAME_ID, auto_handoff_operation_id="b4b4b4b4-b4b4-4b4b-8b4b-b4b4b4b4b4b4"
    )
    assert supervisor.close_requests == [identity]
    # Never before the deadline elapses.
    client.tick(GAME_ID)
    assert supervisor.terminations == []

    clock.advance(16.0)
    client.tick(GAME_ID)
    assert supervisor.terminations == [identity]
    assert client.process_status(GAME_ID).status is ProcessStatus.SAFELY_CLOSED
    records = client.store.load_match_state_or_empty(GAME_ID)
    assert records.pending_post_commit_close is None

    # Never twice.
    client.tick(GAME_ID)
    clock.advance(20.0)
    client.tick(GAME_ID)
    assert supervisor.terminations == [identity]
    assert supervisor.close_requests == [identity]
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
    supervisor.exit_on_close_request = False
    client, config, _exe = _local_client_after_opponent_commit(
        tmp_path, storage, clock, supervisor, allow_force_close=True
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
    assert supervisor.close_requests == [identity]

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

    client.tick(GAME_ID)
    clock.advance(16.0)
    client.tick(GAME_ID)
    clock.advance(20.0)
    client.tick(GAME_ID)
    # The stale entitlement never escalates to the reused pid.
    assert supervisor.close_requests == [identity]
    assert supervisor.terminations == []
    assert supervisor.probe(reused).outcome is ProbeOutcome.RUNNING_MATCH
    client.close()


def test_restart_rearms_close_deadline_once(tmp_path: Path) -> None:
    storage = FakeStorage()
    clock = FakeClock()
    supervisor = FakeProcessSupervisor()
    supervisor.exit_on_close_request = False
    client, config, exe = _local_client_after_opponent_commit(
        tmp_path, storage, clock, supervisor, allow_force_close=True
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
        GAME_ID, auto_handoff_operation_id="b5b5b5b5-b5b5-4b5b-8b5b-b5b5b5b5b5b5"
    )
    assert supervisor.close_requests == [identity]
    client.close()

    restarted = _make_process_client(
        tmp_path / "local", storage, clock, supervisor, civ4_executable=exe
    )
    restarted.open_match(config)
    restarted.tick(GAME_ID)
    # No second graceful request; deadline re-armed once from current clock.
    assert supervisor.close_requests == [identity]
    assert supervisor.terminations == []
    status = restarted.process_status(GAME_ID)
    assert status.status is ProcessStatus.CLOSE_REQUESTED

    clock.advance(16.0)
    restarted.tick(GAME_ID)
    assert supervisor.terminations == [identity]
    assert restarted.process_status(GAME_ID).status is ProcessStatus.SAFELY_CLOSED
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
    assert restarted.process_status(GAME_ID).status is ProcessStatus.READY
    restarted.tick(GAME_ID)
    assert len(supervisor.launched) == 1
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
    client.tick(GAME_ID)
    client.tick(GAME_ID)
    assert len(supervisor.launched) == 1
    client.close()


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
