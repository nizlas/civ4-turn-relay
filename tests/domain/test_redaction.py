"""Tests for secret-redaction helpers (FR-012 foundation).

All secret values are synthetic placeholders.
"""

import copy

import pytest

from civ4_turn_relay.domain import (
    REDACTED,
    is_sensitive_field_name,
    redact_known_secrets,
    redact_structure,
)


@pytest.mark.parametrize(
    "name",
    [
        "password",
        "PASSWORD",
        "sftp_password",
        "CIV4_RELAY_SFTP_PASSWORD",
        "passwd",
        "passphrase",
        "secret",
        "client_secret",
        "token",
        "api_token",
        "access-token",
        "private_key",
        "private-key",
        "private key",
        "privateKey",
        "sftp_private_key_path",
        "api_key",
        "apiKey",
        "credential",
        "credentials",
    ],
)
def test_sensitive_field_names(name: str) -> None:
    assert is_sensitive_field_name(name)


@pytest.mark.parametrize(
    "name",
    ["host", "username", "port", "log_level", "game_id", "display_name"],
)
def test_non_sensitive_field_names(name: str) -> None:
    assert not is_sensitive_field_name(name)


def test_redact_nested_structure() -> None:
    original = {
        "sftp": {
            "host": "sftp.example.invalid",
            "password": "placeholder-pass",
            "private-key": "placeholder-key-bytes",
        },
        "matches": [
            {"game_id": "example-match", "api_token": "placeholder-token"},
            ("tuple-item", {"secret": "placeholder"}),
        ],
        "log_level": "INFO",
    }
    snapshot = copy.deepcopy(original)
    redacted = redact_structure(original)
    assert original == snapshot  # caller's input is never mutated
    assert isinstance(redacted, dict)
    sftp = redacted["sftp"]
    assert isinstance(sftp, dict)
    assert sftp["password"] == REDACTED
    assert sftp["private-key"] == REDACTED
    assert sftp["host"] == "sftp.example.invalid"
    matches = redacted["matches"]
    assert isinstance(matches, list)
    first = matches[0]
    assert isinstance(first, dict)
    assert first["api_token"] == REDACTED
    assert first["game_id"] == "example-match"
    second = matches[1]
    assert isinstance(second, tuple)  # sequence types are preserved
    assert second[1] == {"secret": REDACTED}
    assert redacted["log_level"] == "INFO"


def test_redact_whole_subtree_under_sensitive_key() -> None:
    redacted = redact_structure({"credentials": {"user": "u", "pass": "p"}})
    assert redacted == {"credentials": REDACTED}


def test_redact_scalars_pass_through() -> None:
    assert redact_structure("plain") == "plain"
    assert redact_structure(7) == 7
    assert redact_structure(None) is None


def test_redact_known_secrets_in_text() -> None:
    text = "auth failed for placeholder-pass at host (key=placeholder-pass-long)"
    result = redact_known_secrets(text, ["placeholder-pass", "placeholder-pass-long"])
    assert "placeholder-pass" not in result
    assert result.count(REDACTED) == 2


def test_redact_known_secrets_ignores_empty_values() -> None:
    assert redact_known_secrets("nothing to hide", ["", ""]) == "nothing to hide"


def test_redact_known_secrets_no_match() -> None:
    assert redact_known_secrets("clean text", ["placeholder"]) == "clean text"
