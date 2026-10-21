"""Assistant panel: autocomplete, command memory, error rules and playbooks.

Everything on this page works offline. When no AI provider is configured the
page says so plainly and the deterministic features stay fully usable.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
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
from nexus_remote.assistant.context import summarise
from nexus_remote.assistant.model_adapters import (
    ModelResponse,
    build_prompt,
    suggestion_from_response,
)
from nexus_remote.services import AppServices
from nexus_remote.ui.command_input import SuggestionLineEdit
from nexus_remote.ui.tasks import run_async


class AssistantPanel(QWidget):
    """Deterministic assistance: suggestions, history, error rules, playbooks.

    The same widget is used as a full page and as the compact side panel that
    sits beside the open terminal. ``compact`` switches the inner splitters to
    a vertical layout and trims secondary columns so it stays readable at
    roughly one third of the window width.
    """

    send_shell_requested = Signal(str)  # command to insert into the open shell
    insert_text_requested = Signal(str)  # selected text to insert safely
    status_message = Signal(str)

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
        self._context_provider = None  # callable returning a terminal snapshot
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
        self.prefix_edit.textChanged.connect(self._update_prefix_actions)

        self.send_shell_button = QPushButton("Send" if self.compact else "Send to shell")
        self.send_shell_button.setToolTip(
            "Insert the command into the open interactive shell without pressing Enter."
        )
        self.send_shell_button.clicked.connect(self.send_to_shell)
        self.copy_button = QPushButton("Copy")
        self.copy_button.setToolTip("Copy the command to the clipboard.")
        self.copy_button.clicked.connect(self.copy_prefix)
        self._update_prefix_actions("")

        self.suggestion_list = QListWidget()
        self.suggestion_list.setMinimumHeight(200 if self.compact else 240)
        self.suggestion_list.setAlternatingRowColors(True)
        self.suggestion_list.itemDoubleClicked.connect(self._explain_selected)
        self.suggestion_list.currentItemChanged.connect(
            lambda current, _previous: self._explain_selected(current) if current else None
        )

        self.explanation_view = QPlainTextEdit()
        self.explanation_view.setReadOnly(True)
        self.explanation_view.setObjectName("Mono")
        self.explanation_view.setMaximumHeight(110)
        self.explanation_view.setMinimumHeight(72)
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
        prefix_row = QHBoxLayout()
        prefix_row.setContentsMargins(0, 0, 0, 0)
        prefix_row.setSpacing(4)
        prefix_row.addWidget(self.prefix_edit, 1)
        prefix_row.addWidget(self.send_shell_button)
        prefix_row.addWidget(self.copy_button)
        left_layout.addLayout(prefix_row)
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
            # Give the suggestion list (the point of this tab) the room; the
            # snippet browser below it can scroll.
            splitter.setSizes([470, 210])

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

    def _update_prefix_actions(self, text: str) -> None:
        enabled = bool((text or "").strip())
        self.send_shell_button.setEnabled(enabled)
        self.copy_button.setEnabled(enabled)

    def send_to_shell(self) -> None:
        """Hand the typed command to the open shell (typed, never executed)."""
        command = self.prefix_edit.text().strip()
        if not command:
            return
        self.status_message.emit("Command inserted into the open shell - press Enter there to run it.")
        self.send_shell_requested.emit(command)

    def copy_prefix(self) -> None:
        command = self.prefix_edit.text().strip()
        if not command:
            return
        QApplication.clipboard().setText(command)
        self.status_message.emit("Command copied to the clipboard.")

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
        self.error_page = page
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
        # Pasting an error should produce an answer straight away, so the
        # diagnosis follows the text with a short debounce instead of waiting
        # for a button press.
        self._diagnose_timer = QTimer(self)
        self._diagnose_timer.setSingleShot(True)
        self._diagnose_timer.setInterval(150)
        self._diagnose_timer.timeout.connect(self.diagnose)
        self.error_input.textChanged.connect(self._diagnose_timer.start)

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

    def diagnose_text(self, text: str) -> None:
        """Show ``text`` in the error tab and diagnose it immediately.

        The rules are deterministic and offline, so the answer is ready as
        soon as the text arrives - there is nothing to wait for.
        """

        self.error_input.setPlainText(text or "")
        if hasattr(self, "error_page"):
            self.tabs.setCurrentWidget(self.error_page)
        self.diagnose()

    def _show_playbook(self, item: QListWidgetItem) -> None:
        self.playbook_view.setPlainText(self.services.playbooks.render(str(item.data(Qt.ItemDataRole.UserRole))))

    # -- AI -----------------------------------------------------------------
    def _build_ai_tab(self) -> QWidget:
        page = QWidget()
        self.ai_status = QLabel("")
        self.ai_status.setWordWrap(True)
        self._ai_last_response: ModelResponse | None = None

        self.ai_question = QPlainTextEdit()
        self.ai_question.setObjectName("Mono")
        self.ai_question.setPlaceholderText(
            "Ask for an explanation, a next diagnostic step or a read-only check..."
        )
        self.ai_question.setMaximumHeight(86)
        self.ai_context = QPlainTextEdit()
        self.ai_context.setObjectName("Mono")
        self.ai_context.setPlaceholderText(
            "Optional context (error text, banner). It is sent as untrusted data."
        )
        self.ai_context.setMaximumHeight(64)

        self.ai_ask_button = QPushButton("Ask provider")
        self.ai_ask_button.setObjectName("Primary")
        self.ai_ask_button.clicked.connect(self.ask_ai)
        self.ai_copy_button = QPushButton("Copy")
        self.ai_copy_button.setEnabled(False)
        self.ai_copy_button.clicked.connect(self.copy_ai_response)
        self.ai_insert_button = QPushButton("Insert into shell")
        self.ai_insert_button.setEnabled(False)
        self.ai_insert_button.setToolTip(
            "Insert one command-looking line into the open shell. It is never run automatically."
        )
        self.ai_insert_button.clicked.connect(self.insert_ai_suggestion)
        self.ai_selection_button = QPushButton("Send selection" if not self.compact else "Send sel.")
        self.ai_selection_button.setEnabled(False)
        self.ai_selection_button.setToolTip(
            "Insert the highlighted part of the answer into the open shell, exactly as selected. "
            "It is not run; you still press Enter."
        )
        self.ai_selection_button.clicked.connect(self.insert_ai_selection)

        ask_row = QHBoxLayout()
        ask_row.setContentsMargins(0, 0, 0, 0)
        ask_row.addWidget(self.ai_ask_button)
        ask_row.addWidget(self.ai_copy_button)
        ask_row.addWidget(self.ai_insert_button)
        ask_row.addWidget(self.ai_selection_button)
        ask_row.addStretch(1)

        self.ai_response_header = QLabel("AI output is unverified and never executed automatically.")
        self.ai_response_header.setObjectName("Faint")
        self.ai_response_header.setWordWrap(True)
        self.ai_response = QPlainTextEdit()
        self.ai_response.setReadOnly(True)
        self.ai_response.setObjectName("Mono")
        self.ai_response.setPlaceholderText("The model's answer appears here.")
        self.ai_response.selectionChanged.connect(self._update_selection_actions)

        self.ai_context_toggle = QCheckBox("Include terminal context")
        self.ai_context_toggle.setChecked(True)
        self.ai_context_toggle.setToolTip(
            "Adds a short, bounded snapshot of the attached console (shell, directory and the "
            "last few lines) to the question, so the answer can be specific. Uncheck to send only "
            "what you typed."
        )
        self.ai_context_note = QLabel("")
        self.ai_context_note.setObjectName("Faint")
        self.ai_context_note.setWordWrap(True)

        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.ai_status)
        layout.addWidget(QLabel("Question"))
        layout.addWidget(self.ai_question)
        layout.addWidget(QLabel("Context (optional)"))
        layout.addWidget(self.ai_context)
        layout.addWidget(self.ai_context_toggle)
        layout.addWidget(self.ai_context_note)
        layout.addLayout(ask_row)
        layout.addWidget(self.ai_response_header)
        layout.addWidget(self.ai_response, 1)
        ai_note = _note(
            "AI is optional. Terminal output, banners and remote logs are untrusted input: the model "
            "may summarise them, but the deterministic policy engine decides what is allowed, and "
            "AI-generated commands are labelled and never run automatically."
        )
        ai_note.setVisible(not self.compact)
        layout.addWidget(ai_note)
        return page

    def ask_ai(self) -> None:
        """Send only what the operator typed, plus the context they allowed."""

        question = self.ai_question.toPlainText().strip()
        if not question:
            QMessageBox.information(self, "Nothing to ask", "Type a question for the model first.")
            return
        registry = self.services.models
        if not registry.available():
            self.ai_status.setText(
                "No AI provider is ready. Open Settings > Optional AI to choose one and store a key."
            )
            return
        context = self._collect_context()
        prompt = build_prompt(question, untrusted_output=context)
        self._ai_last_response = None
        self.ai_response.clear()
        self.ai_copy_button.setEnabled(False)
        self.ai_insert_button.setEnabled(False)
        self._update_selection_actions()
        self.ai_ask_button.setEnabled(False)
        self.ai_response_header.setText("Waiting for the provider...")
        run_async(
            registry.complete,
            prompt,
            on_result=self._ai_answered,
            on_error=self._ai_failed,
        )

    def _collect_context(self) -> str:
        """Merge the typed context with a bounded terminal snapshot."""

        parts: list[str] = []
        typed = self.ai_context.toPlainText().strip()
        if typed:
            parts.append(typed)
        included = ""
        if self.ai_context_toggle.isChecked():
            snapshot = ""
            if self._context_provider is not None:
                try:
                    snapshot = str(self._context_provider() or "").strip()
                except Exception:  # pragma: no cover - context is best effort
                    snapshot = ""
            if snapshot:
                parts.append(snapshot)
                included = summarise(snapshot)
        if included:
            self.ai_context_note.setText(f"Sent with the question: {included}")
        elif self.ai_context_toggle.isChecked():
            self.ai_context_note.setText(
                "No terminal is attached; only what you typed is sent."
            )
        else:
            self.ai_context_note.setText("Terminal context is off; only what you typed is sent.")
        return "\n\n".join(parts)

    def set_context_provider(self, provider) -> None:
        """Register a callable that returns a snapshot of the open terminal."""

        self._context_provider = provider

    # -- inserting an answer ------------------------------------------------
    def _selected_response_text(self) -> str:
        cursor = self.ai_response.textCursor()
        if not cursor.hasSelection():
            return ""
        # Qt separates paragraphs with U+2029 in selectedText().
        return (
            cursor.selectedText()
            .replace("\u2029", "\n")
            .replace("\u2028", "\n")
            .strip("\n")
        )

    def _update_selection_actions(self) -> None:
        if not hasattr(self, "ai_selection_button"):
            return
        self.ai_selection_button.setEnabled(bool(self._selected_response_text().strip()))

    def insert_ai_selection(self) -> None:
        """Insert the highlighted part of the answer, exactly as selected."""

        text = self._selected_response_text()
        if not text.strip():
            self.status_message.emit("Select the part of the answer you want to insert first.")
            return
        self.insert_text_requested.emit(text)

    def _ai_answered(self, response: object) -> None:
        self.ai_ask_button.setEnabled(True)
        if not isinstance(response, ModelResponse):
            self._ai_failed("the provider returned an unexpected response", "")
            return
        text = response.text.strip()
        if not text:
            self._ai_failed("the provider returned an empty answer", "")
            return
        self._ai_last_response = response
        self.ai_response_header.setText(
            f"AI-generated by {response.provider} / {response.model} - unverified. "
            + (
                "The answer was cut off by the token limit; ask a narrower question. "
                if response.truncated
                else ""
            )
            + "Nothing runs until you insert it and press Enter yourself."
        )
        self.ai_response.setPlainText(text)
        self.ai_copy_button.setEnabled(True)
        self.ai_insert_button.setEnabled(
            suggestion_from_response(response, validator=_looks_like_command) is not None
        )
        self._update_selection_actions()
        self.services.audit.record(
            "ai",
            "ai.request",
            severity="info",
            outcome="success",
            detail={
                "provider": response.provider,
                "model": response.model,
                "response_chars": len(text),
            },
        )

    def _ai_failed(self, message: str, detail: str = "") -> None:
        self.ai_ask_button.setEnabled(True)
        self.ai_response_header.setText("The provider did not answer.")
        self.ai_response.setPlainText(
            (str(message) or "the provider did not answer")
            + (f"\n\n{detail}" if detail and detail != message else "")
        )
        self._update_selection_actions()
        self.services.audit.record(
            "ai",
            "ai.request",
            severity="warning",
            outcome="failure",
            detail={"message": (str(message) or "provider error")[:300]},
        )

    def copy_ai_response(self) -> None:
        text = self.ai_response.toPlainText()
        if text:
            QApplication.clipboard().setText(text)
            self.status_message.emit("AI response copied to the clipboard.")

    def insert_ai_suggestion(self) -> None:
        selection = self._selected_response_text()
        if selection.strip():
            # A highlighted answer is inserted exactly as picked, not reduced
            # to the first command-looking line.
            self.insert_text_requested.emit(selection)
            return
        if self._ai_last_response is None:
            return
        candidate = suggestion_from_response(
            self._ai_last_response, validator=_looks_like_command
        )
        if not candidate:
            self.status_message.emit("No single command-looking line was found in the response.")
            return
        self.send_shell_requested.emit(candidate)
        self.status_message.emit(
            "AI suggestion inserted into the open shell - review it, then press Enter to run it."
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
        config = self.services.ai_config
        if not config.enabled:
            self.ai_status.setText(
                "AI is disabled. Suggestion engine, history, rules and playbooks work offline; "
                "choose a provider in Settings > Optional AI to ask a model."
            )
        else:
            self.ai_status.setText(
                f"Provider: {config.label}  |  model: {provider.model.model or config.model}"
                + (
                    ""
                    if provider.available()
                    else "  - not ready (a key may be missing; Settings > Optional AI)"
                )
            )


_COMMAND_VERBS = frozenset(
    """
    apt awk cat cd chmod chown curl cut date df dig dir dmesg docker dnf du echo env
    export file find free getent git grep head history host hostname htop ifconfig ip
    ipconfig journalctl kubectl last less ls lsblk lsof md5sum mkdir more mount mv
    netstat nmap nslookup openssl pacman ping pip powershell printenv ps pwd python
    reg route sc scp sed sftp sha256sum show sort ssh stat ss sudo systemctl tail tar
    tasklist taskkill tee top tracert traceroute type uname uniq uptime ver w wget wc
    where who whoami winget wmic yum
    """.split()
)
_PS_VERBS = frozenset(
    "add clear close copy disable enable export get import invoke move new out remove "
    "rename restart resolve select set start stop test where write".split()
)


def _looks_like_command(text: str) -> bool:
    """Reject prose lines when offering an AI answer for insertion.

    This is only a display/insertion guard. The operator still reviews the text
    and the remote policy gate evaluates it before anything can run.
    """

    candidate = (text or "").strip().strip("`").strip()
    if not candidate or len(candidate) > 200:
        return False
    if candidate[-1] in ":.,?!":
        return False
    first = candidate.split()[0].strip("'\"").lower()
    if not first or first.startswith(("-", "*", "•")):
        return False
    if first in _COMMAND_VERBS:
        return True
    if first.endswith((".exe", ".ps1", ".sh", ".py", ".bat", ".cmd")):
        return True
    if "/" in first or "\\" in first:
        return True
    if "-" in first and first.split("-", 1)[0] in _PS_VERBS:
        return True
    return False


def _note(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("Faint")
    label.setWordWrap(True)
    return label
