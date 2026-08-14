"""
The Data tab: the game's `inifiles/` tables, as something you can search.

WHY A TAB, AND WHY THIS SHAPE
-----------------------------
``inifiles/`` is the largest genuinely moddable surface in the game and the only
one that needed **no reverse engineering at all**: 68 files, 13,091 sections and
120,034 entries of plain text that the engine reads directly.  Ship stats,
weapon damage, planet types, prices, the lot.

The unit here is the **section**, not a row in a grid.  That is a measurement,
not a preference: only 27 of the 68 files are uniform enough to be a table --
``Planets.ini`` is 5,970 sections sharing one key set, 99% of the time -- while
the cluster files mix object types and share a key set in 40-55% of their
sections.  A grid would work beautifully for a third of the data and misrepresent
the rest, so the editor is "pick a section, edit its keys", which is true of all
68 files.

WHAT IT DOES NOT DO, DELIBERATELY
---------------------------------
It edits **values**.  It does not add or delete keys and sections: the engine's
schema is not written down anywhere, so a key this tool invents is a key nobody
can say the game reads, and the failure mode is silence.  Changing a number that
is already there is the edit people actually want and the one that cannot be
wrong in that particular way.

Widgets only.  Reading, editing and writing live in ``Session.open_ini`` /
``Session.set_ini_values``, which is where they can be tested.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QStyledItemDelegate,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from dsotools.errors import DsoError

from .. import workers

class _ValueOnlyDelegate(QStyledItemDelegate):
    """No editor at all for the key and the comment.

    They were editable and silently reverted afterwards, which is a worse
    answer than not opening: the user types, watches it undo itself, and has to
    guess whether the tool is broken or the edit is disallowed.  A column that
    cannot be edited should not offer a cursor.
    """

    def createEditor(self, parent, option, index):
        if index.column() != 1:
            return None
        return super().createEditor(parent, option, index)


#: Sections shown at once.  ``Planets.ini`` has 5,970 and the filter box is the
#: real navigation tool; this only bounds the initial render.
MAX_SECTIONS = 3000


class DataTab(QWidget):
    def __init__(self, window) -> None:
        super().__init__()
        self.window = window
        self.session = window.session
        self._rows: list = []
        self._file: Optional[str] = None
        self._data: Optional[dict] = None
        self._section: Optional[dict] = None
        #: ``{(section, key): value}`` -- edits not yet written.
        self._edits: dict = {}
        self._loading = False

        layout = QVBoxLayout(self)

        row = QHBoxLayout()
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("filter files…")
        self.filter_edit.textChanged.connect(self._apply_filter)
        self.btn_reload = QPushButton("Reload")
        self.btn_reload.clicked.connect(self.reload)
        row.addWidget(self.filter_edit, 1)
        row.addWidget(self.btn_reload)
        layout.addLayout(row)

        split = QSplitter(Qt.Orientation.Horizontal)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["File", "In mod", "Source"])
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.itemSelectionChanged.connect(self._open_selected)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._file_menu)
        split.addWidget(self.tree)

        right = QWidget()
        rl = QVBoxLayout(right)
        self.title = QLabel("Open a game folder, then pick a table.")
        self.title.setWordWrap(True)
        rl.addWidget(self.title)

        inner = QSplitter(Qt.Orientation.Horizontal)

        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        self.section_filter = QLineEdit()
        self.section_filter.setPlaceholderText("filter sections…")
        self.section_filter.textChanged.connect(self._fill_sections)
        ll.addWidget(self.section_filter)
        self.sections = QTreeWidget()
        self.sections.setColumnCount(2)
        self.sections.setHeaderLabels(["Section", "Keys"])
        self.sections.setRootIsDecorated(False)
        self.sections.setAlternatingRowColors(True)
        self.sections.itemSelectionChanged.connect(self._section_selected)
        ll.addWidget(self.sections, 1)
        inner.addWidget(left)

        entries = QWidget()
        el = QVBoxLayout(entries)
        el.setContentsMargins(0, 0, 0, 0)
        self.entries = QTreeWidget()
        self.entries.setColumnCount(3)
        self.entries.setHeaderLabels(["Key", "Value", "What it is"])
        self.entries.setRootIsDecorated(False)
        self.entries.setAlternatingRowColors(True)
        # Only the value column can be edited: this edits numbers in a text
        # file, it does not pretend to know a schema.
        self.entries.setItemDelegate(_ValueOnlyDelegate(self.entries))
        self.entries.itemChanged.connect(self._entry_changed)
        el.addWidget(self.entries, 1)

        actions = QHBoxLayout()
        self.notes = QLabel("")
        self.notes.setWordWrap(True)
        actions.addWidget(self.notes, 1)
        self.btn_revert = QPushButton("Revert")
        self.btn_revert.setToolTip("Throw away the unsaved edits and reread the file")
        self.btn_revert.clicked.connect(self.revert)
        self.btn_save = QPushButton("Save to mod")
        self.btn_save.clicked.connect(self.save)
        actions.addWidget(self.btn_revert)
        actions.addWidget(self.btn_save)
        el.addLayout(actions)
        inner.addWidget(entries)
        inner.setStretchFactor(0, 1)
        inner.setStretchFactor(1, 2)

        rl.addWidget(inner, 1)
        split.addWidget(right)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 3)
        # Wide enough for the file names *and* where each resolves from: the
        # third column is the one that says whether you are looking at your own
        # copy or the game's.
        split.setSizes([320, 900])
        layout.addWidget(split, 1)
        self._sync_controls()

    # -- browsing ------------------------------------------------------------

    def refresh(self) -> None:
        """The game or the mod changed: reload the list, keep the open file."""
        self._data = None
        self._section = None
        self._edits = {}
        self.sections.clear()
        self.entries.clear()
        self.reload()

    def reload(self) -> None:
        if not self.session.stock:
            self.tree.clear()
            self.title.setText("Open a game folder, then pick a table.")
            return
        self.btn_reload.setEnabled(False)
        workers.run(
            self.session.ini_files,
            on_result=self._rows_ready,
            on_error=self._failed,
            on_done=lambda: self.btn_reload.setEnabled(True),
        )

    def _rows_ready(self, rows) -> None:
        self._rows = rows
        self._apply_filter()
        # Reopen whatever was open, so a save does not send the user back to
        # the top of a 68-file list.
        if self._file:
            self._open_file(self._file)

    def _apply_filter(self) -> None:
        needle = self.filter_edit.text().strip().lower()
        # Signals off while the list is rebuilt: repopulating changes the
        # selection, which would open whatever landed in row 0.
        self.tree.blockSignals(True)
        self.tree.clear()
        for r in self._rows:
            if needle and needle not in r["name"].lower():
                continue
            item = QTreeWidgetItem([r["name"], "yes" if r["in_mod"] else "", r["source"]])
            item.setData(0, Qt.ItemDataRole.UserRole, r)
            item.setToolTip(0, f"{r['vpath']}  ({r['size']:,} bytes)")
            if r["in_mod"]:
                item.setForeground(0, QBrush(QColor("#2980b9")))
            self.tree.addTopLevelItem(item)
        if self._file:
            for i in range(self.tree.topLevelItemCount()):
                item = self.tree.topLevelItem(i)
                row = item.data(0, Qt.ItemDataRole.UserRole)
                if row and row["vpath"].lower() == self._file.lower():
                    self.tree.setCurrentItem(item)
                    break
        self.tree.blockSignals(False)
        for c in range(3):
            self.tree.resizeColumnToContents(c)

    def _open_selected(self) -> None:
        items = self.tree.selectedItems()
        if not items:
            return
        row = items[0].data(0, Qt.ItemDataRole.UserRole)
        if row and self._confirm_discard():
            self._open_file(row["vpath"])

    def reveal(self, vpath: str) -> None:
        """Open ``vpath`` here, for the cross-tab jump."""
        self.filter_edit.blockSignals(True)
        self.filter_edit.clear()
        self.filter_edit.blockSignals(False)
        self._file = vpath
        self._apply_filter()
        self._open_file(vpath)

    # -- one file ------------------------------------------------------------

    def _open_file(self, vpath: str) -> None:
        self._file = vpath
        self.title.setText(f"{vpath} — reading…")
        workers.run(
            self.session.open_ini,
            vpath,
            on_result=lambda data, p=vpath: self._file_ready(p, data),
            on_error=self._failed,
        )

    def _file_ready(self, vpath: str, data: dict) -> None:
        if vpath != self._file:
            return                      # the user moved on while this was read
        self._data = data
        self._edits = {}
        self._section = None
        self.entries.clear()
        self._fill_sections()
        self._show_notes()
        self._sync_controls()

    def _fill_sections(self) -> None:
        if self._data is None:
            self.sections.clear()
            return
        needle = self.section_filter.text().strip().lower()
        self.sections.blockSignals(True)
        self.sections.clear()
        shown = 0
        for section in self._data["sections"]:
            if needle and needle not in section["name"].lower():
                continue
            if shown >= MAX_SECTIONS:
                break
            item = QTreeWidgetItem([section["name"], str(len(section["entries"]))])
            item.setData(0, Qt.ItemDataRole.UserRole, section)
            if section["duplicate_keys"]:
                item.setForeground(0, QBrush(QColor("#9a6f09")))
                item.setToolTip(
                    0,
                    "Duplicate keys: " + ", ".join(section["duplicate_keys"])
                    + "\nThe engine tolerates these; which one wins is not "
                      "something this tool decides for you.",
                )
            self.sections.addTopLevelItem(item)
            shown += 1
        self.sections.blockSignals(False)
        total = len(self._data["sections"])
        more = f"  (showing {shown} of {total})" if shown < total else f"  ({total})"
        self.sections.setHeaderLabels(["Section" + more, "Keys"])
        self.sections.resizeColumnToContents(0)
        self.title.setText(
            f"<b>{self._data['vpath']}</b> — {total} section(s), "
            f"{sum(len(s['entries']) for s in self._data['sections']):,} entries"
        )

    def _section_selected(self) -> None:
        items = self.sections.selectedItems()
        if not items:
            return
        self._section = items[0].data(0, Qt.ItemDataRole.UserRole)
        self._fill_entries()

    def _fill_entries(self) -> None:
        self.entries.blockSignals(True)
        self.entries.clear()
        if self._section is not None:
            for entry in self._section["entries"]:
                key = entry["key"]
                pending = self._edits.get((self._section["name"], key))
                item = QTreeWidgetItem([
                    key,
                    pending if pending is not None else entry["value"],
                    entry["comment"],
                ])
                item.setData(0, Qt.ItemDataRole.UserRole, entry)
                # Editable as an item, restricted to the value column by the
                # delegate -- per-column flags do not exist on a tree item.
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
                item.setToolTip(0, "Read-only: this edits values, not keys.")
                item.setToolTip(2, "The file's own comment. Read-only.")
                if pending is not None:
                    item.setForeground(1, QBrush(QColor("#2980b9")))
                self.entries.addTopLevelItem(item)
        self.entries.blockSignals(False)
        for c in range(3):
            self.entries.resizeColumnToContents(c)
        self._sync_controls()

    def _entry_changed(self, item, column: int) -> None:
        if self._loading or self._section is None:
            return
        entry = item.data(0, Qt.ItemDataRole.UserRole)
        if entry is None:
            return
        if column != 1:
            return          # the delegate does not open an editor there

        key = (self._section["name"], entry["key"])
        new = item.text(1)
        if new == entry["value"]:
            self._edits.pop(key, None)
            # Cleared, not set to black: an explicit colour overrides the
            # palette, and on a dark theme #000000 is text you cannot read.
            item.setData(1, Qt.ItemDataRole.ForegroundRole, None)
        else:
            self._edits[key] = new
            item.setForeground(1, QBrush(QColor("#2980b9")))
        self._sync_controls()

    # -- saving --------------------------------------------------------------

    def _sync_controls(self) -> None:
        dirty = bool(self._edits)
        self.btn_save.setEnabled(dirty and bool(self.session.mod))
        self.btn_revert.setEnabled(dirty)
        if dirty and not self.session.mod:
            self.btn_save.setToolTip("Open a mod first — edits are saved into it")
        else:
            self.btn_save.setToolTip(
                f"Write {len(self._edits)} changed value(s) into the mod"
                if dirty else "Nothing has been changed"
            )

    def _show_notes(self) -> None:
        parts = []
        if self._data and self._data["duplicate_sections"]:
            names = ", ".join(self._data["duplicate_sections"][:4])
            parts.append(
                f'<span style="color:#9a6f09">Duplicate section(s): {names}. '
                "The engine tolerates these, but which one wins is not "
                "established — so nothing here resolves them for you.</span>"
            )
        self.notes.setText(" ".join(parts))

    def _confirm_discard(self) -> bool:
        if not self._edits:
            return True
        return QMessageBox.question(
            self, "Unsaved changes",
            f"{len(self._edits)} value(s) have been changed but not saved.<br><br>"
            "Open another file and lose them?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        ) == QMessageBox.StandardButton.Yes

    def revert(self) -> None:
        self._edits = {}
        if self._file:
            self._open_file(self._file)

    def save(self) -> None:
        if not self._edits or not self._file:
            return
        edits = [(section, key, value)
                 for (section, key), value in sorted(self._edits.items())]
        try:
            routed = self.session.set_ini_values(self._file, edits)
        except (DsoError, OSError) as exc:
            QMessageBox.warning(self, "Save", str(exc))
            return
        self._edits = {}
        QMessageBox.information(
            self, "Save",
            f"{len(edits)} value(s) written into the mod:<pre>"
            + "\n".join(f"{k}   → {v}" for k, v in routed.items())
            + "</pre>",
        )
        # Reopened from disk, so what is on screen is what was written.
        self._open_file(self._file)

    # -- actions on a file ---------------------------------------------------

    def _file_menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        if item is None:
            return
        row = item.data(0, Qt.ItemDataRole.UserRole)
        if not row:
            return
        vpath = row["vpath"]

        from ..linked_assets import (
            export_asset, replace_asset_dialog, reset_asset_to_stock, show_users,
        )

        menu = QMenu(self.tree)
        act_export = menu.addAction("Export…")
        act_replace = menu.addAction("Replace…")
        act_replace.setEnabled(bool(self.session.mod))
        if not self.session.mod:
            act_replace.setToolTip("Open a mod first — replacements are saved into it")
        act_reset = menu.addAction("Reset to stock…")
        why = self.session.can_reset_to_stock(vpath)
        act_reset.setEnabled(why is None)
        if why is not None:
            act_reset.setToolTip(why)
        act_uses = menu.addAction("What uses this…")
        menu.addSeparator()
        act_copy = menu.addAction("Copy path")
        menu.setToolTipsVisible(True)

        chosen = menu.exec(self.tree.viewport().mapToGlobal(pos))
        if chosen is act_export:
            export_asset(self, self.session, vpath)
        elif chosen is act_replace:
            if replace_asset_dialog(self, self.session, vpath):
                self.refresh()
        elif chosen is act_reset:
            if reset_asset_to_stock(self, self.session, vpath):
                self._edits = {}
        elif chosen is act_uses:
            show_users(self, self.session, vpath)
        elif chosen is act_copy:
            QApplication.clipboard().setText(vpath)

    def _failed(self, message: str, _traceback: str) -> None:
        self.title.setText(f"<b>failed:</b> {message}")


__all__ = ["DataTab"]
