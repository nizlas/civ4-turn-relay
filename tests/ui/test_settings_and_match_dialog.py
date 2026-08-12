"""Settings redaction and match-dialog validation tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget
from pytestqt.qtbot import QtBot

from civ4_turn_relay.domain import GlobalConfig, TurnHandlingMode
from civ4_turn_relay.ui.match_dialog import MatchEditDialog
from civ4_turn_relay.ui.settings_dialog import GlobalSettingsDialog

SENTINEL_PASSWORD = "SENTINEL-SECRET"
SENTINEL_KEY_PATH = r"C:\Secret\sentinel_id_ed25519"
FINGERPRINT = "SHA256:AAAA"


def _config_with_secrets() -> GlobalConfig:
    return GlobalConfig(
        sftp_host="sftp.example.test",
        sftp_port=22,
        sftp_username="relay-user",
        sftp_remote_root="/relay/games",
        sftp_password=SENTINEL_PASSWORD,
        sftp_private_key_path=SENTINEL_KEY_PATH,
        sftp_host_key_sha256=FINGERPRINT,
    )


def test_settings_dialog_never_renders_secrets(qtbot: QtBot) -> None:
    dialog = GlobalSettingsDialog(config=_config_with_secrets())
    qtbot.addWidget(dialog)
    rendered = dialog.rendered_text()
    assert SENTINEL_PASSWORD not in rendered
    assert SENTINEL_KEY_PATH not in rendered
    assert "sentinel_id_ed25519" not in rendered
    assert "[REDACTED]" in rendered
    # Non-secret values are shown as-is.
    assert "sftp.example.test" in rendered
    assert "relay-user" in rendered


def test_settings_dialog_shows_trust_mechanism(qtbot: QtBot) -> None:
    pinned = GlobalSettingsDialog(config=_config_with_secrets())
    qtbot.addWidget(pinned)
    assert "pinned host key fingerprint" in pinned.trust_label.text()
    # The fingerprint itself is public and may be displayed.
    assert FINGERPRINT in pinned.trust_label.text()

    known_hosts_config = GlobalConfig(
        sftp_host="sftp.example.test",
        sftp_port=22,
        sftp_username="relay-user",
        sftp_remote_root="/relay/games",
        sftp_password=SENTINEL_PASSWORD,
        sftp_known_hosts_path=r"C:\Placeholder\known_hosts",
    )
    with_hosts = GlobalSettingsDialog(config=known_hosts_config)
    qtbot.addWidget(with_hosts)
    assert "known_hosts file configured" in with_hosts.trust_label.text()


def test_settings_dialog_reports_load_error(qtbot: QtBot) -> None:
    dialog = GlobalSettingsDialog(config=None, error_text="missing SFTP host")
    qtbot.addWidget(dialog)
    assert "missing SFTP host" in dialog.status_label.text()
    assert dialog.open_env_button.isEnabled() is False
    assert dialog.create_env_button.isEnabled() is False


def _patch_warnings(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    warnings: list[str] = []

    def fake_warning(
        parent: QWidget | None, title: str, text: str, *args: object
    ) -> QMessageBox.StandardButton:
        del parent, title, args
        warnings.append(text)
        return QMessageBox.StandardButton.Ok

    monkeypatch.setattr(QMessageBox, "warning", staticmethod(fake_warning))
    return warnings


def _fill_valid(dialog: MatchEditDialog, tmp_path: Path, game_id: str) -> None:
    dialog.game_id_edit.setText(game_id)
    dialog.display_name_edit.setText("Test Match")
    dialog.players_edit.setPlainText("player_a:Alice\nplayer_b:Bob")
    dialog.pbem_dir_edit.setText(str(tmp_path))


def test_match_dialog_stays_open_on_invalid_input(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del tmp_path
    warnings = _patch_warnings(monkeypatch)
    dialog = MatchEditDialog()
    qtbot.addWidget(dialog)
    dialog.game_id_edit.setText("test-match")
    dialog.display_name_edit.setText("Test Match")
    dialog.players_edit.setPlainText("player_a:Alice")
    dialog.pbem_dir_edit.setText("")  # invalid: empty PBEM directory

    dialog.accept()

    assert len(warnings) == 1
    assert dialog.result_config() is None


def test_match_dialog_valid_input_builds_config(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    warnings = _patch_warnings(monkeypatch)
    dialog = MatchEditDialog()
    qtbot.addWidget(dialog)
    _fill_valid(dialog, tmp_path, "test-match")
    dialog.managed_radio.setChecked(True)
    assert dialog.force_close_checkbox.isEnabled()
    dialog.force_close_checkbox.setChecked(True)

    dialog.accept()

    assert warnings == []
    config = dialog.result_config()
    assert config is not None
    assert config.game_id == "test-match"
    assert config.turn_handling_mode is TurnHandlingMode.FULLY_MANAGED
    assert config.allow_force_close_after_commit is True
    assert tuple(player.id for player in config.players) == (
        "player_a",
        "player_b",
    )
    assert config.local_player_id == "player_a"
    assert config.save_matching.filename_glob == "*.CivBeyondSwordSave"
    assert config.mod_name == "Mods\\AdvCiv"


def test_force_close_checkbox_disabled_under_standard(qtbot: QtBot) -> None:
    dialog = MatchEditDialog()
    qtbot.addWidget(dialog)
    assert dialog.standard_radio.isChecked()
    assert dialog.force_close_checkbox.isEnabled() is False

    dialog.managed_radio.setChecked(True)
    assert dialog.force_close_checkbox.isEnabled() is True
    dialog.force_close_checkbox.setChecked(True)

    dialog.standard_radio.setChecked(True)
    assert dialog.force_close_checkbox.isEnabled() is False
    assert dialog.force_close_checkbox.isChecked() is False


def test_match_dialog_refuses_duplicate_game_id(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    warnings = _patch_warnings(monkeypatch)
    dialog = MatchEditDialog(existing_game_ids=("test-match",))
    qtbot.addWidget(dialog)
    _fill_valid(dialog, tmp_path, "test-match")

    dialog.accept()

    assert len(warnings) == 1
    assert "already exists" in warnings[0]
    assert dialog.result_config() is None


def test_match_dialog_editing_locks_game_id_and_prefills(
    qtbot: QtBot, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    warnings = _patch_warnings(monkeypatch)
    create = MatchEditDialog()
    qtbot.addWidget(create)
    _fill_valid(create, tmp_path, "test-match")
    create.accept()
    existing = create.result_config()
    assert existing is not None

    dialog = MatchEditDialog(existing=existing)
    qtbot.addWidget(dialog)
    assert dialog.game_id_edit.isEnabled() is False
    assert dialog.game_id_edit.text() == "test-match"
    assert dialog.display_name_edit.text() == "Test Match"
    assert dialog.local_player_combo.currentText() == "player_a"

    dialog.display_name_edit.setText("Renamed Match")
    dialog.accept()
    assert warnings == []
    updated = dialog.result_config()
    assert updated is not None
    assert updated.display_name == "Renamed Match"
    assert updated.game_id == "test-match"


def test_first_turn_helper_names_first_player(qtbot: QtBot, qapp: QApplication) -> None:
    del qapp
    dialog = MatchEditDialog()
    qtbot.addWidget(dialog)
    dialog.players_edit.setPlainText("player_b:Bob\nplayer_a:Alice")
    text = dialog.first_turn_label.text()
    assert text.startswith("First turn: Bob")
    assert "Civilization itself creates the PBEM game" in text
    assert "Relay cannot generate a Civ save" in text
