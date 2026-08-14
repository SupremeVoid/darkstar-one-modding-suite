"""
Declaring a sound: the choices that decide where it goes and how it plays.

Three of them matter and none has a safe default:

* **kind** picks how the engine loads it. ``Stream`` reads from disk as it
  plays, which is what music wants and what a short effect must not do;
  ``Sound2D`` and ``Sound3D`` are loaded whole, positioned or not.
* **group** is half the sound's address -- the same name in two groups is two
  sounds -- and a group with ``Select`` set is a random pool, so dropping a
  sound into one silently makes it an alternative to its neighbours.
* **name** is the other half, and it is what a script will say.

Rate, channel count and length are *not* choices: they come from the file, and
are shown so the author can see what they are committing to.
"""

from __future__ import annotations

import os
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
)

from dsotools.errors import DsoError

KINDS = (
    ("Sound2D", "loaded whole, no position — interface and notifications"),
    ("Sound3D", "loaded whole, positioned in the world — effects"),
    ("Stream", "streamed from disk — music and long atmospheres"),
)


class AddSoundDialog(QDialog):
    """Copy a sound file into the mod and declare it."""

    def __init__(self, session, source: str, parent=None) -> None:
        super().__init__(parent)
        self.session = session
        self.source = source
        self.added: Optional[dict] = None
        self.setWindowTitle("Add a sound")
        self.setMinimumWidth(560)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        stem = os.path.splitext(os.path.basename(source))[0]
        self.name = QLineEdit(stem)
        self.name.textChanged.connect(self._sync)
        form.addRow("Name", self.name)

        self.kind = QComboBox()
        for value, why in KINDS:
            self.kind.addItem(f"{value} — {why}", value)
        self.kind.currentIndexChanged.connect(lambda _i: self._sync())
        form.addRow("Kind", self.kind)

        self.group = QComboBox()
        self.group.setEditable(True)
        for path in session.sound_groups():
            self.group.addItem(path)
        self.group.setCurrentText("USER")
        self.group.currentTextChanged.connect(lambda _t: self._sync())
        form.addRow("Group", self.group)
        layout.addLayout(form)

        self.detail = QLabel("")
        self.detail.setWordWrap(True)
        self.detail.setTextFormat(Qt.TextFormat.RichText)
        layout.addWidget(self.detail)

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                        | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Add")
        self.buttons.accepted.connect(self.add)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        try:
            self.info = session.probe_sound(source)
        except DsoError as exc:
            self.info = None
            self.detail.setText(f"<b>{exc}</b>")
        self._sync()

    def _sync(self) -> None:
        ok = self.buttons.button(QDialogButtonBox.StandardButton.Ok)
        if self.info is None:
            ok.setEnabled(False)
            return
        name = self.name.text().strip()
        group = self.group.currentText().strip()
        ok.setEnabled(bool(name))

        seconds = self.info["seconds"] or 0
        described = (f"{self.info['kind'].upper()}, "
                     f"{self.info['frequency']:,} Hz, "
                     f"{'mono' if self.info['channels'] == 1 else 'stereo'}, "
                     f"{seconds:.2f}s")
        clash = ""
        if name and self.session.sounds():
            reference = f"{group}/{name}" if group else name
            if any(r["reference"].lower() == reference.lower()
                   for r in self.session.sounds()):
                clash = ("<br><b>Something already answers to that group and "
                         "name.</b> One of the two would never be heard.")
        long_effect = ""
        if self.kind.currentData() != "Stream" and seconds > 20:
            long_effect = ("<br>That is long for a loaded sound — "
                           "<b>Stream</b> is what the game uses past a few "
                           "seconds.")
        layout_note = ""
        if not clash:
            layout_note = (f"<br>Copied to <code>"
                           f"{self.session.SOUND_FOLDERS[self.kind.currentData()]}/"
                           f"</code> and declared as "
                           f"<code>{group}/{name}</code>.")
        self.detail.setText(described + layout_note + long_effect + clash)

    def add(self) -> Optional[dict]:
        try:
            self.added = self.session.add_sound(
                self.source,
                name=self.name.text().strip(),
                kind=self.kind.currentData(),
                group=self.group.currentText().strip(),
            )
        except DsoError as exc:
            QMessageBox.warning(self, "Add a sound", str(exc))
            return None
        self.accept()
        return self.added


__all__ = ["AddSoundDialog", "KINDS"]
