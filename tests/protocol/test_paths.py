"""Game path scoping: validation before join, game-relative vs storage paths."""

from __future__ import annotations

import pytest

from civ4_turn_relay.domain import DomainValidationError
from civ4_turn_relay.protocol import GamePaths


def test_resolves_game_relative_paths_under_game_root() -> None:
    paths = GamePaths("example-match")
    assert paths.root == "example-match"
    assert paths.manifest == "example-match/manifest.json"
    assert paths.saves == "example-match/saves"
    assert paths.temporary == "example-match/temporary"
    assert paths.locks == "example-match/locks"
    assert paths.history == "example-match/history"
    assert paths.resolve("saves/000001_aabbccddeeff.sav") == (
        "example-match/saves/000001_aabbccddeeff.sav"
    )
    assert paths.temporary_manifest("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee") == (
        "example-match/temporary/manifest-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee.json"
    )


@pytest.mark.parametrize(
    "bad_relative",
    ["", "/absolute", "a/../b", "..", "./a", "a//b", "a\\b", "a/"],
)
def test_reject_unsafe_game_relative_paths(bad_relative: str) -> None:
    paths = GamePaths("example-match")
    with pytest.raises(DomainValidationError):
        paths.resolve(bad_relative)


@pytest.mark.parametrize(
    "bad_game_id",
    ["", "AB", "../x", "a/b", "a\\b", ".hidden", "a..b", "a b"],
)
def test_reject_unsafe_game_id_before_resolve(bad_game_id: str) -> None:
    with pytest.raises(DomainValidationError):
        GamePaths(bad_game_id)
