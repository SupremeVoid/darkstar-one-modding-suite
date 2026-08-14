"""
The blinker editor: the one part of a scene with no geometry and no material.

WHY IT NEEDS ITS OWN WIDGET
---------------------------
Everything else in the Models tab hangs off a mesh -- a ``.3do``, a shader, a
material, textures -- and the panels above this one are shaped for that.  A
``CBlinkerGroup`` has none of it.  All 621 in the corpus are the same shape: a
``Texture`` attribute and a list of

    <Blinker displacement="x y z size" vrow="…" animtime="…" />

The engine draws each entry as a camera-facing sprite cut from that texture, so
there is nothing for the shader editor to edit and nothing for the viewport to
draw as a mesh -- the viewport shows small emissive spheres instead, which say
*where* the lights are without pretending to show what they look like.

WHAT THE COLUMNS ARE
--------------------
``x``/``y``/``z`` are a position relative to the group's node.  ``size`` is the
sprite's extent.  ``vrow`` selects a row of the texture -- the sheet holds
several lights and each blinker picks one -- and ``animtime`` is how long its
blink cycle takes.  Read off shipped data, not guessed: ``vrow`` is 0.111 in
``PlayerShip``'s ``blinks_0``, which is 1/9 of a nine-row sheet.

Widgets only, as everywhere in this layer.  The reading and writing live in
``Session.blinker_groups`` / ``Session.set_blinkers``.
"""

from __future__ import annotations

import posixpath
from typing import Callable, List, Optional

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QGroupBox,
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

COLUMNS = ("x", "y", "z", "size", "vrow", "animtime")


