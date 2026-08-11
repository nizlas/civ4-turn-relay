"""Table-driven tests for identifier, digest, and timestamp validators."""

import pytest

from civ4_turn_relay.domain import (
    DomainValidationError,
    validate_game_id,
    validate_operation_id,
    validate_player_id,
    validate_sha256_hex,
    validate_utc_timestamp,
)

VALID_SHA256 = "a" * 63 + "b"


@pytest.mark.parametrize(
    "value",
    [
        "abc",
        "advciv-test",
        "pbem-match-01",
        "a" + "b" * 62 + "c",  # maximum length 64
        "a-1",
        "game2",
    ],
)
def test_valid_game_ids(value: str) -> None:
    assert validate_game_id(value) == value
    assert 3 <= len(value) <= 64


@pytest.mark.parametrize(
    "value",
    [
        "",
        "ab",  # too short (length 2)
        "a" * 65,  # too long (length 65)
        "Abc",  # uppercase start
        "aBc",  # uppercase inside
        "ABC",
        "1abc",  # must start with a letter
        "-abc",
        "abc-",  # must end with alphanumeric
        "a.bc",  # dots
        "..",
        "../abc",  # traversal
        "a/../b",
        "a/b",  # separator
        "a\\b",  # backslash separator
        "a b",  # space
        "a_b_c",  # underscore not allowed in game IDs
    ],
)
def test_invalid_game_ids(value: str) -> None:
    with pytest.raises(DomainValidationError):
        validate_game_id(value)


@pytest.mark.parametrize(
    "value",
    [
        "a",  # minimum length 1
        "player_a",
        "player-b",
        "p123",
        "a" + "b" * 31,  # maximum length 32
    ],
)
def test_valid_player_ids(value: str) -> None:
    assert validate_player_id(value) == value


@pytest.mark.parametrize(
    "value",
    [
        "",
        "A",  # uppercase
        "Player_a",
        "1player",  # leading digit
        "_player",  # leading underscore
        "-player",  # leading hyphen
        "a" + "b" * 32,  # length 33
        "player a",  # space
        "player/a",  # separator
        "pl.ayer",  # dot
    ],
)
def test_invalid_player_ids(value: str) -> None:
    with pytest.raises(DomainValidationError):
        validate_player_id(value)


def test_valid_sha256() -> None:
    assert validate_sha256_hex(VALID_SHA256) == VALID_SHA256
    assert validate_sha256_hex("0123456789abcdef" * 4) == "0123456789abcdef" * 4


@pytest.mark.parametrize(
    "value",
    [
        "",
        "a" * 63,  # too short
        "a" * 65,  # too long
        "A" * 64,  # uppercase hex
        "g" * 64,  # not hex
        "a" * 32 + "Z" + "a" * 31,
    ],
)
def test_invalid_sha256(value: str) -> None:
    with pytest.raises(DomainValidationError):
        validate_sha256_hex(value)


def test_valid_operation_id() -> None:
    value = "11111111-2222-3333-4444-555555555555"
    assert validate_operation_id(value) == value


@pytest.mark.parametrize(
    "value",
    [
        "",
        "11111111222233334444555555555555",  # missing hyphens
        "11111111-2222-3333-4444-55555555555",  # too short
        "11111111-2222-3333-4444-5555555555556",  # too long
        "11111111-2222-3333-4444-55555555555G",  # non-hex
        "11111111-2222-3333-4444-55555555555Z",
        "AAAAAAAA-2222-3333-4444-555555555555",  # uppercase rejected
        "{11111111-2222-3333-4444-555555555555}",  # braces
        "1111-11112222-3333-4444-555555555555",  # misplaced hyphens
    ],
)
def test_invalid_operation_id(value: str) -> None:
    with pytest.raises(DomainValidationError):
        validate_operation_id(value)


@pytest.mark.parametrize(
    "value",
    ["2026-08-10T19:43:00Z", "2026-01-01T00:00:00Z", "2026-12-31T23:59:59Z"],
)
def test_valid_utc_timestamps(value: str) -> None:
    assert validate_utc_timestamp(value) == value


@pytest.mark.parametrize(
    "value",
    [
        "",
        "2026-08-10T19:43:00",  # missing Z
        "2026-08-10 19:43:00Z",  # space separator
        "2026-08-10T19:43:00.000Z",  # sub-second resolution
        "2026-08-10T19:43:00+00:00",  # offset instead of Z
        "2026-8-10T19:43:00Z",  # non-zero-padded month
        "2026-13-01T00:00:00Z",  # invalid month
        "2026-02-30T00:00:00Z",  # invalid day
        "2026-01-01T24:00:00Z",  # invalid hour
        "2026-01-01T00:60:00Z",  # invalid minute
        "2026-01-01T00:00:60Z",  # leap second not allowed
        "2026-01-01t00:00:00Z",  # lowercase separator
        "2026-01-01T00:00:00z",  # lowercase zone
    ],
)
def test_invalid_utc_timestamps(value: str) -> None:
    with pytest.raises(DomainValidationError):
        validate_utc_timestamp(value)


def test_error_carries_field_path() -> None:
    with pytest.raises(DomainValidationError) as exc_info:
        validate_game_id("../escape", field_path="game_id")
    assert exc_info.value.field_path == "game_id"
    assert str(exc_info.value).startswith("game_id: ")
