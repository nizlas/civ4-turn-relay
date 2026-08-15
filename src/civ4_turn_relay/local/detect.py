"""Outgoing save detection with session-scoped stability sampling (§6.2)."""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from enum import Enum, unique
from pathlib import Path

from civ4_turn_relay.domain import (
    DomainValidationError,
    SaveMatchingRules,
    sha256_hex,
    validate_sha256_hex,
    validate_windows_local_path,
)
from civ4_turn_relay.local.clock import Clock
from civ4_turn_relay.local.records import (
    MatchLocalRecords,
    OutgoingCandidateRecord,
    StabilityObservation,
)

STABILITY_INTERVAL_SECONDS = 1.0
MAX_OBSERVATIONS_PER_PATH = 2


@unique
class DetectionOutcome(Enum):
    """Typed outcomes for outgoing-candidate observation."""

    NO_CANDIDATE = "no_candidate"
    STABILIZING = "stabilizing"
    ONE_CANDIDATE = "one_candidate"
    MULTIPLE_CANDIDATES = "multiple_candidates"
    MISSING_BASELINE = "missing_baseline"
    IO_FAILURE = "io_failure"
    CONTAINMENT_FAILURE = "containment_failure"


@dataclass(frozen=True, slots=True)
class DetectionResult:
    """Immutable detection snapshot; caller persists ``observations``."""

    outcome: DetectionOutcome
    candidates: tuple[OutgoingCandidateRecord, ...] = ()
    observations: tuple[StabilityObservation, ...] = ()
    reason: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, DetectionOutcome):
            raise DomainValidationError(
                "expected a DetectionOutcome",
                field_path="outcome",
            )
        if self.reason is not None and not isinstance(self.reason, str):
            raise DomainValidationError(
                "expected a string reason or None",
                field_path="reason",
            )


def _resolve_contained(root: Path, candidate: Path) -> Path | None:
    root_resolved = root.resolve(strict=False)
    try:
        resolved = candidate.resolve(strict=False)
        resolved.relative_to(root_resolved)
    except (ValueError, OSError):
        return None
    return resolved


def _baseline_index(records: MatchLocalRecords) -> tuple[dict[str, str], set[str]]:
    by_path: dict[str, str] = {}
    hashes: set[str] = set()
    baseline = records.play_session_baseline
    if baseline is None:
        return by_path, hashes
    for entry in baseline.entries:
        by_path[entry.path] = entry.sha256
        hashes.add(entry.sha256)
    return by_path, hashes


def _excluded_hashes(
    records: MatchLocalRecords,
    accepted_save_hashes: tuple[str, ...],
) -> set[str]:
    excluded = set(records.processed_outgoing_hashes)
    excluded.update(accepted_save_hashes)
    if records.downloaded_save is not None:
        excluded.add(records.downloaded_save.sha256)
    return excluded


def _session_key(
    records: MatchLocalRecords,
) -> tuple[int, str | None, str] | None:
    baseline = records.play_session_baseline
    if baseline is None:
        return None
    return (
        baseline.protocol_sequence,
        baseline.accepted_sha256,
        baseline.recorded_at,
    )


def _filter_session_observations(
    observations: tuple[StabilityObservation, ...],
    *,
    session_protocol_sequence: int,
    session_accepted_sha256: str | None,
    session_baseline_recorded_at: str,
) -> tuple[StabilityObservation, ...]:
    return tuple(
        item
        for item in observations
        if item.session_protocol_sequence == session_protocol_sequence
        and item.session_accepted_sha256 == session_accepted_sha256
        and item.session_baseline_recorded_at == session_baseline_recorded_at
    )


def _observations_for_path(
    observations: tuple[StabilityObservation, ...], path: str
) -> tuple[StabilityObservation, ...]:
    return tuple(item for item in observations if item.path == path)


