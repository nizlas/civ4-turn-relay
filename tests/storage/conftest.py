"""Fixtures for storage contract and fake tests.

Contract modules request a ``storage`` fixture typed as the port. Binding it
to :class:`FakeStorage` here lets a later Paramiko suite reuse the same tests
by providing an alternate fixture.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from civ4_turn_relay.storage import FakeStorage, Storage, StorageCapabilities


@pytest.fixture
def make_storage() -> Callable[..., FakeStorage]:
    def _make(*, capabilities: StorageCapabilities | None = None) -> FakeStorage:
        return FakeStorage(capabilities=capabilities)

    return _make


@pytest.fixture
def storage(make_storage: Callable[..., FakeStorage]) -> Storage:
    return make_storage()


@pytest.fixture
def fake(make_storage: Callable[..., FakeStorage]) -> FakeStorage:
    return make_storage()
