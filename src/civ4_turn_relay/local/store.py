"""LocalStore: installation identity, per-match config, and match state.

On-disk layout under an explicit caller-provided root::

    installation.json
    matches/{game_id}/config.json
    matches/{game_id}/state.json

``state.json`` is the sole durable source for :class:`MatchLocalRecords`,
including embedded handoff-journal fields.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

from civ4_turn_relay.domain import (
    DomainValidationError,
    MatchConfig,
    parse_json_object_bytes,
    to_canonical_json_bytes,
    validate_client_id,
    validate_game_id,
)
from civ4_turn_relay.domain.serialization import (
    check_exact_keys,
    get_integer,
    get_string,
)
from civ4_turn_relay.local.errors import (
    LocalStoreCorruptError,
    LocalStoreIOError,
    LocalStoreMissingError,
    LocalStoreUnsupportedSchemaError,
)
from civ4_turn_relay.local.json_store import (
    FsyncFn,
    ReplaceFn,
    UuidFactory,
    atomic_write_bytes,
    exclusive_create_bytes,
)
from civ4_turn_relay.local.records import MatchLocalRecords

INSTALLATION_SCHEMA_VERSION = 1
_INSTALLATION_KEYS = ("client_id", "schema_version")


@dataclass(frozen=True, slots=True)
class InstallationIdentity:
    """Stable installation identity persisted in ``installation.json``."""

    client_id: str
    schema_version: int = INSTALLATION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "client_id",
            validate_client_id(self.client_id, field_path="client_id"),
        )
        if isinstance(self.schema_version, bool) or not isinstance(
            self.schema_version, int
        ):
            raise DomainValidationError(
                "expected an integer (booleans are not integers)",
                field_path="schema_version",
            )
        if self.schema_version != INSTALLATION_SCHEMA_VERSION:
            raise DomainValidationError(
                f"unsupported installation schema version "
                f"(expected {INSTALLATION_SCHEMA_VERSION})",
                field_path="schema_version",
            )

    def to_mapping(self) -> dict[str, object]:
        return {
            "client_id": self.client_id,
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, object]) -> InstallationIdentity:
        check_exact_keys(mapping, _INSTALLATION_KEYS)
        return cls(
            client_id=get_string(mapping, "client_id"),
            schema_version=get_integer(mapping, "schema_version"),
        )

    def to_json_bytes(self) -> bytes:
        return to_canonical_json_bytes(self.to_mapping())

    @classmethod
    def from_json_bytes(cls, data: bytes) -> InstallationIdentity:
        return cls.from_mapping(parse_json_object_bytes(data))


class LocalStore:
    """Filesystem-backed local persistence under an explicit data root."""

    def __init__(
        self,
        root: Path | str,
        *,
        replace_fn: ReplaceFn = os.replace,
        fsync_fn: FsyncFn = os.fsync,
        uuid_factory: UuidFactory = uuid.uuid4,
    ) -> None:
        self._root = Path(root)
        self._replace_fn = replace_fn
        self._fsync_fn = fsync_fn
        self._uuid_factory = uuid_factory

    @property
    def root(self) -> Path:
        return self._root

    def installation_path(self) -> Path:
        return self._contained(self._root / "installation.json")

    def match_dir(self, game_id: str) -> Path:
        validated = validate_game_id(game_id, field_path="game_id")
        return self._contained(self._root / "matches" / validated)

    def match_config_path(self, game_id: str) -> Path:
        return self._contained(self.match_dir(game_id) / "config.json")

    def match_state_path(self, game_id: str) -> Path:
        return self._contained(self.match_dir(game_id) / "state.json")

    def get_or_create_installation_identity(
        self,
        *,
        uuid_factory: UuidFactory | None = None,
    ) -> str:
        """Return the durable ``client_id``, creating it once if absent.

        First-time initialization uses exclusive file creation so concurrent
        LocalStore/process attempts converge on one persisted UUID. Corrupt or
        unsupported installation documents are never treated as valid identity.
        """
        path = self.installation_path()
        existing = self._try_read_installation(path)
        if existing is not None:
            return existing.client_id

        factory = uuid_factory or self._uuid_factory
        candidate = str(factory())
        # validate_client_id accepts canonical UUID form.
        identity = InstallationIdentity(client_id=candidate)
        payload = identity.to_json_bytes()
        try:
            created = exclusive_create_bytes(path, payload, fsync_fn=self._fsync_fn)
        except LocalStoreIOError:
            raise
        except OSError as error:
            raise LocalStoreIOError(
                "failed to create installation identity",
                path=str(path),
            ) from error

        if created:
            return identity.client_id

        winner = self._try_read_installation(path)
        if winner is None:
            # Destination exists but is unreadable/invalid: never invent a new ID.
            raise LocalStoreCorruptError(
                "installation identity exists but is not valid",
                path=str(path),
            )
        return winner.client_id

    def load_match_config(self, game_id: str) -> MatchConfig:
        path = self.match_config_path(game_id)
        data = self._read_bytes_or_missing(path)
        try:
            config = MatchConfig.from_json_bytes(data)
        except DomainValidationError as error:
            self._raise_schema_or_corrupt(error, path=str(path))
        if config.game_id != validate_game_id(game_id, field_path="game_id"):
            raise LocalStoreCorruptError(
                "config game_id does not match store path",
                path=str(path),
            )
        return config

    def write_match_config(self, config: MatchConfig) -> None:
        if not isinstance(config, MatchConfig):
            raise TypeError("config must be a MatchConfig instance")
        path = self.match_config_path(config.game_id)
        self._write_bytes(path, config.to_json_bytes())

    def load_match_state(self, game_id: str) -> MatchLocalRecords:
        path = self.match_state_path(game_id)
        data = self._read_bytes_or_missing(path)
        try:
            records = MatchLocalRecords.from_json_bytes(data)
        except DomainValidationError as error:
            self._raise_schema_or_corrupt(error, path=str(path))
        validated = validate_game_id(game_id, field_path="game_id")
        if records.game_id != validated:
            raise LocalStoreCorruptError(
                "state game_id does not match store path",
                path=str(path),
            )
        return records

    def write_match_state(self, records: MatchLocalRecords) -> None:
        if not isinstance(records, MatchLocalRecords):
            raise TypeError("records must be a MatchLocalRecords instance")
        path = self.match_state_path(records.game_id)
        self._write_bytes(path, records.to_json_bytes())

    def load_match_state_or_empty(self, game_id: str) -> MatchLocalRecords:
        """Load state, or return an empty schema-v1 record when missing."""
        try:
            return self.load_match_state(game_id)
        except LocalStoreMissingError:
            return MatchLocalRecords(game_id=validate_game_id(game_id))

    def update_match_state(
        self,
        game_id: str,
        mutator: Callable[[MatchLocalRecords], MatchLocalRecords],
    ) -> MatchLocalRecords:
        """Read-modify-write match state, preserving unrelated fields."""
        current = self.load_match_state_or_empty(game_id)
        updated = mutator(current)
        if not isinstance(updated, MatchLocalRecords):
            raise TypeError("mutator must return MatchLocalRecords")
        if updated.game_id != current.game_id:
            raise DomainValidationError(
                "mutator must not change game_id",
                field_path="game_id",
            )
        self.write_match_state(updated)
        return updated

    def _try_read_installation(self, path: Path) -> InstallationIdentity | None:
        if not path.is_file():
            return None
        try:
            data = path.read_bytes()
        except OSError as error:
            raise LocalStoreIOError(
                "failed to read installation identity",
                path=str(path),
            ) from error
        try:
            return InstallationIdentity.from_json_bytes(data)
        except DomainValidationError as error:
            if error.field_path == "schema_version" and "unsupported" in error.message:
                raise LocalStoreUnsupportedSchemaError(
                    error.message,
                    path=str(path),
                ) from error
            raise LocalStoreCorruptError(
                "installation identity is corrupt or invalid",
                path=str(path),
            ) from error

    def _read_bytes_or_missing(self, path: Path) -> bytes:
        if not path.is_file():
            raise LocalStoreMissingError("document is missing", path=str(path))
        try:
            return path.read_bytes()
        except OSError as error:
            raise LocalStoreIOError(
                "failed to read document",
                path=str(path),
            ) from error

    def _write_bytes(self, path: Path, data: bytes) -> None:
        atomic_write_bytes(
            path,
            data,
            replace_fn=self._replace_fn,
            fsync_fn=self._fsync_fn,
            uuid_factory=self._uuid_factory,
        )

    def _contained(self, path: Path) -> Path:
        """Require ``path`` to resolve strictly beneath the store root."""
        root = self._root.resolve(strict=False)
        candidate = path if path.is_absolute() else (self._root / path)
        resolved = candidate.resolve(strict=False)
        try:
            resolved.relative_to(root)
        except ValueError as error:
            raise DomainValidationError(
                "path escapes the local store root",
                field_path="path",
            ) from error
        # Extra Windows-oriented defense: reject ``..`` components in the
        # unresolved relative form under root.
        try:
            relative = PureWindowsPath(resolved).relative_to(PureWindowsPath(root))
        except ValueError as error:
            raise DomainValidationError(
                "path escapes the local store root",
                field_path="path",
            ) from error
        if ".." in relative.parts:
            raise DomainValidationError(
                "path escapes the local store root",
                field_path="path",
            )
        return resolved

    @staticmethod
    def _raise_schema_or_corrupt(error: DomainValidationError, *, path: str) -> None:
        if error.field_path == "schema_version" and "unsupported" in error.message:
            raise LocalStoreUnsupportedSchemaError(error.message, path=path) from error
        raise LocalStoreCorruptError(
            "document is corrupt or invalid",
            path=path,
        ) from error
