"""System tray icon with Open/Quit. Quitting never terminates Civilization.

The quit action only invokes the coordinator callback; confirmation and
clean shutdown ordering are owned by the coordinator, never by the tray.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

_ICON_SIZE = 32


def _generated_icon() -> QIcon:
    """Simple painted icon (background square + circle); no binary assets."""
    pixmap = QPixmap(_ICON_SIZE, _ICON_SIZE)
    pixmap.fill(QColor(30, 60, 110))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor(240, 200, 60))
    painter.setPen(Qt.PenStyle.NoPen)
    margin = _ICON_SIZE // 5
    painter.drawEllipse(
        margin, margin, _ICON_SIZE - 2 * margin, _ICON_SIZE - 2 * margin
    )
    painter.end()
    return QIcon(pixmap)


class RelayTray(QSystemTrayIcon):
    """Tray icon with an "Open Relay" and a "Quit" action."""

    def __init__(
        self,
        *,
        on_open: Callable[[], None],
        on_quit: Callable[[], None],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(_generated_icon(), parent)
        self.setToolTip("Civ4 Turn Relay")
        self._menu = QMenu()
        open_action = self._menu.addAction("Open Relay")
        open_action.triggered.connect(on_open)
        quit_action = self._menu.addAction("Quit")
        quit_action.triggered.connect(on_quit)
        self.setContextMenu(self._menu)
        self.activated.connect(self._on_activated)
        self._on_open = on_open

    @staticmethod
    def is_available() -> bool:
        return bool(QSystemTrayIcon.isSystemTrayAvailable())

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self._on_open()
