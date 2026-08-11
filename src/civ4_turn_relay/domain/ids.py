"""Pure validators for protocol identifiers, digests, and timestamps.

The patterns are normative in ``docs/SYNC_PROTOCOL.md`` (§2.1 for game IDs,
§3.1 for player IDs, §3.3 for digests and timestamps).
"""

from __future__ import annotations

import re
from datetime import datetime

from civ4_turn_relay.domain.errors import DomainValidationError

GAME_ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]{1,62}[a-z0-9]$")
PLAYER_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
SHA256_HEX_PATTERN = re.compile(r"^[0-9a-f]{64}$")
OPERATION_ID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
UTC_TIMESTAMP_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

_UTC_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def validate_game_id(value: str, *, field_path: str = "game_id") -> str:
    """Validate a game ID (protocol §2.1); return it unchanged."""
    if not isinstance(value, str) or not GAME_ID_PATTERN.fullmatch(value):
        raise DomainValidationError(
            "expected a game ID of 3-64 chars matching "
            "^[a-z][a-z0-9-]{1,62}[a-z0-9]$ (lowercase; no separators, "
            "dots, spaces, or traversal)",
            field_path=field_path,
        )
    return value


def validate_player_id(value: str, *, field_path: str = "player_id") -> str:
    """Validate a player ID (protocol §3.1); return it unchanged."""
    if not isinstance(value, str) or not PLAYER_ID_PATTERN.fullmatch(value):
        raise DomainValidationError(
            "expected a player ID matching ^[a-z][a-z0-9_-]{0,31}$",
            field_path=field_path,
        )
    return value


def validate_sha256_hex(value: str, *, field_path: str = "sha256") -> str:
    """Validate a SHA-256 digest: exactly 64 lowercase hex characters."""
    if not isinstance(value, str) or not SHA256_HEX_PATTERN.fullmatch(value):
        raise DomainValidationError(
            "expected a SHA-256 digest of exactly 64 lowercase hex characters",
            field_path=field_path,
        )
    return value


def validate_operation_id(value: str, *, field_path: str = "operation_id") -> str:
    """Validate a UUID operation ID in canonical lowercase hyphenated form."""
    if not isinstance(value, str) or not OPERATION_ID_PATTERN.fullmatch(value):
        raise DomainValidationError(
            "expected a UUID in canonical lowercase 8-4-4-4-12 form",
            field_path=field_path,
        )
    return value


def validate_utc_timestamp(value: str, *, field_path: str = "timestamp") -> str:
    """Validate a UTC timestamp in exact ``YYYY-MM-DDTHH:MM:SSZ`` form."""
    message = (
        "expected a UTC timestamp in exact second-resolution YYYY-MM-DDTHH:MM:SSZ form"
    )
    if not isinstance(value, str) or not UTC_TIMESTAMP_PATTERN.fullmatch(value):
        raise DomainValidationError(message, field_path=field_path)
    try:
        datetime.strptime(value, _UTC_TIMESTAMP_FORMAT)
    except ValueError:
        raise DomainValidationError(message, field_path=field_path) from None
    return value
