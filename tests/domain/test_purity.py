"""The domain package must stay pure: stdlib-only, no adapter imports.

Guards the P1 exit criterion that no I/O adapters (Paramiko, PySide6,
Watchdog, subprocess, sockets, ...) leak into the domain layer.
"""

import ast
from pathlib import Path

import civ4_turn_relay.domain

ALLOWED_TOP_LEVEL_IMPORTS = {
    "__future__",
    "civ4_turn_relay",
    "collections",
    "dataclasses",
    "datetime",
    "enum",
    "hashlib",
    "json",
    "pathlib",
    "re",
    "typing",
}


def domain_module_files() -> list[Path]:
    package_file = civ4_turn_relay.domain.__file__
    assert package_file is not None
    files = sorted(Path(package_file).parent.glob("*.py"))
    assert files, "domain package modules not found"
    return files


def test_domain_modules_import_only_allowed_modules() -> None:
    for module_file in domain_module_files():
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


def test_domain_never_imports_adapters_or_io_frameworks() -> None:
    forbidden = {"paramiko", "PySide6", "watchdog", "subprocess", "socket", "os"}
    assert not (ALLOWED_TOP_LEVEL_IMPORTS & forbidden)
