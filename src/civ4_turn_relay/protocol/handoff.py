"""Safe handoff commit engine (protocol §7.2–§7.3).

Accepts synthetic outgoing save bytes from the current human owner and
atomically commits a handoff against the P2 ``Storage`` port. Manifest
replacement is the sole remote commit point.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, unique

from civ4_turn_relay.domain import (
    MIN_CLIENT_PROTOCOL,
    AcceptedSave,
    DomainValidationError,
    Manifest,
    ProtocolMetadata,
    sha256_hex,
    validate_client_id,
    validate_game_id,
    validate_operation_id,
    validate_original_filename,
    validate_player_id,
    validate_utc_timestamp,
)
from civ4_turn_relay.protocol.hash_classify import (
    HashClassification,
    classify_candidate_hash,
)
from civ4_turn_relay.protocol.journal import HandoffJournal
from civ4_turn_relay.protocol.lock import (
    LockAcquireOutcome,
    acquire_or_resume_upload_lock,
    confirm_lock_ownership,
    release_owned_upload_lock,
)
from civ4_turn_relay.protocol.manifest_access import (
    ManifestReadOutcome,
    read_authoritative_manifest,
)
from civ4_turn_relay.protocol.paths import (
    GamePaths,
    extension_from_original_filename,
)
from civ4_turn_relay.protocol.players import next_human_player_id
from civ4_turn_relay.storage import (
    ObjectComparisonResult,
    Storage,
    StorageAlreadyExistsError,
    StorageCapabilityError,
    StorageError,
    StorageNotFoundError,
    StorageTransportError,
    compare_stored_object,
    read_fingerprint,
)

# Protocol recommendation: 256 MiB (§12). Keep default no larger.
DEFAULT_MAX_SAVE_BYTES = 256 * 1024 * 1024


@unique
class HandoffOutcome(Enum):
    """Typed handoff results. Rejected/idempotent never mean newly committed."""

    COMMITTED = "committed"
    IDEMPOTENT_ACK = "idempotent_ack"
    JOURNAL_ONLY_ACK = "journal_only_ack"
    NOT_CURRENT_OWNER = "not_current_owner"
    REJECT_INCOMING = "reject_incoming"
    STALE_REPLAY = "stale_replay"
    LOCK_HELD = "lock_held"
    LOCK_UNREADABLE = "lock_unreadable"
    LOCK_OWNERSHIP_LOST = "lock_ownership_lost"
    HARD_INTEGRITY_FAILURE = "hard_integrity_failure"
    CAPABILITY_FAILURE = "capability_failure"
    TRANSPORT_FAILURE = "transport_failure"
    INVALID_MANIFEST = "invalid_manifest"
    MISSING_MANIFEST = "missing_manifest"


@dataclass(frozen=True, slots=True)
class HandoffRequest:
    """Validated inputs for a handoff attempt (injected time; no wall clock)."""

    game_id: str
    local_player_id: str
    client_id: str
    operation_id: str
    outgoing_bytes: bytes
    original_filename: str
    now_utc: str
    max_save_bytes: int = DEFAULT_MAX_SAVE_BYTES

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "game_id", validate_game_id(self.game_id, field_path="game_id")
        )
        object.__setattr__(
            self,
            "local_player_id",
            validate_player_id(self.local_player_id, field_path="local_player_id"),
        )
        object.__setattr__(
            self,
            "client_id",
            validate_client_id(self.client_id, field_path="client_id"),
        )
        object.__setattr__(
            self,
            "operation_id",
            validate_operation_id(self.operation_id, field_path="operation_id"),
        )
        if type(self.outgoing_bytes) is not bytes:
            raise DomainValidationError(
                "outgoing_bytes must be exact bytes",
                field_path="outgoing_bytes",
            )
        object.__setattr__(
            self,
            "original_filename",
            validate_original_filename(
                self.original_filename, field_path="original_filename"
            ),
        )
        object.__setattr__(
            self,
            "now_utc",
            validate_utc_timestamp(self.now_utc, field_path="now_utc"),
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
        if len(self.outgoing_bytes) == 0:
            raise DomainValidationError(
                "outgoing save must be non-empty",
                field_path="outgoing_bytes",
            )
        if len(self.outgoing_bytes) > self.max_save_bytes:
            raise DomainValidationError(
                "outgoing save exceeds configured maximum size",
                field_path="outgoing_bytes",
            )


@dataclass(frozen=True, slots=True)
class HandoffResult:
    """Immutable handoff result with explicit manifest-change flag."""

    outcome: HandoffOutcome
    manifest_changed: bool
    manifest: Manifest | None = None
    sha256: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, HandoffOutcome):
            raise DomainValidationError(
                "expected a HandoffOutcome",
                field_path="outcome",
            )
        if type(self.manifest_changed) is not bool:
            raise DomainValidationError(
                "expected an exact boolean",
                field_path="manifest_changed",
            )
        if self.manifest is not None and not isinstance(self.manifest, Manifest):
            raise DomainValidationError(
                "expected a Manifest",
                field_path="manifest",
            )
        if self.manifest_changed and self.outcome is not HandoffOutcome.COMMITTED:
            raise DomainValidationError(
                "only COMMITTED may report manifest_changed=True",
                field_path="manifest_changed",
            )
        if self.outcome is HandoffOutcome.COMMITTED and not self.manifest_changed:
            raise DomainValidationError(
                "COMMITTED requires manifest_changed=True",
                field_path="manifest_changed",
            )


def commit_handoff(
    storage: Storage,
    request: HandoffRequest,
    *,
    journal: HandoffJournal,
) -> HandoffResult:
    """Execute the §7.3 handoff algorithm against ``storage``."""
    if not isinstance(request, HandoffRequest):
        raise TypeError("request must be a HandoffRequest")
    if not isinstance(journal, HandoffJournal):
        raise TypeError("journal must satisfy HandoffJournal")

    digest = sha256_hex(request.outgoing_bytes)
    size_bytes = len(request.outgoing_bytes)
    extension = extension_from_original_filename(request.original_filename)
    paths = GamePaths(request.game_id)

    caps = storage.capabilities()
    if not (
        caps.exclusive_mkdir
        and caps.atomic_replace
        and caps.atomic_publish_no_replace
        and caps.complete_readback
    ):
        return HandoffResult(HandoffOutcome.CAPABILITY_FAILURE, False, sha256=digest)

    lock = acquire_or_resume_upload_lock(
        storage,
        game_id=request.game_id,
        operation_id=request.operation_id,
        client_id=request.client_id,
        player_id=request.local_player_id,
        now_utc=request.now_utc,
        journal=journal,
        sha256=digest,
    )
    if lock.outcome is LockAcquireOutcome.FOREIGN_HELD:
        return HandoffResult(HandoffOutcome.LOCK_HELD, False, sha256=digest)
    if lock.outcome is LockAcquireOutcome.UNREADABLE:
        return HandoffResult(HandoffOutcome.LOCK_UNREADABLE, False, sha256=digest)
    if lock.outcome is LockAcquireOutcome.CAPABILITY_FAILURE:
        return HandoffResult(HandoffOutcome.CAPABILITY_FAILURE, False, sha256=digest)
    if lock.outcome is LockAcquireOutcome.TRANSPORT_FAILURE or not lock.owned:
        return HandoffResult(HandoffOutcome.TRANSPORT_FAILURE, False, sha256=digest)

    owned = True
    try:
        read = read_authoritative_manifest(storage, request.game_id)
        if read.outcome is ManifestReadOutcome.MISSING:
            return _fail(HandoffOutcome.MISSING_MANIFEST, digest)
        if read.outcome is ManifestReadOutcome.INVALID:
            return _fail(HandoffOutcome.INVALID_MANIFEST, digest)
        if read.outcome is ManifestReadOutcome.GAME_ID_MISMATCH:
            return _fail(HandoffOutcome.INVALID_MANIFEST, digest)
        if (
            read.outcome is ManifestReadOutcome.TRANSPORT_FAILURE
            or read.manifest is None
            or read.raw_bytes is None
        ):
            return _fail(HandoffOutcome.TRANSPORT_FAILURE, digest)

        manifest = read.manifest
        raw_manifest = read.raw_bytes
        evidence = journal.evidence_for_hash(game_id=request.game_id, sha256=digest)
        classification = classify_candidate_hash(
            candidate_sha256=digest,
            accepted_save_hashes=manifest.accepted_save_hashes,
            local_player_id=request.local_player_id,
            current_player_id=manifest.current_player_id,
            last_sender_id=manifest.last_sender_id,
            evidence=evidence,
        )

        if classification is HashClassification.REJECT_INCOMING:
            return _fail(HandoffOutcome.REJECT_INCOMING, digest, manifest)
        if classification is HashClassification.IDEMPOTENT_ACK:
            journal.record_historical_acceptance(game_id=request.game_id, sha256=digest)
            journal.clear_in_progress(game_id=request.game_id)
            return HandoffResult(
                HandoffOutcome.IDEMPOTENT_ACK,
                False,
                manifest=manifest,
                sha256=digest,
            )
        if classification is HashClassification.JOURNAL_ONLY_ACK:
            return _fail(HandoffOutcome.JOURNAL_ONLY_ACK, digest, manifest)
        if classification is HashClassification.STALE_REPLAY:
            return _fail(HandoffOutcome.STALE_REPLAY, digest, manifest)
        if classification is not HashClassification.NEW_HANDOFF_CANDIDATE:
            return _fail(HandoffOutcome.STALE_REPLAY, digest, manifest)
        if manifest.current_player_id != request.local_player_id:
            return _fail(HandoffOutcome.NOT_CURRENT_OWNER, digest, manifest)

        next_seq = manifest.protocol_sequence + 1
        next_player = next_human_player_id(
            manifest, after_player_id=request.local_player_id
        )
        save_relative = paths.accepted_save_relative(next_seq, digest, extension)
        save_storage = paths.resolve(save_relative)
        temp_upload = paths.temporary_upload(request.operation_id, extension)

        staged = _stage_and_verify_upload(
            storage,
            temp_path=temp_upload,
            data=request.outgoing_bytes,
            digest=digest,
            size_bytes=size_bytes,
        )
        if staged is not None:
            return staged

        published = _publish_or_reuse_final(
            storage,
            temp_path=temp_upload,
            final_path=save_storage,
            digest=digest,
            size_bytes=size_bytes,
        )
        if published is not None:
            return published

        # Final object must be fully verified before any manifest reference.
        final_check = compare_stored_object(
            storage,
            save_storage,
            expected_size=size_bytes,
            expected_sha256=digest,
        )
        if final_check is not ObjectComparisonResult.EXACT_MATCH:
            return _fail(HandoffOutcome.HARD_INTEGRITY_FAILURE, digest, manifest)

        history_relative = paths.history_manifest_relative(
            manifest.protocol_sequence, sha256_hex(raw_manifest)
        )
        history_storage = paths.resolve(history_relative)
        archived = _archive_or_reuse_history(
            storage,
            history_path=history_storage,
            exact_bytes=raw_manifest,
        )
        if archived is not None:
            return archived

        new_manifest = Manifest(
            schema_version=manifest.schema_version,
            game_id=manifest.game_id,
            display_name=manifest.display_name,
            players=manifest.players,
            protocol_sequence=next_seq,
            current_player_id=next_player,
            last_sender_id=request.local_player_id,
            accepted_save=AcceptedSave(
                sha256=digest,
                size_bytes=size_bytes,
                remote_path=save_relative,
                original_filename=request.original_filename,
                accepted_at=request.now_utc,
            ),
            accepted_save_hashes=(*manifest.accepted_save_hashes, digest),
            previous_manifest_ref=history_relative,
            protocol=ProtocolMetadata(
                min_client_protocol=MIN_CLIENT_PROTOCOL,
                last_operation_id=request.operation_id,
            ),
        )
        new_payload = new_manifest.to_json_bytes()
        temp_manifest = paths.temporary_manifest(request.operation_id)
        try:
            storage.write_file(temp_manifest, new_payload, overwrite=True)
        except StorageCapabilityError:
            return _fail(HandoffOutcome.CAPABILITY_FAILURE, digest, manifest)
        except StorageTransportError:
            return _fail(HandoffOutcome.TRANSPORT_FAILURE, digest, manifest)
        except StorageError:
            return _fail(HandoffOutcome.TRANSPORT_FAILURE, digest, manifest)

        if not confirm_lock_ownership(
            storage,
            game_id=request.game_id,
            operation_id=request.operation_id,
            client_id=request.client_id,
        ):
            return _fail(HandoffOutcome.LOCK_OWNERSHIP_LOST, digest, manifest)

        try:
            storage.atomic_replace(temp_manifest, paths.manifest)
        except StorageCapabilityError:
            return _fail(HandoffOutcome.CAPABILITY_FAILURE, digest, manifest)
        except StorageTransportError:
            return _reconcile_uncertain_commit(
                storage,
                request=request,
                digest=digest,
                expected_sequence=next_seq,
                journal=journal,
            )
        except StorageError:
            return _fail(HandoffOutcome.TRANSPORT_FAILURE, digest, manifest)

        journal.record_historical_acceptance(game_id=request.game_id, sha256=digest)
        journal.clear_in_progress(game_id=request.game_id)
        return HandoffResult(
            HandoffOutcome.COMMITTED,
            True,
            manifest=new_manifest,
            sha256=digest,
        )
    finally:
        if owned:
            release_owned_upload_lock(
                storage,
                game_id=request.game_id,
                operation_id=request.operation_id,
                client_id=request.client_id,
            )


def _fail(
    outcome: HandoffOutcome,
    digest: str,
    manifest: Manifest | None = None,
) -> HandoffResult:
    return HandoffResult(outcome, False, manifest=manifest, sha256=digest)


def _stage_and_verify_upload(
    storage: Storage,
    *,
    temp_path: str,
    data: bytes,
    digest: str,
    size_bytes: int,
) -> HandoffResult | None:
    try:
        existing = compare_stored_object(
            storage,
            temp_path,
            expected_size=size_bytes,
            expected_sha256=digest,
        )
        if existing is ObjectComparisonResult.EXACT_MATCH:
            return None
        # Wrong bytes at temp path for this op — rewrite.
        storage.write_file(temp_path, data, overwrite=True)
    except StorageNotFoundError:
        try:
            storage.write_file(temp_path, data, overwrite=False)
        except StorageCapabilityError:
            return _fail(HandoffOutcome.CAPABILITY_FAILURE, digest)
        except StorageTransportError:
            return _fail(HandoffOutcome.TRANSPORT_FAILURE, digest)
        except StorageError:
            return _fail(HandoffOutcome.TRANSPORT_FAILURE, digest)
    except StorageCapabilityError:
        return _fail(HandoffOutcome.CAPABILITY_FAILURE, digest)
    except StorageTransportError:
        return _fail(HandoffOutcome.TRANSPORT_FAILURE, digest)
    except StorageError:
        return _fail(HandoffOutcome.TRANSPORT_FAILURE, digest)

    try:
        check = compare_stored_object(
            storage,
            temp_path,
            expected_size=size_bytes,
            expected_sha256=digest,
        )
    except StorageTransportError:
        return _fail(HandoffOutcome.TRANSPORT_FAILURE, digest)
    except StorageError:
        return _fail(HandoffOutcome.TRANSPORT_FAILURE, digest)
    if check is not ObjectComparisonResult.EXACT_MATCH:
        return _fail(HandoffOutcome.HARD_INTEGRITY_FAILURE, digest)
    return None


def _publish_or_reuse_final(
    storage: Storage,
    *,
    temp_path: str,
    final_path: str,
    digest: str,
    size_bytes: int,
) -> HandoffResult | None:
    try:
        storage.publish_no_replace(temp_path, final_path)
    except StorageAlreadyExistsError:
        try:
            check = compare_stored_object(
                storage,
                final_path,
                expected_size=size_bytes,
                expected_sha256=digest,
            )
        except StorageTransportError:
            return _fail(HandoffOutcome.TRANSPORT_FAILURE, digest)
        except StorageError:
            return _fail(HandoffOutcome.TRANSPORT_FAILURE, digest)
        if check is ObjectComparisonResult.EXACT_MATCH:
            # Reuse exact orphan; never overwrite.
            return None
        return _fail(HandoffOutcome.HARD_INTEGRITY_FAILURE, digest)
    except StorageCapabilityError:
        return _fail(HandoffOutcome.CAPABILITY_FAILURE, digest)
    except StorageTransportError:
        return _fail(HandoffOutcome.TRANSPORT_FAILURE, digest)
    except StorageError:
        return _fail(HandoffOutcome.TRANSPORT_FAILURE, digest)

    # Ensure publish result is fully readable/matching before continuing.
    try:
        fingerprint = read_fingerprint(storage, final_path)
    except StorageTransportError:
        return _fail(HandoffOutcome.TRANSPORT_FAILURE, digest)
    except StorageError:
        return _fail(HandoffOutcome.TRANSPORT_FAILURE, digest)
    if fingerprint.sha256 != digest or fingerprint.size_bytes != size_bytes:
        return _fail(HandoffOutcome.HARD_INTEGRITY_FAILURE, digest)
    return None


def _archive_or_reuse_history(
    storage: Storage,
    *,
    history_path: str,
    exact_bytes: bytes,
) -> HandoffResult | None:
    digest = sha256_hex(exact_bytes)
    try:
        storage.write_file(history_path, exact_bytes, overwrite=False)
        # Prefer publish_no_replace semantics: write_file(overwrite=False) is
        # exclusive create for new history objects.
        return None
    except StorageAlreadyExistsError:
        try:
            check = compare_stored_object(
                storage,
                history_path,
                expected_size=len(exact_bytes),
                expected_sha256=digest,
            )
        except StorageError:
            return _fail(HandoffOutcome.TRANSPORT_FAILURE, digest)
        if check is ObjectComparisonResult.EXACT_MATCH:
            return None
        return _fail(HandoffOutcome.HARD_INTEGRITY_FAILURE, digest)
    except StorageCapabilityError:
        return _fail(HandoffOutcome.CAPABILITY_FAILURE, digest)
    except StorageTransportError:
        return _fail(HandoffOutcome.TRANSPORT_FAILURE, digest)
    except StorageError:
        return _fail(HandoffOutcome.TRANSPORT_FAILURE, digest)


def _reconcile_uncertain_commit(
    storage: Storage,
    *,
    request: HandoffRequest,
    digest: str,
    expected_sequence: int,
    journal: HandoffJournal,
) -> HandoffResult:
    """Classify after uncertain atomic_replace using verified remote evidence."""
    recovered = read_authoritative_manifest(storage, request.game_id)
    if (
        recovered.outcome is ManifestReadOutcome.OK
        and recovered.manifest is not None
        and recovered.manifest.protocol.last_operation_id == request.operation_id
        and recovered.manifest.protocol_sequence == expected_sequence
        and recovered.manifest.last_sender_id == request.local_player_id
        and recovered.manifest.accepted_save is not None
        and recovered.manifest.accepted_save.sha256 == digest
        and digest in recovered.manifest.accepted_save_hashes
    ):
        journal.record_historical_acceptance(game_id=request.game_id, sha256=digest)
        journal.clear_in_progress(game_id=request.game_id)
        return HandoffResult(
            HandoffOutcome.IDEMPOTENT_ACK,
            False,
            manifest=recovered.manifest,
            sha256=digest,
        )
    if recovered.outcome is ManifestReadOutcome.OK and recovered.manifest is not None:
        # Unrelated valid remote state — do not attribute to this attempt.
        return HandoffResult(
            HandoffOutcome.TRANSPORT_FAILURE,
            False,
            manifest=recovered.manifest,
            sha256=digest,
        )
    return HandoffResult(HandoffOutcome.TRANSPORT_FAILURE, False, sha256=digest)
