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
    validate_game_id,
    validate_operation_id,
    validate_remote_relative_path,
)

MANIFEST_FILENAME = "manifest.json"
SAVES_DIR = "saves"
TEMPORARY_DIR = "temporary"
LOCKS_DIR = "locks"
HISTORY_DIR = "history"

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
