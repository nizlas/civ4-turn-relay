"""Verified download of the currently accepted save (protocol §5).

P3/P4 boundary: this module produces immutable verified evidence and bytes
only. It never writes local filesystem paths, promotes a playable save, or
persists durable download records. P4 owns local atomic promotion and journal
persistence from a successful :class:`VerifiedDownloadArtifact`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, unique

from civ4_turn_relay.domain import (
    DomainValidationError,
    sha256_hex,
    validate_accepted_save_path,
    validate_game_id,
    validate_player_id,
    validate_sha256_hex,
)
from civ4_turn_relay.protocol.handoff import DEFAULT_MAX_SAVE_BYTES
from civ4_turn_relay.protocol.manifest_access import (
    ManifestReadOutcome,
    read_authoritative_manifest,
)
from civ4_turn_relay.protocol.paths import GamePaths
from civ4_turn_relay.storage import (
    Storage,
    StorageError,
    StorageNotFoundError,
    StorageTransportError,
    StorageWrongKindError,
)


@unique
class DownloadOutcome(Enum):
    """Typed outcomes for verified accepted-save download."""

    VERIFIED = "verified"
    ALREADY_VERIFIED = "already_verified"
    NOT_CURRENT_OWNER = "not_current_owner"
    NO_DOWNLOADABLE_TURN = "no_downloadable_turn"
    HASH_MISMATCH = "hash_mismatch"
    SIZE_MISMATCH = "size_mismatch"
    PATH_VIOLATION = "path_violation"
    WRONG_KIND = "wrong_kind"
    OVERSIZE = "oversize"
    MISSING_SAVE = "missing_save"
    INVALID_MANIFEST = "invalid_manifest"
    MISSING_MANIFEST = "missing_manifest"
    TRANSPORT_FAILURE = "transport_failure"
    CAPABILITY_FAILURE = "capability_failure"


@dataclass(frozen=True, slots=True)
class VerifiedDownloadEvidence:
    """Caller-supplied prior verification for sequence + hash + size.

    Pure evidence only — not a durable journal backend. Stale or incomplete
    evidence MUST NOT suppress a necessary download.
    """

    game_id: str
    protocol_sequence: int
    sha256: str
    size_bytes: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "game_id", validate_game_id(self.game_id, field_path="game_id")
        )
        if isinstance(self.protocol_sequence, bool) or not isinstance(
            self.protocol_sequence, int
        ):
            raise DomainValidationError(
                "expected an integer protocol_sequence",
                field_path="protocol_sequence",
            )
        if self.protocol_sequence < 1:
            raise DomainValidationError(
                "protocol_sequence for verified evidence must be >= 1",
                field_path="protocol_sequence",
            )
        object.__setattr__(
            self, "sha256", validate_sha256_hex(self.sha256, field_path="sha256")
        )
        if isinstance(self.size_bytes, bool) or not isinstance(self.size_bytes, int):
            raise DomainValidationError(
                "expected an integer size_bytes",
                field_path="size_bytes",
            )
        if self.size_bytes <= 0:
            raise DomainValidationError(
                "size_bytes must be positive",
                field_path="size_bytes",
            )


@dataclass(frozen=True, slots=True)
class VerifiedDownloadArtifact:
    """Immutable verified save bytes for later P4 local promotion."""

    game_id: str
    protocol_sequence: int
    sha256: str
    size_bytes: int
    remote_path: str
    original_filename: str
    verified_bytes: bytes

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "game_id", validate_game_id(self.game_id, field_path="game_id")
        )
        if isinstance(self.protocol_sequence, bool) or not isinstance(
            self.protocol_sequence, int
        ):
            raise DomainValidationError(
                "expected an integer protocol_sequence",
                field_path="protocol_sequence",
            )
        if self.protocol_sequence < 1:
            raise DomainValidationError(
                "protocol_sequence must be >= 1",
                field_path="protocol_sequence",
            )
        object.__setattr__(
            self, "sha256", validate_sha256_hex(self.sha256, field_path="sha256")
        )
        if isinstance(self.size_bytes, bool) or not isinstance(self.size_bytes, int):
            raise DomainValidationError(
                "expected an integer size_bytes",
                field_path="size_bytes",
            )
        if self.size_bytes <= 0:
            raise DomainValidationError(
                "size_bytes must be positive",
                field_path="size_bytes",
            )
        object.__setattr__(
            self,
            "remote_path",
            validate_accepted_save_path(self.remote_path, field_path="remote_path"),
        )
        if not isinstance(self.original_filename, str) or not self.original_filename:
            raise DomainValidationError(
                "expected a non-empty original_filename",
                field_path="original_filename",
            )
        if type(self.verified_bytes) is not bytes:
            raise DomainValidationError(
                "verified_bytes must be exact bytes",
                field_path="verified_bytes",
            )
        if len(self.verified_bytes) != self.size_bytes:
            raise DomainValidationError(
                "verified_bytes length must equal size_bytes",
                field_path="verified_bytes",
            )
        if sha256_hex(self.verified_bytes) != self.sha256:
            raise DomainValidationError(
                "verified_bytes digest must equal sha256",
                field_path="verified_bytes",
            )


@dataclass(frozen=True, slots=True)
class DownloadRequest:
    """Validated inputs for a verified download attempt."""

    game_id: str
    local_player_id: str
    max_save_bytes: int = DEFAULT_MAX_SAVE_BYTES
    prior_evidence: VerifiedDownloadEvidence | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "game_id", validate_game_id(self.game_id, field_path="game_id")
        )
        object.__setattr__(
            self,
            "local_player_id",
            validate_player_id(self.local_player_id, field_path="local_player_id"),
        )
        if isinstance(self.max_save_bytes, bool) or not isinstance(
            self.max_save_bytes, int
        ):
            raise DomainValidationError(
                "expected an integer max_save_bytes",
                field_path="max_save_bytes",
            )
        if self.max_save_bytes <= 0 or self.max_save_bytes > DEFAULT_MAX_SAVE_BYTES:
            raise DomainValidationError(
                f"max_save_bytes must be in 1..{DEFAULT_MAX_SAVE_BYTES}",
                field_path="max_save_bytes",
            )
        if self.prior_evidence is not None and not isinstance(
            self.prior_evidence, VerifiedDownloadEvidence
        ):
            raise DomainValidationError(
                "expected VerifiedDownloadEvidence or None",
                field_path="prior_evidence",
            )
        if (
            self.prior_evidence is not None
            and self.prior_evidence.game_id != self.game_id
        ):
            raise DomainValidationError(
                "prior_evidence.game_id must match request.game_id",
                field_path="prior_evidence.game_id",
            )


@dataclass(frozen=True, slots=True)
class DownloadResult:
    """Immutable download result; failures never carry a verified artifact.

    ``ALREADY_VERIFIED`` means prior evidence still matches the authoritative
    manifest; no save-object read occurred and no new artifact is returned
    (P4 already holds the locally promoted bytes).
    """

    outcome: DownloadOutcome
    artifact: VerifiedDownloadArtifact | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, DownloadOutcome):
            raise DomainValidationError(
                "expected a DownloadOutcome",
                field_path="outcome",
            )
        if self.artifact is not None and not isinstance(
            self.artifact, VerifiedDownloadArtifact
        ):
            raise DomainValidationError(
                "expected a VerifiedDownloadArtifact",
                field_path="artifact",
            )
        if self.outcome is DownloadOutcome.VERIFIED:
            if self.artifact is None:
                raise DomainValidationError(
                    "VERIFIED requires a verified artifact",
                    field_path="artifact",
                )
        elif self.artifact is not None:
            raise DomainValidationError(
                "only VERIFIED may carry a verified artifact",
                field_path="artifact",
            )


def download_accepted_save(
    storage: Storage, request: DownloadRequest
) -> DownloadResult:
    """Verify and return the current accepted save when the local player owns it."""
    if not isinstance(request, DownloadRequest):
        raise TypeError("request must be a DownloadRequest")

    caps = storage.capabilities()
    if not caps.complete_readback:
        return DownloadResult(DownloadOutcome.CAPABILITY_FAILURE)

    read = read_authoritative_manifest(storage, request.game_id)
    if read.outcome is ManifestReadOutcome.MISSING:
        return DownloadResult(DownloadOutcome.MISSING_MANIFEST)
    if read.outcome in {
        ManifestReadOutcome.INVALID,
        ManifestReadOutcome.GAME_ID_MISMATCH,
    }:
        return DownloadResult(DownloadOutcome.INVALID_MANIFEST)
    if read.outcome is ManifestReadOutcome.TRANSPORT_FAILURE or read.manifest is None:
        return DownloadResult(DownloadOutcome.TRANSPORT_FAILURE)

    manifest = read.manifest
    if manifest.current_player_id != request.local_player_id:
        return DownloadResult(DownloadOutcome.NOT_CURRENT_OWNER)
    if manifest.protocol_sequence == 0 or manifest.accepted_save is None:
        return DownloadResult(DownloadOutcome.NO_DOWNLOADABLE_TURN)

    accepted = manifest.accepted_save
    if accepted.size_bytes > request.max_save_bytes:
        return DownloadResult(DownloadOutcome.OVERSIZE)

    evidence = request.prior_evidence
    if (
        evidence is not None
        and evidence.protocol_sequence == manifest.protocol_sequence
        and evidence.sha256 == accepted.sha256
        and evidence.size_bytes == accepted.size_bytes
    ):
        return DownloadResult(DownloadOutcome.ALREADY_VERIFIED)

    try:
        remote_relative = validate_accepted_save_path(
            accepted.remote_path, field_path="accepted_save.remote_path"
        )
    except DomainValidationError:
        return DownloadResult(DownloadOutcome.PATH_VIOLATION)

    paths = GamePaths(request.game_id)
    try:
        storage_path = paths.resolve(remote_relative)
    except DomainValidationError:
        return DownloadResult(DownloadOutcome.PATH_VIOLATION)

    try:
        data = storage.read_file(storage_path)
    except StorageNotFoundError:
        return DownloadResult(DownloadOutcome.MISSING_SAVE)
    except StorageWrongKindError:
        return DownloadResult(DownloadOutcome.WRONG_KIND)
    except StorageTransportError:
        return DownloadResult(DownloadOutcome.TRANSPORT_FAILURE)
    except StorageError:
        return DownloadResult(DownloadOutcome.TRANSPORT_FAILURE)

    if type(data) is not bytes:
        return DownloadResult(DownloadOutcome.WRONG_KIND)
    if len(data) > request.max_save_bytes:
        return DownloadResult(DownloadOutcome.OVERSIZE)
    if len(data) != accepted.size_bytes:
        return DownloadResult(DownloadOutcome.SIZE_MISMATCH)
    digest = sha256_hex(data)
    if digest != accepted.sha256:
        return DownloadResult(DownloadOutcome.HASH_MISMATCH)

    return DownloadResult(
        DownloadOutcome.VERIFIED,
        artifact=VerifiedDownloadArtifact(
            game_id=manifest.game_id,
            protocol_sequence=manifest.protocol_sequence,
            sha256=digest,
            size_bytes=len(data),
            remote_path=remote_relative,
            original_filename=accepted.original_filename,
            verified_bytes=data,
        ),
    )
