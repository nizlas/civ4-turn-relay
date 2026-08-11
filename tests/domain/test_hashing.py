"""Tests for pure hashing helpers (synthetic in-memory bytes only)."""

from civ4_turn_relay.domain import sha256_hex, validate_sha256_hex

SHA256_OF_EMPTY = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
SHA256_OF_ABC = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_known_digests() -> None:
    assert sha256_hex(b"") == SHA256_OF_EMPTY
    assert sha256_hex(b"abc") == SHA256_OF_ABC


def test_output_is_valid_lowercase_hex() -> None:
    digest = sha256_hex(b"synthetic save bytes \x00\x01\x02")
    assert validate_sha256_hex(digest) == digest


def test_different_bytes_different_digest() -> None:
    assert sha256_hex(b"synthetic-1") != sha256_hex(b"synthetic-2")
