"""Fixtures that rebind the reusable contract to every StorageProvider."""

from __future__ import annotations

from typing import cast

import pytest

from civ4_turn_relay.storage import Storage
from tests.storage.contract.provider import CONTRACT_PROVIDERS, StorageProvider


@pytest.fixture(params=CONTRACT_PROVIDERS, ids=lambda provider: provider.name)
def provider(request: pytest.FixtureRequest) -> StorageProvider:
    return cast(StorageProvider, request.param)


@pytest.fixture
def storage(provider: StorageProvider) -> Storage:
    return provider.create()
