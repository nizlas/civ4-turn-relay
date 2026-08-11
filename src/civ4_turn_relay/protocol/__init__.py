"""Protocol-layer foundations: paths, manifest access, init, classification.

Depends only on the stdlib, P1 domain models, and the P2 ``Storage`` port.
Concrete fakes, UI, Paramiko, Watchdog, and Civ integration stay outside.
"""

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
from civ4_turn_relay.protocol.journal import InMemoryOperationJournal, OperationJournal
from civ4_turn_relay.protocol.manifest_access import (
    ManifestReadOutcome,
    ManifestReadResult,
    read_authoritative_manifest,
)
from civ4_turn_relay.protocol.paths import GamePaths
from civ4_turn_relay.protocol.players import next_human_player_id

__all__ = [
    "GamePaths",
    "HashClassification",
    "InMemoryOperationJournal",
    "InitializeOutcome",
    "InitializeResult",
    "ManifestReadOutcome",
    "ManifestReadResult",
    "OperationJournal",
    "PriorAttemptEvidence",
    "classify_candidate_hash",
    "initialize_match",
    "next_human_player_id",
    "read_authoritative_manifest",
]
