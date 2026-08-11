"""Immutable manifest models for sync-protocol schema version 1.

The models are always valid: every constructor validates the protocol rules
from ``docs/SYNC_PROTOCOL.md`` §3–§4 (including the ``accepted_save_hashes``
rules in §3.2), so an instance that exists has passed validation. Strict
mapping/JSON parsing rejects missing, unexpected, malformed, or mistyped
fields instead of silently discarding data.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from civ4_turn_relay.domain.errors import DomainValidationError
from civ4_turn_relay.domain.ids import (
    validate_game_id,
    validate_operation_id,
    validate_player_id,
    validate_sha256_hex,
    validate_utc_timestamp,
)
from civ4_turn_relay.domain.paths import (
    validate_accepted_save_path,
    validate_history_manifest_ref,
    validate_original_filename,
)
from civ4_turn_relay.domain.serialization import (
    check_exact_keys,
    get_array,
    get_integer,
    get_object,
    get_optional_object,
    get_optional_string,
    get_string,
    parse_json_object_bytes,
    to_canonical_json_bytes,
)

MANIFEST_SCHEMA_VERSION = 1
MIN_CLIENT_PROTOCOL = 1

_MANIFEST_KEYS = (
    "accepted_save",
    "accepted_save_hashes",
    "current_player_id",
    "display_name",
    "game_id",
    "last_sender_id",
    "players",
    "previous_manifest_ref",
    "protocol",
    "protocol_sequence",
    "schema_version",
)


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
class Player:
    """One human relay participant. AI civilizations never appear here."""

    id: str
    display_name: str

    def __post_init__(self) -> None:
        validate_player_id(self.id, field_path="id")
        _require_non_empty(self.display_name, "display_name")

    def to_mapping(self) -> dict[str, object]:
        return {"display_name": self.display_name, "id": self.id}

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, object], *, path: str = "") -> Player:
        check_exact_keys(mapping, ("display_name", "id"), path=path)
        player_id = get_string(mapping, "id", path=path)
        display_name = get_string(mapping, "display_name", path=path)
        try:
            return cls(id=player_id, display_name=display_name)
        except DomainValidationError as error:
            raise error.with_prefix(path) from None


@dataclass(frozen=True, slots=True)
class AcceptedSave:
    """The remote save object referenced by the committed manifest."""

    sha256: str
    size_bytes: int
    remote_path: str
    original_filename: str
    accepted_at: str

    def __post_init__(self) -> None:
        validate_sha256_hex(self.sha256, field_path="sha256")
        _require_true_int(self.size_bytes, "size_bytes")
        if self.size_bytes <= 0:
            raise DomainValidationError(
                "expected a positive byte count", field_path="size_bytes"
            )
        validate_accepted_save_path(self.remote_path, field_path="remote_path")
        validate_original_filename(
            self.original_filename, field_path="original_filename"
        )
        validate_utc_timestamp(self.accepted_at, field_path="accepted_at")

    def to_mapping(self) -> dict[str, object]:
        return {
            "accepted_at": self.accepted_at,
            "original_filename": self.original_filename,
            "remote_path": self.remote_path,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }

    @classmethod
    def from_mapping(
        cls, mapping: Mapping[str, object], *, path: str = ""
    ) -> AcceptedSave:
        keys = (
            "accepted_at",
            "original_filename",
            "remote_path",
            "sha256",
            "size_bytes",
        )
        check_exact_keys(mapping, keys, path=path)
        sha256 = get_string(mapping, "sha256", path=path)
        size_bytes = get_integer(mapping, "size_bytes", path=path)
        remote_path = get_string(mapping, "remote_path", path=path)
        original_filename = get_string(mapping, "original_filename", path=path)
        accepted_at = get_string(mapping, "accepted_at", path=path)
        try:
            return cls(
                sha256=sha256,
                size_bytes=size_bytes,
                remote_path=remote_path,
                original_filename=original_filename,
                accepted_at=accepted_at,
            )
        except DomainValidationError as error:
            raise error.with_prefix(path) from None


@dataclass(frozen=True, slots=True)
class ProtocolMetadata:
    """Recovery metadata under the manifest ``protocol`` key."""

    min_client_protocol: int
    last_operation_id: str | None

    def __post_init__(self) -> None:
        _require_true_int(self.min_client_protocol, "min_client_protocol")
        if self.min_client_protocol != MIN_CLIENT_PROTOCOL:
            raise DomainValidationError(
                f"must be {MIN_CLIENT_PROTOCOL} for protocol v1",
                field_path="min_client_protocol",
            )
        if self.last_operation_id is not None:
            validate_operation_id(
                self.last_operation_id, field_path="last_operation_id"
            )

    def to_mapping(self) -> dict[str, object]:
        return {
            "last_operation_id": self.last_operation_id,
            "min_client_protocol": self.min_client_protocol,
        }

    @classmethod
    def from_mapping(
        cls, mapping: Mapping[str, object], *, path: str = ""
    ) -> ProtocolMetadata:
        check_exact_keys(
            mapping, ("last_operation_id", "min_client_protocol"), path=path
        )
        min_client_protocol = get_integer(mapping, "min_client_protocol", path=path)
        last_operation_id = get_optional_string(mapping, "last_operation_id", path=path)
        try:
            return cls(
                min_client_protocol=min_client_protocol,
                last_operation_id=last_operation_id,
            )
        except DomainValidationError as error:
            raise error.with_prefix(path) from None


@dataclass(frozen=True, slots=True)
class Manifest:
    """Authoritative match state (manifest schema version 1)."""

    schema_version: int
    game_id: str
    display_name: str
    players: tuple[Player, ...]
    protocol_sequence: int
    current_player_id: str
    last_sender_id: str | None
    accepted_save: AcceptedSave | None
    accepted_save_hashes: tuple[str, ...]
    previous_manifest_ref: str | None
    protocol: ProtocolMetadata

    def __post_init__(self) -> None:
        _require_true_int(self.schema_version, "schema_version")
        if self.schema_version != MANIFEST_SCHEMA_VERSION:
            raise DomainValidationError(
                f"schema_version must be {MANIFEST_SCHEMA_VERSION}",
                field_path="schema_version",
            )
        validate_game_id(self.game_id, field_path="game_id")
        _require_non_empty(self.display_name, "display_name")
        self._validate_players()
        _require_true_int(self.protocol_sequence, "protocol_sequence")
        if self.protocol_sequence < 0:
            raise DomainValidationError(
                "expected a non-negative integer", field_path="protocol_sequence"
            )
        self._validate_hashes()
        self._validate_sequence_coupling()

    def _validate_players(self) -> None:
        if not self.players:
            raise DomainValidationError(
                "must list at least one human player", field_path="players"
            )
        seen: set[str] = set()
        for index, player in enumerate(self.players):
            if player.id in seen:
                raise DomainValidationError(
                    "duplicate player ID", field_path=f"players[{index}].id"
                )
            seen.add(player.id)
        if self.current_player_id not in seen:
            raise DomainValidationError(
                "must be the ID of a listed player",
                field_path="current_player_id",
            )
        if self.last_sender_id is not None and self.last_sender_id not in seen:
            raise DomainValidationError(
                "must be the ID of a listed player", field_path="last_sender_id"
            )

    def _validate_hashes(self) -> None:
        for index, digest in enumerate(self.accepted_save_hashes):
            validate_sha256_hex(digest, field_path=f"accepted_save_hashes[{index}]")
        if len(set(self.accepted_save_hashes)) != len(self.accepted_save_hashes):
            raise DomainValidationError(
                "entries must be unique", field_path="accepted_save_hashes"
            )
        if len(self.accepted_save_hashes) != self.protocol_sequence:
            raise DomainValidationError(
                "length must equal protocol_sequence",
                field_path="accepted_save_hashes",
            )

    def _validate_sequence_coupling(self) -> None:
        if self.protocol_sequence == 0:
            if self.accepted_save is not None:
                raise DomainValidationError(
                    "must be null when protocol_sequence is 0",
                    field_path="accepted_save",
                )
            if self.last_sender_id is not None:
                raise DomainValidationError(
                    "must be null when protocol_sequence is 0",
                    field_path="last_sender_id",
                )
            if self.previous_manifest_ref is not None:
                raise DomainValidationError(
                    "must be null when protocol_sequence is 0",
                    field_path="previous_manifest_ref",
                )
            if self.protocol.last_operation_id is not None:
                raise DomainValidationError(
                    "must be null when protocol_sequence is 0",
                    field_path="protocol.last_operation_id",
                )
            return
        if self.accepted_save is None:
            raise DomainValidationError(
                "required when protocol_sequence > 0", field_path="accepted_save"
            )
        if self.last_sender_id is None:
            raise DomainValidationError(
                "required when protocol_sequence > 0", field_path="last_sender_id"
            )
        if self.previous_manifest_ref is None:
            raise DomainValidationError(
                "required when protocol_sequence > 0",
                field_path="previous_manifest_ref",
            )
        validate_history_manifest_ref(
            self.previous_manifest_ref, field_path="previous_manifest_ref"
        )
        if self.protocol.last_operation_id is None:
            raise DomainValidationError(
                "required when protocol_sequence > 0",
                field_path="protocol.last_operation_id",
            )
        if self.accepted_save_hashes[-1] != self.accepted_save.sha256:
            raise DomainValidationError(
                "final entry must equal accepted_save.sha256",
                field_path="accepted_save_hashes",
            )

    def to_mapping(self) -> dict[str, object]:
        """Return a primitive mapping of this manifest."""
        accepted: dict[str, object] | None = (
            None if self.accepted_save is None else self.accepted_save.to_mapping()
        )
        return {
            "accepted_save": accepted,
            "accepted_save_hashes": list(self.accepted_save_hashes),
            "current_player_id": self.current_player_id,
            "display_name": self.display_name,
            "game_id": self.game_id,
            "last_sender_id": self.last_sender_id,
            "players": [player.to_mapping() for player in self.players],
            "previous_manifest_ref": self.previous_manifest_ref,
            "protocol": self.protocol.to_mapping(),
            "protocol_sequence": self.protocol_sequence,
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, object]) -> Manifest:
        """Parse and validate a manifest from a primitive mapping."""
        check_exact_keys(mapping, _MANIFEST_KEYS)
        players_raw = get_array(mapping, "players")
        players: list[Player] = []
        for index, item in enumerate(players_raw):
            item_path = f"players[{index}]"
            if not isinstance(item, Mapping):
                raise DomainValidationError("expected an object", field_path=item_path)
            players.append(Player.from_mapping(item, path=item_path))
        hashes_raw = get_array(mapping, "accepted_save_hashes")
        hashes: list[str] = []
        for index, item in enumerate(hashes_raw):
            if not isinstance(item, str):
                raise DomainValidationError(
                    "expected a string",
                    field_path=f"accepted_save_hashes[{index}]",
                )
            hashes.append(item)
        accepted_raw = get_optional_object(mapping, "accepted_save")
        accepted_save = (
            None
            if accepted_raw is None
            else AcceptedSave.from_mapping(accepted_raw, path="accepted_save")
        )
        protocol = ProtocolMetadata.from_mapping(
            get_object(mapping, "protocol"), path="protocol"
        )
        return cls(
            schema_version=get_integer(mapping, "schema_version"),
            game_id=get_string(mapping, "game_id"),
            display_name=get_string(mapping, "display_name"),
            players=tuple(players),
            protocol_sequence=get_integer(mapping, "protocol_sequence"),
            current_player_id=get_string(mapping, "current_player_id"),
            last_sender_id=get_optional_string(mapping, "last_sender_id"),
            accepted_save=accepted_save,
            accepted_save_hashes=tuple(hashes),
            previous_manifest_ref=get_optional_string(mapping, "previous_manifest_ref"),
            protocol=protocol,
        )

    def to_json_bytes(self) -> bytes:
        """Serialize to deterministic canonical JSON bytes (§3.3)."""
        return to_canonical_json_bytes(self.to_mapping())

    @classmethod
    def from_json_bytes(cls, data: bytes) -> Manifest:
        """Parse and validate a manifest from JSON bytes."""
        return cls.from_mapping(parse_json_object_bytes(data))
