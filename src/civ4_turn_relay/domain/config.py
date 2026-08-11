"""Global and per-match configuration models (design spec §4).

Global configuration mirrors the ``.env.example`` shape: installation and
server settings only. Per-match configuration carries match-local values
only. SFTP settings and credentials never appear per match; player identity,
mod, PBEM directory, and turn-handling settings never appear globally.

Parsing is pure: environment values are read from a supplied mapping (never
``os.environ``) and per-match configuration round-trips through strict
deterministic JSON. No file is read or written here.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum, unique

from civ4_turn_relay.domain.construction import (
    canonicalize_tuple,
    require_instance,
    require_optional_string,
)
from civ4_turn_relay.domain.errors import DomainValidationError
from civ4_turn_relay.domain.ids import validate_game_id, validate_player_id
from civ4_turn_relay.domain.manifest import Player
from civ4_turn_relay.domain.paths import validate_windows_local_path
from civ4_turn_relay.domain.redaction import REDACTED
from civ4_turn_relay.domain.serialization import (
    check_exact_keys,
    get_array,
    get_boolean,
    get_object,
    get_optional_string,
    get_string,
    parse_json_object_bytes,
    to_canonical_json_bytes,
)

ENV_PREFIX = "CIV4_RELAY_"

DEFAULT_LOG_LEVEL = "INFO"
DEFAULT_POLL_INTERVAL_SECONDS = 10

_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})

_ENV_KEYS = frozenset(
    {
        "LOG_LEVEL",
        "POLL_INTERVAL_SECONDS",
        "CIV4_EXECUTABLE",
        "SFTP_HOST",
        "SFTP_PORT",
        "SFTP_USERNAME",
        "SFTP_REMOTE_ROOT",
        "SFTP_PRIVATE_KEY_PATH",
        "SFTP_PASSWORD",
    }
)

_MATCH_CONFIG_REQUIRED_KEYS = (
    "display_name",
    "game_id",
    "launch_profile",
    "local_player_id",
    "mod_name",
    "pbem_save_directory",
    "players",
    "save_matching",
)

_MATCH_CONFIG_OPTIONAL_KEYS = (
    "allow_force_close_after_commit",
    "turn_handling_mode",
)


@unique
class TurnHandlingMode(Enum):
    """Per-match turn-handling mode (design spec §4.2 / §8.5)."""

    STANDARD = "standard"
    FULLY_MANAGED = "fully_managed"


_DECIMAL_INTEGER = re.compile(r"^[0-9]+$")


def _require_non_empty(value: str, field_path: str) -> None:
    if not isinstance(value, str) or not value:
        raise DomainValidationError(
            "expected a non-empty string", field_path=field_path
        )


def _require_true_int(value: int, field_path: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise DomainValidationError(
            "expected an integer (booleans are not integers)",
            field_path=field_path,
        )


@dataclass(frozen=True, slots=True)
class GlobalConfig:
    """Installation-wide settings (the ``.env.example`` shape).

    ``sftp_password`` and ``sftp_private_key_path`` are secrets: they are
    excluded from ``repr``, never appear in validation errors, and are
    redacted in diagnostic representations.
    """

    sftp_host: str
    sftp_port: int
    sftp_username: str
    sftp_remote_root: str
    log_level: str = DEFAULT_LOG_LEVEL
    poll_interval_seconds: int = DEFAULT_POLL_INTERVAL_SECONDS
    civ4_executable: str | None = None
    sftp_private_key_path: str | None = field(default=None, repr=False)
    sftp_password: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        _require_non_empty(self.sftp_host, "sftp_host")
        if any(char.isspace() for char in self.sftp_host):
            raise DomainValidationError(
                "whitespace is not allowed", field_path="sftp_host"
            )
        _require_true_int(self.sftp_port, "sftp_port")
        if not 1 <= self.sftp_port <= 65535:
            raise DomainValidationError(
                "expected a port between 1 and 65535", field_path="sftp_port"
            )
        _require_non_empty(self.sftp_username, "sftp_username")
        _require_non_empty(self.sftp_remote_root, "sftp_remote_root")
        if "\\" in self.sftp_remote_root:
            raise DomainValidationError(
                "backslashes are not allowed in remote paths",
                field_path="sftp_remote_root",
            )
        if not isinstance(self.log_level, str) or self.log_level not in _LOG_LEVELS:
            raise DomainValidationError(
                "expected one of DEBUG, INFO, WARNING, ERROR, CRITICAL",
                field_path="log_level",
            )
        _require_true_int(self.poll_interval_seconds, "poll_interval_seconds")
        if self.poll_interval_seconds < 1:
            raise DomainValidationError(
                "expected a positive number of seconds",
                field_path="poll_interval_seconds",
            )
        civ4_executable = require_optional_string(
            self.civ4_executable, field_path="civ4_executable"
        )
        object.__setattr__(self, "civ4_executable", civ4_executable)
        if civ4_executable is not None:
            validate_windows_local_path(civ4_executable, field_path="civ4_executable")
        private_key_path = require_optional_string(
            self.sftp_private_key_path, field_path="sftp_private_key_path"
        )
        object.__setattr__(self, "sftp_private_key_path", private_key_path)
        if private_key_path is not None:
            validate_windows_local_path(
                private_key_path, field_path="sftp_private_key_path"
            )
        password = require_optional_string(
            self.sftp_password, field_path="sftp_password"
        )
        object.__setattr__(self, "sftp_password", password)
        if password is not None and not password:
            raise DomainValidationError(
                "must be omitted instead of empty", field_path="sftp_password"
            )

    def secret_values(self) -> tuple[str, ...]:
        """Known secret values for text redaction (see FR-012)."""
        secrets: list[str] = []
        if self.sftp_password is not None:
            secrets.append(self.sftp_password)
        if self.sftp_private_key_path is not None:
            secrets.append(self.sftp_private_key_path)
        return tuple(secrets)

    def to_redacted_mapping(self) -> dict[str, object]:
        """Diagnostic representation with all secret fields redacted."""
        return {
            "civ4_executable": self.civ4_executable,
            "log_level": self.log_level,
            "poll_interval_seconds": self.poll_interval_seconds,
            "sftp_host": self.sftp_host,
            "sftp_password": (None if self.sftp_password is None else REDACTED),
            "sftp_port": self.sftp_port,
            "sftp_private_key_path": (
                None if self.sftp_private_key_path is None else REDACTED
            ),
            "sftp_remote_root": self.sftp_remote_root,
            "sftp_username": self.sftp_username,
        }


def _parse_env_integer(text: str, *, field_path: str) -> int:
    if not _DECIMAL_INTEGER.fullmatch(text):
        raise DomainValidationError("expected a decimal integer", field_path=field_path)
    return int(text)


def global_config_from_env_mapping(env: Mapping[str, str]) -> GlobalConfig:
    """Parse global configuration from a supplied environment mapping.

    Only ``CIV4_RELAY_*`` keys are considered; unknown keys with that prefix
    are rejected, other keys are ignored. Empty values count as absent. This
    function never reads ``os.environ`` or any file.
    """
    relevant = {key: value for key, value in env.items() if key.startswith(ENV_PREFIX)}
    known = {ENV_PREFIX + suffix for suffix in _ENV_KEYS}
    unknown = set(relevant) - known
    if unknown:
        raise DomainValidationError(
            "unexpected configuration key", field_path=sorted(unknown)[0]
        )

    def value_of(suffix: str) -> str | None:
        text = relevant.get(ENV_PREFIX + suffix, "")
        return text if text else None

    def required(suffix: str) -> str:
        text = value_of(suffix)
        if text is None:
            raise DomainValidationError(
                "required configuration value is missing",
                field_path=ENV_PREFIX + suffix,
            )
        return text

    port = _parse_env_integer(
        required("SFTP_PORT"), field_path=ENV_PREFIX + "SFTP_PORT"
    )
    poll_text = value_of("POLL_INTERVAL_SECONDS")
    poll_interval = (
        DEFAULT_POLL_INTERVAL_SECONDS
        if poll_text is None
        else _parse_env_integer(
            poll_text, field_path=ENV_PREFIX + "POLL_INTERVAL_SECONDS"
        )
    )
    return GlobalConfig(
        sftp_host=required("SFTP_HOST"),
        sftp_port=port,
        sftp_username=required("SFTP_USERNAME"),
        sftp_remote_root=required("SFTP_REMOTE_ROOT"),
        log_level=value_of("LOG_LEVEL") or DEFAULT_LOG_LEVEL,
        poll_interval_seconds=poll_interval,
        civ4_executable=value_of("CIV4_EXECUTABLE"),
        sftp_private_key_path=value_of("SFTP_PRIVATE_KEY_PATH"),
        sftp_password=value_of("SFTP_PASSWORD"),
    )


@dataclass(frozen=True, slots=True)
class SaveMatchingRules:
    """Minimal declarative rules for matching PBEM save filenames.

    ``filename_glob`` matches basenames inside the match PBEM directory
    (e.g. ``*.CivBeyondSwordSave``). File matching itself is out of scope
    until P4.
    """

    filename_glob: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.filename_glob, str)
            or not self.filename_glob
            or self.filename_glob in {".", ".."}
            or "/" in self.filename_glob
            or "\\" in self.filename_glob
        ):
            raise DomainValidationError(
                "expected a basename glob without directory separators",
                field_path="filename_glob",
            )

    def to_mapping(self) -> dict[str, object]:
        return {"filename_glob": self.filename_glob}

    @classmethod
    def from_mapping(
        cls, mapping: Mapping[str, object], *, path: str = ""
    ) -> SaveMatchingRules:
        check_exact_keys(mapping, ("filename_glob",), path=path)
        filename_glob = get_string(mapping, "filename_glob", path=path)
        try:
            return cls(filename_glob=filename_glob)
        except DomainValidationError as error:
            raise error.with_prefix(path) from None


def _parse_turn_handling_mode(
    value: object, *, field_path: str = "turn_handling_mode"
) -> TurnHandlingMode:
    if isinstance(value, TurnHandlingMode):
        return value
    if not isinstance(value, str):
        raise DomainValidationError(
            "expected a turn-handling mode string",
            field_path=field_path,
        )
    try:
        return TurnHandlingMode(value)
    except ValueError:
        raise DomainValidationError(
            "expected one of standard, fully_managed",
            field_path=field_path,
        ) from None


@dataclass(frozen=True, slots=True)
class MatchConfig:
    """Match-local configuration only (design spec §4.2).

    Never carries SFTP settings or credentials; those are global.
    """

    game_id: str
    display_name: str
    players: tuple[Player, ...]
    local_player_id: str
    launch_profile: str | None
    mod_name: str | None
    pbem_save_directory: str
    save_matching: SaveMatchingRules
    turn_handling_mode: TurnHandlingMode = TurnHandlingMode.STANDARD
    allow_force_close_after_commit: bool = False

    def __post_init__(self) -> None:
        players = canonicalize_tuple(
            self.players, Player, field_path="players", item_label="Player instance"
        )
        object.__setattr__(self, "players", players)
        save_matching = require_instance(
            self.save_matching, SaveMatchingRules, field_path="save_matching"
        )
        object.__setattr__(self, "save_matching", save_matching)
        launch_profile = require_optional_string(
            self.launch_profile, field_path="launch_profile"
        )
        object.__setattr__(self, "launch_profile", launch_profile)
        mod_name = require_optional_string(self.mod_name, field_path="mod_name")
        object.__setattr__(self, "mod_name", mod_name)
        turn_handling_mode = _parse_turn_handling_mode(
            self.turn_handling_mode, field_path="turn_handling_mode"
        )
        object.__setattr__(self, "turn_handling_mode", turn_handling_mode)

        validate_game_id(self.game_id, field_path="game_id")
        _require_non_empty(self.display_name, "display_name")
        if not players:
            raise DomainValidationError(
                "must list at least one human player", field_path="players"
            )
        seen: set[str] = set()
        for index, player in enumerate(players):
            if player.id in seen:
                raise DomainValidationError(
                    "duplicate player ID", field_path=f"players[{index}].id"
                )
            seen.add(player.id)
        validate_player_id(self.local_player_id, field_path="local_player_id")
        if self.local_player_id not in seen:
            raise DomainValidationError(
                "must be the ID of a listed player",
                field_path="local_player_id",
            )
        if launch_profile is not None:
            _require_non_empty(launch_profile, "launch_profile")
        if mod_name is not None:
            _require_non_empty(mod_name, "mod_name")
        validate_windows_local_path(
            self.pbem_save_directory, field_path="pbem_save_directory"
        )
        if not isinstance(self.allow_force_close_after_commit, bool):
            raise DomainValidationError(
                "expected a boolean",
                field_path="allow_force_close_after_commit",
            )
        # Meaningful only in Fully managed; STANDARD always stores false.
        if turn_handling_mode is TurnHandlingMode.STANDARD:
            object.__setattr__(self, "allow_force_close_after_commit", False)

    def to_mapping(self) -> dict[str, object]:
        """Return a primitive mapping of this per-match configuration."""
        return {
            "allow_force_close_after_commit": self.allow_force_close_after_commit,
            "display_name": self.display_name,
            "game_id": self.game_id,
            "launch_profile": self.launch_profile,
            "local_player_id": self.local_player_id,
            "mod_name": self.mod_name,
            "pbem_save_directory": self.pbem_save_directory,
            "players": [player.to_mapping() for player in self.players],
            "save_matching": self.save_matching.to_mapping(),
            "turn_handling_mode": self.turn_handling_mode.value,
        }

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, object]) -> MatchConfig:
        """Parse and validate per-match configuration from a mapping."""
        check_exact_keys(
            mapping,
            _MATCH_CONFIG_REQUIRED_KEYS,
            optional=_MATCH_CONFIG_OPTIONAL_KEYS,
        )
        players_raw = get_array(mapping, "players")
        players: list[Player] = []
        for index, item in enumerate(players_raw):
            item_path = f"players[{index}]"
            if not isinstance(item, Mapping):
                raise DomainValidationError("expected an object", field_path=item_path)
            players.append(Player.from_mapping(item, path=item_path))
        save_matching = SaveMatchingRules.from_mapping(
            get_object(mapping, "save_matching"), path="save_matching"
        )
        if "turn_handling_mode" in mapping:
            turn_handling_mode = _parse_turn_handling_mode(
                get_string(mapping, "turn_handling_mode"),
                field_path="turn_handling_mode",
            )
        else:
            turn_handling_mode = TurnHandlingMode.STANDARD
        return cls(
            game_id=get_string(mapping, "game_id"),
            display_name=get_string(mapping, "display_name"),
            players=tuple(players),
            local_player_id=get_string(mapping, "local_player_id"),
            launch_profile=get_optional_string(mapping, "launch_profile"),
            mod_name=get_optional_string(mapping, "mod_name"),
            pbem_save_directory=get_string(mapping, "pbem_save_directory"),
            save_matching=save_matching,
            turn_handling_mode=turn_handling_mode,
            allow_force_close_after_commit=get_boolean(
                mapping, "allow_force_close_after_commit", default=False
            ),
        )

    def to_json_bytes(self) -> bytes:
        """Serialize to deterministic canonical JSON bytes."""
        return to_canonical_json_bytes(self.to_mapping())

    @classmethod
    def from_json_bytes(cls, data: bytes) -> MatchConfig:
        """Parse and validate per-match configuration from JSON bytes."""
        return cls.from_mapping(parse_json_object_bytes(data))
