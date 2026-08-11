"""Shared helpers for FakeStorage two-client e2e tests."""

from __future__ import annotations

import uuid
from pathlib import Path

from civ4_turn_relay.app import RelayClient
from civ4_turn_relay.domain import (
    MatchConfig,
    Player,
    SaveMatchingRules,
    TurnHandlingMode,
)
from civ4_turn_relay.local import FakeClock, LocalStore, ProcessObservation
from civ4_turn_relay.storage import FakeStorage

GAME_ID = "e2e-match-01"
GAME_ID_B = "e2e-match-02"
NOW_UTC = "2026-08-11T12:00:00Z"
SAVE_A = b"synthetic-save-player-a-v1"
SAVE_B = b"synthetic-save-player-b-v1"
SAVE_NAME_A = "E2E_PlayerA.CivBeyondSwordSave"
SAVE_NAME_B = "E2E_PlayerB.CivBeyondSwordSave"
GLOB = "*.CivBeyondSwordSave"
EXE = r"C:\Placeholder\Civ4BeyondSword.exe"


def players() -> tuple[Player, ...]:
    return (
        Player(id="player_a", display_name="Player A"),
        Player(id="player_b", display_name="Player B"),
    )


def players_with_ai_placeholder() -> tuple[Player, ...]:
    """Human order only — AI civs are never listed as PBEM humans."""
    return players()


def match_config(
    tmp_path: Path,
    *,
    game_id: str = GAME_ID,
    local_player_id: str = "player_a",
    mode: TurnHandlingMode = TurnHandlingMode.STANDARD,
    allow_force_close: bool = False,
    pbem_name: str = "pbem",
) -> MatchConfig:
    pbem = tmp_path / pbem_name
    pbem.mkdir(parents=True, exist_ok=True)
    return MatchConfig(
        game_id=game_id,
        display_name="E2E Match",
        players=players(),
        local_player_id=local_player_id,
        launch_profile="default",
        mod_name="AdvCiv",
        pbem_save_directory=str(pbem.resolve()),
        save_matching=SaveMatchingRules(filename_glob=GLOB),
        turn_handling_mode=mode,
        allow_force_close_after_commit=allow_force_close,
    )


def make_client(
    root: Path,
    storage: FakeStorage,
    clock: FakeClock,
    *,
    client_uuid: uuid.UUID | None = None,
) -> RelayClient:
    store = LocalStore(root)
    fixed = client_uuid or uuid.UUID("11111111-1111-4111-8111-111111111111")
    store.get_or_create_installation_identity(uuid_factory=lambda: fixed)
    return RelayClient(
        store=store,
        storage=storage,
        clock=clock,
        poll_interval_seconds=0.1,
        now_utc_fn=lambda: NOW_UTC,
        operation_id_factory=lambda: str(uuid.uuid4()),
        owns_storage=False,
        auto_execute_managed_handoff=True,
        enable_monitoring=True,
    )


def write_stable_save(
    pbem: Path,
    name: str,
    data: bytes,
    clock: FakeClock,
    client: RelayClient,
    game_id: str,
) -> None:
    path = pbem / name
    path.write_bytes(data)
    client.observe_candidates(game_id)
    clock.advance(1.0)
    client.observe_candidates(game_id)
    clock.advance(1.0)
    client.observe_candidates(game_id)


def running_process(pid: int = 4242) -> ProcessObservation:
    return ProcessObservation(
        pid=pid,
        process_start_time_utc=NOW_UTC,
        executable_path=EXE,
        running=True,
    )


def stopped_process(pid: int = 4242) -> ProcessObservation:
    return ProcessObservation(
        pid=pid,
        process_start_time_utc=NOW_UTC,
        executable_path=EXE,
        running=False,
    )
