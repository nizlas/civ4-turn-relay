"""Tests for global vs per-match configuration models.

All hosts, usernames, passwords, and paths are synthetic placeholders.
"""

import dataclasses
from typing import Any

import pytest

from civ4_turn_relay.domain import (
    DEFAULT_LOG_LEVEL,
    DEFAULT_POLL_INTERVAL_SECONDS,
    REDACTED,
    DomainValidationError,
    GlobalConfig,
    MatchConfig,
    Player,
    SaveMatchingRules,
    TurnHandlingMode,
    global_config_from_env_mapping,
    redact_known_secrets,
)

FAKE_PASSWORD = "placeholder-not-a-real-password"
FAKE_PRIVATE_KEY_PATH = "C:\\Placeholder\\Keys\\id_ed25519_placeholder"


def env_mapping() -> dict[str, str]:
    return {
        "CIV4_RELAY_LOG_LEVEL": "DEBUG",
        "CIV4_RELAY_POLL_INTERVAL_SECONDS": "5",
        "CIV4_RELAY_CIV4_EXECUTABLE": "C:\\Placeholder\\Civ4BeyondSword.exe",
        "CIV4_RELAY_SFTP_HOST": "sftp.example.invalid",
        "CIV4_RELAY_SFTP_PORT": "2222",
        "CIV4_RELAY_SFTP_USERNAME": "placeholder-user",
        "CIV4_RELAY_SFTP_REMOTE_ROOT": "/placeholder/civ4-relay",
        "CIV4_RELAY_SFTP_PRIVATE_KEY_PATH": "",
        "CIV4_RELAY_SFTP_PASSWORD": FAKE_PASSWORD,
    }


def env_mapping_with_key() -> dict[str, str]:
    return env_mapping() | {
        "CIV4_RELAY_SFTP_PRIVATE_KEY_PATH": FAKE_PRIVATE_KEY_PATH,
    }


def match_mapping() -> dict[str, Any]:
    return {
        "allow_force_close_after_commit": True,
        "display_name": "Example Match",
        "game_id": "example-match",
        "launch_profile": "default",
        "local_player_id": "player_a",
        "mod_name": "AdvCiv",
        "pbem_save_directory": "C:\\Placeholder\\Saves\\pbem",
        "players": [
            {"display_name": "Player A", "id": "player_a"},
            {"display_name": "Player B", "id": "player_b"},
        ],
        "save_matching": {"filename_glob": "*.CivBeyondSwordSave"},
        "turn_handling_mode": "fully_managed",
    }


