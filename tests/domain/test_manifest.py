"""Table-driven tests for the manifest model, validation, and JSON forms.

All values are synthetic placeholders; no real saves or credentials.
"""

import json
from typing import Any

import pytest

from civ4_turn_relay.domain import (
    AcceptedSave,
    DomainValidationError,
    Manifest,
    Player,
    ProtocolMetadata,
)

HASH_1 = "a1b2c3d4e5f6789012345678abcdef9012345678abcdef9012345678abcdef90"
HASH_2 = "b" * 64
OP_ID = "11111111-2222-3333-4444-555555555555"


def seq0_mapping() -> dict[str, Any]:
    return {
        "accepted_save": None,
        "accepted_save_hashes": [],
        "current_player_id": "player_a",
        "display_name": "Example Match",
        "game_id": "example-match",
        "last_sender_id": None,
        "players": [
            {"display_name": "Player A", "id": "player_a"},
            {"display_name": "Player B", "id": "player_b"},
        ],
        "previous_manifest_ref": None,
        "protocol": {"last_operation_id": None, "min_client_protocol": 1},
        "protocol_sequence": 0,
        "schema_version": 1,
    }


def seq1_mapping() -> dict[str, Any]:
    return {
        "accepted_save": {
            "accepted_at": "2026-08-10T19:43:00Z",
            "original_filename": "ExampleMatch_PlayerA.CivBeyondSwordSave",
            "remote_path": "saves/000001_a1b2c3d4e5f6.CivBeyondSwordSave",
            "sha256": HASH_1,
            "size_bytes": 1234567,
        },
        "accepted_save_hashes": [HASH_1],
        "current_player_id": "player_b",
        "display_name": "Example Match",
        "game_id": "example-match",
        "last_sender_id": "player_a",
        "players": [
            {"display_name": "Player A", "id": "player_a"},
            {"display_name": "Player B", "id": "player_b"},
        ],
        "previous_manifest_ref": "history/manifest-000000-0123456789ab.json",
        "protocol": {"last_operation_id": OP_ID, "min_client_protocol": 1},
        "protocol_sequence": 1,
        "schema_version": 1,
    }


def test_valid_sequence_zero_manifest() -> None:
    manifest = Manifest.from_mapping(seq0_mapping())
    assert manifest.protocol_sequence == 0
    assert manifest.accepted_save is None
    assert manifest.last_sender_id is None
    assert manifest.previous_manifest_ref is None
    assert manifest.protocol.last_operation_id is None
    assert manifest.accepted_save_hashes == ()
    assert manifest.current_player_id == "player_a"
    assert [player.id for player in manifest.players] == ["player_a", "player_b"]


def test_valid_sequence_one_manifest() -> None:
    manifest = Manifest.from_mapping(seq1_mapping())
    assert manifest.protocol_sequence == 1
    assert manifest.accepted_save is not None
    assert manifest.accepted_save.sha256 == HASH_1
    assert manifest.accepted_save_hashes == (HASH_1,)
    assert manifest.last_sender_id == "player_a"
    assert manifest.protocol.last_operation_id == OP_ID


def test_direct_construction_matches_mapping_parse() -> None:
    manifest = Manifest(
        schema_version=1,
        game_id="example-match",
        display_name="Example Match",
        players=(
            Player(id="player_a", display_name="Player A"),
            Player(id="player_b", display_name="Player B"),
        ),
        protocol_sequence=1,
        current_player_id="player_b",
        last_sender_id="player_a",
        accepted_save=AcceptedSave(
            sha256=HASH_1,
            size_bytes=1234567,
            remote_path="saves/000001_a1b2c3d4e5f6.CivBeyondSwordSave",
            original_filename="ExampleMatch_PlayerA.CivBeyondSwordSave",
            accepted_at="2026-08-10T19:43:00Z",
        ),
        accepted_save_hashes=(HASH_1,),
        previous_manifest_ref="history/manifest-000000-0123456789ab.json",
        protocol=ProtocolMetadata(min_client_protocol=1, last_operation_id=OP_ID),
    )
    assert manifest == Manifest.from_mapping(seq1_mapping())


def test_deterministic_serialization() -> None:
    manifest = Manifest.from_mapping(seq1_mapping())
    first = manifest.to_json_bytes()
    second = manifest.to_json_bytes()
    assert first == second
    assert not first.startswith(b"\xef\xbb\xbf")  # no BOM
    assert b"\r" not in first  # LF only
    assert first.endswith(b"\n")
    assert not first.endswith(b"\n\n")  # exactly one final newline
    keys = list(json.loads(first).keys())
    assert keys == sorted(keys)  # lexicographically sorted keys


def test_serialization_round_trip() -> None:
    for mapping in (seq0_mapping(), seq1_mapping()):
        manifest = Manifest.from_mapping(mapping)
        assert Manifest.from_json_bytes(manifest.to_json_bytes()) == manifest
        assert manifest.to_mapping() == mapping