def _merge_observations(
    existing: tuple[StabilityObservation, ...],
    *,
    path: str,
    size_bytes: int,
    mtime_ns: int,
    observed_at: float,
    session_protocol_sequence: int,
    session_accepted_sha256: str | None,
    session_baseline_recorded_at: str,
) -> tuple[StabilityObservation, ...]:
    scoped = _filter_session_observations(
        existing,
        session_protocol_sequence=session_protocol_sequence,
        session_accepted_sha256=session_accepted_sha256,
        session_baseline_recorded_at=session_baseline_recorded_at,
    )
    others = tuple(item for item in scoped if item.path != path)
    path_samples = list(_observations_for_path(scoped, path))
    # Same-size rewrite with a new mtime invalidates prior samples for this path.
    if path_samples and (
        path_samples[-1].size_bytes != size_bytes
        or path_samples[-1].mtime_ns != mtime_ns
    ):
        path_samples = []
    path_samples.append(
        StabilityObservation(
            path=path,
            size_bytes=size_bytes,
            observed_at_seconds=observed_at,
            mtime_ns=mtime_ns,
            session_protocol_sequence=session_protocol_sequence,
            session_accepted_sha256=session_accepted_sha256,
            session_baseline_recorded_at=session_baseline_recorded_at,
        )
    )
    if len(path_samples) > MAX_OBSERVATIONS_PER_PATH:
        # Keep oldest + newest so a later same-time poll cannot erase the
        # stability gap proven by the first sample.
        path_samples = [path_samples[0], path_samples[-1]]
    return (*others, *path_samples)


def _is_stable(
    observations: tuple[StabilityObservation, ...],
    *,
    path: str,
    size_bytes: int,
    mtime_ns: int,
    now_seconds: float,
) -> bool:
    samples = [
        item
        for item in _observations_for_path(observations, path)
        if item.size_bytes == size_bytes and item.mtime_ns == mtime_ns
    ]
    if len(samples) < 2:
        return False
    times = sorted(item.observed_at_seconds for item in samples)
    for earlier, later in zip(times, times[1:], strict=False):
        if later - earlier >= STABILITY_INTERVAL_SECONDS:
            return True
    if now_seconds - samples[0].observed_at_seconds >= STABILITY_INTERVAL_SECONDS:
        return len(samples) >= 2
    return False


