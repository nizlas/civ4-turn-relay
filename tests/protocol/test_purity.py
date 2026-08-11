"""Protocol package may import only stdlib, domain, and storage port surface."""

from __future__ import annotations

import ast
from pathlib import Path

import civ4_turn_relay.protocol

ALLOWED_TOP_LEVEL_IMPORTS = {
    "__future__",
    "civ4_turn_relay",
    "collections",
    "dataclasses",
    "enum",
    "typing",
}

FORBIDDEN = {
    "paramiko",
    "PySide6",
    "watchdog",
    "subprocess",
    "socket",
    "os",
    "pathlib",
}


def protocol_module_files() -> list[Path]:
    package_file = civ4_turn_relay.protocol.__file__
    assert package_file is not None
    files = sorted(Path(package_file).parent.glob("*.py"))
    assert files, "protocol package modules not found"
    return files


def test_protocol_modules_import_only_allowed_modules() -> None:
    for module_file in protocol_module_files():
        tree = ast.parse(module_file.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                assert node.level == 0, f"relative import in {module_file.name}"
                assert node.module is not None
                names = [node.module]
            else:
                continue
            for name in names:
                top_level = name.split(".")[0]
                assert top_level in ALLOWED_TOP_LEVEL_IMPORTS, (
                    f"{module_file.name} imports disallowed module {name!r}"
                )
                assert "fake" not in name.split("."), (
                    f"{module_file.name} must not import FakeStorage ({name})"
                )


def test_protocol_never_imports_adapters_or_io_frameworks() -> None:
    assert not (ALLOWED_TOP_LEVEL_IMPORTS & FORBIDDEN)


def test_protocol_does_not_import_storage_fake_module() -> None:
    for module_file in protocol_module_files():
        text = module_file.read_text(encoding="utf-8")
        assert "storage.fake" not in text
        assert "FakeStorage" not in text
