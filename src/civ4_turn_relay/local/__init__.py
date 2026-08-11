"""Local persistence adapters: durable schemas, LocalStore, journals."""

from civ4_turn_relay.local.baseline import capture_play_session_baseline
from civ4_turn_relay.local.clock import Clock, FakeClock, SystemClock
from civ4_turn_relay.local.detect import (
    STABILITY_INTERVAL_SECONDS,
    DetectionOutcome,
    DetectionResult,
    observe_outgoing_candidates,
)
from civ4_turn_relay.local.diagnostics import DiagnosticEvent, emit_diagnostic
from civ4_turn_relay.local.errors import (
    LocalStoreCorruptError,
    LocalStoreError,
    LocalStoreIOError,
    LocalStoreMissingError,
    LocalStoreUnsupportedSchemaError,
)
from civ4_turn_relay.local.intents import OrchestrationIntent, OrchestrationIntentKind
from civ4_turn_relay.local.journal import DurableHandoffJournal
from civ4_turn_relay.local.json_store import AtomicJsonStore, atomic_write_bytes
from civ4_turn_relay.local.orchestrate import (
    HandoffEvidence,
    ProcessObservation,
    decide_intents,
)
from civ4_turn_relay.local.promote import (
    PromoteOutcome,
    PromoteResult,
    promote_verified_download,
)
from civ4_turn_relay.local.reconcile import (
    ReconcileRequest,
    ReconcileResult,
    reconcile_match,
)
from civ4_turn_relay.local.records import (
    LOCAL_RECORDS_SCHEMA_VERSION,
    BaselineEntry,
    DownloadedSaveRecord,
    LaunchAttemptRecord,
    MatchLocalRecords,
    OutgoingCandidateRecord,
    PlaySessionBaseline,
    ProcessAssociationRecord,
    StabilityObservation,
    VerifiedRemoteRecord,
)
from civ4_turn_relay.local.store import (
    INSTALLATION_SCHEMA_VERSION,
    InstallationIdentity,
    LocalStore,
)

__all__ = [
    "INSTALLATION_SCHEMA_VERSION",
    "LOCAL_RECORDS_SCHEMA_VERSION",
    "STABILITY_INTERVAL_SECONDS",
    "AtomicJsonStore",
    "BaselineEntry",
    "Clock",
    "DetectionOutcome",
    "DetectionResult",
    "DiagnosticEvent",
    "DownloadedSaveRecord",
    "DurableHandoffJournal",
    "FakeClock",
    "HandoffEvidence",
    "InstallationIdentity",
    "LaunchAttemptRecord",
    "LocalStore",
    "LocalStoreCorruptError",
    "LocalStoreError",
    "LocalStoreIOError",
    "LocalStoreMissingError",
    "LocalStoreUnsupportedSchemaError",
    "MatchLocalRecords",
    "OrchestrationIntent",
    "OrchestrationIntentKind",
    "OutgoingCandidateRecord",
    "PlaySessionBaseline",
    "ProcessAssociationRecord",
    "ProcessObservation",
    "PromoteOutcome",
    "PromoteResult",
    "ReconcileRequest",
    "ReconcileResult",
    "StabilityObservation",
    "SystemClock",
    "VerifiedRemoteRecord",
    "atomic_write_bytes",
    "capture_play_session_baseline",
    "decide_intents",
    "emit_diagnostic",
    "observe_outgoing_candidates",
    "promote_verified_download",
    "reconcile_match",
]
