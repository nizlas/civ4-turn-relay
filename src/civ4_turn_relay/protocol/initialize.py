"""Atomic match initialization and existing-match classification (§2.5).

Only a valid committed ``manifest.json`` makes a match initialized. Incomplete
trees require explicit repair; this module never deletes or silently finishes
them.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, unique

from civ4_turn_relay.domain import (
    MANIFEST_SCHEMA_VERSION,
    MIN_CLIENT_PROTOCOL,
    Manifest,
    MatchConfig,
    ProtocolMetadata,
    validate_operation_id,
)
from civ4_turn_relay.protocol.manifest_access import (
    ManifestReadOutcome,
    read_authoritative_manifest,
)
from civ4_turn_relay.protocol.paths import REQUIRED_SUBDIRECTORIES, GamePaths
from civ4_turn_relay.storage import (
    Storage,
    StorageAlreadyExistsError,
    StorageCapabilityError,
    StorageError,
    StorageTransportError,
)


@unique
class InitializeOutcome(Enum):
    """Typed initialization / join classification (no UI strings)."""

    CREATED = "created"
    JOINED_EXISTING = "joined_existing"
    INCOMPLETE_OR_CONFLICTING = "incomplete_or_conflicting"
    INVALID_MANIFEST = "invalid_manifest"
    GAME_ID_MISMATCH = "game_id_mismatch"
    CAPABILITY_FAILURE = "capability_failure"
    TRANSPORT_FAILURE = "transport_failure"


@dataclass(frozen=True, slots=True)
class InitializeResult:
    """Immutable result of attempting match initialization (§2.5)."""

    outcome: InitializeOutcome
    manifest: Manifest | None = None

    @property
    def initialized(self) -> bool:
        """True when a valid authoritative manifest is available."""
        return (
            self.outcome
            in {InitializeOutcome.CREATED, InitializeOutcome.JOINED_EXISTING}
            and self.manifest is not None
        )


def initialize_match(
    storage: Storage,
    config: MatchConfig,
    *,
    operation_id: str,
) -> InitializeResult:
    """Create a new match or classify an existing game root (§2.5).

    ``config`` and ``operation_id`` are validated before any remote I/O.
    ``operation_id`` names the temporary manifest object only; the sequence-zero
    manifest keeps ``protocol.last_operation_id`` as null.
    """
    if not isinstance(config, MatchConfig):
        raise TypeError("config must be a MatchConfig instance")
    validate_operation_id(operation_id, field_path="operation_id")
    paths = GamePaths(config.game_id)

    try:
        storage.mkdir(paths.root)
    except StorageAlreadyExistsError:
        return _classify_existing_game_root(storage, config.game_id)
    except StorageCapabilityError:
        return InitializeResult(InitializeOutcome.CAPABILITY_FAILURE)
    except StorageTransportError:
        return InitializeResult(InitializeOutcome.TRANSPORT_FAILURE)
    except StorageError:
        return InitializeResult(InitializeOutcome.TRANSPORT_FAILURE)

    return _finish_new_match(storage, config, paths=paths, operation_id=operation_id)


def _classify_existing_game_root(storage: Storage, game_id: str) -> InitializeResult:
    read = read_authoritative_manifest(storage, game_id)
    if read.outcome is ManifestReadOutcome.OK and read.manifest is not None:
        return InitializeResult(InitializeOutcome.JOINED_EXISTING, read.manifest)
    if read.outcome is ManifestReadOutcome.INVALID:
        return InitializeResult(InitializeOutcome.INVALID_MANIFEST)
    if read.outcome is ManifestReadOutcome.GAME_ID_MISMATCH:
        return InitializeResult(InitializeOutcome.GAME_ID_MISMATCH)
    if read.outcome is ManifestReadOutcome.TRANSPORT_FAILURE:
        return InitializeResult(InitializeOutcome.TRANSPORT_FAILURE)
    return InitializeResult(InitializeOutcome.INCOMPLETE_OR_CONFLICTING)


def _finish_new_match(
    storage: Storage,
    config: MatchConfig,
    *,
    paths: GamePaths,
    operation_id: str,
) -> InitializeResult:
    try:
        for name in REQUIRED_SUBDIRECTORIES:
            storage.mkdir(paths.resolve(name))
    except StorageCapabilityError:
        return InitializeResult(InitializeOutcome.CAPABILITY_FAILURE)
    except StorageError:
        return InitializeResult(InitializeOutcome.TRANSPORT_FAILURE)

    manifest = _sequence_zero_manifest(config)
    payload = manifest.to_json_bytes()
    temp_path = paths.temporary_manifest(operation_id)
    manifest_path = paths.manifest

    try:
        storage.write_file(temp_path, payload, overwrite=False)
        storage.atomic_replace(temp_path, manifest_path)
    except StorageCapabilityError:
        return InitializeResult(InitializeOutcome.CAPABILITY_FAILURE)
    except StorageTransportError:
        # After-fault on atomic_replace may leave a valid committed match.
        recovered = read_authoritative_manifest(storage, config.game_id)
        if (
            recovered.outcome is ManifestReadOutcome.OK
            and recovered.manifest is not None
        ):
            return InitializeResult(InitializeOutcome.CREATED, recovered.manifest)
        return InitializeResult(InitializeOutcome.TRANSPORT_FAILURE)
    except StorageError:
        return InitializeResult(InitializeOutcome.TRANSPORT_FAILURE)

    return InitializeResult(InitializeOutcome.CREATED, manifest)


def _sequence_zero_manifest(config: MatchConfig) -> Manifest:
    first_human = config.players[0]
    return Manifest(
        schema_version=MANIFEST_SCHEMA_VERSION,
        game_id=config.game_id,
        display_name=config.display_name,
        players=config.players,
        protocol_sequence=0,
        current_player_id=first_human.id,
        last_sender_id=None,
        accepted_save=None,
        accepted_save_hashes=(),
        previous_manifest_ref=None,
        protocol=ProtocolMetadata(
            min_client_protocol=MIN_CLIENT_PROTOCOL,
            last_operation_id=None,
        ),
    )
