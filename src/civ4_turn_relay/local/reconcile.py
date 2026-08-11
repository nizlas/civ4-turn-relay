"""Startup reconciliation: manifest, download, detect, intents (design §9.1)."""

from __future__ import annotations

from dataclasses import dataclass, replace

from civ4_turn_relay.domain import (
    DomainValidationError,
    MatchConfig,
    OperationalState,
    validate_client_id,
)
from civ4_turn_relay.local.baseline import capture_play_session_baseline
from civ4_turn_relay.local.clock import Clock
from civ4_turn_relay.local.detect import (
    DetectionOutcome,
    DetectionResult,
    observe_outgoing_candidates,
)
from civ4_turn_relay.local.diagnostics import DiagnosticEvent, emit_diagnostic
from civ4_turn_relay.local.intents import OrchestrationIntent, OrchestrationIntentKind
from civ4_turn_relay.local.journal import DurableHandoffJournal
from civ4_turn_relay.local.orchestrate import (
    HandoffEvidence,
    ProcessObservation,
    decide_intents,
)
from civ4_turn_relay.local.promote import PromoteOutcome, promote_verified_download
from civ4_turn_relay.local.records import (
    LaunchAttemptRecord,
    MatchLocalRecords,
    VerifiedRemoteRecord,
)
from civ4_turn_relay.local.store import LocalStore
from civ4_turn_relay.protocol.download import (
    DownloadOutcome,
    DownloadRequest,
    VerifiedDownloadEvidence,
    download_accepted_save,
)
from civ4_turn_relay.protocol.handoff import DEFAULT_MAX_SAVE_BYTES
from civ4_turn_relay.protocol.manifest_access import (
    ManifestReadOutcome,
    read_authoritative_manifest,
)
from civ4_turn_relay.storage import Storage


@dataclass(frozen=True, slots=True)
class ReconcileRequest:
    """Validated inputs for one reconciliation pass."""

    client_id: str
    now_utc: str
    max_save_bytes: int = DEFAULT_MAX_SAVE_BYTES
    process_observation: ProcessObservation | None = None
    user_requested_start: bool = False
    handoff_evidence: HandoffEvidence | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "client_id",
            validate_client_id(self.client_id, field_path="client_id"),
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


@dataclass(frozen=True, slots=True)
class ReconcileResult:
    """Immutable reconciliation output."""

    operational_state: OperationalState
    records: MatchLocalRecords
    intents: tuple[OrchestrationIntent, ...]
    diagnostics: tuple[DiagnosticEvent, ...]
    persisted: bool
    retry_required: bool


def _launch_key_from_verified(
    verified: VerifiedRemoteRecord | None,
) -> tuple[int, str | None] | None:
    if verified is None:
        return None
    return (verified.protocol_sequence, verified.accepted_sha256)


def _prior_download_evidence(
    records: MatchLocalRecords,
) -> VerifiedDownloadEvidence | None:
    downloaded = records.downloaded_save
    if downloaded is None:
        return None
    return VerifiedDownloadEvidence(
        game_id=records.game_id,
        protocol_sequence=downloaded.protocol_sequence,
        sha256=downloaded.sha256,
        size_bytes=downloaded.size_bytes,
    )


def _apply_detection(
    records: MatchLocalRecords,
    detection: DetectionResult | None,
) -> tuple[MatchLocalRecords, OperationalState | None]:
    if detection is None:
        return records, None
    updated = replace(records, stability_observations=detection.observations)
    if detection.outcome is DetectionOutcome.ONE_CANDIDATE and detection.candidates:
        updated = replace(
            updated,
            outgoing_candidate=detection.candidates[0],
        )
        return updated, OperationalState.OUTGOING_SAVE_DETECTED
    return updated, None


