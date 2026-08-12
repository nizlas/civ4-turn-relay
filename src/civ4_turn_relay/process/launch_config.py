"""Civilization IV launch command construction and launch planning.

Exact documented command shape (never any other flags, never a shell):

- ``argv[0]`` is the configured executable path.
- If a mod is configured, one single argument ``mod=<mod token>`` is appended,
  where the configured value is the exact Civ-relative mod folder token (for
  example ``Mods\\AdvCiv``). No space is inserted after ``mod=`` and the token
  is passed through verbatim after validation. Omitting the mod value omits
  the argument entirely, deliberately deferring to the Civilization INI
  configuration.
- If a save is configured, one single argument ``/fxsload=<absolute save
  path>`` is appended (direct-save-load mechanism modeled explicitly; exact
  flag behavior is empirically confirmed via the documented manual smoke
  test).

Planning here is pure aside from the injectable ``is_file`` probe and path
resolution; nothing in this module launches a process.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum, unique
from pathlib import Path, PurePosixPath, PureWindowsPath

from civ4_turn_relay.domain import DomainValidationError, validate_windows_local_path

_MOD_TOKEN_FORBIDDEN_CHARS = frozenset("/\"':")


def _absolute_parent_directory(path: str) -> str:
    """Parent directory of an absolute Windows or POSIX path string.

    ``PureWindowsPath`` alone turns POSIX ``/tmp/...`` parents into
    ``\\tmp\\...`` (rooted, no drive), which fails validation on Linux CI.
    """
    windows = PureWindowsPath(path)
    if windows.is_absolute():
        return str(windows.parent)
    posix = PurePosixPath(path)
    if posix.is_absolute():
        return str(posix.parent)
    return str(Path(path).parent)


def _validate_mod_token(value: str, *, field_path: str) -> str:
    """Validate a Civ-relative mod folder token such as ``Mods\\AdvCiv``.

    The token is passed to Civilization verbatim as one argument; it is never
    invented, normalized, or rewritten. Absolute paths, traversal, quotes,
    control characters, and stray leading/trailing whitespace are rejected.
    """
    if not isinstance(value, str) or not value:
        raise DomainValidationError(
            "expected a non-empty Civ-relative mod folder token",
            field_path=field_path,
        )
    if value != value.strip():
        raise DomainValidationError(
            "leading or trailing whitespace is not allowed in the mod token",
            field_path=field_path,
        )
    if any(char in _MOD_TOKEN_FORBIDDEN_CHARS for char in value):
        raise DomainValidationError(
            "quotes, colons, and forward slashes are not allowed in the "
            "mod token; use backslash-separated relative folders",
            field_path=field_path,
        )
    if any(ord(char) < 0x20 or char == "\x7f" for char in value):
        raise DomainValidationError(
            "control characters are not allowed in the mod token",
            field_path=field_path,
        )
    if value.startswith("\\"):
        raise DomainValidationError(
            "the mod token must be relative, not an absolute or UNC path",
            field_path=field_path,
        )
    for segment in value.split("\\"):
        if not segment:
            raise DomainValidationError(
                "empty folder segments are not allowed in the mod token",
                field_path=field_path,
            )
        if segment in {".", ".."}:
            raise DomainValidationError(
                "traversal segments are not allowed in the mod token",
                field_path=field_path,
            )
        if segment != segment.strip():
            raise DomainValidationError(
                "folder segments must not start or end with whitespace",
                field_path=field_path,
            )
    return value


@dataclass(frozen=True, slots=True)
class CivLaunchConfiguration:
    """Validated inputs for building a Civilization IV launch command."""

    executable_path: str
    mod_name: str | None = None
    save_path: str | None = None
    working_directory: str | None = None

    def __post_init__(self) -> None:
        validate_windows_local_path(self.executable_path, field_path="executable_path")
        if self.mod_name is not None:
            _validate_mod_token(self.mod_name, field_path="mod_name")
        if self.save_path is not None:
            validate_windows_local_path(self.save_path, field_path="save_path")
        if self.working_directory is not None:
            validate_windows_local_path(
                self.working_directory, field_path="working_directory"
            )


@dataclass(frozen=True, slots=True)
class CivLaunchCommand:
    """A concrete argv-style launch command; never executed through a shell."""

    argv: tuple[str, ...]
    working_directory: str | None

    def dry_run_preview(self) -> str:
        """Return the Windows-quoted single command line for display only."""
        return subprocess.list2cmdline(self.argv)


def build_civ_command(config: CivLaunchConfiguration) -> CivLaunchCommand:
    """Build the exact documented Civ4 command from validated configuration."""
    argv = [config.executable_path]
    if config.mod_name is not None:
        argv.append(f"mod={config.mod_name}")
    if config.save_path is not None:
        argv.append(f"/fxsload={config.save_path}")
    return CivLaunchCommand(
        argv=tuple(argv), working_directory=config.working_directory
    )


@unique
class LaunchPlanOutcome(Enum):
    """Result classification for :func:`build_launch_plan`."""

    READY = "ready"
    EXECUTABLE_NOT_CONFIGURED = "executable_not_configured"
    EXECUTABLE_NOT_FOUND = "executable_not_found"
    SAVE_NOT_FOUND = "save_not_found"
    SAVE_OUTSIDE_PBEM_DIRECTORY = "save_outside_pbem_directory"
    INVALID_CONFIGURATION = "invalid_configuration"


@dataclass(frozen=True, slots=True)
class LaunchPlan:
    """A launch decision: either a ready command or an actionable refusal."""

    outcome: LaunchPlanOutcome
    command: CivLaunchCommand | None = None
    reason: str = ""

    def __post_init__(self) -> None:
        is_ready = self.outcome is LaunchPlanOutcome.READY
        if is_ready and self.command is None:
            raise DomainValidationError(
                "a ready plan requires a command", field_path="command"
            )
        if not is_ready and self.command is not None:
            raise DomainValidationError(
                "only a ready plan may carry a command", field_path="command"
            )


def _default_is_file(path: str) -> bool:
    return Path(path).is_file()


def build_launch_plan(
    *,
    executable_path: str | None,
    mod_name: str | None,
    save_path: str | None,
    pbem_save_directory: str,
    is_file: Callable[[str], bool] | None = None,
) -> LaunchPlan:
    """Decide whether a launch is possible and build the command if so.

    Filesystem access is limited to the injectable ``is_file`` probe plus
    symlink and containment resolution of ``save_path``.
    """
    probe = is_file if is_file is not None else _default_is_file
    if executable_path is None or not executable_path:
        return LaunchPlan(
            outcome=LaunchPlanOutcome.EXECUTABLE_NOT_CONFIGURED,
            reason=(
                "no Civilization IV executable is configured; "
                "set civ4_executable in the global configuration"
            ),
        )
    if not probe(executable_path):
        return LaunchPlan(
            outcome=LaunchPlanOutcome.EXECUTABLE_NOT_FOUND,
            reason="the configured Civilization IV executable is not a file",
        )
    if save_path is not None:
        if not probe(save_path):
            return LaunchPlan(
                outcome=LaunchPlanOutcome.SAVE_NOT_FOUND,
                reason="the save to load is not a regular file",
            )
        if Path(save_path).is_symlink():
            return LaunchPlan(
                outcome=LaunchPlanOutcome.SAVE_OUTSIDE_PBEM_DIRECTORY,
                reason="the save to load is a symlink and is not trusted",
            )
        resolved_save = Path(save_path).resolve(strict=False)
        resolved_root = Path(pbem_save_directory).resolve(strict=False)
        try:
            resolved_save.relative_to(resolved_root)
        except ValueError:
            return LaunchPlan(
                outcome=LaunchPlanOutcome.SAVE_OUTSIDE_PBEM_DIRECTORY,
                reason="the save to load resolves outside the PBEM save directory",
            )
    try:
        config = CivLaunchConfiguration(
            executable_path=executable_path,
            mod_name=mod_name,
            save_path=save_path,
            working_directory=_absolute_parent_directory(executable_path),
        )
    except DomainValidationError as error:
        return LaunchPlan(
            outcome=LaunchPlanOutcome.INVALID_CONFIGURATION, reason=str(error)
        )
    return LaunchPlan(
        outcome=LaunchPlanOutcome.READY, command=build_civ_command(config)
    )
