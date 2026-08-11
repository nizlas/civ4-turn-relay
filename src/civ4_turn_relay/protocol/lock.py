"""Upload-lock lifecycle (protocol §7.1).

Acquire only via exclusive ``mkdir``. TTL/age is informational and never
authorizes automatic foreign-lock deletion. Own-lock resume requires journal
agreement on ``operation_id`` and ``client_id``. Abandoned-lock repair is a
separate confirmed API with structured audit events.
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

    def matches_owner(self, *, operation_id: str, client_id: str) -> bool:
        return self.operation_id == validate_operation_id(
            operation_id, field_path="operation_id"
        ) and self.client_id == validate_client_id(client_id, field_path="client_id")


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
        if self.owned and (
            self.outcome
            not in {LockAcquireOutcome.ACQUIRED, LockAcquireOutcome.RESUMED}
            or self.document is None
        ):
            raise DomainValidationError(
                "owned locks require ACQUIRED/RESUMED with a document",
                field_path="owned",
            )


@unique
class LockInspectionKind(Enum):
    """Observed state of ``locks/upload.lock/``."""

    ABSENT = "absent"
    READABLE = "readable"
    UNREADABLE = "unreadable"
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
        elif self.document is not None:
            raise DomainValidationError(
                "non-READABLE inspections must not carry a document",
                field_path="document",
            )


@dataclass(frozen=True, slots=True)
class LockRepairAuditEvent:
    """Structured audit record for confirmed abandoned-lock repair."""

    game_id: str
    confirmed: bool
    removed: bool
    observed_operation_id: str | None
    observed_client_id: str | None
    observed_player_id: str | None
    observed_created_at: str | None
    observed_expires_at: str | None
    reason: str


@unique
class LockRepairOutcome(Enum):
    """Outcome of confirmed abandoned-lock repair."""

    REMOVED = "removed"
    NOT_CONFIRMED = "not_confirmed"
    ABSENT = "absent"
    CHANGED = "changed"
    UNREADABLE = "unreadable"
    TRANSPORT_FAILURE = "transport_failure"


@dataclass(frozen=True, slots=True)
class LockRepairResult:
    """Immutable result of a repair attempt."""

    outcome: LockRepairOutcome
    audit: LockRepairAuditEvent


def inspect_upload_lock(storage: Storage, game_id: str) -> LockInspection:
    """Read the upload lock without mutating it."""
    validate_game_id(game_id, field_path="game_id")
    paths = GamePaths(game_id)
    try:
        storage.list_dir(paths.upload_lock_dir)
    except StorageNotFoundError:
        return LockInspection(LockInspectionKind.ABSENT)
    except StorageWrongKindError:
        return LockInspection(LockInspectionKind.UNREADABLE)
    except StorageError:
        return LockInspection(LockInspectionKind.TRANSPORT_FAILURE)

    try:
        raw = storage.read_file(paths.upload_lock_json)
    except StorageNotFoundError:
        return LockInspection(LockInspectionKind.UNREADABLE)
    except StorageWrongKindError:
        return LockInspection(LockInspectionKind.UNREADABLE)
    except StorageError:
        return LockInspection(LockInspectionKind.TRANSPORT_FAILURE)

    if type(raw) is not bytes:
        return LockInspection(LockInspectionKind.UNREADABLE)
    try:
        document = LockDocument.from_json_bytes(raw)
    except DomainValidationError:
        return LockInspection(LockInspectionKind.UNREADABLE, raw_bytes=raw)
    return LockInspection(LockInspectionKind.READABLE, document=document, raw_bytes=raw)


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
            paths=paths,
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
        # Ambiguous: directory may exist without readable ownership — retain
        # journal evidence for safe retry; do not guess ownership.
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
    paths: GamePaths,
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
        and document.matches_owner(operation_id=operation_id, client_id=client_id)
        and progress.operation_id == operation_id
        and progress.client_id == client_id
        and progress.sha256 == sha256
        and progress.player_id == player_id
    ):
        return LockAcquireResult(
            LockAcquireOutcome.RESUMED, document=document, owned=True
        )
    return LockAcquireResult(
        LockAcquireOutcome.FOREIGN_HELD, document=document, owned=False
    )


def confirm_lock_ownership(
    storage: Storage,
    *,
    game_id: str,
    operation_id: str,
    client_id: str,
) -> bool:
    """Re-read ``lock.json`` and verify ownership (pre-commit check)."""
    inspection = inspect_upload_lock(storage, game_id)
    if (
        inspection.kind is not LockInspectionKind.READABLE
        or inspection.document is None
    ):
        return False
    return inspection.document.matches_owner(
        operation_id=operation_id, client_id=client_id
    )


def release_owned_upload_lock(
    storage: Storage,
    *,
    game_id: str,
    operation_id: str,
    client_id: str,
) -> None:
    """Best-effort release only when the lock is still owned by this operation."""
    if not confirm_lock_ownership(
        storage,
        game_id=game_id,
        operation_id=operation_id,
        client_id=client_id,
    ):
        return
    paths = GamePaths(game_id)
    try:
        storage.remove_file(paths.upload_lock_json)
    except StorageNotFoundError:
        pass
    except StorageError:
        return
    try:
        storage.remove_dir(paths.upload_lock_dir)
    except StorageError:
        return


def repair_abandoned_upload_lock(
    storage: Storage,
    *,
    game_id: str,
    expected: LockDocument,
    confirmed: bool,
) -> LockRepairResult:
    """Remove a foreign/abandoned lock only with explicit confirmation.

    Re-reads and verifies the observed lock still matches ``expected`` before
    removal. No confirmation means no mutation.
    """
    validate_game_id(game_id, field_path="game_id")
    if not isinstance(expected, LockDocument):
        raise TypeError("expected must be a LockDocument")
    if type(confirmed) is not bool:
        raise DomainValidationError(
            "expected an exact boolean",
            field_path="confirmed",
        )

    if not confirmed:
        return LockRepairResult(
            LockRepairOutcome.NOT_CONFIRMED,
            LockRepairAuditEvent(
                game_id=game_id,
                confirmed=False,
                removed=False,
                observed_operation_id=expected.operation_id,
                observed_client_id=expected.client_id,
                observed_player_id=expected.player_id,
                observed_created_at=expected.created_at,
                observed_expires_at=expected.expires_at,
                reason="confirmation_required",
            ),
        )

    inspection = inspect_upload_lock(storage, game_id)
    if inspection.kind is LockInspectionKind.ABSENT:
        return LockRepairResult(
            LockRepairOutcome.ABSENT,
            LockRepairAuditEvent(
                game_id=game_id,
                confirmed=True,
                removed=False,
                observed_operation_id=None,
                observed_client_id=None,
                observed_player_id=None,
                observed_created_at=None,
                observed_expires_at=None,
                reason="lock_absent",
            ),
        )
    if inspection.kind is LockInspectionKind.TRANSPORT_FAILURE:
        return LockRepairResult(
            LockRepairOutcome.TRANSPORT_FAILURE,
            LockRepairAuditEvent(
                game_id=game_id,
                confirmed=True,
                removed=False,
                observed_operation_id=expected.operation_id,
                observed_client_id=expected.client_id,
                observed_player_id=expected.player_id,
                observed_created_at=expected.created_at,
                observed_expires_at=expected.expires_at,
                reason="transport_failure",
            ),
        )
    if (
        inspection.kind is not LockInspectionKind.READABLE
        or inspection.document is None
    ):
        return LockRepairResult(
            LockRepairOutcome.UNREADABLE,
            LockRepairAuditEvent(
                game_id=game_id,
                confirmed=True,
                removed=False,
                observed_operation_id=None,
                observed_client_id=None,
                observed_player_id=None,
                observed_created_at=None,
                observed_expires_at=None,
                reason="lock_unreadable",
            ),
        )

    observed = inspection.document
    if (
        observed.operation_id != expected.operation_id
        or observed.client_id != expected.client_id
        or observed.player_id != expected.player_id
        or observed.created_at != expected.created_at
        or observed.expires_at != expected.expires_at
        or inspection.raw_bytes != expected.to_json_bytes()
    ):
        return LockRepairResult(
            LockRepairOutcome.CHANGED,
            LockRepairAuditEvent(
                game_id=game_id,
                confirmed=True,
                removed=False,
                observed_operation_id=observed.operation_id,
                observed_client_id=observed.client_id,
                observed_player_id=observed.player_id,
                observed_created_at=observed.created_at,
                observed_expires_at=observed.expires_at,
                reason="lock_metadata_changed",
            ),
        )

    paths = GamePaths(game_id)
    try:
        storage.remove_file(paths.upload_lock_json)
        storage.remove_dir(paths.upload_lock_dir)
    except StorageError:
        return LockRepairResult(
            LockRepairOutcome.TRANSPORT_FAILURE,
            LockRepairAuditEvent(
                game_id=game_id,
                confirmed=True,
                removed=False,
                observed_operation_id=observed.operation_id,
                observed_client_id=observed.client_id,
                observed_player_id=observed.player_id,
                observed_created_at=observed.created_at,
                observed_expires_at=observed.expires_at,
                reason="removal_failed",
            ),
        )

    return LockRepairResult(
        LockRepairOutcome.REMOVED,
        LockRepairAuditEvent(
            game_id=game_id,
            confirmed=True,
            removed=True,
            observed_operation_id=observed.operation_id,
            observed_client_id=observed.client_id,
            observed_player_id=observed.player_id,
            observed_created_at=observed.created_at,
            observed_expires_at=observed.expires_at,
            reason="removed",
        ),
    )
