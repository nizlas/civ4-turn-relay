"""Safe temporary-orphan cleanup under ``{game_id}/temporary/`` (protocol §11).

Deletes only explicit caller-supplied candidates after containment checks.
Never mutates ``manifest.json``, saves, history, or locks. Active upload-lock
``operation_id`` temps are protected. Age detection and unreferenced final-save
cleanup are out of scope for P3.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum, unique

from civ4_turn_relay.domain import (
    DomainValidationError,
    validate_game_id,
    validate_remote_relative_path,
)
from civ4_turn_relay.protocol.lock import LockInspectionKind, inspect_upload_lock
from civ4_turn_relay.protocol.paths import TEMPORARY_DIR, GamePaths
from civ4_turn_relay.storage import (
    Storage,
    StorageError,
    StorageNotFoundError,
    StorageWrongKindError,
)


@unique
class TemporaryCleanupOutcome(Enum):
    """Aggregate outcome for an explicit temporary cleanup attempt."""

    COMPLETED = "completed"
    LOCK_UNSAFE = "lock_unsafe"
    PATH_VIOLATION = "path_violation"
    TRANSPORT_FAILURE = "transport_failure"


@unique
class TemporaryCandidateAction(Enum):
    """Per-candidate cleanup classification."""

    REMOVED = "removed"
    MISSING = "missing"
    PROTECTED = "protected"
    WRONG_KIND = "wrong_kind"
    TRANSPORT_FAILURE = "transport_failure"
    PATH_VIOLATION = "path_violation"
    SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class TemporaryCandidateResult:
    """Immutable per-candidate cleanup result."""

    candidate: str
    storage_path: str | None
    action: TemporaryCandidateAction

    def __post_init__(self) -> None:
        if not isinstance(self.candidate, str) or not self.candidate:
            raise DomainValidationError(
                "expected a non-empty candidate path",
                field_path="candidate",
            )
        if self.storage_path is not None and (
            not isinstance(self.storage_path, str) or not self.storage_path
        ):
            raise DomainValidationError(
                "storage_path must be a non-empty string when present",
                field_path="storage_path",
            )
        if not isinstance(self.action, TemporaryCandidateAction):
            raise DomainValidationError(
                "expected a TemporaryCandidateAction",
                field_path="action",
            )


@dataclass(frozen=True, slots=True)
class TemporaryCleanupResult:
    """Immutable aggregate cleanup result."""

    outcome: TemporaryCleanupOutcome
    items: tuple[TemporaryCandidateResult, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, TemporaryCleanupOutcome):
            raise DomainValidationError(
                "expected a TemporaryCleanupOutcome",
                field_path="outcome",
            )
        if not isinstance(self.items, tuple):
            raise DomainValidationError(
                "items must be a tuple",
                field_path="items",
            )
        for index, item in enumerate(self.items):
            if not isinstance(item, TemporaryCandidateResult):
                raise DomainValidationError(
                    "expected TemporaryCandidateResult",
                    field_path=f"items[{index}]",
                )


def cleanup_temporary_orphans(
    storage: Storage,
    *,
    game_id: str,
    candidates: Sequence[str],
) -> TemporaryCleanupResult:
    """Remove explicit temporary candidates if safe relative to the upload lock."""
    validate_game_id(game_id, field_path="game_id")
    if not isinstance(candidates, Sequence) or isinstance(candidates, (str, bytes)):
        raise TypeError("candidates must be a sequence of path strings")

    paths = GamePaths(game_id)
    resolved: list[tuple[str, str]] = []
    violations: list[TemporaryCandidateResult] = []
    for candidate in candidates:
        if not isinstance(candidate, str):
            raise DomainValidationError(
                "candidate must be a string path",
                field_path="candidates",
            )
        storage_path = _resolve_temporary_candidate(paths, candidate)
        if storage_path is None:
            violations.append(
                TemporaryCandidateResult(
                    candidate=candidate,
                    storage_path=None,
                    action=TemporaryCandidateAction.PATH_VIOLATION,
                )
            )
        else:
            resolved.append((candidate, storage_path))

    if violations:
        skipped = tuple(
            TemporaryCandidateResult(
                candidate=candidate,
                storage_path=storage_path,
                action=TemporaryCandidateAction.SKIPPED,
            )
            for candidate, storage_path in resolved
        )
        return TemporaryCleanupResult(
            TemporaryCleanupOutcome.PATH_VIOLATION,
            (*violations, *skipped),
        )

    inspection = inspect_upload_lock(storage, game_id)
    if inspection.kind in {
        LockInspectionKind.TRANSPORT_FAILURE,
        LockInspectionKind.MALFORMED,
        LockInspectionKind.MISSING_LOCK_JSON,
        LockInspectionKind.WRONG_KIND,
    }:
        return TemporaryCleanupResult(
            TemporaryCleanupOutcome.LOCK_UNSAFE,
            tuple(
                TemporaryCandidateResult(
                    candidate=candidate,
                    storage_path=storage_path,
                    action=TemporaryCandidateAction.SKIPPED,
                )
                for candidate, storage_path in resolved
            ),
        )

    protected_operation_id: str | None = None
    if (
        inspection.kind is LockInspectionKind.READABLE
        and inspection.document is not None
    ):
        protected_operation_id = inspection.document.operation_id

    items: list[TemporaryCandidateResult] = []
    for candidate, storage_path in resolved:
        basename = storage_path.rsplit("/", 1)[-1]
        if protected_operation_id is not None and _protected_by_operation(
            basename, protected_operation_id
        ):
            items.append(
                TemporaryCandidateResult(
                    candidate=candidate,
                    storage_path=storage_path,
                    action=TemporaryCandidateAction.PROTECTED,
                )
            )
            continue
        try:
            storage.remove_file(storage_path)
        except StorageNotFoundError:
            items.append(
                TemporaryCandidateResult(
                    candidate=candidate,
                    storage_path=storage_path,
                    action=TemporaryCandidateAction.MISSING,
                )
            )
            continue
        except StorageWrongKindError:
            items.append(
                TemporaryCandidateResult(
                    candidate=candidate,
                    storage_path=storage_path,
                    action=TemporaryCandidateAction.WRONG_KIND,
                )
            )
            continue
        except StorageError:
            return TemporaryCleanupResult(
                TemporaryCleanupOutcome.TRANSPORT_FAILURE,
                (
                    *items,
                    TemporaryCandidateResult(
                        candidate=candidate,
                        storage_path=storage_path,
                        action=TemporaryCandidateAction.TRANSPORT_FAILURE,
                    ),
                    *(
                        TemporaryCandidateResult(
                            candidate=other_candidate,
                            storage_path=other_path,
                            action=TemporaryCandidateAction.SKIPPED,
                        )
                        for other_candidate, other_path in resolved[len(items) + 1 :]
                    ),
                ),
            )
        items.append(
            TemporaryCandidateResult(
                candidate=candidate,
                storage_path=storage_path,
                action=TemporaryCandidateAction.REMOVED,
            )
        )

    return TemporaryCleanupResult(TemporaryCleanupOutcome.COMPLETED, tuple(items))


def _resolve_temporary_candidate(paths: GamePaths, candidate: str) -> str | None:
    """Return a storage path under ``temporary/`` or None if containment fails."""
    prefix = f"{paths.temporary}/"
    if candidate.startswith(prefix):
        storage_path = candidate
    else:
        try:
            relative = validate_remote_relative_path(candidate, field_path="candidate")
        except DomainValidationError:
            return None
        parts = relative.split("/")
        if len(parts) != 2 or parts[0] != TEMPORARY_DIR:
            return None
        try:
            storage_path = paths.resolve(relative)
        except DomainValidationError:
            return None

    if not storage_path.startswith(prefix):
        return None
    remainder = storage_path[len(prefix) :]
    if not remainder or "/" in remainder or remainder in {".", ".."}:
        return None
    return storage_path


def _protected_by_operation(basename: str, operation_id: str) -> bool:
    if basename.startswith(f"{operation_id}."):
        return True
    if basename == f"manifest-{operation_id}.json":
        return True
    if basename == f"history-{operation_id}.json":
        return True
    return False
