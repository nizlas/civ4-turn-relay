"""Create/edit dialog for per-match configuration.

Validation happens exclusively through the ``MatchConfig`` constructor; the
dialog stays open on any :class:`DomainValidationError`. SFTP settings and
credentials never appear here (they are global-only by design).
"""

from __future__ import annotations

from collections.abc import Collection

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from civ4_turn_relay.domain import (
    DomainValidationError,
    MatchConfig,
    Player,
    SaveMatchingRules,
    TurnHandlingMode,
)

_DEFAULT_GLOB = "*.CivBeyondSwordSave"
_DEFAULT_MOD = "Mods\\AdvCiv"

_FIRST_TURN_TEXT = (
    "Civilization itself creates the PBEM game. Start the game in Civ, "
    "save the first PBEM turn into the folder above, and Relay will detect "
    "and send it. Relay cannot generate a Civ save."
)

_MANAGED_CLOSE_POLICY = (
    "Fully managed: Relay starts Civilization for your turn and, once your "
    "sent turn is verified on the server, automatically closes the exact "
    "Civilization process it launched. Standard never closes Civilization."
)


def _parse_players(text: str) -> tuple[Player, ...]:
    """Parse ordered "player_id:display name" lines into Player instances."""
    players: list[Player] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        player_id, _, display = stripped.partition(":")
        player_id = player_id.strip()
        display = display.strip() or player_id
        players.append(Player(id=player_id, display_name=display))
    return tuple(players)


def _players_text(players: tuple[Player, ...]) -> str:
    return "\n".join(f"{player.id}:{player.display_name}" for player in players)


