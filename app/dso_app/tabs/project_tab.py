"""
The Project tab: which game, which mod, and what the mod actually changes.

The file table is the heart of it.  Every row says what the file *does* --
override, addition, identical to stock, or dead -- because "this mod contains
1,442 files" tells a user nothing and "3 of them will never be read" tells them
everything.
"""

from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QPlainTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from dsotools.project import FileState

from .. import theme

#: What each file state is called, and the diagnostic severity it corresponds
#: to.  The colour comes from that severity rather than being chosen here, so a
#: state and the rule that reports it cannot disagree: a dead file is
#: ``PRJ005``, a warning, and this tab used to paint it red while the Problems
#: tab painted the same finding amber.
#:
#: This replaced a deliberate choice -- "colour by consequence, not by
#: category; dead files cost people hours, so they must catch the eye" -- and
#: that argument was not wrong, it was aimed at the wrong control.  If a dead
#: file deserves the strongest colour then it deserves ``ERROR``, which is what
#: ``validate.py`` defines as "a change will not take effect".  Deciding that
#: is a severity question, not a palette one, and it belongs in one place.
#:
#: ``None`` means "no colour" -- ordinary content needs no marking.
_STATE_STYLE = {
    FileState.DEAD: ("never read by the engine", theme.SEVERITY["warning"]),
    FileState.IDENTICAL: ("identical to stock; no effect", theme.SEVERITY["hint"]),
    FileState.OVERRIDE: ("replaces a stock file", theme.SEVERITY["info"]),
    FileState.ADDITION: ("new content", None),
}


