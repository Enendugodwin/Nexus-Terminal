"""Main window: sidebar navigation, workspace pages and status bar."""

from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from nexus_remote import config
from nexus_remote.resources import application_icon
from nexus_remote.services import AppServices
from nexus_remote.ui.commands_page import CommandsPage
from nexus_remote.ui.dashboard import DashboardPage
from nexus_remote.ui.device_manager import DeviceManagerPage
from nexus_remote.ui.diagnostics_view import DiagnosticsPage
from nexus_remote.ui.powershell_page import PowerShellPage
from nexus_remote.ui.security_center import SecurityCenter
from nexus_remote.ui.settings_view import SettingsPage
from nexus_remote.ui.tasks import run_async
from nexus_remote.ui.theme import apply_theme
from nexus_remote.ui.workspace import WorkspacePage
from nexus_remote.updates import UpdateChecker, UpdateCheckResult

PAGES = (
    ("Dashboard", "Inventory and session health"),
    ("Devices", "Saved device profiles and connection settings"),
    ("Workspace", "Sessions, terminal with assistant, files and remote desktop"),
    ("PowerShell", "Local PowerShell console on this computer"),
    ("Commands", "Save and reuse commands per environment"),
    ("Diagnostics", "Connectivity checks and evidence"),
    ("Security", "Credentials, host keys and audit trail"),
    ("Settings", "Policy, routing restrictions and integrations"),
)
PAGE_INDEX = {name: index for index, (name, _description) in enumerate(PAGES)}