class TestGlobalConfig:
    def test_full_env_parse(self) -> None:
        config = global_config_from_env_mapping(env_mapping())
        assert config.log_level == "DEBUG"
        assert config.poll_interval_seconds == 5
        assert config.civ4_executable == "C:\\Placeholder\\Civ4BeyondSword.exe"
        assert config.sftp_host == "sftp.example.invalid"
        assert config.sftp_port == 2222
        assert config.sftp_username == "placeholder-user"
        assert config.sftp_remote_root == "/placeholder/civ4-relay"
        assert config.sftp_private_key_path is None  # empty means absent
        assert config.sftp_password == FAKE_PASSWORD

    def test_defaults_applied_when_absent(self) -> None:
        env = env_mapping()
        del env["CIV4_RELAY_LOG_LEVEL"]
        del env["CIV4_RELAY_POLL_INTERVAL_SECONDS"]
        del env["CIV4_RELAY_CIV4_EXECUTABLE"]
        del env["CIV4_RELAY_SFTP_PASSWORD"]
        config = global_config_from_env_mapping(env)
        assert config.log_level == DEFAULT_LOG_LEVEL
        assert config.poll_interval_seconds == DEFAULT_POLL_INTERVAL_SECONDS
        assert config.civ4_executable is None
        assert config.sftp_password is None

    def test_unrelated_env_keys_ignored(self) -> None:
        env = env_mapping() | {"PATH": "/usr/bin", "HOME": "/home/placeholder"}
        assert global_config_from_env_mapping(env).sftp_port == 2222

    def test_unknown_prefixed_key_rejected(self) -> None:
        env = env_mapping() | {"CIV4_RELAY_SURPRISE": "x"}
        with pytest.raises(DomainValidationError) as exc_info:
            global_config_from_env_mapping(env)
        assert exc_info.value.field_path == "CIV4_RELAY_SURPRISE"

    @pytest.mark.parametrize(
        "missing",
        [
            "CIV4_RELAY_SFTP_HOST",
            "CIV4_RELAY_SFTP_PORT",
            "CIV4_RELAY_SFTP_USERNAME",
            "CIV4_RELAY_SFTP_REMOTE_ROOT",
        ],
    )
    def test_missing_required_value(self, missing: str) -> None:
        env = env_mapping()
        del env[missing]
        with pytest.raises(DomainValidationError) as exc_info:
            global_config_from_env_mapping(env)
        assert exc_info.value.field_path == missing

    @pytest.mark.parametrize("port", ["0", "65536", "70000", "-1", "abc", "22.5"])
    def test_invalid_port(self, port: str) -> None:
        env = env_mapping() | {"CIV4_RELAY_SFTP_PORT": port}
        with pytest.raises(DomainValidationError):
            global_config_from_env_mapping(env)

    @pytest.mark.parametrize("interval", ["0", "-5", "abc", "1.5", ""])
    def test_invalid_poll_interval(self, interval: str) -> None:
        env = env_mapping() | {"CIV4_RELAY_POLL_INTERVAL_SECONDS": interval}
        if interval == "":
            # Empty counts as absent and takes the default instead.
            config = global_config_from_env_mapping(env)
            assert config.poll_interval_seconds == DEFAULT_POLL_INTERVAL_SECONDS
            return
        with pytest.raises(DomainValidationError):
            global_config_from_env_mapping(env)

    def test_invalid_log_level(self) -> None:
        env = env_mapping() | {"CIV4_RELAY_LOG_LEVEL": "LOUD"}
        with pytest.raises(DomainValidationError) as exc_info:
            global_config_from_env_mapping(env)
        assert exc_info.value.field_path == "log_level"

    def test_secrets_absent_from_repr_and_str(self) -> None:
        config = global_config_from_env_mapping(env_mapping_with_key())
        rendered = repr(config)
        as_text = str(config)
        assert FAKE_PASSWORD not in rendered
        assert FAKE_PASSWORD not in as_text
        assert FAKE_PRIVATE_KEY_PATH not in rendered
        assert FAKE_PRIVATE_KEY_PATH not in as_text

    def test_secrets_absent_from_validation_errors(self) -> None:
        env = env_mapping_with_key() | {"CIV4_RELAY_SFTP_PORT": "not-a-port"}
        with pytest.raises(DomainValidationError) as exc_info:
            global_config_from_env_mapping(env)
        message = str(exc_info.value)
        assert FAKE_PASSWORD not in message
        assert FAKE_PRIVATE_KEY_PATH not in message

    def test_redacted_diagnostics(self) -> None:
        config = global_config_from_env_mapping(env_mapping_with_key())
        redacted = config.to_redacted_mapping()
        assert redacted["sftp_password"] == REDACTED
        assert redacted["sftp_private_key_path"] == REDACTED
        redacted_text = str(redacted)
        assert FAKE_PASSWORD not in redacted_text
        assert FAKE_PRIVATE_KEY_PATH not in redacted_text
        assert redacted["sftp_host"] == "sftp.example.invalid"

    def test_redacted_diagnostics_show_absent_secrets_as_none(self) -> None:
        env = env_mapping()
        del env["CIV4_RELAY_SFTP_PASSWORD"]
        redacted = global_config_from_env_mapping(env).to_redacted_mapping()
        assert redacted["sftp_password"] is None
        assert redacted["sftp_private_key_path"] is None

    def test_secret_values_include_password_and_private_key_path(self) -> None:
        config = global_config_from_env_mapping(env_mapping_with_key())
        assert config.secret_values() == (FAKE_PASSWORD, FAKE_PRIVATE_KEY_PATH)

    def test_known_secret_text_redaction_covers_password_and_key_path(self) -> None:
        config = global_config_from_env_mapping(env_mapping_with_key())
        diagnostic = (
            f"auth failed password={FAKE_PASSWORD} "
            f"key={FAKE_PRIVATE_KEY_PATH} host={config.sftp_host}"
        )
        redacted = redact_known_secrets(diagnostic, config.secret_values())
        assert FAKE_PASSWORD not in redacted
        assert FAKE_PRIVATE_KEY_PATH not in redacted
        assert REDACTED in redacted
        assert config.sftp_host in redacted

    def test_direct_construction_accepts_valid_optional_secrets(self) -> None:
        config = GlobalConfig(
            sftp_host="sftp.example.invalid",
            sftp_port=22,
            sftp_username="placeholder-user",
            sftp_remote_root="/placeholder",
            sftp_private_key_path=FAKE_PRIVATE_KEY_PATH,
            sftp_password=FAKE_PASSWORD,
        )
        assert config.sftp_private_key_path == FAKE_PRIVATE_KEY_PATH
        assert config.sftp_password == FAKE_PASSWORD
        assert FAKE_PRIVATE_KEY_PATH not in repr(config)


