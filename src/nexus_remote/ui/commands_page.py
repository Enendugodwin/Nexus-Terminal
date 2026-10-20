"""Saved commands: the operator's own reusable command library.

Commands are stored locally per environment with a name, description, tags and
the policy's risk classification. They are never executed from this page: a
saved command is *sent to the SSH console*, where it is reviewed and the policy
gate runs exactly as it does for a typed command.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from nexus_remote.assistant.snippets import Snippet
from nexus_remote.services import AppServices
from nexus_remote.ui.command_input import SuggestionLineEdit

ENVIRONMENTS = ("bash", "powershell", "cmd", "cisco", "panos", "unknown")
COLUMNS = ("Name", "Command", "Environment", "Risk", "Verified", "Tags", "Updated")


class CommandDialog(QDialog):
    """Create or edit one saved command, with inline completion and live risk."""

    def __init__(
        self,
        services: AppServices,
        snippet: Snippet | None = None,
        *,
        command: str = "",
        environment: str = "bash",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.services = services
        self.snippet = snippet
        self.saved: Snippet | None = None
        self.setWindowTitle("Edit saved command" if snippet else "Save command")
        self.setMinimumWidth(560)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("Name it, e.g. “Restart nginx”")
        self.name_edit.setClearButtonEnabled(True)

        self.command_edit = SuggestionLineEdit()
        self.command_edit.setObjectName("Mono")
        self.command_edit.setMinimumHeight(34)
        self.command_edit.setPlaceholderText("systemctl restart nginx")
        self.command_edit.textEdited.connect(self._on_command_edited)
        self.command_edit.setClearButtonEnabled(True)

        self.environment_combo = QComboBox()
        for value in ENVIRONMENTS:
            self.environment_combo.addItem(value, value)
        self.environment_combo.currentIndexChanged.connect(self._on_environment_changed)

        self.description_edit = QPlainTextEdit()
        self.description_edit.setObjectName("Mono")
        self.description_edit.setPlaceholderText("When and why to use it (optional)")
        self.description_edit.setMaximumHeight(90)

        self.tags_edit = QLineEdit()
        self.tags_edit.setPlaceholderText("comma-separated, e.g. nginx, web")

        self.verified_check = QCheckBox("I have run this exact command successfully")
        self.risk_label = QLabel("")
        self.risk_label.setObjectName("Faint")

        form = QFormLayout()
        form.addRow("Name", self.name_edit)
        form.addRow("Command", self.command_edit)
        form.addRow("Environment", self.environment_combo)
        form.addRow("Description", self.description_edit)
        form.addRow("Tags", self.tags_edit)
        form.addRow("", self.verified_check)
        form.addRow("Risk", self.risk_label)

        note = QLabel(
            "Saved commands are text, not actions: sending one fills the console input and the "
            "policy gate still runs when you press Run."
        )
        note.setObjectName("Faint")
        note.setWordWrap(True)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.save_button = buttons.button(QDialogButtonBox.StandardButton.Save)

        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(note)
        layout.addWidget(buttons)

        # -- prefill ---------------------------------------------------------
        if snippet is not None:
            self.name_edit.setText(snippet.name)
            self.command_edit.setText(snippet.command)
            self.environment_combo.setCurrentIndex(
                max(self.environment_combo.findData(snippet.environment), 0)
            )
            self.description_edit.setPlainText(snippet.description)
            self.tags_edit.setText(", ".join(snippet.tags))
            self.verified_check.setChecked(snippet.verified)
        else:
            self.command_edit.setText(command)
            self.environment_combo.setCurrentIndex(
                max(self.environment_combo.findData(environment), 0)
            )
        self.command_edit.setCursorPosition(len(self.command_edit.text()))
        self._refresh_risk()
        self._suggest()

    # -- live feedback -------------------------------------------------------
    def _current_environment(self) -> str:
        return self.environment_combo.currentData() or "unknown"

    def _refresh_risk(self) -> None:
        command = self.command_edit.text().strip()
        if not command:
            self.risk_label.setText("type a command to see its classification")
            return
        risk = self.services.policy.classify(command, self._current_environment())
        self.risk_label.setText(
            f"{risk.value} - the console re-checks this before running anything"
        )

    def _on_command_edited(self, _text: str) -> None:
        self._refresh_risk()
        self._suggest()

    def _on_environment_changed(self) -> None:
        self._refresh_risk()
        self._suggest()

    def _suggest(self) -> None:
        environment = self._current_environment()
        history = [
            record.command for record in self.services.history.recent(100, environment=environment)
        ]
        suggestions = self.services.autocomplete.suggest(
            self.command_edit.text(), environment, history=history, limit=6
        )
        self.command_edit.set_suggestion(suggestions[0].text if suggestions else None)

    # -- persistence ---------------------------------------------------------
    def _tags(self) -> list[str]:
        return sorted({tag.strip() for tag in self.tags_edit.text().split(",") if tag.strip()})

    def accept(self) -> None:  # noqa: N802 - Qt API
        command = self.command_edit.text().strip()
        if not command:
            QMessageBox.warning(self, "Nothing to save", "A saved command needs a command.")
            self.command_edit.setFocus()
            return
        environment = self._current_environment()
        name = self.name_edit.text().strip() or command[:40]
        risk = self.services.policy.classify(command, environment).value
        description = self.description_edit.toPlainText().strip()
        tags = self._tags()
        verified = self.verified_check.isChecked()
        if self.snippet is None:
            self.saved = self.services.snippets.add(
                name,
                command,
                environment=environment,
                description=description,
                tags=tags,
                risk=risk,
                verified=verified,
            )
        else:
            snippet = self.snippet
            snippet.name = name
            snippet.command = command
            snippet.environment = environment
            snippet.description = description
            snippet.tags = tags
            snippet.risk = risk
            snippet.verified = verified
            self.saved = self.services.snippets.update(snippet)
        super().accept()


class CommandsPage(QWidget):
    """The command library: search, edit, and send commands to the console."""

    send_requested = Signal(str, str)  # command, environment

    def __init__(self, services: AppServices, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.services = services
        self._snippets: list[Snippet] = []

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search saved commands, tags or descriptions…")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self.refresh)

        self.environment_filter = QComboBox()
        self.environment_filter.addItem("any environment", "")
        for value in ENVIRONMENTS:
            self.environment_filter.addItem(value, value)
        self.environment_filter.currentIndexChanged.connect(self.refresh)

        self.send_button = QPushButton("Send to console")
        self.send_button.clicked.connect(self.send_selected)
        self.new_button = QPushButton("New command")
        self.new_button.setObjectName("Primary")
        self.new_button.clicked.connect(self.add_command)
        self.edit_button = QPushButton("Edit")
        self.edit_button.clicked.connect(self.edit_selected)
        self.copy_button = QPushButton("Copy")
        self.copy_button.clicked.connect(self.copy_selected)
        self.delete_button = QPushButton("Delete")
        self.delete_button.setObjectName("Danger")
        self.delete_button.clicked.connect(self.delete_selected)

        toolbar = QHBoxLayout()
        toolbar.addWidget(self.search_edit, 2)
        toolbar.addWidget(self.environment_filter, 1)
        toolbar.addWidget(self.send_button)
        toolbar.addWidget(self.new_button)
        toolbar.addWidget(self.edit_button)
        toolbar.addWidget(self.copy_button)
        toolbar.addWidget(self.delete_button)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)  # the command itself
        self.table.setAlternatingRowColors(True)
        self.table.doubleClicked.connect(lambda _index: self.edit_selected())
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)

        self.status_label = QLabel("")
        self.status_label.setObjectName("Faint")

        note = QLabel(
            "Saved commands stay on this machine. “Send to console” only fills the SSH console "
            "input - nothing runs until you press Run there, and the policy gate applies then."
        )
        note.setObjectName("Faint")
        note.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.addLayout(toolbar)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.status_label)
        layout.addWidget(note)

        self.refresh()

    # -- data ---------------------------------------------------------------
    def refresh(self, select: str | None = None) -> None:
        if select is None:
            selected = self._selected_snippet()
            select = selected.id if selected else None
        environment = self.environment_filter.currentData() or None
        self._snippets = self.services.snippets.list(
            self.search_edit.text(), environment=environment
        )
        self.table.setRowCount(len(self._snippets))
        for row, snippet in enumerate(self._snippets):
            values = [
                snippet.name,
                snippet.command,
                snippet.environment,
                snippet.risk,
                "yes" if snippet.verified else "no",
                ", ".join(snippet.tags),
                snippet.updated_at[:16].replace("T", " "),
            ]
            for column, value in enumerate(values):
                item = QTableWidgetItem(str(value))
                if column == 0:
                    item.setData(Qt.ItemDataRole.UserRole, snippet.id)
                if column == 1:
                    item.setToolTip(snippet.command)
                self.table.setItem(row, column, item)
            if snippet.id == select:
                self.table.selectRow(row)
        if self._snippets:
            verified = sum(1 for snippet in self._snippets if snippet.verified)
            self.status_label.setText(
                f"{len(self._snippets)} saved command(s), {verified} verified by you. "
                "Verified means you confirmed it ran successfully - not that it is safe everywhere."
            )
        else:
            self.status_label.setText(
                "No saved commands yet. Press “New command”, or save the console line from the "
                "SSH console tab."
            )

    def _selected_snippet(self) -> Snippet | None:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows:
            return None
        row = rows[0].row()
        if 0 <= row < len(self._snippets):
            return self._snippets[row]
        return None

    # -- actions -------------------------------------------------------------
    def add_command(self) -> None:
        dialog = CommandDialog(self.services, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.saved is not None:
            self.refresh(select=dialog.saved.id)
            self.status_label.setText(f"Saved “{dialog.saved.name}”.")

    def edit_selected(self) -> None:
        snippet = self._selected_snippet()
        if snippet is None:
            self.status_label.setText("Select a command to edit first.")
            return
        dialog = CommandDialog(self.services, snippet, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted and dialog.saved is not None:
            self.refresh(select=dialog.saved.id)
            self.status_label.setText(f"Updated “{dialog.saved.name}”.")

    def delete_selected(self) -> None:
        snippet = self._selected_snippet()
        if snippet is None:
            self.status_label.setText("Select a command to delete first.")
            return
        answer = QMessageBox.question(
            self,
            "Delete saved command",
            f"Delete “{snippet.name}”?\n\n{snippet.command}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.services.snippets.delete(snippet.id)
        self.refresh()
        self.status_label.setText(f"Deleted “{snippet.name}”.")

    def send_selected(self) -> None:
        snippet = self._selected_snippet()
        if snippet is None:
            self.status_label.setText("Select a command to send to the console first.")
            return
        self.send_requested.emit(snippet.command, snippet.environment)
        self.status_label.setText(
            f"Loaded “{snippet.name}” into the SSH console - review it there, then press Run."
        )

    def copy_selected(self) -> None:
        snippet = self._selected_snippet()
        if snippet is None:
            self.status_label.setText("Select a command to copy first.")
            return
        QApplication.clipboard().setText(snippet.command)
        self.status_label.setText(f"Copied the command for “{snippet.name}”.")

    def _context_menu(self, position) -> None:
        from PySide6.QtWidgets import QMenu

        item = self.table.itemAt(position)
        if item is not None:
            self.table.selectRow(item.row())
        menu = QMenu(self)
        menu.addAction("Send to console", self.send_selected)
        menu.addAction("Edit…", self.edit_selected)
        menu.addAction("Copy command", self.copy_selected)
        menu.addSeparator()
        menu.addAction("Delete", self.delete_selected)
        menu.exec(self.table.viewport().mapToGlobal(position))
