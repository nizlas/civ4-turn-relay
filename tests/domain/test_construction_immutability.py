"""Regression: direct construction is deeply immutable and fully typed."""

from __future__ import annotations

from typing import Any, cast

import pytest

from civ4_turn_relay.domain import (
    AcceptedSave,
    DomainValidationError,
    GlobalConfig,
    Manifest,
    MatchConfig,
    Player,
    ProtocolMetadata,
    SaveMatchingRules,
)

HASH_1 = "a1b2c3d4e5f6789012345678abcdef9012345678abcdef9012345678abcdef90"
OP_ID = "11111111-2222-3333-4444-555555555555"


def _players() -> tuple[Player, Player]:
    return (
        Player(id="player_a", display_name="Player A"),
        Player(id="player_b", display_name="Player B"),
    )


def _accepted_save() -> AcceptedSave:
    return AcceptedSave(
        sha256=HASH_1,
        size_bytes=1234567,
        remote_path="saves/000001_a1b2c3d4e5f6.CivBeyondSwordSave",
        original_filename="ExampleMatch_PlayerA.CivBeyondSwordSave",
        accepted_at="2026-08-10T19:43:00Z",
    )


def _seq0_manifest(**overrides: Any) -> Manifest:
    values: dict[str, Any] = {
        "schema_version": 1,
        "game_id": "example-match",
        "display_name": "Example Match",
        "players": _players(),
        "protocol_sequence": 0,
        "current_player_id": "player_a",
        "last_sender_id": None,
        "accepted_save": None,
        "accepted_save_hashes": (),
        "previous_manifest_ref": None,
        "protocol": ProtocolMetadata(min_client_protocol=1, last_operation_id=None),
    }
    values.update(overrides)
    return Manifest(**values)


def _seq1_manifest(**overrides: Any) -> Manifest:
    values: dict[str, Any] = {
        "schema_version": 1,
        "game_id": "example-match",
        "display_name": "Example Match",
        "players": _players(),
        "protocol_sequence": 1,
        "current_player_id": "player_b",
        "last_sender_id": "player_a",
        "accepted_save": _accepted_save(),
        "accepted_save_hashes": (HASH_1,),
        "previous_manifest_ref": "history/manifest-000000-0123456789ab.json",
        "protocol": ProtocolMetadata(min_client_protocol=1, last_operation_id=OP_ID),
    }
    values.update(overrides)
    return Manifest(**values)


def _match_config(**overrides: Any) -> MatchConfig:
    values: dict[str, Any] = {
        "game_id": "example-match",
        "display_name": "Example Match",
        "players": _players(),
        "local_player_id": "player_a",
        "launch_profile": "default",
        "mod_name": "AdvCiv",
        "pbem_save_directory": "C:\\Placeholder\\Saves\\pbem",
        "save_matching": SaveMatchingRules(filename_glob="*.CivBeyondSwordSave"),
        "auto_launch": False,
    }
    values.update(overrides)
    return MatchConfig(**values)


class TestDeepImmutability:
    def test_manifest_canonicalizes_player_list(self) -> None:
        mutable_players = list(_players())
        manifest = _seq0_manifest(players=mutable_players)
        assert isinstance(manifest.players, tuple)
        # Caller-owned list must not remain as the model's storage.
        assert id(manifest.players) != id(mutable_players)
        mutable_players.append(Player(id="player_c", display_name="C"))
        assert len(manifest.players) == 2
        assert [player.id for player in manifest.players] == [
            "player_a",
            "player_b",
        ]

    def test_manifest_canonicalizes_hash_list(self) -> None:
        mutable_hashes = [HASH_1]
        manifest = _seq1_manifest(accepted_save_hashes=mutable_hashes)
        assert isinstance(manifest.accepted_save_hashes, tuple)
        assert id(manifest.accepted_save_hashes) != id(mutable_hashes)
        mutable_hashes.clear()
        assert manifest.accepted_save_hashes == (HASH_1,)

    def test_match_config_canonicalizes_player_list(self) -> None:
        mutable_players = list(_players())
        config = _match_config(players=mutable_players)
        assert isinstance(config.players, tuple)
        assert id(config.players) != id(mutable_players)
        mutable_players.pop()
        assert len(config.players) == 2

    def test_validated_collections_cannot_be_reassigned(self) -> None:
        manifest = _seq0_manifest()
        config = _match_config()
        with pytest.raises(AttributeError):
            manifest.players = ()  # type: ignore[misc]
        with pytest.raises(AttributeError):
            manifest.accepted_save_hashes = (HASH_1,)  # type: ignore[misc]
        with pytest.raises(AttributeError):
            config.players = ()  # type: ignore[misc]
        with pytest.raises(AttributeError):
            config.save_matching = SaveMatchingRules(filename_glob="*.sav")  # type: ignore[misc]

    def test_direct_construction_matches_parsed_invariants(self) -> None:
        constructed = _seq1_manifest()
        parsed = Manifest.from_mapping(constructed.to_mapping())
        assert constructed == parsed
        assert constructed.to_json_bytes() == parsed.to_json_bytes()
        match_constructed = _match_config()
        match_parsed = MatchConfig.from_mapping(match_constructed.to_mapping())
        assert match_constructed == match_parsed