class TestMatchConfig:
    def test_full_parse(self) -> None:
        config = MatchConfig.from_mapping(match_mapping())
        assert config.game_id == "example-match"
        assert config.local_player_id == "player_a"
        assert config.turn_handling_mode is TurnHandlingMode.FULLY_MANAGED
        assert config.allow_force_close_after_commit is True
        assert config.save_matching.filename_glob == "*.CivBeyondSwordSave"
        assert [player.id for player in config.players] == [
            "player_a",
            "player_b",
        ]

    def test_turn_handling_defaults(self) -> None:
        mapping = match_mapping()
        del mapping["turn_handling_mode"]
        del mapping["allow_force_close_after_commit"]
        parsed = MatchConfig.from_mapping(mapping)
        assert parsed.turn_handling_mode is TurnHandlingMode.STANDARD
        assert parsed.allow_force_close_after_commit is False
        config = MatchConfig(
            game_id="example-match",
            display_name="Example Match",
            players=(Player(id="player_a", display_name="Player A"),),
            local_player_id="player_a",
            launch_profile=None,
            mod_name=None,
            pbem_save_directory="C:\\Placeholder\\Saves\\pbem",
            save_matching=SaveMatchingRules(filename_glob="*.sav"),
        )
        assert config.turn_handling_mode is TurnHandlingMode.STANDARD
        assert config.allow_force_close_after_commit is False

    def test_standard_canonicalizes_force_close_to_false(self) -> None:
        mapping = match_mapping()
        mapping["turn_handling_mode"] = "standard"
        mapping["allow_force_close_after_commit"] = True
        parsed = MatchConfig.from_mapping(mapping)
        assert parsed.turn_handling_mode is TurnHandlingMode.STANDARD
        assert parsed.allow_force_close_after_commit is False
        assert parsed.to_mapping()["allow_force_close_after_commit"] is False
        constructed = MatchConfig(
            game_id="example-match",
            display_name="Example Match",
            players=(Player(id="player_a", display_name="Player A"),),
            local_player_id="player_a",
            launch_profile=None,
            mod_name=None,
            pbem_save_directory="C:\\Placeholder\\Saves\\pbem",
            save_matching=SaveMatchingRules(filename_glob="*.sav"),
            turn_handling_mode=TurnHandlingMode.STANDARD,
            allow_force_close_after_commit=True,
        )
        assert constructed.allow_force_close_after_commit is False

    def test_fully_managed_preserves_force_close_true(self) -> None:
        config = MatchConfig.from_mapping(match_mapping())
        assert config.turn_handling_mode is TurnHandlingMode.FULLY_MANAGED
        assert config.allow_force_close_after_commit is True

    def test_obsolete_auto_launch_rejected(self) -> None:
        mapping = match_mapping() | {"auto_launch": True}
        with pytest.raises(DomainValidationError) as exc_info:
            MatchConfig.from_mapping(mapping)
        assert exc_info.value.field_path == "auto_launch"

    def test_json_round_trip_is_deterministic(self) -> None:
        config = MatchConfig.from_mapping(match_mapping())
        data = config.to_json_bytes()
        assert data == config.to_json_bytes()
        assert MatchConfig.from_json_bytes(data) == config
        assert data.endswith(b"\n") and not data.endswith(b"\n\n")

    @pytest.mark.parametrize(
        ("expected_path", "overrides"),
        [
            ("game_id", {"game_id": "Bad Game"}),
            ("display_name", {"display_name": ""}),
            ("players", {"players": []}),
            (
                "players[1].id",
                {
                    "players": [
                        {"display_name": "A", "id": "player_a"},
                        {"display_name": "Dup", "id": "player_a"},
                    ]
                },
            ),
            ("local_player_id", {"local_player_id": "player_c"}),
            ("turn_handling_mode", {"turn_handling_mode": "auto_launch"}),
            ("turn_handling_mode", {"turn_handling_mode": 1}),
            (
                "allow_force_close_after_commit",
                {"allow_force_close_after_commit": 1},
            ),
            (
                "allow_force_close_after_commit",
                {"allow_force_close_after_commit": "true"},
            ),
            ("pbem_save_directory", {"pbem_save_directory": "relative\\dir"}),
            ("save_matching", {"save_matching": "*.sav"}),
            (
                "save_matching.filename_glob",
                {"save_matching": {"filename_glob": "dir/*.sav"}},
            ),
            ("surprise", {"surprise": True}),
            ("launch_profile", {"launch_profile": ""}),
            ("mod_name", {"mod_name": ""}),
        ],
    )
    def test_invalid_match_config(
        self, expected_path: str, overrides: dict[str, Any]
    ) -> None:
        mapping = match_mapping() | overrides
        with pytest.raises(DomainValidationError) as exc_info:
            MatchConfig.from_mapping(mapping)
        assert exc_info.value.field_path == expected_path

    def test_missing_required_field(self) -> None:
        mapping = match_mapping()
        del mapping["pbem_save_directory"]
        with pytest.raises(DomainValidationError) as exc_info:
            MatchConfig.from_mapping(mapping)
        assert exc_info.value.field_path == "pbem_save_directory"