def test_parse_serialize_stability() -> None:
    data = Manifest.from_mapping(seq1_mapping()).to_json_bytes()
    assert Manifest.from_json_bytes(data).to_json_bytes() == data


def invalid_manifest_cases() -> list[tuple[str, dict[str, Any], str]]:
    cases: list[tuple[str, dict[str, Any], str]] = []

    def case(name: str, expected_path: str, **overrides: Any) -> None:
        base = seq1_mapping() if "seq1" in name else seq0_mapping()
        for key, value in overrides.items():
            if value is ...:
                del base[key]
            else:
                base[key] = value
        cases.append((name, base, expected_path))

    # Hash-list rules (protocol §3.2)
    case("seq1 hash list too short", "accepted_save_hashes", protocol_sequence=2)
    case(
        "seq1 hash list too long",
        "accepted_save_hashes",
        accepted_save_hashes=[HASH_2, HASH_1],
    )
    case(
        "seq1 duplicate hashes",
        "accepted_save_hashes",
        accepted_save_hashes=[HASH_1, HASH_1],
        protocol_sequence=2,
    )
    case(
        "seq1 latest hash mismatch",
        "accepted_save_hashes",
        accepted_save_hashes=[HASH_2],
    )
    case(
        "seq1 invalid hash entry",
        "accepted_save_hashes[0]",
        accepted_save_hashes=["A" * 64],
    )
    case(
        "seq1 non-string hash entry",
        "accepted_save_hashes[0]",
        accepted_save_hashes=[123],
    )
    case(
        "seq0 nonempty hash list",
        "accepted_save_hashes",
        **{
            "accepted_save_hashes": [HASH_1],
        },
    )
    # Null coupling (§3.1/§3.2)
    case(
        "seq0 with accepted save",
        "accepted_save",
        **{
            "accepted_save": seq1_mapping()["accepted_save"],
            "accepted_save_hashes": [],
        },
    )
    case("seq0 with last sender", "last_sender_id", last_sender_id="player_a")
    case(
        "seq0 with previous ref",
        "previous_manifest_ref",
        previous_manifest_ref="history/manifest-000000-0123456789ab.json",
    )
    case(
        "seq0 with last operation",
        "protocol.last_operation_id",
        protocol={"last_operation_id": OP_ID, "min_client_protocol": 1},
    )
    case("seq1 missing accepted save", "accepted_save", accepted_save=None)
    case("seq1 missing last sender", "last_sender_id", last_sender_id=None)
    case(
        "seq1 missing previous ref",
        "previous_manifest_ref",
        previous_manifest_ref=None,
    )
    case(
        "seq1 missing last operation",
        "protocol.last_operation_id",
        protocol={"last_operation_id": None, "min_client_protocol": 1},
    )
    # Schema version
    case("seq0 wrong schema version", "schema_version", schema_version=2)
    case("seq0 string schema version", "schema_version", schema_version="1")
    case("seq0 boolean schema version", "schema_version", schema_version=True)
    # Missing / unexpected / mistyped fields
    case("seq0 missing players", "players", players=...)
    case("seq0 missing protocol", "protocol", protocol=...)
    case("seq0 missing game id", "game_id", game_id=...)
    case("seq0 unexpected field", "surprise", surprise="x")
    case("seq0 players not array", "players", players="player_a")
    case("seq0 player not object", "players[0]", players=["player_a"])
    case(
        "seq0 player unexpected field",
        "players[0].is_ai",
        players=[
            {"display_name": "Player A", "id": "player_a", "is_ai": False},
            {"display_name": "Player B", "id": "player_b"},
        ],
    )
    case(
        "seq0 player missing display name",
        "players[0].display_name",
        players=[{"id": "player_a"}, {"display_name": "B", "id": "player_b"}],
    )
    case("seq0 protocol not object", "protocol", protocol=[1])
    case(
        "seq0 protocol unexpected field",
        "protocol.extra",
        protocol={
            "extra": 1,
            "last_operation_id": None,
            "min_client_protocol": 1,
        },
    )
    case(
        "seq0 min client protocol not 1",
        "protocol.min_client_protocol",
        protocol={"last_operation_id": None, "min_client_protocol": 2},
    )
    case("seq0 display name not string", "display_name", display_name=7)
    case("seq0 empty display name", "display_name", display_name="")
    case(
        "seq1 accepted save unexpected field",
        "accepted_save.extra",
        accepted_save={**seq1_mapping()["accepted_save"], "extra": 1},
    )
    case(
        "seq1 accepted save missing sha256",
        "accepted_save.sha256",
        accepted_save={
            key: value
            for key, value in seq1_mapping()["accepted_save"].items()
            if key != "sha256"
        },
    )
    case(
        "seq1 accepted save size not integer",
        "accepted_save.size_bytes",
        accepted_save={**seq1_mapping()["accepted_save"], "size_bytes": "1"},
    )
    # Booleans are not integers
    case(
        "seq0 boolean protocol sequence",
        "protocol_sequence",
        protocol_sequence=False,
    )
    case(
        "seq1 boolean size bytes",
        "accepted_save.size_bytes",
        accepted_save={**seq1_mapping()["accepted_save"], "size_bytes": True},
    )
    case(
        "seq0 boolean min client protocol",
        "protocol.min_client_protocol",
        protocol={"last_operation_id": None, "min_client_protocol": True},
    )
    # Sequence range and player references
    case("seq0 negative sequence", "protocol_sequence", protocol_sequence=-1)
    case(
        "seq0 unknown current player",
        "current_player_id",
        current_player_id="nobody",
    )
    case(
        "seq1 unknown last sender",
        "last_sender_id",
        last_sender_id="nobody",
    )
    case("seq0 empty players", "players", players=[])
    case(
        "seq0 duplicate player ids",
        "players[1].id",
        players=[
            {"display_name": "Player A", "id": "player_a"},
            {"display_name": "Other", "id": "player_a"},
        ],
        current_player_id="player_a",
    )
    case(
        "seq0 invalid player id",
        "players[0].id",
        players=[{"display_name": "Player A", "id": "Player-A"}],
    )
    # UUID / timestamp / path / filename / size details
    case(
        "seq1 invalid operation uuid",
        "protocol.last_operation_id",
        protocol={"last_operation_id": "not-a-uuid", "min_client_protocol": 1},
    )
    case(
        "seq1 invalid accepted timestamp",
        "accepted_save.accepted_at",
        accepted_save={
            **seq1_mapping()["accepted_save"],
            "accepted_at": "2026-08-10 19:43:00",
        },
    )
    case(
        "seq1 invalid accepted hash",
        "accepted_save.sha256",
        accepted_save={**seq1_mapping()["accepted_save"], "sha256": "abc"},
        accepted_save_hashes=["abc"],
    )
    case(
        "seq1 absolute save path",
        "accepted_save.remote_path",
        accepted_save={
            **seq1_mapping()["accepted_save"],
            "remote_path": "/saves/000001_ab.sav",
        },
    )
    case(
        "seq1 traversing save path",
        "accepted_save.remote_path",
        accepted_save={
            **seq1_mapping()["accepted_save"],
            "remote_path": "saves/../escape.sav",
        },
    )
    case(
        "seq1 save path outside saves",
        "accepted_save.remote_path",
        accepted_save={
            **seq1_mapping()["accepted_save"],
            "remote_path": "temporary/000001_ab.sav",
        },
    )
    case(
        "seq1 original filename with directory",
        "accepted_save.original_filename",
        accepted_save={
            **seq1_mapping()["accepted_save"],
            "original_filename": "dir/name.sav",
        },
    )
    case(
        "seq1 zero size",
        "accepted_save.size_bytes",
        accepted_save={**seq1_mapping()["accepted_save"], "size_bytes": 0},
    )
    case(
        "seq1 invalid history ref",
        "previous_manifest_ref",
        previous_manifest_ref="saves/manifest.json",
    )
    case(
        "seq1 traversing history ref",
        "previous_manifest_ref",
        previous_manifest_ref="history/../manifest.json",
    )
    case("seq0 invalid game id", "game_id", game_id="Bad..Id")
    return cases


@pytest.mark.parametrize(
    ("name", "mapping", "expected_path"),
    invalid_manifest_cases(),
    ids=[name for name, _, _ in invalid_manifest_cases()],
)
def test_invalid_manifests(
    name: str, mapping: dict[str, Any], expected_path: str
) -> None:
    with pytest.raises(DomainValidationError) as exc_info:
        Manifest.from_mapping(mapping)
    assert exc_info.value.field_path == expected_path


@pytest.mark.parametrize(
    ("data", "reason"),
    [
        (b"\xff\xfe\x00", "invalid UTF-8"),
        (b"\xef\xbb\xbf{}", "UTF-8 BOM"),
        (b"{not json", "malformed JSON"),
        (b"[]\n", "top-level array"),
        (b'"text"\n', "top-level string"),
        (b"42\n", "top-level number"),
        (b"null\n", "top-level null"),
    ],
)
def test_invalid_manifest_documents(data: bytes, reason: str) -> None:
    with pytest.raises(DomainValidationError):
        Manifest.from_json_bytes(data)


def test_manifest_is_immutable() -> None:
    manifest = Manifest.from_mapping(seq0_mapping())
    with pytest.raises(AttributeError):
        manifest.protocol_sequence = 1  # type: ignore[misc]
