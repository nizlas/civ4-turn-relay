"""Paramiko SFTP :class:`~civ4_turn_relay.storage.port.Storage` adapter.

Authenticates lazily. Host keys are verified against a known_hosts file and/or
an explicit SHA-256 fingerprint; unknown and mismatched keys fail closed.
Required atomic semantics are proven by a contained capability probe before
protocol commits can succeed.
"""

from __future__ import annotations

import base64
import hashlib
import uuid
from pathlib import Path, PurePosixPath
from types import TracebackType
from typing import Any

from civ4_turn_relay.domain import (
    DomainValidationError,
    GlobalConfig,
    validate_remote_relative_path,
)
from civ4_turn_relay.storage.errors import (
    StorageAlreadyExistsError,
    StorageCapabilityError,
    StorageError,
    StorageInvalidPathError,
    StorageNotEmptyError,
    StorageNotFoundError,
    StorageTransportError,
    StorageWrongKindError,
)
from civ4_turn_relay.storage.port import (
    StorageCapabilities,
    StorageEntry,
    StorageEntryKind,
)

_UNVERIFIED = StorageCapabilities(
    exclusive_mkdir=False,
    atomic_replace=False,
    atomic_publish_no_replace=False,
    complete_readback=False,
)


class ParamikoStorage:
    """Synchronous Storage over Paramiko SFTP with strict host-key checks."""

    def __init__(
        self,
        config: GlobalConfig,
        *,
        connect: bool = False,
    ) -> None:
        if not isinstance(config, GlobalConfig):
            raise TypeError("config must be a GlobalConfig instance")
        self._config = config
        self._transport: Any | None = None
        self._sftp: Any | None = None
        self._capabilities = _UNVERIFIED
        self._capabilities_verified = False
        self._closed = False
        self._root = _normalize_remote_root(config.sftp_remote_root)
        if connect:
            self.connect()

    def __repr__(self) -> str:
        return (
            f"ParamikoStorage(host={self._config.sftp_host!r}, "
            f"port={self._config.sftp_port}, "
            f"remote_root={self._config.sftp_remote_root!r})"
        )

    @classmethod
    def from_global_config(
        cls, config: GlobalConfig, *, connect: bool = False
    ) -> ParamikoStorage:
        return cls(config, connect=connect)

    def connect(self) -> None:
        """Authenticate and verify capabilities. Idempotent while connected."""
        self._ensure_open()
        if self._sftp is not None:
            return
        self._open_connection()
        self._verify_capabilities()

    def close(self) -> None:
        """Close the SFTP client and transport. Idempotent."""
        if self._closed and self._sftp is None and self._transport is None:
            return
        sftp = self._sftp
        transport = self._transport
        self._sftp = None
        self._transport = None
        self._capabilities = _UNVERIFIED
        self._capabilities_verified = False
        self._closed = True
        if sftp is not None:
            try:
                sftp.close()
            except Exception:
                pass
        if transport is not None:
            try:
                transport.close()
            except Exception:
                pass

    def __enter__(self) -> ParamikoStorage:
        self.connect()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        del exc_type, exc, tb
        self.close()

    def capabilities(self) -> StorageCapabilities:
        self._ensure_connected_for_readonly()
        return self._capabilities

    def mkdir(self, path: str) -> None:
        remote = self._resolve(path)
        self._require_capability("exclusive_mkdir", self._capabilities.exclusive_mkdir)
        sftp = self._require_sftp()
        relative_parent = str(PurePosixPath(path).parent)
        if relative_parent not in {".", ""}:
            parent_remote = self._resolve(relative_parent)
            if self._stat_kind(parent_remote) is None:
                raise StorageNotFoundError(
                    "parent directory not found", path=relative_parent
                )
        try:
            sftp.mkdir(remote)
        except OSError as error:
            self._raise_mapped(error, path=path, creating=True)

    def write_file(self, path: str, data: bytes, *, overwrite: bool = False) -> None:
        if not isinstance(data, bytes):
            raise TypeError("data must be bytes")
        remote = self._resolve(path)
        sftp = self._require_sftp()
        if not overwrite:
            kind = self._stat_kind(remote)
            if kind is StorageEntryKind.DIRECTORY:
                raise StorageWrongKindError("path is a directory", path=path)
            if kind is StorageEntryKind.FILE:
                raise StorageAlreadyExistsError("file already exists", path=path)
        else:
            kind = self._stat_kind(remote)
            if kind is StorageEntryKind.DIRECTORY:
                raise StorageWrongKindError("path is a directory", path=path)
        relative_parent = str(PurePosixPath(path).parent)
        if relative_parent not in {".", ""}:
            parent_remote = self._resolve(relative_parent)
            if self._stat_kind(parent_remote) is None:
                raise StorageNotFoundError(
                    "parent directory not found", path=relative_parent
                )
        try:
            with sftp.file(remote, "wb") as handle:
                handle.write(data)
                handle.flush()
        except OSError as error:
            self._raise_mapped(error, path=path)

    def read_file(self, path: str) -> bytes:
        remote = self._resolve(path)
        self._require_capability(
            "complete_readback", self._capabilities.complete_readback
        )
        sftp = self._require_sftp()
        kind = self._stat_kind(remote)
        if kind is StorageEntryKind.DIRECTORY:
            raise StorageWrongKindError("path is a directory", path=path)
        if kind is None:
            raise StorageNotFoundError("file not found", path=path)
        try:
            with sftp.file(remote, "rb") as handle:
                data = handle.read()
        except OSError as error:
            self._raise_mapped(error, path=path)
        if not isinstance(data, bytes):
            raise StorageTransportError("incomplete read-back", path=path)
        return data

    def list_dir(self, path: str) -> tuple[StorageEntry, ...]:
        remote = self._resolve(path)
        sftp = self._require_sftp()
        kind = self._stat_kind(remote)
        if kind is StorageEntryKind.FILE:
            raise StorageWrongKindError("path is a file", path=path)
        if kind is None:
            raise StorageNotFoundError("directory not found", path=path)
        try:
            names = sorted(sftp.listdir(remote))
        except OSError as error:
            self._raise_mapped(error, path=path)
        entries: list[StorageEntry] = []
        for name in names:
            child = f"{remote.rstrip('/')}/{name}"
            child_kind = self._stat_kind(child)
            if child_kind is None:
                continue
            entries.append(StorageEntry(name=name, kind=child_kind))
        return tuple(entries)

    def remove_file(self, path: str) -> None:
        remote = self._resolve(path)
        sftp = self._require_sftp()
        kind = self._stat_kind(remote)
        if kind is StorageEntryKind.DIRECTORY:
            raise StorageWrongKindError("path is a directory", path=path)
        if kind is None:
            raise StorageNotFoundError("file not found", path=path)
        try:
            sftp.remove(remote)
        except OSError as error:
            self._raise_mapped(error, path=path)

    def remove_dir(self, path: str) -> None:
        remote = self._resolve(path)
        sftp = self._require_sftp()
        kind = self._stat_kind(remote)
        if kind is StorageEntryKind.FILE:
            raise StorageWrongKindError("path is a file", path=path)
        if kind is None:
            raise StorageNotFoundError("directory not found", path=path)
        try:
            children = sftp.listdir(remote)
        except OSError as error:
            self._raise_mapped(error, path=path)
        if children:
            raise StorageNotEmptyError("directory is not empty", path=path)
        try:
            sftp.rmdir(remote)
        except OSError as error:
            self._raise_mapped(error, path=path)

    def publish_no_replace(self, source: str, destination: str) -> None:
        source_remote = self._resolve(source)
        dest_remote = self._resolve(destination)
        self._require_capability(
            "atomic_publish_no_replace",
            self._capabilities.atomic_publish_no_replace,
        )
        sftp = self._require_sftp()
        if self._stat_kind(source_remote) is not StorageEntryKind.FILE:
            if self._stat_kind(source_remote) is StorageEntryKind.DIRECTORY:
                raise StorageWrongKindError("source is a directory", path=source)
            raise StorageNotFoundError("source file not found", path=source)
        dest_kind = self._stat_kind(dest_remote)
        if dest_kind is StorageEntryKind.DIRECTORY:
            raise StorageWrongKindError("destination is a directory", path=destination)
        if dest_kind is StorageEntryKind.FILE:
            raise StorageAlreadyExistsError(
                "destination already exists", path=destination
            )
        relative_parent = str(PurePosixPath(destination).parent)
        if relative_parent not in {".", ""}:
            parent_remote = self._resolve(relative_parent)
            if self._stat_kind(parent_remote) is None:
                raise StorageNotFoundError(
                    "parent directory not found", path=relative_parent
                )
        try:
            # OpenSSH SSH_FXP_RENAME refuses to replace an existing destination.
            sftp.rename(source_remote, dest_remote)
        except OSError as error:
            # Re-check conflict after ambiguous failure without assuming success.
            if self._stat_kind(dest_remote) is StorageEntryKind.FILE:
                if self._stat_kind(source_remote) is None:
                    raise StorageAlreadyExistsError(
                        "destination already exists", path=destination
                    ) from error
            self._raise_mapped(error, path=destination)

    def atomic_replace(self, source: str, destination: str) -> None:
        source_remote = self._resolve(source)
        dest_remote = self._resolve(destination)
        self._require_capability("atomic_replace", self._capabilities.atomic_replace)
        sftp = self._require_sftp()
        if not hasattr(sftp, "posix_rename"):
            raise StorageCapabilityError(
                "posix-rename is unavailable", path=destination
            )
        if self._stat_kind(source_remote) is not StorageEntryKind.FILE:
            if self._stat_kind(source_remote) is StorageEntryKind.DIRECTORY:
                raise StorageWrongKindError("source is a directory", path=source)
            raise StorageNotFoundError("source file not found", path=source)
        if self._stat_kind(dest_remote) is StorageEntryKind.DIRECTORY:
            raise StorageWrongKindError("destination is a directory", path=destination)
        relative_parent = str(PurePosixPath(destination).parent)
        if relative_parent not in {".", ""}:
            parent_remote = self._resolve(relative_parent)
            if self._stat_kind(parent_remote) is None:
                raise StorageNotFoundError(
                    "parent directory not found", path=relative_parent
                )
        try:
            sftp.posix_rename(source_remote, dest_remote)
        except OSError as error:
            raise StorageTransportError(
                "atomic replace failed with ambiguous result",
                path=destination,
            ) from error

    def _ensure_open(self) -> None:
        if self._closed and self._sftp is None:
            # Allow reopen after close for read-only reconnect patterns.
            self._closed = False

    def _ensure_connected_for_readonly(self) -> None:
        self._ensure_open()
        if self._sftp is None:
            self.connect()

    def _require_sftp(self) -> Any:
        self._ensure_connected_for_readonly()
        if self._sftp is None:
            raise StorageTransportError("SFTP connection is not available")
        return self._sftp

    def _require_capability(self, name: str, enabled: bool) -> None:
        if not enabled:
            raise StorageCapabilityError(f"{name} is unavailable")

    def _resolve(self, path: str) -> str:
        try:
            relative = validate_remote_relative_path(path, field_path="path")
        except DomainValidationError as error:
            raise StorageInvalidPathError(error.message, path=path) from error
        joined = f"{self._root.rstrip('/')}/{relative}"
        normalized = _normalize_remote_root(joined)
        root = self._root.rstrip("/")
        if normalized != root and not normalized.startswith(root + "/"):
            raise StorageInvalidPathError("path escapes the configured root", path=path)
        return normalized

    def _stat_kind(self, remote: str) -> StorageEntryKind | None:
        sftp = self._require_sftp()
        try:
            import stat as stat_mod

            attrs = sftp.stat(remote)
            mode = int(attrs.st_mode)
            if stat_mod.S_ISDIR(mode):
                return StorageEntryKind.DIRECTORY
            if stat_mod.S_ISLNK(mode):
                # Refuse to treat escaping symlinks as ordinary entries.
                try:
                    target = sftp.normalize(remote)
                except OSError as error:
                    raise StorageTransportError(
                        "symlink resolution failed", path=remote
                    ) from error
                root = self._root.rstrip("/")
                if target != root and not target.startswith(root + "/"):
                    raise StorageInvalidPathError(
                        "symlink escapes the configured root", path=remote
                    )
                return StorageEntryKind.FILE
            return StorageEntryKind.FILE
        except FileNotFoundError:
            return None
        except OSError as error:
            if _is_not_found(error):
                return None
            raise StorageTransportError("stat failed", path=remote) from error

    def _open_connection(self) -> None:
        import paramiko

        config = self._config
        client = paramiko.SSHClient()
        if config.sftp_known_hosts_path is not None:
            client.load_host_keys(config.sftp_known_hosts_path)
            client.set_missing_host_key_policy(paramiko.RejectPolicy())
        elif config.sftp_host_key_sha256 is not None:
            client.set_missing_host_key_policy(
                _fingerprint_host_key_policy(
                    expected=config.sftp_host_key_sha256,
                    host=config.sftp_host,
                )
            )
        else:
            # GlobalConfig validation should prevent this.
            raise StorageTransportError("host-key verification is not configured")
        password = config.sftp_password
        pkey = None
        if config.sftp_private_key_path is not None:
            pkey = _load_private_key(
                config.sftp_private_key_path,
                passphrase=config.sftp_private_key_passphrase,
            )
        try:
            client.connect(
                hostname=config.sftp_host,
                port=config.sftp_port,
                username=config.sftp_username,
                password=password,
                pkey=pkey,
                look_for_keys=False,
                allow_agent=False,
                timeout=config.sftp_connect_timeout_seconds,
                auth_timeout=config.sftp_connect_timeout_seconds,
                banner_timeout=config.sftp_connect_timeout_seconds,
            )
        except paramiko.BadHostKeyException as error:
            raise StorageTransportError(
                f"host key mismatch for {config.sftp_host} ({error.key.get_name()})"
            ) from error
        except paramiko.SSHException as error:
            message = str(error).lower()
            if "not found in known_hosts" in message or "unknown host key" in message:
                raise StorageTransportError(
                    f"unknown host key for {config.sftp_host}"
                ) from error
            raise StorageTransportError(
                "SSH authentication or transport failed"
            ) from error
        except OSError as error:
            raise StorageTransportError("SFTP connection failed") from error

        transport = client.get_transport()
        if transport is None:
            client.close()
            raise StorageTransportError("SSH transport missing after connect")
        transport.set_keepalive(30)
        remote_key = transport.get_remote_server_key()
        if config.sftp_host_key_sha256 is not None:
            actual = _sha256_fingerprint(remote_key)
            if actual != config.sftp_host_key_sha256:
                client.close()
                raise StorageTransportError(
                    f"host key fingerprint mismatch for {config.sftp_host} "
                    f"({remote_key.get_name()})"
                )
        try:
            sftp = paramiko.SFTPClient.from_transport(transport)
        except Exception as error:
            client.close()
            raise StorageTransportError("failed to open SFTP channel") from error
        if sftp is None:
            client.close()
            raise StorageTransportError("failed to open SFTP channel")
        self._transport = transport
        self._sftp = sftp
        self._closed = False
        # Retain SSHClient so transport is not closed by GC.
        self._ssh_client = client

    def _verify_capabilities(self) -> None:
        """Probe exclusive mkdir, publish-no-replace, posix-rename, read-back."""
        sftp = self._require_sftp()
        probe = f".civ4-relay-cap-probe-{uuid.uuid4().hex}"
        probe_remote = self._resolve(probe)
        exclusive_mkdir = False
        atomic_publish = False
        atomic_replace = False
        complete_readback = False
        cleanup_ambiguous = False
        try:
            sftp.mkdir(probe_remote)
            try:
                sftp.mkdir(probe_remote)
            except OSError:
                exclusive_mkdir = True
            else:
                exclusive_mkdir = False

            source_a = f"{probe}/a.bin"
            dest_a = f"{probe}/published.bin"
            self._write_raw(self._resolve(source_a), b"probe-a")
            sftp.rename(self._resolve(source_a), self._resolve(dest_a))
            try:
                self._write_raw(self._resolve(source_a), b"probe-a2")
                sftp.rename(self._resolve(source_a), self._resolve(dest_a))
                atomic_publish = False
            except OSError:
                # Refusal must leave the existing destination byte-identical.
                atomic_publish = (
                    self._stat_kind(self._resolve(dest_a)) is StorageEntryKind.FILE
                    and self._read_raw(self._resolve(dest_a)) == b"probe-a"
                )

            source_b = f"{probe}/b.bin"
            dest_b = f"{probe}/replaced.bin"
            self._write_raw(self._resolve(source_b), b"first")
            sftp.rename(self._resolve(source_b), self._resolve(dest_b))
            self._write_raw(self._resolve(source_b), b"second")
            if hasattr(sftp, "posix_rename"):
                sftp.posix_rename(self._resolve(source_b), self._resolve(dest_b))
                data = self._read_raw(self._resolve(dest_b))
                atomic_replace = data == b"second"
            else:
                atomic_replace = False

            sample = self._read_raw(self._resolve(dest_a))
            complete_readback = sample == b"probe-a"
        except StorageError:
            raise
        except Exception as error:
            raise StorageCapabilityError(
                "capability probe failed; refusing non-capable server"
            ) from error
        finally:
            try:
                self._cleanup_probe(probe)
            except Exception:
                cleanup_ambiguous = True

        if cleanup_ambiguous:
            # Probe data may remain, but never delete unrelated game objects.
            pass
        if not (
            exclusive_mkdir and atomic_publish and atomic_replace and complete_readback
        ):
            self._capabilities = StorageCapabilities(
                exclusive_mkdir=exclusive_mkdir,
                atomic_replace=atomic_replace,
                atomic_publish_no_replace=atomic_publish,
                complete_readback=complete_readback,
            )
            self._capabilities_verified = True
            raise StorageCapabilityError(
                "server lacks required SFTP semantics for protocol commits"
            )
        self._capabilities = StorageCapabilities(
            exclusive_mkdir=True,
            atomic_replace=True,
            atomic_publish_no_replace=True,
            complete_readback=True,
        )
        self._capabilities_verified = True

    def _write_raw(self, remote: str, data: bytes) -> None:
        sftp = self._require_sftp()
        with sftp.file(remote, "wb") as handle:
            handle.write(data)
            handle.flush()

    def _read_raw(self, remote: str) -> bytes:
        sftp = self._require_sftp()
        with sftp.file(remote, "rb") as handle:
            data = handle.read()
        if not isinstance(data, bytes):
            raise StorageTransportError("incomplete read-back", path=remote)
        return data

    def _cleanup_probe(self, probe: str) -> None:
        remote = self._resolve(probe)
        sftp = self._require_sftp()
        try:
            for name in sftp.listdir(remote):
                child = f"{remote.rstrip('/')}/{name}"
                kind = self._stat_kind(child)
                if kind is StorageEntryKind.FILE:
                    sftp.remove(child)
                elif kind is StorageEntryKind.DIRECTORY:
                    sftp.rmdir(child)
            sftp.rmdir(remote)
        except OSError as error:
            raise StorageTransportError(
                "capability probe cleanup ambiguous", path=probe
            ) from error

    def _raise_mapped(
        self, error: OSError, *, path: str, creating: bool = False
    ) -> None:
        if _is_not_found(error):
            raise StorageNotFoundError("path not found", path=path) from error
        if _is_exists(error):
            raise StorageAlreadyExistsError("path already exists", path=path) from error
        if _is_permission(error):
            raise StorageTransportError("permission denied", path=path) from error
        if creating and _is_exists(error):
            raise StorageAlreadyExistsError("path already exists", path=path) from error
        raise StorageTransportError("SFTP operation failed", path=path) from error


