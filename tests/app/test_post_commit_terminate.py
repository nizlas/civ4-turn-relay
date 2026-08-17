"""ProcessCoordinator direct post-commit termination safety (P7).

The Fully Managed close is a direct termination of the exact entitled
process — verified handoff first, then close. These tests pin the
authorization boundary: only an exact identity match against the durable
entitlement plus a fresh ``RUNNING_MATCH`` probe may terminate, at most once
per handoff operation per session.
"""

from __future__ import annotations

from collections.abc import Mapping

from civ4_turn_relay.app.process_runtime import (
    ProcessCoordinator,
    ProcessStatus,
    close_payload_matches_record,
    identity_from_close_record,
)
from civ4_turn_relay.domain import sha256_hex
from civ4_turn_relay.local import FakeClock, PostCommitCloseRecord
from civ4_turn_relay.process import (
    FakeProcessSupervisor,
    ProcessIdentity,
    TerminateOutcome,
)

NOW_UTC = "2026-08-15T12:00:00Z"
OP_ID = "abababab-abab-4bab-8bab-abababababab"
EXE = r"C:\Games\Civ4\Civ4BeyondSword.exe"
DIGEST = sha256_hex(b"synthetic-outgoing-save")
CREATE_NS = 1_760_184_000_000_001_111


def _coordinator(supervisor: FakeProcessSupervisor) -> ProcessCoordinator:
    return ProcessCoordinator(
        supervisor=supervisor,
        clock=FakeClock(),
        now_utc_fn=lambda: NOW_UTC,
        civ4_executable=EXE,
    )


def _entitlement(
    *,
    pid: int = 4242,
    create_ns: int = CREATE_NS,
    executable: str = EXE,
    close_requested: bool = False,
) -> PostCommitCloseRecord:
    return PostCommitCloseRecord(
        game_id="example-match",
        source_protocol_sequence=1,
        operation_id=OP_ID,
        sha256=DIGEST,
        pid=pid,
        process_start_time_utc=NOW_UTC,
        process_create_time_ns=create_ns,
        executable_path=executable,
        close_requested=close_requested,
    )


def _running_entitled(
    supervisor: FakeProcessSupervisor,
) -> tuple[PostCommitCloseRecord, ProcessIdentity]:
    pending = _entitlement()
    identity = identity_from_close_record(pending)
    supervisor.spawn_external(identity)
    return pending, identity


def test_terminates_exact_entitled_process_once() -> None:
    supervisor = FakeProcessSupervisor()
    coordinator = _coordinator(supervisor)
    pending, identity = _running_entitled(supervisor)

    result = coordinator.terminate_after_commit(identity, pending)

    assert result is not None
    assert result.outcome is TerminateOutcome.TERMINATED
    assert supervisor.terminations == [identity]
    # No graceful close request was ever attempted.
    assert supervisor.close_requests == []
    # Closed only because a fresh probe proved the process is gone.
    assert coordinator.safely_closed

    # The same operation never terminates twice in one session.
    assert coordinator.terminate_after_commit(identity, pending) is None
    assert supervisor.terminations == [identity]


def test_already_exited_process_completes_without_terminate_call() -> None:
    supervisor = FakeProcessSupervisor()
    coordinator = _coordinator(supervisor)
    pending = _entitlement()
    identity = identity_from_close_record(pending)

    result = coordinator.terminate_after_commit(identity, pending)

    assert result is not None
    assert result.outcome is TerminateOutcome.NOT_RUNNING
    assert supervisor.terminations == []
    assert coordinator.safely_closed


def test_identity_not_matching_entitlement_is_refused_locally() -> None:
    supervisor = FakeProcessSupervisor()
    coordinator = _coordinator(supervisor)
    pending, _identity = _running_entitled(supervisor)

    wrong_exe = ProcessIdentity(
        pid=pending.pid,
        process_start_time_utc=pending.process_start_time_utc,
        process_create_time_ns=pending.process_create_time_ns,
        executable_path=r"C:\Games\Other\NotCiv.exe",
    )
    wrong_token = ProcessIdentity(
        pid=pending.pid,
        process_start_time_utc=pending.process_start_time_utc,
        process_create_time_ns=pending.process_create_time_ns + 1,
        executable_path=pending.executable_path,
    )
    wrong_pid = ProcessIdentity(
        pid=pending.pid + 1,
        process_start_time_utc=pending.process_start_time_utc,
        process_create_time_ns=pending.process_create_time_ns,
        executable_path=pending.executable_path,
    )
    for candidate in (wrong_exe, wrong_token, wrong_pid):
        assert coordinator.terminate_after_commit(candidate, pending) is None
    assert supervisor.terminations == []


def test_pid_reused_by_foreign_process_never_terminates() -> None:
    """A same-name, same-pid foreign process must never be touched."""
    supervisor = FakeProcessSupervisor()
    coordinator = _coordinator(supervisor)
    pending = _entitlement()
    identity = identity_from_close_record(pending)
    impostor = ProcessIdentity(
        pid=pending.pid,
        process_start_time_utc=pending.process_start_time_utc,
        process_create_time_ns=pending.process_create_time_ns + 7,
        executable_path=pending.executable_path,
    )
    supervisor.spawn_external(impostor)

    result = coordinator.terminate_after_commit(identity, pending)

    assert result is not None
    assert result.outcome is TerminateOutcome.IDENTITY_MISMATCH
    assert supervisor.terminations == []


def test_failed_termination_reports_truthful_close_failed_status() -> None:
    supervisor = FakeProcessSupervisor()
    supervisor.terminate_fails = True
    coordinator = _coordinator(supervisor)
    pending, identity = _running_entitled(supervisor)

    result = coordinator.terminate_after_commit(identity, pending)

    assert result is not None
    assert result.outcome is TerminateOutcome.TERMINATE_FAILED
    assert not coordinator.safely_closed
    status = coordinator.status_snapshot(association=identity)
    assert status.status is ProcessStatus.CLOSE_FAILED
    assert "could not be closed" in status.message

    # Automatic retries stay blocked; an explicit user request may retry.
    assert coordinator.terminate_after_commit(identity, pending) is None
    supervisor.terminate_fails = False
    retried = coordinator.terminate_after_commit(identity, pending, user_requested=True)
    assert retried is not None
    assert retried.outcome is TerminateOutcome.TERMINATED
    assert coordinator.safely_closed
    assert supervisor.terminations == [identity, identity]


def test_close_payload_must_name_the_full_entitlement() -> None:
    """A close-intent payload missing any evidence field never matches."""
    pending = _entitlement()
    full: Mapping[str, object] = {
        "pid": pending.pid,
        "process_start_time_utc": pending.process_start_time_utc,
        "process_create_time_ns": pending.process_create_time_ns,
        "executable_path": pending.executable_path,
        "operation_id": pending.operation_id,
        "sha256": pending.sha256,
        "source_protocol_sequence": pending.source_protocol_sequence,
    }
    assert close_payload_matches_record(full, pending)
    for key in full:
        partial = {name: value for name, value in full.items() if name != key}
        assert not close_payload_matches_record(partial, pending)
