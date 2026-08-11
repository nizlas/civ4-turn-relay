"""Adapter-neutral storage providers for the reusable contract suite.

Providers construct :class:`~civ4_turn_relay.storage.port.Storage` instances.
Contract cases must not inspect provider internals or concrete adapter types.
A second delegating provider wraps :class:`FakeStorage` so the same cases are
proven against a non-fake public type without adding Paramiko in P2.

``create()`` takes no fake-specific construction controls (capability overrides
included). Capability-negation coverage lives in fake-only tests.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from civ4_turn_relay.storage import FakeStorage, Storage, StorageCapabilities
from civ4_turn_relay.storage.port import (
    StorageEntry,
)


@runtime_checkable
class StorageProvider(Protocol):
    """Factory that opens a fresh Storage for one contract case."""

    @property
    def name(self) -> str:
        """Stable id used in pytest parametrization."""

    def create(self) -> Storage:
        """Return a new storage rooted at an empty tree."""


class FakeStorageProvider:
    """Provider that returns a plain in-memory FakeStorage as Storage."""

    @property
    def name(self) -> str:
        return "fake"

    def create(self) -> Storage:
        return FakeStorage()


class DelegatingStorage:
    """Opaque Storage wrapper that only forwards port methods.

    Contract cases see only the public Storage surface; they cannot reach the
    wrapped fake's snapshot/fault APIs through this type.
    """

    def __init__(self, inner: Storage) -> None:
        self._inner = inner

    def capabilities(self) -> StorageCapabilities:
        return self._inner.capabilities()

    def mkdir(self, path: str) -> None:
        self._inner.mkdir(path)

    def write_file(self, path: str, data: bytes, *, overwrite: bool = False) -> None:
        self._inner.write_file(path, data, overwrite=overwrite)

    def read_file(self, path: str) -> bytes:
        return self._inner.read_file(path)

    def list_dir(self, path: str) -> tuple[StorageEntry, ...]:
        return self._inner.list_dir(path)

    def remove_file(self, path: str) -> None:
        self._inner.remove_file(path)

    def remove_dir(self, path: str) -> None:
        self._inner.remove_dir(path)

    def publish_no_replace(self, source: str, destination: str) -> None:
        self._inner.publish_no_replace(source, destination)

    def atomic_replace(self, source: str, destination: str) -> None:
        self._inner.atomic_replace(source, destination)

    def close(self) -> None:
        close = getattr(self._inner, "close", None)
        if callable(close):
            close()


class DelegatingStorageProvider:
    """Provider that returns DelegatingStorage around FakeStorage."""

    @property
    def name(self) -> str:
        return "delegating-wrapper"

    def create(self) -> Storage:
        return DelegatingStorage(FakeStorage())


CONTRACT_PROVIDERS: tuple[StorageProvider, ...] = (
    FakeStorageProvider(),
    DelegatingStorageProvider(),
)