def _fingerprint_host_key_policy(*, expected: str, host: str) -> Any:
    """Build a Paramiko policy that accepts only a matching SHA-256 fingerprint."""
    import paramiko

    pinned_host = host

    class _FingerprintHostKeyPolicy(paramiko.MissingHostKeyPolicy):
        def missing_host_key(self, client: Any, hostname: str, key: Any) -> None:
            actual = _sha256_fingerprint(key)
            if actual != expected:
                raise paramiko.BadHostKeyException(hostname, key, key)
            # Session-only accept; never persists trust outside this client.
            _ = pinned_host
            client.get_host_keys().add(hostname, key.get_name(), key)

    return _FingerprintHostKeyPolicy()


def _normalize_remote_root(root: str) -> str:
    if not isinstance(root, str) or not root:
        raise DomainValidationError(
            "expected a non-empty remote root",
            field_path="sftp_remote_root",
        )
    if "\\" in root:
        raise DomainValidationError(
            "backslashes are not allowed in remote paths",
            field_path="sftp_remote_root",
        )
    pure = PurePosixPath(root)
    normalized = pure.as_posix()
    if normalized != "/" and normalized.endswith("/"):
        normalized = normalized.rstrip("/")
    return normalized


def _sha256_fingerprint(key: Any) -> str:
    digest = hashlib.sha256(key.asbytes()).digest()
    encoded = base64.b64encode(digest).decode("ascii").rstrip("=")
    return f"SHA256:{encoded}"


def _load_private_key(path: str, *, passphrase: str | None) -> Any:
    import paramiko

    key_path = Path(path)
    loaders = (
        paramiko.Ed25519Key.from_private_key_file,
        paramiko.RSAKey.from_private_key_file,
        paramiko.ECDSAKey.from_private_key_file,
    )
    last_error: Exception | None = None
    for loader in loaders:
        try:
            return loader(str(key_path), password=passphrase)
        except Exception as error:  # noqa: BLE001 - try next key type
            last_error = error
    raise StorageTransportError("failed to load private key") from last_error


def _is_not_found(error: OSError) -> bool:
    errno = getattr(error, "errno", None)
    code = getattr(error, "code", None)
    if errno in {2, None} and "no such file" in str(error).lower():
        return True
    return code == 2 or errno == 2


def _is_exists(error: OSError) -> bool:
    errno = getattr(error, "errno", None)
    code = getattr(error, "code", None)
    if "exists" in str(error).lower():
        return True
    return code == 11 or errno in {17}


def _is_permission(error: OSError) -> bool:
    errno = getattr(error, "errno", None)
    code = getattr(error, "code", None)
    return code == 3 or errno == 13 or "permission" in str(error).lower()
