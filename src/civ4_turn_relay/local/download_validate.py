"""Validate a durable downloaded-save record against the local filesystem."""

from __future__ import annotations

from pathlib import Path

from civ4_turn_relay.domain import sha256_hex, validate_windows_local_path
from civ4_turn_relay.local.records import DownloadedSaveRecord, MatchLocalRecords
from civ4_turn_relay.protocol.download import VerifiedDownloadEvidence


def _read_matching_local_save(
    record: DownloadedSaveRecord,
    *,
    pbem_save_directory: str,
    max_save_bytes: int,
) -> bool:
    if record.size_bytes <= 0 or record.size_bytes > max_save_bytes:
        return False
    try:
        validate_windows_local_path(
            pbem_save_directory, field_path="pbem_save_directory"
        )
        validate_windows_local_path(record.local_path, field_path="local_path")
    except Exception:
        return False
    root = Path(pbem_save_directory).resolve(strict=False)
    path = Path(record.local_path)
    try:
        if path.is_symlink() or not path.is_file():
            return False
        resolved = path.resolve(strict=False)
        resolved.relative_to(root)
        data = resolved.read_bytes()
    except (OSError, ValueError):
        return False
    if len(data) != record.size_bytes:
        return False
    return sha256_hex(data) == record.sha256


def validated_prior_download_evidence(
    records: MatchLocalRecords,
    *,
    pbem_save_directory: str,
    protocol_sequence: int,
    accepted_sha256: str | None,
    max_save_bytes: int,
) -> VerifiedDownloadEvidence | None:
    """Return prior evidence only when the local promoted file still matches."""
    downloaded = records.downloaded_save
    if downloaded is None:
        return None
    if downloaded.protocol_sequence != protocol_sequence:
        return None
    if accepted_sha256 is None or downloaded.sha256 != accepted_sha256:
        return None
    if not _read_matching_local_save(
        downloaded,
        pbem_save_directory=pbem_save_directory,
        max_save_bytes=max_save_bytes,
    ):
        return None
    return VerifiedDownloadEvidence(
        game_id=records.game_id,
        protocol_sequence=downloaded.protocol_sequence,
        sha256=downloaded.sha256,
        size_bytes=downloaded.size_bytes,
    )
