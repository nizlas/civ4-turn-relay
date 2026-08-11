"""StorageProvider binding ParamikoStorage to a disposable OpenSSH root."""

from __future__ import annotations

import uuid
from dataclasses import replace

from civ4_turn_relay.domain import GlobalConfig
from civ4_turn_relay.storage import ParamikoStorage, Storage
from tests.storage.contract.provider import StorageProvider
from tests.storage.integration.openssh_harness import OpenSSHFixture


class ParamikoOpenSSHProvider:
    """Create a fresh contained subdirectory under the disposable SFTP root."""

    def __init__(self, fixture: OpenSSHFixture, base_config: GlobalConfig) -> None:
        self._fixture = fixture
        self._base_config = base_config

    @property
    def name(self) -> str:
        return "paramiko-openssh"

    def create(self) -> Storage:
        bootstrap = ParamikoStorage(self._base_config)
        bootstrap.connect()
        dirname = f"contract-{uuid.uuid4().hex}"
        bootstrap.mkdir(dirname)
        bootstrap.close()
        child_root = f"{self._fixture.remote_root.rstrip('/')}/{dirname}"
        config = replace(self._base_config, sftp_remote_root=child_root)
        storage = ParamikoStorage(config)
        storage.connect()
        return storage


def assert_provider(provider: StorageProvider) -> StorageProvider:
    return provider