class ProjectTab(QWidget):
    def __init__(self, window) -> None:
        super().__init__()
        self.window = window
        self.session = window.session

        layout = QVBoxLayout(self)

        # -- sources ---------------------------------------------------------
        sources = QGroupBox("Sources")
        grid = QVBoxLayout(sources)

        row = QHBoxLayout()
        self.game_edit = QLineEdit()
        self.game_edit.setReadOnly(True)
        self.game_edit.setPlaceholderText("folder of extracted .cpr archives")
        btn_game = QPushButton("Game data…")
        btn_game.clicked.connect(window.choose_game)
        row.addWidget(QLabel("Game:"))
        row.addWidget(self.game_edit, 1)
        row.addWidget(btn_game)
        grid.addLayout(row)

        row = QHBoxLayout()
        self.mod_combo = QComboBox()
        self.mod_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.mod_combo.activated.connect(self._pick_discovered)
        btn_new = QPushButton("New…")
        btn_new.clicked.connect(self.create_mod)
        self.btn_props = QPushButton("Properties…")
        self.btn_props.setToolTip("Edit this mod's name, description and folder")
        self.btn_props.clicked.connect(self.edit_metadata)
        btn_mod = QPushButton("Browse…")
        btn_mod.clicked.connect(window.choose_mod)
        row.addWidget(QLabel("Mod:"))
        row.addWidget(self.mod_combo, 1)
        row.addWidget(btn_new)
        row.addWidget(self.btn_props)
        row.addWidget(btn_mod)
        grid.addLayout(row)

        self.summary = QLabel("No mod open.")
        self.summary.setWordWrap(True)
        grid.addWidget(self.summary)
        layout.addWidget(sources)

        # Files that go into the game installation rather than the mod folder.
        # Kept visible rather than tucked into a menu: it is the one thing this
        # app does that a user cannot undo by deleting a folder.
        from ..root_files import RootFilesPanel

        self.root_files = RootFilesPanel(window)
        layout.addWidget(self.root_files)

        # -- contents --------------------------------------------------------
        # Boxed and labelled for the same reason "Game folder files" is: the
        # two lists sit one above the other and answer different questions —
        # what the mod ships, versus what it puts in the installation. Unlabelled,
        # the lower one reads as a continuation of the upper.
        files_box = QGroupBox("Mod files")
        files_layout = QVBoxLayout(files_box)
        self.files_status = QLabel("")
        self.files_status.setWordWrap(True)
        self.files_status.setTextFormat(Qt.TextFormat.RichText)
        files_layout.addWidget(self.files_status)

        row = QHBoxLayout()
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("filter by path…")
        self.filter_edit.textChanged.connect(self._apply_filter)
        btn_validate = QPushButton("Validate")
        btn_validate.clicked.connect(window.run_validation)
        btn_index = QPushButton("Rebuild index")
        btn_index.clicked.connect(window.run_index)
        self.btn_deploy = QPushButton("Deploy…")
        self.btn_deploy.setToolTip(
            "Put this mod into the shape the engine actually reads:\n"
            "move loose 3DView/ files into user_data.zip, and add the\n"
            "inifiles/items.ini without which the game will not list it."
        )
        self.btn_deploy.clicked.connect(window.run_deploy)
        row.addWidget(self.filter_edit, 1)
        row.addWidget(btn_validate)
        row.addWidget(btn_index)
        row.addWidget(self.btn_deploy)
        files_layout.addLayout(row)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(5)
        self.tree.setHeaderLabels(["Path", "State", "Delivered", "Loads from", "Size"])
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setSortingEnabled(False)
        # The mod's own file list is the other place you want to jump from:
        # "this is the file I changed -- show me it".
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._file_menu)
        self.tree.itemDoubleClicked.connect(
            lambda item, _c: self._open_row(item)
        )
        header = self.tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        files_layout.addWidget(self.tree, 1)
        layout.addWidget(files_box, 1)

        self.refresh()

    # -- population ----------------------------------------------------------

    def refresh(self) -> None:
        self.game_edit.setText(self.session.game_path or "")
        self.root_files.refresh()
        self._refresh_mod_list()
        self.btn_props.setEnabled(self.session.mod is not None)
        self.btn_deploy.setEnabled(self.session.mod is not None)

        if not self.session.mod:
            self.summary.setText("No mod open.")
            self.summary.setToolTip("")
            self.files_status.setText(self._describe_files())
            self.tree.clear()
            return

        counts = self.session.mod_summary()
        parts = []
        for state in (FileState.OVERRIDE, FileState.ADDITION, FileState.IDENTICAL, FileState.DEAD):
            if counts.get(state):
                parts.append(f"{counts[state]} {_STATE_STYLE[state][0]}")
        base = self.session.base_game_status()
        # The folder name is shown because it is what the game keys the mod
        # selection on, and it is not always the display name.
        self.summary.setText(
            f"<b>{self.session.mod.display_name}</b> "
            f"<span style='color: palette(mid);'>({self.session.mod.name})</span><br>"
            + (" &nbsp;·&nbsp; ".join(parts) or "no files")
            + f"<br>base game: {base}"
        )
        self.summary.setToolTip(self.session.base_game_explanation())
        self.files_status.setText(self._describe_files(counts))
        self._populate()

    def _describe_files(self, counts=None) -> str:
        """What the list below means, and what is in it right now.

        The states are named here rather than only colour-coded in the tree,
        because two of them are the ones that bite: *identical to stock* does
        nothing at all, and *dead* is a file in a place the engine never reads
        — both look like ordinary content in a folder listing.
        """
        if not self.session.mod:
            return ("Everything the open mod ships, and where the engine reads "
                    "each file from.")
        total = sum((counts or {}).values())
        lead = (f"<b>{total} file(s)</b> in this mod." if total
                else "This mod has no files yet.")
        return (lead + " <b>Override</b> replaces a stock file, "
                "<b>addition</b> is new, <b>identical to stock</b> changes "
                "nothing, and <b>dead</b> sits where the engine never looks.")

    def _refresh_mod_list(self) -> None:
        """Rebuild the list, starting with a placeholder rather than a mod.

        Preselecting the first discovered mod makes the box *look* like that mod
        is open when nothing is -- the combo said "Tutorial" while the rest of
        the tab was empty. An explicit placeholder keeps the widget honest.
        """
        self.mod_combo.blockSignals(True)
        self.mod_combo.clear()
        self.mod_combo.addItem("Select an existing mod, or create a new one…", None)

        self._discovered = self.session.discover_mods()
        for m in self._discovered:
            try:
                label = m.display_name or m.name
            except Exception:  # noqa: BLE001
                label = m.name
            suffix = "" if m.is_listable() else "   ⚠ not listed by the game"
            self.mod_combo.addItem(f"{label}{suffix}", m.root)

        if self.session.mod:
            idx = self.mod_combo.findData(self.session.mod.root)
            if idx < 0:
                self.mod_combo.addItem(
                    self.session.mod.display_name or self.session.mod.name,
                    self.session.mod.root,
                )
                idx = self.mod_combo.count() - 1
            self.mod_combo.setCurrentIndex(idx)
        else:
            self.mod_combo.setCurrentIndex(0)
        self.mod_combo.blockSignals(False)

    def _pick_discovered(self, index: int) -> None:
        root = self.mod_combo.itemData(index)
        if not root:
            return                      # the placeholder
        self.window.open_mod_async(root)

    def create_mod(self) -> None:
        dialog = NewModDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        name, desc = dialog.values()
        self.window.guard(
            lambda: self.session.create_mod(name, desc), "create the mod"
        )

    def edit_metadata(self) -> None:
        if not self.session.mod:
            return
        dialog = ModPropertiesDialog(self.session, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        name, desc, folder = dialog.values()
        self.window.guard(
            lambda: self.session.update_mod_metadata(name, desc, folder),
            "update the mod",
        )

    def _populate(self) -> None:
        self.tree.clear()
        for row in self.session.mod_tree():
            label, colour = _STATE_STYLE.get(row["state"], (row["state"], None))
            # `items.ini` identical to stock is not dead weight, and saying so
            # is how PRJ002 once talked authors into deleting it -- which makes
            # the game refuse to list the mod, silently.  Its presence is what
            # counts, not its contents.
            if row.get("required") and row["state"] == FileState.IDENTICAL:
                label = "identical to stock, and required"
                colour = None
            item = QTreeWidgetItem(
                [
                    row["vpath"],
                    label,
                    row["source"],
                    row["stock_origin"] or "",
                    f"{row['size']:,}",
                ]
            )
            if colour:
                brush = QBrush(QColor(colour))
                for col in range(item.columnCount()):
                    item.setForeground(col, brush)
            if row["state"] == FileState.DEAD:
                item.setToolTip(
                    0,
                    "A mod's loose 3DView/ folder is never read by the engine.\n"
                    "Move this into user_data.zip or it will do nothing.",
                )
            elif row.get("required"):
                item.setToolTip(
                    0,
                    "Keep this file. A mod without inifiles/items.ini is not "
                    "listed by the game at all,\nand nothing says why — so it "
                    "is worth having even when it matches stock exactly.",
                )
            item.setTextAlignment(4, Qt.AlignmentFlag.AlignRight)
            self.tree.addTopLevelItem(item)
        self._apply_filter(self.filter_edit.text())

    def _reset_to_stock(self, vpath: str) -> None:
        """The shared action, so this tab and the Models tab say the same thing.

        The refresh comes back through Session's own "mod" notification, the
        same one a save uses -- so the diff tree, the problem list, the preview
        cache and both asset panels update together instead of one at a time.
        """
        from ..linked_assets import reset_asset_to_stock

        reset_asset_to_stock(self, self.session, vpath)

    def _remove_from_mod(self, vpath: str) -> None:
        """Take a file out of the mod, having said what that costs.

        The notes are the session's own words rather than this tab's: what a
        removal breaks is a fact about the mod, and a dialog that phrases it
        independently is a second opinion waiting to disagree.
        """
        from PySide6.QtWidgets import QMessageBox

        from dsotools.errors import DsoError

        notes = self.session.removal_notes(vpath)
        body = f"Remove <b>{vpath}</b> from this mod?"
        if notes:
            body += "<br><br>" + "<br><br>".join(notes)
        if QMessageBox.question(
            self, "Remove from the mod", body,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            self.session.remove_from_mod(vpath)
        except DsoError as exc:
            QMessageBox.warning(self, "Remove from the mod", str(exc))
            return
        self.window.status_label.setText(f"removed {vpath}")

    def _apply_filter(self, text: str) -> None:
        needle = text.strip().lower()
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            item.setHidden(bool(needle) and needle not in item.text(0).lower())

    # -- acting on a file ----------------------------------------------------

    def _open_row(self, item) -> None:
        if item is not None:
            self.window.open_asset(item.text(0))

    def _file_menu(self, pos) -> None:
        """The same actions the Models and Textures tabs offer, from here.

        This list is where an author looks at what their mod actually changes,
        so it is the natural place to ask "show me that" or "what else uses
        it" -- rather than reading a path and going hunting for it by hand.
        """
        from PySide6.QtWidgets import QApplication, QMenu, QMessageBox

        from dsotools.errors import DsoError

        item = self.tree.itemAt(pos)
        if item is None:
            return
        vpath = item.text(0)

        menu = QMenu(self.tree)
        act_open = menu.addAction("Open in its tab")
        act_open.setEnabled(
            self.window.ASSET_ROUTES.get(os.path.splitext(vpath)[1].lower())
            is not None
        )
        act_preview = menu.addAction("Preview…")
        act_uses = menu.addAction("What uses this…")
        menu.addSeparator()
        # One delete, named for what it actually does to *this* file.  Dropping
        # an override lets the stock file show through; dropping an addition
        # means it is gone.  Offering one word for both was how "reset" ended
        # up disabled on every file a mod had added, with the explanation
        # "delete it outside the app".
        if self.session.removal_kind(vpath) == "reset":
            act_remove = menu.addAction("Reset to stock…")
            why = self.session.can_reset_to_stock(vpath)
        else:
            act_remove = menu.addAction("Remove from the mod…")
            why = self.session.can_remove_from_mod(vpath)
        act_remove.setEnabled(why is None)
        if why is not None:
            # Disabled *and* explained.  A greyed-out item with no reason is
            # how people conclude the app is broken.
            act_remove.setToolTip(why)
        menu.addSeparator()
        act_export = menu.addAction("Export…")
        act_copy = menu.addAction("Copy path")
        menu.setToolTipsVisible(True)

        chosen = menu.exec(self.tree.viewport().mapToGlobal(pos))
        if chosen is act_open:
            self.window.open_asset(vpath)
        elif chosen is act_preview:
            from ..asset_preview import AssetPreviewDialog

            try:
                AssetPreviewDialog(self, self.session, vpath).exec()
            except DsoError as exc:
                QMessageBox.warning(self, "Preview", str(exc))
        elif chosen is act_uses:
            from ..linked_assets import show_users

            show_users(self, self.session, vpath)
        elif chosen is act_remove:
            if self.session.removal_kind(vpath) == "reset":
                self._reset_to_stock(vpath)
            else:
                self._remove_from_mod(vpath)
        elif chosen is act_export:
            from ..linked_assets import export_asset

            export_asset(self, self.session, vpath)
        elif chosen is act_copy:
            QApplication.clipboard().setText(vpath)



class DeployDialog(QDialog):
    """Say what deploying will do, then let it happen.

    Three states, and the difference between them is the whole point:

    * **nothing to do** -- the mod already loads the way it is laid out.  OK is
      disabled; there is no such thing as a deploy that changes nothing here,
      and offering one invites the user to click it and wonder what happened.
    * **ready** -- the plan is listed, line by line, before anything is written.
    * **blocked** -- validation found errors deploying will not fix.  The
      override exists, but it is a checkbox the user has to tick after reading
      what they are overriding, not a second button next to OK.
    """

    def __init__(self, gate, parent=None) -> None:
        super().__init__(parent)
        self.gate = gate
        self.setWindowTitle("Deploy mod")
        self.setMinimumWidth(560)

        layout = QVBoxLayout(self)

        lead = QLabel(
            "Deploying writes this mod into the layout the engine actually "
            "reads. It does not change what your files contain."
        )
        lead.setWordWrap(True)
        layout.addWidget(lead)

        lines = gate.plan.summary()
        plan_box = QGroupBox("What will change")
        plan_layout = QVBoxLayout(plan_box)
        if lines:
            for text in lines:
                item = QLabel("•  " + text)
                item.setWordWrap(True)
                plan_layout.addWidget(item)
        else:
            nothing = QLabel(
                "Nothing. Every file in this mod is already somewhere the "
                "engine reads it."
            )
            nothing.setWordWrap(True)
            nothing.setStyleSheet("color: palette(mid);")
            plan_layout.addWidget(nothing)
        layout.addWidget(plan_box)

        self.override = QCheckBox()
        if gate.blocked:
            problems = QGroupBox(f"{len(gate.blockers)} error(s) block this")
            pl = QVBoxLayout(problems)
            for text in gate.blocker_lines()[:8]:
                one = QLabel("•  " + text)
                one.setWordWrap(True)
                one.setStyleSheet("color: #c0392b;")
                pl.addWidget(one)
            if len(gate.blockers) > 8:
                pl.addWidget(QLabel(f"…and {len(gate.blockers) - 8} more (see Problems)."))
            layout.addWidget(problems)

            self.override.setText("Deploy anyway — I have read the errors above")
            layout.addWidget(self.override)
        elif gate.unvalidated:
            warn = QLabel(
                "This mod was not validated, so nothing was checked before "
                "deploying."
            )
            warn.setWordWrap(True)
            warn.setStyleSheet("color: #b9770e;")
            layout.addWidget(warn)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self._ok.setText("Deploy")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.override.toggled.connect(self._update_ok)
        self._update_ok()

    def _update_ok(self) -> None:
        ok = not self.gate.plan.empty
        if self.gate.blocked:
            ok = ok and self.override.isChecked()
        self._ok.setEnabled(ok)

    def forced(self) -> bool:
        return self.override.isChecked()


class NewModDialog(QDialog):
    """Name and description; everything else the game needs is written for you."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("New mod")
        self.setMinimumWidth(460)

        form = QFormLayout(self)
        self.name = QLineEdit()
        self.name.setPlaceholderText("Shown in the game's mod list")
        self.desc = QPlainTextEdit()
        self.desc.setPlaceholderText("A short description (about 350 characters)")
        self.desc.setFixedHeight(80)
        form.addRow("Name:", self.name)
        form.addRow("Description:", self.desc)

        note = QLabel(
            "The manifest and a stock <code>inifiles/items.ini</code> are written "
            "for you — without that file the game silently refuses to list a mod."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: palette(mid);")
        form.addRow(note)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self._ok.setEnabled(False)
        self.name.textChanged.connect(lambda t: self._ok.setEnabled(bool(t.strip())))
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def values(self):
        return self.name.text().strip(), self.desc.toPlainText().strip()


class ModPropertiesDialog(QDialog):
    """Edit a mod's metadata, and rename its folder as a separate decision.

    Name and folder are deliberately two fields.  They are two different things
    to the game -- ``mod_name`` is what the mod list displays, the folder is
    what ``mod.ini`` records as the selected mod -- and folding them together
    would mean a harmless-looking typo fix in the display name silently moved a
    directory and deselected the user's active mod.

    So: editing the name leaves the folder alone, the rename checkbox has to be
    ticked on purpose, and if the mod is the currently selected one the dialog
    says what that will cost *before* the user commits.
    """

    def __init__(self, session, parent=None) -> None:
        super().__init__(parent)
        self.session = session
        mod = session.mod
        self.setWindowTitle("Mod properties")
        self.setMinimumWidth(520)

        form = QFormLayout(self)

        self.name = QLineEdit(mod.display_name or "")
        self.name.setPlaceholderText("Shown in the game's mod list")
        self.desc = QPlainTextEdit(mod.description or "")
        self.desc.setFixedHeight(80)
        form.addRow("Name:", self.name)
        form.addRow("Description:", self.desc)

        self._original_folder = mod.name
        self.rename_check = QCheckBox("Also rename the folder on disk")
        self.folder = QLineEdit(mod.name)
        self.folder.setEnabled(False)
        self.rename_check.toggled.connect(self.folder.setEnabled)
        self.rename_check.toggled.connect(self._update_warning)
        self.folder.textChanged.connect(self._update_warning)
        form.addRow(self.rename_check)
        form.addRow("Folder:", self.folder)

        self.warning = QLabel()
        self.warning.setWordWrap(True)
        self.warning.setStyleSheet("color: #b9770e;")
        self.warning.hide()
        form.addRow(self.warning)

        # A ';' cannot be stored -- the game reads the rest of the line as a
        # comment.  Said here, while the text is still in front of the user,
        # rather than as an error box after they press OK.
        self.value_error = QLabel()
        self.value_error.setWordWrap(True)
        self.value_error.setStyleSheet("color: #c0392b;")
        self.value_error.hide()
        form.addRow(self.value_error)

        path = QLabel(f"<code>{mod.root}</code>")
        path.setWordWrap(True)
        path.setStyleSheet("color: palette(mid);")
        form.addRow(path)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        self._ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        self.name.textChanged.connect(self._update_ok)
        self.folder.textChanged.connect(self._update_ok)
        self.desc.textChanged.connect(self._update_ok)
        self.rename_check.toggled.connect(self._update_ok)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self._update_ok()

    def _update_ok(self) -> None:
        problem = self._value_problem()
        self.value_error.setText(problem or "")
        self.value_error.setVisible(bool(problem))

        ok = bool(self.name.text().strip()) and not problem
        if self.rename_check.isChecked():
            ok = ok and bool(self.folder.text().strip())
        self._ok.setEnabled(ok)

    def _value_problem(self):
        """Reuse the library's own rule rather than restating it here.

        A second copy of "what may appear in a value" in the widget layer is a
        second thing to keep in step, and the one that drifts is always the
        copy the user actually meets.
        """
        from dsotools.errors import DsoError
        from dsotools.formats import ini

        for label, text in (("Name", self.name.text()),
                            ("Description", self.desc.toPlainText())):
            try:
                ini.check_value(text)
            except DsoError as exc:
                return f"{label}: {exc}"
        return None

    def _update_warning(self) -> None:
        text = self.session.mod_rename_warning(self._chosen_folder() or "")
        self.warning.setText(text or "")
        self.warning.setVisible(bool(text))

    def _chosen_folder(self):
        if not self.rename_check.isChecked():
            return None
        folder = self.folder.text().strip()
        return folder if folder and folder != self._original_folder else None

    def values(self):
        return (
            self.name.text().strip(),
            self.desc.toPlainText().strip(),
            self._chosen_folder(),
        )


__all__ = ["ProjectTab", "DeployDialog"]