def observe_outgoing_candidates(
    pbem_dir: str,
    save_matching: SaveMatchingRules,
    records: MatchLocalRecords,
    accepted_save_hashes: tuple[str, ...],
    *,
    clock: Clock,
    max_save_bytes: int,
) -> DetectionResult:
    """Scan PBEM files, update stability observations, return candidates."""
    session = _session_key(records)
    if session is None:
        return DetectionResult(
            DetectionOutcome.MISSING_BASELINE,
            reason="play_session_baseline_missing",
        )
    session_protocol_sequence, session_accepted_sha256, session_baseline_recorded_at = (
        session
    )

    try:
        validate_windows_local_path(pbem_dir, field_path="pbem_dir")
    except DomainValidationError:
        return DetectionResult(
            DetectionOutcome.CONTAINMENT_FAILURE,
            reason="invalid_pbem_directory",
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

    normalized_hashes = tuple(
        validate_sha256_hex(item, field_path=f"accepted_save_hashes[{index}]")
        for index, item in enumerate(accepted_save_hashes)
    )
    excluded = _excluded_hashes(records, normalized_hashes)
    baseline_by_path, baseline_hashes = _baseline_index(records)

    root = Path(pbem_dir)
    if not root.is_dir():
        return DetectionResult(DetectionOutcome.IO_FAILURE, reason="pbem_dir_missing")

    now_seconds = clock.now()
    observations = _filter_session_observations(
        records.stability_observations,
        session_protocol_sequence=session_protocol_sequence,
        session_accepted_sha256=session_accepted_sha256,
        session_baseline_recorded_at=session_baseline_recorded_at,
    )
    stable_candidates: list[OutgoingCandidateRecord] = []
    saw_potential = False

    for path in root.rglob("*"):
        if path.is_symlink():
            continue
        if not path.is_file():
            continue
        if not fnmatch.fnmatch(path.name, save_matching.filename_glob):
            continue
        contained = _resolve_contained(root, path)
        if contained is None:
            return DetectionResult(
                DetectionOutcome.CONTAINMENT_FAILURE,
                reason="path_escapes_pbem_root",
            )
        try:
            stat_result = contained.stat()
            size = stat_result.st_size
            mtime_ns = int(
                getattr(stat_result, "st_mtime_ns", int(stat_result.st_mtime * 1e9))
            )
        except OSError:
            return DetectionResult(DetectionOutcome.IO_FAILURE, reason="stat_failed")
        if size > max_save_bytes:
            continue

        local_path = validate_windows_local_path(str(contained), field_path="path")
        baseline_hash_at_path = baseline_by_path.get(local_path)
        try:
            data = contained.read_bytes()
        except OSError:
            return DetectionResult(
                DetectionOutcome.IO_FAILURE,
                reason="read_failed",
            )
        digest = sha256_hex(data)
        if (
            baseline_hash_at_path is not None
            and digest == baseline_hash_at_path
            and len(data) == size
        ):
            continue
        # Hash exclusion must be decided before a file counts as potential
        # outgoing activity. An already-processed outgoing save or the
        # downloaded incoming save is known content regardless of whether
        # the session baseline recorded its path; treating it as potential
        # would hold detection in a perpetual STABILIZING state and block
        # the Fully Managed automatic launch. A file mid-write hashes to
        # an unknown digest and still counts as potential.
        if digest in excluded:
            continue
        if baseline_hash_at_path is None and digest in baseline_hashes:
            continue
        saw_potential = True

        observations = _merge_observations(
            observations,
            path=local_path,
            size_bytes=size,
            mtime_ns=mtime_ns,
            observed_at=now_seconds,
            session_protocol_sequence=session_protocol_sequence,
            session_accepted_sha256=session_accepted_sha256,
            session_baseline_recorded_at=session_baseline_recorded_at,
        )
        if not _is_stable(
            observations,
            path=local_path,
            size_bytes=size,
            mtime_ns=mtime_ns,
            now_seconds=now_seconds,
        ):
            continue

        try:
            data = contained.read_bytes()
        except OSError:
            return DetectionResult(DetectionOutcome.IO_FAILURE, reason="read_failed")
        if len(data) != size:
            continue
        digest = sha256_hex(data)
        if digest in excluded:
            continue
        if baseline_hash_at_path is None and digest in baseline_hashes:
            continue

        stable_candidates.append(
            OutgoingCandidateRecord(
                path=local_path,
                sha256=digest,
                size_bytes=size,
            )
        )

    if len(stable_candidates) > 1:
        return DetectionResult(
            DetectionOutcome.MULTIPLE_CANDIDATES,
            candidates=tuple(stable_candidates),
            observations=observations,
            reason="multiple_stable_candidates",
        )
    if len(stable_candidates) == 1:
        return DetectionResult(
            DetectionOutcome.ONE_CANDIDATE,
            candidates=tuple(stable_candidates),
            observations=observations,
        )
    if saw_potential:
        return DetectionResult(
            DetectionOutcome.STABILIZING,
            observations=observations,
            reason="awaiting_stability",
        )
    return DetectionResult(
        DetectionOutcome.NO_CANDIDATE,
        observations=observations,
    )


def revalidate_candidate_file(
    candidate: OutgoingCandidateRecord,
    *,
    pbem_save_directory: str,
    max_save_bytes: int,
) -> bytes | None:
    """Re-read the candidate; return bytes only when path/size/hash still match."""
    try:
        validate_windows_local_path(
            pbem_save_directory, field_path="pbem_save_directory"
        )
        validate_windows_local_path(candidate.path, field_path="path")
    except DomainValidationError:
        return None
    root = Path(pbem_save_directory).resolve(strict=False)
    path = Path(candidate.path)
    try:
        if path.is_symlink() or not path.is_file():
            return None
        resolved = path.resolve(strict=False)
        resolved.relative_to(root)
        data = resolved.read_bytes()
    except (OSError, ValueError):
        return None
    if len(data) > max_save_bytes:
        return None
    if len(data) != candidate.size_bytes:
        return None
    if sha256_hex(data) != candidate.sha256:
        return None
    return data
