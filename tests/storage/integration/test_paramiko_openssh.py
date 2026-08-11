"""Paramiko Storage against disposable OpenSSH (skipped without Docker)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from civ4_turn_relay.domain import GlobalConfig
from civ4_turn_relay.storage import ParamikoStorage, StorageTransportError
from tests.storage.contract import cases
from tests.storage.integration.openssh_harness import (
    OpenSSHFixture,
    OpenSSHSftpServer,
    docker_available,
)
from tests.storage.integration.paramiko_provider import ParamikoOpenSSHProvider

pytestmark = pytest.mark.openssh_sftp


def _skip_without_docker() -> None:
    if not docker_available():
        pytest.skip(
            "Docker/OpenSSH disposable server unavailable; "
            "run when Docker is available: pytest -m openssh_sftp"
        )


@pytest.fixture(scope="module")
def openssh_fixture() -> Iterator[OpenSSHFixture]:
    _skip_without_docker()
    with OpenSSHSftpServer() as fixture:
        yield fixture


@pytest.fixture(scope="module")
def base_config(openssh_fixture: OpenSSHFixture) -> GlobalConfig:
    known_hosts = str(Path(openssh_fixture.known_hosts_path).resolve())
    return GlobalConfig(
        sftp_host=openssh_fixture.host,
        sftp_port=openssh_fixture.port,
        sftp_username=openssh_fixture.username,
        sftp_remote_root=openssh_fixture.remote_root,
        sftp_password=openssh_fixture.password,
        sftp_host_key_sha256=openssh_fixture.host_key_sha256,
        sftp_known_hosts_path=known_hosts,
        sftp_connect_timeout_seconds=10,
    )


@pytest.fixture
def paramiko_storage(
    openssh_fixture: OpenSSHFixture, base_config: GlobalConfig
) -> Iterator[ParamikoStorage]:
    provider = ParamikoOpenSSHProvider(openssh_fixture, base_config)
    storage = provider.create()
    assert isinstance(storage, ParamikoStorage)
    try:
        yield storage
    finally:
        storage.close()


def test_paramiko_contract_core(paramiko_storage: ParamikoStorage) -> None:
    cases.case_provider_reports_required_capabilities(paramiko_storage)
    cases.case_write_read_round_trip(paramiko_storage)
    cases.case_write_without_overwrite_refuses_existing_file(paramiko_storage)
    cases.case_first_exclusive_mkdir_succeeds(paramiko_storage)
    cases.case_second_mkdir_same_path_already_exists(paramiko_storage)
    cases.case_missing_destination_publishes_atomically(paramiko_storage)
    cases.case_existing_same_content_destination_not_overwritten(paramiko_storage)
    cases.case_existing_different_content_destination_not_overwritten(paramiko_storage)
    cases.case_replace_absent_destination(paramiko_storage)
    cases.case_replace_existing_file(paramiko_storage)
    cases.case_exact_byte_count_and_sha256(paramiko_storage, b"openssh-probe")
    cases.case_deterministic_immediate_child_listing(paramiko_storage)
    cases.case_remove_file_and_empty_directory(paramiko_storage)
    cases.case_path_traversal_rejected_before_state_changes(paramiko_storage, "a/../b")


def test_host_key_mismatch_refused(base_config: GlobalConfig) -> None:
    from dataclasses import replace

    bad = replace(
        base_config,
        sftp_host_key_sha256="SHA256:BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB",
        sftp_known_hosts_path=None,
    )
    with pytest.raises(StorageTransportError):
        ParamikoStorage(bad).connect()


def test_unknown_host_key_refused(openssh_fixture: OpenSSHFixture) -> None:
    empty = Path(openssh_fixture.known_hosts_path).with_name("empty-known-hosts")
    empty.write_text("", encoding="utf-8")
    config = GlobalConfig(
        sftp_host=openssh_fixture.host,
        sftp_port=openssh_fixture.port,
        sftp_username=openssh_fixture.username,
        sftp_remote_root=openssh_fixture.remote_root,
        sftp_password=openssh_fixture.password,
        sftp_known_hosts_path=str(empty.resolve()),
        sftp_connect_timeout_seconds=10,
    )
    with pytest.raises(StorageTransportError):
        ParamikoStorage(config).connect()
    empty.unlink(missing_ok=True)


def test_close_idempotent(base_config: GlobalConfig) -> None:
    storage = ParamikoStorage(base_config)
    storage.connect()
    storage.close()
    storage.close()
