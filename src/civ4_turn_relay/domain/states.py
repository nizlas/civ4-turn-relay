"""Local operational states (design spec §6; names only in P1).

These are local operational modes derived from evidence. They never change
remote turn ownership; the transition engine is out of scope until P4.
"""

from __future__ import annotations

from enum import Enum, unique


@unique
class OperationalState(Enum):
    """Exact local operational state names from the design spec."""

    RECONCILING = "RECONCILING"
    WAITING_FOR_MY_FIRST_SAVE = "WAITING_FOR_MY_FIRST_SAVE"
    WAITING_FOR_OTHER_PLAYER = "WAITING_FOR_OTHER_PLAYER"
    DOWNLOADING = "DOWNLOADING"
    MY_TURN_DOWNLOADED = "MY_TURN_DOWNLOADED"
    CIV_RUNNING = "CIV_RUNNING"
    OUTGOING_SAVE_DETECTED = "OUTGOING_SAVE_DETECTED"
    UPLOADING = "UPLOADING"
    ERROR = "ERROR"