class TestNestedRuntimeTypes:
    def test_manifest_rejects_non_player_entries(self) -> None:
        with pytest.raises(DomainValidationError) as exc_info:
            _seq0_manifest(players=[{"id": "player_a", "display_name": "A"}])
        assert exc_info.value.field_path == "players[0]"

    def test_manifest_rejects_non_sequence_players(self) -> None:
        with pytest.raises(DomainValidationError) as exc_info:
            _seq0_manifest(players="player_a")
        assert exc_info.value.field_path == "players"

    def test_manifest_rejects_wrong_accepted_save_type(self) -> None:
        with pytest.raises(DomainValidationError) as exc_info:
            _seq1_manifest(accepted_save={"sha256": HASH_1})
        assert exc_info.value.field_path == "accepted_save"

    def test_manifest_rejects_wrong_protocol_type(self) -> None:
        with pytest.raises(DomainValidationError) as exc_info:
            _seq0_manifest(
                protocol={"min_client_protocol": 1, "last_operation_id": None}
            )
        assert exc_info.value.field_path == "protocol"

    def test_manifest_rejects_non_string_hashes(self) -> None:
        with pytest.raises(DomainValidationError) as exc_info:
            _seq1_manifest(accepted_save_hashes=[123])
        assert exc_info.value.field_path == "accepted_save_hashes[0]"

    def test_manifest_rejects_wrong_optional_string_types(self) -> None:
        with pytest.raises(DomainValidationError) as exc_info:
            _seq0_manifest(last_sender_id=123)
        assert exc_info.value.field_path == "last_sender_id"
        with pytest.raises(DomainValidationError) as exc_info:
            _seq0_manifest(previous_manifest_ref=object())
        assert exc_info.value.field_path == "previous_manifest_ref"

    def test_match_config_rejects_none_save_matching(self) -> None:
        with pytest.raises(DomainValidationError) as exc_info:
            _match_config(save_matching=None)
        assert exc_info.value.field_path == "save_matching"
        assert not isinstance(exc_info.value, AttributeError)

    def test_match_config_rejects_wrong_save_matching_type(self) -> None:
        with pytest.raises(DomainValidationError) as exc_info:
            _match_config(save_matching={"filename_glob": "*.sav"})
        assert exc_info.value.field_path == "save_matching"

    def test_match_config_rejects_non_player_entries(self) -> None:
        with pytest.raises(DomainValidationError) as exc_info:
            _match_config(players=["player_a"])
        assert exc_info.value.field_path == "players[0]"

    def test_match_config_rejects_wrong_optional_string_types(self) -> None:
        with pytest.raises(DomainValidationError) as exc_info:
            _match_config(launch_profile=1)
        assert exc_info.value.field_path == "launch_profile"
        with pytest.raises(DomainValidationError) as exc_info:
            _match_config(mod_name=True)
        assert exc_info.value.field_path == "mod_name"

    def test_protocol_metadata_rejects_wrong_operation_id_type(self) -> None:
        with pytest.raises(DomainValidationError) as exc_info:
            ProtocolMetadata(min_client_protocol=1, last_operation_id=cast(Any, 123))
        assert exc_info.value.field_path == "last_operation_id"

    def test_global_config_rejects_wrong_optional_secret_types(self) -> None:
        with pytest.raises(DomainValidationError) as exc_info:
            GlobalConfig(
                sftp_host="sftp.example.invalid",
                sftp_port=22,
                sftp_username="placeholder-user",
                sftp_remote_root="/placeholder",
                sftp_password=cast(Any, 12345),
            )
        assert exc_info.value.field_path == "sftp_password"
        with pytest.raises(DomainValidationError) as exc_info:
            GlobalConfig(
                sftp_host="sftp.example.invalid",
                sftp_port=22,
                sftp_username="placeholder-user",
                sftp_remote_root="/placeholder",
                sftp_private_key_path=cast(Any, ["not", "a", "path"]),
            )
        assert exc_info.value.field_path == "sftp_private_key_path"

    def test_global_config_rejects_wrong_optional_executable_type(self) -> None:
        with pytest.raises(DomainValidationError) as exc_info:
            GlobalConfig(
                sftp_host="sftp.example.invalid",
                sftp_port=22,
                sftp_username="placeholder-user",
                sftp_remote_root="/placeholder",
                civ4_executable=cast(Any, 7),
            )
        assert exc_info.value.field_path == "civ4_executable"