class BlinkerTable(QGroupBox):
    """One group at a time, chosen from a combo, edited as a table."""

    def __init__(self, session, on_changed: Optional[Callable[[], None]] = None,
                 on_highlight: Optional[Callable[..., None]] = None,
                 on_preview: Optional[Callable[..., None]] = None) -> None:
        super().__init__("Blinkers")
        self.session = session
        self._on_changed = on_changed
        #: Called with ``(position, size)`` for the selected row, or ``None`` to
        #: clear it.  The viewport draws that one in red -- twenty white dots
        #: in a cluster are otherwise indistinguishable, and the whole point of
        #: selecting a row is to know which light you are moving.
        self._on_highlight = on_highlight
        #: Called with ``(node_path, [(position, size)])`` whenever the table
        #: changes, so the white markers follow the numbers being typed.
        #: Without it they stayed where the *file* put them while the red one
        #: moved, which looks like the preview is broken.
        self._on_preview = on_preview
        self._scene: Optional[str] = None
        #: Every group in the scene, and the subset currently offered.  Two
        #: lists because the variant selection filters the picker without
        #: re-reading the scene.
        self._all_groups: List[dict] = []
        self._groups: List[dict] = []
        self._keep = None
        self._loading = False
        #: ``{vpath: facts}`` from `Session.asset_info`, so this pane can say
        #: what the texture is and where it comes from without reading a file
        #: on the GUI thread.
        self._info: dict = {}

        layout = QVBoxLayout(self)

        row = QHBoxLayout()
        row.addWidget(QLabel("Group:"))
        self.group_combo = QComboBox()
        self.group_combo.setMinimumWidth(220)
        self.group_combo.currentIndexChanged.connect(self._group_changed)
        row.addWidget(self.group_combo)
        row.addWidget(QLabel("Texture:"))
        self.texture_label = QLabel("—")
        self.texture_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        row.addWidget(self.texture_label, 1)
        # The sheet a blinker is cut from is an asset like any other, and this
        # is the only pane that knows which one it is -- the linked-assets panel
        # deliberately does not list blinker groups, because a group is not a
        # submesh.  Without this the texture was named here and replaceable
        # nowhere.
        self.btn_texture = QPushButton("Replace texture…")
        self.btn_texture.clicked.connect(self.replace_texture)
        row.addWidget(self.btn_texture)
        self.btn_add = QPushButton("Add")
        self.btn_add.setToolTip("Append a blinker at the origin of this group")
        self.btn_add.clicked.connect(self.add_blinker)
        self.btn_del = QPushButton("Delete")
        self.btn_del.setToolTip("Remove the selected blinker")
        self.btn_del.clicked.connect(self.delete_blinker)
        self.btn_save = QPushButton("Save to mod")
        self.btn_save.clicked.connect(self.save)
        for b in (self.btn_add, self.btn_del, self.btn_save):
            row.addWidget(b)
        layout.addLayout(row)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(list(COLUMNS))
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setMinimumHeight(120)
        self.table.installEventFilter(self)
        self.table.itemChanged.connect(self._cell_changed)
        self.table.itemSelectionChanged.connect(self._highlight_selected)
        layout.addWidget(self.table)

        self.setVisible(False)

    def eventFilter(self, obj, event):
        """Escape clears the selection while the table has focus.

        Without it there is no way back to "nothing selected" once a row has
        been clicked -- and therefore no way to get rid of the red marker
        short of changing scene.
        """
        if (obj is self.table
                and event.type() == QEvent.Type.KeyPress
                and event.key() == Qt.Key.Key_Escape):
            self.clear_selection()
            return True
        return super().eventFilter(obj, event)

    def clear_selection(self) -> None:
        """Deselect, and take the marker with it.

        Clearing while `_loading` is set on purpose: the selection signal would
        otherwise fire mid-change and read a half-built table.  The marker is
        then cleared explicitly, because that suppressed signal is exactly the
        one that would have done it.
        """
        self._loading = True
        try:
            self.table.clearSelection()
            self.table.setCurrentCell(-1, -1)
        finally:
            self._loading = False
        if self._on_highlight is not None:
            self._on_highlight(None)

    # -- contents ------------------------------------------------------------

    def load(self, scene_path: Optional[str], keep=None) -> None:
        """Read the scene's groups.  ``keep(path)`` filters which are offered."""
        self._scene = scene_path
        self._all_groups = []
        if scene_path:
            try:
                self._all_groups = self.session.blinker_groups(scene_path)
            except DsoError:
                self._all_groups = []
        self._keep = keep
        self._refill_combo()

    def set_reachable(self, keep) -> None:
        """Re-filter the picker after the variant selection changed.

        Separate from :meth:`load` because it must **not** re-read the scene:
        changing a booster would otherwise re-parse an 8,000-line XML on the GUI
        thread to rebuild a combo box.
        """
        self._keep = keep
        self._refill_combo()

    def _refill_combo(self) -> None:
        """Rebuild the picker, keeping the open group if it is still offered.

        Keeping it matters: changing the *booster* must not throw away the body
        blinker you were editing.  When it is gone -- because its own variant
        was switched away -- the first remaining group is selected, which is the
        only honest answer.
        """
        keep = self._keep
        self._groups = [
            g for g in self._all_groups if keep is None or keep(g["path"])
        ]
        self.setVisible(bool(self._groups))
        if not self._groups:
            self.group_combo.clear()
            if self._on_highlight is not None:
                self._on_highlight(None)
            return

        previous = self.group_combo.currentData()
        self._loading = True
        try:
            self.group_combo.clear()
            for g in self._groups:
                # The label, not the name: PlayerShip has 63 groups under 5
                # names, so the name alone never says which part's lights these
                # are.  `Session._blinker_labels` builds it from the node path.
                self.group_combo.addItem(
                    f"{g['label']}  ({len(g['blinkers'])})", g["path"]
                )
            index = self.group_combo.findData(previous)
        finally:
            self._loading = False
        if index > 0:
            self.group_combo.setCurrentIndex(index)      # fires _group_changed
        else:
            self._group_changed(0)

    def _current_group(self) -> Optional[dict]:
        path = self.group_combo.currentData()
        return next((g for g in self._groups if g["path"] == path), None)

    def _group_changed(self, _index: int) -> None:
        group = self._current_group()
        if group is None:
            return
        self._show_texture(group)
        self._fill(group["blinkers"])
        # A row index means nothing across groups -- row 3 of `blinks_0` is a
        # different light from row 3 of `blinks_1` -- and `currentRow()`
        # survives a refill, so without this the red marker stayed on screen
        # pointing at a blinker from the group you just left.
        self.clear_selection()
        self._preview_group()

    def set_asset_info(self, info: dict) -> None:
        """``{vpath: facts}`` from ``Session.asset_info``, filled on a worker."""
        self._info = dict(info or {})
        group = self._current_group()
        if group is not None:
            self._show_texture(group)

    def _show_texture(self, group: dict) -> None:
        """Name the sheet, say what it is, and say whether it is the mod's.

        The same three facts every other asset row in the app now carries.  The
        *resolved* path is shown when there is one: the scene's own reference is
        relative to the scene, so it is not something the user can go and find.
        """
        vpath = group.get("texture_vpath")
        facts = self._info.get(vpath) or {}
        bits = [vpath or group.get("texture") or "—"]
        if facts.get("format"):
            bits.append(facts["format"])
        if facts.get("in_mod"):
            bits.append("from the mod")
        elif vpath is None and group.get("texture"):
            bits.append("does not resolve")
        self.texture_label.setText("   ·   ".join(bits))
        self.texture_label.setToolTip(
            group.get("texture") or ""
        )

        why = None
        if not vpath:
            why = "This group's texture does not resolve, so there is nothing to replace."
        elif not self.session.mod:
            why = "Open a mod first — replacements are saved into it."
        self.btn_texture.setEnabled(why is None)
        self.btn_texture.setToolTip(why or f"Put your own {posixpath.basename(vpath)} into the mod")

    def replace_texture(self) -> None:
        """Replace the sheet this group's sprites are cut from."""
        from .linked_assets import replace_asset_dialog

        group = self._current_group()
        vpath = (group or {}).get("texture_vpath")
        if not vpath:
            return
        if replace_asset_dialog(self, self.session, vpath) and self._on_changed:
            self._on_changed()

    def _fill(self, blinkers: List[dict]) -> None:
        self._loading = True
        try:
            self.table.setRowCount(len(blinkers))
            for r, b in enumerate(blinkers):
                x, y, z = b["position"]
                values = (x, y, z, b["size"], b["vrow"], b["animtime"])
                for c, value in enumerate(values):
                    # An absent vrow/animtime is a real state -- the attribute
                    # is missing -- so it is blank rather than 0.
                    text = "" if value is None else f"{value:+f}"
                    self.table.setItem(r, c, QTableWidgetItem(text))
        finally:
            self._loading = False

    # -- editing -------------------------------------------------------------

    def _preview_group(self) -> None:
        """Push the table's current numbers to the viewport's white markers."""
        if self._on_preview is None or self._loading:
            return
        group = self._current_group()
        if group is None:
            return
        rows = self._read_table(quiet=True)
        if rows is None:
            return
        self._on_preview(
            group["path"], [(r["position"], r["size"]) for r in rows]
        )

    def _highlight_selected(self) -> None:
        """Mark the selected row's blinker in the viewport."""
        if self._on_highlight is None or self._loading:
            return
        # `currentRow()` keeps pointing at the last cell visited even after the
        # selection is cleared, so the marker outlived the selection until this
        # asked whether anything is actually selected.
        selected = self.table.selectionModel()
        if selected is None or not selected.hasSelection():
            self._on_highlight(None)
            return
        row = self.table.currentRow()
        rows = self._read_table(quiet=True)
        if rows is None or not 0 <= row < len(rows):
            self._on_highlight(None)
            return
        self._on_highlight(rows[row]["position"], rows[row]["size"])

    def _cell_changed(self, item: QTableWidgetItem) -> None:
        if self._loading:
            return
        text = item.text().strip()
        if text == "":
            return                                  # blank means "not set"
        try:
            float(text)
        except ValueError:
            QMessageBox.warning(
                self, "Blinkers",
                f"{COLUMNS[item.column()]} must be a number; "
                f"{text!r} is not. Reverting that cell.",
            )
            group = self._current_group()
            if group is not None:
                self._fill(group["blinkers"])
            return
        self._preview_group()
        self._highlight_selected()

    def add_blinker(self) -> None:
        rows = self._read_table()
        if rows is None:
            return
        rows.append({
            "position": (0.0, 0.0, 0.0), "size": 0.2,
            "vrow": 0.0, "animtime": 1.0,
        })
        self._fill(rows)
        self._preview_group()
        self.table.selectRow(len(rows) - 1)

    def delete_blinker(self) -> None:
        row = self.table.currentRow()
        rows = self._read_table()
        if rows is None or not 0 <= row < len(rows):
            QMessageBox.information(self, "Blinkers", "Select a row first.")
            return
        del rows[row]
        self._fill(rows)
        self._preview_group()

    def _read_table(self, *, quiet: bool = False) -> Optional[List[dict]]:
        """The table as data, or ``None`` if a cell will not parse.

        ``quiet`` suppresses the complaint: the highlight reads the table on
        every selection change, and a half-typed number is not a moment to
        interrupt someone with a dialog.
        """
        out: List[dict] = []
        for r in range(self.table.rowCount()):
            values: List[Optional[float]] = []
            for c in range(len(COLUMNS)):
                item = self.table.item(r, c)
                text = (item.text().strip() if item else "")
                if text == "":
                    values.append(None)
                    continue
                try:
                    values.append(float(text))
                except ValueError:
                    if not quiet:
                        QMessageBox.warning(
                            self, "Blinkers",
                            f"Row {r + 1}, {COLUMNS[c]}: {text!r} is not a number.",
                        )
                    return None
            out.append({
                "position": tuple(v or 0.0 for v in values[:3]),
                "size": values[3] if values[3] is not None else 0.2,
                "vrow": values[4],
                "animtime": values[5],
            })
        return out

    def save(self) -> None:
        group = self._current_group()
        if group is None or not self._scene:
            return
        rows = self._read_table()
        if rows is None:
            return
        try:
            routed = self.session.set_blinkers(self._scene, group["path"], rows)
        except (DsoError, OSError) as exc:
            QMessageBox.warning(self, "Blinkers", str(exc))
            return
        QMessageBox.information(
            self, "Blinkers",
            "Written into the mod:<pre>"
            + "\n".join(f"{k}   → {v}" for k, v in routed.items())
            + "</pre>",
        )
        if self._on_changed is not None:
            self._on_changed()


__all__ = ["BlinkerTable", "COLUMNS"]
