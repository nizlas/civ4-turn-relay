"""Civ launch command construction and launch planning."""

from __future__ import annotations

from pathlib import Path

import pytest

from civ4_turn_relay.domain import DomainValidationError
from civ4_turn_relay.process import (
    CivLaunchCommand,
    CivLaunchConfiguration,
    LaunchPlan,
    LaunchPlanOutcome,
    build_civ_command,
    build_launch_plan,
)

_EXE = "C:\\Games\\Firaxis\\Beyond the Sword\\Civ4BeyondSword.exe"
_SAVE = "C:\\Saves\\PBEM\\turn-042.CivBeyondSwordSave"
_MOD = "Mods\\AdvCiv"
_STEAM = "C:\\Program Files (x86)\\Steam\\steam.exe"


def test_command_executable_only() -> None:
    command = build_civ_command(CivLaunchConfiguration(executable_path=_EXE))
    assert command.argv == (_EXE,)
    assert command.working_directory is None


def test_command_with_mod_token_is_exactly_one_argument() -> None:
    command = build_civ_command(
        CivLaunchConfiguration(executable_path=_EXE, mod_name=_MOD)
    )
    assert command.argv == (_EXE, "mod=\\AdvCiv")
    assert sum(arg.startswith("mod=") for arg in command.argv) == 1


def test_command_with_mod_and_save_direct_load() -> None:
    command = build_civ_command(
        CivLaunchConfiguration(executable_path=_EXE, mod_name=_MOD, save_path=_SAVE)
    )
    assert command.argv == (_EXE, f"/fxsload={_SAVE}", "mod=\\AdvCiv")


def test_steam_context_is_carried_without_touching_the_argv() -> None:
    command = build_civ_command(
        CivLaunchConfiguration(
            executable_path=_EXE,
            mod_name=_MOD,
            save_path=_SAVE,
            steam_app_id="8800",
            steam_executable_path=_STEAM,
        )
    )
    assert command.argv == (_EXE, f"/fxsload={_SAVE}", "mod=\\AdvCiv")
    assert command.environment == (("SteamAppId", "8800"), ("SteamGameId", "8800"))
    assert command.steam_executable_path == _STEAM


@pytest.mark.parametrize("app_id", ["", "0", "-1", "8800 ", "steam"])
def test_steam_context_rejects_invalid_app_ids(app_id: str) -> None:
    with pytest.raises(DomainValidationError):
        CivLaunchConfiguration(
            executable_path=_EXE,
            steam_app_id=app_id,
            steam_executable_path=_STEAM,
        )


def test_steam_app_id_requires_a_steam_executable() -> None:
    with pytest.raises(DomainValidationError):
        CivLaunchConfiguration(executable_path=_EXE, steam_app_id="8800")


def test_steam_executable_requires_an_app_id() -> None:
    with pytest.raises(DomainValidationError):
        CivLaunchConfiguration(executable_path=_EXE, steam_executable_path=_STEAM)


def test_omitted_mod_produces_no_mod_argument() -> None:
    command = build_civ_command(
        CivLaunchConfiguration(executable_path=_EXE, mod_name=None, save_path=_SAVE)
    )
    assert command.argv == (_EXE, f"/fxsload={_SAVE}")
    assert not any(arg.startswith("mod=") for arg in command.argv)


def test_sequence_zero_shape_is_mod_without_save() -> None:
    command = build_civ_command(
        CivLaunchConfiguration(executable_path=_EXE, mod_name=_MOD, save_path=None)
    )
    assert command.argv == (_EXE, "mod=\\AdvCiv")
    assert not any(arg.startswith("/fxsload=") for arg in command.argv)


def test_direct_save_precedes_mod_for_civ_legacy_parser() -> None:
    command = build_civ_command(
        CivLaunchConfiguration(executable_path=_EXE, mod_name=_MOD, save_path=_SAVE)
    )
    assert command.argv.index(f"/fxsload={_SAVE}") < command.argv.index("mod=\\AdvCiv")


