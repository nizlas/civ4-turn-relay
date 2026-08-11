"""Full object read-back and fingerprint helpers.

Size alone is never sufficient: callers compare exact byte length and
lowercase SHA-256 (via the P1 helper) before reuse. These helpers never
overwrite, repair, or accept objects — they only report facts.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, unique

from civ4_turn_relay.domain.hashing import sha256_hex
from civ4_turn_relay.storage.port import Storage


@dataclass(frozen=True, slots=True)
class ObjectFingerprint:
    """Immutable result of a complete object read-back."""

    path: str
    size_bytes: int
    sha256: str
    content: bytes


@unique
class ObjectComparisonResult(Enum):
    """Outcome of comparing a stored object to expected size and digest."""

    EXACT_MATCH = "exact_match"
    MISMATCH = "mismatch"


def fingerprint_bytes(path: str, data: bytes) -> ObjectFingerprint:
    """Build a fingerprint from already-read bytes."""
    return ObjectFingerprint(
        path=path,
        size_bytes=len(data),
        sha256=sha256_hex(data),
        content=data,
    )


def read_fingerprint(storage: Storage, path: str) -> ObjectFingerprint:
    """Read the entire stored object and return its fingerprint."""
    return fingerprint_bytes(path, storage.read_file(path))


def compare_stored_object(
    storage: Storage,
    path: str,
    *,
    expected_size: int,
    expected_sha256: str,
) -> ObjectComparisonResult:
    """Compare a stored object to expected size and SHA-256.

    Returns :attr:`ObjectComparisonResult.EXACT_MATCH` only when both match.
    Does not mutate storage and does not interpret protocol meaning.
    """
    fingerprint = read_fingerprint(storage, path)
    if (
        fingerprint.size_bytes == expected_size
        and fingerprint.sha256 == expected_sha256
    ):
        return ObjectComparisonResult.EXACT_MATCH
    return ObjectComparisonResult.MISMATCH
