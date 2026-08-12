"""Run UI shutdown tests in a fresh interpreter process.

Windows has seen intermittent PySide6 heap corruption during interpreter
teardown after otherwise-green pytest-qt runs. A non-zero child exit code
(including crash / abort) fails this gate. The child uses the offscreen
Qt platform and does not require Civilization or SFTP.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SHUTDOWN_NODE_IDS = (
    "tests/ui/test_tray_and_quit.py::test_quit_when_idle_needs_no_confirmation",
    "tests/ui/test_tray_and_quit.py::test_shutdown_is_idempotent_and_blocks_further_commands",
    "tests/ui/test_controller.py::test_shutdown_stops_thread_and_is_idempotent",
    "tests/ui/test_tray_and_quit.py::test_successful_gated_quit_authorizes_application_exit",
)


@pytest.mark.parametrize("node_id", _SHUTDOWN_NODE_IDS)
def test_ui_shutdown_exits_cleanly_in_subprocess(node_id: str) -> None:
    env = os.environ.copy()
    env.setdefault("QT_QPA_PLATFORM", "offscreen")
    # Avoid inheriting a polluted PYTHONPATH from packaging/tooling shells.
    completed = subprocess.run(
        [sys.executable, "-m", "pytest", node_id, "-q", "--tb=line"],
        cwd=_REPO_ROOT,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, (
        f"subprocess pytest for {node_id} exited {completed.returncode}\n"
        f"--- stdout ---\n{completed.stdout}\n"
        f"--- stderr ---\n{completed.stderr}\n"
        "On Windows, re-run packaging/tools/run_ui_teardown_stress.ps1 if this "
        "fails only under that OS (PySide6 teardown heap corruption)."
    )
