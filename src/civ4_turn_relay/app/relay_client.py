"""Production headless Relay client composing P1–P4 layers.

Adapters observe; this service orchestrates. The server manifest remains
authoritative. Local cached state never advances ownership. All remote
mutations go through the P3 protocol APIs.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC
from pathlib import Path

from civ4_turn_relay.app.process_runtime import (
    ProcessCoordinator,
    ProcessStatus,
    ProcessStatusSnapshot,
    close_payload_matches_record,
    identity_from_close_record,
)
from civ4_turn_relay.app.snapshot import MatchClientSnapshot, PendingUserAction
from civ4_turn_relay.domain import (
    DomainValidationError,
    MatchConfig,
    OperationalState,
    TurnHandlingMode,
    validate_game_id,
    validate_operation_id,
    validate_utc_timestamp,
)
from civ4_turn_relay.fs import MatchMonitor, WatchdogWatcher
from civ4_turn_relay.local import (
    Clock,
    DetectionOutcome,
    DetectionResult,
    DiagnosticEvent,
    DurableHandoffJournal,
    HandoffEvidence,
    LocalStore,
    MatchLocalRecords,
    OrchestrationIntent,
    OrchestrationIntentKind,
    OutgoingCandidateRecord,
    PostCommitCloseRecord,
    ProcessAssociationRecord,
    ProcessObservation,
    ReconcileResult,
    SystemClock,
    attribute_handoff_result,
    emit_diagnostic,
    observe_outgoing_candidates,
    reconcile_match,
    revalidate_candidate_file,
)
from civ4_turn_relay.process import (
    CloseRequestOutcome,
    CloseRequestResult,
    FocusOutcome,
    FocusResult,
    GuardedLaunchOutcome,
    GuardedLaunchResult,
    LaunchPlan,
    ProbeOutcome,
    ProcessIdentity,
    ProcessSupervisor,
    TerminateOutcome,
    TerminateResult,
    build_launch_plan,
    observation_from_identity,
)
from civ4_turn_relay.protocol import (
    DEFAULT_MAX_SAVE_BYTES,
    HandoffOutcome,
    HandoffRequest,
    HandoffResult,
    InitializeResult,
    commit_handoff,
    initialize_match,
    read_authoritative_manifest,
)
from civ4_turn_relay.protocol.manifest_access import ManifestReadOutcome
from civ4_turn_relay.storage import Storage, StorageError, StorageTransportError


def _default_now_utc() -> str:
    from datetime import datetime

    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


LAUNCH_LOG_FILENAME = "launch_diagnostics.log"


@dataclass
class _MatchSession:
    """Per-match runtime session (never shared across matches)."""

    config: MatchConfig
    monitor: MatchMonitor | None = None
    process_observation: ProcessObservation | None = None
    selected_candidate_path: str | None = None
    last_reconcile: ReconcileResult | None = None
    last_handoff: HandoffResult | None = None
    last_detection: DetectionResult | None = None
    storage_available: bool = True
    monitoring_available: bool = False
    dirty: bool = True
    last_auto_handoff_sha256: str | None = None
    last_close_operation_id: str | None = None
    last_outgoing_bytes: bytes | None = None
    last_outgoing_filename: str | None = None
    coordinator: ProcessCoordinator | None = None
    # Fully Managed gets one automatic launch attempt per received turn in
    # this Relay session. The key is deliberately session-local: after a
    # Relay restart, the currently pending turn gets one fresh attempt, while
    # periodic polling never retries a failed spawn in a loop.
    auto_launch_attempted_key: tuple[int, str | None] | None = None
    # Dedup key for the durable launch diagnostics log: an entry is written
    # only when (game_id, protocol_sequence, accepted_sha256, event/reason)
    # changes or a real launch attempt occurs — never once per poll.
    last_launch_log_key: tuple[str, str, str, str] | None = None


class RelayClient:
    """Headless multi-match Relay orchestration service."""

    def __init__(
        self,
        *,
        store: LocalStore,
        storage: Storage,
        clock: Clock | None = None,
        poll_interval_seconds: float = 10.0,
        now_utc_fn: Callable[[], str] | None = None,
        operation_id_factory: Callable[[], str] | None = None,
        owns_storage: bool = False,
        auto_execute_managed_handoff: bool = True,
        enable_monitoring: bool = True,
        process_supervisor: ProcessSupervisor | None = None,
        civ4_executable: str | None = None,
        steam_app_id: str | None = None,
        steam_executable: str | None = None,
    ) -> None:
        if not isinstance(store, LocalStore):
            raise TypeError("store must be a LocalStore instance")
        if not isinstance(storage, Storage):
            raise TypeError("storage must satisfy Storage")
        if process_supervisor is not None and not isinstance(
            process_supervisor, ProcessSupervisor
        ):
            raise TypeError("process_supervisor must satisfy ProcessSupervisor")
        if civ4_executable is not None and not isinstance(civ4_executable, str):
            raise TypeError("civ4_executable must be a string or None")
        if steam_app_id is not None and not isinstance(steam_app_id, str):
            raise TypeError("steam_app_id must be a string or None")
        if steam_executable is not None and not isinstance(steam_executable, str):
            raise TypeError("steam_executable must be a string or None")
        if (steam_app_id is None) != (steam_executable is None):
            raise ValueError(
                "steam_app_id and steam_executable must be configured together"
            )
        if isinstance(poll_interval_seconds, bool) or not isinstance(
            poll_interval_seconds, int | float
        ):
            raise TypeError("poll_interval_seconds must be numeric")
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be positive")
        self._store = store
        self._storage = storage
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._poll_interval_seconds = float(poll_interval_seconds)
        self._now_utc_fn = now_utc_fn if now_utc_fn is not None else _default_now_utc
        self._operation_id_factory = (
            operation_id_factory
            if operation_id_factory is not None
            else (lambda: str(uuid.uuid4()))
        )
        self._owns_storage = owns_storage
        self._auto_execute_managed_handoff = auto_execute_managed_handoff
        self._enable_monitoring = enable_monitoring
        self._process_supervisor = process_supervisor
        self._civ4_executable = civ4_executable
        self._steam_app_id = steam_app_id
        self._steam_executable = steam_executable
        self._sessions: dict[str, _MatchSession] = {}
        self._closed = False
        # Ensure installation identity exists without inventing remote state.
        self._store.get_or_create_installation_identity()

    @property
    def store(self) -> LocalStore:
        return self._store

    @property
    def storage(self) -> Storage:
        return self._storage

    @property
    def client_id(self) -> str:
        return self._store.get_or_create_installation_identity()

    def close(self) -> None:
        """Stop watchers and optionally close owned storage. Idempotent."""
        if self._closed:
            return
        for session in list(self._sessions.values()):
            if session.monitor is not None:
                session.monitor.stop()
                session.monitor = None
        self._sessions.clear()
        if self._owns_storage:
            close = getattr(self._storage, "close", None)
            if callable(close):
                close()
        self._closed = True

    def __enter__(self) -> RelayClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def initialize_or_join(
        self,
        config: MatchConfig,
        *,
        operation_id: str | None = None,
    ) -> InitializeResult:
        """Initialize or join a match, persist config, and open a session."""
        self._ensure_open()
        if not isinstance(config, MatchConfig):
            raise TypeError("config must be a MatchConfig instance")
        op_id = (
            validate_operation_id(operation_id, field_path="operation_id")
            if operation_id is not None
            else self._operation_id_factory()
        )
        try:
            result = initialize_match(self._storage, config, operation_id=op_id)
        except StorageTransportError:
            session = self._ensure_session(config)
            session.storage_available = False
            raise
        except StorageError:
            session = self._ensure_session(config)
            session.storage_available = False
            raise
        if result.initialized:
            self._store.write_match_config(config)
            self._ensure_session(config)
            self._start_monitor(config.game_id)
        return result

    def open_match(self, config: MatchConfig) -> None:
        """Register an already-configured match and start monitoring."""
        self._ensure_open()
        if not isinstance(config, MatchConfig):
            raise TypeError("config must be a MatchConfig instance")
        self._store.write_match_config(config)
        self._ensure_session(config)
        self._start_monitor(config.game_id)

    def reconcile(
        self,
        game_id: str,
        *,
        user_requested_start: bool = False,
        now_utc: str | None = None,
        handoff_evidence: HandoffEvidence | None = None,
    ) -> ReconcileResult:
        """Reconcile one match against the authoritative remote manifest."""
        self._ensure_open()
        session = self._require_session(game_id)
        stamp = validate_utc_timestamp(
            now_utc if now_utc is not None else self._now_utc_fn(),
            field_path="now_utc",
        )
        journal = DurableHandoffJournal(self._store, game_id=session.config.game_id)
        try:
            result = reconcile_match(
                self._storage,
                self._store,
                session.config,
                client_id=self.client_id,
                journal=journal,
                clock=self._clock,
                now_utc=stamp,
                process_observation=session.process_observation,
                user_requested_start=user_requested_start,
                handoff_evidence=handoff_evidence,
            )
            session.storage_available = True
        except StorageTransportError as error:
            session.storage_available = False
            result = self._transport_reconcile_failure(session, stamp, error)
        session.last_reconcile = result
        session.dirty = False
        if (
            handoff_evidence is not None
            and handoff_evidence.outcome_name
            in {
                HandoffOutcome.COMMITTED.value,
                HandoffOutcome.IDEMPOTENT_ACK.value,
            }
            and handoff_evidence.operation_id is not None
        ):
            session.last_close_operation_id = handoff_evidence.operation_id
        return result

    def tick(
        self,
        game_id: str,
        *,
        now_utc: str | None = None,
        auto_handoff_operation_id: str | None = None,
    ) -> MatchClientSnapshot:
        """Poll watchers, reconcile, and optionally auto-handoff in managed mode.

        With a configured process supervisor the tick additionally refreshes
        the process observation via an identity probe before reconciling,
        acts on the resulting launch/close intents through the supervisor,
        and tracks any pending post-commit close. Process outcomes never
        advance remote protocol state.
        """
        self._ensure_open()
        session = self._require_session(game_id)
        if session.monitor is not None:
            try:
                session.monitor.poll()
                session.monitoring_available = session.monitor.is_healthy()
            except Exception:
                session.monitoring_available = False
        coordinator = self._coordinator(session)
        if coordinator is not None:
            self._refresh_process_observation(session, coordinator)
        # First reconcile from the authoritative manifest. A Fully Managed
        # profile then gets exactly one automatic attempt for each *new*
        # (sequence, accepted-hash) turn in this Relay session. The old
        # boolean retry flag was consumed by the first-ever turn and could
        # therefore leave a later received turn permanently displaying
        # "Starting automatically…" without a launch attempt.
        result = self.reconcile(game_id, now_utc=now_utc)
        verified = result.records.verified_remote
        launch_key = (
            None
            if verified is None
            else (verified.protocol_sequence, verified.accepted_sha256)
        )
        launch_states = {
            OperationalState.WAITING_FOR_MY_FIRST_SAVE,
            OperationalState.MY_TURN_DOWNLOADED,
        }
        has_start_intent = any(
            intent.kind is OrchestrationIntentKind.START_CIV
            for intent in result.intents
        )
        needs_one_auto_attempt = (
            session.config.turn_handling_mode is TurnHandlingMode.FULLY_MANAGED
            and result.operational_state in launch_states
            and launch_key is not None
            and session.auto_launch_attempted_key != launch_key
            and not has_start_intent
        )
        if needs_one_auto_attempt:
            # This is not a UI click: it grants the normal Fully Managed
            # intent decision one fresh per-turn attempt. It still honours
            # all safety checks (baseline, candidate detection, launch guard,
            # exact process identity).
            result = self.reconcile(
                game_id,
                now_utc=now_utc,
                user_requested_start=True,
            )
            has_start_intent = any(
                intent.kind is OrchestrationIntentKind.START_CIV
                for intent in result.intents
            )
        if has_start_intent and launch_key is not None:
            session.auto_launch_attempted_key = launch_key
        if (
            self._auto_execute_managed_handoff
            and session.config.turn_handling_mode is TurnHandlingMode.FULLY_MANAGED
            and any(
                intent.kind is OrchestrationIntentKind.PREPARE_OR_SEND_HANDOFF
                for intent in result.intents
            )
        ):
            candidate = result.records.outgoing_candidate
            if (
                candidate is not None
                and candidate.sha256 != session.last_auto_handoff_sha256
            ):
                handoff = self.execute_handoff(
                    game_id,
                    operation_id=auto_handoff_operation_id,
                    now_utc=now_utc,
                )
                if handoff.outcome in {
                    HandoffOutcome.COMMITTED,
                    HandoffOutcome.IDEMPOTENT_ACK,
                }:
                    session.last_auto_handoff_sha256 = candidate.sha256
        if coordinator is not None:
            latest = (
                session.last_reconcile if session.last_reconcile is not None else result
            )
            self._act_on_process_intents(session, coordinator, latest, now_utc)
            self._track_close_progress(session, coordinator, now_utc)
        return self.snapshot(game_id)

    def request_start(
        self,
        game_id: str,
        *,
        now_utc: str | None = None,
    ) -> ReconcileResult:
        """Request Start/Resume; launches through the supervisor when configured.

        Without a configured process supervisor this only emits intents
        (previous behavior). A user request bypasses the durable
        launch-attempt suppression, which is the explicit retry path after
        a failed or refused launch.
        """
        self._ensure_open()
        session = self._require_session(game_id)
        coordinator = self._coordinator(session)
        if coordinator is not None:
            self._refresh_process_observation(session, coordinator)
        result = self.reconcile(game_id, user_requested_start=True, now_utc=now_utc)
        if coordinator is not None:
            result = self._act_on_process_intents(session, coordinator, result, now_utc)
        return result

    def set_process_observation(
        self,
        game_id: str,
        observation: ProcessObservation | None,
    ) -> None:
        """Accept process facts observed through the process adapter."""
        self._ensure_open()
        session = self._require_session(game_id)
        session.process_observation = observation
        if observation is not None and observation.running:
            records = self._store.load_match_state_or_empty(session.config.game_id)
            if records.process_association is None:
                verified = records.verified_remote
                updated = replace(
                    records,
                    process_association=ProcessAssociationRecord(
                        protocol_sequence=(
                            0 if verified is None else verified.protocol_sequence
                        ),
                        accepted_sha256=(
                            None if verified is None else verified.accepted_sha256
                        ),
                        pid=observation.pid,
                        process_start_time_utc=observation.process_start_time_utc,
                        process_create_time_ns=observation.process_create_time_ns,
                        executable_path=observation.executable_path,
                        associated_at=observation.process_start_time_utc,
                    ),
                )
                self._store.write_match_state(updated)
        session.dirty = True

    def observe_candidates(self, game_id: str) -> DetectionResult:
        """Observe outgoing candidates for one match."""
        self._ensure_open()
        session = self._require_session(game_id)
        records = self._store.load_match_state_or_empty(session.config.game_id)
        accepted: tuple[str, ...] = ()
        read = read_authoritative_manifest(self._storage, session.config.game_id)
        if read.outcome is ManifestReadOutcome.OK and read.manifest is not None:
            accepted = read.manifest.accepted_save_hashes
        detection = observe_outgoing_candidates(
            session.config.pbem_save_directory,
            session.config.save_matching,
            records,
            accepted,
            clock=self._clock,
            max_save_bytes=DEFAULT_MAX_SAVE_BYTES,
        )
        session.last_detection = detection
        if detection.outcome is DetectionOutcome.ONE_CANDIDATE and detection.candidates:
            updated = replace(
                records,
                outgoing_candidate=detection.candidates[0],
                stability_observations=detection.observations,
            )
            self._store.write_match_state(updated)
        elif detection.outcome is DetectionOutcome.MULTIPLE_CANDIDATES:
            updated = replace(
                records,
                stability_observations=detection.observations,
                outgoing_candidate=None,
            )
            self._store.write_match_state(updated)
        else:
            updated = replace(records, stability_observations=detection.observations)
            self._store.write_match_state(updated)
        session.dirty = True
        return detection

    def select_candidate(self, game_id: str, path: str) -> OutgoingCandidateRecord:
        """Select one candidate when multiple valid candidates exist."""
        self._ensure_open()
        session = self._require_session(game_id)
        detection = self.observe_candidates(game_id)
        if detection.outcome is not DetectionOutcome.MULTIPLE_CANDIDATES:
            raise DomainValidationError(
                "candidate selection requires multiple valid candidates",
                field_path="path",
            )
        chosen: OutgoingCandidateRecord | None = None
        for candidate in detection.candidates:
            if candidate.path == path:
                chosen = candidate
                break
        if chosen is None:
            raise DomainValidationError(
                "selected path is not among the current candidates",
                field_path="path",
            )
        session.selected_candidate_path = path
        records = self._store.load_match_state_or_empty(session.config.game_id)
        self._store.write_match_state(replace(records, outgoing_candidate=chosen))
        session.dirty = True
        return chosen

    def execute_handoff(
        self,
        game_id: str,
        *,
        operation_id: str | None = None,
        now_utc: str | None = None,
        outgoing_bytes: bytes | None = None,
        original_filename: str | None = None,
    ) -> HandoffResult:
        """Prepare and execute a handoff through P3, then reconcile evidence."""
        self._ensure_open()
        session = self._require_session(game_id)
        stamp = validate_utc_timestamp(
            now_utc if now_utc is not None else self._now_utc_fn(),
            field_path="now_utc",
        )
        op_id = (
            validate_operation_id(operation_id, field_path="operation_id")
            if operation_id is not None
            else self._operation_id_factory()
        )
        records = self._store.load_match_state_or_empty(session.config.game_id)
        candidate = records.outgoing_candidate
        source_sequence = (
            0
            if records.verified_remote is None
            else records.verified_remote.protocol_sequence
        )
        if records.in_progress_handoff is not None:
            # Resume the exact in-progress operation when present.
            op_id = records.in_progress_handoff.operation_id
            source_sequence = (
                records.in_progress_handoff.protocol_sequence
                if records.in_progress_handoff.protocol_sequence is not None
                else source_sequence
            )
        if outgoing_bytes is None:
            if candidate is not None:
                payload = revalidate_candidate_file(
                    candidate,
                    pbem_save_directory=session.config.pbem_save_directory,
                    max_save_bytes=DEFAULT_MAX_SAVE_BYTES,
                )
                if payload is None:
                    self._store.write_match_state(
                        replace(
                            records,
                            outgoing_candidate=None,
                            last_transition_reason="candidate_changed_before_handoff",
                        )
                    )
                    raise DomainValidationError(
                        "outgoing candidate changed before handoff",
                        field_path="outgoing_candidate",
                    )
                filename = original_filename or Path(candidate.path).name
            elif session.last_outgoing_bytes is not None:
                payload = session.last_outgoing_bytes
                retry_name = original_filename or session.last_outgoing_filename
                if retry_name is None:
                    raise DomainValidationError(
                        "original_filename is required for retry without candidate",
                        field_path="original_filename",
                    )
                filename = retry_name
            else:
                raise DomainValidationError(
                    "no outgoing candidate selected",
                    field_path="outgoing_candidate",
                )
        else:
            payload = outgoing_bytes
            if original_filename is None:
                if candidate is not None:
                    filename = Path(candidate.path).name
                elif session.last_outgoing_filename is not None:
                    filename = session.last_outgoing_filename
                else:
                    raise DomainValidationError(
                        "original_filename is required without a candidate",
                        field_path="original_filename",
                    )
            else:
                filename = original_filename

        session.last_outgoing_bytes = payload
        session.last_outgoing_filename = filename
        journal = DurableHandoffJournal(self._store, game_id=session.config.game_id)
        handoff_request = HandoffRequest(
            game_id=session.config.game_id,
            local_player_id=session.config.local_player_id,
            client_id=self.client_id,
            operation_id=op_id,
            outgoing_bytes=payload,
            original_filename=filename,
            now_utc=stamp,
        )
        try:
            result = commit_handoff(
                self._storage,
                handoff_request,
                journal=journal,
            )
            session.storage_available = True
        except StorageTransportError:
            session.storage_available = False
            raise
        session.last_handoff = result
        evidence = attribute_handoff_result(
            request=handoff_request,
            result=result,
            source_protocol_sequence=source_sequence,
        )
        if evidence is not None:
            self.reconcile(game_id, now_utc=stamp, handoff_evidence=evidence)
        else:
            # Ambiguous/non-success: reconcile without fabricated success evidence.
            self.reconcile(game_id, now_utc=stamp)
        return result

    def snapshot(self, game_id: str) -> MatchClientSnapshot:
        """Return an immutable UI-ready snapshot for one match."""
        self._ensure_open()
        session = self._require_session(game_id)
        if session.last_reconcile is None or session.dirty:
            self.reconcile(game_id)
        assert session.last_reconcile is not None
        result = session.last_reconcile
        current_player_id: str | None = None
        protocol_sequence: int | None = None
        try:
            read = read_authoritative_manifest(self._storage, session.config.game_id)
            if read.outcome is ManifestReadOutcome.OK and read.manifest is not None:
                current_player_id = read.manifest.current_player_id
                protocol_sequence = read.manifest.protocol_sequence
                session.storage_available = True
        except StorageError:
            session.storage_available = False
        pending = _pending_action(result.intents, result.operational_state)
        status = result.records.last_transition_reason or result.operational_state.value
        latest: DiagnosticEvent | None = (
            result.diagnostics[-1] if result.diagnostics else None
        )
        return MatchClientSnapshot(
            game_id=session.config.game_id,
            display_name=session.config.display_name,
            local_player_id=session.config.local_player_id,
            current_player_id=current_player_id,
            protocol_sequence=protocol_sequence,
            operational_state=result.operational_state,
            turn_handling_mode=session.config.turn_handling_mode,
            primary_status=status,
            pending_user_action=pending,
            intents=result.intents,
            latest_diagnostic=latest,
            monitoring_available=session.monitoring_available,
            storage_available=session.storage_available,
            retry_required=result.retry_required,
        )

    def process_status(self, game_id: str) -> ProcessStatusSnapshot:
        """Return the typed per-match process status for UI display."""
        self._ensure_open()
        session = self._require_session(game_id)
        coordinator = self._coordinator(session)
        if coordinator is None:
            return ProcessStatusSnapshot(
                status=ProcessStatus.UNAVAILABLE,
                message="no process adapter configured",
            )
        records = self._store.load_match_state_or_empty(session.config.game_id)
        association: ProcessIdentity | None = None
        if records.process_association is not None:
            assoc = records.process_association
            association = ProcessIdentity(
                pid=assoc.pid,
                process_start_time_utc=assoc.process_start_time_utc,
                process_create_time_ns=assoc.process_create_time_ns,
                executable_path=assoc.executable_path,
            )
        return coordinator.status_snapshot(association=association)

    def launch_preview(self, game_id: str) -> LaunchPlan:
        """Build the launch plan for this match without launching (dry run)."""
        self._ensure_open()
        session = self._require_session(game_id)
        records = self._store.load_match_state_or_empty(session.config.game_id)
        return self._launch_plan_for(session.config, records)

    def focus_civ(self, game_id: str) -> FocusResult:
        """Focus the associated Civ window after identity verification."""
        self._ensure_open()
        session = self._require_session(game_id)
        coordinator = self._coordinator(session)
        if coordinator is None:
            return FocusResult(
                outcome=FocusOutcome.ADAPTER_UNAVAILABLE,
                message="no process adapter configured",
            )
        identity = self._associated_identity(session, coordinator)
        if identity is None:
            return FocusResult(
                outcome=FocusOutcome.NOT_RUNNING,
                message="no Relay-launched process is associated with this match",
            )
        return coordinator.focus(identity)

    def request_civ_close(self, game_id: str) -> CloseRequestResult:
        """Manually close Civ backed by the durable post-commit entitlement.

        Allowed only when a durable ``pending_post_commit_close`` record
        exists and a fresh probe verifies exactly that identity is running.
        Fully Managed matches retry the direct entitled termination (Civ's
        modal PBEM confirmation blocks a graceful close); Standard matches
        keep the graceful close request and never terminate.
        """
        self._ensure_open()
        session = self._require_session(game_id)
        coordinator = self._coordinator(session)
        if coordinator is None:
            return CloseRequestResult(
                outcome=CloseRequestOutcome.ADAPTER_UNAVAILABLE,
                message="no process adapter configured",
            )
        records = self._store.load_match_state_or_empty(session.config.game_id)
        pending = records.pending_post_commit_close
        if pending is None:
            return CloseRequestResult(
                outcome=CloseRequestOutcome.REQUEST_FAILED,
                message="no committed-turn close entitlement exists for this match",
            )
        identity = identity_from_close_record(pending)
        probe = coordinator.probe(identity)
        if probe.outcome is ProbeOutcome.NOT_RUNNING:
            return CloseRequestResult(outcome=CloseRequestOutcome.NOT_RUNNING)
        if probe.outcome is ProbeOutcome.RUNNING_MISMATCH:
            return CloseRequestResult(
                outcome=CloseRequestOutcome.IDENTITY_MISMATCH,
                message=probe.message,
            )
        if probe.outcome is not ProbeOutcome.RUNNING_MATCH:
            return CloseRequestResult(
                outcome=CloseRequestOutcome.ADAPTER_UNAVAILABLE,
                message=probe.message,
            )
        if session.config.turn_handling_mode is TurnHandlingMode.FULLY_MANAGED:
            terminate = self._terminate_pending_close(
                session, coordinator, pending, None, user_requested=True
            )
            return _close_result_from_terminate(terminate)
        result = coordinator.request_close(
            identity, operation_id=pending.operation_id, allow_repeat=True
        )
        if result is None:
            return CloseRequestResult(
                outcome=CloseRequestOutcome.IDENTITY_MISMATCH,
                message="the entitled process identity could not be verified",
            )
        if (
            result.outcome is CloseRequestOutcome.REQUESTED
            and not pending.close_requested
        ):
            self._store.update_match_state(
                session.config.game_id, _mark_close_requested
            )
            session.last_close_operation_id = pending.operation_id
        return result

    def _coordinator(self, session: _MatchSession) -> ProcessCoordinator | None:
        if self._process_supervisor is None:
            return None
        if session.coordinator is None:
            session.coordinator = ProcessCoordinator(
                supervisor=self._process_supervisor,
                clock=self._clock,
                now_utc_fn=self._now_utc_fn,
                civ4_executable=self._civ4_executable,
            )
        return session.coordinator

    def _associated_identity(
        self, session: _MatchSession, coordinator: ProcessCoordinator
    ) -> ProcessIdentity | None:
        records = self._store.load_match_state_or_empty(session.config.game_id)
        association = records.process_association
        if association is not None:
            return ProcessIdentity(
                pid=association.pid,
                process_start_time_utc=association.process_start_time_utc,
                process_create_time_ns=association.process_create_time_ns,
                executable_path=association.executable_path,
            )
        return coordinator.session_identity

    def _refresh_process_observation(
        self, session: _MatchSession, coordinator: ProcessCoordinator
    ) -> None:
        """Probe the associated identity and update the session observation.

        On an unavailable adapter or failed probe the prior observation is
        kept; a mismatch is remembered by the coordinator so no close,
        focus, or terminate ever targets the reused pid.
        """
        game_id = session.config.game_id
        identity = self._associated_identity(session, coordinator)
        if identity is None:
            return
        probe = coordinator.probe(identity)
        if probe.outcome is ProbeOutcome.RUNNING_MATCH:
            self.set_process_observation(
                game_id, observation_from_identity(identity, running=True)
            )
        elif probe.outcome in {
            ProbeOutcome.NOT_RUNNING,
            ProbeOutcome.RUNNING_MISMATCH,
        }:
            self.set_process_observation(
                game_id, observation_from_identity(identity, running=False)
            )

    def _launch_plan_for(
        self, config: MatchConfig, records: MatchLocalRecords
    ) -> LaunchPlan:
        save_path: str | None = None
        verified = records.verified_remote
        downloaded = records.downloaded_save
        if (
            verified is not None
            and verified.protocol_sequence > 0
            and downloaded is not None
            and downloaded.protocol_sequence == verified.protocol_sequence
        ):
            save_path = downloaded.local_path
        return build_launch_plan(
            executable_path=self._civ4_executable,
            mod_name=config.mod_name,
            save_path=save_path,
            pbem_save_directory=config.pbem_save_directory,
            steam_app_id=self._steam_app_id,
            steam_executable_path=self._steam_executable,
        )

    def _persist_process_association(
        self, game_id: str, identity: ProcessIdentity
    ) -> None:
        """Durably associate the freshly launched identity with this match."""

        def mutate(records: MatchLocalRecords) -> MatchLocalRecords:
            verified = records.verified_remote
            return replace(
                records,
                process_association=ProcessAssociationRecord(
                    protocol_sequence=(
                        0 if verified is None else verified.protocol_sequence
                    ),
                    accepted_sha256=(
                        None if verified is None else verified.accepted_sha256
                    ),
                    pid=identity.pid,
                    process_start_time_utc=identity.process_start_time_utc,
                    process_create_time_ns=identity.process_create_time_ns,
                    executable_path=identity.executable_path,
                    associated_at=identity.process_start_time_utc,
                ),
            )

        self._store.update_match_state(game_id, mutate)

    def _clear_launch_attempt(self, game_id: str) -> None:
        """Restore the launch-attempt key after a deferred guarded launch.

        A deferred launch (existing Civ, busy guard, indeterminate scan)
        spawned nothing, so the durable one-launch-per-sequence/hash key
        written alongside the START_CIV intent must not stay consumed.
        """

        def mutate(records: MatchLocalRecords) -> MatchLocalRecords:
            return replace(records, launch_attempt=None)

        self._store.update_match_state(game_id, mutate)

    def _append_session_diagnostic(
        self, session: _MatchSession, diagnostic: DiagnosticEvent
    ) -> None:
        """Attach a diagnostic to the latest reconcile result for snapshots."""
        if session.last_reconcile is not None:
            session.last_reconcile = replace(
                session.last_reconcile,
                diagnostics=(*session.last_reconcile.diagnostics, diagnostic),
            )

    def _note_guard_cleanup_failure(
        self, session: _MatchSession, launch: GuardedLaunchResult
    ) -> None:
        """Surface a held-guard cleanup failure without changing launch outcome."""
        if not launch.cleanup_failed:
            return
        diagnostic = emit_diagnostic(
            "launch_guard_cleanup_failed",
            fields={
                "cleanup_outcome": (
                    launch.cleanup_outcome.value
                    if launch.cleanup_outcome is not None
                    else "unknown"
                ),
                "launch_outcome": launch.outcome.value,
            },
            message=launch.cleanup_message or "launch-guard cleanup failed",
        )
        self._append_session_diagnostic(session, diagnostic)

    def _record_launch_event(
        self,
        session: _MatchSession,
        event: DiagnosticEvent,
        *,
        dedup_key: tuple[str, str, str, str],
        force: bool,
        now_utc: str | None,
    ) -> None:
        """Append one line to the durable, deduplicated launch diagnostics log.

        The log lives at ``<app-data root>/launch_diagnostics.log`` with one
        event per line: ``<utc> <event-name> <sorted-json-fields> | <message>``.
        A line is written only when ``dedup_key`` changes or ``force`` is set
        (a real launch attempt) — never once per polling interval. Fields are
        already secret-free and hash-shortened by ``emit_diagnostic``.
        """
        if not force and session.last_launch_log_key == dedup_key:
            return
        session.last_launch_log_key = dedup_key
        stamp = now_utc if now_utc is not None else self._now_utc_fn()
        line = f"{stamp} {event.name} " + json.dumps(
            event.fields, sort_keys=True, default=str
        )
        if event.message:
            line += f" | {event.message}"
        try:
            path = self._store.root / LAUNCH_LOG_FILENAME
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError:
            # Diagnostics must never affect orchestration or launching.
            pass

    def _note_launch_attempt(
        self,
        session: _MatchSession,
        records: MatchLocalRecords,
        plan: LaunchPlan,
        launch: GuardedLaunchResult | None,
        now_utc: str | None,
    ) -> None:
        """Record a real launch attempt (or its local refusal) durably."""
        verified = records.verified_remote
        sequence = 0 if verified is None else verified.protocol_sequence
        accepted = None if verified is None else verified.accepted_sha256
        if launch is None:
            guarded = "refused_locally"
            message = plan.reason or "launch already in flight or process running"
            identity_persisted = False
        else:
            guarded = launch.outcome.value
            message = launch.message
            identity_persisted = (
                launch.outcome is GuardedLaunchOutcome.LAUNCHED
                and launch.identity is not None
            )
        fields: dict[str, object] = {
            "game_id": session.config.game_id,
            "protocol_sequence": sequence,
            "accepted_sha256": accepted,
            "plan_outcome": plan.outcome.value,
            "plan_reason": plan.reason or None,
            "guarded_outcome": guarded,
            "identity_persisted": identity_persisted,
        }
        if plan.command is not None:
            fields["command"] = plan.command.dry_run_preview()
        event = emit_diagnostic("launch_attempted", fields=fields, message=message)
        self._append_session_diagnostic(session, event)
        self._record_launch_event(
            session,
            event,
            dedup_key=(
                session.config.game_id,
                str(sequence),
                str(accepted),
                f"attempt:{guarded}",
            ),
            force=launch is not None,
            now_utc=now_utc,
        )

    def _note_launch_suppression(
        self,
        session: _MatchSession,
        result: ReconcileResult,
        now_utc: str | None,
    ) -> None:
        """Durably log why no automatic launch happened, deduplicated."""
        suppression = next(
            (
                diagnostic
                for diagnostic in reversed(result.diagnostics)
                if diagnostic.name == "auto_launch_suppressed"
            ),
            None,
        )
        if suppression is None:
            return
        verified = result.records.verified_remote
        sequence = 0 if verified is None else verified.protocol_sequence
        accepted = None if verified is None else verified.accepted_sha256
        reason = suppression.fields.get("reason", "unknown")
        self._record_launch_event(
            session,
            suppression,
            dedup_key=(
                session.config.game_id,
                str(sequence),
                str(accepted),
                f"suppressed:{reason}",
            ),
            force=False,
            now_utc=now_utc,
        )

    def _act_on_process_intents(
        self,
        session: _MatchSession,
        coordinator: ProcessCoordinator,
        result: ReconcileResult,
        now_utc: str | None,
    ) -> ReconcileResult:
        """Act on START_CIV and CLOSE_CIV_AFTER_COMMIT reconcile intents."""
        game_id = session.config.game_id
        if any(
            intent.kind is OrchestrationIntentKind.START_CIV
            for intent in result.intents
        ):
            if coordinator.availability().available:
                records = self._store.load_match_state_or_empty(game_id)
                plan = self._launch_plan_for(session.config, records)
                launch = coordinator.attempt_launch(plan)
                if (
                    launch is not None
                    and launch.outcome is GuardedLaunchOutcome.LAUNCHED
                    and launch.identity is not None
                ):
                    self._persist_process_association(game_id, launch.identity)
                    self.set_process_observation(
                        game_id,
                        observation_from_identity(launch.identity, running=True),
                    )
                    result = self.reconcile(game_id, now_utc=now_utc)
                elif launch is not None and launch.deferred:
                    # Nothing was spawned, adopted, or associated. The
                    # reconcile that emitted START_CIV already persisted the
                    # durable launch-attempt key; restore it so the deferral
                    # never counts as a consumed launch and a later ordinary
                    # tick (or explicit Start) retries the guarded launch.
                    self._clear_launch_attempt(game_id)
                self._note_launch_attempt(session, records, plan, launch, now_utc)
                if launch is not None:
                    self._note_guard_cleanup_failure(session, launch)
        else:
            if result.operational_state not in {
                OperationalState.WAITING_FOR_MY_FIRST_SAVE,
                OperationalState.MY_TURN_DOWNLOADED,
            }:
                # No launch is wanted anymore; drop any stale waiting status.
                coordinator.clear_launch_deferral()
            self._note_launch_suppression(session, result, now_utc)
        close_intent = next(
            (
                intent
                for intent in result.intents
                if intent.kind is OrchestrationIntentKind.CLOSE_CIV_AFTER_COMMIT
            ),
            None,
        )
        if (
            close_intent is not None
            and session.config.turn_handling_mode is TurnHandlingMode.FULLY_MANAGED
        ):
            self._close_entitled_after_commit(
                session, coordinator, close_intent, now_utc
            )
        return result

    def _close_entitled_after_commit(
        self,
        session: _MatchSession,
        coordinator: ProcessCoordinator,
        intent: OrchestrationIntent,
        now_utc: str | None,
    ) -> None:
        """Terminate only the exact durable entitlement named by the intent."""
        game_id = session.config.game_id
        payload = intent.payload or {}
        records = self._store.load_match_state_or_empty(game_id)
        pending = records.pending_post_commit_close
        if pending is None or not close_payload_matches_record(payload, pending):
            return
        self._terminate_pending_close(session, coordinator, pending, now_utc)

    def _terminate_pending_close(
        self,
        session: _MatchSession,
        coordinator: ProcessCoordinator,
        pending: PostCommitCloseRecord,
        now_utc: str | None,
        *,
        user_requested: bool = False,
    ) -> TerminateResult | None:
        """Run the direct post-commit termination and record typed evidence.

        The handoff this entitlement belongs to is already committed on the
        server; nothing here can roll it back. A refusal or failure only
        leaves Civilization open with a truthful status.
        """
        game_id = session.config.game_id
        identity = identity_from_close_record(pending)
        result = coordinator.terminate_after_commit(
            identity, pending, user_requested=user_requested
        )
        if result is None:
            return None
        session.last_close_operation_id = pending.operation_id
        if not pending.close_requested:
            self._store.update_match_state(game_id, _mark_close_requested)
        self._append_session_diagnostic(
            session,
            emit_diagnostic(
                "post_commit_close",
                fields={
                    "outcome": result.outcome.value,
                    "operation_id": pending.operation_id,
                    "source_protocol_sequence": pending.source_protocol_sequence,
                    "sha256": pending.sha256,
                    "pid": pending.pid,
                    "safely_closed": coordinator.safely_closed,
                },
                message=_POST_COMMIT_CLOSE_MESSAGES.get(result.outcome, ""),
            ),
        )
        if coordinator.safely_closed or (
            result.outcome is TerminateOutcome.IDENTITY_MISMATCH
        ):
            # Either probing confirmed the entitled process is gone, or its
            # pid is reused by a different process — in both cases the exact
            # entitled process no longer exists and the entitlement is done.
            self.set_process_observation(
                game_id, observation_from_identity(identity, running=False)
            )
            self.reconcile(game_id, now_utc=now_utc)
        return result

    def _track_close_progress(
        self,
        session: _MatchSession,
        coordinator: ProcessCoordinator,
        now_utc: str | None,
    ) -> None:
        """Advance a pending post-commit close and recover after a restart.

        Fully Managed restart recovery: a durable entitlement whose close
        action was already initiated (``close_requested``) gets exactly one
        fresh termination attempt per Relay session against the re-verified
        exact identity. Close completion is only ever concluded from
        probing, never from timers or the termination call alone.
        """
        game_id = session.config.game_id
        records = self._store.load_match_state_or_empty(game_id)
        pending = records.pending_post_commit_close
        if (
            pending is not None
            and pending.close_requested
            and session.config.turn_handling_mode is TurnHandlingMode.FULLY_MANAGED
        ):
            self._terminate_pending_close(session, coordinator, pending, now_utc)
        identity = coordinator.close_identity
        if identity is None or coordinator.safely_closed:
            return
        probe = coordinator.probe(identity)
        if probe.outcome is ProbeOutcome.NOT_RUNNING:
            self.set_process_observation(
                game_id, observation_from_identity(identity, running=False)
            )
            self.reconcile(game_id, now_utc=now_utc)
            coordinator.note_safely_closed()
            return
        if probe.outcome is ProbeOutcome.RUNNING_MISMATCH:
            coordinator.drop_close_attempt(
                probe.message or "identity could not be verified"
            )

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("RelayClient is closed")

    def _ensure_session(self, config: MatchConfig) -> _MatchSession:
        existing = self._sessions.get(config.game_id)
        if existing is not None:
            existing.config = config
            return existing
        session = _MatchSession(config=config)
        self._sessions[config.game_id] = session
        return session

    def _require_session(self, game_id: str) -> _MatchSession:
        validated = validate_game_id(game_id, field_path="game_id")
        session = self._sessions.get(validated)
        if session is not None:
            return session
        config = self._store.load_match_config(validated)
        return self._ensure_session(config)

    def _start_monitor(self, game_id: str) -> None:
        session = self._require_session(game_id)
        if not self._enable_monitoring:
            session.monitoring_available = False
            return
        if session.monitor is not None:
            session.monitor.stop()
        monitor = MatchMonitor(
            clock=self._clock,
            poll_interval_seconds=self._poll_interval_seconds,
            primary=WatchdogWatcher(),
        )

        def _on_event(_event: object) -> None:
            session.dirty = True

        try:
            monitor.start(session.config.pbem_save_directory, _on_event)
            session.monitor = monitor
            session.monitoring_available = monitor.is_healthy()
        except Exception:
            session.monitor = None
            session.monitoring_available = False

    def _transport_reconcile_failure(
        self,
        session: _MatchSession,
        now_utc: str,
        error: StorageTransportError,
    ) -> ReconcileResult:
        del error
        records = self._store.load_match_state_or_empty(session.config.game_id)
        state = OperationalState.ERROR
        records = replace(
            records,
            last_operational_state=state,
            last_transition_reason="storage_transport_failure",
            retry_count=records.retry_count + 1,
        )
        self._store.write_match_state(records)
        intent = OrchestrationIntent(OrchestrationIntentKind.RETRY)
        return ReconcileResult(
            operational_state=state,
            records=records,
            intents=(intent,),
            diagnostics=(),
            persisted=True,
            retry_required=True,
        )


_POST_COMMIT_CLOSE_MESSAGES: dict[TerminateOutcome, str] = {
    TerminateOutcome.TERMINATED: (
        "the turn is safely sent; Relay terminated the exact Civilization "
        "process it launched for this turn"
    ),
    TerminateOutcome.NOT_RUNNING: (
        "the turn is safely sent and the Relay-launched Civilization "
        "process had already exited; nothing was terminated"
    ),
    TerminateOutcome.IDENTITY_MISMATCH: (
        "the entitled process identity could not be re-verified (the pid "
        "may be reused); no process was terminated"
    ),
    TerminateOutcome.ADAPTER_UNAVAILABLE: (
        "the process adapter is unavailable; Civilization was not closed"
    ),
    TerminateOutcome.TERMINATE_FAILED: (
        "the turn is safely sent, but terminating Civilization failed; "
        "close it manually"
    ),
}


def _close_result_from_terminate(
    terminate: TerminateResult | None,
) -> CloseRequestResult:
    """Map a user-requested entitled termination onto the close-result type."""
    if terminate is None:
        return CloseRequestResult(
            outcome=CloseRequestOutcome.IDENTITY_MISMATCH,
            message="the entitled process identity could not be verified",
        )
    if terminate.outcome is TerminateOutcome.TERMINATED:
        return CloseRequestResult(outcome=CloseRequestOutcome.REQUESTED)
    if terminate.outcome is TerminateOutcome.NOT_RUNNING:
        return CloseRequestResult(outcome=CloseRequestOutcome.NOT_RUNNING)
    if terminate.outcome is TerminateOutcome.IDENTITY_MISMATCH:
        return CloseRequestResult(
            outcome=CloseRequestOutcome.IDENTITY_MISMATCH,
            message=terminate.message,
        )
    if terminate.outcome is TerminateOutcome.ADAPTER_UNAVAILABLE:
        return CloseRequestResult(
            outcome=CloseRequestOutcome.ADAPTER_UNAVAILABLE,
            message=terminate.message,
        )
    return CloseRequestResult(
        outcome=CloseRequestOutcome.REQUEST_FAILED,
        message=terminate.message or "terminating Civilization failed",
    )


def _mark_close_requested(records: MatchLocalRecords) -> MatchLocalRecords:
    """Flip the durable close_requested flag when an entitlement exists."""
    pending = records.pending_post_commit_close
    if pending is None or pending.close_requested:
        return records
    return replace(
        records,
        pending_post_commit_close=replace(pending, close_requested=True),
    )


def _pending_action(
    intents: tuple[OrchestrationIntent, ...],
    state: OperationalState,
) -> PendingUserAction:
    kinds = {intent.kind for intent in intents}
    if OrchestrationIntentKind.REQUIRE_CANDIDATE_SELECTION in kinds:
        return PendingUserAction.SELECT_CANDIDATE
    if OrchestrationIntentKind.RETRY in kinds or state is OperationalState.ERROR:
        return PendingUserAction.RETRY
    if OrchestrationIntentKind.PREPARE_OR_SEND_HANDOFF in kinds:
        return PendingUserAction.CONFIRM_SEND
    if OrchestrationIntentKind.START_CIV in kinds or (
        OrchestrationIntentKind.RESUME_OR_FOCUS_CIV in kinds
    ):
        return PendingUserAction.START_OR_RESUME
    if OrchestrationIntentKind.WAIT in kinds:
        return PendingUserAction.WAIT
    if OrchestrationIntentKind.REQUIRE_USER_ACTION in kinds:
        for intent in intents:
            if (
                intent.kind is OrchestrationIntentKind.REQUIRE_USER_ACTION
                and intent.payload is not None
                and intent.payload.get("reason") == "civ_exited_without_outgoing"
            ):
                # A prior Civ launch exited (or an earlier Relay version
                # recorded an unverified attempt). Fully Managed must not
                # relaunch in a loop, but the user needs an explicit,
                # visible Start / Resume action.
                return PendingUserAction.START_OR_RESUME
        return PendingUserAction.OTHER
    return PendingUserAction.NONE
