"""Pure domain layer: validated models, validators, and serialization.

This package contains no I/O adapters. It never touches the filesystem,
network, SFTP, GUI, Watchdog, or Civilization processes; it only validates
and transforms in-memory values.
"""

from civ4_turn_relay.domain.config import (
    DEFAULT_LOG_LEVEL,
    DEFAULT_POLL_INTERVAL_SECONDS,
    ENV_PREFIX,
    GlobalConfig,
    MatchConfig,
    SaveMatchingRules,
    TurnHandlingMode,
    global_config_from_env_mapping,
)
from civ4_turn_relay.domain.errors import DomainValidationError
from civ4_turn_relay.domain.hashing import sha256_hex
from civ4_turn_relay.domain.ids import (
    add_utc_seconds,
    validate_client_id,
    validate_game_id,
    validate_operation_id,
    validate_player_id,
    validate_sha256_hex,
    validate_utc_timestamp,
)
from civ4_turn_relay.domain.manifest import (
    MANIFEST_SCHEMA_VERSION,
    MIN_CLIENT_PROTOCOL,
    AcceptedSave,
    Manifest,
    Player,
    ProtocolMetadata,
)
from civ4_turn_relay.domain.paths import (
    validate_accepted_save_path,
    validate_history_manifest_ref,
    validate_original_filename,
    validate_remote_relative_path,
    validate_windows_local_path,
)
from civ4_turn_relay.domain.redaction import (
    REDACTED,
    is_sensitive_field_name,
    redact_known_secrets,
    redact_structure,
)
from civ4_turn_relay.domain.serialization import (
    parse_json_object_bytes,
    to_canonical_json_bytes,
)
from civ4_turn_relay.domain.states import OperationalState

__all__ = [
    "DEFAULT_LOG_LEVEL",
    "DEFAULT_POLL_INTERVAL_SECONDS",
    "ENV_PREFIX",
    "MANIFEST_SCHEMA_VERSION",
    "MIN_CLIENT_PROTOCOL",
    "REDACTED",
    "AcceptedSave",
    "DomainValidationError",
    "GlobalConfig",
    "Manifest",
    "MatchConfig",
    "OperationalState",
    "Player",
    "ProtocolMetadata",
    "SaveMatchingRules",
    "TurnHandlingMode",
    "global_config_from_env_mapping",
    "is_sensitive_field_name",
    "parse_json_object_bytes",
    "redact_known_secrets",
    "redact_structure",
    "sha256_hex",
    "to_canonical_json_bytes",
    "add_utc_seconds",
    "validate_accepted_save_path",
    "validate_client_id",
    "validate_game_id",
    "validate_history_manifest_ref",
    "validate_operation_id",
    "validate_original_filename",
    "validate_player_id",
    "validate_remote_relative_path",
    "validate_sha256_hex",
    "validate_utc_timestamp",
    "validate_windows_local_path",
]
