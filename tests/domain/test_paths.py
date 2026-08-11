"""Table-driven tests for pure remote/local path validators."""

import pytest

from civ4_turn_relay.domain import (
    DomainValidationError,
    validate_accepted_save_path,
    validate_history_manifest_ref,
    validate_original_filename,
    validate_remote_relative_path,
    validate_windows_local_path,
)


@pytest.mark.parametrize(
    "value",
    [
        "saves/000001_a1b2c3d4e5f6.CivBeyondSwordSave",
        "history/manifest-000000-0123456789ab.json",
        "temporary/op.upload",
        "manifest.json",
        "a/b/c",
    ],
)
def test_valid_remote_relative_paths(value: str) -> None:
    assert validate_remote_relative_path(value) == value


@pytest.mark.parametrize(
    "value",
    [
        "",
        "/absolute/path",  # absolute
        "/",  # absolute root
        "a/../b",  # traversal component
        "..",
        "../escape",
        "./a",  # dot component
        "a/./b",
        "a//b",  # empty component
        "a/",  # trailing separator gives empty component
        "a\\b",  # backslash separator
        "saves\\x",
        "a/b\\c",  # ambiguous mixed separators
        "a/\x00b",  # control character
    ],
)
def test_invalid_remote_relative_paths(value: str) -> None:
    with pytest.raises(DomainValidationError):
        validate_remote_relative_path(value)


@pytest.mark.parametrize(
    "value",
    [
        "saves/000001_a1b2c3d4e5f6.CivBeyondSwordSave",
        "saves/000042_abcdef123456.Civ4SavedGame",
    ],
)
def test_valid_accepted_save_paths(value: str) -> None:
    assert validate_accepted_save_path(value) == value


@pytest.mark.parametrize(
    "value",
    [
        "saves",  # the directory itself, not below it
        "saves/",  # empty component
        "other/000001_ab.sav",  # not under saves/
        "history/000001_ab.sav",
        "/saves/000001_ab.sav",  # absolute
        "saves/../000001_ab.sav",  # traversal
        "../saves/000001_ab.sav",
        "saves\\000001_ab.sav",  # backslash
        "",
    ],
)
def test_invalid_accepted_save_paths(value: str) -> None:
    with pytest.raises(DomainValidationError):
        validate_accepted_save_path(value)


def test_valid_history_manifest_ref() -> None:
    value = "history/manifest-000000-0123456789ab.json"
    assert validate_history_manifest_ref(value) == value


@pytest.mark.parametrize(
    "value",
    [
        "history",  # the directory itself
        "history/",
        "saves/manifest-000000-ab.json",  # wrong directory
        "manifest-000000-ab.json",  # not below history/
        "/history/manifest-000000-ab.json",  # absolute
        "history/../manifest.json",  # traversal
        "history\\manifest.json",  # backslash
        "",
    ],
)
def test_invalid_history_manifest_refs(value: str) -> None:
    with pytest.raises(DomainValidationError):
        validate_history_manifest_ref(value)


@pytest.mark.parametrize(
    "value",
    [
        "ExampleMatch_PlayerA.CivBeyondSwordSave",
        "save.Civ4SavedGame",
        "no-extension",
    ],
)
def test_valid_original_filenames(value: str) -> None:
    assert validate_original_filename(value) == value


@pytest.mark.parametrize(
    "value",
    [
        "",
        ".",
        "..",
        "dir/save.Civ",  # posix separator
        "dir\\save.Civ",  # windows separator
        "/save.Civ",  # absolute
        "../save.Civ",  # traversal
        "C:save.Civ",  # drive-relative windows form
        "save\x00.Civ",  # control character
    ],
)
def test_invalid_original_filenames(value: str) -> None:
    with pytest.raises(DomainValidationError):
        validate_original_filename(value)


@pytest.mark.parametrize(
    "value",
    [
        "C:\\Games\\Civ4\\Saves\\pbem",
        "C:/Games/Civ4/Saves/pbem",  # forward slashes are valid on Windows
        "D:\\PBEM",
        "\\\\host\\share\\pbem",  # UNC
    ],
)
def test_valid_windows_local_paths(value: str) -> None:
    assert validate_windows_local_path(value) == value


@pytest.mark.parametrize(
    "value",
    [
        "",
        "relative\\path",  # not absolute
        "Saves",
        "C:relative",  # drive-relative
        "\\rooted-no-drive",  # missing drive
        "C:\\a\\..\\b",  # traversal
        "C:\\a\\.\\b",  # dot component
        'C:\\bad"name',  # forbidden character
        "C:\\bad|name",
        "C:\\bad?name",
        "C:\\bad*name",
        "C:\\bad<name>",
        "C:\\a\x00b",  # control character
        "C:\\a:b",  # colon outside drive
    ],
)
def test_invalid_windows_local_paths(value: str) -> None:
    with pytest.raises(DomainValidationError):
        validate_windows_local_path(value)
