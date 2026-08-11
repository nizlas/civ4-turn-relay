"""Pure managed-mode orchestration intent decisions (design §8.5)."""

from __future__ import annotations

from dataclasses import dataclass

from civ4_turn_relay.domain import (
    DomainValidationError,
    OperationalState,
    TurnHandlingMode,
    validate_sha256_hex,
    validate_utc_timestamp,
    validate_windows_local_path,
)
from civ4_turn_relay.local.detect import DetectionOutcome, DetectionResult
from civ4_turn_relay.local.intents import OrchestrationIntent, OrchestrationIntentKind
from civ4_turn_relay.local.records import MatchLocalRecords
from civ4_turn_relay.protocol.handoff import HandoffOutcome


@dataclass(frozen=True, slots=True)
class ProcessObservation:
    """Supplied process facts from an adapter (P7 later)."""

    pid: int
    process_start_time_utc: str
    executable_path: str
    running: bool

    def __post_init__(self) -> None:
        if isinstance(self.pid, bool) or not isinstance(self.pid, int):
            raise DomainValidationError(
                "expected an integer pid",
                field_path="pid",
            )
        if self.pid <= 0:
            raise DomainValidationError(
                "expected a positive process id",
                field_path="pid",
            )
        object.__setattr__(
            self,
            "process_start_time_utc",
            validate_utc_timestamp(
                self.process_start_time_utc, field_path="process_start_time_utc"
            ),
        )
        validate_windows_local_path(self.executable_path, field_path="executable_path")
        if not isinstance(self.running, bool):
            raise DomainValidationError(
                "expected a boolean running flag",
                field_path="running",
            )


@dataclass(frozen=True, slots=True)
class HandoffEvidence:
    """Local proof of a completed or idempotent handoff."""

    outcome_name: str
    sha256: str
    protocol_sequence: int
    operation_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.outcome_name, str) or not self.outcome_name:
            raise DomainValidationError(
                "expected a non-empty outcome name",
                field_path="outcome_name",
            )
        object.__setattr__(
            self, "sha256", validate_sha256_hex(self.sha256, field_path="sha256")
        )
        if isinstance(self.protocol_sequence, bool) or not isinstance(
            self.protocol_sequence, int
        ):
            raise DomainValidationError(
                "expected an integer protocol_sequence",
                field_path="protocol_sequence",
            )
        if self.operation_id is not None and not isinstance(self.operation_id, str):
            raise DomainValidationError(
                "expected a string operation_id or None",
                field_path="operation_id",
            )


def _launch_key(records: MatchLocalRecords) -> tuple[int, str | None] | None:
    verified = records.verified_remote
    if verified is None:
        return None
    return (verified.protocol_sequence, verified.accepted_sha256)


def _launch_already_attempted(records: MatchLocalRecords) -> bool:
    key = _launch_key(records)
    attempt = records.launch_attempt
    if key is None or attempt is None:
        return False
    return (
        attempt.protocol_sequence,
        attempt.accepted_sha256,
    ) == key


def _process_running(
    records: MatchLocalRecords, observation: ProcessObservation | None
) -> bool:
    if observation is not None and observation.running:
        return True
    association = records.process_association
    return association is not None and observation is not None and observation.running


def _post_commit_outcome(handoff: HandoffEvidence | None) -> bool:
    if handoff is None:
        return False
    return handoff.outcome_name in {
        HandoffOutcome.COMMITTED.value,
        HandoffOutcome.IDEMPOTENT_ACK.value,
    }


def decide_intents(
    turn_handling_mode: TurnHandlingMode,
    allow_force_close_after_commit: bool,
    state: OperationalState,
    records: MatchLocalRecords,
    detection: DetectionResult | None,
    process_observation: ProcessObservation | None,
    handoff_evidence: HandoffEvidence | None,
    user_requested_start: bool,
) -> tuple[OrchestrationIntent, ...]:
    """Return orchestration intents without performing side effects."""
    del allow_force_close_after_commit  # never creates terminate intents

    intents: list[OrchestrationIntent] = []

    if state in {
        OperationalState.WAITING_FOR_OTHER_PLAYER,
        OperationalState.RECONCILING,
    }:
        intents.append(OrchestrationIntent(OrchestrationIntentKind.WAIT))
        return tuple(intents)

    if (
        detection is not None
        and detection.outcome is DetectionOutcome.MULTIPLE_CANDIDATES
    ):
        intents.append(
            OrchestrationIntent(OrchestrationIntentKind.REQUIRE_CANDIDATE_SELECTION)
        )

    if detection is not None and detection.outcome is DetectionOutcome.MISSING_BASELINE:
        intents.append(
            OrchestrationIntent(
                OrchestrationIntentKind.REQUIRE_USER_ACTION,
                payload={"reason": "missing_baseline"},
            )
        )

    if state is OperationalState.OUTGOING_SAVE_DETECTED:
        if records.play_session_baseline is not None and (
            detection is None or detection.outcome is DetectionOutcome.ONE_CANDIDATE
        ):
            intents.append(
                OrchestrationIntent(OrchestrationIntentKind.PREPARE_OR_SEND_HANDOFF)
            )

    running = _process_running(records, process_observation)
    launch_states = {
        OperationalState.WAITING_FOR_MY_FIRST_SAVE,
        OperationalState.MY_TURN_DOWNLOADED,
    }

    if turn_handling_mode is TurnHandlingMode.FULLY_MANAGED:
        if _post_commit_outcome(handoff_evidence) and (
            running or records.process_association is not None
        ):
            intents.append(
                OrchestrationIntent(OrchestrationIntentKind.REQUEST_GRACEFUL_CLOSE)
            )

        if state in launch_states:
            has_outgoing = detection is not None and detection.outcome in {
                DetectionOutcome.ONE_CANDIDATE,
                DetectionOutcome.STABILIZING,
                DetectionOutcome.MULTIPLE_CANDIDATES,
            }
            if running:
                intents.append(
                    OrchestrationIntent(OrchestrationIntentKind.RESUME_OR_FOCUS_CIV)
                )
            elif has_outgoing:
                pass
            elif _launch_already_attempted(records) and not user_requested_start:
                intents.append(
                    OrchestrationIntent(
                        OrchestrationIntentKind.REQUIRE_USER_ACTION,
                        payload={"reason": "civ_exited_without_outgoing"},
                    )
                )
            elif user_requested_start:
                intents.append(OrchestrationIntent(OrchestrationIntentKind.START_CIV))
            elif not _launch_already_attempted(records):
                if records.process_association is not None:
                    intents.append(
                        OrchestrationIntent(OrchestrationIntentKind.RESUME_OR_FOCUS_CIV)
                    )
                else:
                    intents.append(
                        OrchestrationIntent(OrchestrationIntentKind.START_CIV)
                    )
    else:
        # Standard: never auto-launch or auto-close; user Start/Resume only.
        if user_requested_start and state in launch_states:
            if running:
                intents.append(
                    OrchestrationIntent(OrchestrationIntentKind.RESUME_OR_FOCUS_CIV)
                )
            else:
                intents.append(OrchestrationIntent(OrchestrationIntentKind.START_CIV))

    if state is OperationalState.ERROR:
        intents.append(OrchestrationIntent(OrchestrationIntentKind.RETRY))

    return tuple(intents)