def reconcile_match(
    storage: Storage,
    store: LocalStore,
    config: MatchConfig,
    *,
    client_id: str,
    journal: DurableHandoffJournal,
    clock: Clock,
    now_utc: str,
    max_save_bytes: int = DEFAULT_MAX_SAVE_BYTES,
    process_observation: ProcessObservation | None = None,
    user_requested_start: bool = False,
    handoff_evidence: HandoffEvidence | None = None,
) -> ReconcileResult:
    """Reconcile local state against the authoritative remote manifest."""
    del journal  # reserved for in-progress cleanup extensions
    request = ReconcileRequest(
        client_id=client_id,
        now_utc=now_utc,
        max_save_bytes=max_save_bytes,
        process_observation=process_observation,
        user_requested_start=user_requested_start,
        handoff_evidence=handoff_evidence,
    )

    diagnostics: list[DiagnosticEvent] = []
    retry_required = False
    detection: DetectionResult | None = None
    records = store.load_match_state_or_empty(config.game_id)
    read = read_authoritative_manifest(storage, config.game_id)

    if read.outcome is not ManifestReadOutcome.OK or read.manifest is None:
        diagnostics.append(
            emit_diagnostic(
                "manifest_read_failed",
                fields={"outcome": read.outcome.value},
                message="authoritative manifest unavailable",
            )
        )
        state = OperationalState.ERROR
        records = replace(
            records,
            last_operational_state=state,
            last_transition_reason="manifest_read_failed",
            retry_count=records.retry_count + 1,
        )
        intents = decide_intents(
            config.turn_handling_mode,
            config.allow_force_close_after_commit,
            state,
            records,
            None,
            process_observation,
            handoff_evidence,
            user_requested_start,
        )
        store.write_match_state(records)
        return ReconcileResult(
            operational_state=state,
            records=records,
            intents=intents,
            diagnostics=tuple(diagnostics),
            persisted=True,
            retry_required=True,
        )

    manifest = read.manifest
    verified = VerifiedRemoteRecord(
        protocol_sequence=manifest.protocol_sequence,
        accepted_sha256=(
            None if manifest.accepted_save is None else manifest.accepted_save.sha256
        ),
    )
    records = replace(records, verified_remote=verified)

    is_owner = manifest.current_player_id == config.local_player_id
    in_progress = records.in_progress_handoff is not None

    if not is_owner:
        if in_progress:
            state = OperationalState.UPLOADING
            records = replace(
                records,
                last_operational_state=state,
                last_transition_reason="handoff_in_progress",
            )
        else:
            state = OperationalState.WAITING_FOR_OTHER_PLAYER
            records = replace(
                records,
                last_operational_state=state,
                last_transition_reason="not_current_owner",
            )
    elif manifest.protocol_sequence == 0 and manifest.accepted_save is None:
        state = OperationalState.WAITING_FOR_MY_FIRST_SAVE
        records = replace(
            records,
            last_operational_state=state,
            last_transition_reason="awaiting_first_save",
        )
        detection = observe_outgoing_candidates(
            config.pbem_save_directory,
            config.save_matching,
            records,
            manifest.accepted_save_hashes,
            clock=clock,
            max_save_bytes=request.max_save_bytes,
        )
        records, detected_state = _apply_detection(records, detection)
        if detected_state is not None:
            state = detected_state
    elif is_owner and manifest.accepted_save is not None:
        prior = _prior_download_evidence(records)
        download = download_accepted_save(
            storage,
            DownloadRequest(
                game_id=config.game_id,
                local_player_id=config.local_player_id,
                max_save_bytes=request.max_save_bytes,
                prior_evidence=prior,
            ),
        )
        if download.outcome in {
            DownloadOutcome.VERIFIED,
            DownloadOutcome.ALREADY_VERIFIED,
        }:
            if (
                download.outcome is DownloadOutcome.VERIFIED
                and download.artifact is not None
            ):
                promoted = promote_verified_download(
                    download.artifact,
                    config.pbem_save_directory,
                )
                if (
                    promoted.outcome
                    in {
                        PromoteOutcome.PROMOTED,
                        PromoteOutcome.ALREADY_PRESENT,
                    }
                    and promoted.record is not None
                ):
                    records = replace(records, downloaded_save=promoted.record)
                elif promoted.outcome is PromoteOutcome.CONFLICT:
                    state = OperationalState.ERROR
                    records = replace(
                        records,
                        last_operational_state=state,
                        last_transition_reason="promote_conflict",
                    )
                    retry_required = True
                else:
                    state = OperationalState.ERROR
                    records = replace(
                        records,
                        last_operational_state=state,
                        last_transition_reason=f"promote_{promoted.outcome.value}",
                    )
                    retry_required = True
            elif (
                download.outcome is DownloadOutcome.ALREADY_VERIFIED
                and records.downloaded_save is not None
            ):
                pass
            if not retry_required:
                state = OperationalState.MY_TURN_DOWNLOADED
                records = replace(
                    records,
                    last_operational_state=state,
                    last_transition_reason="accepted_save_ready",
                )
        else:
            state = OperationalState.DOWNLOADING
            records = replace(
                records,
                last_operational_state=state,
                last_transition_reason=f"download_{download.outcome.value}",
            )
            retry_required = download.outcome not in {
                DownloadOutcome.NOT_CURRENT_OWNER,
                DownloadOutcome.NO_DOWNLOADABLE_TURN,
            }

        if not retry_required and state is not OperationalState.DOWNLOADING:
            detection = observe_outgoing_candidates(
                config.pbem_save_directory,
                config.save_matching,
                records,
                manifest.accepted_save_hashes,
                clock=clock,
                max_save_bytes=request.max_save_bytes,
            )
            records, detected_state = _apply_detection(records, detection)
            if detected_state is not None:
                state = detected_state
        else:
            detection = None
    else:
        state = OperationalState.WAITING_FOR_OTHER_PLAYER
        records = replace(
            records,
            last_operational_state=state,
            last_transition_reason="no_actionable_turn",
        )

    if (
        process_observation is not None
        and process_observation.running
        and records.play_session_baseline is not None
        and state
        in {
            OperationalState.MY_TURN_DOWNLOADED,
            OperationalState.WAITING_FOR_MY_FIRST_SAVE,
        }
    ):
        state = OperationalState.CIV_RUNNING
        records = replace(
            records,
            last_operational_state=state,
            last_transition_reason="civ_running",
        )

    if handoff_evidence is not None and handoff_evidence.outcome_name in {
        "committed",
        "idempotent_ack",
    }:
        processed = records.processed_outgoing_hashes
        if handoff_evidence.sha256 not in processed:
            processed = (*processed, handoff_evidence.sha256)
        state = OperationalState.WAITING_FOR_OTHER_PLAYER
        records = replace(
            records,
            processed_outgoing_hashes=processed,
            outgoing_candidate=None,
            in_progress_handoff=None,
            last_operational_state=state,
            last_transition_reason=f"handoff_{handoff_evidence.outcome_name}",
        )

    intents = decide_intents(
        config.turn_handling_mode,
        config.allow_force_close_after_commit,
        state,
        records,
        detection,
        process_observation,
        handoff_evidence,
        user_requested_start,
    )

    start_requested = any(
        intent.kind is OrchestrationIntentKind.START_CIV for intent in intents
    )
    if start_requested:
        key = _launch_key_from_verified(records.verified_remote)
        try:
            baseline = capture_play_session_baseline(
                config.pbem_save_directory,
                config.save_matching,
                protocol_sequence=key[0] if key else 0,
                accepted_sha256=key[1] if key else None,
                recorded_at=now_utc,
                max_save_bytes=request.max_save_bytes,
            )
        except DomainValidationError:
            baseline = None
        if baseline is None:
            intents = tuple(
                intent
                for intent in intents
                if intent.kind is not OrchestrationIntentKind.START_CIV
            )
            diagnostics.append(
                emit_diagnostic(
                    "baseline_capture_failed",
                    message="cannot auto-launch without baseline",
                )
            )
        else:
            records = replace(records, play_session_baseline=baseline)
            if key is not None:
                records = replace(
                    records,
                    launch_attempt=LaunchAttemptRecord(
                        protocol_sequence=key[0],
                        accepted_sha256=key[1],
                        attempted_at=now_utc,
                    ),
                )

    records = replace(records, last_operational_state=state)
    store.write_match_state(records)
    diagnostics.append(
        emit_diagnostic(
            "reconcile_complete",
            fields={
                "operational_state": state.value,
                "protocol_sequence": manifest.protocol_sequence,
            },
            message="reconciliation finished",
        )
    )

    return ReconcileResult(
        operational_state=state,
        records=records,
        intents=intents,
        diagnostics=tuple(diagnostics),
        persisted=True,
        retry_required=retry_required,
    )
