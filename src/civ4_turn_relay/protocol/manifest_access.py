"""Authoritative remote manifest reader (protocol §2.4, §3).

Reads ``{game_id}/manifest.json``, parses it through P1
``Manifest.from_json_bytes``, and verifies directory ``game_id`` equality.
Never repairs, rewrites, or infers ownership from local state.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, unique

from civ4_turn_relay.domain import DomainValidationError, Manifest, validate_game_id
from civ4_turn_relay.protocol.paths import GamePaths
from civ4_turn_relay.storage import (
    Storage,
    StorageError,
    StorageNotFoundError,
    StorageTransportError,
    StorageWrongKindError,
)


@unique
class ManifestReadOutcome(Enum):
    """Typed outcomes for an authoritative manifest read."""

    OK = "ok"
    MISSING = "missing"
    INVALID = "invalid"
    GAME_ID_MISMATCH = "game_id_mismatch"
    TRANSPORT_FAILURE = "transport_failure"


@dataclass(frozen=True, slots=True)
class ManifestReadResult:
    """Immutable result of reading the authoritative remote manifest."""

    outcome: ManifestReadOutcome
    manifest: Manifest | None = None

    @property
    def ok(self) -> bool:
        return self.outcome is ManifestReadOutcome.OK and self.manifest is not None


def read_authoritative_manifest(storage: Storage, game_id: str) -> ManifestReadResult:
    """Read and validate the committed remote manifest for ``game_id``.

    Validates ``game_id`` before any storage I/O. Distinguishes missing,
    invalid schema/hash-list, directory/manifest game-ID mismatch, and
    transport failures. Does not mutate storage.
    """
    validate_game_id(game_id, field_path="game_id")
    paths = GamePaths(game_id)
    try:
        raw = storage.read_file(paths.manifest)
    except StorageNotFoundError:
        return ManifestReadResult(ManifestReadOutcome.MISSING)
    except StorageWrongKindError:
        return ManifestReadResult(ManifestReadOutcome.MISSING)
    except StorageTransportError:
        return ManifestReadResult(ManifestReadOutcome.TRANSPORT_FAILURE)
    except StorageError:
        return ManifestReadResult(ManifestReadOutcome.TRANSPORT_FAILURE)

    try:
        manifest = Manifest.from_json_bytes(raw)
    except DomainValidationError:
        return ManifestReadResult(ManifestReadOutcome.INVALID)

    if manifest.game_id != game_id:
        return ManifestReadResult(ManifestReadOutcome.GAME_ID_MISMATCH)

    return ManifestReadResult(ManifestReadOutcome.OK, manifest)
