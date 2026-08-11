"""Unit tests for Paramiko path containment (no network)."""

from __future__ import annotations

import pytest

from civ4_turn_relay.domain import GlobalConfig
from civ4_turn_relay.storage import ParamikoStorage, StorageInvalidPathError

FAKE_HOST_KEY = "SHA256:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"


def _config() -> GlobalConfig:
    return GlobalConfig(
        sftp_host="sftp.example.invalid",
        sftp_port=22,
        sftp_username="placeholder-user",
        sftp_remote_root="/placeholder/games",
        sftp_password="placeholder-password",
        sftp_host_key_sha256=FAKE_HOST_KEY,
    )


@pytest.mark.parametrize(
    "bad_path",
    ["", "/absolute", "a/../b", "..", "./a", "a//b", "a\\b", "a/"],
)
def test_path_rejected_before_connect(bad_path: str) -> None:
    storage = ParamikoStorage(_config())
    with pytest.raises(StorageInvalidPathError):
        storage._resolve(bad_path)  # noqa: SLF001 - containment unit check


def test_repr_config_does_not_leak_password() -> None:
    config = _config()
    assert "placeholder-password" not in repr(config)
    assert "placeholder-password" not in repr(ParamikoStorage(config))
