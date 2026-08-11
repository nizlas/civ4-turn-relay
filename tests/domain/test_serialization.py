"""Tests for deterministic JSON helpers and strict document parsing."""

import pytest

from civ4_turn_relay.domain import (
    DomainValidationError,
    Manifest,
    MatchConfig,
    parse_json_object_bytes,
    to_canonical_json_bytes,
)

HASH_1 = "a1b2c3d4e5f6789012345678abcdef9012345678abcdef9012345678abcdef90"
OP_ID = "11111111-2222-3333-4444-555555555555"


def test_keys_are_lexicographically_sorted() -> None:
    data = to_canonical_json_bytes({"zebra": 1, "alpha": 2, "mid": {"b": 1, "a": 2}})
    text = data.decode("utf-8")
    assert text.index('"alpha"') < text.index('"mid"') < text.index('"zebra"')
    assert text.index('"a"') < text.index('"b"')


def test_lf_newlines_and_single_trailing_newline() -> None:
    data = to_canonical_json_bytes({"a": [1, 2], "b": {"c": None}})
    assert b"\r" not in data
    assert data.endswith(b"\n")
    assert not data.endswith(b"\n\n")


def test_no_bom_and_utf8_content_preserved() -> None:
    data = to_canonical_json_bytes({"name": "smörgåsbord"})
    assert not data.startswith(b"\xef\xbb\xbf")
    assert "smörgåsbord" in data.decode("utf-8")


def test_serialization_is_deterministic_across_insert_order() -> None:
    first = to_canonical_json_bytes({"a": 1, "b": 2})
    second = to_canonical_json_bytes({"b": 2, "a": 1})
    assert first == second


def test_parse_round_trip() -> None:
    mapping = {"a": [1, 2, 3], "b": None, "c": {"d": "x"}}
    parsed = parse_json_object_bytes(to_canonical_json_bytes(mapping))
    assert dict(parsed) == mapping


@pytest.mark.parametrize(
    "data",
    [
        b"\xff\xfe invalid utf-8",
        b"\xef\xbb\xbf{}",  # BOM
        b"{broken",
        b"",
        b"[]",  # wrong top-level type
        b'"string"',
        b"7",
        b"true",
        b"null",
    ],
)
def test_parse_rejects_invalid_documents(data: bytes) -> None:
    with pytest.raises(DomainValidationError):
        parse_json_object_bytes(data)


@pytest.mark.parametrize(
    ("data", "expected_path"),
    [
        (b'{"a": 1, "a": 2}', "a"),
        (b'{"outer": {"inner": 1, "inner": 2}}', "outer.inner"),
        (b'{"items": [{"x": 1, "x": 2}]}', "items[0].x"),
    ],
)
def test_parse_rejects_duplicate_keys(data: bytes, expected_path: str) -> None:
    with pytest.raises(DomainValidationError) as exc_info:
        parse_json_object_bytes(data)
    assert exc_info.value.field_path == expected_path
    assert exc_info.value.message == "duplicate object key"
    # Errors must describe the path, not embed the conflicting values.
    assert str(exc_info.value) == f"{expected_path}: duplicate object key"


@pytest.mark.parametrize("token", [b"NaN", b"Infinity", b"-Infinity"])
def test_parse_rejects_nonfinite_constants(token: bytes) -> None:
    with pytest.raises(DomainValidationError) as exc_info:
        parse_json_object_bytes(b'{"value": ' + token + b"}")
    assert exc_info.value.message == (
        "non-finite JSON numbers (NaN/Infinity) are not allowed"
    )
    # The constant token itself is named in the rule text, but no JSON value
    # payload beyond that is embedded.
    assert "value:" not in str(exc_info.value)


def _valid_seq0_manifest_json() -> str:
    return """{
  "accepted_save": null,
  "accepted_save_hashes": [],
  "current_player_id": "player_a",
  "display_name": "Example Match",
  "game_id": "example-match",
  "last_sender_id": null,
  "players": [
    {"display_name": "Player A", "id": "player_a"},
    {"display_name": "Player B", "id": "player_b"}
  ],
  "previous_manifest_ref": null,
  "protocol": {"last_operation_id": null, "min_client_protocol": 1},
  "protocol_sequence": 0,
  "schema_version": 1
}
"""


