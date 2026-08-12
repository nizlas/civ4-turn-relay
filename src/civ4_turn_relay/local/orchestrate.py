"""Pure managed-mode orchestration intent decisions (design §8.5)."""

from __future__ import annotations

from dataclasses import dataclass

from civ4_turn_relay.domain import (
    DomainValidationError,
    OperationalState,
    TurnHandlingMode,
    validate_utc_timestamp,
    validate_windows_local_path,
)
from civ4_turn_relay.local.detect import DetectionOutcome, DetectionResult
from civ4_turn_relay.local.handoff_evidence import HandoffEvidence
from civ4_turn_relay.local.intents import OrchestrationIntent, OrchestrationIntentKind
from civ4_turn_relay.local.records import MatchLocalRecords, PostCommitCloseRecord
from civ4_turn_relay.protocol.handoff import HandoffOutcome


@dataclass(frozen=True, slots=True)
class ProcessObservation:
    """Supplied process facts from an adapter (P7 later).

    ``process_create_time_ns`` is the precise creation token from the process
    backend; the second-resolution UTC timestamp is diagnostic only and never
    the sole identity check.
    """

    pid: int
    process_start_time_utc: str
    process_create_time_ns: int
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
        if isinstance(self.process_create_time_ns, bool) or not isinstance(
            self.process_create_time_ns, int
        ):
            raise DomainValidationError(
                "expected an integer creation token",
                field_path="process_create_time_ns",
            )
        if self.process_create_time_ns <= 0:
            raise DomainValidationError(
                "expected a positive creation token",
                field_path="process_create_time_ns",
            )
        validate_windows_local_path(self.executable_path, field_path="executable_path")
        if not isinstance(self.running, bool):
            raise DomainValidationError(
                "expected a boolean running flag",
                field_path="running",
            )


def observation_matches_association(
    observation: ProcessObservation | None,
    *,
    pid: int,
    process_create_time_ns: int,
    executable_path: str,
) -> bool:
    """True only when observation identity exactly matches the durable association.

    Identity is pid + precise creation token + executable path; a matching
    whole-second timestamp with a different creation token is a mismatch.
    """
    if observation is None:
        return False
    return (
        observation.pid == pid
        and observation.process_create_time_ns == process_create_time_ns
        and observation.executable_path == executable_path
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


def _matched_running_process(
    records: MatchLocalRecords, observation: ProcessObservation | None
) -> bool:
    association = records.process_association
    if association is None:
        return False
    return (
        observation_matches_association(
            observation,
            pid=association.pid,
            process_create_time_ns=association.process_create_time_ns,
            executable_path=association.executable_path,
        )
        and observation is not None
        and observation.running
    )


def _post_commit_outcome(handoff: HandoffEvidence | None) -> bool:
    if handoff is None:
        return False
    return handoff.outcome in {
        HandoffOutcome.COMMITTED,
        HandoffOutcome.IDEMPOTENT_ACK,
    }


def _close_target(
    records: MatchLocalRecords,
    handoff: HandoffEvidence | None,
) -> PostCommitCloseRecord | None:
    pending = records.pending_post_commit_close
    if pending is not None:
        if handoff is not None and (
            pending.operation_id != handoff.operation_id
            or pending.sha256 != handoff.sha256
            or pending.source_protocol_sequence != handoff.source_protocol_sequence
        ):
            return None
        return pending
    association = records.process_association
    if handoff is None or association is None:
        return None
    if association.protocol_sequence != handoff.source_protocol_sequence:
        return None
    return PostCommitCloseRecord(
        game_id=handoff.game_id,
        source_protocol_sequence=handoff.source_protocol_sequence,
        operation_id=handoff.operation_id,
        sha256=handoff.sha256,
        pid=association.pid,
        process_start_time_utc=association.process_start_time_utc,
        process_create_time_ns=association.process_create_time_ns,
        executable_path=association.executable_path,
        close_requested=False,
    )


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
    matched_running = _matched_running_process(records, process_observation)

    if turn_handling_mode is TurnHandlingMode.FULLY_MANAGED:
        close_target = None
        if _post_commit_outcome(handoff_evidence):
            close_target = _close_target(records, handoff_evidence)
        elif records.pending_post_commit_close is not None:
            close_target = records.pending_post_commit_close
        if (
            close_target is not None
            and not close_target.close_requested
            and observation_matches_association(
                process_observation,
                pid=close_target.pid,
                process_create_time_ns=close_target.process_create_time_ns,
                executable_path=close_target.executable_path,
            )
            and process_observation is not None
            and process_observation.running
        ):
            intents.append(
                OrchestrationIntent(
                    OrchestrationIntentKind.REQUEST_GRACEFUL_CLOSE,
                    payload={
                        "pid": close_target.pid,
                        "process_start_time_utc": close_target.process_start_time_utc,
                        "process_create_time_ns": (close_target.process_create_time_ns),
                        "executable_path": close_target.executable_path,
                        "operation_id": close_target.operation_id,
                        "sha256": close_target.sha256,
                        "source_protocol_sequence": (
                            close_target.source_protocol_sequence
                        ),
                    },
                )
            )

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

    launch_states = {
        OperationalState.WAITING_FOR_MY_FIRST_SAVE,
        OperationalState.MY_TURN_DOWNLOADED,
    }

    if turn_handling_mode is TurnHandlingMode.FULLY_MANAGED:
        if state in launch_states:
            has_outgoing = detection is not None and detection.outcome in {
                DetectionOutcome.ONE_CANDIDATE,
                DetectionOutcome.STABILIZING,
                DetectionOutcome.MULTIPLE_CANDIDATES,
            }
            if matched_running:
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
                if records.process_association is not None and matched_running:
                    intents.append(
                        OrchestrationIntent(OrchestrationIntentKind.RESUME_OR_FOCUS_CIV)
                    )
                elif records.process_association is None:
                    intents.append(
                        OrchestrationIntent(OrchestrationIntentKind.START_CIV)
                    )
    else:
        # Standard: never auto-launch or auto-close; user Start/Resume only.
        if user_requested_start and state in launch_states:
            if matched_running:
                intents.append(
                    OrchestrationIntent(OrchestrationIntentKind.RESUME_OR_FOCUS_CIV)
                )
            else:
                intents.append(OrchestrationIntent(OrchestrationIntentKind.START_CIV))

    if state is OperationalState.ERROR:
        intents.append(OrchestrationIntent(OrchestrationIntentKind.RETRY))

    return tuple(intents)
