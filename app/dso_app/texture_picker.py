"""
Choosing a texture out of the whole virtual file system.

Needed the moment a texture slot became rebindable: the game has 3,053 of
them, so a combo box is not an option and a file dialog is wrong -- the choice
is a *virtual* path, and what is on disk in the mod folder is only part of it.

Only textures under ``3DView/`` are offered, and that is a measured limit
rather than a tidy one: a scene resolves a reference against its own folder and
``3DView/``, and across all 1,006 stock scenes **0 of 45,322 resolving
references** need anything looser.  A texture elsewhere -- ``staticImages/``,
``images/`` -- cannot be named from a scene at all, so listing it would offer a
choice that writes a reference resolving to nothing.

Two things earn their place in the list:

* **the mod's own textures first**, because rebinding a slot is nearly always
  the second half of "I added a texture"; and
* **what a slot binds today**, preselected, so the dialog opens on the answer
  the author is about to change rather than on the top of an alphabetical list
  of three thousand.
"""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
)

#: What a texture slot can be bound to.  ``.dds`` only: a slot is a sampler,
#: and the atlas formats around it (``.tex``, ``.aim``) are indexes into one
#: rather than something a shader can sample.
TEXTURE_KINDS = (".dds",)


class TexturePickerDialog(QDialog):
    """Pick one texture vpath, with the mod's own offered first."""

    #: The only root a scene can reach.  See the module docstring.
    BINDABLE_ROOT = "3dview/"

    def __init__(self, parent, session, *, current: Optional[str] = None,
                 title: str = "Choose a texture") -> None:
        super().__init__(parent)
        self.session = session
        self.setWindowTitle(title)
        self.setMinimumSize(640, 460)
        self._rows: List[dict] = []

        layout = QVBoxLayout(self)

        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("filter by path…")
        self.filter_edit.textChanged.connect(self._populate)
        layout.addWidget(self.filter_edit)

        self.mod_only = QCheckBox("Only textures this mod ships")
        self.mod_only.stateChanged.connect(self._populate)
        layout.addWidget(self.mod_only)

        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(lambda _i: self.accept())
        self.list.currentItemChanged.connect(lambda *_: self._sync())
        layout.addWidget(self.list, 1)

        self.note = QLabel()
        self.note.setWordWrap(True)
        self.note.setStyleSheet("color: palette(mid);")
        layout.addWidget(self.note)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Bind")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        every = session.texture_assets(list(TEXTURE_KINDS))
        self._rows = [r for r in every
                      if r["vpath"].lower().startswith(self.BINDABLE_ROOT)]
        self._excluded = len(every) - len(self._rows)
        self._populate()
        if current:
            self._select(current)
        self._sync()

    # -- contents ------------------------------------------------------------

    def _populate(self) -> None:
        keep = self.chosen()
        needle = self.filter_edit.text().strip().lower()
        mod_only = self.mod_only.isChecked()

        self.list.clear()
        # The mod's own first, then everything else, each alphabetical. Sorting
        # purely by path buries the four textures the author added among three
        # thousand of Ascaron's.
        ordered = sorted(
            (r for r in self._rows
             if (not mod_only or r["in_mod"])
             and (not needle or needle in r["vpath"].lower())),
            key=lambda r: (not r["in_mod"], r["vpath"].lower()),
        )
        for row in ordered:
            label = row["vpath"] + ("   · in this mod" if row["in_mod"] else "")
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, row["vpath"])
            self.list.addItem(item)
        message = f"{self.list.count()} of {len(self._rows)} texture(s) shown."
        if self._excluded:
            message += (
                f"  {self._excluded} outside 3DView/ are not listed: a scene "
                "resolves references against its own folder and 3DView/, so a "
                "texture elsewhere cannot be bound to one."
            )
        self.note.setText(message)
        if keep:
            self._select(keep)

    def _select(self, vpath: str) -> None:
        want = vpath.lower()
        for i in range(self.list.count()):
            item = self.list.item(i)
            if (item.data(Qt.ItemDataRole.UserRole) or "").lower() == want:
                self.list.setCurrentItem(item)
                self.list.scrollToItem(item)
                return

    def _sync(self) -> None:
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(
            self.chosen() is not None
        )

    def chosen(self) -> Optional[str]:
        item = self.list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None


def pick_texture(parent, session, *, current: Optional[str] = None,
                 title: str = "Choose a texture") -> Optional[str]:
    """Run the picker.  Returns a vpath, or ``None`` if nothing was chosen."""
    dialog = TexturePickerDialog(parent, session, current=current, title=title)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return None
    return dialog.chosen()


__all__ = ["TexturePickerDialog", "pick_texture", "TEXTURE_KINDS"]
