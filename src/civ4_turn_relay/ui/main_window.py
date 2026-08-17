"""Main window: match list, one prominent status, one primary button.

Pure presentation and command dispatch: every button click dispatches
exactly one command to the hub based on the current view model; snapshots
arrive via queued signals and are rendered as-is. No protocol logic and no
match-state mutation happens here.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QAction, QCloseEvent, QFont
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from civ4_turn_relay.app.process_runtime import ProcessStatus
from civ4_turn_relay.domain import OperationalState
from civ4_turn_relay.ui.controller import MatchUiSnapshot
from civ4_turn_relay.ui.presenter import (
    MatchViewModel,
    PrimaryActionKind,
    SecondaryActionKind,
    build_view_model,
)

_GAME_ID_ROLE = int(Qt.ItemDataRole.UserRole)
_ATTENTION_MARKER = " \u25cf"
_MAX_DIAGNOSTIC_BLOCKS = 500

_ACTIVE_PROCESS_STATUSES = frozenset(
    {
        ProcessStatus.STARTING,
        ProcessStatus.RUNNING,
        ProcessStatus.CLOSE_REQUESTED,
        ProcessStatus.CLOSING_AFTER_COMMIT,
        ProcessStatus.CLOSE_FAILED,
    }
)


class MatchCommandDispatcher(Protocol):
    """Command surface the window dispatches to (satisfied by the hub)."""

    def request_start(self, game_id: str) -> None: ...

    def send_save(self, game_id: str) -> None: ...

    def select_candidate(self, game_id: str, path: str) -> None: ...

    def retry(self, game_id: str) -> None: ...

    def focus_civ(self, game_id: str) -> None: ...

    def close_civ(self, game_id: str) -> None: ...


class MainWindow(QMainWindow):
    """Minimal multi-match window over immutable snapshots."""

    add_match_requested = Signal()
    edit_match_requested = Signal(str)
    settings_requested = Signal()
    reload_requested = Signal()
    quit_requested = Signal()
    """Emitted when the window close would quit the application (idle, no tray)."""

    def __init__(
        self,
        dispatcher: MatchCommandDispatcher,
        *,
        save_directory_provider: Callable[[str], str] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._dispatcher = dispatcher
        self._save_directory_provider = save_directory_provider
        self._snapshots: dict[str, MatchUiSnapshot] = {}
        self._view_models: dict[str, MatchViewModel] = {}
        self._last_diagnostic: dict[str, str] = {}
        self._tray_available = False
        self._quit_allowed = False

        self.setWindowTitle("Civ4 Turn Relay")
        self._build_menu()
        self._build_widgets()

    # ----- construction -------------------------------------------------

    def _build_menu(self) -> None:
        menu_bar = self.menuBar()
        file_menu = menu_bar.addMenu("&File")
        quit_action = QAction("Quit", self)
        quit_action.triggered.connect(self.quit_requested.emit)
        file_menu.addAction(quit_action)

        match_menu = menu_bar.addMenu("&Match")
        add_action = QAction("Add match…", self)
        add_action.triggered.connect(self._on_add_match)
        match_menu.addAction(add_action)
        edit_action = QAction("Edit match…", self)
        edit_action.triggered.connect(self._on_edit_match)
        match_menu.addAction(edit_action)

        settings_menu = menu_bar.addMenu("&Settings")
        settings_action = QAction("Settings…", self)
        settings_action.triggered.connect(self._on_settings)
        settings_menu.addAction(settings_action)
        reload_action = QAction("Reload configuration", self)
        reload_action.triggered.connect(self._on_reload)
        settings_menu.addAction(reload_action)

    def _build_widgets(self) -> None:
        central = QWidget(self)
        layout = QHBoxLayout(central)

        self.match_list = QListWidget(central)
        self.match_list.setMaximumWidth(240)
        self.match_list.currentItemChanged.connect(self._on_selection_changed)
        layout.addWidget(self.match_list)

        right = QVBoxLayout()
        self.name_label = QLabel("", central)
        name_font = QFont()
        name_font.setPointSize(16)
        name_font.setBold(True)
        self.name_label.setFont(name_font)
        right.addWidget(self.name_label)

        self.status_label = QLabel("", central)
        status_font = QFont()
        status_font.setPointSize(12)
        status_font.setBold(True)
        self.status_label.setFont(status_font)
        right.addWidget(self.status_label)

        self.detail_label = QLabel("", central)
        self.detail_label.setWordWrap(True)
        right.addWidget(self.detail_label)

        self.primary_button = QPushButton("", central)
        self.primary_button.setEnabled(False)
        self.primary_button.clicked.connect(self._on_primary_clicked)
        right.addWidget(self.primary_button)

        secondary_row = QHBoxLayout()
        self.focus_button = QPushButton("Focus Civilization", central)
        self.focus_button.clicked.connect(self._on_focus_clicked)
        self.focus_button.setVisible(False)
        secondary_row.addWidget(self.focus_button)
        self.close_button = QPushButton("Close Civilization", central)
        self.close_button.clicked.connect(self._on_close_clicked)
        self.close_button.setVisible(False)
        secondary_row.addWidget(self.close_button)
        secondary_row.addStretch(1)
        right.addLayout(secondary_row)

        right.addWidget(QLabel("Details", central))
        self.details_view = QPlainTextEdit(central)
        self.details_view.setReadOnly(True)
        self.details_view.setMaximumBlockCount(_MAX_DIAGNOSTIC_BLOCKS)
        right.addWidget(self.details_view, 1)

        layout.addLayout(right, 1)
        self.setCentralWidget(central)

    # ----- snapshot intake ----------------------------------------------

    @Slot(object)
    def on_snapshot(self, payload: object) -> None:
        """Render a fresh immutable snapshot; identical snapshots are inert."""
        if not isinstance(payload, MatchUiSnapshot):
            return
        game_id = payload.game_id
        self._snapshots[game_id] = payload
        view_model = build_view_model(payload.client, payload.process)
        self._view_models[game_id] = view_model
        self._update_list_item(view_model)
        self._append_diagnostic(payload)
        if self.match_list.currentItem() is None:
            self.match_list.setCurrentRow(0)
        elif self.current_game_id() == game_id:
            self._render(view_model)

    @Slot(str)
    def on_error(self, message: str) -> None:
        self.details_view.appendPlainText(message)

    def current_game_id(self) -> str | None:
        item = self.match_list.currentItem()
        if item is None:
            return None
        value = item.data(_GAME_ID_ROLE)
        return value if isinstance(value, str) else None

    def has_active_match(self) -> bool:
        """True when any match has a live process or a handoff in flight."""
        for snapshot in self._snapshots.values():
            process = snapshot.process
            if process is not None and process.status in _ACTIVE_PROCESS_STATUSES:
                return True
            if snapshot.client.operational_state is OperationalState.UPLOADING:
                return True
        return False

    def set_tray_available(self, available: bool) -> None:
        self._tray_available = available

    def allow_quit(self) -> None:
        """Permit the next close event to really close the window."""
        self._quit_allowed = True

    # ----- rendering ----------------------------------------------------

    def _update_list_item(self, view_model: MatchViewModel) -> None:
        text = view_model.display_name
        if view_model.attention:
            text += _ATTENTION_MARKER
        for row in range(self.match_list.count()):
            item = self.match_list.item(row)
            if item is not None and item.data(_GAME_ID_ROLE) == view_model.game_id:
                if item.text() != text:
                    item.setText(text)
                return
        item = QListWidgetItem(text)
        item.setData(_GAME_ID_ROLE, view_model.game_id)
        self.match_list.addItem(item)

    def _append_diagnostic(self, snapshot: MatchUiSnapshot) -> None:
        diagnostic = snapshot.client.latest_diagnostic
        if diagnostic is None or not diagnostic.message:
            return
        game_id = snapshot.game_id
        if self._last_diagnostic.get(game_id) == diagnostic.message:
            return
        self._last_diagnostic[game_id] = diagnostic.message
        self.details_view.appendPlainText(f"[{game_id}] {diagnostic.message}")

    def _render(self, view_model: MatchViewModel) -> None:
        self.name_label.setText(view_model.display_name)
        self.status_label.setText(view_model.status_text)
        self.detail_label.setText(view_model.detail_text)
        self.primary_button.setText(view_model.primary_label)
        self.primary_button.setEnabled(view_model.primary_enabled)
        self.focus_button.setVisible(
            SecondaryActionKind.FOCUS_CIV in view_model.secondary_actions
        )
        self.close_button.setVisible(
            SecondaryActionKind.CLOSE_CIV in view_model.secondary_actions
        )

    # ----- dispatch -----------------------------------------------------

    def _on_selection_changed(
        self, current: QListWidgetItem | None, previous: QListWidgetItem | None
    ) -> None:
        del previous
        if current is None:
            return
        value = current.data(_GAME_ID_ROLE)
        if isinstance(value, str):
            view_model = self._view_models.get(value)
            if view_model is not None:
                self._render(view_model)

    def _on_primary_clicked(self) -> None:
        game_id = self.current_game_id()
        if game_id is None:
            return
        view_model = self._view_models.get(game_id)
        if view_model is None or not view_model.primary_enabled:
            return
        kind = view_model.primary_action
        if kind is PrimaryActionKind.START_CIV:
            self._dispatcher.request_start(game_id)
        elif kind is PrimaryActionKind.SEND_SAVE:
            self._dispatcher.send_save(game_id)
        elif kind is PrimaryActionKind.CHOOSE_SAVE:
            self._choose_save(game_id)
        elif kind is PrimaryActionKind.RETRY:
            self._dispatcher.retry(game_id)
        elif kind is PrimaryActionKind.FOCUS_CIV:
            self._dispatcher.focus_civ(game_id)

    def _choose_save(self, game_id: str) -> None:
        start_dir = ""
        if self._save_directory_provider is not None:
            start_dir = self._save_directory_provider(game_id)
        path, _selected_filter = QFileDialog.getOpenFileName(
            self, "Choose the save to send", start_dir
        )
        if path:
            self._dispatcher.select_candidate(game_id, path)

    def _on_focus_clicked(self) -> None:
        game_id = self.current_game_id()
        if game_id is not None:
            self._dispatcher.focus_civ(game_id)

    def _on_close_clicked(self) -> None:
        game_id = self.current_game_id()
        if game_id is not None:
            self._dispatcher.close_civ(game_id)

    def _on_add_match(self) -> None:
        self.add_match_requested.emit()

    def _on_edit_match(self) -> None:
        game_id = self.current_game_id()
        if game_id is not None:
            self.edit_match_requested.emit(game_id)

    def _on_settings(self) -> None:
        self.settings_requested.emit()

    def _on_reload(self) -> None:
        self.reload_requested.emit()

    # ----- lifecycle ----------------------------------------------------

    def closeEvent(self, event: QCloseEvent) -> None:
        """Hide to tray while active; funnel idle quit through the coordinator."""
        if self._quit_allowed:
            event.accept()
            return
        if self.has_active_match() or self._tray_available:
            event.ignore()
            self.hide()
            return
        # Idle without tray: do not tear down widgets before worker shutdown.
        event.ignore()
        self.quit_requested.emit()
