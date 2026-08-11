"""Upload-lock lifecycle (protocol §7.1).

Acquire only via exclusive ``mkdir``. TTL/age is informational and never
authorizes automatic foreign-lock deletion. Own-lock resume requires journal
agreement on ``operation_id``, ``client_id``, ``player_id``, and outgoing hash
(via journal). Abandoned-lock repair uses an immutable preview token and never
removes transport-failure observations.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum, unique

from civ4_turn_relay.domain import (
    DomainValidationError,
    add_utc_seconds,
    parse_json_object_bytes,
    to_canonical_json_bytes,
    validate_client_id,
    validate_game_id,
    validate_operation_id,
    validate_player_id,
    validate_utc_timestamp,
)
from civ4_turn_relay.domain.serialization import (
    check_exact_keys,
    get_string,
)
from civ4_turn_relay.protocol.journal import HandoffJournal, InProgressHandoff
from civ4_turn_relay.protocol.paths import GamePaths
from civ4_turn_relay.storage import (
    Storage,
    StorageAlreadyExistsError,
    StorageCapabilityError,
    StorageError,
    StorageNotFoundError,
    StorageTransportError,
    StorageWrongKindError,
)

LOCK_TTL_SECONDS = 15 * 60
"""Informational abandoned-lock threshold only; never auto-deletes."""

_LOCK_KEYS = (
    "client_id",
    "created_at",
    "expires_at",
    "operation_id",
    "player_id",
)


@dataclass(frozen=True, slots=True)
class LockDocument:
    """Strict, immutable ``lock.json`` contents."""

    operation_id: str
    client_id: str
    player_id: str
    created_at: str
    expires_at: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "operation_id",
            validate_operation_id(self.operation_id, field_path="operation_id"),
        )
        object.__setattr__(
            self,
            "client_id",
            validate_client_id(self.client_id, field_path="client_id"),
        )
        object.__setattr__(
            self,
            "player_id",
            validate_player_id(self.player_id, field_path="player_id"),
        )
        object.__setattr__(
            self,
            "created_at",
            validate_utc_timestamp(self.created_at, field_path="created_at"),
        )
        object.__setattr__(
            self,
            "expires_at",
            validate_utc_timestamp(self.expires_at, field_path="expires_at"),
        )

    def to_mapping(self) -> dict[str, object]:
        return {
            "client_id": self.client_id,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "operation_id": self.operation_id,
            "player_id": self.player_id,
        }

    def to_json_bytes(self) -> bytes:
        return to_canonical_json_bytes(self.to_mapping())

    @classmethod
    def from_mapping(
        cls, mapping: Mapping[str, object], *, path: str = ""
    ) -> LockDocument:
        check_exact_keys(mapping, _LOCK_KEYS, path=path)
        try:
            return cls(
                operation_id=get_string(mapping, "operation_id", path=path),
                client_id=get_string(mapping, "client_id", path=path),
                player_id=get_string(mapping, "player_id", path=path),
                created_at=get_string(mapping, "created_at", path=path),
                expires_at=get_string(mapping, "expires_at", path=path),
            )
        except DomainValidationError as error:
            raise error.with_prefix(path) from None

    @classmethod
    def from_json_bytes(cls, data: bytes) -> LockDocument:
        if type(data) is not bytes:
            raise DomainValidationError(
                "expected exact bytes",
                field_path="lock_json",
            )
        return cls.from_mapping(parse_json_object_bytes(data))

    def matches_owner(
        self, *, operation_id: str, client_id: str, player_id: str
    ) -> bool:
        return (
            self.operation_id
            == validate_operation_id(operation_id, field_path="operation_id")
            and self.client_id == validate_client_id(client_id, field_path="client_id")
            and self.player_id == validate_player_id(player_id, field_path="player_id")
        )


def build_lock_document(
    *,
    operation_id: str,
    client_id: str,
    player_id: str,
    now_utc: str,
) -> LockDocument:
    """Build a lock document from injected ``now_utc`` (no wall clock)."""
    created_at = validate_utc_timestamp(now_utc, field_path="now_utc")
    expires_at = add_utc_seconds(created_at, LOCK_TTL_SECONDS, field_path="now_utc")
    return LockDocument(
        operation_id=operation_id,
        client_id=client_id,
        player_id=player_id,
        created_at=created_at,
        expires_at=expires_at,
    )


@unique
class LockAcquireOutcome(Enum):
    """Outcome of attempting to acquire or resume the upload lock."""

    ACQUIRED = "acquired"
    RESUMED = "resumed"
    FOREIGN_HELD = "foreign_held"
    UNREADABLE = "unreadable"
    CAPABILITY_FAILURE = "capability_failure"
    TRANSPORT_FAILURE = "transport_failure"


@dataclass(frozen=True, slots=True)
class LockAcquireResult:
    """Immutable result of lock acquisition / resume."""

    outcome: LockAcquireOutcome
    document: LockDocument | None = None
    owned: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, LockAcquireOutcome):
            raise DomainValidationError(
                "expected a LockAcquireOutcome",
                field_path="outcome",
            )
        if self.document is not None and not isinstance(self.document, LockDocument):
            raise DomainValidationError(
                "expected a LockDocument",
                field_path="document",
            )
        if type(self.owned) is not bool:
            raise DomainValidationError(
                "expected an exact boolean",
                field_path="owned",
            )
        owned_outcomes = {
            LockAcquireOutcome.ACQUIRED,
            LockAcquireOutcome.RESUMED,
        }
        if self.outcome in owned_outcomes:
            if not self.owned or self.document is None:
                raise DomainValidationError(
                    "ACQUIRED/RESUMED require owned=True and a document",
                    field_path="owned",
                )
        elif self.owned:
            raise DomainValidationError(
                "owned=True is only valid for ACQUIRED/RESUMED",
                field_path="owned",
            )


@unique
class LockInspectionKind(Enum):
    """Observed state of ``locks/upload.lock/``."""

    ABSENT = "absent"
    READABLE = "readable"
    MISSING_LOCK_JSON = "missing_lock_json"
    MALFORMED = "malformed"
    TRANSPORT_FAILURE = "transport_failure"


@dataclass(frozen=True, slots=True)
class LockInspection:
    """Observation of the remote upload lock (no mutations)."""

    kind: LockInspectionKind
    document: LockDocument | None = None
    raw_bytes: bytes | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, LockInspectionKind):
            raise DomainValidationError(
                "expected a LockInspectionKind",
                field_path="kind",
            )
        if self.document is not None and not isinstance(self.document, LockDocument):
            raise DomainValidationError(
                "expected a LockDocument",
                field_path="document",
            )
        if self.raw_bytes is not None and type(self.raw_bytes) is not bytes:
            raise DomainValidationError(
                "raw_bytes must be exact bytes",
                field_path="raw_bytes",
            )
        if self.kind is LockInspectionKind.READABLE:
            if self.document is None or self.raw_bytes is None:
                raise DomainValidationError(
                    "READABLE requires document and raw_bytes",
                    field_path="kind",
                )
        elif self.kind is LockInspectionKind.MALFORMED:
            if self.document is not None:
                raise DomainValidationError(
                    "MALFORMED must not carry a document",
                    field_path="document",
                )
        elif self.document is not None:
            raise DomainValidationError(
                "this inspection kind must not carry a document",
                field_path="document",
            )
        elif (
            self.kind
            in {
                LockInspectionKind.ABSENT,
                LockInspectionKind.MISSING_LOCK_JSON,
                LockInspectionKind.TRANSPORT_FAILURE,
            }
            and self.raw_bytes is not None
        ):
            raise DomainValidationError(
                "this inspection kind must not carry raw_bytes",
                field_path="raw_bytes",
            )


@unique
class LockOwnershipStatus(Enum):
    """Typed result of re-reading lock ownership."""

    OWNED = "owned"
    FOREIGN = "foreign"
    ABSENT = "absent"
    UNREADABLE = "unreadable"
    TRANSPORT_FAILURE = "transport_failure"


@dataclass(frozen=True, slots=True)
class LockOwnershipCheck:
    """Immutable ownership confirmation result."""

    status: LockOwnershipStatus
    document: LockDocument | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, LockOwnershipStatus):
            raise DomainValidationError(
                "expected a LockOwnershipStatus",
                field_path="status",
            )
        if self.document is not None and not isinstance(self.document, LockDocument):
            raise DomainValidationError(
                "expected a LockDocument",
                field_path="document",
            )
        if self.status is LockOwnershipStatus.OWNED and self.document is None:
            raise DomainValidationError(
                "OWNED requires a document",
                field_path="document",
            )


@unique
class LockReleaseOutcome(Enum):
    """Outcome of attempting to release an owned upload lock."""

    RELEASED = "released"
    ABSENT = "absent"
    NOT_OWNED = "not_owned"
    UNREADABLE = "unreadable"
    TRANSPORT_FAILURE = "transport_failure"


@dataclass(frozen=True, slots=True)
class LockReleaseResult:
    """Immutable release attempt result."""

    outcome: LockReleaseOutcome

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, LockReleaseOutcome):
            raise DomainValidationError(
                "expected a LockReleaseOutcome",
                field_path="outcome",
            )

    @property
    def cleanup_complete(self) -> bool:
        """True when the lock is confirmed gone or released."""
        return self.outcome in {
            LockReleaseOutcome.RELEASED,
            LockReleaseOutcome.ABSENT,
        }

    @property
    def ambiguous(self) -> bool:
        return self.outcome is LockReleaseOutcome.TRANSPORT_FAILURE


@dataclass(frozen=True, slots=True)
class LockRepairPreview:
    """Immutable repair-preview token from a prior inspection.

    Transport-failure previews are never removable. Confirmed repair must
    re-inspect and match this observation exactly before any mutation.
    """

    game_id: str
    kind: LockInspectionKind
    document: LockDocument | None = None
    raw_bytes: bytes | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "game_id", validate_game_id(self.game_id, field_path="game_id")
        )
        if not isinstance(self.kind, LockInspectionKind):
            raise DomainValidationError(
                "expected a LockInspectionKind",
                field_path="kind",
            )
        if self.document is not None and not isinstance(self.document, LockDocument):
            raise DomainValidationError(
                "expected a LockDocument",
                field_path="document",
            )
        if self.raw_bytes is not None and type(self.raw_bytes) is not bytes:
            raise DomainValidationError(
                "raw_bytes must be exact bytes",
                field_path="raw_bytes",
            )
        if self.kind is LockInspectionKind.TRANSPORT_FAILURE:
            if self.document is not None or self.raw_bytes is not None:
                raise DomainValidationError(
                    "TRANSPORT_FAILURE preview carries no lock payload",
                    field_path="kind",
                )
        elif self.kind is LockInspectionKind.READABLE:
            if self.document is None or self.raw_bytes is None:
                raise DomainValidationError(
                    "READABLE preview requires document and raw_bytes",
                    field_path="kind",
                )
        elif self.kind is LockInspectionKind.MALFORMED:
            if self.document is not None:
                raise DomainValidationError(
                    "MALFORMED preview must not carry a document",
                    field_path="document",
                )
        elif self.kind in {
            LockInspectionKind.ABSENT,
            LockInspectionKind.MISSING_LOCK_JSON,
        }:
            if self.document is not None or self.raw_bytes is not None:
                raise DomainValidationError(
                    "this preview kind must not carry lock payload",
                    field_path="kind",
                )


@dataclass(frozen=True, slots=True)
class LockRepairAuditEvent:
    """Structured audit record for abandoned-lock repair."""

    game_id: str
    confirmed: bool
    removed: bool
    observation_kind: str
    observed_operation_id: str | None
    observed_client_id: str | None
    observed_player_id: str | None
    observed_created_at: str | None
    observed_expires_at: str | None
    reason: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "game_id", validate_game_id(self.game_id, field_path="game_id")
        )
        if type(self.confirmed) is not bool:
            raise DomainValidationError(
                "expected an exact boolean",
                field_path="confirmed",
            )
        if type(self.removed) is not bool:
            raise DomainValidationError(
                "expected an exact boolean",
                field_path="removed",
            )
        if not isinstance(self.observation_kind, str) or not self.observation_kind:
            raise DomainValidationError(
                "expected a non-empty observation_kind",
                field_path="observation_kind",
            )
        if not isinstance(self.reason, str) or not self.reason:
            raise DomainValidationError(
                "expected a non-empty reason",
                field_path="reason",
            )


@unique
class LockRepairOutcome(Enum):
    """Outcome of confirmed abandoned-lock repair."""

    REMOVED = "removed"
    NOT_CONFIRMED = "not_confirmed"
    ABSENT = "absent"
    CHANGED = "changed"
    UNREADABLE = "unreadable"
    TRANSPORT_FAILURE = "transport_failure"
    NOT_REPAIRABLE = "not_repairable"


@dataclass(frozen=True, slots=True)
class LockRepairResult:
    """Immutable result of a repair attempt."""

    outcome: LockRepairOutcome
    audit: LockRepairAuditEvent

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, LockRepairOutcome):
            raise DomainValidationError(
                "expected a LockRepairOutcome",
                field_path="outcome",
            )
        if not isinstance(self.audit, LockRepairAuditEvent):
            raise DomainValidationError(
                "expected a LockRepairAuditEvent",
                field_path="audit",
            )


def inspect_upload_lock(storage: Storage, game_id: str) -> LockInspection:
    """Read the upload lock without mutating it."""
    validate_game_id(game_id, field_path="game_id")
    paths = GamePaths(game_id)
    try:
        storage.list_dir(paths.upload_lock_dir)
    except StorageNotFoundError:
        return LockInspection(LockInspectionKind.ABSENT)
    except StorageWrongKindError:
        return LockInspection(LockInspectionKind.MALFORMED)
    except StorageError:
        return LockInspection(LockInspectionKind.TRANSPORT_FAILURE)

    try:
        raw = storage.read_file(paths.upload_lock_json)
    except StorageNotFoundError:
        return LockInspection(LockInspectionKind.MISSING_LOCK_JSON)
    except StorageWrongKindError:
        return LockInspection(LockInspectionKind.MALFORMED)
    except StorageError:
        return LockInspection(LockInspectionKind.TRANSPORT_FAILURE)

    if type(raw) is not bytes:
        return LockInspection(LockInspectionKind.MALFORMED)
    try:
        document = LockDocument.from_json_bytes(raw)
    except DomainValidationError:
        return LockInspection(LockInspectionKind.MALFORMED, raw_bytes=raw)
    return LockInspection(LockInspectionKind.READABLE, document=document, raw_bytes=raw)


def preview_lock_repair(storage: Storage, game_id: str) -> LockRepairPreview:
    """Capture an immutable repair preview from the current lock observation."""
    inspection = inspect_upload_lock(storage, game_id)
    return LockRepairPreview(
        game_id=game_id,
        kind=inspection.kind,
        document=inspection.document,
        raw_bytes=inspection.raw_bytes,
    )


def acquire_or_resume_upload_lock(
    storage: Storage,
    *,
    game_id: str,
    operation_id: str,
    client_id: str,
    player_id: str,
    now_utc: str,
    journal: HandoffJournal,
    sha256: str,
) -> LockAcquireResult:
    """Acquire ``upload.lock/`` or resume when journal ownership agrees."""
    validate_game_id(game_id, field_path="game_id")
    validate_operation_id(operation_id, field_path="operation_id")
    validate_client_id(client_id, field_path="client_id")
    validate_player_id(player_id, field_path="player_id")
    validate_utc_timestamp(now_utc, field_path="now_utc")
    if not isinstance(journal, HandoffJournal):
        raise TypeError("journal must satisfy HandoffJournal")

    paths = GamePaths(game_id)
    caps = storage.capabilities()
    if not caps.exclusive_mkdir:
        return LockAcquireResult(LockAcquireOutcome.CAPABILITY_FAILURE)

    document = build_lock_document(
        operation_id=operation_id,
        client_id=client_id,
        player_id=player_id,
        now_utc=now_utc,
    )

    try:
        storage.mkdir(paths.upload_lock_dir)
    except StorageCapabilityError:
        return LockAcquireResult(LockAcquireOutcome.CAPABILITY_FAILURE)
    except StorageAlreadyExistsError:
        return _resume_existing_lock(
            storage,
            operation_id=operation_id,
            client_id=client_id,
            journal=journal,
            game_id=game_id,
            sha256=sha256,
            player_id=player_id,
        )
    except StorageTransportError:
        return LockAcquireResult(LockAcquireOutcome.TRANSPORT_FAILURE)
    except StorageError:
        return LockAcquireResult(LockAcquireOutcome.TRANSPORT_FAILURE)

    try:
        storage.write_file(
            paths.upload_lock_json, document.to_json_bytes(), overwrite=False
        )
    except StorageTransportError:
        journal.begin_handoff(
            InProgressHandoff(
                game_id=game_id,
                operation_id=operation_id,
                client_id=client_id,
                player_id=player_id,
                sha256=sha256,
            )
        )
        return LockAcquireResult(LockAcquireOutcome.TRANSPORT_FAILURE)
    except StorageError:
        journal.begin_handoff(
            InProgressHandoff(
                game_id=game_id,
                operation_id=operation_id,
                client_id=client_id,
                player_id=player_id,
                sha256=sha256,
            )
        )
        return LockAcquireResult(LockAcquireOutcome.TRANSPORT_FAILURE)

    journal.begin_handoff(
        InProgressHandoff(
            game_id=game_id,
            operation_id=operation_id,
            client_id=client_id,
            player_id=player_id,
            sha256=sha256,
        )
    )
    return LockAcquireResult(LockAcquireOutcome.ACQUIRED, document=document, owned=True)


def _resume_existing_lock(
    storage: Storage,
    *,
    operation_id: str,
    client_id: str,
    journal: HandoffJournal,
    game_id: str,
    sha256: str,
    player_id: str,
) -> LockAcquireResult:
    inspection = inspect_upload_lock(storage, game_id)
    if inspection.kind is LockInspectionKind.TRANSPORT_FAILURE:
        return LockAcquireResult(LockAcquireOutcome.TRANSPORT_FAILURE)
    if (
        inspection.kind is not LockInspectionKind.READABLE
        or inspection.document is None
    ):
        return LockAcquireResult(LockAcquireOutcome.UNREADABLE)

    document = inspection.document
    progress = journal.in_progress_handoff(game_id=game_id)
    if (
        progress is not None
        and document.matches_owner(
            operation_id=operation_id,
            client_id=client_id,
            player_id=player_id,
        )
        and progress.operation_id == operation_id
        and progress.client_id == client_id
        and progress.player_id == player_id
        and progress.sha256 == sha256
    ):
        return LockAcquireResult(
            LockAcquireOutcome.RESUMED, document=document, owned=True
        )
    return LockAcquireResult(
        LockAcquireOutcome.FOREIGN_HELD, document=document, owned=False
    )


def check_lock_ownership(
    storage: Storage,
    *,
    game_id: str,
    operation_id: str,
    client_id: str,
    player_id: str,
) -> LockOwnershipCheck:
    """Re-read ``lock.json`` and classify ownership (pre-commit / release)."""
    inspection = inspect_upload_lock(storage, game_id)
    if inspection.kind is LockInspectionKind.TRANSPORT_FAILURE:
        return LockOwnershipCheck(LockOwnershipStatus.TRANSPORT_FAILURE)
    if inspection.kind is LockInspectionKind.ABSENT:
        return LockOwnershipCheck(LockOwnershipStatus.ABSENT)
    if (
        inspection.kind is not LockInspectionKind.READABLE
        or inspection.document is None
    ):
        return LockOwnershipCheck(LockOwnershipStatus.UNREADABLE)
    if inspection.document.matches_owner(
        operation_id=operation_id,
        client_id=client_id,
        player_id=player_id,
    ):
        return LockOwnershipCheck(
            LockOwnershipStatus.OWNED, document=inspection.document
        )
    return LockOwnershipCheck(LockOwnershipStatus.FOREIGN, document=inspection.document)


def release_owned_upload_lock(
    storage: Storage,
    *,
    game_id: str,
    operation_id: str,
    client_id: str,
    player_id: str,
) -> LockReleaseResult:
    """Release only after ownership is positively verified."""
    ownership = check_lock_ownership(
        storage,
        game_id=game_id,
        operation_id=operation_id,
        client_id=client_id,
        player_id=player_id,
    )
    if ownership.status is LockOwnershipStatus.TRANSPORT_FAILURE:
        return LockReleaseResult(LockReleaseOutcome.TRANSPORT_FAILURE)
    if ownership.status is LockOwnershipStatus.ABSENT:
        return LockReleaseResult(LockReleaseOutcome.ABSENT)
    if ownership.status is LockOwnershipStatus.UNREADABLE:
        return LockReleaseResult(LockReleaseOutcome.UNREADABLE)
    if ownership.status is not LockOwnershipStatus.OWNED:
        return LockReleaseResult(LockReleaseOutcome.NOT_OWNED)

    paths = GamePaths(game_id)
    try:
        storage.remove_file(paths.upload_lock_json)
    except StorageNotFoundError:
        pass
    except StorageError:
        return LockReleaseResult(LockReleaseOutcome.TRANSPORT_FAILURE)
    try:
        storage.remove_dir(paths.upload_lock_dir)
    except StorageNotFoundError:
        return LockReleaseResult(LockReleaseOutcome.ABSENT)
    except StorageError:
        return LockReleaseResult(LockReleaseOutcome.TRANSPORT_FAILURE)
    return LockReleaseResult(LockReleaseOutcome.RELEASED)


def repair_abandoned_upload_lock(
    storage: Storage,
    *,
    preview: LockRepairPreview,
    confirmed: bool,
) -> LockRepairResult:
    """Remove a foreign/abandoned/malformed lock only with explicit confirmation."""
    if not isinstance(preview, LockRepairPreview):
        raise TypeError("preview must be a LockRepairPreview")
    if type(confirmed) is not bool:
        raise DomainValidationError(
            "expected an exact boolean",
            field_path="confirmed",
        )

    def _audit(
        *,
        removed: bool,
        reason: str,
        observation: LockInspection | LockRepairPreview,
    ) -> LockRepairAuditEvent:
        document = observation.document
        return LockRepairAuditEvent(
            game_id=preview.game_id,
            confirmed=confirmed,
            removed=removed,
            observation_kind=observation.kind.value,
            observed_operation_id=(None if document is None else document.operation_id),
            observed_client_id=None if document is None else document.client_id,
            observed_player_id=None if document is None else document.player_id,
            observed_created_at=None if document is None else document.created_at,
            observed_expires_at=None if document is None else document.expires_at,
            reason=reason,
        )

    if not confirmed:
        return LockRepairResult(
            LockRepairOutcome.NOT_CONFIRMED,
            _audit(removed=False, reason="confirmation_required", observation=preview),
        )

    if preview.kind is LockInspectionKind.TRANSPORT_FAILURE:
        return LockRepairResult(
            LockRepairOutcome.NOT_REPAIRABLE,
            _audit(
                removed=False,
                reason="transport_failure_not_repairable",
                observation=preview,
            ),
        )

    if preview.kind is LockInspectionKind.ABSENT:
        current = inspect_upload_lock(storage, preview.game_id)
        if current.kind is LockInspectionKind.ABSENT:
            return LockRepairResult(
                LockRepairOutcome.ABSENT,
                _audit(removed=False, reason="lock_absent", observation=current),
            )
        return LockRepairResult(
            LockRepairOutcome.CHANGED,
            _audit(removed=False, reason="lock_appeared", observation=current),
        )

    current = inspect_upload_lock(storage, preview.game_id)
    if current.kind is LockInspectionKind.TRANSPORT_FAILURE:
        return LockRepairResult(
            LockRepairOutcome.TRANSPORT_FAILURE,
            _audit(
                removed=False,
                reason="transport_failure",
                observation=current,
            ),
        )
    if not _preview_matches_inspection(preview, current):
        return LockRepairResult(
            LockRepairOutcome.CHANGED,
            _audit(
                removed=False,
                reason="lock_observation_changed",
                observation=current,
            ),
        )

    paths = GamePaths(preview.game_id)
    try:
        if current.kind is not LockInspectionKind.MISSING_LOCK_JSON:
            try:
                storage.remove_file(paths.upload_lock_json)
            except StorageNotFoundError:
                # Race to missing json — treat as change unless preview was missing.
                if preview.kind is not LockInspectionKind.MISSING_LOCK_JSON:
                    return LockRepairResult(
                        LockRepairOutcome.CHANGED,
                        _audit(
                            removed=False,
                            reason="lock_json_disappeared",
                            observation=inspect_upload_lock(storage, preview.game_id),
                        ),
                    )
        storage.remove_dir(paths.upload_lock_dir)
    except StorageError:
        return LockRepairResult(
            LockRepairOutcome.TRANSPORT_FAILURE,
            _audit(removed=False, reason="removal_failed", observation=current),
        )

    return LockRepairResult(
        LockRepairOutcome.REMOVED,
        _audit(removed=True, reason="removed", observation=current),
    )


def _preview_matches_inspection(
    preview: LockRepairPreview, current: LockInspection
) -> bool:
    if preview.kind is not current.kind:
        return False
    if preview.kind is LockInspectionKind.READABLE:
        return (
            preview.document == current.document
            and preview.raw_bytes == current.raw_bytes
        )
    if preview.kind is LockInspectionKind.MALFORMED:
        return preview.raw_bytes == current.raw_bytes
    if preview.kind is LockInspectionKind.MISSING_LOCK_JSON:
        return True
    return False
