"""Read-only global settings view over the redacted configuration mapping.

The dialog never loads configuration itself and never renders raw secret
values: it only displays ``GlobalConfig.to_redacted_mapping()`` output plus
the (public) host-key trust mechanism.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from PySide6.QtCore import QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from civ4_turn_relay.domain import GlobalConfig


def _trust_line(config: GlobalConfig) -> str:
    if config.sftp_known_hosts_path is not None:
        return "Host-key trust: known_hosts file configured"
    if config.sftp_host_key_sha256 is not None:
        return (
            "Host-key trust: pinned host key fingerprint configured "
            f"({config.sftp_host_key_sha256})"
        )
    return "Host-key trust: not configured"


class GlobalSettingsDialog(QDialog):
    """Presentation-only dialog for global configuration health."""

    reload_requested = Signal()

    def __init__(
        self,
        *,
        config: GlobalConfig | None,
        error_text: str | None = None,
        dotenv_path: Path | None = None,
        env_example_path: Path | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Global settings")
        self._dotenv_path = dotenv_path
        self._env_example_path = env_example_path
        self._value_labels: list[QLabel] = []

        layout = QVBoxLayout(self)
        if config is not None:
            status = "Global configuration loaded"
        else:
            status = f"Global configuration error: {error_text or 'not loaded'}"
        self.status_label = QLabel(status, self)
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)

        form = QFormLayout()
        if config is not None:
            for key, value in config.to_redacted_mapping().items():
                value_label = QLabel("" if value is None else str(value), self)
                self._value_labels.append(value_label)
                form.addRow(QLabel(key, self), value_label)
        layout.addLayout(form)

        trust = "Host-key trust: unknown (configuration not loaded)"
        if config is not None:
            trust = _trust_line(config)
        self.trust_label = QLabel(trust, self)
        self.trust_label.setWordWrap(True)
        layout.addWidget(self.trust_label)

        buttons = QHBoxLayout()
        self.reload_button = QPushButton("Reload configuration", self)
        self.reload_button.clicked.connect(self.reload_requested.emit)
        buttons.addWidget(self.reload_button)

        self.open_env_button = QPushButton("Open .env folder", self)
        self.open_env_button.setEnabled(
            dotenv_path is not None and dotenv_path.exists()
        )
        self.open_env_button.clicked.connect(self._open_env_folder)
        buttons.addWidget(self.open_env_button)

        self.create_env_button = QPushButton("Create from .env.example", self)
        self.create_env_button.setEnabled(
            env_example_path is not None and env_example_path.is_file()
        )
        self.create_env_button.clicked.connect(self._create_from_example)
        buttons.addWidget(self.create_env_button)

        buttons.addStretch(1)
        close_button = QPushButton("Close", self)
        close_button.clicked.connect(self.accept)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)

    def rendered_text(self) -> str:
        """All displayed text, for tests asserting secrets never appear."""
        parts = [self.status_label.text(), self.trust_label.text()]
        for label in self.findChildren(QLabel):
            parts.append(label.text())
        return "\n".join(parts)

    def _open_env_folder(self) -> None:
        if self._dotenv_path is None or not self._dotenv_path.exists():
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._dotenv_path.parent)))

    def _create_from_example(self) -> None:
        if self._env_example_path is None or not self._env_example_path.is_file():
            return
        suggested = ""
        if self._dotenv_path is not None:
            suggested = str(self._dotenv_path)
        target_text, _selected_filter = QFileDialog.getSaveFileName(
            self,
            "Create .env from .env.example",
            suggested,
            options=QFileDialog.Option.DontConfirmOverwrite,
        )
        if not target_text:
            return
        target = Path(target_text)
        if target.exists():
            QMessageBox.warning(
                self,
                "File already exists",
                "The chosen file already exists and will not be overwritten.",
            )
            return
        shutil.copyfile(self._env_example_path, target)
        QMessageBox.information(
            self,
            "Created",
            "A placeholder .env was created. Fill in your server settings "
            "and reload the configuration.",
        )
