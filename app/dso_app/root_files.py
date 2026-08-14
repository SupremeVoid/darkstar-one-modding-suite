"""
The Project tab's panel for files that go into the **game installation**.

WHY THIS IS ITS OWN PANEL
-------------------------
Everything else this app writes lands inside a mod folder, and deleting that
folder undoes it.  This does not: no ``.cpr`` archive holds a single ``lua/``
entry, so a mod that changes the shared mission libraries has to overwrite the
installation itself.  One large mod ships a second archive for exactly
that, and its readme says "copy into game root".

Copied by hand, that is a one-way door -- the stock ``MissionLib.lua`` it
replaces has no other copy anywhere.  So this panel exists to make the same
change **reversible**, and to say out loud which of the two situations the user
is in:

* installed through here -- the displaced original is in the vault, and
  Uninstall puts it back;
* already in place when we found it -- ownership can be *adopted* so the state
  is at least describable, but nothing can be restored, and the panel says so
  rather than offering a button that would quietly delete a file the game
  needs.

Nothing here parses or writes anything itself; every operation is one
:mod:`dso_app.session` call, which is one :mod:`dsotools.rootfiles` call.
"""

from __future__ import annotations

from typing import List

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
)

from dsotools.errors import DsoError

from . import workers

#: An untracked difference: nothing owns it, so nothing can undo it.
_UNTRACKED = "#c0392b"

#: How each planned action reads to a person.
WHAT = {
    "new": "add (nothing there now)",
    "replace": "overwrite, keeping a backup of the original",
    "update": "update this mod's own file",
    "conflict": "another mod owns this",
    "same": "already identical",
}