class TestConfigSeparation:
    """Global and per-match configuration remain explicitly separate."""

    def test_no_sftp_or_credentials_in_match_config(self) -> None:
        names = {item.name for item in dataclasses.fields(MatchConfig)}
        assert not any("sftp" in name for name in names)
        assert not any("password" in name for name in names)
        assert not any("key" in name for name in names)
        assert not any("host" in name for name in names)

    def test_no_match_local_values_in_global_config(self) -> None:
        names = {item.name for item in dataclasses.fields(GlobalConfig)}
        assert not any("player" in name for name in names)
        assert not any("mod" in name for name in names)
        assert not any("pbem" in name for name in names)
        assert not any("launch" in name for name in names)
        assert not any("game" in name for name in names)

    def test_expected_global_shape_matches_env_example(self) -> None:
        names = {item.name for item in dataclasses.fields(GlobalConfig)}
        assert names == {
            "log_level",
            "poll_interval_seconds",
            "civ4_executable",
            "sftp_host",
            "sftp_port",
            "sftp_username",
            "sftp_remote_root",
            "sftp_private_key_path",
            "sftp_password",
        }

    def test_expected_match_shape(self) -> None:
        names = {item.name for item in dataclasses.fields(MatchConfig)}
        assert names == {
            "game_id",
            "display_name",
            "players",
            "local_player_id",
            "launch_profile",
            "mod_name",
            "pbem_save_directory",
            "save_matching",
            "turn_handling_mode",
            "allow_force_close_after_commit",
        }
