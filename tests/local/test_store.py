"""LocalStore layout, identity race, atomic writes, and typed failures."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

from civ4_turn_relay.domain import (
    DomainValidationError,
    MatchConfig,
    OperationalState,
    Player,
    SaveMatchingRules,
    TurnHandlingMode,
)
from civ4_turn_relay.local import (
    LocalStore,
    LocalStoreCorruptError,
    LocalStoreIOError,
    LocalStoreMissingError,
    LocalStoreUnsupportedSchemaError,
    MatchLocalRecords,
)
from tests.protocol.helpers import HASH_1

FIXED_UUID = uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
OTHER_UUID = uuid.UUID("11111111-2222-3333-4444-555555555555")


def _config(game_id: str = "example-match") -> MatchConfig:
    return MatchConfig(
        game_id=game_id,
        display_name="Example Match",
        players=(Player(id="player_a", display_name="Player A"),),
        local_player_id="player_a",
        launch_profile=None,
        mod_name=None,
        pbem_save_directory=r"C:\Placeholder\Saves\pbem",
        save_matching=SaveMatchingRules(filename_glob="*.CivBeyondSwordSave"),
        turn_handling_mode=TurnHandlingMode.STANDARD,
    )


def test_layout_paths_under_root(tmp_path: Path) -> None:
    store = LocalStore(tmp_path)
    assert store.installation_path() == (tmp_path / "installation.json").resolve()
    assert (
        store.match_config_path("example-match")
        == (tmp_path / "matches" / "example-match" / "config.json").resolve()
    )
    assert (
        store.match_state_path("example-match")
        == (tmp_path / "matches" / "example-match" / "state.json").resolve()
    )


def test_path_containment_rejects_traversal(tmp_path: Path) -> None:
    store = LocalStore(tmp_path)
    with pytest.raises(DomainValidationError):
        store.match_config_path("../escape")


def test_stable_client_id_across_restarts(tmp_path: Path) -> None:
    first = LocalStore(tmp_path).get_or_create_installation_identity(
        uuid_factory=lambda: FIXED_UUID
    )
    second = LocalStore(tmp_path).get_or_create_installation_identity(
        uuid_factory=lambda: OTHER_UUID
    )
    assert first == str(FIXED_UUID)
    assert second == first
    assert (tmp_path / "installation.json").is_file()


def test_competing_first_identity_initialization(tmp_path: Path) -> None:
    store_a = LocalStore(tmp_path)
    store_b = LocalStore(tmp_path)
    created_a = store_a.get_or_create_installation_identity(
        uuid_factory=lambda: FIXED_UUID
    )
    created_b = store_b.get_or_create_installation_identity(
        uuid_factory=lambda: OTHER_UUID
    )
    assert created_a == created_b == str(FIXED_UUID)


def test_corrupt_installation_never_treated_as_identity(tmp_path: Path) -> None:
    path = tmp_path / "installation.json"
    path.write_bytes(b"{not-json")
    store = LocalStore(tmp_path)
    with pytest.raises(LocalStoreCorruptError):
        store.get_or_create_installation_identity(uuid_factory=lambda: FIXED_UUID)


def test_unsupported_installation_schema(tmp_path: Path) -> None:
    path = tmp_path / "installation.json"
    path.write_text(
        '{\n  "client_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",\n'
        '  "schema_version": 99\n}\n',
        encoding="utf-8",
        newline="\n",
    )
    with pytest.raises(LocalStoreUnsupportedSchemaError):
        LocalStore(tmp_path).get_or_create_installation_identity()


def test_match_config_round_trip(tmp_path: Path) -> None:
    store = LocalStore(tmp_path)
    config = _config()
    store.write_match_config(config)
    assert store.load_match_config("example-match") == config
    assert (tmp_path / "matches" / "example-match" / "config.json").is_file()


def test_missing_config_and_state_are_typed(tmp_path: Path) -> None:
    store = LocalStore(tmp_path)
    with pytest.raises(LocalStoreMissingError):
        store.load_match_config("example-match")
    with pytest.raises(LocalStoreMissingError):
        store.load_match_state("example-match")


def test_corrupt_and_unsupported_state(tmp_path: Path) -> None:
    store = LocalStore(tmp_path)
    path = store.match_state_path("example-match")
    path.parent.mkdir(parents=True)
    path.write_bytes(b"{bad")
    with pytest.raises(LocalStoreCorruptError):
        store.load_match_state("example-match")
    path.write_text(
        '{\n  "game_id": "example-match",\n  "schema_version": 9\n}\n',
        encoding="utf-8",
        newline="\n",
    )
    with pytest.raises(LocalStoreUnsupportedSchemaError):
        store.load_match_state("example-match")


def test_atomic_state_replacement(tmp_path: Path) -> None:
    store = LocalStore(tmp_path)
    first = MatchLocalRecords(game_id="example-match", retry_count=1)
    second = MatchLocalRecords(
        game_id="example-match",
        retry_count=2,
        last_operational_state=OperationalState.UPLOADING,
    )
    store.write_match_state(first)
    store.write_match_state(second)
    assert store.load_match_state("example-match") == second
    leftovers = list((tmp_path / "matches" / "example-match").glob(".state.json.*.tmp"))
    assert leftovers == []


def test_replace_failure_preserves_previous_state(tmp_path: Path) -> None:
    calls = {"n": 0}

    def flaky_replace(src: str, dst: str) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            os.replace(src, dst)
            return
        raise OSError("simulated replace failure")

    store = LocalStore(tmp_path, replace_fn=flaky_replace)
    good = MatchLocalRecords(game_id="example-match", retry_count=3)
    store.write_match_state(good)
    with pytest.raises(LocalStoreIOError):
        store.write_match_state(
            MatchLocalRecords(game_id="example-match", retry_count=9)
        )
    assert store.load_match_state("example-match") == good


def test_fsync_failure_preserves_previous_state(tmp_path: Path) -> None:
    calls = {"n": 0}

    def flaky_fsync(fd: int) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            os.fsync(fd)
            return
        raise OSError("simulated fsync failure")

    store = LocalStore(tmp_path, fsync_fn=flaky_fsync)
    good = MatchLocalRecords(game_id="example-match", retry_count=4)
    store.write_match_state(good)
    with pytest.raises(LocalStoreIOError):
        store.write_match_state(
            MatchLocalRecords(game_id="example-match", retry_count=8)
        )
    assert store.load_match_state("example-match") == good


def test_state_update_preserves_unrelated_fields(tmp_path: Path) -> None:
    store = LocalStore(tmp_path)
    store.write_match_state(
        MatchLocalRecords(
            game_id="example-match",
            retry_count=2,
            last_transition_reason="baseline_recorded",
            processed_outgoing_hashes=(HASH_1,),
        )
    )

    def bump(records: MatchLocalRecords) -> MatchLocalRecords:
        from dataclasses import replace

        return replace(records, retry_count=records.retry_count + 1)

    updated = store.update_match_state("example-match", bump)
    assert updated.retry_count == 3
    assert updated.last_transition_reason == "baseline_recorded"
    assert updated.processed_outgoing_hashes == (HASH_1,)
