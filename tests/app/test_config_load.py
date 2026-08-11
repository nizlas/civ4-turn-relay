"""Tests for explicit dotenv / environ global config loading."""

from __future__ import annotations

from pathlib import Path

from civ4_turn_relay.app import load_global_config
from civ4_turn_relay.domain import REDACTED

FAKE_PASSWORD = "placeholder-dotenv-password"
FAKE_HOST_KEY = "SHA256:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"


def test_environ_overrides_dotenv(tmp_path: Path) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "\n".join(
            [
                "CIV4_RELAY_SFTP_HOST=from-dotenv.example.invalid",
                "CIV4_RELAY_SFTP_PORT=22",
                "CIV4_RELAY_SFTP_USERNAME=dotenv-user",
                "CIV4_RELAY_SFTP_REMOTE_ROOT=/dotenv/games",
                f"CIV4_RELAY_SFTP_PASSWORD={FAKE_PASSWORD}",
                f"CIV4_RELAY_SFTP_HOST_KEY_SHA256={FAKE_HOST_KEY}",
            ]
        ),
        encoding="utf-8",
    )
    config = load_global_config(
        dotenv_path=dotenv,
        environ={
            "CIV4_RELAY_SFTP_HOST": "from-environ.example.invalid",
            "CIV4_RELAY_SFTP_PORT": "2222",
            "CIV4_RELAY_SFTP_USERNAME": "env-user",
            "CIV4_RELAY_SFTP_REMOTE_ROOT": "/env/games",
            "CIV4_RELAY_SFTP_PASSWORD": "env-password-placeholder",
            "CIV4_RELAY_SFTP_HOST_KEY_SHA256": FAKE_HOST_KEY,
        },
    )
    assert config.sftp_host == "from-environ.example.invalid"
    assert config.sftp_port == 2222
    assert FAKE_PASSWORD not in repr(config)
    assert config.to_redacted_mapping()["sftp_password"] == REDACTED
