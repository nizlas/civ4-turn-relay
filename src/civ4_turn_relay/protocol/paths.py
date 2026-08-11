"""Central remote game-path scoping under the storage adapter root.

The storage adapter root is treated as the games collection root
(``{server_root}/games/`` in protocol §2). Storage paths therefore look like
``{game_id}/manifest.json`` and ``{game_id}/saves/...``.

Manifest fields such as ``accepted_save.remote_path`` remain **game-relative**
(``saves/...``). Callers resolve them through :class:`GamePaths` before any
storage I/O so path joining is not scattered across the engine.
"""

from __future__ import annotations

from dataclasses import dataclass

from civ4_turn_relay.domain import (
    DomainValidationError,
    validate_accepted_save_path,
    validate_game_id,
    validate_history_manifest_ref,
    validate_operation_id,
    validate_remote_relative_path,
    validate_sha256_hex,
)

MANIFEST_FILENAME = "manifest.json"
SAVES_DIR = "saves"
TEMPORARY_DIR = "temporary"
LOCKS_DIR = "locks"
HISTORY_DIR = "history"
UPLOAD_LOCK_DIRNAME = "upload.lock"
LOCK_JSON_FILENAME = "lock.json"

REQUIRED_SUBDIRECTORIES: tuple[str, ...] = (
    SAVES_DIR,
    TEMPORARY_DIR,
    LOCKS_DIR,
    HISTORY_DIR,
)


@dataclass(frozen=True, slots=True)
class GamePaths:
    """Validated mapping from game-relative paths to storage paths."""

    game_id: str

    def __post_init__(self) -> None:
        validate_game_id(self.game_id, field_path="game_id")

    @property
    def root(self) -> str:
        """Storage path of the game directory (``{game_id}``)."""
        return self.game_id

    def resolve(self, game_relative_path: str) -> str:
        """Map a game-relative path to a storage path under the game root.

        Validates the game-relative path with P1 rules before joining. Rejects
        traversal, absolute paths, backslashes, empty components, and escapes.
        """
        relative = validate_remote_relative_path(
            game_relative_path, field_path="game_relative_path"
        )
        return f"{self.game_id}/{relative}"

    @property
    def manifest(self) -> str:
        """Storage path of the authoritative ``manifest.json``."""
        return self.resolve(MANIFEST_FILENAME)

    @property
    def saves(self) -> str:
        return self.resolve(SAVES_DIR)

    @property
    def temporary(self) -> str:
        return self.resolve(TEMPORARY_DIR)

    @property
    def locks(self) -> str:
        return self.resolve(LOCKS_DIR)

    @property
    def history(self) -> str:
        return self.resolve(HISTORY_DIR)

    def temporary_manifest(self, operation_id: str) -> str:
        """Storage path for a staged init/commit manifest temp object."""
        validate_operation_id(operation_id, field_path="operation_id")
        return self.resolve(f"{TEMPORARY_DIR}/manifest-{operation_id}.json")

    @property
    def upload_lock_dir(self) -> str:
        """Storage path of ``locks/upload.lock/``."""
        return self.resolve(f"{LOCKS_DIR}/{UPLOAD_LOCK_DIRNAME}")

    @property
    def upload_lock_json(self) -> str:
        """Storage path of ``locks/upload.lock/lock.json``."""
        return self.resolve(f"{LOCKS_DIR}/{UPLOAD_LOCK_DIRNAME}/{LOCK_JSON_FILENAME}")

    def temporary_upload(self, operation_id: str, extension: str) -> str:
        """Storage path for a staged outgoing save upload."""
        validate_operation_id(operation_id, field_path="operation_id")
        _validate_save_extension(extension)
        return self.resolve(f"{TEMPORARY_DIR}/{operation_id}.upload{extension}")

    def accepted_save_relative(self, sequence: int, sha256: str, extension: str) -> str:
        """Game-relative immutable save path ``saves/{seq}_{hash12}{ext}``."""
        _require_nonneg_int(sequence, field_path="sequence")
        digest = validate_sha256_hex(sha256, field_path="sha256")
        _validate_save_extension(extension)
        relative = f"{SAVES_DIR}/{sequence:06d}_{digest[:12]}{extension}"
        return validate_accepted_save_path(relative, field_path="remote_path")

    def history_manifest_relative(self, sequence: int, manifest_sha256: str) -> str:
        """Game-relative history path for archived manifest bytes."""
        _require_nonneg_int(sequence, field_path="sequence")
        digest = validate_sha256_hex(manifest_sha256, field_path="manifest_sha256")
        relative = f"{HISTORY_DIR}/manifest-{sequence:06d}-{digest[:12]}.json"
        return validate_history_manifest_ref(
            relative, field_path="previous_manifest_ref"
        )


def _require_nonneg_int(value: int, *, field_path: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise DomainValidationError(
            "expected a non-negative integer",
            field_path=field_path,
        )


def _validate_save_extension(extension: str) -> str:
    if not isinstance(extension, str) or not extension.startswith("."):
        raise DomainValidationError(
            "expected a save extension beginning with '.'",
            field_path="extension",
        )
    if "/" in extension or "\\" in extension or extension in {".", ".."}:
        raise DomainValidationError(
            "extension must not contain path separators",
            field_path="extension",
        )
    return extension


def extension_from_original_filename(original_filename: str) -> str:
    """Return the final ``.suffix`` from a validated basename."""
    if "." not in original_filename or original_filename.startswith("."):
        raise DomainValidationError(
            "original filename must include a non-empty extension",
            field_path="original_filename",
        )
    suffix = original_filename.rsplit(".", 1)[1]
    if not suffix:
        raise DomainValidationError(
            "original filename must include a non-empty extension",
            field_path="original_filename",
        )
    return _validate_save_extension(f".{suffix}")
