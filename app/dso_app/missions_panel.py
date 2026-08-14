"""
Starting a mission script -- new, or replacing a stock one.

There is no "replace a mission" API in the engine. ``NScript.Register`` keys
the mission table by ``Name``, and a mod's ``scripts\\`` is read after
``lua/mission/missions.bin``, so registering an existing name overwrites that
mission and registering a new one adds it. The same call does both, which is
exactly why this needs a UI: the difference between extending the game and
silently disabling a stock mission is one string, and nothing warns you.

So the two cases are separate tabs here, and each says which it is. Picking a
stock mission fills the template with that mission's real type, group and
states, because a state the override leaves out stops existing.

Nothing here reads a bundle or writes Lua; :mod:`dso_app.session` does that.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QSpinBox,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from dsotools import missions as missionsmod
from dsotools.errors import DsoError

#: Types the reference does not list but the stock bundle uses.
UNDOCUMENTED = ("MTYPE_STORY_CHAPTER", "MTYPE_MENU")


class MissionDialog(QDialog):
    """Create a mission script in the open mod.

    ``written`` holds the path afterwards so the caller can open it.
    """

    #: Which tab is the overriding one.  Named rather than written as a literal
    #: in three places, because the order is a UI decision that has changed
    #: once already.
    NEW_TAB = 0
    OVERRIDE_TAB = 1

    def __init__(self, session, parent=None) -> None:
        super().__init__(parent)
        self.session = session
        self.written: Optional[str] = None
        self.setWindowTitle("New mission")
        self.setMinimumSize(720, 520)

        layout = QVBoxLayout(self)
        self.tabs = QTabWidget()
        # New mission first, and selected: writing one is the common case, and
        # replacing a stock mission is the deliberate one. A dialog that opens
        # on the destructive half invites it to happen by accident.
        self.tabs.addTab(self._new_page(), "New mission")
        self.tabs.addTab(self._override_page(), "Override a stock mission")
        self.tabs.currentChanged.connect(lambda _i: self._sync())
        layout.addWidget(self.tabs, 1)

        self.note = QLabel("")
        self.note.setWordWrap(True)
        self.note.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self.note)

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                        | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Create")
        self.buttons.accepted.connect(self.create)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        self._fill_stock()
        self._sync()

    # -- pages ----------------------------------------------------------------

    def _override_page(self) -> QWidget:
        page = QWidget()
        pl = QVBoxLayout(page)
        lead = QLabel(
            "Registering a stock mission's <b>Name</b> replaces it. The script "
            "starts with that mission's real type, group and states — a state "
            "you leave out stops existing for it.")
        lead.setWordWrap(True)
        pl.addWidget(lead)

        self.filter = QLineEdit()
        self.filter.setPlaceholderText("filter by name, type or state…")
        self.filter.textChanged.connect(self._apply_filter)
        pl.addWidget(self.filter)

        self.stock = QTreeWidget()
        self.stock.setColumnCount(4)
        self.stock.setHeaderLabels(["Mission", "Type", "States", "In this mod"])
        self.stock.setRootIsDecorated(False)
        self.stock.setAlternatingRowColors(True)
        self.stock.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.stock.itemSelectionChanged.connect(self._sync)
        pl.addWidget(self.stock, 1)
        return page

    def _new_page(self) -> QWidget:
        page = QWidget()
        pl = QVBoxLayout(page)
        lead = QLabel(
            "A name no stock mission uses. If it clashes with one, the stock "
            "mission is replaced instead of a new one being added, so the name "
            "is checked before anything is written.")
        lead.setWordWrap(True)
        pl.addWidget(lead)

        row = QHBoxLayout()
        row.addWidget(QLabel("Name"))
        self.new_name = QLineEdit()
        self.new_name.setPlaceholderText("MY_PATROL")
        self.new_name.textChanged.connect(self._sync)
        row.addWidget(self.new_name, 1)
        row.addWidget(QLabel("Type"))
        self.new_type = QComboBox()
        for kind in list(missionsmod.MISSION_TYPES) + list(UNDOCUMENTED):
            self.new_type.addItem(kind
                                  + (" (undocumented)" if kind in UNDOCUMENTED else ""),
                                  kind)
        self.new_type.setCurrentIndex(
            list(missionsmod.MISSION_TYPES).index("MTYPE_ALWAYS"))
        row.addWidget(self.new_type)
        row.addWidget(QLabel("Group"))
        self.new_group = QSpinBox()
        self.new_group.setMaximum(999)
        row.addWidget(self.new_group)
        pl.addLayout(row)

        pl.addWidget(QLabel("States — one stub per selected state:"))
        self.states = QListWidget()
        self.states.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        for state in self.session.mission_states():
            item = QListWidgetItem(state)
            self.states.addItem(item)
            if state in missionsmod.DEFAULT_STATES:
                item.setSelected(True)
        self.states.itemSelectionChanged.connect(self._sync)
        pl.addWidget(self.states, 1)
        return page

    # -- contents -------------------------------------------------------------

    def _fill_stock(self) -> None:
        self.stock.clear()
        for row in self.session.stock_missions():
            item = QTreeWidgetItem([
                row["name"],
                row["type"] or "?",
                f"{len(row['states'])}: " + ", ".join(row["states"][:4])
                + (" …" if len(row["states"]) > 4 else ""),
                row["overridden"] or "",
            ])
            item.setData(0, Qt.ItemDataRole.UserRole, row)
            self.stock.addTopLevelItem(item)
        for column in range(3):
            self.stock.resizeColumnToContents(column)

    def _apply_filter(self, text: str) -> None:
        needle = text.strip().lower()
        for i in range(self.stock.topLevelItemCount()):
            item = self.stock.topLevelItem(i)
            row = item.data(0, Qt.ItemDataRole.UserRole)
            haystack = " ".join([row["name"], row["type"] or ""] + row["states"]).lower()
            item.setHidden(bool(needle) and needle not in haystack)

    def selected_mission(self) -> Optional[dict]:
        items = self.stock.selectedItems()
        return items[0].data(0, Qt.ItemDataRole.UserRole) if items else None

    # -- feedback -------------------------------------------------------------

    def _sync(self) -> None:
        overriding = self.tabs.currentIndex() == self.OVERRIDE_TAB
        ok = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        if overriding:
            row = self.selected_mission()
            if row is None:
                self.note.setText("Pick a mission to replace.")
                ok.setEnabled(False)
                return
            existing = row["overridden"]
            self.note.setText(
                f"Writes <code>scripts/{row['name']}.lua</code>, replacing the "
                f"stock <b>{row['name']}</b> ({row['type']}, "
                f"{len(row['states'])} states)."
                + (f"<br><b>This mod already registers it</b> in "
                   f"<code>{existing}</code> — creating it again would overwrite "
                   f"that file." if existing else ""))
            ok.setEnabled(True)
            return

        name = self.new_name.text().strip()
        chosen = [i.text() for i in self.states.selectedItems()]
        if not name:
            self.note.setText("Give the mission a name.")
            ok.setEnabled(False)
            return
        clash = self.session.stock_mission(name)
        if clash is not None:
            self.note.setText(
                f"<b>{name}</b> is a stock mission ({clash['type']}). Registering "
                f"it would replace that mission — use the other tab if that is "
                f"what you want.")
            ok.setEnabled(False)
            return
        self.note.setText(
            f"Writes <code>scripts/{name}.lua</code> with "
            f"{len(chosen) or len(missionsmod.DEFAULT_STATES)} state stub(s).")
        ok.setEnabled(True)

    # -- action ---------------------------------------------------------------

    def create(self) -> Optional[str]:
        try:
            if self.tabs.currentIndex() == self.OVERRIDE_TAB:
                row = self.selected_mission()
                if row is None:
                    return None
                if row["overridden"] and not self._confirm_overwrite(row["overridden"]):
                    return None
                self.written = self.session.create_mission_override(
                    row["name"], overwrite=bool(row["overridden"]))
            else:
                chosen = [i.text() for i in self.states.selectedItems()]
                self.written = self.session.create_mission(
                    self.new_name.text().strip(),
                    type=self.new_type.currentData(),
                    group=self.new_group.value(),
                    states=chosen or None)
        except DsoError as exc:
            QMessageBox.warning(self, "New mission", str(exc))
            return None
        self.accept()
        return self.written

    def _confirm_overwrite(self, file_name: str) -> bool:
        answer = QMessageBox.question(
            self, "New mission",
            f"{file_name} already registers this mission. Replace that file "
            f"with a fresh template? Anything written in it is lost.")
        return answer == QMessageBox.StandardButton.Yes