def test_command_is_argument_tuple_never_a_shell_string() -> None:
    command = build_civ_command(
        CivLaunchConfiguration(executable_path=_EXE, mod_name=_MOD, save_path=_SAVE)
    )
    assert isinstance(command.argv, tuple)
    assert all(isinstance(arg, str) for arg in command.argv)
    assert len(command.argv) == 3


def test_command_carries_working_directory() -> None:
    directory = "C:\\Games\\Firaxis\\Beyond the Sword"
    command = build_civ_command(
        CivLaunchConfiguration(executable_path=_EXE, working_directory=directory)
    )
    assert command.working_directory == directory


def test_dry_run_preview_quotes_paths_with_spaces() -> None:
    command = build_civ_command(
        CivLaunchConfiguration(executable_path=_EXE, mod_name=_MOD)
    )
    assert command.dry_run_preview() == f'"{_EXE}" mod=\\AdvCiv'


def test_dry_run_preview_leaves_spaceless_arguments_unquoted() -> None:
    """The executable is always quoted (Civ IV's verified legacy form);
    spaceless non-executable arguments stay unquoted."""
    command = CivLaunchCommand(
        argv=("C:\\Civ4\\civ4.exe", "mod=\\AdvCiv"), working_directory=None
    )
    assert command.dry_run_preview() == '"C:\\Civ4\\civ4.exe" mod=\\AdvCiv'


@pytest.mark.parametrize(
    "mod_name",
    [
        "",
        "Mods/AdvCiv",
        'Mods\\Adv"Civ',
        "Mods\\Adv'Civ",
        "Mods\\Adv\x00Civ",
        "Mods\\Adv\x7fCiv",
        "C:\\Mods\\AdvCiv",
        "\\Mods\\AdvCiv",
        "\\\\server\\Mods\\AdvCiv",
        "Mods\\..\\AdvCiv",
        "..\\AdvCiv",
        "Mods\\.\\AdvCiv",
        "Mods\\\\AdvCiv",
        "Mods\\AdvCiv\\",
        " Mods\\AdvCiv",
        "Mods\\AdvCiv ",
        "Mods\\ AdvCiv",
        "Mods\\AdvCiv \\Sub",
    ],
)
def test_mod_token_rejects_malformed_values(mod_name: str) -> None:
    with pytest.raises(DomainValidationError):
        CivLaunchConfiguration(executable_path=_EXE, mod_name=mod_name)


@pytest.mark.parametrize(
    "mod_name",
    ["AdvCiv", "Mods\\AdvCiv", "Mods\\Advanced Civ", "Mods\\Sub\\AdvCiv"],
)
def test_mod_token_translates_relative_folder_to_civ_cli(mod_name: str) -> None:
    command = build_civ_command(
        CivLaunchConfiguration(executable_path=_EXE, mod_name=mod_name)
    )
    relative = mod_name.removeprefix("Mods\\")
    assert command.argv == (_EXE, f"mod=\\{relative}")


def test_bare_mods_root_is_rejected_when_building_command() -> None:
    with pytest.raises(DomainValidationError, match="below Mods"):
        CivLaunchConfiguration(executable_path=_EXE, mod_name="Mods")


def test_launch_plan_invariant_requires_command_only_when_ready() -> None:
    command = build_civ_command(CivLaunchConfiguration(executable_path=_EXE))
    with pytest.raises(DomainValidationError):
        LaunchPlan(outcome=LaunchPlanOutcome.READY, command=None)
    with pytest.raises(DomainValidationError):
        LaunchPlan(outcome=LaunchPlanOutcome.SAVE_NOT_FOUND, command=command)


@pytest.mark.parametrize("executable_path", [None, ""])
def test_plan_executable_not_configured(executable_path: str | None) -> None:
    plan = build_launch_plan(
        executable_path=executable_path,
        mod_name=_MOD,
        save_path=None,
        pbem_save_directory="C:\\Saves\\PBEM",
        is_file=lambda _path: True,
    )
    assert plan.outcome is LaunchPlanOutcome.EXECUTABLE_NOT_CONFIGURED
    assert plan.command is None
    assert plan.reason


