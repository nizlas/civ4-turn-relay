"""Shared synthetic fixtures for protocol tests (placeholders only)."""

from __future__ import annotations

from typing import Literal

from civ4_turn_relay.domain import (
    MatchConfig,
    Player,
    SaveMatchingRules,
)
from civ4_turn_relay.protocol import InMemoryOperationJournal, initialize_match
from civ4_turn_relay.storage import (
    FakeStorage,
    Storage,
    StorageCapabilities,
    StorageEntry,
    StorageNotFoundError,
    StorageTransportError,
)

OP_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
OP_ID_2 = "11111111-2222-3333-4444-555555555555"
OP_ID_3 = "99999999-aaaa-bbbb-cccc-dddddddddddd"
HASH_1 = "a1b2c3d4e5f6789012345678abcdef9012345678abcdef9012345678abcdef90"
HASH_2 = "b" * 64
HASH_3 = "c" * 64
CLIENT_A = "client-alpha"
CLIENT_B = "client-bravo"
NOW_UTC = "2026-08-10T19:00:00Z"
SAVE_NAME = "ExampleMatch_PlayerA.CivBeyondSwordSave"


def sample_players() -> tuple[Player, ...]:
    return (
        Player(id="player_a", display_name="Player A"),
        Player(id="player_b", display_name="Player B"),
        Player(id="player_c", display_name="Player C"),
    )


def sample_match_config(
    *,
    game_id: str = "example-match",
    players: tuple[Player, ...] | None = None,
    local_player_id: str = "player_a",
) -> MatchConfig:
    resolved = players if players is not None else sample_players()[:2]
    return MatchConfig(
        game_id=game_id,
        display_name="Example Match",
        players=resolved,
        local_player_id=local_player_id,
        launch_profile=None,
        mod_name=None,
        pbem_save_directory=r"C:\Games\Civ4\Saves\pbem",
        save_matching=SaveMatchingRules(filename_glob="*.CivBeyondSwordSave"),
    )


def initialize_ready_match(
    *,
    storage: FakeStorage | None = None,
    journal: InMemoryOperationJournal | None = None,
    game_id: str = "example-match",
    players: tuple[Player, ...] | None = None,
    local_player_id: str = "player_a",
    operation_id: str = OP_ID,
) -> tuple[FakeStorage, InMemoryOperationJournal, str]:
    """Create a sequence-zero match ready for handoff tests."""
    resolved_storage = storage if storage is not None else FakeStorage()
    resolved_journal = journal if journal is not None else InMemoryOperationJournal()
    config = sample_match_config(
        game_id=game_id, players=players, local_player_id=local_player_id
    )
    result = initialize_match(resolved_storage, config, operation_id=operation_id)
    assert result.initialized, result.outcome
    return resolved_storage, resolved_journal, config.game_id


class CountingStorage:
    """Storage wrapper that counts every port call (validation-before-I/O spies)."""

    def __init__(self, inner: Storage) -> None:
        self._inner = inner
        self.calls: list[str] = []

    def _record(self, name: str) -> None:
        self.calls.append(name)

    def capabilities(self) -> StorageCapabilities:
        self._record("capabilities")
        return self._inner.capabilities()

    def mkdir(self, path: str) -> None:
        self._record("mkdir")
        self._inner.mkdir(path)

    def write_file(self, path: str, data: bytes, *, overwrite: bool = False) -> None:
        self._record("write_file")
        self._inner.write_file(path, data, overwrite=overwrite)

    def read_file(self, path: str) -> bytes:
        self._record("read_file")
        return self._inner.read_file(path)

    def list_dir(self, path: str) -> tuple[StorageEntry, ...]:
        self._record("list_dir")
        return self._inner.list_dir(path)

    def remove_file(self, path: str) -> None:
        self._record("remove_file")
        self._inner.remove_file(path)

    def remove_dir(self, path: str) -> None:
        self._record("remove_dir")
        self._inner.remove_dir(path)

    def publish_no_replace(self, source: str, destination: str) -> None:
        self._record("publish_no_replace")
        self._inner.publish_no_replace(source, destination)

    def atomic_replace(self, source: str, destination: str) -> None:
        self._record("atomic_replace")
        self._inner.atomic_replace(source, destination)


class UncertainReplaceStorage:
    """Installs chosen destination bytes, then raises StorageTransportError.

    Simulates an uncertain atomic-replace outcome without further protocol
    mutation. ``committed_bytes="source"`` installs the staged source object.
    """

    def __init__(
        self,
        inner: Storage,
        *,
        committed_bytes: bytes | Literal["source"],
    ) -> None:
        self._inner = inner
        self._committed_bytes = committed_bytes
        self._uncertain_fired = False
        self.mutations_after_uncertain = 0

    def _count_mutation(self) -> None:
        if self._uncertain_fired:
            self.mutations_after_uncertain += 1

    def capabilities(self) -> StorageCapabilities:
        return self._inner.capabilities()

    def mkdir(self, path: str) -> None:
        self._count_mutation()
        self._inner.mkdir(path)

    def write_file(self, path: str, data: bytes, *, overwrite: bool = False) -> None:
        self._count_mutation()
        self._inner.write_file(path, data, overwrite=overwrite)

    def read_file(self, path: str) -> bytes:
        return self._inner.read_file(path)

    def list_dir(self, path: str) -> tuple[StorageEntry, ...]:
        return self._inner.list_dir(path)

    def remove_file(self, path: str) -> None:
        self._count_mutation()
        self._inner.remove_file(path)

    def remove_dir(self, path: str) -> None:
        self._count_mutation()
        self._inner.remove_dir(path)

    def publish_no_replace(self, source: str, destination: str) -> None:
        self._count_mutation()
        self._inner.publish_no_replace(source, destination)

    def atomic_replace(self, source: str, destination: str) -> None:
        self._count_mutation()
        if self._committed_bytes == "source":
            data = self._inner.read_file(source)
        else:
            data = self._committed_bytes
        self._inner.write_file(destination, data, overwrite=True)
        try:
            self._inner.remove_file(source)
        except StorageNotFoundError:
            pass
        self._uncertain_fired = True
        raise StorageTransportError("uncertain atomic replace result", path=destination)
