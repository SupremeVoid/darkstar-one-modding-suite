"""
Authoring a mod's on-screen text.

Every engine call that shows words takes a *StringId*, never a literal, so a
mod that wants to say anything at all needs its own ``.res`` table.  That file
stores only a hash of each id, which is why this editor writes two things at
once: the table the game reads, and the ``(id, text)`` pairs in the
``.dsoproject`` that make it editable again tomorrow.

The dialog therefore refuses to save a hash collision -- one of the two texts
would be permanently unreachable, and which one depends on write order.  The
shipped ``Xml2ResConverter`` refused it too.

Nothing here parses or writes a ``.res``; :mod:`dso_app.session` does that.
"""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from dsotools.errors import DsoError

#: An id that is not yet in the built table.
_UNBUILT = "#9a6f09"
#: An id the stock game already uses.
_SHADOW = "#9a6f09"

COLUMNS = ("StringId", "Text", "Key", "State")


class StringTableDialog(QDialog):
    """Edit the mod's text and build ``strings/user_strings.res``."""

    def __init__(self, session, parent=None) -> None:
        super().__init__(parent)
        self.session = session
        self.setWindowTitle("Mod text")
        self.setMinimumSize(760, 460)
        layout = QVBoxLayout(self)

        self.lead = QLabel("")
        self.lead.setWordWrap(True)
        self.lead.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self.lead)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(list(COLUMNS))
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.table.itemChanged.connect(self._recompute)
        layout.addWidget(self.table, 1)

        row = QHBoxLayout()
        add = QPushButton("Add")
        add.clicked.connect(self.add_row)
        remove = QPushButton("Remove")
        remove.clicked.connect(self.remove_selected)
        row.addWidget(add)
        row.addWidget(remove)
        row.addStretch(1)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        row.addWidget(self.status, 1)
        layout.addLayout(row)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save
                                   | QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Save).setToolTip(
            "Write strings/user_strings.res and record the ids in the project")
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.reload()

    # -- contents ------------------------------------------------------------

    def reload(self) -> None:
        rows = self.session.strings()
        orphans = self.session.orphan_strings()
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        for entry in rows:
            self._append(entry["id"], entry["text"])
        self.table.blockSignals(False)
        self._recompute()

        note = ""
        if orphans:
            note = (f"<br><b>{len(orphans)}</b> key(s) in the built table have no id "
                    f"behind them. A table stores hashes only, so those texts can "
                    f"no longer be named — saving here drops them.")
        self.lead.setText(
            "Text the game can show. Scripts name a <b>StringId</b>; the table "
            "carries the words. Saving writes "
            "<code>strings/user_strings.res</code> and records the ids in the "
            "project file, which is the only place they survive." + note)

    def _append(self, identifier: str = "", text: str = "") -> None:
        # Signals stay blocked until every column exists: ``_recompute`` reads
        # all four, and a half-built row would fire it against missing cells.
        was_blocked = self.table.signalsBlocked()
        self.table.blockSignals(True)
        r = self.table.rowCount()
        self.table.insertRow(r)
        self.table.setItem(r, 0, QTableWidgetItem(identifier))
        self.table.setItem(r, 1, QTableWidgetItem(text))
        for column in (2, 3):
            item = QTableWidgetItem("")
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self.table.setItem(r, column, item)
        self.table.blockSignals(was_blocked)

    def pairs(self) -> List[tuple]:
        """The authored rows, skipping any with no id."""
        out = []
        for r in range(self.table.rowCount()):
            identifier = (self.table.item(r, 0).text() if self.table.item(r, 0) else "").strip()
            text = self.table.item(r, 1).text() if self.table.item(r, 1) else ""
            if identifier:
                out.append((identifier, text))
        return out

    # -- feedback ------------------------------------------------------------

    def _recompute(self) -> None:
        """Fill in the key and state columns, and report what would go wrong."""
        from dsotools.formats import res as resfmt

        self.table.blockSignals(True)
        seen = {}
        collisions = []
        shadowed = []
        for r in range(self.table.rowCount()):
            identifier = (self.table.item(r, 0).text() if self.table.item(r, 0) else "").strip()
            key_item = self.table.item(r, 2)
            state_item = self.table.item(r, 3)
            if not identifier:
                key_item.setText("")
                state_item.setText("no id")
                continue
            key = resfmt.string_hash(identifier)
            key_item.setText(f"0x{key:08x}")
            state, colour = "own text", None
            if key in seen and seen[key] != identifier:
                state, colour = f"collides with {seen[key]}", _UNBUILT
                collisions.append((seen[key], identifier))
            else:
                stock = self.session.stock_string(identifier)
                if stock is not None:
                    state, colour = "overrides stock text", _SHADOW
                    shadowed.append(identifier)
            seen.setdefault(key, identifier)
            state_item.setText(state)
            state_item.setForeground(QBrush(QColor(colour)) if colour
                                     else self.table.palette().text())
        self.table.blockSignals(False)

        if collisions:
            a, b = collisions[0]
            self.status.setText(
                f"<span style='color:{_UNBUILT}'>{a} and {b} hash alike — "
                f"rename one before saving.</span>")
        elif shadowed:
            self.status.setText(f"{len(shadowed)} id(s) already mean something "
                                f"in the stock game; yours will win.")
        else:
            self.status.setText(f"{len(self.pairs())} entr(y/ies).")

    # -- actions -------------------------------------------------------------

    def add_row(self) -> None:
        self._append("ID_", "")
        self._recompute()
        self.table.setCurrentCell(self.table.rowCount() - 1, 0)
        self.table.editItem(self.table.item(self.table.rowCount() - 1, 0))

    def remove_selected(self) -> None:
        for r in sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True):
            self.table.removeRow(r)
        self._recompute()

    def save(self) -> Optional[str]:
        try:
            written = self.session.save_strings(self.pairs())
        except DsoError as exc:
            QMessageBox.warning(self, "Mod text", str(exc))
            return None
        self.reload()
        self.status.setText(f"Written to {written}")
        return written
