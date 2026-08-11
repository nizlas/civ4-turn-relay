"""Production headless Relay client composing P1–P4 layers.

Adapters observe; this service orchestrates. The server manifest remains
authoritative. Local cached state never advances ownership. All remote
mutations go through the P3 protocol APIs.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC
from pathlib import Path

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
    OrchestrationIntent,
    OrchestrationIntentKind,
    OutgoingCandidateRecord,
    ProcessAssociationRecord,
    ProcessObservation,
    ReconcileResult,
    SystemClock,
    attribute_handoff_result,
    observe_outgoing_candidates,
    reconcile_match,
    revalidate_candidate_file,
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
    ) -> None:
        if not isinstance(store, LocalStore):
            raise TypeError("store must be a LocalStore instance")
        if not isinstance(storage, Storage):
            raise TypeError("storage must satisfy Storage")
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
        """Poll watchers, reconcile, and optionally auto-handoff in managed mode."""
        self._ensure_open()
        session = self._require_session(game_id)
        if session.monitor is not None:
            try:
                session.monitor.poll()
                session.monitoring_available = session.monitor.is_healthy()
            except Exception:
                session.monitoring_available = False
        result = self.reconcile(game_id, now_utc=now_utc)
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
        return self.snapshot(game_id)

    def request_start(
        self,
        game_id: str,
        *,
        now_utc: str | None = None,
    ) -> ReconcileResult:
        """Request Start/Resume; emits intents only (no real process launch)."""
        return self.reconcile(game_id, user_requested_start=True, now_utc=now_utc)

    def set_process_observation(
        self,
        game_id: str,
        observation: ProcessObservation | None,
    ) -> None:
        """Accept process facts from a future P7 adapter."""
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
        return PendingUserAction.OTHER
    return PendingUserAction.NONE
