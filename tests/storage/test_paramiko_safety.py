"""Unit-level Paramiko adapter safety without requiring Docker/OpenSSH."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from civ4_turn_relay.domain import GlobalConfig
from civ4_turn_relay.storage import (
    ParamikoStorage,
    StorageAlreadyExistsError,
    StorageCapabilityError,
    StorageInvalidPathError,
    StorageTransportError,
)
from civ4_turn_relay.storage.paramiko_sftp import (
    _fingerprint_host_key_policy,
    _sha256_fingerprint,
)

FAKE_HOST_KEY = "SHA256:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"


def _config(**overrides: object) -> GlobalConfig:
    base: dict[str, object] = {
        "sftp_host": "sftp.example.invalid",
        "sftp_port": 22,
        "sftp_username": "placeholder-user",
        "sftp_remote_root": "/placeholder/games",
        "sftp_password": "placeholder-password",
        "sftp_host_key_sha256": FAKE_HOST_KEY,
    }
    base.update(overrides)
    return GlobalConfig(**base)  # type: ignore[arg-type]


def test_no_auto_add_policy_in_source() -> None:
    import inspect

    import civ4_turn_relay.storage.paramiko_sftp as module

    source = inspect.getsource(module)
    assert "AutoAddPolicy" not in source


def test_fingerprint_policy_rejects_mismatch() -> None:
    import paramiko

    key = paramiko.RSAKey.generate(1024)
    policy = _fingerprint_host_key_policy(expected=FAKE_HOST_KEY, host="example")
    client = MagicMock()
    with pytest.raises(paramiko.BadHostKeyException):
        policy.missing_host_key(client, "example", key)


def test_fingerprint_policy_accepts_exact_bytes() -> None:
    import paramiko

    key = paramiko.RSAKey.generate(1024)
    expected = _sha256_fingerprint(key)
    policy = _fingerprint_host_key_policy(expected=expected, host="example")
    client = MagicMock()
    client.get_host_keys.return_value = MagicMock()
    policy.missing_host_key(client, "example", key)
    client.get_host_keys.return_value.add.assert_called_once()


def test_atomic_replace_requires_posix_rename() -> None:
    storage = ParamikoStorage(_config())
    storage._capabilities_verified = True  # noqa: SLF001
    from civ4_turn_relay.storage.port import StorageCapabilities

    storage._capabilities = StorageCapabilities(  # noqa: SLF001
        exclusive_mkdir=True,
        atomic_replace=True,
        atomic_publish_no_replace=True,
        complete_readback=True,
    )
    sftp = MagicMock()
    del sftp.posix_rename
    storage._sftp = sftp  # noqa: SLF001
    storage._closed = False  # noqa: SLF001
    from civ4_turn_relay.storage.port import StorageEntryKind

    def _kind(remote: str) -> StorageEntryKind | None:
        if remote.endswith("src.bin"):
            return StorageEntryKind.FILE
        return None

    storage._stat_kind = _kind  # type: ignore[method-assign]  # noqa: SLF001
    with pytest.raises(StorageCapabilityError):
        storage.atomic_replace("src.bin", "dest.bin")


def test_publish_no_replace_refuses_existing_without_delete() -> None:
    storage = ParamikoStorage(_config())
    storage._capabilities_verified = True  # noqa: SLF001
    from civ4_turn_relay.storage.port import StorageCapabilities, StorageEntryKind

    storage._capabilities = StorageCapabilities(  # noqa: SLF001
        exclusive_mkdir=True,
        atomic_replace=True,
        atomic_publish_no_replace=True,
        complete_readback=True,
    )
    sftp = MagicMock()
    storage._sftp = sftp  # noqa: SLF001
    storage._closed = False  # noqa: SLF001

    def kind(remote: str) -> StorageEntryKind | None:
        if remote.endswith("src.bin") or remote.endswith("dest.bin"):
            return StorageEntryKind.FILE
        return None

    storage._stat_kind = kind  # type: ignore[method-assign]  # noqa: SLF001
    with pytest.raises(StorageAlreadyExistsError):
        storage.publish_no_replace("src.bin", "dest.bin")
    sftp.rename.assert_not_called()
    sftp.remove.assert_not_called()


def test_known_hosts_and_fingerprint_modes_exclusive() -> None:
    from civ4_turn_relay.domain import DomainValidationError

    with pytest.raises(DomainValidationError, match="mutually exclusive"):
        _config(
            sftp_known_hosts_path=r"C:\hosts",
            sftp_host_key_sha256=FAKE_HOST_KEY,
        )


def test_path_containment_rejects_windows_separators() -> None:
    storage = ParamikoStorage(_config())
    with pytest.raises(StorageInvalidPathError):
        storage._resolve(r"a\b")  # noqa: SLF001


def test_mkdir_generic_failure_rechecks_existing_directory() -> None:
    """OpenSSH's generic Failure must still classify an existing directory."""
    from civ4_turn_relay.storage.port import StorageCapabilities, StorageEntryKind

    storage = ParamikoStorage(_config())
    storage._capabilities_verified = True  # noqa: SLF001
    storage._capabilities = StorageCapabilities(  # noqa: SLF001
        exclusive_mkdir=True,
        atomic_replace=True,
        atomic_publish_no_replace=True,
        complete_readback=True,
    )
    sftp = MagicMock()
    sftp.mkdir.side_effect = OSError("Failure")
    storage._sftp = sftp  # noqa: SLF001
    storage._closed = False  # noqa: SLF001
    def existing_directory(_remote: str) -> StorageEntryKind | None:
        return StorageEntryKind.DIRECTORY

    storage._stat_kind = existing_directory  # type: ignore[assignment]  # noqa: SLF001

    with pytest.raises(StorageAlreadyExistsError):
        storage.mkdir("existing-game")