class MatchEditDialog(QDialog):
    """Create or edit one MatchConfig; result available via result_config()."""

    def __init__(
        self,
        *,
        existing: MatchConfig | None = None,
        existing_game_ids: Collection[str] = (),
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._editing = existing is not None
        self._existing_game_ids = frozenset(existing_game_ids)
        self._result: MatchConfig | None = None

        self.setWindowTitle("Edit match" if self._editing else "Add match")
        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.game_id_edit = QLineEdit(self)
        self.game_id_edit.setEnabled(not self._editing)
        form.addRow("Game ID", self.game_id_edit)

        self.display_name_edit = QLineEdit(self)
        form.addRow("Display name", self.display_name_edit)

        self.players_edit = QPlainTextEdit(self)
        self.players_edit.setPlaceholderText(
            "One player per line, in turn order:\nplayer_id:Display Name"
        )
        self.players_edit.setMaximumHeight(110)
        self.players_edit.textChanged.connect(self._on_players_changed)
        form.addRow("Players (ordered)", self.players_edit)

        self.local_player_combo = QComboBox(self)
        form.addRow("You are", self.local_player_combo)

        pbem_row = QHBoxLayout()
        self.pbem_dir_edit = QLineEdit(self)
        pbem_row.addWidget(self.pbem_dir_edit, 1)
        self.browse_button = QPushButton("Browse…", self)
        self.browse_button.clicked.connect(self._on_browse)
        pbem_row.addWidget(self.browse_button)
        form.addRow("PBEM save folder", pbem_row)

        self.glob_edit = QLineEdit(_DEFAULT_GLOB, self)
        form.addRow("Save filename pattern", self.glob_edit)

        self.mod_name_edit = QLineEdit(_DEFAULT_MOD, self)
        self.mod_name_edit.setToolTip(
            "Civ-relative mod folder, for example Mods\\AdvCiv. Relay "
            "translates it to Civilization IV's mod=\\AdvCiv command-line "
            "syntax. Leave empty to rely on the Civilization INI."
        )
        form.addRow("Mod folder (Civ-relative)", self.mod_name_edit)

        self.launch_profile_edit = QLineEdit(self)
        form.addRow("Launch profile (optional)", self.launch_profile_edit)

        mode_row = QHBoxLayout()
        self.standard_radio = QRadioButton("Standard", self)
        self.standard_radio.setChecked(True)
        self.managed_radio = QRadioButton("Fully managed", self)
        mode_row.addWidget(self.standard_radio)
        mode_row.addWidget(self.managed_radio)
        mode_row.addStretch(1)
        form.addRow("Turn handling", mode_row)

        self.close_policy_label = QLabel(_MANAGED_CLOSE_POLICY, self)
        self.close_policy_label.setWordWrap(True)
        form.addRow("", self.close_policy_label)

        layout.addLayout(form)

        self.first_turn_label = QLabel("", self)
        self.first_turn_label.setWordWrap(True)
        layout.addWidget(self.first_turn_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel,
            self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        if existing is not None:
            self._prefill(existing)
        self._on_players_changed()

    def _prefill(self, config: MatchConfig) -> None:
        self.game_id_edit.setText(config.game_id)
        self.display_name_edit.setText(config.display_name)
        self.players_edit.setPlainText(_players_text(config.players))
        self.pbem_dir_edit.setText(config.pbem_save_directory)
        self.glob_edit.setText(config.save_matching.filename_glob)
        self.mod_name_edit.setText(config.mod_name or "")
        self.launch_profile_edit.setText(config.launch_profile or "")
        if config.turn_handling_mode is TurnHandlingMode.FULLY_MANAGED:
            self.managed_radio.setChecked(True)
        else:
            self.standard_radio.setChecked(True)
        self._select_local_player(config.local_player_id)

    def _select_local_player(self, player_id: str) -> None:
        index = self.local_player_combo.findText(player_id)
        if index >= 0:
            self.local_player_combo.setCurrentIndex(index)

    def _on_players_changed(self) -> None:
        """Refresh the local-player choices and the first-turn helper text."""
        current = self.local_player_combo.currentText()
        ids: list[str] = []
        first_display: str | None = None
        for line in self.players_edit.toPlainText().splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            player_id, _, display = stripped.partition(":")
            player_id = player_id.strip()
            if player_id:
                ids.append(player_id)
                if first_display is None:
                    first_display = display.strip() or player_id
        self.local_player_combo.clear()
        self.local_player_combo.addItems(ids)
        if current in ids:
            self._select_local_player(current)
        if first_display is not None:
            self.first_turn_label.setText(
                f"First turn: {first_display} (the first listed player). "
                + _FIRST_TURN_TEXT
            )
        else:
            self.first_turn_label.setText(_FIRST_TURN_TEXT)

    def _on_browse(self) -> None:
        directory = QFileDialog.getExistingDirectory(
            self, "Choose the PBEM save folder", self.pbem_dir_edit.text()
        )
        if directory:
            self.pbem_dir_edit.setText(directory)

    def selected_mode(self) -> TurnHandlingMode:
        if self.managed_radio.isChecked():
            return TurnHandlingMode.FULLY_MANAGED
        return TurnHandlingMode.STANDARD

    def accept(self) -> None:
        """Validate through MatchConfig; stay open on validation failure."""
        game_id = self.game_id_edit.text().strip()
        if not self._editing and game_id in self._existing_game_ids:
            QMessageBox.warning(
                self,
                "Match already exists",
                f"A match with game ID {game_id!r} already exists locally. "
                "Existing matches are never silently overwritten.",
            )
            return
        try:
            config = MatchConfig(
                game_id=game_id,
                display_name=self.display_name_edit.text().strip(),
                players=_parse_players(self.players_edit.toPlainText()),
                local_player_id=self.local_player_combo.currentText(),
                launch_profile=self.launch_profile_edit.text().strip() or None,
                mod_name=self.mod_name_edit.text().strip() or None,
                pbem_save_directory=self.pbem_dir_edit.text().strip(),
                save_matching=SaveMatchingRules(
                    filename_glob=self.glob_edit.text().strip() or _DEFAULT_GLOB
                ),
                turn_handling_mode=self.selected_mode(),
            )
        except DomainValidationError as error:
            QMessageBox.warning(self, "Invalid match configuration", str(error))
            return
        self._result = config
        super().accept()

    def result_config(self) -> MatchConfig | None:
        """The validated configuration, or None when not accepted."""
        return self._result
