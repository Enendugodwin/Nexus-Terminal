"""Assistant panel: autocomplete, command memory, error rules and playbooks.

Everything on this page works offline. When no AI provider is configured the
page says so plainly and the deterministic features stay fully usable.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from nexus_remote.assistant.autocomplete import SuggestionSource
from nexus_remote.assistant.command_history import CommandStatus
from nexus_remote.services import AppServices
from nexus_remote.ui.command_input import SuggestionLineEdit


class AssistantPanel(QWidget):
    """Deterministic assistance: suggestions, history, error rules, playbooks.

    The same widget is used as a full page and as the compact side panel that
    sits beside the open terminal. ``compact`` switches the inner splitters to
    a vertical layout and trims secondary columns so it stays readable at
    roughly one third of the window width.
    """

    def __init__(
        self,
        services: AppServices,
        parent: QWidget | None = None,
        *,
        compact: bool = False,
    ) -> None:
        super().__init__(parent)
        self.services = services
        self.compact = compact
        if compact:
            self.setMinimumWidth(300)

        title = QLabel("Assistant")
        title.setObjectName("CardTitle")
        self.context_label = QLabel("No terminal attached")
        self.context_label.setObjectName("Faint")
        self.context_label.setWordWrap(True)

        header = QVBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(1)
        header.addWidget(title)
        header.addWidget(self.context_label)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.addTab(self._build_commands_tab(), "Commands")
        self.tabs.addTab(self._build_history_tab(), "History")
        self.tabs.addTab(self._build_errors_tab(), "Errors & playbooks")
        self.tabs.addTab(self._build_ai_tab(), "Optional AI")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(*((8, 6, 8, 6) if compact else (12, 10, 12, 10)))
        layout.setSpacing(4)
        layout.addLayout(header)
        layout.addWidget(self.tabs, 1)
        self.refresh()

    # -- commands -----------------------------------------------------------
    def _build_commands_tab(self) -> QWidget:
        page = QWidget()
        self.environment_combo = QComboBox()
        for environment in ("bash", "powershell", "cmd", "cisco", "panos"):
            self.environment_combo.addItem(environment, environment)
        self.environment_combo.currentIndexChanged.connect(self._update_suggestions)

        self.prefix_edit = SuggestionLineEdit()
        self.prefix_edit.setPlaceholderText("start typing a command… (Tab completes)")
        self.prefix_edit.setObjectName("Mono")
        self.prefix_edit.setMinimumHeight(38)
        self.prefix_edit.setMinimumWidth(180)
        self.prefix_edit.setClearButtonEnabled(True)
        self.prefix_edit.setToolTip(
            "Suggestions appear inline as you type. Tab or → completes, ↑ ↓ browse, Enter takes the "
            "highlighted suggestion."
        )
        self.prefix_edit.textEdited.connect(self._update_suggestions)
        self.prefix_edit.accepted.connect(lambda _text: self.suggestion_list.setCurrentRow(-1))
        self.prefix_edit.navigate.connect(self._navigate_suggestions)
        self.prefix_edit.returnPressed.connect(self._accept_suggestion)

        self.suggestion_list = QListWidget()
        self.suggestion_list.itemDoubleClicked.connect(self._explain_selected)
        self.suggestion_list.currentItemChanged.connect(
            lambda current, _previous: self._explain_selected(current) if current else None
        )

        self.explanation_view = QPlainTextEdit()
        self.explanation_view.setReadOnly(True)
        self.explanation_view.setObjectName("Mono")
        self.explanation_view.setMaximumHeight(140)
        self.explanation_view.setPlaceholderText(
            "Select a suggestion to see its description and provenance."
        )

        save_snippet = QPushButton("Save prefix as snippet")
        save_snippet.clicked.connect(self.save_snippet)

        self.snippet_list = QListWidget()
        refresh_button = QPushButton("Refresh")
        refresh_button.clicked.connect(self.refresh)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(QLabel("Environment"))
        left_layout.addWidget(self.environment_combo)
        left_layout.addWidget(QLabel("Prefix"))
        left_layout.addWidget(self.prefix_edit)
        left_layout.addWidget(self.suggestion_list, 1)
        left_layout.addWidget(self.explanation_view)
        left_layout.addWidget(save_snippet)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        header = QHBoxLayout()
        title = QLabel("Saved snippets")
        title.setObjectName("CardTitle")
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(refresh_button)
        right_layout.addLayout(header)
        right_layout.addWidget(self.snippet_list, 1)
        delete_snippet = QPushButton("Delete selected snippet")
        delete_snippet.clicked.connect(self.delete_snippet)
        right_layout.addWidget(delete_snippet)

        splitter = QSplitter(
            Qt.Orientation.Vertical if self.compact else Qt.Orientation.Horizontal
        )
        splitter.addWidget(left)
        splitter.addWidget(right)
        if self.compact:
            splitter.setSizes([420, 180])

        note = QLabel(
            "Suggestions are environment-specific: a Cisco command is never offered for bash. "
            "Provenance is always shown - verified, history, documentation, inferred or AI-generated."
        )
        note.setObjectName("Faint")
        note.setWordWrap(True)
        note.setVisible(not self.compact)

        layout = QVBoxLayout(page)
        layout.addWidget(splitter, 1)
        layout.addWidget(note)
        return page

    def _update_suggestions(self) -> None:
        environment = self.environment_combo.currentData()
        prefix = self.prefix_edit.text()
        history = [record.command for record in self.services.history.recent(200, environment=environment)]
        suggestions = self.services.autocomplete.suggest(prefix, environment, history=history, limit=12)
        self.suggestion_list.clear()
        for suggestion in suggestions:
            item = QListWidgetItem(suggestion.display())
            item.setData(Qt.ItemDataRole.UserRole, suggestion.text)
            if suggestion.source is SuggestionSource.AI_GENERATED:
                item.setForeground(Qt.GlobalColor.yellow)
            self.suggestion_list.addItem(item)
        self.suggestion_list.setCurrentRow(-1)
        self.prefix_edit.set_suggestion(suggestions[0].text if suggestions else None)

    def _navigate_suggestions(self, delta: int) -> None:
        count = self.suggestion_list.count()
        if not count:
            return
        row = self.suggestion_list.currentRow()
        row = 0 if row < 0 else (row + delta) % count
        self.suggestion_list.setCurrentRow(row)
        item = self.suggestion_list.item(row)
        self.prefix_edit.set_suggestion(str(item.data(Qt.ItemDataRole.UserRole)))

    def _accept_suggestion(self) -> None:
        item = self.suggestion_list.currentItem()
        if item is not None:
            self.prefix_edit.setText(str(item.data(Qt.ItemDataRole.UserRole)))
        elif not self.prefix_edit.accept_suggestion():
            return
        self.prefix_edit.setCursorPosition(len(self.prefix_edit.text()))
        self.prefix_edit.clear_suggestion()
        self.suggestion_list.setCurrentRow(-1)

    def _explain_selected(self, item: QListWidgetItem) -> None:
        command = str(item.data(Qt.ItemDataRole.UserRole))
        environment = self.environment_combo.currentData()
        self.explanation_view.setPlainText(self.services.autocomplete.explain(command, environment))

    def save_snippet(self) -> None:  # pragma: no cover - needs user input
        prefix = self.prefix_edit.text().strip()
        if not prefix:
            QMessageBox.information(self, "Nothing to save", "Type the command to save first.")
            return
        environment = self.environment_combo.currentData()
        risk = self.services.policy.classify(prefix, environment)
        self.services.snippets.add(
            prefix[:40], prefix, environment=environment, risk=risk.value, verified=False
        )
        self.refresh()

    def delete_snippet(self) -> None:  # pragma: no cover - needs user input
        item = self.snippet_list.currentItem()
        if item is None:
            return
        self.services.snippets.delete(str(item.data(Qt.ItemDataRole.UserRole)))
        self.refresh()

    # -- history ------------------------------------------------------------
    def _build_history_tab(self) -> QWidget:
        page = QWidget()
        self.history_filter = QLineEdit()
        self.history_filter.setPlaceholderText("search command history")
        self.history_filter.textChanged.connect(self.refresh)
        save_button = QPushButton("Save as snippet")
        save_button.clicked.connect(self.snippet_from_history)
        confirm_button = QPushButton("Mark confirmed")
        confirm_button.clicked.connect(lambda: self._mark_history(CommandStatus.CONFIRMED))
        fail_button = QPushButton("Mark failed")
        fail_button.clicked.connect(lambda: self._mark_history(CommandStatus.FAILED))

        self.history_table = QTableWidget(0, 5)
        self.history_table.setHorizontalHeaderLabels(["Time", "Command", "Environment", "Status", "Source"])
        self.history_table.verticalHeader().setVisible(False)
        self.history_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.history_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.history_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.history_table.setAlternatingRowColors(True)
        if self.compact:
            # Time, command and status are the useful columns at panel width.
            self.history_table.setColumnHidden(2, True)
            self.history_table.setColumnHidden(4, True)

        controls = QHBoxLayout()
        controls.addWidget(self.history_filter, 1)
        controls.addWidget(save_button)
        controls.addWidget(confirm_button)
        controls.addWidget(fail_button)

        self.history_stats = QLabel("")
        self.history_stats.setObjectName("Faint")

        layout = QVBoxLayout(page)
        layout.addLayout(controls)
        layout.addWidget(self.history_table, 1)
        layout.addWidget(self.history_stats)
        layout.addWidget(
            _note(
                "A suggestion is not a solution: only commands you ran and confirmed are treated as "
                "working knowledge. Unconfirmed suggestions are never replayed as proven fixes."
            )
        )
        return page

    def _mark_history(self, status: CommandStatus) -> None:  # pragma: no cover - needs user input
        record_id = self._selected_history_id()
        if record_id is None:
            return
        self.services.history.mark(record_id, status)
        self.refresh()

    def snippet_from_history(self) -> None:  # pragma: no cover - needs user input
        record_id = self._selected_history_id()
        if record_id is None:
            return
        for record in self.services.history.recent(200):
            if record.id == record_id:
                self.services.snippets.from_history(record)
                self.refresh()
                return

    def _selected_history_id(self) -> int | None:
        rows = self.history_table.selectionModel().selectedRows() if self.history_table.selectionModel() else []
        if not rows:
            return None
        item = self.history_table.item(rows[0].row(), 0)
        return int(item.data(Qt.ItemDataRole.UserRole)) if item else None

    # -- errors -------------------------------------------------------------
    def _build_errors_tab(self) -> QWidget:
        page = QWidget()
        self.error_input = QPlainTextEdit()
        self.error_input.setObjectName("Mono")
        self.error_input.setPlaceholderText(
            "Paste an error message or command output here. Remote text is treated as untrusted data."
        )
        self.error_input.setMaximumHeight(140)
        self.error_protocol = QComboBox()
        for protocol in ("any", "ssh", "sftp", "rdp", "smb"):
            self.error_protocol.addItem(protocol, protocol)
        diagnose_button = QPushButton("Diagnose")
        diagnose_button.setObjectName("Primary")
        diagnose_button.clicked.connect(self.diagnose)

        self.diagnosis_view = QPlainTextEdit()
        self.diagnosis_view.setReadOnly(True)
        self.diagnosis_view.setObjectName("Mono")

        self.playbook_list = QListWidget()
        for playbook in self.services.playbooks.all():
            item = QListWidgetItem(playbook.title)
            item.setData(Qt.ItemDataRole.UserRole, playbook.key)
            self.playbook_list.addItem(item)
        self.playbook_list.itemClicked.connect(self._show_playbook)
        self.playbook_view = QPlainTextEdit()
        self.playbook_view.setReadOnly(True)
        self.playbook_view.setObjectName("Mono")

        controls = QHBoxLayout()
        controls.addWidget(QLabel("Protocol"))
        controls.addWidget(self.error_protocol)
        controls.addWidget(diagnose_button)
        controls.addStretch(1)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(QLabel("Observed error"))
        left_layout.addWidget(self.error_input)
        left_layout.addLayout(controls)
        left_layout.addWidget(self.diagnosis_view, 1)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(QLabel("Troubleshooting playbooks"))
        right_layout.addWidget(self.playbook_list)
        right_layout.addWidget(self.playbook_view, 1)

        splitter = QSplitter(
            Qt.Orientation.Vertical if self.compact else Qt.Orientation.Horizontal
        )
        splitter.addWidget(left)
        splitter.addWidget(right)
        if self.compact:
            splitter.setSizes([360, 220])

        note = QLabel(
            "Rules are deterministic and offline. Output from remote systems is never interpreted as "
            "instructions, and no proposed command runs without your explicit approval."
        )
        note.setObjectName("Faint")
        note.setWordWrap(True)
        note.setVisible(not self.compact)

        layout = QVBoxLayout(page)
        layout.addWidget(splitter, 1)
        layout.addWidget(note)
        return page

    def diagnose(self) -> None:
        output = self.error_input.toPlainText()
        protocol = self.error_protocol.currentData()
        diagnosis = self.services.errors.diagnose(
            output, self.environment_combo.currentData(), None if protocol == "any" else protocol
        )
        if diagnosis is None:
            matches = self.services.playbooks.match(output)
            if matches:
                self.diagnosis_view.setPlainText(
                    "No specific rule matched. Related playbooks:\n\n"
                    + "\n\n".join(playbook.render() for playbook in matches[:2])
                )
            else:
                self.diagnosis_view.setPlainText(
                    "No matching rule. The error text may be from an application rather than the "
                    "connection layer; the current diagnostics for this device are still worth running."
                )
            return
        self.diagnosis_view.setPlainText(diagnosis.render())

    def _show_playbook(self, item: QListWidgetItem) -> None:
        self.playbook_view.setPlainText(self.services.playbooks.render(str(item.data(Qt.ItemDataRole.UserRole))))

    # -- AI -----------------------------------------------------------------
    def _build_ai_tab(self) -> QWidget:
        page = QWidget()
        self.ai_status = QLabel("")
        self.ai_status.setWordWrap(True)
        model_edit = QLineEdit(self.services.database.get_setting("ai.model", "llama3.1") or "")
        host_edit = QLineEdit(self.services.database.get_setting("ai.host", "http://127.0.0.1:11434") or "")
        test_button = QPushButton("Test provider")
        test_button.clicked.connect(lambda: self._test_provider(host_edit.text(), model_edit.text()))

        layout = QVBoxLayout(page)
        layout.addWidget(self.ai_status)
        form = QHBoxLayout()
        form.addWidget(QLabel("Ollama endpoint"))
        form.addWidget(host_edit, 1)
        form.addWidget(QLabel("Model"))
        form.addWidget(model_edit, 1)
        form.addWidget(test_button)
        layout.addLayout(form)
        ai_note = _note(
            "AI is optional. Terminal output, banners and remote logs are untrusted input: the model "
            "may summarise them, but the deterministic policy engine decides what is allowed, and "
            "AI-generated commands are labelled and never run automatically."
        )
        ai_note.setVisible(not self.compact)
        layout.addWidget(ai_note)
        layout.addStretch(1)
        return page

    def _test_provider(self, host: str, model: str) -> None:  # pragma: no cover - needs a local model
        from nexus_remote.assistant.model_adapters import OllamaModel

        provider = OllamaModel(model=model or "llama3.1", host=host or "http://127.0.0.1:11434")
        available = provider.available()
        self.ai_status.setText(
            f"{provider.name} at {provider.host}: {'reachable' if available else 'not reachable'}"
            + (f" (model {provider.model})" if available else " - deterministic features remain available")
        )

    # -- context ------------------------------------------------------------
    def set_context(self, text: str) -> None:
        """Show which terminal the assistant is working alongside."""
        self.context_label.setText(text or "No terminal attached")

    def set_environment(self, environment: str | None) -> None:
        """Follow the attached session's shell family."""
        if not environment or environment == "unknown":
            return
        index = self.environment_combo.findData(environment)
        if index >= 0:
            self.environment_combo.setCurrentIndex(index)

    # -- refresh ------------------------------------------------------------
    def refresh(self) -> None:
        self._update_suggestions()
        environment = self.environment_combo.currentData()
        snippets = self.services.snippets.list(environment=environment if environment != "unknown" else None)
        self.snippet_list.clear()
        for snippet in snippets:
            item = QListWidgetItem(snippet.label)
            item.setData(Qt.ItemDataRole.UserRole, snippet.id)
            self.snippet_list.addItem(item)

        if hasattr(self, "history_table"):
            records = self.services.history.search(self.history_filter.text(), limit=200)
            self.history_table.setRowCount(len(records))
            for row, record in enumerate(records):
                values = [record.ts.replace("T", " "), record.command, record.environment, record.status, record.source]
                for column, value in enumerate(values):
                    item = QTableWidgetItem(str(value))
                    if column == 0:
                        item.setData(Qt.ItemDataRole.UserRole, record.id)
                    self.history_table.setItem(row, column, item)
            stats = self.services.history.stats()
            self.history_stats.setText(
                " | ".join(f"{key}: {value}" for key, value in sorted(stats.items()))
                + "   (confirmed entries are the only ones treated as proven)"
            )

        provider = self.services.models
        self.ai_status.setText(
            f"Provider: {provider.model.name}"
            + ("" if provider.available() else "  - not configured; suggestion engine, history, rules and "
               "playbooks all work offline")
        )


def _note(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("Faint")
    label.setWordWrap(True)
    return label