def test_mkdir_generic_failure_rechecks_wrong_kind_file() -> None:
    from civ4_turn_relay.storage import StorageWrongKindError
    from civ4_turn_relay.storage.port import StorageCapabilities, StorageEntryKind

    storage = ParamikoStorage(_config())
    storage._capabilities_verified = True  # noqa: SLF001
    storage._capabilities = StorageCapabilities(  # noqa: SLF001
        exclusive_mkdir=True,
        atomic_replace=True,
        atomic_publish_no_replace=True,
        complete_readback=True,
    )
    sftp = MagicMock()
    sftp.mkdir.side_effect = OSError("Failure")
    storage._sftp = sftp  # noqa: SLF001
    storage._closed = False  # noqa: SLF001
    def existing_file(_remote: str) -> StorageEntryKind | None:
        return StorageEntryKind.FILE

    storage._stat_kind = existing_file  # type: ignore[assignment]  # noqa: SLF001

    with pytest.raises(StorageWrongKindError):
        storage.mkdir("wrong-kind")


@pytest.mark.parametrize(
    "operation",
    ("mkdir", "read_file", "publish_no_replace", "atomic_replace"),
)
def test_first_capability_gated_operation_connects_before_check(
    operation: str,
) -> None:
    """A lazy adapter must probe capabilities before reading their flags."""
    from civ4_turn_relay.storage.port import StorageCapabilities, StorageEntryKind

    storage = ParamikoStorage(_config())
    sftp = MagicMock()
    handle = sftp.file.return_value.__enter__.return_value
    handle.read.return_value = b"turn"

    def require_sftp() -> MagicMock:
        storage._capabilities = StorageCapabilities(  # noqa: SLF001
            exclusive_mkdir=True,
            atomic_replace=True,
            atomic_publish_no_replace=True,
            complete_readback=True,
        )
        storage._capabilities_verified = True  # noqa: SLF001
        return sftp

    storage._require_sftp = require_sftp  # type: ignore[method-assign]  # noqa: SLF001
    storage._stat_kind = lambda remote: (  # type: ignore[method-assign]  # noqa: SLF001
        StorageEntryKind.FILE if remote.endswith("source.bin") else None
    )

    if operation == "mkdir":
        storage.mkdir("game")
        sftp.mkdir.assert_called_once()
    elif operation == "read_file":

        def always_file(_remote: str) -> StorageEntryKind | None:
            return StorageEntryKind.FILE

        storage._stat_kind = always_file  # type: ignore[assignment]  # noqa: SLF001
        assert storage.read_file("source.bin") == b"turn"
    elif operation == "publish_no_replace":
        storage.publish_no_replace("source.bin", "destination.bin")
        sftp.rename.assert_called_once()
    else:
        storage.atomic_replace("source.bin", "destination.bin")
        sftp.posix_rename.assert_called_once()


def test_mutating_failure_is_transport_not_silent_replay() -> None:
    storage = ParamikoStorage(_config())
    storage._capabilities_verified = True  # noqa: SLF001
    from civ4_turn_relay.storage.port import StorageCapabilities, StorageEntryKind

    storage._capabilities = StorageCapabilities(  # noqa: SLF001
        exclusive_mkdir=True,
        atomic_replace=True,
        atomic_publish_no_replace=True,
        complete_readback=True,
    )
    sftp = MagicMock()
    sftp.posix_rename.side_effect = OSError("connection dropped mid-rename")
    storage._sftp = sftp  # noqa: SLF001
    storage._closed = False  # noqa: SLF001
    storage._stat_kind = lambda remote: (  # type: ignore[method-assign]  # noqa: SLF001
        StorageEntryKind.FILE if remote.endswith(".bin") else None
    )
    with pytest.raises(StorageTransportError):
        storage.atomic_replace("src.bin", "dest.bin")
