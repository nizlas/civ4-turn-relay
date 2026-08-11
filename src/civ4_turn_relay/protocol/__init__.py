"""Protocol-layer foundations: paths, manifest access, init, locks, handoff.

Depends only on the stdlib, P1 domain models, and the P2 ``Storage`` port.
Concrete fakes, UI, Paramiko, Watchdog, and Civ integration stay outside.
"""

from civ4_turn_relay.protocol.handoff import (
    DEFAULT_MAX_SAVE_BYTES,
    HandoffOutcome,
    HandoffRequest,
    HandoffResult,
    commit_handoff,
)
from civ4_turn_relay.protocol.hash_classify import (
    HashClassification,
    PriorAttemptEvidence,
    classify_candidate_hash,
)
from civ4_turn_relay.protocol.initialize import (
    InitializeOutcome,
    InitializeResult,
    initialize_match,
)
from civ4_turn_relay.protocol.journal import (
    HandoffJournal,
    InMemoryOperationJournal,
    InProgressHandoff,
    OperationJournal,
)
from civ4_turn_relay.protocol.lock import (
    LOCK_TTL_SECONDS,
    LockAcquireOutcome,
    LockAcquireResult,
    LockDocument,
    LockInspection,
    LockInspectionKind,
    LockRepairAuditEvent,
    LockRepairOutcome,
    LockRepairResult,
    acquire_or_resume_upload_lock,
    build_lock_document,
    confirm_lock_ownership,
    inspect_upload_lock,
    release_owned_upload_lock,
    repair_abandoned_upload_lock,
)
from civ4_turn_relay.protocol.manifest_access import (
    ManifestReadOutcome,
    ManifestReadResult,
    read_authoritative_manifest,
)
from civ4_turn_relay.protocol.paths import GamePaths, extension_from_original_filename
from civ4_turn_relay.protocol.players import next_human_player_id

__all__ = [
    "DEFAULT_MAX_SAVE_BYTES",
    "GamePaths",
    "HandoffJournal",
    "HandoffOutcome",
    "HandoffRequest",
    "HandoffResult",
    "HashClassification",
    "InMemoryOperationJournal",
    "InProgressHandoff",
    "InitializeOutcome",
    "InitializeResult",
    "LOCK_TTL_SECONDS",
    "LockAcquireOutcome",
    "LockAcquireResult",
    "LockDocument",
    "LockInspection",
    "LockInspectionKind",
    "LockRepairAuditEvent",
    "LockRepairOutcome",
    "LockRepairResult",
    "ManifestReadOutcome",
    "ManifestReadResult",
    "OperationJournal",
    "PriorAttemptEvidence",
    "acquire_or_resume_upload_lock",
    "build_lock_document",
    "classify_candidate_hash",
    "commit_handoff",
    "confirm_lock_ownership",
    "extension_from_original_filename",
    "initialize_match",
    "inspect_upload_lock",
    "next_human_player_id",
    "read_authoritative_manifest",
    "release_owned_upload_lock",
    "repair_abandoned_upload_lock",
]
