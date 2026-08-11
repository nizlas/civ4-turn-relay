"""Start/stop a disposable OpenSSH SFTP container for integration tests.

Requires Docker. Skips cleanly when Docker is unavailable. Never connects to
real infrastructure. Fixture credentials only.
"""

from __future__ import annotations

import base64
import hashlib
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

OPENSSH_DIR = Path(__file__).resolve().parent / "openssh"
IMAGE_TAG = "civ4-turn-relay-openssh-sftp-test"
FIXTURE_USER = "relaytest"
FIXTURE_PASSWORD = "relaytest-password-not-for-production"
REMOTE_ROOT = "/data/games"


def docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        completed = subprocess.run(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0 and bool(completed.stdout.strip())


@dataclass(frozen=True, slots=True)
class OpenSSHFixture:
    """Connection details for one disposable SFTP container."""

    host: str
    port: int
    username: str
    password: str
    remote_root: str
    host_key_sha256: str
    container_id: str
    known_hosts_path: Path


class OpenSSHSftpServer:
    """Context manager for a disposable OpenSSH SFTP container."""

    def __init__(self) -> None:
        self._fixture: OpenSSHFixture | None = None

    def __enter__(self) -> OpenSSHFixture:
        if not docker_available():
            raise RuntimeError("Docker is not available")
        self._build_image()
        container = f"civ4-relay-sftp-{uuid.uuid4().hex[:10]}"
        run = subprocess.run(
            [
                "docker",
                "run",
                "-d",
                "--rm",
                "-p",
                "127.0.0.1::22",
                "--name",
                container,
                IMAGE_TAG,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        container_id = run.stdout.strip()
        port = self._published_port(container_id)
        fingerprint = self._wait_for_fingerprint(port)
        known_hosts = Path.cwd() / f".civ4-relay-known-hosts-{container_id[:8]}"
        # Host key line for Paramiko known_hosts loading.
        keyscan = subprocess.run(
            ["ssh-keyscan", "-p", str(port), "127.0.0.1"],
            check=False,
            capture_output=True,
            text=True,
            timeout=20,
        )
        known_hosts.write_text(keyscan.stdout, encoding="utf-8")
        self._fixture = OpenSSHFixture(
            host="127.0.0.1",
            port=port,
            username=FIXTURE_USER,
            password=FIXTURE_PASSWORD,
            remote_root=REMOTE_ROOT,
            host_key_sha256=fingerprint,
            container_id=container_id,
            known_hosts_path=known_hosts,
        )
        return self._fixture

    def __exit__(self, *exc: object) -> None:
        del exc
        if self._fixture is None:
            return
        subprocess.run(
            ["docker", "rm", "-f", self._fixture.container_id],
            check=False,
            capture_output=True,
            text=True,
        )
        try:
            self._fixture.known_hosts_path.unlink(missing_ok=True)
        except OSError:
            pass
        self._fixture = None

    def _build_image(self) -> None:
        subprocess.run(
            ["docker", "build", "-t", IMAGE_TAG, str(OPENSSH_DIR)],
            check=True,
            capture_output=True,
            text=True,
        )

    def _published_port(self, container_id: str) -> int:
        completed = subprocess.run(
            ["docker", "port", container_id, "22/tcp"],
            check=True,
            capture_output=True,
            text=True,
        )
        # Example: 127.0.0.1:32768
        line = completed.stdout.strip().splitlines()[0]
        return int(line.rsplit(":", 1)[1])

    def _wait_for_fingerprint(self, port: int, *, attempts: int = 30) -> str:
        last_error: Exception | None = None
        for _ in range(attempts):
            try:
                completed = subprocess.run(
                    ["ssh-keyscan", "-p", str(port), "-t", "ed25519", "127.0.0.1"],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if completed.returncode == 0 and completed.stdout.strip():
                    # Parse OpenSSH host key line and compute SHA256 fingerprint.
                    for line in completed.stdout.splitlines():
                        parts = line.strip().split()
                        if len(parts) < 3:
                            continue
                        key_bytes = base64.b64decode(parts[2])
                        digest = hashlib.sha256(key_bytes).digest()
                        encoded = base64.b64encode(digest).decode("ascii").rstrip("=")
                        return f"SHA256:{encoded}"
            except Exception as error:  # noqa: BLE001 - retry readiness
                last_error = error
            time.sleep(0.5)
        raise RuntimeError(
            f"OpenSSH fixture did not become ready on port {port}"
        ) from last_error
