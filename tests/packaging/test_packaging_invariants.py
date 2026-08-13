"""Static checks for P9 packaging scaffolding (no Civ / SFTP / Inno required)."""

from __future__ import annotations

import sys
import tomllib
from pathlib import Path

import pytest

from civ4_turn_relay.ui.app import (
    _dotenv_for_loading,
    _find_env_example,
    user_data_dir,
)

ROOT = Path(__file__).resolve().parents[2]
PACKAGING = ROOT / "packaging"


def test_pyproject_gui_entry_and_version() -> None:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert data["project"]["version"]
    assert data["project"]["gui-scripts"]["civ4-turn-relay-ui"] == (
        "civ4_turn_relay.ui.app:main"
    )
    deps = " ".join(data["project"]["dependencies"]).lower()
    for required in ("pyside6", "psutil", "paramiko", "python-dotenv", "watchdog"):
        assert required in deps


def test_user_data_dir_is_appdata_civ4_turn_relay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("APPDATA", r"C:\Users\example\AppData\Roaming")
    path = user_data_dir()
    assert path == Path(r"C:\Users\example\AppData\Roaming") / "civ4-turn-relay"


def test_user_data_dir_falls_back_to_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.setattr(
        "civ4_turn_relay.ui.app.Path.home",
        lambda: tmp_path,
    )
    assert user_data_dir() == tmp_path / "civ4-turn-relay"


def test_packaging_scripts_exist() -> None:
    assert (PACKAGING / "build_windows.ps1").is_file()
    assert (PACKAGING / "build_installer.ps1").is_file()
    assert (PACKAGING / "installer.iss").is_file()
    assert (PACKAGING / "civ4-turn-relay.spec").is_file()
    assert (PACKAGING / "tools" / "run_ui_teardown_stress.ps1").is_file()
    assert (ROOT / "docs" / "RELEASE.md").is_file()


def test_installer_is_per_user_and_preserves_appdata() -> None:
    text = (PACKAGING / "installer.iss").read_text(encoding="utf-8")
    assert "PrivilegesRequired=lowest" in text
    assert "PrivilegesRequiredOverridesAllowed=" not in text
    assert r"{localappdata}\Programs\civ4-turn-relay" in text
    assert "{userdesktop}" in text
    assert "NEVER" in text and "civ4-turn-relay" in text
    uninstall_body = text.split("[UninstallDelete]", 1)[1].split("[", 1)[0]
    assert "userappdata" not in uninstall_body.lower()
    assert "%APPDATA%" not in uninstall_body
    assert "SignTool" not in text


def test_installer_vc_redist_is_optional_external() -> None:
    text = (PACKAGING / "installer.iss").read_text(encoding="utf-8")
    assert "vc_redist.x64.exe" in text
    assert "FileExists" in text
    assert not (PACKAGING / "prereq" / "vc_redist.x64.exe").exists()


def test_build_script_refuses_secrets_and_uses_project_version() -> None:
    text = (PACKAGING / "build_windows.ps1").read_text(encoding="utf-8")
    assert "Get-ProjectVersion" in text
    assert "pyproject.toml" in text
    for token in (".env", "known_hosts", "id_rsa", "CivBeyondSwordSave"):
        assert token in text
    # Must not invent a Civ shell command line.
    assert "cmd.exe" not in text.lower()
    assert "/c civ" not in text.lower()
    assert "mod=Mods" not in text  # launch argv belongs in process code, not packaging
    assert "if ($proc.HasExited)" in text
    assert "$proc.HasExited -and" not in text
    assert '[string]$RepoRoot = ""' in text


def test_installer_build_wrapper_reads_version_and_requires_portable() -> None:
    text = (PACKAGING / "build_installer.ps1").read_text(encoding="utf-8")
    assert "Get-ProjectVersion" in text
    assert "pyproject.toml" in text
    assert "MyAppVersion" in text
    assert "ISCC" in text
    assert "build_windows.ps1" in text
    assert "Portable build output not found" in text or "portable" in text.lower()
    assert '[string]$RepoRoot = ""' in text
    assert "LOCALAPPDATA" in text


def test_teardown_stress_script_uses_reporoot() -> None:
    text = (PACKAGING / "tools" / "run_ui_teardown_stress.ps1").read_text(
        encoding="utf-8"
    )
    assert "Push-Location" in text
    assert "Pop-Location" in text
    assert "pyproject.toml" in text
    assert "RepoRoot" in text


def test_pyinstaller_spec_excludes_tests_and_ships_env_example_only() -> None:
    text = (PACKAGING / "civ4-turn-relay.spec").read_text(encoding="utf-8")
    assert "excludes=[" in text
    assert '"tests"' in text or "'tests'" in text
    assert ".env.example" in text
    assert '".env"' not in text.split(".env.example")[0] or "never" in text.lower()
    assert "collect_all" in text
    assert "PySide6" in text and "paramiko" in text
    app_text = (ROOT / "src" / "civ4_turn_relay" / "ui" / "app.py").read_text(
        encoding="utf-8"
    )
    assert 'if __name__ == "__main__":' in app_text


def test_frozen_app_finds_packaged_env_example(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    working = tmp_path / "working"
    bundle = tmp_path / "bundle"
    working.mkdir()
    bundle.mkdir()
    example = bundle / ".env.example"
    example.write_text("CIV4_RELAY_SFTP_HOST=placeholder\n", encoding="utf-8")
    monkeypatch.chdir(working)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)

    assert _find_env_example() == example


def test_missing_first_run_dotenv_is_not_passed_to_strict_loader(
    tmp_path: Path,
) -> None:
    target = tmp_path / ".env"
    assert _dotenv_for_loading(target) is None
    target.write_text("CIV4_RELAY_SFTP_HOST=placeholder\n", encoding="utf-8")
    assert _dotenv_for_loading(target) == target


def test_gitignore_keeps_scripts_ignores_artifacts_and_vcredist() -> None:
    text = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "dist/" in text
    assert "build/" in text
    assert "vc_redist.x64.exe" in text
    assert "!packaging/*.spec" in text


def test_release_docs_mention_manual_p7_and_unsigned_builds() -> None:
    text = (ROOT / "docs" / "RELEASE.md").read_text(encoding="utf-8")
    assert "DESKTOP_CLIENT.md" in text
    assert "unsigned" in text.lower()
    assert "%APPDATA%\\civ4-turn-relay" in text or "%APPDATA%" in text
    assert "run_ui_teardown_stress.ps1" in text
    assert "build_installer.ps1" in text
    assert "PrivilegesRequiredOverridesAllowed" not in text
    assert "P7" in text and "ACTIVE" in text
    assert "0.1.0" not in text.split("## Installer build", 1)[1].split("## ", 1)[0]
