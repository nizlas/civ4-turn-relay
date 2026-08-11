"""Atomic local promotion of a verified downloaded save into the PBEM directory."""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum, unique
from pathlib import Path

from civ4_turn_relay.domain import (
    DomainValidationError,
    sha256_hex,
    validate_original_filename,
    validate_windows_local_path,
)
from civ4_turn_relay.local.records import DownloadedSaveRecord
from civ4_turn_relay.protocol.download import VerifiedDownloadArtifact

ReplaceFn = Callable[[str, str], None]
FsyncFn = Callable[[int], None]


@unique
class PromoteOutcome(Enum):
    """Typed outcomes for verified-download promotion."""

    PROMOTED = "promoted"
    ALREADY_PRESENT = "already_present"
    CONFLICT = "conflict"
    PATH_VIOLATION = "path_violation"
    IO_FAILURE = "io_failure"
    VERIFY_FAILURE = "verify_failure"


@dataclass(frozen=True, slots=True)
class PromoteResult:
    """Immutable promotion result."""

    outcome: PromoteOutcome
    record: DownloadedSaveRecord | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, PromoteOutcome):
            raise DomainValidationError(
                "expected a PromoteOutcome",
                field_path="outcome",
            )
        if self.outcome in {PromoteOutcome.PROMOTED, PromoteOutcome.ALREADY_PRESENT}:
            if self.record is None:
                raise DomainValidationError(
                    f"{self.outcome.value} requires a DownloadedSaveRecord",
                    field_path="record",
                )
        elif self.record is not None:
            raise DomainValidationError(
                "only promoted or already-present outcomes may carry a record",
                field_path="record",
            )


def _resolve_destination(pbem_dir: Path, original_filename: str) -> Path | None:
    try:
        basename = validate_original_filename(original_filename)
        root = pbem_dir.resolve(strict=False)
        destination = (root / basename).resolve(strict=False)
        destination.relative_to(root)
        return destination
    except (DomainValidationError, ValueError):
        return None


def promote_verified_download(
    artifact: VerifiedDownloadArtifact,
    pbem_save_directory: str,
    *,
    replace_fn: ReplaceFn = os.replace,
    fsync_fn: FsyncFn = os.fsync,
) -> PromoteResult:
    """Promote verified bytes into the match PBEM directory atomically."""
    if not isinstance(artifact, VerifiedDownloadArtifact):
        raise TypeError("artifact must be a VerifiedDownloadArtifact")

    try:
        validate_windows_local_path(
            pbem_save_directory, field_path="pbem_save_directory"
        )
    except DomainValidationError:
        return PromoteResult(PromoteOutcome.PATH_VIOLATION)

    pbem_dir = Path(pbem_save_directory)
    destination = _resolve_destination(pbem_dir, artifact.original_filename)
    if destination is None:
        return PromoteResult(PromoteOutcome.PATH_VIOLATION)

    if destination.is_file():
        try:
            existing = destination.read_bytes()
        except OSError:
            return PromoteResult(PromoteOutcome.IO_FAILURE)
        if existing == artifact.verified_bytes:
            try:
                local_path = validate_windows_local_path(
                    str(destination), field_path="local_path"
                )
            except DomainValidationError:
                return PromoteResult(PromoteOutcome.PATH_VIOLATION)
            return PromoteResult(
                PromoteOutcome.ALREADY_PRESENT,
                record=DownloadedSaveRecord(
                    local_path=local_path,
                    sha256=artifact.sha256,
                    size_bytes=artifact.size_bytes,
                    protocol_sequence=artifact.protocol_sequence,
                ),
            )
        return PromoteResult(PromoteOutcome.CONFLICT)

    parent = destination.parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return PromoteResult(PromoteOutcome.IO_FAILURE)

    temporary = parent / f".{destination.name}.{uuid.uuid4().hex}.tmp"
    try:
        try:
            with temporary.open("wb") as handle:
                handle.write(artifact.verified_bytes)
                handle.flush()
                fsync_fn(handle.fileno())
        except OSError:
            return PromoteResult(PromoteOutcome.IO_FAILURE)
        try:
            replace_fn(str(temporary), str(destination))
        except OSError:
            return PromoteResult(PromoteOutcome.IO_FAILURE)
    finally:
        if temporary.exists():
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass

    try:
        reread = destination.read_bytes()
    except OSError:
        return PromoteResult(PromoteOutcome.IO_FAILURE)
    if len(reread) != artifact.size_bytes or sha256_hex(reread) != artifact.sha256:
        return PromoteResult(PromoteOutcome.VERIFY_FAILURE)

    try:
        local_path = validate_windows_local_path(
            str(destination), field_path="local_path"
        )
    except DomainValidationError:
        return PromoteResult(PromoteOutcome.PATH_VIOLATION)

    return PromoteResult(
        PromoteOutcome.PROMOTED,
        record=DownloadedSaveRecord(
            local_path=local_path,
            sha256=artifact.sha256,
            size_bytes=artifact.size_bytes,
            protocol_sequence=artifact.protocol_sequence,
        ),
    )
