"""
Adding an asset of a known kind: a texture, a script.

The generic version of this -- "type a path, pick a file" -- is plumbing, not a
modding feature.  What an author reaches for is *add a texture*, from the tab
that shows textures, and the difference is not cosmetic: a typed entry point
knows the folder, knows which source formats it accepts, and knows the thing
that matters most, which is **whether the asset does anything on its own**.

A texture is inert until a scene names it, and that is exactly the failure this
whole project exists to surface.  So the dialog says so before the write, and
the tab that opened it offers the binding step afterwards.  An app that writes
the file and stays quiet has built a very tidy no-op.

There is deliberately no *model* kind: a new model needs a scene to name it, and
nothing a mod can write reaches a new scene name -- see ``Session.ADD_KINDS``
and ``specs/scene.md`` 4.3.4.  Replacing the ``.3do`` an existing scene already
names is the route that works.

What each kind needs lives in ``Session.ADD_KINDS``, not here -- the tabs and
this dialog read it, so "textures go in 3DView/textures/" is stated once.
"""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
)

from dsotools.errors import DsoError

from . import theme


class AddAssetDialog(QDialog):
    """Pick a file, confirm where it goes, and be told what it will not do yet."""

    def __init__(self, parent, session, kind: str) -> None:
        super().__init__(parent)
        self.session = session
        self.kind = kind
        self.spec = session.add_kind(kind)
        self.setWindowTitle(f"Add a {self.spec['label']}")
        self.setMinimumWidth(640)
        self._source = ""

        layout = QVBoxLayout(self)
        form = QFormLayout()

        pick = QHBoxLayout()
        self.source_edit = QLineEdit()
        self.source_edit.setPlaceholderText(
            "the " + " or ".join(self.spec["extensions"]) + " file to add…")
        self.source_edit.textChanged.connect(self._source_changed)
        btn_browse = QPushButton("Browse…")
        btn_browse.clicked.connect(self._browse)
        pick.addWidget(self.source_edit, 1)
        pick.addWidget(btn_browse)
        form.addRow("File:", pick)

        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText(f"{self.spec['folder']}/mine{self.spec['target_ext'] or ''}")
        self.path_edit.textChanged.connect(self._revalidate)
        form.addRow("Path in the mod:", self.path_edit)
        layout.addLayout(form)

        self.note = QLabel()
        self.note.setWordWrap(True)
        self.note.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self.note)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Add")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        self._revalidate()

    # -- picking -------------------------------------------------------------

    def _browse(self) -> None:
        patterns = " ".join(f"*{e}" for e in self.spec["extensions"])
        path, _ = QFileDialog.getOpenFileName(
            self, f"{self.spec['label'].capitalize()} to add", "",
            f"{self.spec['label'].capitalize()} files ({patterns})",
        )
        if path:
            self.source_edit.setText(path)

    def _source_changed(self, text: str) -> None:
        """Propose a path when a file is chosen; never overwrite a typed one."""
        self._source = text.strip()
        if self._source and not self.path_edit.text().strip():
            self.path_edit.setText(
                self.session.suggest_add_path(self.kind, self._source))
        self._revalidate()

    # -- saying what will happen --------------------------------------------

    def _revalidate(self) -> None:
        vpath = self.path_edit.text().strip()
        ok = False
        colour = None

        why = self.session.check_add_source(self.kind, self._source)
        if why is not None:
            message = why
            colour = None if not self._source else theme.SEVERITY["error"]
        elif not vpath:
            message = "Say where in the mod it should go."
        else:
            why = self.session.check_new_path(vpath)
            if why is not None:
                message, colour = why, theme.SEVERITY["error"]
            else:
                ok = True
                message = self._what_will_happen(vpath)

        self.note.setText(
            f"<span style='color:{colour}'>{message}</span>" if colour else message
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(ok)

    def _what_will_happen(self, vpath: str) -> str:
        target = self.session.mod.deploy_target(vpath) if self.session.mod else ""
        where = ("into <b>user_data.zip</b>" if target == "zip"
                 else "as a loose file")
        parts = [f"Will be written {where}."]

        if self.spec["inert"]:
            parts.append(self.spec["inert"])
        return "<br><br>".join(parts)

    # -- result --------------------------------------------------------------

    @property
    def source(self) -> str:
        return self._source

    @property
    def vpath(self) -> str:
        return self.path_edit.text().strip()


def add_asset(parent, session, kind: str) -> Optional[str]:
    """Run the dialog and do it.  Returns the vpath added, or ``None``."""
    from PySide6.QtWidgets import QMessageBox

    if not session.mod:
        QMessageBox.information(parent, "Add", "Open a mod first.")
        return None

    dialog = AddAssetDialog(parent, session, kind)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None

    try:
        routed = session.add_asset(dialog.vpath, dialog.source)
    except DsoError as exc:
        QMessageBox.warning(parent, "Add", str(exc))
        return None
    return next(iter(routed), None)


__all__ = ["AddAssetDialog", "add_asset"]
