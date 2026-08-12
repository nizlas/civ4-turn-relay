"""Pure path validators. No filesystem access happens here.

Remote paths are relative POSIX paths under the remote game root
(``{server_root}/games/{game_id}/``, protocol §2). Local paths are Windows
paths validated structurally only; they are never required to exist.
"""

from __future__ import annotations

from pathlib import PurePosixPath, PureWindowsPath

from civ4_turn_relay.domain.errors import DomainValidationError

SAVES_DIRECTORY = "saves"
HISTORY_DIRECTORY = "history"

_WINDOWS_FORBIDDEN_CHARS = frozenset('<>"|?*')


def validate_remote_relative_path(value: str, *, field_path: str = "path") -> str:
    """Validate a remote path as relative, unambiguous, and root-contained.

    Rejects absolute paths, ``.``/``..`` components, backslashes, empty
    components (including trailing separators), and control characters.
    """
    if not isinstance(value, str) or not value:
        raise DomainValidationError(
            "expected a non-empty relative POSIX path", field_path=field_path
        )
    if "\\" in value:
        raise DomainValidationError(
            "backslashes are not allowed in remote paths", field_path=field_path
        )
    if any(ord(char) < 0x20 or char == "\x7f" for char in value):
        raise DomainValidationError(
            "control characters are not allowed in remote paths",
            field_path=field_path,
        )
    if value.startswith("/"):
        raise DomainValidationError(
            "absolute remote paths are not allowed", field_path=field_path
        )
    for component in value.split("/"):
        if component == "":
            raise DomainValidationError(
                "empty path components are not allowed", field_path=field_path
            )
        if component in {".", ".."}:
            raise DomainValidationError(
                "'.' and '..' path components are not allowed",
                field_path=field_path,
            )
    # Belt-and-braces containment check using pure path logic: joining the
    # validated path onto a synthetic game root must stay strictly below it.
    root = PurePosixPath("game-root")
    joined = root.joinpath(value)
    if joined.parts[: len(root.parts)] != root.parts or joined == root:
        raise DomainValidationError("path escapes the game root", field_path=field_path)
    return value


def validate_accepted_save_path(value: str, *, field_path: str = "remote_path") -> str:
    """Validate an accepted-save path strictly below ``saves/``."""
    validate_remote_relative_path(value, field_path=field_path)
    parts = PurePosixPath(value).parts
    if parts[0] != SAVES_DIRECTORY or len(parts) < 2:
        raise DomainValidationError(
            "expected a path strictly below saves/", field_path=field_path
        )
    return value


def validate_history_manifest_ref(
    value: str, *, field_path: str = "previous_manifest_ref"
) -> str:
    """Validate a manifest-history reference strictly below ``history/``."""
    validate_remote_relative_path(value, field_path=field_path)
    parts = PurePosixPath(value).parts
    if parts[0] != HISTORY_DIRECTORY or len(parts) < 2:
        raise DomainValidationError(
            "expected a path strictly below history/", field_path=field_path
        )
    return value


def validate_original_filename(
    value: str, *, field_path: str = "original_filename"
) -> str:
    """Validate an original save filename as a basename only."""
    message = "expected a basename only (no directory separators or dot components)"
    if not isinstance(value, str) or not value or value in {".", ".."}:
        raise DomainValidationError(message, field_path=field_path)
    if "/" in value or "\\" in value:
        raise DomainValidationError(message, field_path=field_path)
    if any(ord(char) < 0x20 or char == "\x7f" for char in value):
        raise DomainValidationError(
            "control characters are not allowed in filenames",
            field_path=field_path,
        )
    if PurePosixPath(value).name != value or PureWindowsPath(value).name != value:
        raise DomainValidationError(message, field_path=field_path)
    return value


def validate_windows_local_path(value: str, *, field_path: str = "path") -> str:
    """Structurally validate an absolute local path for the Windows client.

    Pure string/path checks only; the path is never resolved or accessed.

    Accepts absolute Windows paths (drive letter or UNC). Also accepts absolute
    POSIX paths so filesystem-backed tests on Linux/macOS CI can use native
    temporary directories. Production clients remain Windows-only; relative and
    drive-relative forms are still rejected.
    """
    if not isinstance(value, str) or not value:
        raise DomainValidationError(
            "expected a non-empty Windows path", field_path=field_path
        )
    if any(ord(char) < 0x20 or char == "\x7f" for char in value):
        raise DomainValidationError(
            "control characters are not allowed in local paths",
            field_path=field_path,
        )
    if any(char in _WINDOWS_FORBIDDEN_CHARS for char in value):
        raise DomainValidationError(
            'characters <>"|?* are not allowed in local paths',
            field_path=field_path,
        )
    windows_absolute = PureWindowsPath(value).is_absolute()
    posix_absolute = PurePosixPath(value).is_absolute()
    if windows_absolute:
        if ":" in value[2:] or (":" in value[:2] and value.find(":") != 1):
            raise DomainValidationError(
                "a colon is only allowed as part of the drive letter",
                field_path=field_path,
            )
        components = value.replace("\\", "/").split("/")
    elif posix_absolute:
        components = value.split("/")
    else:
        raise DomainValidationError(
            "expected an absolute Windows path (drive letter or UNC)",
            field_path=field_path,
        )
    for component in components:
        if component in {".", ".."}:
            raise DomainValidationError(
                "'.' and '..' path components are not allowed",
                field_path=field_path,
            )
    return value