def test_plan_executable_not_found() -> None:
    plan = build_launch_plan(
        executable_path=_EXE,
        mod_name=None,
        save_path=None,
        pbem_save_directory="C:\\Saves\\PBEM",
        is_file=lambda _path: False,
    )
    assert plan.outcome is LaunchPlanOutcome.EXECUTABLE_NOT_FOUND
    assert plan.reason


def test_plan_requires_existing_steam_executable_when_steam_is_configured() -> None:
    plan = build_launch_plan(
        executable_path=_EXE,
        mod_name=None,
        save_path=None,
        pbem_save_directory="C:\\Saves\\PBEM",
        steam_app_id="8800",
        steam_executable_path=_STEAM,
        is_file=lambda path: path == _EXE,
    )
    assert plan.outcome is LaunchPlanOutcome.STEAM_EXECUTABLE_NOT_FOUND


def test_plan_save_not_found() -> None:
    plan = build_launch_plan(
        executable_path=_EXE,
        mod_name=None,
        save_path=_SAVE,
        pbem_save_directory="C:\\Saves\\PBEM",
        is_file=lambda path: path == _EXE,
    )
    assert plan.outcome is LaunchPlanOutcome.SAVE_NOT_FOUND


def test_plan_save_outside_pbem_directory_by_traversal(tmp_path: Path) -> None:
    pbem_directory = tmp_path / "pbem"
    escaping_save = tmp_path / "pbem" / ".." / "outside.CivBeyondSwordSave"
    plan = build_launch_plan(
        executable_path=str(tmp_path / "civ" / "Civ4BeyondSword.exe"),
        mod_name=None,
        save_path=str(escaping_save),
        pbem_save_directory=str(pbem_directory),
        is_file=lambda _path: True,
    )
    assert plan.outcome is LaunchPlanOutcome.SAVE_OUTSIDE_PBEM_DIRECTORY


def test_plan_save_outside_pbem_directory_absolute_elsewhere(tmp_path: Path) -> None:
    plan = build_launch_plan(
        executable_path=str(tmp_path / "civ" / "Civ4BeyondSword.exe"),
        mod_name=None,
        save_path=str(tmp_path / "elsewhere" / "turn.CivBeyondSwordSave"),
        pbem_save_directory=str(tmp_path / "pbem"),
        is_file=lambda _path: True,
    )
    assert plan.outcome is LaunchPlanOutcome.SAVE_OUTSIDE_PBEM_DIRECTORY


def test_plan_ready_builds_command_and_working_directory(tmp_path: Path) -> None:
    executable = tmp_path / "civ" / "Civ4BeyondSword.exe"
    save = tmp_path / "pbem" / "turn-042.CivBeyondSwordSave"
    plan = build_launch_plan(
        executable_path=str(executable),
        mod_name=_MOD,
        save_path=str(save),
        pbem_save_directory=str(tmp_path / "pbem"),
        is_file=lambda _path: True,
    )
    assert plan.outcome is LaunchPlanOutcome.READY
    assert plan.command is not None
    assert plan.command.argv == (
        str(executable),
        f"/fxsload={save}",
        "mod=\\AdvCiv",
    )
    assert plan.command.working_directory == str(tmp_path / "civ")


def test_plan_ready_without_save_skips_directory_checks() -> None:
    plan = build_launch_plan(
        executable_path=_EXE,
        mod_name=_MOD,
        save_path=None,
        pbem_save_directory="C:\\Saves\\PBEM",
        is_file=lambda path: path == _EXE,
    )
    assert plan.outcome is LaunchPlanOutcome.READY
    assert plan.command is not None
    assert plan.command.argv == (_EXE, "mod=\\AdvCiv")


def test_plan_invalid_configuration_reports_validation_message(
    tmp_path: Path,
) -> None:
    plan = build_launch_plan(
        executable_path=str(tmp_path / "civ" / "Civ4BeyondSword.exe"),
        mod_name="Mods\\..\\AdvCiv",
        save_path=None,
        pbem_save_directory=str(tmp_path / "pbem"),
        is_file=lambda _path: True,
    )
    assert plan.outcome is LaunchPlanOutcome.INVALID_CONFIGURATION
    assert "mod_name" in plan.reason
