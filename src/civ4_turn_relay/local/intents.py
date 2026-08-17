"""Pure orchestration intents returned by reconciliation (no process I/O)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum, unique

from civ4_turn_relay.domain import DomainValidationError


@unique
class OrchestrationIntentKind(Enum):
    """Commands for outer adapters; never mutate remote ownership alone."""

    WAIT = "wait"
    DOWNLOAD_ACCEPTED_SAVE = "download_accepted_save"
    START_CIV = "start_civ"
    RESUME_OR_FOCUS_CIV = "resume_or_focus_civ"
    PREPARE_OR_SEND_HANDOFF = "prepare_or_send_handoff"
    REQUIRE_CANDIDATE_SELECTION = "require_candidate_selection"
    # Fully Managed only: the committed (or idempotently acknowledged) turn
    # entitles Relay to close the exact Civ process it launched. Civ's modal
    # PBEM confirmation blocks a graceful close, so the app layer terminates
    # the entitled process directly after re-verifying its identity.
    CLOSE_CIV_AFTER_COMMIT = "close_civ_after_commit"
    SHOW_POST_COMMIT_CLOSE_WARNING = "show_post_commit_close_warning"
    REQUIRE_USER_ACTION = "require_user_action"
    RETRY = "retry"


_PRIMITIVE = (str, int, float, bool, type(None))


def _validate_payload(payload: Mapping[str, object] | None) -> dict[str, object] | None:
    if payload is None:
        return None
    if not isinstance(payload, Mapping):
        raise DomainValidationError(
            "expected a mapping or None",
            field_path="payload",
        )
    normalized: dict[str, object] = {}
    for key, value in payload.items():
        if not isinstance(key, str) or not key:
            raise DomainValidationError(
                "payload keys must be non-empty strings",
                field_path="payload",
            )
        if not isinstance(value, _PRIMITIVE):
            raise DomainValidationError(
                "payload values must be primitives only",
                field_path=f"payload.{key}",
            )
        normalized[key] = value
    return normalized


@dataclass(frozen=True, slots=True)
class OrchestrationIntent:
    """One orchestration command with optional primitive payload."""

    kind: OrchestrationIntentKind
    payload: dict[str, object] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, OrchestrationIntentKind):
            raise DomainValidationError(
                "expected an OrchestrationIntentKind",
                field_path="kind",
            )
        object.__setattr__(self, "payload", _validate_payload(self.payload))
