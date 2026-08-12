"""Application coordinator and GUI entry point.

``RelayApplication`` wires store + storage + RelayClient + worker hub +
window + tray, owns shutdown ordering, and hosts the add/edit/settings
flows. ``main()`` stays thin: it loads configuration (errors surface in the
UI instead of crashing), builds the production adapters, and runs the Qt
event loop. Quitting never terminates Civilization and never touches match
state.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from pathlib import Path

from PySide6.QtWidgets import QApplication, QMessageBox

from civ4_turn_relay.app.config_load import load_global_config
from civ4_turn_relay.app.relay_client import RelayClient
from civ4_turn_relay.domain import GlobalConfig, MatchConfig
from civ4_turn_relay.local import LocalStore
from civ4_turn_relay.process import WindowsProcessSupervisor
from civ4_turn_relay.storage import (
    ParamikoStorage,
    Storage,
    StorageCapabilities,
    StorageEntry,
    StorageTransportError,
)
from civ4_turn_relay.ui.controller import RelayWorkerHub
from civ4_turn_relay.ui.main_window import MainWindow
from civ4_turn_relay.ui.match_dialog import MatchEditDialog
from civ4_turn_relay.ui.settings_dialog import GlobalSettingsDialog
from civ4_turn_relay.ui.tray import RelayTray

_QUIT_QUESTION = (
    "Civilization is still running or a turn operation is in flight.\n\n"
    "Relay will NOT close Civilization and will not change any match "
    "state. Quit anyway?"
)


class _UnconfiguredStorage:
    """Fail-closed Storage placeholder used when no global config loads.

    Every operation raises a transport error so matches surface an honest
    connection problem instead of fabricated remote state.
    """

    _MESSAGE = "global configuration is not loaded; storage is unavailable"

    def capabilities(self) -> StorageCapabilities:
        return StorageCapabilities(
            exclusive_mkdir=False,
            atomic_replace=False,
            atomic_publish_no_replace=False,
            complete_readback=False,
        )

    def mkdir(self, path: str) -> None:
        raise StorageTransportError(self._MESSAGE)

    def write_file(self, path: str, data: bytes, *, overwrite: bool = False) -> None:
        raise StorageTransportError(self._MESSAGE)

    def read_file(self, path: str) -> bytes:
        raise StorageTransportError(self._MESSAGE)

    def list_dir(self, path: str) -> tuple[StorageEntry, ...]:
        raise StorageTransportError(self._MESSAGE)

    def remove_file(self, path: str) -> None:
        raise StorageTransportError(self._MESSAGE)

    def remove_dir(self, path: str) -> None:
        raise StorageTransportError(self._MESSAGE)

    def publish_no_replace(self, source: str, destination: str) -> None:
        raise StorageTransportError(self._MESSAGE)

    def atomic_replace(self, source: str, destination: str) -> None:
        raise StorageTransportError(self._MESSAGE)


class RelayApplication:
    """Coordinator: owns wiring, dialogs, quit flow, and shutdown ordering."""

    def __init__(
        self,
        *,
        client: RelayClient,
        global_config: GlobalConfig | None,
        config_error: str | None = None,
        dotenv_path: Path | None = None,
        env_example_path: Path | None = None,
        poll_interval_seconds: float = 10.0,
        hub: RelayWorkerHub | None = None,
        enable_tray: bool = True,
        config_loader: Callable[[], GlobalConfig] | None = None,
        quit_fn: Callable[[], None] | None = None,
    ) -> None:
        self._client = client
        self._global_config = global_config
        self._config_error = config_error
        self._dotenv_path = dotenv_path
        self._env_example_path = env_example_path
        self._poll_interval_seconds = poll_interval_seconds
        self._config_loader = config_loader
        self._quit_fn = quit_fn if quit_fn is not None else self._default_quit
        self._configs: dict[str, MatchConfig] = {}
        self._shut_down = False

        self.hub = hub if hub is not None else RelayWorkerHub(client)
        self.window = MainWindow(self.hub, save_directory_provider=self._pbem_dir_for)
        self.hub.snapshot_ready.connect(self.window.on_snapshot)
        self.hub.error.connect(self.window.on_error)
        self.window.add_match_requested.connect(self.add_match)
        self.window.edit_match_requested.connect(self.edit_match)
        self.window.settings_requested.connect(self.show_settings)
        self.window.reload_requested.connect(self.reload_config)
        self.window.quit_requested.connect(self.request_quit)

        self.tray: RelayTray | None = None
        if enable_tray and RelayTray.is_available():
            self.tray = RelayTray(on_open=self._show_window, on_quit=self.request_quit)
            self.window.set_tray_available(True)
            app = QApplication.instance()
            if app is not None:
                QApplication.setQuitOnLastWindowClosed(False)

    # ----- lifecycle ----------------------------------------------------

    def start(self) -> None:
        """Open stored matches through the hub, start polling, show the UI."""
        app = QApplication.instance()
        if app is not None:
            # Ensure every quit path (last-window-close, tray, OS) joins the
            # worker before Qt begins tearing down widgets.
            app.aboutToQuit.connect(self.shutdown)
        store = self._client.store
        for game_id in store.list_match_ids():
            try:
                config = store.load_match_config(game_id)
            except Exception as exc:
                self.window.on_error(
                    f"{game_id}: stored match configuration could not be "
                    f"loaded ({type(exc).__name__})"
                )
                continue
            self._configs[game_id] = config
            self.hub.open_match(config)
        self.hub.start_polling(int(self._poll_interval_seconds * 1000))
        if self.tray is not None:
            self.tray.show()
        self.window.show()

    def request_quit(self) -> None:
        """Quit flow: confirm while active, then shut down cleanly."""
        if self._shut_down:
            return
        if self.window.has_active_match():
            answer = QMessageBox.question(
                self.window,
                "Quit Relay?",
                _QUIT_QUESTION,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.shutdown()
        self._quit_fn()

    def shutdown(self) -> None:
        """Stop timers, join the worker, dispose tray/window. Idempotent.

        Never terminates Civilization and never mutates match ownership.
        """
        if self._shut_down:
            return
        self._shut_down = True
        self.window.allow_quit()
        try:
            self.hub.snapshot_ready.disconnect(self.window.on_snapshot)
        except (RuntimeError, TypeError):
            pass
        try:
            self.hub.error.disconnect(self.window.on_error)
        except (RuntimeError, TypeError):
            pass
        tray = self.tray
        self.tray = None
        if tray is not None:
            tray.dispose()
        self.hub.shutdown()
        self.window.close()

    # ----- coordinator actions ------------------------------------------

    def add_match(self) -> None:
        dialog = MatchEditDialog(
            existing_game_ids=self._client.store.list_match_ids(),
            parent=self.window,
        )
        if dialog.exec() != int(MatchEditDialog.DialogCode.Accepted):
            return
        config = dialog.result_config()
        if config is None:
            return
        self._configs[config.game_id] = config
        answer = QMessageBox.question(
            self.window,
            "Initialize remote match?",
            "Initialize the remote match now? Choose No to only store the "
            "local configuration.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer == QMessageBox.StandardButton.Yes:
            # initialize_or_join persists the config and opens the session.
            self.hub.initialize_match(config)
        else:
            self.hub.open_match(config)

    def edit_match(self, game_id: str) -> None:
        config = self._configs.get(game_id)
        if config is None:
            try:
                config = self._client.store.load_match_config(game_id)
            except Exception as exc:
                self.window.on_error(
                    f"{game_id}: match configuration could not be loaded "
                    f"({type(exc).__name__})"
                )
                return
        dialog = MatchEditDialog(existing=config, parent=self.window)
        if dialog.exec() != int(MatchEditDialog.DialogCode.Accepted):
            return
        updated = dialog.result_config()
        if updated is None:
            return
        self._configs[updated.game_id] = updated
        self.hub.open_match(updated)

    def show_settings(self) -> None:
        dialog = GlobalSettingsDialog(
            config=self._global_config,
            error_text=self._config_error,
            dotenv_path=self._dotenv_path,
            env_example_path=self._env_example_path,
            parent=self.window,
        )
        dialog.reload_requested.connect(self.reload_config)
        dialog.exec()

    def reload_config(self) -> None:
        """Reload global config; connection settings apply after a restart."""

        def _load_default() -> GlobalConfig:
            return load_global_config(dotenv_path=self._dotenv_path)

        loader = self._config_loader or _load_default
        try:
            self._global_config = loader()
            self._config_error = None
        except Exception as exc:
            self._config_error = str(exc)
            QMessageBox.warning(
                self.window,
                "Configuration error",
                f"The configuration could not be reloaded:\n{exc}",
            )
            return
        QMessageBox.information(
            self.window,
            "Configuration reloaded",
            "The configuration was reloaded. Changed connection settings "
            "take effect after restarting Relay.",
        )

    # ----- helpers -------------------------------------------------------

    def _pbem_dir_for(self, game_id: str) -> str:
        config = self._configs.get(game_id)
        return "" if config is None else config.pbem_save_directory

    def _show_window(self) -> None:
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()

    @staticmethod
    def _default_quit() -> None:
        app = QApplication.instance()
        if app is not None:
            app.quit()


def user_data_dir() -> Path:
    """Per-user Relay data root (config, matches, installation identity).

    On Windows this is ``%APPDATA%\\civ4-turn-relay``. When ``APPDATA`` is
    unset (non-Windows / tests), ``~/civ4-turn-relay`` is used. Installers
    and packaging docs MUST preserve this directory on upgrade and uninstall.
    """
    return Path(os.environ.get("APPDATA", str(Path.home()))) / "civ4-turn-relay"


def _user_data_dir() -> Path:
    """Backward-compatible alias for :func:`user_data_dir`."""
    return user_data_dir()


def _find_dotenv(data_dir: Path) -> Path | None:
    for candidate in (data_dir / ".env", Path.cwd() / ".env"):
        if candidate.is_file():
            return candidate
    return None


def _find_env_example() -> Path | None:
    candidate = Path.cwd() / ".env.example"
    return candidate if candidate.is_file() else None


def main() -> int:
    """GUI entry point (``civ4-turn-relay-ui``)."""
    app = QApplication(sys.argv)
    app.setApplicationName("civ4-turn-relay")
    app.setOrganizationName("civ4-turn-relay")

    data_dir = user_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)
    dotenv_path = _find_dotenv(data_dir)
    env_example_path = _find_env_example()

    config: GlobalConfig | None = None
    config_error: str | None = None
    try:
        config = load_global_config(dotenv_path=dotenv_path)
    except Exception as exc:
        config_error = str(exc)
        QMessageBox.warning(
            None,
            "Configuration error",
            "The global configuration could not be loaded. Relay opens "
            f"without a server connection.\n\n{exc}",
        )
        settings = GlobalSettingsDialog(
            config=None,
            error_text=config_error,
            dotenv_path=dotenv_path,
            env_example_path=env_example_path,
        )
        settings.exec()

    storage: Storage
    startup_error: str | None = None
    if config is not None:
        try:
            storage = ParamikoStorage(config)
        except Exception as exc:
            startup_error = (
                "the storage adapter could not be constructed "
                f"({type(exc).__name__}); Relay opens without a connection"
            )
            storage = _UnconfiguredStorage()
    else:
        storage = _UnconfiguredStorage()

    client = RelayClient(
        store=LocalStore(data_dir),
        storage=storage,
        poll_interval_seconds=(
            float(config.poll_interval_seconds) if config is not None else 10.0
        ),
        process_supervisor=WindowsProcessSupervisor(),
        civ4_executable=config.civ4_executable if config is not None else None,
        owns_storage=True,
    )

    relay = RelayApplication(
        client=client,
        global_config=config,
        config_error=config_error,
        dotenv_path=dotenv_path,
        env_example_path=env_example_path,
        poll_interval_seconds=(
            float(config.poll_interval_seconds) if config is not None else 10.0
        ),
    )
    if startup_error is not None:
        relay.window.on_error(startup_error)
    relay.start()
    # aboutToQuit → shutdown is wired in start(); call again after exec for
    # idempotent cleanup if the loop exited without emitting aboutToQuit.
    exit_code = app.exec()
    relay.shutdown()
    return int(exit_code)