class RootPlanDialog(QDialog):
    """What installing would do, before it does it.

    The same shape as the deploy gate: a plan you can read, and a button that
    is only enabled when the plan is safe to run.
    """

    def __init__(self, rows: List[dict], game_path: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Install into the game folder")
        self.setMinimumWidth(680)
        layout = QVBoxLayout(self)

        conflicts = [r for r in rows if r["what"] == "conflict"]
        replacing = [r for r in rows if r["what"] == "replace"]

        lead = QLabel(
            f"<b>This writes into the game installation</b>, not into the mod "
            f"folder:<br><code>{game_path}</code><br><br>"
            f"{len(rows)} file(s). Originals of the "
            f"{len(replacing)} file(s) being overwritten are copied into "
            f"<code>.dso_backup</code> inside the game folder first, so "
            f"Uninstall can put them back."
        )
        lead.setWordWrap(True)
        layout.addWidget(lead)

        tree = QTreeWidget()
        tree.setColumnCount(3)
        tree.setHeaderLabels(["File", "What happens", "Owner"])
        tree.setRootIsDecorated(False)
        tree.setAlternatingRowColors(True)
        for row in rows:
            item = QTreeWidgetItem([row["path"], WHAT.get(row["what"], row["what"]),
                                    row.get("owner") or ""])
            if row["what"] == "conflict":
                item.setForeground(1, Qt.GlobalColor.red)
            tree.addTopLevelItem(item)
        for column in range(3):
            tree.resizeColumnToContents(column)
        layout.addWidget(tree, 1)

        if conflicts:
            warning = QLabel(
                f"<b>{len(conflicts)} file(s) belong to another mod.</b> "
                f"Installing over them would make that mod unremovable. "
                f"Uninstall it first, or use Swap."
            )
            warning.setWordWrap(True)
            layout.addWidget(warning)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        ok = buttons.button(QDialogButtonBox.StandardButton.Ok)
        ok.setText("Install")
        ok.setEnabled(not conflicts)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class InstallationStateDialog(QDialog):
    """Everything in the game folder that is not the stock file.

    The stock state is recorded data, not something measured from whatever
    happens to be installed, so this list means the same thing on a machine
    that has been modded for years as on a fresh one.

    ``added`` and ``modified`` entries with no owner are the ones worth acting
    on: nothing is tracking them, so nothing can put them back.
    """

    def __init__(self, rows: List[dict], edition, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("What is not stock")
        self.setMinimumSize(720, 420)
        self.chosen: List[str] = []
        layout = QVBoxLayout(self)

        unclaimed = [r for r in rows if not r["owner"] and r["state"] != "missing"]
        lead = QLabel(
            f"Installation detected as <b>{edition or 'unknown'}</b>. "
            + (f"<b>{len(rows)} file(s)</b> differ from a stock install."
               if rows else
               "Every file matches the recorded stock state.")
            + (f"<br><br><b>{len(unclaimed)}</b> of them are not tracked by any "
               f"mod, so nothing can restore them. Adopting them records which "
               f"mod owns them — it cannot recover the originals, which are "
               f"already gone."
               if unclaimed else ""))
        lead.setWordWrap(True)
        layout.addWidget(lead)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["File", "State", "Tracked by"])
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        for row in rows:
            item = QTreeWidgetItem([row["path"], row["state"],
                                    row["owner"] or "—"])
            if not row["owner"] and row["state"] != "missing":
                item.setForeground(2, QBrush(QColor(_UNTRACKED)))
            item.setData(0, Qt.ItemDataRole.UserRole, row)
            self.tree.addTopLevelItem(item)
        for column in range(3):
            self.tree.resizeColumnToContents(column)
        layout.addWidget(self.tree, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        if unclaimed:
            adopt = buttons.addButton("Adopt the untracked ones",
                                      QDialogButtonBox.ButtonRole.AcceptRole)
            adopt.setToolTip("Record them as the open mod's. No original is "
                             "recovered — none was kept.")
            self.chosen = [r["path"] for r in unclaimed]
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class RootFilesPanel(QGroupBox):
    """Install / uninstall / adopt a mod's install-folder payload."""

    def __init__(self, window) -> None:
        super().__init__("Game folder files")
        self.window = window
        self.session = window.session

        layout = QVBoxLayout(self)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self.status)

        row = QHBoxLayout()
        self.btn_install = QPushButton("Install…")
        self.btn_install.setToolTip(
            "Copy this mod's root/ payload into the game installation, "
            "backing up whatever it displaces")
        self.btn_install.clicked.connect(self.install)
        self.btn_uninstall = QPushButton("Uninstall")
        self.btn_uninstall.setToolTip(
            "Put the originals back and remove what this mod added")
        self.btn_uninstall.clicked.connect(self.uninstall)
        self.btn_verify = QPushButton("Verify")
        self.btn_verify.setToolTip(
            "Check the installed files still match what was installed")
        self.btn_verify.clicked.connect(self.verify)
        self.btn_import = QPushButton("Import from zip…")
        self.btn_import.setToolTip(
            "Take a “copy this into the game root” archive into the mod, "
            "so it becomes something this tool can install and undo")
        self.btn_import.clicked.connect(self.import_zip)
        self.btn_scan = QPushButton("What is not stock…")
        self.btn_scan.setToolTip(
            "Compare this installation with the recorded stock state and list "
            "everything that differs, with whichever mod is tracking it")
        self.btn_scan.clicked.connect(self.scan_installation)
        self.btn_adopt = QPushButton("Adopt in place")
        self.btn_adopt.setToolTip(
            "Record files already sitting in the game folder as this mod's. "
            "Nothing can be restored afterwards — no original was kept")
        self.btn_adopt.clicked.connect(self.adopt)
        for button in (self.btn_install, self.btn_uninstall, self.btn_verify,
                       self.btn_import, self.btn_adopt, self.btn_scan):
            row.addWidget(button)
        row.addStretch(1)
        layout.addLayout(row)

        self.files = QTreeWidget()
        self.files.setColumnCount(4)
        self.files.setHeaderLabels(["File", "State", "Size", "In the game folder"])
        self.files.setRootIsDecorated(False)
        self.files.setAlternatingRowColors(True)
        self.files.setMaximumHeight(150)
        layout.addWidget(self.files)

        self.refresh()

    # -- state ---------------------------------------------------------------

    def refresh(self) -> None:
        self.files.clear()
        payload = self.session.root_payload() if self.session.mod else []
        installed = self.session.installed_root_mods()
        others = [m for m in installed
                  if not self.session.mod or m["name"] != self.session.mod.name]
        mine = next((m for m in installed
                     if self.session.mod and m["name"] == self.session.mod.name), None)

        for row in payload:
            state = "installed" if row["installed"] else "not installed"
            if row["installed"] and not row["current"]:
                state = "installed, changed since"
            item = QTreeWidgetItem([
                row["path"], state, f"{row['size']:,}",
                "yes" if row["in_game"] else "no",
            ])
            self.files.addTopLevelItem(item)
        for column in range(4):
            self.files.resizeColumnToContents(column)

        self.status.setText(self._describe(payload, mine, others))
        has_game = bool(self.session.game_path)
        self.btn_install.setEnabled(bool(payload) and has_game)
        self.btn_uninstall.setEnabled(mine is not None)
        self.btn_verify.setEnabled(bool(installed))
        self.btn_import.setEnabled(bool(self.session.mod))
        self.btn_scan.setEnabled(has_game)
        self.btn_adopt.setEnabled(
            bool(has_game and self.session.mod
                 and self.session.unclaimed_root_files()))
        if others:
            names = ", ".join(m["name"] for m in others)
            self.btn_install.setText(f"Swap from {others[0]['name']}…"
                                     if len(others) == 1 else "Install…")
            self.btn_install.setToolTip(
                f"{names} currently has files in the game folder. Installing "
                f"this mod's payload takes those out first.")
        else:
            self.btn_install.setText("Install…")

    def _describe(self, payload, mine, others) -> str:
        if not self.session.mod:
            return "Open a mod to see whether it needs files in the game folder."
        if not payload and not mine:
            return (
                "This mod delivers nothing into the game installation — which "
                "is the good case.<br>"
                "Some content cannot be delivered from the mod folder at all: "
                "the shared mission libraries under <code>lua/</code> are in no "
                "archive, so a mod that changes them must overwrite the "
                "installation. If this mod ships a second "
                "“copy into game root” archive, import it here."
            )
        lines = [f"<b>{len(payload)} file(s)</b> are delivered into the game "
                 f"installation."]
        if mine:
            restorable = mine["restorable"]
            lines.append(
                f"Installed: {mine['files']} file(s)."
                + (f" <b>{restorable}</b> can be put back on uninstall."
                   if restorable else
                   " <b>Nothing can be put back</b> — these were adopted in "
                   "place, so no original was ever kept.")
            )
        if others:
            lines.append(
                "Also installed here: "
                + ", ".join(f"{m['name']} ({m['files']})" for m in others)
                + ".")
        return "<br>".join(lines)

    # -- actions -------------------------------------------------------------

    def install(self) -> None:
        try:
            rows = self.session.root_plan()
        except DsoError as exc:
            QMessageBox.warning(self, "Install", str(exc))
            return
        if not rows:
            QMessageBox.information(self, "Install", "There is nothing to install.")
            return

        dialog = RootPlanDialog(rows, self.session.game_path or "", self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        others = [m["name"] for m in self.session.installed_root_mods()
                  if m["name"] != self.session.mod.name]
        try:
            if others:
                result = self.session.swap_root_files(others[0])
            else:
                result = self.session.install_root_files()
        except (DsoError, OSError) as exc:
            QMessageBox.warning(self, "Install", str(exc))
            return
        self._report("Install", result)

    def uninstall(self) -> None:
        name = self.session.mod.name if self.session.mod else ""
        mine = next((m for m in self.session.installed_root_mods()
                     if m["name"] == name), None)
        warning = ""
        if mine and not mine["restorable"]:
            warning = ("<br><br><b>No original was ever kept for these files.</b> "
                       "They were adopted in place, so they will be left where "
                       "they are and only the record of them is dropped. To get "
                       "the stock files back, verify the game's files in Steam.")
        if QMessageBox.question(
            self, "Uninstall",
            f"Take {name}'s files out of the game installation?" + warning,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            result = self.session.uninstall_root_files()
        except (DsoError, OSError) as exc:
            QMessageBox.warning(self, "Uninstall", str(exc))
            return
        self._report("Uninstall", result)

    def verify(self) -> None:
        problems = self.session.verify_root_files()
        if not problems:
            QMessageBox.information(
                self, "Verify",
                "Every file the ledger records is present and unchanged.")
            return
        listing = "<br>".join(f"{path} — {why}"
                              for path, why in sorted(problems.items())[:20])
        more = ("<br>…and more" if len(problems) > 20 else "")
        QMessageBox.warning(
            self, "Verify",
            f"<b>{len(problems)} file(s) no longer match:</b><br>{listing}{more}")

    def import_zip(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Import a game-root archive", "", "Zip archives (*.zip)")
        if not path:
            return
        try:
            written = self.session.import_root_zip(path)
        except (DsoError, OSError) as exc:
            QMessageBox.warning(self, "Import", str(exc))
            return
        self.refresh()
        listing = "<br>".join(written[:15])
        more = f"<br>…and {len(written) - 15} more" if len(written) > 15 else ""
        QMessageBox.information(
            self, "Import",
            f"<b>{len(written)} file(s)</b> are now part of this mod's "
            f"install-folder payload:<br>{listing}{more}<br><br>"
            f"Nothing has been written into the game yet — use Install for that."
            if written else
            "That archive held nothing that belongs in the game folder.")

    def adopt(self) -> None:
        unclaimed = self.session.unclaimed_root_files()
        if not unclaimed:
            return
        differing = [u for u in unclaimed if not u["identical"]]
        if QMessageBox.question(
            self, "Adopt in place",
            f"Record {len(unclaimed)} file(s) already in the game folder as "
            f"{self.session.mod.name}'s?<br><br>"
            f"This makes the state describable — which mod owns what — but "
            f"<b>no original can be recovered</b>, because none was kept when "
            f"they were copied in."
            + (f"<br><br>{len(differing)} of them differ from this mod's copy, "
               f"so they may be from a different version."
               if differing else ""),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            result = self.session.adopt_root_files([u["path"] for u in unclaimed])
        except (DsoError, OSError) as exc:
            QMessageBox.warning(self, "Adopt", str(exc))
            return
        self._report("Adopt in place", result)

    def scan_installation(self) -> None:
        """Compare the installation with the recorded stock state.

        Runs on a worker: the comparison hashes every file up to 4 MB, which is
        about a second, and a second of a frozen window is a second too many.
        """
        self.btn_scan.setEnabled(False)
        self.btn_scan.setText("checking…")
        workers.run(
            self.session.non_stock_files,
            on_result=self._scanned,
            on_error=self._scan_failed,
            on_done=lambda: (self.btn_scan.setText("What is not stock…"),
                             self.btn_scan.setEnabled(bool(self.session.game_path))),
        )

    def _scanned(self, rows) -> None:
        edition = self.session.installation_state(quick=True)["edition"] \
            if rows is not None else None
        dialog = InstallationStateDialog(rows, edition, self)
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.chosen:
            return
        if not self.session.mod:
            QMessageBox.information(
                self, "Adopt", "Open a mod first — adopted files are recorded "
                               "as belonging to one.")
            return
        try:
            result = self.session.adopt_root_files(dialog.chosen)
        except (DsoError, OSError) as exc:
            QMessageBox.warning(self, "Adopt", str(exc))
            return
        self._report("Adopt in place", result)

    def _scan_failed(self, message: str, _traceback: str) -> None:
        QMessageBox.warning(self, "What is not stock", message)

    def _report(self, title: str, result: dict) -> None:
        self.refresh()
        lines = [f"<b>{result['summary']}</b>"]
        for label, key in (("Written", "written"), ("Restored", "restored"),
                           ("Removed", "removed")):
            if result[key]:
                shown = "<br>".join(result[key][:10])
                lines.append(f"<br><b>{label}:</b><br>{shown}")
        if result["skipped"]:
            shown = "<br>".join(f"{p} — {why}"
                                for p, why in sorted(result["skipped"].items())[:10])
            lines.append(f"<br><b>Left alone:</b><br>{shown}")
        QMessageBox.information(self, title, "".join(lines))


__all__ = ["RootFilesPanel", "RootPlanDialog", "WHAT"]
