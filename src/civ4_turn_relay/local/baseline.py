"""Play-session baseline capture before Civ launch (protocol §6.1)."""

from __future__ import annotations

import fnmatch
from pathlib import Path

from civ4_turn_relay.domain import (
    DomainValidationError,
    SaveMatchingRules,
    sha256_hex,
    validate_windows_local_path,
)
from civ4_turn_relay.local.records import BaselineEntry, PlaySessionBaseline


def _resolve_contained(root: Path, candidate: Path) -> Path | None:
    root_resolved = root.resolve(strict=False)
    try:
        resolved = candidate.resolve(strict=False)
        resolved.relative_to(root_resolved)
    except (ValueError, OSError):
        return None
    return resolved


def capture_play_session_baseline(
    pbem_dir: str,
    save_matching: SaveMatchingRules,
    *,
    protocol_sequence: int,
    accepted_sha256: str | None,
    recorded_at: str,
    max_save_bytes: int,
) -> PlaySessionBaseline:
    """Scan matching PBEM files and record path/size/sha256 snapshots."""
    validate_windows_local_path(pbem_dir, field_path="pbem_dir")
    if isinstance(protocol_sequence, bool) or not isinstance(protocol_sequence, int):
        raise DomainValidationError(
            "expected an integer protocol_sequence",
            field_path="protocol_sequence",
        )
    if isinstance(max_save_bytes, bool) or not isinstance(max_save_bytes, int):
        raise DomainValidationError(
            "expected an integer max_save_bytes",
            field_path="max_save_bytes",
        )
    if max_save_bytes <= 0:
        raise DomainValidationError(
            "max_save_bytes must be positive",
            field_path="max_save_bytes",
        )

    root = Path(pbem_dir)
    if not root.is_dir():
        raise DomainValidationError(
            "PBEM directory is not accessible",
            field_path="pbem_dir",
        )

    entries: list[BaselineEntry] = []
    for path in root.rglob("*"):
        if path.is_symlink():
            continue
        if not path.is_file():
            continue
        if not fnmatch.fnmatch(path.name, save_matching.filename_glob):
            continue
        contained = _resolve_contained(root, path)
        if contained is None:
            raise DomainValidationError(
                "matched file escapes PBEM directory",
                field_path="pbem_dir",
            )
        try:
            size = contained.stat().st_size
        except OSError as error:
            raise DomainValidationError(
                "failed to stat matched file",
                field_path="pbem_dir",
            ) from error
        if size > max_save_bytes:
            continue
        try:
            data = contained.read_bytes()
        except OSError as error:
            raise DomainValidationError(
                "failed to read matched file",
                field_path="pbem_dir",
            ) from error
        if len(data) != size:
            continue
        local_path = validate_windows_local_path(str(contained), field_path="path")
        entries.append(
            BaselineEntry(
                path=local_path,
                sha256=sha256_hex(data),
                size_bytes=size,
            )
        )

    entries.sort(key=lambda item: item.path)
    return PlaySessionBaseline(
        recorded_at=recorded_at,
        protocol_sequence=protocol_sequence,
        accepted_sha256=accepted_sha256,
        entries=tuple(entries),
    )