def _valid_match_config_json() -> str:
    return """{
  "allow_force_close_after_commit": false,
  "display_name": "Example Match",
  "game_id": "example-match",
  "launch_profile": "default",
  "local_player_id": "player_a",
  "mod_name": "AdvCiv",
  "pbem_save_directory": "C:\\\\Placeholder\\\\Saves\\\\pbem",
  "players": [
    {"display_name": "Player A", "id": "player_a"},
    {"display_name": "Player B", "id": "player_b"}
  ],
  "save_matching": {"filename_glob": "*.CivBeyondSwordSave"},
  "turn_handling_mode": "standard"
}
"""


@pytest.mark.parametrize(
    ("document", "expected_path"),
    [
        (
            _valid_seq0_manifest_json().replace(
                '"current_player_id": "player_a"',
                '"current_player_id": "player_a", "current_player_id": "player_b"',
            ),
            "current_player_id",
        ),
        (
            _valid_seq0_manifest_json().replace(
                '"protocol_sequence": 0',
                '"protocol_sequence": 0, "protocol_sequence": 1',
            ),
            "protocol_sequence",
        ),
        (
            _valid_seq0_manifest_json().replace(
                '"last_operation_id": null',
                '"last_operation_id": null, "last_operation_id": "' + OP_ID + '"',
            ),
            "protocol.last_operation_id",
        ),
        (
            _valid_seq0_manifest_json().replace(
                '"min_client_protocol": 1',
                '"min_client_protocol": 1, "min_client_protocol": 2',
            ),
            "protocol.min_client_protocol",
        ),
    ],
)
def test_manifest_from_json_bytes_rejects_duplicate_keys(
    document: str, expected_path: str
) -> None:
    with pytest.raises(DomainValidationError) as exc_info:
        Manifest.from_json_bytes(document.encode("utf-8"))
    assert exc_info.value.field_path == expected_path


def test_manifest_from_json_bytes_rejects_nested_accepted_save_duplicate() -> None:
    # Start from a valid seq>0 document and inject a duplicate nested key.
    valid = {
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
    text = to_canonical_json_bytes(valid).decode("utf-8")
    duplicate_hash = "b" * 64
    document = text.replace(
        f'"sha256": "{HASH_1}"',
        f'"sha256": "{HASH_1}",\n    "sha256": "{duplicate_hash}"',
        1,
    )
    with pytest.raises(DomainValidationError) as exc_info:
        Manifest.from_json_bytes(document.encode("utf-8"))
    assert exc_info.value.field_path == "accepted_save.sha256"


@pytest.mark.parametrize(
    ("document", "expected_path"),
    [
        (
            _valid_match_config_json().replace(
                '"local_player_id": "player_a"',
                '"local_player_id": "player_a", "local_player_id": "player_b"',
            ),
            "local_player_id",
        ),
        (
            _valid_match_config_json().replace(
                '"filename_glob": "*.CivBeyondSwordSave"',
                '"filename_glob": "*.CivBeyondSwordSave", '
                '"filename_glob": "*.Civ4SavedGame"',
            ),
            "save_matching.filename_glob",
        ),
        (
            _valid_match_config_json().replace(
                '"game_id": "example-match"',
                '"game_id": "example-match", "game_id": "other-match"',
            ),
            "game_id",
        ),
    ],
)
def test_match_config_from_json_bytes_rejects_duplicate_keys(
    document: str, expected_path: str
) -> None:
    with pytest.raises(DomainValidationError) as exc_info:
        MatchConfig.from_json_bytes(document.encode("utf-8"))
    assert exc_info.value.field_path == expected_path


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity"])
def test_manifest_and_match_config_reject_nonfinite_constants(token: str) -> None:
    manifest_doc = _valid_seq0_manifest_json().replace(
        '"accepted_save": null', f'"accepted_save": {token}'
    )
    with pytest.raises(DomainValidationError) as exc_info:
        Manifest.from_json_bytes(manifest_doc.encode("utf-8"))
    assert "non-finite" in exc_info.value.message

    match_doc = _valid_match_config_json().replace(
        '"allow_force_close_after_commit": false',
        f'"allow_force_close_after_commit": {token}',
    )
    with pytest.raises(DomainValidationError) as exc_info:
        MatchConfig.from_json_bytes(match_doc.encode("utf-8"))
    assert "non-finite" in exc_info.value.message
