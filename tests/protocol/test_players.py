"""Foundation coverage for ordered-player wrap-around (PT-42 foundation)."""

from __future__ import annotations

import pytest

from civ4_turn_relay.domain import (
    MANIFEST_SCHEMA_VERSION,
    MIN_CLIENT_PROTOCOL,
    Manifest,
    ProtocolMetadata,
)
from civ4_turn_relay.protocol import next_human_player_id
from tests.protocol.helpers import sample_players


def _manifest_current(player_id: str) -> Manifest:
    players = sample_players()
    return Manifest(
        schema_version=MANIFEST_SCHEMA_VERSION,
        game_id="example-match",
        display_name="Example Match",
        players=players,
        protocol_sequence=0,
        current_player_id=player_id,
        last_sender_id=None,
        accepted_save=None,
        accepted_save_hashes=(),
        previous_manifest_ref=None,
        protocol=ProtocolMetadata(
            min_client_protocol=MIN_CLIENT_PROTOCOL, last_operation_id=None
        ),
    )


@pytest.mark.pt("PT-42")
def test_pt42_foundation_three_humans_wrap_around() -> None:
    """PT-42 foundation: next human wraps from last ordered player to first."""
    assert next_human_player_id(_manifest_current("player_a")) == "player_b"
    assert next_human_player_id(_manifest_current("player_b")) == "player_c"
    assert next_human_player_id(_manifest_current("player_c")) == "player_a"
    assert (
        next_human_player_id(_manifest_current("player_a"), after_player_id="player_c")
        == "player_a"
    )
