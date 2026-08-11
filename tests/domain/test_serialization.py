"""Tests for deterministic JSON helpers and strict document parsing."""

import pytest

from civ4_turn_relay.domain import (
    DomainValidationError,
    parse_json_object_bytes,
    to_canonical_json_bytes,
)


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