class MainWindow(QMainWindow):
    """The single desktop workspace window."""

    def __init__(self, services: AppServices) -> None:
        super().__init__()
        self.services = services
        self.setWindowTitle(f"{config.APP_NAME} {config.APP_VERSION}")
        self.setWindowIcon(application_icon())
        self.resize(1280, 820)
        self.setMinimumSize(1024, 640)

        # -- sidebar --------------------------------------------------------
        sidebar = QWidget()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(214)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(0, 0, 0, 0)
        brand_row = QHBoxLayout()
        brand_row.setContentsMargins(16, 12, 12, 0)
        brand_row.setSpacing(8)
        brand_icon = QLabel()
        brand_icon.setPixmap(application_icon().pixmap(26, 26))
        brand_icon.setFixedSize(26, 26)
        title = QLabel(config.APP_NAME)
        title.setObjectName("BrandTitle")
        title.setContentsMargins(0, 0, 0, 0)
        brand_row.addWidget(brand_icon)
        brand_row.addWidget(title, 1)
        subtitle = QLabel("Remote access workspace")
        subtitle.setObjectName("SidebarSubtitle")
        self.nav = QListWidget()
        self.nav.setObjectName("NavList")
        for name, _description in PAGES:
            self.nav.addItem(QListWidgetItem(name))
        self.nav.setCurrentRow(0)
        self.nav.currentRowChanged.connect(self._switch_page)
        sidebar_layout.addLayout(brand_row)
        sidebar_layout.addWidget(subtitle)
        sidebar_layout.addWidget(self.nav, 1)
        self.vault_state = QLabel("")
        self.vault_state.setObjectName("Faint")
        self.vault_state.setWordWrap(True)
        self.vault_state.setContentsMargins(14, 8, 14, 12)
        sidebar_layout.addWidget(self.vault_state)

        # -- pages ----------------------------------------------------------
        self.dashboard = DashboardPage(services)
        self.devices = DeviceManagerPage(services)
        self.workspace = WorkspacePage(services)
        self.powershell = PowerShellPage(services)
        self.commands = CommandsPage(services)
        self.diagnostics = DiagnosticsPage(services)
        self.security = SecurityCenter(services)
        self.settings = SettingsPage(services, on_theme_changed=self._apply_theme)
        self.stack = QStackedWidget()
        for page in (
            self.dashboard,
            self.devices,
            self.workspace,
            self.powershell,
            self.commands,
            self.diagnostics,
            self.security,
            self.settings,
        ):
            self.stack.addWidget(page)

        # -- top bar --------------------------------------------------------
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search devices, tags, owners…")
        self.search_edit.setMinimumWidth(280)
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._global_search)

        self.session_label = QLabel("0 active sessions")
        self.session_label.setObjectName("Muted")
        connections_button = QPushButton("Connections")
        connections_button.clicked.connect(lambda: self._go_to(PAGE_INDEX["Workspace"]))
        profile_button = QPushButton("Workspace menu")
        menu = QMenu(self)
        menu.addAction(
            _action("New connection", lambda: self._go_to(PAGE_INDEX["Workspace"]), menu)
        )
        menu.addAction(_action("Assistant beside terminal", self._show_assistant, menu))
        menu.addAction(_action("Local PowerShell", lambda: self._go_to(PAGE_INDEX["PowerShell"]), menu))
        menu.addAction(_action("Saved commands", lambda: self._go_to(PAGE_INDEX["Commands"]), menu))
        menu.addAction(_action("Run diagnostics", lambda: self._go_to(PAGE_INDEX["Diagnostics"]), menu))
        menu.addAction(_action("Security Center", lambda: self._go_to(PAGE_INDEX["Security"]), menu))
        menu.addAction(_action("Settings", lambda: self._go_to(PAGE_INDEX["Settings"]), menu))
        menu.addSeparator()
        menu.addAction(_action("Exit", self.close, menu))
        profile_button.setMenu(menu)

        top_bar = QWidget()
        top_bar.setObjectName("TopBar")
        top_layout = QHBoxLayout(top_bar)
        top_layout.setContentsMargins(16, 8, 16, 8)
        self.page_title = QLabel(PAGES[0][0])
        self.page_title.setObjectName("PageTitle")
        top_layout.addWidget(self.page_title)
        top_layout.addStretch(1)
        top_layout.addWidget(self.search_edit)
        top_layout.addWidget(self.session_label)
        top_layout.addWidget(connections_button)
        top_layout.addWidget(profile_button)

        central = QWidget()
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)
        central_layout.addWidget(top_bar)
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(sidebar)
        body.addWidget(self.stack, 1)
        central_layout.addLayout(body, 1)
        self.setCentralWidget(central)

        # -- status bar -----------------------------------------------------
        self.status_message = QLabel("Ready")
        self.statusBar().addWidget(self.status_message, 1)
        self.status_right = QLabel("")
        self.statusBar().addPermanentWidget(self.status_right)

        # -- wiring ---------------------------------------------------------
        self.devices.connect_requested.connect(self._connect)
        self.workspace.diagnostic_requested.connect(self._show_diagnostics)
        self.workspace.status_message.connect(self._set_status)
        self.commands.send_requested.connect(self._send_command)
        self.commands.status_message.connect(self._set_status)
        self.nav.currentRowChanged.connect(self._refresh_current)

        self._timer = QTimer(self)
        self._timer.setInterval(3000)
        self._timer.timeout.connect(self._refresh_status)
        self._timer.start()
        self._refresh_status()
        self._switch_page(0)

        # Vendor catalog updates are checked quietly after the window is up:
        # being offline is normal and must never block startup.
        if self.services.catalog_auto_check and self.services.catalog_feed_url:
            QTimer.singleShot(4000, lambda: self.commands.check_for_updates(silent=True))
        if self.services.update_auto_check and self.services.update_feed_url:
            QTimer.singleShot(7000, self._check_updates_silently)

    # -- navigation ---------------------------------------------------------
    def _switch_page(self, index: int) -> None:
        if index < 0 or index >= self.stack.count():
            return
        self.stack.setCurrentIndex(index)
        self.page_title.setText(PAGES[index][0])
        self.nav.blockSignals(True)
        self.nav.setCurrentRow(index)
        self.nav.blockSignals(False)
        self._refresh_current(index)

    def _go_to(self, index: int) -> None:
        self.nav.setCurrentRow(index)
        self._switch_page(index)

    def _refresh_current(self, index: int | None = None) -> None:
        index = self.stack.currentIndex() if index is None else index
        page = self.stack.widget(index)
        try:
            if page is self.dashboard:
                self.dashboard.refresh()
            elif page is self.devices:
                self.devices.refresh()
            elif page is self.workspace:
                self.workspace.refresh_devices()
                self.workspace.refresh_rdp_devices()
            elif page is self.commands:
                self.commands.refresh()
            elif page is self.diagnostics:
                self.diagnostics.refresh_devices()
            elif page is self.security:
                self.security.refresh()
            elif page is self.settings:
                self.settings.refresh()
        except Exception as exc:  # pragma: no cover - UI resilience
            self._set_status(f"could not refresh page: {exc}")

    def _refresh_status(self) -> None:
        count = self.services.sessions.count_active()
        self.session_label.setText(f"{count} active session{'s' if count != 1 else ''}")
        self.status_right.setText(
            f"vault: {'persistent' if self.services.credentials.store.persistent else 'in-memory'}"
            f"   |   AI: {self.services.models.model.name}"
        )
        self.vault_state.setText(
            f"Offline-ready\nVault: {self.services.credentials.store.backend_name}"
        )

    # -- actions ------------------------------------------------------------
    def _connect(self, device_id: str, protocol: str) -> None:
        self._go_to(PAGE_INDEX["Workspace"])
        self.workspace.open_connection(device_id, protocol)

    def _show_assistant(self) -> None:
        self._go_to(PAGE_INDEX["Workspace"])
        self.workspace.show_terminal_and_assistant()

    def _send_command(self, command: str, environment: str) -> None:
        """Load a saved command into the console; the operator still presses Run."""
        self._go_to(PAGE_INDEX["Workspace"])
        self.workspace.load_command(command, environment)
        self._set_status("Command loaded into the SSH console - review it, then press Run.")

    def _show_diagnostics(self, host: str, port: int, protocol: str) -> None:
        self._go_to(PAGE_INDEX["Diagnostics"])
        self.diagnostics.set_target(host, port or None, protocol)
        self.diagnostics.run_checks()

    def _global_search(self, text: str) -> None:
        if not text:
            self.devices.search_edit.clear()
            return
        self.devices.search_edit.setText(text)
        self._go_to(PAGE_INDEX["Devices"])

    def _set_status(self, message: str) -> None:
        self.status_message.setText(message)

    # -- appearance / updates ----------------------------------------------
    def _apply_theme(self, name: str) -> None:
        """Apply a theme live and persist the choice."""
        from PySide6.QtWidgets import QApplication

        selected = apply_theme(QApplication.instance(), name)
        self.services.database.set_setting("ui.theme", selected)
        current = self.stack.currentWidget()
        if current is not None:
            current.update()
        self.update()
        self._set_status(f"Theme: {selected}")

    def _check_updates_silently(self) -> None:
        checker = UpdateChecker(self.services.update_feed_url)
        run_async(checker.check, on_result=self._update_checked_silently)

    def _update_checked_silently(self, result: UpdateCheckResult) -> None:
        if result.available and result.info is not None:
            self._set_status(
                f"Update {result.info.version} is available - open Settings > Updates to install it."
            )

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt API
        active = self.services.sessions.count_active()
        if active:
            self._set_status(f"closing {active} session(s)…")
        self.powershell.shutdown()
        self.services.close()
        super().closeEvent(event)


def _action(text: str, callback, parent: QWidget | None = None) -> QAction:
    # The action must have a C++ owner: PySide6 drops an unparented QAction
    # passed to QMenu.addAction(), leaving an empty menu behind.
    action = QAction(text, parent) if parent is not None else QAction(text)
    action.triggered.connect(lambda _checked=False: callback())
    return action
