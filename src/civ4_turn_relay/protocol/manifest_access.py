"""Authoritative remote manifest reader (protocol §2.4, §3).

Reads ``{game_id}/manifest.json``, parses it through P1
``Manifest.from_json_bytes``, and verifies directory ``game_id`` equality.
Never repairs, rewrites, or infers ownership from local state.

Successful reads preserve the exact immutable ``bytes`` returned by storage so
callers can attribute an operation to a specific committed payload without
relying on canonical reserialization.
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
    """Immutable result of reading the authoritative remote manifest.

    Field invariants:

    - ``outcome`` must be a real ``ManifestReadOutcome`` (no string coercion).
    - ``manifest``, when non-``None``, must be a real ``Manifest`` instance.
    - ``OK`` requires both ``manifest`` and exact ``raw_bytes``.
    - Non-``OK`` outcomes never carry a parsed ``manifest``.
    - ``MISSING`` and ``TRANSPORT_FAILURE`` require ``raw_bytes is None``.
    - ``INVALID`` and ``GAME_ID_MISMATCH`` MAY retain exact storage ``raw_bytes``
      as diagnostics of what was read; otherwise ``raw_bytes`` is ``None``
      (e.g. wrong-kind path with no readable file bytes).
    - When present, ``raw_bytes`` must be exact ``bytes`` (not ``bytearray``).
    """

    outcome: ManifestReadOutcome
    manifest: Manifest | None = None
    raw_bytes: bytes | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, ManifestReadOutcome):
            raise DomainValidationError(
                "expected a ManifestReadOutcome",
                field_path="outcome",
            )
        if self.manifest is not None and not isinstance(self.manifest, Manifest):
            raise DomainValidationError(
                "expected a Manifest instance",
                field_path="manifest",
            )
        if self.raw_bytes is not None and type(self.raw_bytes) is not bytes:
            raise DomainValidationError(
                "raw_bytes must be exact bytes",
                field_path="raw_bytes",
            )
        if self.outcome is ManifestReadOutcome.OK:
            if self.manifest is None:
                raise DomainValidationError(
                    "OK requires a parsed manifest",
                    field_path="manifest",
                )
            if self.raw_bytes is None:
                raise DomainValidationError(
                    "OK requires exact storage raw_bytes",
                    field_path="raw_bytes",
                )
            return
        if self.manifest is not None:
            raise DomainValidationError(
                "non-OK outcomes must not carry a manifest",
                field_path="manifest",
            )
        if (
            self.outcome
            in {
                ManifestReadOutcome.MISSING,
                ManifestReadOutcome.TRANSPORT_FAILURE,
            }
            and self.raw_bytes is not None
        ):
            raise DomainValidationError(
                "this outcome must not carry raw_bytes",
                field_path="raw_bytes",
            )

    @property
    def ok(self) -> bool:
        return (
            self.outcome is ManifestReadOutcome.OK
            and self.manifest is not None
            and self.raw_bytes is not None
        )


def read_authoritative_manifest(storage: Storage, game_id: str) -> ManifestReadResult:
    """Read and validate the committed remote manifest for ``game_id``.

    Validates ``game_id`` before any storage I/O. Distinguishes missing,
    invalid schema/hash-list, directory/manifest game-ID mismatch, and
    transport failures. Does not mutate storage. On success, ``raw_bytes`` are
    exactly the bytes returned by ``Storage.read_file`` (no canonicalization).
    """
    validate_game_id(game_id, field_path="game_id")
    paths = GamePaths(game_id)
    try:
        raw = storage.read_file(paths.manifest)
    except StorageNotFoundError:
        return ManifestReadResult(ManifestReadOutcome.MISSING)
    except StorageWrongKindError:
        # A directory (or other wrong kind) at manifest.json is structural
        # corruption requiring repair — not a missing uninitialized match.
        return ManifestReadResult(ManifestReadOutcome.INVALID)
    except StorageTransportError:
        return ManifestReadResult(ManifestReadOutcome.TRANSPORT_FAILURE)
    except StorageError:
        return ManifestReadResult(ManifestReadOutcome.TRANSPORT_FAILURE)

    if type(raw) is not bytes:
        # Storage port contracts bytes; reject other bytes-like values early.
        return ManifestReadResult(ManifestReadOutcome.INVALID)

    try:
        manifest = Manifest.from_json_bytes(raw)
    except DomainValidationError:
        return ManifestReadResult(ManifestReadOutcome.INVALID, raw_bytes=raw)

    if manifest.game_id != game_id:
        return ManifestReadResult(ManifestReadOutcome.GAME_ID_MISMATCH, raw_bytes=raw)

    return ManifestReadResult(ManifestReadOutcome.OK, manifest=manifest, raw_bytes=raw)
