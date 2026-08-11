"""Storage port, verification helpers, and failure-injectable in-memory fake.

This package is adapter-independent. Protocol ownership and handoff logic live
outside it. The primary fake is in-memory and models OpenSSH/SFTP semantics
needed by later protocol tests.
"""

from civ4_turn_relay.storage.errors import (
    StorageAlreadyExistsError,
    StorageCapabilityError,
    StorageError,
    StorageInvalidPathError,
    StorageNotEmptyError,
    StorageNotFoundError,
    StorageTransportError,
    StorageWrongKindError,
)
from civ4_turn_relay.storage.fake import FakeStorage, StorageSnapshot
from civ4_turn_relay.storage.faults import (
    FaultController,
    FaultMoment,
    FaultScheduleError,
    StorageOp,
)
from civ4_turn_relay.storage.paramiko_sftp import ParamikoStorage
from civ4_turn_relay.storage.port import (
    Storage,
    StorageCapabilities,
    StorageEntry,
    StorageEntryKind,
)
from civ4_turn_relay.storage.verification import (
    ObjectComparisonResult,
    ObjectFingerprint,
    compare_stored_object,
    fingerprint_bytes,
    read_fingerprint,
)

__all__ = [
    "FakeStorage",
    "FaultController",
    "FaultMoment",
    "FaultScheduleError",
    "ObjectComparisonResult",
    "ObjectFingerprint",
    "ParamikoStorage",
    "Storage",
    "StorageAlreadyExistsError",
    "StorageCapabilities",
    "StorageCapabilityError",
    "StorageEntry",
    "StorageEntryKind",
    "StorageError",
    "StorageInvalidPathError",
    "StorageNotEmptyError",
    "StorageNotFoundError",
    "StorageOp",
    "StorageSnapshot",
    "StorageTransportError",
    "StorageWrongKindError",
    "compare_stored_object",
    "fingerprint_bytes",
    "read_fingerprint",
]
