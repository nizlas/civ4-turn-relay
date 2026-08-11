"""Immutable local durable-record models (protocol §10; design §8–§9.1).

These records explain recovery and suppress duplicate local work. They are
never authoritative for ownership: on conflict the remote manifest wins.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from civ4_turn_relay.domain import (
    DomainValidationError,
    OperationalState,
    validate_game_id,
    validate_sha256_hex,
    validate_utc_timestamp,
    validate_windows_local_path,
)
from civ4_turn_relay.domain.construction import (
    canonicalize_tuple,
    require_optional_instance,
    require_optional_string,
)
from civ4_turn_relay.domain.serialization import (
    check_exact_keys,
    get_array,
    get_integer,
    get_optional_object,
    get_optional_string,
    get_string,
    parse_json_object_bytes,
    to_canonical_json_bytes,
)
from civ4_turn_relay.protocol.journal import InProgressHandoff

LOCAL_RECORDS_SCHEMA_VERSION = 1

_BASELINE_ENTRY_KEYS = ("path", "sha256", "size_bytes")
_PLAY_SESSION_BASELINE_KEYS = (
    "accepted_sha256",
    "entries",
    "protocol_sequence",
    "recorded_at",
)
_STABILITY_OBSERVATION_KEYS = ("observed_at_seconds", "path", "size_bytes")
_VERIFIED_REMOTE_KEYS = ("accepted_sha256", "protocol_sequence")
_DOWNLOADED_SAVE_KEYS = ("local_path", "protocol_sequence", "sha256", "size_bytes")
_OUTGOING_CANDIDATE_KEYS = ("path", "sha256", "size_bytes")
_LAUNCH_ATTEMPT_KEYS = ("accepted_sha256", "attempted_at", "protocol_sequence")
_PROCESS_ASSOCIATION_KEYS = (
    "accepted_sha256",
    "associated_at",
    "executable_path",
    "pid",
    "process_start_time_utc",
    "protocol_sequence",
)
_MATCH_LOCAL_REQUIRED_KEYS = ("game_id", "schema_version")
_MATCH_LOCAL_OPTIONAL_KEYS = (
    "attempted_handoff_hashes",
    "downloaded_save",
    "historically_accepted_hashes",
    "in_progress_handoff",
    "last_error_class",
    "last_operational_state",
    "last_transition_reason",
    "launch_attempt",
    "outgoing_candidate",
    "play_session_baseline",
    "process_association",
    "processed_outgoing_hashes",
    "retry_count",
    "stability_observations",
    "verified_remote",
)


def _require_true_int(value: int, field_path: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise DomainValidationError(
            "expected an integer (booleans are not integers)",
            field_path=field_path,
        )


def _require_non_negative_int(value: int, field_path: str) -> None:
    _require_true_int(value, field_path)
    if value < 0:
        raise DomainValidationError(
            "expected a non-negative integer", field_path=field_path
        )


def _optional_sha256(value: object, *, field_path: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise DomainValidationError(
            "expected a SHA-256 digest or null", field_path=field_path
        )
    return validate_sha256_hex(value, field_path=field_path)


def _parse_operational_state(value: object, *, field_path: str) -> OperationalState:
    if isinstance(value, OperationalState):
        return value
    if not isinstance(value, str):
        raise DomainValidationError(
            "expected an operational state string", field_path=field_path
        )
    try:
        return OperationalState(value)
    except ValueError:
        raise DomainValidationError(
            "expected a known operational state name",
            field_path=field_path,
        ) from None


@dataclass(frozen=True, slots=True)
class BaselineEntry:
    """One stable PBEM file observed when recording a play-session baseline."""

    path: str
    sha256: str
    size_bytes: int

    def __post_init__(self) -> None:
        validate_windows_local_path(self.path, field_path="path")
        object.__setattr__(
            self, "sha256", validate_sha256_hex(self.sha256, field_path="sha256")
        )
        _require_non_negative_int(self.size_bytes, "size_bytes")

    def to_mapping(self) -> dict[str, object]:
        return {
            "path": self.path,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }

    @classmethod
    def from_mapping(
        cls, mapping: Mapping[str, object], *, path: str = ""
    ) -> BaselineEntry:
        check_exact_keys(mapping, _BASELINE_ENTRY_KEYS, path=path)
        try:
            return cls(
                path=get_string(mapping, "path", path=path),
                sha256=get_string(mapping, "sha256", path=path),
                size_bytes=get_integer(mapping, "size_bytes", path=path),
            )
        except DomainValidationError as error:
            raise error.with_prefix(path) from None


@dataclass(frozen=True, slots=True)
class PlaySessionBaseline:
    """Durable pre-launch snapshot of matching PBEM files (protocol §6.1)."""

    recorded_at: str
    protocol_sequence: int
    accepted_sha256: str | None
    entries: tuple[BaselineEntry, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "recorded_at",
            validate_utc_timestamp(self.recorded_at, field_path="recorded_at"),
        )
        _require_non_negative_int(self.protocol_sequence, "protocol_sequence")
        object.__setattr__(
            self,
            "accepted_sha256",
            _optional_sha256(self.accepted_sha256, field_path="accepted_sha256"),
        )
        if self.protocol_sequence == 0 and self.accepted_sha256 is not None:
            raise DomainValidationError(
                "sequence 0 must not carry an accepted hash",
                field_path="accepted_sha256",
            )
        if self.protocol_sequence > 0 and self.accepted_sha256 is None:
            raise DomainValidationError(
                "sequence > 0 requires an accepted hash",
                field_path="accepted_sha256",
            )
        entries = canonicalize_tuple(
            self.entries,
            BaselineEntry,
            field_path="entries",
            item_label="BaselineEntry instance",
        )
        object.__setattr__(self, "entries", entries)
        seen_paths: set[str] = set()
        for index, entry in enumerate(entries):
            if entry.path in seen_paths:
                raise DomainValidationError(
                    "duplicate baseline path",
                    field_path=f"entries[{index}].path",
                )
            seen_paths.add(entry.path)

    def to_mapping(self) -> dict[str, object]:
        return {
            "accepted_sha256": self.accepted_sha256,
            "entries": [entry.to_mapping() for entry in self.entries],
            "protocol_sequence": self.protocol_sequence,
            "recorded_at": self.recorded_at,
        }

    @classmethod
    def from_mapping(
        cls, mapping: Mapping[str, object], *, path: str = ""
    ) -> PlaySessionBaseline:
        check_exact_keys(mapping, _PLAY_SESSION_BASELINE_KEYS, path=path)
        raw_entries = get_array(mapping, "entries", path=path)
        entries: list[BaselineEntry] = []
        for index, item in enumerate(raw_entries):
            item_path = f"{path}.entries[{index}]" if path else f"entries[{index}]"
            if not isinstance(item, Mapping):
                raise DomainValidationError("expected an object", field_path=item_path)
            entries.append(BaselineEntry.from_mapping(item, path=item_path))
        try:
            return cls(
                recorded_at=get_string(mapping, "recorded_at", path=path),
                protocol_sequence=get_integer(mapping, "protocol_sequence", path=path),
                accepted_sha256=get_optional_string(
                    mapping, "accepted_sha256", path=path
                ),
                entries=tuple(entries),
            )
        except DomainValidationError as error:
            raise error.with_prefix(path) from None


@dataclass(frozen=True, slots=True)
class StabilityObservation:
    """One durable size sample for outgoing-candidate stability."""

    path: str
    size_bytes: int
    observed_at_seconds: float

    def __post_init__(self) -> None:
        validate_windows_local_path(self.path, field_path="path")
        _require_non_negative_int(self.size_bytes, "size_bytes")
        if isinstance(self.observed_at_seconds, bool) or not isinstance(
            self.observed_at_seconds, int | float
        ):
            raise DomainValidationError(
                "expected a numeric timestamp in seconds",
                field_path="observed_at_seconds",
            )
        object.__setattr__(self, "observed_at_seconds", float(self.observed_at_seconds))

    def to_mapping(self) -> dict[str, object]:
        return {
            "observed_at_seconds": self.observed_at_seconds,
            "path": self.path,
            "size_bytes": self.size_bytes,
        }

    @classmethod
    def from_mapping(
        cls, mapping: Mapping[str, object], *, path: str = ""
    ) -> StabilityObservation:
        check_exact_keys(mapping, _STABILITY_OBSERVATION_KEYS, path=path)
        raw_time = mapping["observed_at_seconds"]
        if isinstance(raw_time, bool) or not isinstance(raw_time, int | float):
            raise DomainValidationError(
                "expected a numeric timestamp in seconds",
                field_path=(
                    f"{path}.observed_at_seconds" if path else "observed_at_seconds"
                ),
            )
        try:
            return cls(
                path=get_string(mapping, "path", path=path),
                size_bytes=get_integer(mapping, "size_bytes", path=path),
                observed_at_seconds=float(raw_time),
            )
        except DomainValidationError as error:
            raise error.with_prefix(path) from None


@dataclass(frozen=True, slots=True)
class VerifiedRemoteRecord:
    """Last verified remote manifest cursor for a match."""

    protocol_sequence: int
    accepted_sha256: str | None

    def __post_init__(self) -> None:
        _require_non_negative_int(self.protocol_sequence, "protocol_sequence")
        object.__setattr__(
            self,
            "accepted_sha256",
            _optional_sha256(self.accepted_sha256, field_path="accepted_sha256"),
        )
        if self.protocol_sequence == 0 and self.accepted_sha256 is not None:
            raise DomainValidationError(
                "sequence 0 must not carry an accepted hash",
                field_path="accepted_sha256",
            )
        if self.protocol_sequence > 0 and self.accepted_sha256 is None:
            raise DomainValidationError(
                "sequence > 0 requires an accepted hash",
                field_path="accepted_sha256",
            )

    def to_mapping(self) -> dict[str, object]:
        return {
            "accepted_sha256": self.accepted_sha256,
            "protocol_sequence": self.protocol_sequence,
        }

    @classmethod
    def from_mapping(
        cls, mapping: Mapping[str, object], *, path: str = ""
    ) -> VerifiedRemoteRecord:
        check_exact_keys(mapping, _VERIFIED_REMOTE_KEYS, path=path)
        try:
            return cls(
                protocol_sequence=get_integer(mapping, "protocol_sequence", path=path),
                accepted_sha256=get_optional_string(
                    mapping, "accepted_sha256", path=path
                ),
            )
        except DomainValidationError as error:
            raise error.with_prefix(path) from None


@dataclass(frozen=True, slots=True)
class DownloadedSaveRecord:
    """Local path of a verified downloaded accepted save."""

    local_path: str
    sha256: str
    size_bytes: int
    protocol_sequence: int

    def __post_init__(self) -> None:
        validate_windows_local_path(self.local_path, field_path="local_path")
        object.__setattr__(
            self, "sha256", validate_sha256_hex(self.sha256, field_path="sha256")
        )
        _require_non_negative_int(self.size_bytes, "size_bytes")
        _require_true_int(self.protocol_sequence, "protocol_sequence")
        if self.protocol_sequence < 1:
            raise DomainValidationError(
                "expected a positive protocol sequence",
                field_path="protocol_sequence",
            )

    def to_mapping(self) -> dict[str, object]:
        return {
            "local_path": self.local_path,
            "protocol_sequence": self.protocol_sequence,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }

    @classmethod
    def from_mapping(
        cls, mapping: Mapping[str, object], *, path: str = ""
    ) -> DownloadedSaveRecord:
        check_exact_keys(mapping, _DOWNLOADED_SAVE_KEYS, path=path)
        try:
            return cls(
                local_path=get_string(mapping, "local_path", path=path),
                sha256=get_string(mapping, "sha256", path=path),
                size_bytes=get_integer(mapping, "size_bytes", path=path),
                protocol_sequence=get_integer(mapping, "protocol_sequence", path=path),
            )
        except DomainValidationError as error:
            raise error.with_prefix(path) from None


@dataclass(frozen=True, slots=True)
class OutgoingCandidateRecord:
    """Stable outgoing candidate selected for upload resume."""

    path: str
    sha256: str
    size_bytes: int

    def __post_init__(self) -> None:
        validate_windows_local_path(self.path, field_path="path")
        object.__setattr__(
            self, "sha256", validate_sha256_hex(self.sha256, field_path="sha256")
        )
        _require_non_negative_int(self.size_bytes, "size_bytes")

    def to_mapping(self) -> dict[str, object]:
        return {
            "path": self.path,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
        }

    @classmethod
    def from_mapping(
        cls, mapping: Mapping[str, object], *, path: str = ""
    ) -> OutgoingCandidateRecord:
        check_exact_keys(mapping, _OUTGOING_CANDIDATE_KEYS, path=path)
        try:
            return cls(
                path=get_string(mapping, "path", path=path),
                sha256=get_string(mapping, "sha256", path=path),
                size_bytes=get_integer(mapping, "size_bytes", path=path),
            )
        except DomainValidationError as error:
            raise error.with_prefix(path) from None


@dataclass(frozen=True, slots=True)
class LaunchAttemptRecord:
    """Durable evidence that Relay attempted a launch for a sequence/hash."""

    protocol_sequence: int
    accepted_sha256: str | None
    attempted_at: str

    def __post_init__(self) -> None:
        _require_non_negative_int(self.protocol_sequence, "protocol_sequence")
        object.__setattr__(
            self,
            "accepted_sha256",
            _optional_sha256(self.accepted_sha256, field_path="accepted_sha256"),
        )
        object.__setattr__(
            self,
            "attempted_at",
            validate_utc_timestamp(self.attempted_at, field_path="attempted_at"),
        )
        if self.protocol_sequence == 0 and self.accepted_sha256 is not None:
            raise DomainValidationError(
                "sequence 0 must not carry an accepted hash",
                field_path="accepted_sha256",
            )
        if self.protocol_sequence > 0 and self.accepted_sha256 is None:
            raise DomainValidationError(
                "sequence > 0 requires an accepted hash",
                field_path="accepted_sha256",
            )

    def to_mapping(self) -> dict[str, object]:
        return {
            "accepted_sha256": self.accepted_sha256,
            "attempted_at": self.attempted_at,
            "protocol_sequence": self.protocol_sequence,
        }

    @classmethod
    def from_mapping(
        cls, mapping: Mapping[str, object], *, path: str = ""
    ) -> LaunchAttemptRecord:
        check_exact_keys(mapping, _LAUNCH_ATTEMPT_KEYS, path=path)
        try:
            return cls(
                protocol_sequence=get_integer(mapping, "protocol_sequence", path=path),
                accepted_sha256=get_optional_string(
                    mapping, "accepted_sha256", path=path
                ),
                attempted_at=get_string(mapping, "attempted_at", path=path),
            )
        except DomainValidationError as error:
            raise error.with_prefix(path) from None


@dataclass(frozen=True, slots=True)
class ProcessAssociationRecord:
    """Relay-owned Civ process association evidence (design §8.1 / §9.1)."""

    protocol_sequence: int
    accepted_sha256: str | None
    pid: int
    process_start_time_utc: str
    executable_path: str
    associated_at: str

    def __post_init__(self) -> None:
        _require_non_negative_int(self.protocol_sequence, "protocol_sequence")
        object.__setattr__(
            self,
            "accepted_sha256",
            _optional_sha256(self.accepted_sha256, field_path="accepted_sha256"),
        )
        _require_true_int(self.pid, "pid")
        if self.pid <= 0:
            raise DomainValidationError(
                "expected a positive process id", field_path="pid"
            )
        object.__setattr__(
            self,
            "process_start_time_utc",
            validate_utc_timestamp(
                self.process_start_time_utc, field_path="process_start_time_utc"
            ),
        )
        validate_windows_local_path(self.executable_path, field_path="executable_path")
        object.__setattr__(
            self,
            "associated_at",
            validate_utc_timestamp(self.associated_at, field_path="associated_at"),
        )
        if self.protocol_sequence == 0 and self.accepted_sha256 is not None:
            raise DomainValidationError(
                "sequence 0 must not carry an accepted hash",
                field_path="accepted_sha256",
            )
        if self.protocol_sequence > 0 and self.accepted_sha256 is None:
            raise DomainValidationError(
                "sequence > 0 requires an accepted hash",
                field_path="accepted_sha256",
            )

    def to_mapping(self) -> dict[str, object]:
        return {
            "accepted_sha256": self.accepted_sha256,
            "associated_at": self.associated_at,
            "executable_path": self.executable_path,
            "pid": self.pid,
            "process_start_time_utc": self.process_start_time_utc,
            "protocol_sequence": self.protocol_sequence,
        }

    @classmethod
    def from_mapping(
        cls, mapping: Mapping[str, object], *, path: str = ""
    ) -> ProcessAssociationRecord:
        check_exact_keys(mapping, _PROCESS_ASSOCIATION_KEYS, path=path)
        try:
            return cls(
                protocol_sequence=get_integer(mapping, "protocol_sequence", path=path),
                accepted_sha256=get_optional_string(
                    mapping, "accepted_sha256", path=path
                ),
                pid=get_integer(mapping, "pid", path=path),
                process_start_time_utc=get_string(
                    mapping, "process_start_time_utc", path=path
                ),
                executable_path=get_string(mapping, "executable_path", path=path),
                associated_at=get_string(mapping, "associated_at", path=path),
            )
        except DomainValidationError as error:
            raise error.with_prefix(path) from None


def _canonicalize_digest_tuple(value: object, *, field_path: str) -> tuple[str, ...]:
    hashes = canonicalize_tuple(
        value,
        str,
        field_path=field_path,
        item_label="SHA-256 digest string",
    )
    normalized: list[str] = []
    seen: set[str] = set()
    for index, digest in enumerate(hashes):
        validated = validate_sha256_hex(digest, field_path=f"{field_path}[{index}]")
        if validated in seen:
            raise DomainValidationError(
                "duplicate hash",
                field_path=f"{field_path}[{index}]",
            )
        seen.add(validated)
        normalized.append(validated)
    return tuple(normalized)


def _string_tuple_from_array(
    values: tuple[object, ...], *, field_path: str
) -> tuple[str, ...]:
    items: list[str] = []
    for index, item in enumerate(values):
        if not isinstance(item, str):
            raise DomainValidationError(
                "expected a string",
                field_path=f"{field_path}[{index}]",
            )
        items.append(item)
    return _canonicalize_digest_tuple(tuple(items), field_path=field_path)


@dataclass(frozen=True, slots=True)
class MatchLocalRecords:
    """Per-match durable local records, including embedded handoff journal state."""

    game_id: str
    schema_version: int = LOCAL_RECORDS_SCHEMA_VERSION
    verified_remote: VerifiedRemoteRecord | None = None
    downloaded_save: DownloadedSaveRecord | None = None
    play_session_baseline: PlaySessionBaseline | None = None
    outgoing_candidate: OutgoingCandidateRecord | None = None
    processed_outgoing_hashes: tuple[str, ...] = ()
    stability_observations: tuple[StabilityObservation, ...] = ()
    in_progress_handoff: InProgressHandoff | None = None
    attempted_handoff_hashes: tuple[str, ...] = ()
    historically_accepted_hashes: tuple[str, ...] = ()
    last_operational_state: OperationalState | None = None
    last_transition_reason: str | None = None
    last_error_class: str | None = None
    retry_count: int = 0
    launch_attempt: LaunchAttemptRecord | None = None
    process_association: ProcessAssociationRecord | None = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "game_id", validate_game_id(self.game_id, field_path="game_id")
        )
        _require_true_int(self.schema_version, "schema_version")
        if self.schema_version != LOCAL_RECORDS_SCHEMA_VERSION:
            raise DomainValidationError(
                f"unsupported local records schema version "
                f"(expected {LOCAL_RECORDS_SCHEMA_VERSION})",
                field_path="schema_version",
            )
        object.__setattr__(
            self,
            "verified_remote",
            require_optional_instance(
                self.verified_remote,
                VerifiedRemoteRecord,
                field_path="verified_remote",
            ),
        )
        object.__setattr__(
            self,
            "downloaded_save",
            require_optional_instance(
                self.downloaded_save,
                DownloadedSaveRecord,
                field_path="downloaded_save",
            ),
        )
        object.__setattr__(
            self,
            "play_session_baseline",
            require_optional_instance(
                self.play_session_baseline,
                PlaySessionBaseline,
                field_path="play_session_baseline",
            ),
        )
        object.__setattr__(
            self,
            "outgoing_candidate",
            require_optional_instance(
                self.outgoing_candidate,
                OutgoingCandidateRecord,
                field_path="outgoing_candidate",
            ),
        )
        object.__setattr__(
            self,
            "processed_outgoing_hashes",
            _canonicalize_digest_tuple(
                self.processed_outgoing_hashes,
                field_path="processed_outgoing_hashes",
            ),
        )
        object.__setattr__(
            self,
            "stability_observations",
            canonicalize_tuple(
                self.stability_observations,
                StabilityObservation,
                field_path="stability_observations",
                item_label="StabilityObservation instance",
            ),
        )
        object.__setattr__(
            self,
            "in_progress_handoff",
            require_optional_instance(
                self.in_progress_handoff,
                InProgressHandoff,
                field_path="in_progress_handoff",
            ),
        )
        if (
            self.in_progress_handoff is not None
            and self.in_progress_handoff.game_id != self.game_id
        ):
            raise DomainValidationError(
                "in-progress game_id must match local records game_id",
                field_path="in_progress_handoff.game_id",
            )
        object.__setattr__(
            self,
            "attempted_handoff_hashes",
            _canonicalize_digest_tuple(
                self.attempted_handoff_hashes,
                field_path="attempted_handoff_hashes",
            ),
        )
        object.__setattr__(
            self,
            "historically_accepted_hashes",
            _canonicalize_digest_tuple(
                self.historically_accepted_hashes,
                field_path="historically_accepted_hashes",
            ),
        )
        if self.last_operational_state is not None:
            object.__setattr__(
                self,
                "last_operational_state",
                _parse_operational_state(
                    self.last_operational_state, field_path="last_operational_state"
                ),
            )
        reason = require_optional_string(
            self.last_transition_reason, field_path="last_transition_reason"
        )
        object.__setattr__(self, "last_transition_reason", reason)
        if reason is not None and not reason:
            raise DomainValidationError(
                "must be omitted instead of empty",
                field_path="last_transition_reason",
            )
        error_class = require_optional_string(
            self.last_error_class, field_path="last_error_class"
        )
        object.__setattr__(self, "last_error_class", error_class)
        if error_class is not None and not error_class:
            raise DomainValidationError(
                "must be omitted instead of empty", field_path="last_error_class"
            )
        _require_non_negative_int(self.retry_count, "retry_count")
        object.__setattr__(
            self,
            "launch_attempt",
            require_optional_instance(
                self.launch_attempt, LaunchAttemptRecord, field_path="launch_attempt"
            ),
        )
        object.__setattr__(
            self,
            "process_association",
            require_optional_instance(
                self.process_association,
                ProcessAssociationRecord,
                field_path="process_association",
            ),
        )

    def to_mapping(self) -> dict[str, object]:
        return {
            "attempted_handoff_hashes": list(self.attempted_handoff_hashes),
            "downloaded_save": (
                None
                if self.downloaded_save is None
                else self.downloaded_save.to_mapping()
            ),
            "game_id": self.game_id,
            "historically_accepted_hashes": list(self.historically_accepted_hashes),
            "in_progress_handoff": (
                None
                if self.in_progress_handoff is None
                else self.in_progress_handoff.to_mapping()
            ),
            "last_error_class": self.last_error_class,
            "last_operational_state": (
                None
                if self.last_operational_state is None
                else self.last_operational_state.value
            ),
            "last_transition_reason": self.last_transition_reason,
            "launch_attempt": (
                None
                if self.launch_attempt is None
                else self.launch_attempt.to_mapping()
            ),
            "outgoing_candidate": (
                None
                if self.outgoing_candidate is None
                else self.outgoing_candidate.to_mapping()
            ),
            "play_session_baseline": (
                None
                if self.play_session_baseline is None
                else self.play_session_baseline.to_mapping()
            ),
            "process_association": (
                None
                if self.process_association is None
                else self.process_association.to_mapping()
            ),
            "processed_outgoing_hashes": list(self.processed_outgoing_hashes),
            "retry_count": self.retry_count,
            "schema_version": self.schema_version,
            "stability_observations": [
                item.to_mapping() for item in self.stability_observations
            ],
            "verified_remote": (
                None
                if self.verified_remote is None
                else self.verified_remote.to_mapping()
            ),
        }

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, object]) -> MatchLocalRecords:
        check_exact_keys(
            mapping,
            _MATCH_LOCAL_REQUIRED_KEYS,
            optional=_MATCH_LOCAL_OPTIONAL_KEYS,
        )
        verified_raw = (
            get_optional_object(mapping, "verified_remote")
            if "verified_remote" in mapping
            else None
        )
        downloaded_raw = (
            get_optional_object(mapping, "downloaded_save")
            if "downloaded_save" in mapping
            else None
        )
        baseline_raw = (
            get_optional_object(mapping, "play_session_baseline")
            if "play_session_baseline" in mapping
            else None
        )
        outgoing_raw = (
            get_optional_object(mapping, "outgoing_candidate")
            if "outgoing_candidate" in mapping
            else None
        )
        launch_raw = (
            get_optional_object(mapping, "launch_attempt")
            if "launch_attempt" in mapping
            else None
        )
        process_raw = (
            get_optional_object(mapping, "process_association")
            if "process_association" in mapping
            else None
        )
        in_progress_raw = (
            get_optional_object(mapping, "in_progress_handoff")
            if "in_progress_handoff" in mapping
            else None
        )
        processed = (
            _string_tuple_from_array(
                get_array(mapping, "processed_outgoing_hashes"),
                field_path="processed_outgoing_hashes",
            )
            if "processed_outgoing_hashes" in mapping
            else ()
        )
        if "stability_observations" in mapping:
            raw_obs = get_array(mapping, "stability_observations")
            observations: list[StabilityObservation] = []
            for index, item in enumerate(raw_obs):
                item_path = f"stability_observations[{index}]"
                if not isinstance(item, Mapping):
                    raise DomainValidationError(
                        "expected an object", field_path=item_path
                    )
                observations.append(
                    StabilityObservation.from_mapping(item, path=item_path)
                )
            stability = tuple(observations)
        else:
            stability = ()
        attempted = (
            _string_tuple_from_array(
                get_array(mapping, "attempted_handoff_hashes"),
                field_path="attempted_handoff_hashes",
            )
            if "attempted_handoff_hashes" in mapping
            else ()
        )
        historical = (
            _string_tuple_from_array(
                get_array(mapping, "historically_accepted_hashes"),
                field_path="historically_accepted_hashes",
            )
            if "historically_accepted_hashes" in mapping
            else ()
        )
        if "last_operational_state" in mapping:
            state_value = mapping["last_operational_state"]
            last_state = (
                None
                if state_value is None
                else _parse_operational_state(
                    state_value, field_path="last_operational_state"
                )
            )
        else:
            last_state = None
        return cls(
            game_id=get_string(mapping, "game_id"),
            schema_version=get_integer(mapping, "schema_version"),
            verified_remote=(
                None
                if verified_raw is None
                else VerifiedRemoteRecord.from_mapping(
                    verified_raw, path="verified_remote"
                )
            ),
            downloaded_save=(
                None
                if downloaded_raw is None
                else DownloadedSaveRecord.from_mapping(
                    downloaded_raw, path="downloaded_save"
                )
            ),
            play_session_baseline=(
                None
                if baseline_raw is None
                else PlaySessionBaseline.from_mapping(
                    baseline_raw, path="play_session_baseline"
                )
            ),
            outgoing_candidate=(
                None
                if outgoing_raw is None
                else OutgoingCandidateRecord.from_mapping(
                    outgoing_raw, path="outgoing_candidate"
                )
            ),
            processed_outgoing_hashes=processed,
            stability_observations=stability,
            in_progress_handoff=(
                None
                if in_progress_raw is None
                else InProgressHandoff.from_mapping(
                    in_progress_raw, path="in_progress_handoff"
                )
            ),
            attempted_handoff_hashes=attempted,
            historically_accepted_hashes=historical,
            last_operational_state=last_state,
            last_transition_reason=(
                get_optional_string(mapping, "last_transition_reason")
                if "last_transition_reason" in mapping
                else None
            ),
            last_error_class=(
                get_optional_string(mapping, "last_error_class")
                if "last_error_class" in mapping
                else None
            ),
            retry_count=(
                get_integer(mapping, "retry_count") if "retry_count" in mapping else 0
            ),
            launch_attempt=(
                None
                if launch_raw is None
                else LaunchAttemptRecord.from_mapping(launch_raw, path="launch_attempt")
            ),
            process_association=(
                None
                if process_raw is None
                else ProcessAssociationRecord.from_mapping(
                    process_raw, path="process_association"
                )
            ),
        )

    def to_json_bytes(self) -> bytes:
        return to_canonical_json_bytes(self.to_mapping())

    @classmethod
    def from_json_bytes(cls, data: bytes) -> MatchLocalRecords:
        return cls.from_mapping(parse_json_object_bytes(data))
