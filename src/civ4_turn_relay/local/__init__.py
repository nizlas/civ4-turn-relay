"""Local persistence adapters: durable schemas, LocalStore, journals.

Reconciliation, baseline scanning, outgoing detection, watching, and process
orchestration remain later P4 slices.
"""

from civ4_turn_relay.local.errors import (
    LocalStoreCorruptError,
    LocalStoreError,
    LocalStoreIOError,
    LocalStoreMissingError,
    LocalStoreUnsupportedSchemaError,
)
from civ4_turn_relay.local.journal import DurableHandoffJournal
from civ4_turn_relay.local.json_store import AtomicJsonStore, atomic_write_bytes
from civ4_turn_relay.local.records import (
    LOCAL_RECORDS_SCHEMA_VERSION,
    BaselineEntry,
    DownloadedSaveRecord,
    LaunchAttemptRecord,
    MatchLocalRecords,
    OutgoingCandidateRecord,
    PlaySessionBaseline,
    ProcessAssociationRecord,
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
    "AtomicJsonStore",
    "BaselineEntry",
    "DownloadedSaveRecord",
    "DurableHandoffJournal",
    "InstallationIdentity",
    "LaunchAttemptRecord",
    "LocalStore",
    "LocalStoreCorruptError",
    "LocalStoreError",
    "LocalStoreIOError",
    "LocalStoreMissingError",
    "LocalStoreUnsupportedSchemaError",
    "MatchLocalRecords",
    "OutgoingCandidateRecord",
    "PlaySessionBaseline",
    "ProcessAssociationRecord",
    "VerifiedRemoteRecord",
    "atomic_write_bytes",
]
