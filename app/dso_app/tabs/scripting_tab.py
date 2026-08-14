"""
The Scripting tab: edit the game's Lua with the official reference beside it.

WHY IT IS SHAPED LIKE THIS
--------------------------
The API is documented -- 318 symbols in 22 ``N*`` namespaces plus
``MissionLib`` and ``CameraLib``, extracted from ``ds1doc_eng.chm`` (see
:mod:`dsotools.scriptdoc`).  So this tab is not a guesswork editor with
syntax colours; it is an editor that can *answer questions*: what does this
function take, what does it return, what is a worked example, and does this
call exist at all.

**Where an edit goes is the other half.**  Two kinds of script sit side by
side and they are delivered completely differently:

* a **mission script** lives in the mod's ``scripts/`` folder and is read
  loose from there;
* a **library** -- ``MissionLib.lua``, ``CameraLib.lua``, ``BattleLib.lua`` --
  exists only in the *game installation*, in no archive at all.

Editing a library therefore cannot save where it was read from without
overwriting the installation irreversibly.  It saves into the mod's ``root/``
payload instead, and the Project tab installs that with a backup.  The tab
says so on screen every time, because a save that silently goes somewhere else
is worse than one that refuses.

The "not documented" list under the editor reports calls that are neither in
the reference nor defined by the Lua in play.  It is deliberately not called
"errors": the shipped libraries themselves call 15 undocumented engine
functions, so this says *the reference does not cover this*, which is a
different claim from *this is wrong*.
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional

from PySide6.QtCore import QRegularExpression, Qt, QTimer
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QSyntaxHighlighter,
    QTextCharFormat,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCompleter,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from dsotools.errors import DsoError

from ..api_view import call_skeleton, symbol_html

#: How each finding reads, and how loudly.  Red is "this cannot work"; amber is
#: "this works and does nothing", which is the quieter and nastier failure.
_RED = "#c0392b"
_AMBER = "#9a6f09"
_SEVERITY = {
    "absent": ("not in this build", _RED),
    "unknown": ("unknown", _RED),
    "stub": ("does nothing", _AMBER),
    "literal": ("needs a StringId", _AMBER),
}

#: Lua's own words.  Short enough to keep here, and the highlighter is the
#: only thing that needs them.
KEYWORDS = (
    "and break do else elseif end false for function if in local nil not or "
    "repeat return then true until while"
).split()


class LuaHighlighter(QSyntaxHighlighter):
    """Comments, strings, numbers, keywords, and the documented namespaces.

    The namespace rule is the useful one: ``NComm`` coloured differently from
    ``NCom`` is a typo caught while typing it.
    """

    def __init__(self, document, namespaces=()) -> None:
        super().__init__(document)
        self.rules = []

        def style(colour, bold=False, italic=False):
            fmt = QTextCharFormat()
            fmt.setForeground(QColor(colour))
            if bold:
                fmt.setFontWeight(QFont.Weight.Bold)
            fmt.setFontItalic(italic)
            return fmt

        keyword = style("#c586c0", bold=True)
        for word in KEYWORDS:
            self.rules.append((QRegularExpression(rf"\b{word}\b"), keyword))

        namespace = style("#4ec9b0", bold=True)
        for name in sorted(namespaces):
            self.rules.append(
                (QRegularExpression(rf"\b{QRegularExpression.escape(name)}\b"),
                 namespace))

        self.rules.append((QRegularExpression(r"\b\d+(\.\d+)?\b"), style("#b5cea8")))
        self.rules.append((QRegularExpression(r'"[^"\n]*"'), style("#ce9178")))
        self.rules.append((QRegularExpression(r"'[^'\n]*'"), style("#ce9178")))
        # Last, so a comment wins over anything it contains.
        self.comment = style("#6a9955", italic=True)
        self.rules.append((QRegularExpression(r"--[^\n]*"), self.comment))

    def highlightBlock(self, text: str) -> None:
        for pattern, fmt in self.rules:
            it = pattern.globalMatch(text)
            while it.hasNext():
                match = it.next()
                self.setFormat(match.capturedStart(), match.capturedLength(), fmt)


class ScriptingTab(QWidget):
    def __init__(self, window) -> None:
        super().__init__()
        self.window = window
        self.session = window.session
        self._key: Optional[str] = None
        self._loading = False
        self._dirty = False
        self._symbols: Dict[str, dict] = {}

        layout = QVBoxLayout(self)
        self.title = QLabel("")
        self.title.setWordWrap(True)
        layout.addWidget(self.title)

        split = QSplitter(Qt.Orientation.Horizontal)

        # -- the scripts ------------------------------------------------------
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        self.file_filter = QLineEdit()
        self.file_filter.setPlaceholderText("filter scripts…")
        self.file_filter.textChanged.connect(self._filter_scripts)
        ll.addWidget(self.file_filter)
        self.files = QTreeWidget()
        self.files.setColumnCount(2)
        self.files.setHeaderLabels(["Script", "Where it lives"])
        self.files.setRootIsDecorated(False)
        self.files.setAlternatingRowColors(True)
        self.files.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.files.itemSelectionChanged.connect(self._open_selected)
        ll.addWidget(self.files, 1)
        split.addWidget(left)

        # -- the editor -------------------------------------------------------
        middle = QWidget()
        ml = QVBoxLayout(middle)
        ml.setContentsMargins(0, 0, 0, 0)
        self.editor = QPlainTextEdit()
        self.editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        font = QFont("Consolas")
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setPointSize(10)
        self.editor.setFont(font)
        self.editor.setTabStopDistance(4 * self.editor.fontMetrics().horizontalAdvance(" "))
        self.editor.textChanged.connect(self._text_changed)
        ml.addWidget(self.editor, 3)

        self.where = QLabel("")
        self.where.setWordWrap(True)
        ml.addWidget(self.where)

        self.problems = QTreeWidget()
        self.problems.setColumnCount(4)
        self.problems.setHeaderLabels(["Line", "Issue", "Symbol", "What it means"])
        self.problems.setRootIsDecorated(False)
        self.problems.setMaximumHeight(110)
        self.problems.itemDoubleClicked.connect(
            lambda item, _c: self._go_to_line(int(item.text(0))))
        ml.addWidget(self.problems, 1)

        row = QHBoxLayout()
        self.status = QLabel("")
        row.addWidget(self.status, 1)
        self.btn_mission = QPushButton("New mission…")
        self.btn_mission.setToolTip(
            "Start a mission script — new, or one that replaces a stock "
            "mission by registering its name.")
        self.btn_mission.clicked.connect(self.new_mission)
        self.btn_script = QPushButton("New script…")
        self.btn_script.setToolTip(
            "A plain scripts/*.lua. The loader runs every one of them, so it\n"
            "takes effect by existing — a mission needs NScript.Register,\n"
            "which “New mission…” writes for you.")
        self.btn_script.clicked.connect(self.new_script)
        self.btn_text = QPushButton("Mod text…")
        self.btn_text.setToolTip(
            "Edit the StringIds this mod defines. Every engine call that shows "
            "words takes an id, never a literal.")
        self.btn_text.clicked.connect(self.edit_text)
        self.btn_revert = QPushButton("Revert")
        self.btn_revert.clicked.connect(self.revert)
        self.btn_save = QPushButton("Save")
        self.btn_save.clicked.connect(self.save)
        row.addWidget(self.btn_mission)
        row.addWidget(self.btn_script)
        row.addWidget(self.btn_text)
        row.addWidget(self.btn_revert)
        row.addWidget(self.btn_save)
        ml.addLayout(row)
        split.addWidget(middle)

        # -- the reference ----------------------------------------------------
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        self.api_filter = QLineEdit()
        self.api_filter.setPlaceholderText("search the API…")
        self.api_filter.textChanged.connect(self._filter_api)
        rl.addWidget(self.api_filter)
        self.api = QTreeWidget()
        self.api.setHeaderLabels(["Documented API"])
        self.api.setAlternatingRowColors(True)
        self.api.itemSelectionChanged.connect(self._show_symbol)
        self.api.itemDoubleClicked.connect(lambda item, _c: self._insert(item))
        rl.addWidget(self.api, 2)
        self.doc = QTextBrowser()
        self.doc.setOpenExternalLinks(False)
        rl.addWidget(self.doc, 3)
        split.addWidget(right)

        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 3)
        split.setStretchFactor(2, 2)
        split.setSizes([240, 700, 380])
        layout.addWidget(split, 1)

        #: Checking runs on a pause, not on every keystroke.
        self._check_timer = QTimer(self)
        self._check_timer.setSingleShot(True)
        self._check_timer.setInterval(400)
        self._check_timer.timeout.connect(self._run_check)

        self.highlighter = None
        self._sync()

    def new_mission(self) -> None:
        """Start a mission script, new or replacing a stock one."""
        if not self.session.mod:
            QMessageBox.information(self, "New mission",
                                    "Open a mod first — the script is written "
                                    "into it.")
            return
        from ..missions_panel import MissionDialog

        dialog = MissionDialog(self.session, self)
        if dialog.exec() and dialog.written:
            self.refresh()
            self._open_path(dialog.written)

    def new_script(self) -> None:
        """Start a plain script -- not a mission, just Lua the loader runs."""
        from PySide6.QtWidgets import QInputDialog

        from dsotools.errors import DsoError

        if not self.session.mod:
            QMessageBox.information(self, "New script",
                                    "Open a mod first — the file is written "
                                    "into it.")
            return
        name, ok = QInputDialog.getText(
            self, "New script", "File name (without .lua):")
        if not ok or not name.strip():
            return
        try:
            written = self.session.new_script(name)
        except DsoError as exc:
            QMessageBox.warning(self, "New script", str(exc))
            return
        self.refresh()
        self._open_path(written)

    def _open_path(self, path: str) -> None:
        """Select and open a script by path, after the list has been rebuilt."""
        for row in self.session.scripts():
            if os.path.normcase(row.get("path") or "") == os.path.normcase(path):
                self.open_script(row)
                return

    def edit_text(self) -> None:
        """Open the mod's string table.

        Lives next to the editor because the two are one job: a script names an
        id, the table gives it words, and neither half does anything alone.
        """
        if not self.session.mod:
            QMessageBox.information(self, "Mod text",
                                    "Open a mod first — the table is written "
                                    "into it.")
            return
        from ..strings_panel import StringTableDialog

        StringTableDialog(self.session, self).exec()
        self._run_check()

    # -- population ------------------------------------------------------------

    def refresh(self) -> None:
        """Rebuild from the session: called when the game or mod changes."""
        self._symbols = self.session.lua_symbols()
        self._fill_api()
        self._fill_scripts()
        if self.highlighter is None:
            namespaces = {s["namespace"] for s in self._symbols.values()
                          if s["namespace"]}
            self.highlighter = LuaHighlighter(self.editor.document(), namespaces)
            self._install_completer(sorted(self._symbols))
        self._sync()

    def _fill_scripts(self) -> None:
        self.files.blockSignals(True)
        try:
            self.files.clear()
            for row in self.session.scripts():
                item = QTreeWidgetItem([row["name"], f"{row['kind']} — {row['where']}"])
                item.setData(0, Qt.ItemDataRole.UserRole, row)
                item.setToolTip(0, row["key"])
                self.files.addTopLevelItem(item)
            for column in range(2):
                self.files.resizeColumnToContents(column)
        finally:
            self.files.blockSignals(False)
        self._filter_scripts(self.file_filter.text())

    def _fill_api(self) -> None:
        self.api.clear()
        database = self.session.lua_api()
        if not database:
            return
        groups: Dict[str, List[dict]] = {}
        for symbol in database["symbols"]:
            key = symbol["namespace"] or ("events" if symbol["kind"] == "event"
                                          else "other")
            groups.setdefault(key, []).append(symbol)
        for name in sorted(groups):
            parent = QTreeWidgetItem([f"{name}  ({len(groups[name])})"])
            for symbol in sorted(groups[name], key=lambda s: s["name"].lower()):
                child = QTreeWidgetItem([symbol["name"]])
                child.setData(0, Qt.ItemDataRole.UserRole, symbol)
                child.setToolTip(0, symbol["signature"])
                parent.addChild(child)
            self.api.addTopLevelItem(parent)

    def _install_completer(self, words) -> None:
        completer = QCompleter(list(words), self.editor)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setWidget(self.editor)
        completer.activated.connect(self._complete)
        self._completer = completer
        self.editor.keyPressEvent = self._key_press          # noqa: E501 - see below

    # -- editing ---------------------------------------------------------------

    def _key_press(self, event) -> None:
        """Ctrl+Space offers the documented calls that match what is typed.

        Deliberately explicit rather than popping up while typing: this editor
        is used on 400-line library files, and a completer that fires on every
        word is in the way more often than it helps.
        """
        QPlainTextEdit.keyPressEvent(self.editor, event)
        if (event.key() == Qt.Key.Key_Space
                and event.modifiers() & Qt.KeyboardModifier.ControlModifier):
            prefix = self._word_under_cursor()
            self._completer.setCompletionPrefix(prefix)
            if self._completer.completionCount():
                rect = self.editor.cursorRect()
                rect.setWidth(320)
                self._completer.complete(rect)

    def _word_under_cursor(self) -> str:
        cursor = self.editor.textCursor()
        cursor.select(QTextCursor.SelectionType.WordUnderCursor)
        word = cursor.selectedText()
        # Reach back over a dotted name: "NComm.Add" completes, "Add" does not.
        line = self.editor.textCursor().block().text()
        column = self.editor.textCursor().positionInBlock()
        head = line[:column]
        dotted = head.rsplit(" ", 1)[-1].rsplit("\t", 1)[-1].rsplit("(", 1)[-1]
        return dotted or word

    def _complete(self, text: str) -> None:
        cursor = self.editor.textCursor()
        prefix = self._completer.completionPrefix()
        cursor.movePosition(QTextCursor.MoveOperation.Left,
                            QTextCursor.MoveMode.KeepAnchor, len(prefix))
        cursor.insertText(text)
        self.editor.setTextCursor(cursor)

    def _text_changed(self) -> None:
        if self._loading:
            return
        self._dirty = True
        self._check_timer.start()
        self._sync()

    def _run_check(self) -> None:
        self.problems.clear()
        if not self._key:
            return
        try:
            findings = self.session.check_script(self.editor.toPlainText())
        except DsoError:
            return
        for finding in findings:
            kind = finding.get("kind", "unknown")
            label, colour = _SEVERITY.get(kind, ("check", _AMBER))
            item = QTreeWidgetItem([str(finding["line"]), label,
                                    finding["symbol"], finding.get("detail", "")])
            for column in range(4):
                item.setForeground(column, QBrush(QColor(colour)))
            item.setToolTip(3, finding.get("detail", ""))
            self.problems.addTopLevelItem(item)
        for column in range(3):
            self.problems.resizeColumnToContents(column)

    def _go_to_line(self, line: int) -> None:
        cursor = QTextCursor(self.editor.document().findBlockByNumber(max(line - 1, 0)))
        self.editor.setTextCursor(cursor)
        self.editor.setFocus()

    # -- opening and saving ----------------------------------------------------

    def _open_selected(self) -> None:
        items = self.files.selectedItems()
        if not items:
            return
        row = items[0].data(0, Qt.ItemDataRole.UserRole)
        if not row or row["key"] == self._key:
            return
        if self._dirty and not self._confirm_discard():
            return
        self.open_script(row)

    def open_script(self, row: dict) -> None:
        try:
            text = self.session.read_script(row["key"])
        except (DsoError, OSError) as exc:
            QMessageBox.warning(self, "Open", str(exc))
            return
        self._loading = True
        try:
            self.editor.setPlainText(text)
        finally:
            self._loading = False
        self._key = row["key"]
        self._dirty = False
        self.where.setText(self._destination(row))
        self._run_check()
        self._sync()

    def _destination(self, row: dict) -> str:
        if row["kind"] == "library":
            return (
                "<b>Library.</b> This file exists only in the game "
                "installation — no archive holds it. Saving writes it into "
                "this mod's <code>root/</code> payload, and the Project tab "
                "installs that with a backup of the original."
            )
        return ("<b>Mission script.</b> Saved in the mod's "
                "<code>scripts/</code> folder, which the game reads loose.")

    def save(self) -> None:
        if not self._key:
            return
        try:
            written = self.session.save_script(self._key, self.editor.toPlainText())
        except (DsoError, OSError) as exc:
            QMessageBox.warning(self, "Save", str(exc))
            return
        self._dirty = False
        self._fill_scripts()
        self._sync()
        QMessageBox.information(self, "Save", f"Written to:<br><code>{written}</code>")

    def revert(self) -> None:
        if not self._key:
            return
        items = [self.files.topLevelItem(i)
                 for i in range(self.files.topLevelItemCount())]
        for item in items:
            row = item.data(0, Qt.ItemDataRole.UserRole)
            if row and row["key"] == self._key:
                self._dirty = False
                self.open_script(row)
                return

    def _confirm_discard(self) -> bool:
        return QMessageBox.question(
            self, "Unsaved changes",
            "This script has unsaved changes.<br><br>Open another and lose them?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        ) == QMessageBox.StandardButton.Yes

    # -- the reference pane ----------------------------------------------------

    def _show_symbol(self) -> None:
        items = self.api.selectedItems()
        if not items:
            return
        symbol = items[0].data(0, Qt.ItemDataRole.UserRole)
        if not symbol:
            self.doc.clear()
            return
        self.doc.setHtml(symbol_html(symbol))

    def _insert(self, item) -> None:
        symbol = item.data(0, Qt.ItemDataRole.UserRole)
        if not symbol:
            return
        self.editor.insertPlainText(call_skeleton(symbol))
        self.editor.setFocus()

    def _filter_api(self, text: str) -> None:
        needle = text.strip().lower()
        for i in range(self.api.topLevelItemCount()):
            parent = self.api.topLevelItem(i)
            shown = 0
            for j in range(parent.childCount()):
                child = parent.child(j)
                symbol = child.data(0, Qt.ItemDataRole.UserRole) or {}
                hit = (not needle
                       or needle in child.text(0).lower()
                       or needle in (symbol.get("summary", "").lower()))
                child.setHidden(not hit)
                shown += hit
            parent.setHidden(shown == 0)
            parent.setExpanded(bool(needle) and shown > 0)

    def _filter_scripts(self, text: str) -> None:
        needle = text.strip().lower()
        for i in range(self.files.topLevelItemCount()):
            item = self.files.topLevelItem(i)
            item.setHidden(bool(needle) and needle not in item.text(0).lower()
                           and needle not in item.text(1).lower())

    # -- state -----------------------------------------------------------------

    def _sync(self) -> None:
        database = self.session.lua_api()
        if not database:
            self.title.setText(
                "<b>No API reference in this build.</b> Run "
                "<code>tools/chm_to_json.py</code> against the Darkstar One "
                "Modding Tools to generate it; the editor works without it, "
                "but nothing can be looked up or checked.")
        else:
            count = len(database["symbols"])
            constants = sum(len(v) for v in database["constants"].values())
            self.title.setText(
                f"{count} documented symbols and {constants} constants from "
                f"<code>{database['source']}</code>. "
                f"Ctrl+Space completes; double-click a function to insert a "
                f"call skeleton.")
        editable = bool(self._key) and bool(self.session.mod)
        self.btn_save.setEnabled(editable and self._dirty)
        self.btn_revert.setEnabled(bool(self._key) and self._dirty)
        if self._key and not self.session.mod:
            self.status.setText("Open a mod — scripts are saved into it.")
        elif self._dirty:
            self.status.setText("unsaved changes")
        else:
            self.status.setText("")


__all__ = ["ScriptingTab", "LuaHighlighter"]
