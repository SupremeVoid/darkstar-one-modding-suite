"""
The Models tab: browse by scene, and see what the engine would draw.

BY SCENE, NOT BY FILE
---------------------
A ``.3do`` on its own is not a thing the game shows.  The scene is what binds a
mesh to its model, its shader and its textures, and it is the unit an author
edits -- so that is the unit this tab browses.  Opening ``objects/main_.3do``
would raise the question "with which textures?", and the honest answer is "it
depends which scene you mean".

The viewport is `viewport.ModelViewport`; the numbers beside it come from
`Session.scene_detail`, which is where the SCN001 check lives.  Nothing here
parses anything.
"""

from __future__ import annotations

import posixpath
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QAbstractScrollArea,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QCheckBox,
    QMenu,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QGroupBox,
    QVBoxLayout,
    QWidget,
)

from dsotools.edit import meshview
from dsotools.errors import DsoError

from .. import workers
from ..blinker_table import BlinkerTable
from ..effect_editor import EffectDialog
from ..linked_assets import LinkedAssets
from ..session import mesh_for, texture_refs
from ..viewport import ModelViewport

MAX_ROWS = 4000

#: What each viewport layer is, said once.  The four non-geometry drawables
#: are additive shells the engine blends over the hull; drawn here as ordinary
#: opaque geometry they will *hide* the ship rather than glow around it, which
#: is worth saying before someone reports it as a bug.
_LAYER_HELP = {
    meshview.LAYER_COLLISION:
        'Collision shells. Never drawn in game.',
    meshview.LAYER_GLOW:
        'CGlowObject — engine glow and light bloom shells. Additive in game, '
        'so they look like solid hulls here.',
    meshview.LAYER_SHINE:
        'CShineObject — specular highlight shells. Additive in game, so they '
        'look like solid hulls here.',
    meshview.LAYER_DISTORTION:
        'CDistortionObject — heat-haze and refraction volumes. The engine '
        'warps what is behind them; here they are drawn as plain geometry.',
    meshview.LAYER_SHIELD:
        'CShieldMesh — the shield bubble. Drawn only when the shield is hit.',
}


def _call_key(call):
    """What identifies a draw call across a reload.

    Not the name: 85 of PlayerShip's meshes share 28 names, and 4 of 200 stock
    scenes have two *visible* rows under one name.  Not the row index either --
    see :meth:`ModelsTab._isolate_selected`.  ``(node_path, lod, index)`` is
    unique in all 200 scenes measured, and survives the reparse that Apply does,
    which object identity does not.
    """
    return (call.node_path, call.lod, call.index)


def _boxed(title: str, widget, extra=None) -> QGroupBox:
    """Put a table in a titled box.

    The blinker editor below these is a ``QGroupBox`` already, so without this
    the pane read as one long list with a labelled section at the bottom.  Same
    reasoning as the Project tab's *Mod files* box: two stacked tables with no
    titles do not announce that they answer different questions.
    """
    box = QGroupBox(title)
    layout = QVBoxLayout(box)
    layout.setContentsMargins(6, 4, 6, 6)
    layout.addWidget(widget)
    if extra is not None:
        layout.addLayout(extra)
    return box


class ModelsTab(QWidget):
    def __init__(self, window) -> None:
        super().__init__()
        self.window = window
        self.session = window.session
        self._rows: list = []
        self._scene: Optional[str] = None
        self._geometry = None
        self._detail = None
        #: The "still working" animation on the title, and what it says.
        self._busy_timer = None
        self._busy_text = ""
        self._busy_step = 0
        #: ``{vpath: facts}`` from `Session.asset_info` for this scene's models
        #: and textures -- format, where it resolves from, whether the mod owns
        #: it, and whether it can be reset to stock.
        self._info: dict = {}
        #: ``(scene, lod, camera, submesh key)`` to restore once the browser
        #: rows come back, or ``None``.  See :meth:`refresh`.
        self._reopen = None
        #: ``{group name: chosen member path}`` for the variant selectors.
        self._selection: dict = {}
        self._layers = list(meshview.DEFAULT_LAYERS)

        layout = QVBoxLayout(self)

        row = QHBoxLayout()
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("filter scenes…")
        self.filter_edit.textChanged.connect(self._apply_filter)
        self.btn_reload = QPushButton("Reload")
        self.btn_reload.clicked.connect(self.reload)
        row.addWidget(self.filter_edit, 1)
        row.addWidget(self.btn_reload)
        layout.addLayout(row)

        split = QSplitter(Qt.Orientation.Horizontal)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["Scene", "In mod", "Source"])
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.itemSelectionChanged.connect(self._open_selected)
        split.addWidget(self.tree)

        right = QWidget()
        rl = QVBoxLayout(right)

        self.title = QLabel("Open a game folder, then pick a scene.")
        self.title.setWordWrap(True)
        rl.addWidget(self.title)

        # A vertical splitter, not a stack: the readout, linked assets and
        # effect editor together are taller than the viewport, and fixed
        # layout squeezed the 3D view down to a strip.
        self.vsplit = QSplitter(Qt.Orientation.Vertical)

        # The viewport **and the controls that drive it** are the top half, so
        # the splitter handle lands underneath them.  Variant combos, the LOD
        # picker and the layer switches all change what is on screen, and
        # dragging the splitter used to sweep them into the scrolling lower
        # pane -- putting the controls for the picture below the tables that
        # merely describe it.
        top = QWidget()
        tl = QVBoxLayout(top)
        tl.setContentsMargins(0, 0, 0, 0)
        self.viewport = ModelViewport()
        self.viewport.setMinimumHeight(220)
        tl.addWidget(self.viewport, 1)

        # One combo per variant group. PlayerShip has three (body, wing,
        # booster) with eleven alternatives each; without this the viewport
        # draws all eleven ships stacked in the same place.
        self.variants_row = QHBoxLayout()
        self.variants_holder = QWidget()
        self.variants_holder.setLayout(self.variants_row)
        tl.addWidget(self.variants_holder)
        self._variant_combos: dict = {}

        row = QHBoxLayout()
        row.addWidget(QLabel("LOD:"))
        self.lod_combo = QComboBox()
        self.lod_combo.setMinimumWidth(150)
        self.lod_combo.currentIndexChanged.connect(self._lod_changed)
        self.btn_all = QPushButton("Show all")
        self.btn_all.setToolTip("Stop isolating and draw every submesh again")
        self.btn_all.clicked.connect(self.show_all)
        self.btn_reset = QPushButton("Reset view")
        self.btn_reset.clicked.connect(self.viewport.reset_view)
        self.btn_effect = QPushButton("Shader options…")
        self.btn_effect.setToolTip(
            "Shader, parameters and material of the selected submesh")
        self.btn_effect.clicked.connect(self.edit_effect)
        row.addWidget(self.lod_combo)
        # Layer toggles. Collision shells are invisible in game; drawing
        # one wraps a station in a grey polyhedron.
        self.layer_row = QHBoxLayout()
        row.addLayout(self.layer_row)
        self._layer_boxes: dict = {}
        row.addStretch(1)
        row.addWidget(self.btn_effect)
        row.addWidget(self.btn_all)
        row.addWidget(self.btn_reset)
        tl.addLayout(row)

        self.vsplit.addWidget(top)
        rl.addWidget(self.vsplit, 1)

        # The lower half scrolls.  Without this the tab's minimum size hint
        # is the sum of every table and control, Qt grows the *window* to
        # satisfy it, and the app silently resizes itself whenever a table
        # gains rows.
        lower = QWidget()
        rl2 = QVBoxLayout(lower)
        rl2.setContentsMargins(0, 0, 0, 0)
        self.lower_scroll = QScrollArea()
        self.lower_scroll.setWidget(lower)
        self.lower_scroll.setWidgetResizable(True)
        self.lower_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.vsplit.addWidget(self.lower_scroll)
        # Neither half may be dragged out of existence -- the viewport used
        # to vanish entirely and not come back.
        self.vsplit.setCollapsible(0, False)
        self.vsplit.setCollapsible(1, False)
        self.vsplit.setStretchFactor(0, 3)
        self.vsplit.setStretchFactor(1, 2)
        self.vsplit.setSizes([620, 380])

        # The readout. Selecting a row isolates that submesh in the viewport,
        # which is the whole reason it is a list and not a label.
        self.parts = QTreeWidget()
        self.parts.setColumnCount(5)
        self.parts.setHeaderLabels(
            ["Submesh", "Shader", "Triangles", "Base colour", "Normal"]
        )
        self.parts.setRootIsDecorated(False)
        self.parts.setAlternatingRowColors(True)
        self.parts.setMinimumHeight(120)
        self.parts.setSizeAdjustPolicy(
            QAbstractScrollArea.SizeAdjustPolicy.AdjustIgnored)
        self.parts.itemSelectionChanged.connect(self._isolate_selected)
        # The same actions the linked-assets panel offers, on the row you are
        # already looking at.  A submesh row names its model and its textures,
        # so "export that" and "replace that" are the obvious next questions and
        # used to mean scrolling to another table to ask them.
        self.parts.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.parts.customContextMenuRequested.connect(self._parts_menu)
        # The submesh count is not decoration: the engine binds one
        # EffectContainer per submesh (SCN001), so a scene whose count does not
        # match the model it points at draws the wrong material on the wrong
        # surface.  Until these existed the app could report that and not fix
        # it, which is half a tool.
        self.btn_submesh_add = QPushButton("Add submesh")
        self.btn_submesh_add.setToolTip(
            "Give this mesh one more EffectContainer, copied from the last.\n"
            "Use it when the model has more submeshes than the scene binds.")
        self.btn_submesh_add.clicked.connect(self.add_submesh)
        self.btn_submesh_del = QPushButton("Remove submesh")
        self.btn_submesh_del.setToolTip(
            "Drop the selected submesh's EffectContainer from the scene.")
        self.btn_submesh_del.clicked.connect(self.remove_submesh)
        submesh_row = QHBoxLayout()
        submesh_row.addStretch(1)
        submesh_row.addWidget(self.btn_submesh_add)
        submesh_row.addWidget(self.btn_submesh_del)
        rl2.addWidget(_boxed("Submeshes", self.parts, extra=submesh_row))

        # Everything bound to the selected submesh, with the shared actions.
        # This is what makes the tab usable without hopping to Textures and
        # searching by hand for a filename you just read off a table.
        self.linked = LinkedAssets(
            self.session,
            on_open=lambda v: self.window.open_asset(v),
            on_changed=self._reload_current,
        )
        self.linked.setMinimumHeight(120)
        rl2.addWidget(_boxed("Linked assets", self.linked))

        # Blinkers get a table of their own because they are the one thing in a
        # scene with no geometry and no material: a texture and a list of point
        # sprites, which the mesh/effect panels above have nothing to say about.
        self.blinkers = BlinkerTable(
            self.session,
            on_changed=self._reload_current,
            on_highlight=self._highlight_blinker,
            on_preview=self._preview_blinkers,
        )
        rl2.addWidget(self.blinkers)


        self.notes = QLabel()
        self.notes.setWordWrap(True)
        self.notes.setTextFormat(Qt.TextFormat.RichText)
        rl2.addWidget(self.notes)

        split.addWidget(right)
        # The scene list is a fixed sidebar: only the right pane stretches,
        # so loading a wide readout cannot squeeze the browser away.
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setCollapsible(0, False)
        split.setSizes([360, 900])
        layout.addWidget(split, 1)

        self._set_controls(False)

    # -- state ---------------------------------------------------------------

    def _set_controls(self, on: bool) -> None:
        for w in (self.lod_combo, self.btn_reset):
            w.setEnabled(on)
        self.btn_effect.setEnabled(bool(on and self.parts.selectedItems()))
        # Only meaningful while something is isolated; otherwise it is a
        # button that does nothing.
        self.btn_all.setEnabled(bool(on and self.parts.selectedItems()))

    def refresh(self) -> None:
        """Reload the browser **and rebuild the scene** the user is looking at.

        Anything that changes what a path resolves to emits "mod", and it lands
        here: a save from this tab, a save from Textures, and -- the case that
        exposed the bug -- "Reset to stock" in the Project tab.

        This used to clear the widgets, drop ``_geometry`` and reload only the
        scene *list*.  The tree selection is restored with signals blocked (so
        that repopulating cannot jump to another scene), which means nothing
        reopened the scene: the tab was left holding no geometry while the
        viewport still displayed the frame it had already uploaded.  Reset a
        texture to stock and the picture on screen was still the one that had
        just been removed -- and the Reload button, which the user has no reason
        to press after an action that claims to have done something, was the
        only way back.

        Keeping the camera and the selected submesh matters here too: a refresh
        that snapped the view back would make every save feel like the tab had
        reset itself, which is what the old comment was protecting against.
        """
        scene, lod = self._scene, (self.lod_combo.currentData() or 0)
        restore = self.viewport.view_state() if scene else None
        items = self.parts.selectedItems()
        call = items[0].data(0, Qt.ItemDataRole.UserRole) if items else None
        restore_submesh = _call_key(call) if call is not None else None

        self._geometry = None
        self.parts.clear()
        self.linked.show_assets([])
        self.blinkers.load(None)
        self.notes.clear()
        self._set_controls(False)
        # The scene is reopened once the browser rows are back, because only
        # then is it known whether it still exists -- opening a game with no
        # ComSat in it must not leave a stale ComSat on screen either.
        self._reopen = (scene, lod, restore, restore_submesh) if scene else None
        self.reload()

    # -- browsing ------------------------------------------------------------

    def reload(self) -> None:
        if not self.session.stock:
            self.tree.clear()
            self.title.setText("Open a game folder, then pick a scene.")
            return
        self.btn_reload.setEnabled(False)
        workers.run(
            self.session.scenes,
            on_result=self._rows_ready,
            on_error=self._failed,
            on_done=lambda: self.btn_reload.setEnabled(True),
        )

    def _rows_ready(self, rows) -> None:
        self._rows = rows
        self._apply_filter()

        pending, self._reopen = self._reopen, None
        if pending is None:
            return
        scene, lod, restore, submesh = pending
        if any(r["vpath"].lower() == scene.lower() for r in rows):
            self.open_scene(scene, lod, restore=restore, restore_submesh=submesh)
            return
        # The scene is gone -- a different game folder, or a mod-only scene
        # whose mod was closed.  Say so and clear the viewport: leaving the
        # last frame up is the same lie this method was fixed to stop telling.
        self._scene = None
        self.viewport.show_geometry(None)
        self.title.setText("Pick a scene.")

    def _apply_filter(self) -> None:
        needle = self.filter_edit.text().strip().lower()
        # Signals off while the list is rebuilt.  Repopulating changes the
        # selection, which fired _open_selected and loaded whatever landed
        # in row 0 -- so saving an edit appeared to jump to another scene.
        self.tree.blockSignals(True)
        self.tree.clear()
        shown = 0
        for r in self._rows:
            if needle and needle not in r["vpath"].lower():
                continue
            if shown >= MAX_ROWS:
                break
            item = QTreeWidgetItem([r["name"], "yes" if r["in_mod"] else "", r["source"]])
            item.setData(0, Qt.ItemDataRole.UserRole, r)
            item.setToolTip(0, r["vpath"])
            if r["in_mod"]:
                item.setForeground(0, QBrush(QColor("#2980b9")))
            self.tree.addTopLevelItem(item)
            shown += 1
        # Put the selection back on the scene that is actually open.
        if self._scene:
            low = self._scene.lower()
            for i in range(self.tree.topLevelItemCount()):
                item = self.tree.topLevelItem(i)
                row = item.data(0, Qt.ItemDataRole.UserRole)
                if row and row["vpath"].lower() == low:
                    self.tree.setCurrentItem(item)
                    break
        self.tree.blockSignals(False)

        total = len(self._rows)
        more = f"  (showing {shown} of {total})" if shown < total else f"  ({total})"
        self.tree.setHeaderLabels(["Scene" + more, "In mod", "Source"])
        # Sized after the header is set, and floored: resizeColumnToContents
        # measures the *rows*, so a short filter result clipped the header into
        # "Scene  (showin".
        self.tree.resizeColumnToContents(0)
        self.tree.setColumnWidth(0, max(self.tree.columnWidth(0), 190))

    def _open_selected(self) -> None:
        items = self.tree.selectedItems()
        if not items:
            return
        row = items[0].data(0, Qt.ItemDataRole.UserRole)
        if row:
            self.open_scene(row["vpath"])

    # -- loading a scene -----------------------------------------------------

    def open_scene(self, scene_path: str, lod: int = 0, *,
                   restore=None, restore_submesh=None) -> None:
        if not self.viewport.ensure_loaded():
            self.title.setText(
                "<b>The 3D viewport could not start.</b><br>"
                + "<br>".join(self.viewport.errors)
            )
            return
        # Switching scenes throws the old one away *before* the new one is
        # built.  Reloading the same scene does not: an Apply would otherwise
        # blank the viewport for a second and read as the tab resetting itself,
        # which is the complaint that put `restore` here in the first place.
        if scene_path != self._scene:
            self._unload()
        self._scene = scene_path
        self.parts.clear()
        self._set_controls(False)
        self._busy(f"{scene_path} — building geometry")
        workers.run(
            self._load,
            scene_path,
            lod,
            on_result=lambda payload: self._scene_ready(
                payload, restore_submesh, restore),
            on_progress=self._loading_progress,
            on_error=self._failed,
        )

    def _unload(self) -> None:
        """Drop everything about the scene on screen.

        A half-cleared tab is worse than an empty one: the viewport kept the
        previous ship while the tables described it, so during a load the
        readout, the linked assets and the picture could all belong to
        different scenes.
        """
        self._geometry = None
        self._detail = None
        self._info = {}
        self._selection = {}
        self.viewport.show_geometry(None)
        self.parts.clear()
        self.linked.show_assets([])
        self.linked.set_asset_info({})
        self.blinkers.load(None)
        self.notes.clear()
        self._build_variant_controls_empty()

    def _build_variant_controls_empty(self) -> None:
        while self.variants_row.count():
            item = self.variants_row.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._variant_combos = {}
        self.variants_holder.setVisible(False)

    # -- "something is happening" --------------------------------------------
    #
    # Opening PlayerShip is seconds of work: 254 draw calls, 69 textures to
    # decode, 189 assets to describe.  A label that says "building geometry" and
    # then sits there is indistinguishable from a hang -- so it animates, and it
    # counts meshes as they are built.

    _SPIN = "|/-\\"

    def _busy(self, text: str) -> None:
        self._busy_text = text
        self._busy_step = 0
        self.title.setText(f"{text}…")
        if self._busy_timer is None:
            from PySide6.QtCore import QTimer

            self._busy_timer = QTimer(self)
            self._busy_timer.setInterval(120)
            self._busy_timer.timeout.connect(self._busy_tick)
        self._busy_timer.start()

    def _busy_tick(self) -> None:
        self._busy_step += 1
        spin = self._SPIN[self._busy_step % len(self._SPIN)]
        self.title.setText(f"{self._busy_text}…  {spin}")

    def _busy_done(self) -> None:
        if self._busy_timer is not None:
            self._busy_timer.stop()

    def _loading_progress(self, done: int, total: int, label: str) -> None:
        """Mesh counts from `build_scene_geometry`, and the phases after it.

        ``total == 0`` means "no count, just a phase name": geometry is only
        the first of three jobs, and a bar that reached 162/163 and then sat
        there for another second said the opposite of what was happening.
        """
        if total:
            self._busy_text = f"{self._scene} — building geometry  {done}/{total}"
        elif label:
            self._busy_text = f"{self._scene} — {label}"

    def _load(self, scene_path: str, lod: int, progress=None):
        """All three on the worker: geometry to draw, detail to read, and the
        texture formats to show.

        The formats are read here rather than where they are displayed because
        a DDS header costs ~4 ms and a scene binds up to a hundred distinct
        textures -- a fifth of a second, which is nothing on a worker and a
        visible stall on the GUI thread.  They are cached in Session, so a
        second scene sharing textures pays nothing.
        """
        geometry = self.session.scene_geometry(scene_path, lod=lod,
                                               progress=progress)
        if progress:
            progress(0, 0, "reading materials and textures")
        detail = self.session.scene_detail(scene_path)
        assets = {
            v
            for mesh in detail["meshes"]
            for v in [mesh.get("model_vpath")] + [
                t for slot in mesh["slots"] for t in (slot.get("texture_vpaths") or [])
            ]
            if v
        }
        # The blinker sheets too: they are scene assets that no submesh binds,
        # and the blinker pane needs the same facts about them as every other
        # row in the tab -- without reading files on the GUI thread.
        try:
            assets.update(
                g["texture_vpath"] for g in self.session.blinker_groups(scene_path)
                if g.get("texture_vpath")
            )
        except DsoError:
            pass
        if progress:
            progress(0, 0, f"describing {len(assets)} assets")
        info = self.session.asset_info(sorted(assets))
        return scene_path, lod, geometry, detail, info

    def _scene_ready(self, payload, restore_submesh=None,
                     restore=None) -> None:
        scene_path, lod, geometry, detail, info = payload
        if scene_path != self._scene:
            return                      # the user moved on while this was loading
        self._busy_done()
        self._geometry = geometry
        self._detail = detail
        self._info = info
        self.linked.set_asset_info(info)
        self.viewport.show_geometry(geometry)
        # A new scene means the old submesh selection, linked assets and effect
        # are meaningless.  Clearing here rather than waiting for the next
        # submesh click is what stops the panels showing the previous scene's
        # numbers next to this scene's model.
        self.parts.clearSelection()
        self.linked.show_assets([])

        self._selection = geometry.default_selection()
        self._build_variant_controls(geometry)
        self._build_layer_controls(geometry)

        self.lod_combo.blockSignals(True)
        self.lod_combo.clear()
        for level in range(max(1, geometry.lod_count)):
            self.lod_combo.addItem(f"LOD {level}", level)
        self.lod_combo.setCurrentIndex(min(lod, self.lod_combo.count() - 1))
        self.lod_combo.blockSignals(False)

        self.blinkers.set_asset_info(info)
        self.blinkers.load(scene_path, keep=self._reachable())
        self._apply_visibility()
        self._fill_notes(geometry, detail)
        self._set_controls(True)
        if restore:
            self.viewport.restore_view_state(restore)

        if restore_submesh:
            self._select_submesh(restore_submesh)

    def _update_title(self) -> None:
        geometry, detail = self._geometry, self._detail
        if geometry is None or detail is None:
            return
        # Submeshes, not draw calls: the blinker markers are drawn but they are
        # this tool's own spheres, so counting them here would make ticking the
        # blinker layer add 2,256 triangles to PlayerShip's total.
        visible = self._part_calls(geometry)
        total_calls = len([c for c in geometry.calls
                           if c.layer not in meshview.MARKER_LAYERS])
        shown_tris = sum(c.triangle_count for c in visible)
        of_total = ("" if len(visible) == total_calls
                    else f" (of {total_calls} / "
                         f"{geometry.triangle_count():,})")
        self.title.setText(
            f"<b>{self._scene}</b><br>{len(visible)} submeshes · "
            f"{shown_tris:,} triangles{of_total} · "
            f"{len(detail['meshes'])} mesh node(s)"
        )

    def _select_submesh(self, key) -> None:
        """Reselect a submesh after a reload, if it still exists.

        Keyed by ``(node_path, lod, index)``, not by name.  A name is not a
        key here either: measured over 200 stock scenes, **4 have two
        *visible* rows sharing one name** -- `Cruiser_G_0` has eight -- so
        saving an edit and reloading could put the selection, and therefore
        the isolate, on the other one.  The triple is unique in all 200.
        """
        for i in range(self.parts.topLevelItemCount()):
            item = self.parts.topLevelItem(i)
            call = item.data(0, Qt.ItemDataRole.UserRole)
            if call is not None and _call_key(call) == key:
                self.parts.setCurrentItem(item)
                return

    def _build_variant_controls(self, geometry) -> None:
        while self.variants_row.count():
            item = self.variants_row.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._variant_combos = {}

        # Only the groups that can currently be reached.  PlayerShip has eleven
        # `boost` groups -- one under each booster -- and ten of them sit under
        # a booster that is not the chosen one, so their combos could not
        # change anything on screen.  They are rebuilt on every selection
        # change, so picking booster_5 swaps in booster_5's own boost combo.
        groups = [
            g for g in geometry.reachable_groups(self._selection) if g.exclusive
        ]
        self.variants_holder.setVisible(bool(groups))
        if not groups:
            return

        for group in groups:
            combo = QComboBox()
            for label, path in group.members:
                combo.addItem(label, path)
            chosen = self._selection.get(group.key)
            idx = combo.findData(chosen)
            if idx >= 0:
                combo.setCurrentIndex(idx)
            combo.currentIndexChanged.connect(
                lambda _i, k=group.key, c=combo: self._variant_changed(k, c)
            )
            self.variants_row.addWidget(QLabel(f"{group.name}:"))
            self.variants_row.addWidget(combo)
            self._variant_combos[group.key] = combo
        self.variants_row.addStretch(1)

    def _reachable(self):
        """A predicate for "is this node path on screen under the variants?"

        The blinker picker asks it of every group.  `PlayerShip` has **63 groups
        under 5 names**, and 56 of them hang off a body, wing or booster that is
        not selected -- so listing them all offers entries whose lights cannot
        be seen while they are edited.

        Returns a *filter*, not an answer, because asking one path at a time
        re-derived the scene's variant grouping each time: 3.3 seconds per
        variant change, which is how this was reported.
        """
        if self._geometry is None:
            return lambda _path: True
        return self._geometry.reachable_filter(self._selection)

    def _variant_changed(self, group_key: str, combo: QComboBox) -> None:
        self._selection[group_key] = combo.currentData()
        # Rebuilt, not just re-applied: choosing a different booster changes
        # *which* nested groups are reachable, so the row of combos itself has
        # to change with it.
        if self._geometry is not None:
            self._build_variant_controls(self._geometry)
        # Same for the blinker picker -- and re-filtered rather than reloaded,
        # so changing a variant does not re-parse the scene.
        self.blinkers.set_reachable(self._reachable())
        self._apply_visibility()
        self.show_all()                      # isolating a hidden submesh is a trap

    def _build_layer_controls(self, geometry) -> None:
        while self.layer_row.count():
            item = self.layer_row.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        self._layer_boxes = {}
        present = geometry.layers()
        if len(present) < 2:
            # Nothing to choose between; a lone 'geometry' tick is noise.
            self._layers = list(present)
            return
        self._layers = [ly for ly in present if ly in meshview.DEFAULT_LAYERS]
        for name in present:
            box = QCheckBox(name)
            box.setChecked(name in self._layers)
            box.setToolTip(_LAYER_HELP.get(name, ''))
            box.toggled.connect(lambda on, n=name: self._layer_toggled(n, on))
            self.layer_row.addWidget(box)
            self._layer_boxes[name] = box

    def _layer_toggled(self, name: str, on: bool) -> None:
        if on and name not in self._layers:
            self._layers.append(name)
        elif not on and name in self._layers:
            self._layers.remove(name)
        self._apply_visibility()
        self.show_all()

    def _apply_visibility(self) -> None:
        """Push the variant choice and layer switches to the viewport."""
        if self._geometry is None:
            return
        self.viewport.apply_visibility(self._selection, self._layers)
        self._fill_parts(self._geometry)
        self._update_title()
        if not self.parts.selectedItems():
            # Nothing picked yet: show the whole tree rather than a blank panel.
            self._show_linked(None)

    def _part_calls(self, geometry):
        """The submeshes on screen — the rows of the parts table.

        ``markers=False`` because a blinker group is a point-sprite emitter
        with no model and no submesh; the spheres are drawn so you can place
        the lights, and they have no shader, no textures and no material to
        show in these columns.  Listing them also put a row in the table whose
        *index* the isolate button then used — see :meth:`_isolate_selected`.
        """
        return geometry.visible_calls(self._selection, self._layers, markers=False)

    def _fill_parts(self, geometry) -> None:
        self.parts.clear()
        # Only what is on screen: a readout listing 113 submeshes when 10 are
        # visible is a list of things you cannot click.
        for call in self._part_calls(geometry):
            item = QTreeWidgetItem([
                call.name,
                posixpath.basename(call.shader or "") or "—",
                f"{call.triangle_count:,}",
                posixpath.basename(call.basecolor.vpath) if call.basecolor else "—",
                posixpath.basename(call.normalmap.vpath) if call.normalmap else "—",
            ])
            item.setData(0, Qt.ItemDataRole.UserRole, call)
            item.setToolTip(0, call.node_path)
            # Amber means "this was inferred", and since `.bsd9` was decoded
            # that is a much narrower claim than it used to be: the shader
            # names its own slots, so the warning belongs only where it did
            # not -- a generic `tex0`, or a shader missing from the install.
            # Marking a slot the shader named would be crying wolf.
            named = {n.lower() for n in call.slot_names}
            if call.basecolor and "_lgh" in call.basecolor.vpath.lower() \
                    and not named & meshview.SHADER_BASE_SLOTS:
                item.setForeground(3, QBrush(QColor("#9a6f09")))
                item.setToolTip(
                    3,
                    "This shader names no colour slot, so its light map is "
                    "being shown as base colour. Inferred from the filename, "
                    "not read from the shader.",
                )
            if call.slot_names:
                item.setToolTip(
                    1,
                    "Texture slots this shader declares:\n"
                    + "\n".join(f"  {i}  {n}" for i, n in enumerate(call.slot_names)),
                )
            # What each of those two textures *is*, on hover, because that is
            # what a replacement has to match and the table only had its name.
            for col, tex in ((3, call.basecolor), (4, call.normalmap)):
                facts = self._info.get(tex.vpath) if tex else None
                if facts:
                    lines = [tex.vpath, facts.get("format") or ""]
                    if facts.get("in_mod"):
                        lines.append("from the mod")
                    item.setToolTip(col, "\n".join(x for x in lines if x))
                    if facts.get("in_mod"):
                        item.setForeground(col, QBrush(QColor("#2980b9")))
            self.parts.addTopLevelItem(item)
        for c in range(5):
            self.parts.resizeColumnToContents(c)

    def _fill_notes(self, geometry, detail) -> None:
        parts = []
        bad = [m for m in detail["meshes"] if m["scn001_ok"] is False]
        if bad:
            names = ", ".join(str(m["name"]) for m in bad[:4])
            parts.append(
                f'<span style="color:#c0392b">SCN001: {len(bad)} mesh(es) whose '
                f"EffectContainer count does not match their submesh total "
                f"({names}) — the engine will bind the wrong material.</span>"
            )
        unresolved = [m for m in detail["meshes"] if not m["resolved"]]
        if unresolved:
            parts.append(
                f'<span style="color:#c0392b">{len(unresolved)} mesh(es) whose '
                ".3do does not resolve.</span>"
            )
        if geometry.skipped:
            shown = "; ".join(f"{ref}: {why}" for ref, why in geometry.skipped[:3])
            parts.append(
                f'<span style="color:#7f8c8d">{len(geometry.skipped)} mesh(es) '
                f"not drawn — {shown}</span>"
            )
        self.notes.setText("<br>".join(parts))

    # -- viewport controls ---------------------------------------------------

    def _lod_changed(self, _index: int) -> None:
        lod = self.lod_combo.currentData()
        if self._scene is not None and lod is not None:
            self.open_scene(self._scene, lod)

    def _isolate_selected(self) -> None:
        items = self.parts.selectedItems()
        if not items:
            return
        # The call, not the row number.  Every other join in this tab already
        # learned that lesson; this one still passed `indexOfTopLevelItem` into
        # a list of a different length, which is why isolating anything past
        # the first few rows of PlayerShip produced a black viewport.
        call = items[0].data(0, Qt.ItemDataRole.UserRole)
        self.viewport.set_isolated(call)
        self.btn_effect.setEnabled(True)
        self.btn_all.setEnabled(True)
        self._show_linked(call)

    def _parts_menu(self, pos) -> None:
        """Model and texture actions for the submesh row under the cursor.

        Every action is the *same* one the linked-assets panel runs -- this
        builds no dialogs of its own.  Two context menus offering "Replace…"
        with two different sets of filters, two different refusal messages and
        two ideas of what a `.dds` may be replaced with is precisely the drift
        `show_users` and `export_asset` were made module-level to avoid.
        """
        item = self.parts.itemAt(pos)
        if item is None:
            return
        call = item.data(0, Qt.ItemDataRole.UserRole)
        if call is None:
            return
        menu = self.build_parts_menu(call)
        if menu is None:
            return
        menu.setToolTipsVisible(True)
        menu.exec(self.parts.viewport().mapToGlobal(pos))

    def build_parts_menu(self, call):
        """The menu for one draw call, built but not shown.

        Separate from :meth:`_parts_menu` so it can be *inspected*: `exec` opens
        a modal loop, so a menu that raises while being assembled would be
        invisible to every check this project has -- `check_app.py` is static
        and the widget layer has no unit tests.  `drive_models_tab.py --menu`
        prints what this returns.
        """
        menu = QMenu(self.parts)
        act_change = menu.addAction("Change model…")
        act_change.setToolTip(
            "Point this mesh at a different .3do — which is what makes a "
            "model you added do anything at all.")
        act_change.triggered.connect(lambda _c=False, k=call: self.change_model(k))
        menu.addSeparator()
        model = self._model_ref(call)
        if model:
            # addSection, not a disabled action: a greyed-out row that looks
            # like a command you cannot use is not the same thing as a heading.
            menu.addSection(f"Model — {posixpath.basename(model)}")
            self._asset_actions(menu, model, resolved=bool(
                (self._mesh_for(call) or {}).get("model_vpath")
            ))

        # One submenu per texture slot, each with the full action set, named by
        # what the *shader* calls the slot -- `t_Normal` says what the binding
        # is for, where "texture 3" says only where it sits.
        refs = texture_refs(self._mesh_for(call), call)
        if refs:
            menu.addSeparator()
            textures = menu.addMenu("Textures")
            for role, vpath, resolves in refs:
                facts = self._info.get(vpath) or {}
                fmt = facts.get("format")
                # The bullet marks the ones the mod supplies, so the submenu
                # says which textures are yours before you open it.
                sub = textures.addMenu(
                    f"{role} — {posixpath.basename(vpath)}"
                    + ("  ●" if facts.get("in_mod") else "")
                    + (f"   [{fmt}]" if fmt else "")
                )
                if resolves:
                    sub.addAction("Preview…").triggered.connect(
                        lambda _c=False, v=vpath: self.linked.preview(v)
                    )
                self._asset_actions(sub, vpath, resolved=resolves)
        return None if menu.isEmpty() else menu

    def _asset_actions(self, menu, vpath: str, *, resolved: bool = True) -> None:
        """Export / Replace / Open / What uses this / Copy path for one asset.

        ``resolved=False`` still shows them, disabled and with the reason: a
        reference that does not resolve is exactly the case someone wants to
        investigate, and a row that silently offers nothing looks broken.
        """
        from PySide6.QtWidgets import QApplication

        act_export = menu.addAction("Export…")
        act_export.triggered.connect(lambda _c=False, v=vpath: self.linked.export(v))
        act_replace = menu.addAction("Replace…")
        act_replace.triggered.connect(lambda _c=False, v=vpath: self.linked.replace(v))
        act_open = menu.addAction("Open in its tab")
        act_open.triggered.connect(lambda _c=False, v=vpath: self.window.open_asset(v))
        act_uses = menu.addAction("What uses this…")
        act_uses.triggered.connect(lambda _c=False, v=vpath: self.linked.show_users(v))
        act_reset = menu.addAction("Reset to stock…")
        act_reset.triggered.connect(
            lambda _c=False, v=vpath: self.linked.reset_to_stock(v)
        )
        why = self.session.can_reset_to_stock(vpath) if resolved else "It does not resolve."
        act_reset.setEnabled(why is None)
        if why is not None:
            act_reset.setToolTip(why)
        menu.addAction("Copy path").triggered.connect(
            lambda _c=False, v=vpath: QApplication.clipboard().setText(v)
        )

        for act in (act_export, act_replace, act_open):
            act.setEnabled(resolved)
            if not resolved:
                act.setToolTip(f"{vpath} does not resolve in this installation")
        if resolved and not self.session.mod:
            act_replace.setEnabled(False)
            act_replace.setToolTip("Open a mod first — replacements are saved into it")

    def _show_linked(self, call) -> None:
        """Fill the linked-assets panel and the effect editor for one submesh."""
        if call is None:
            self.linked.show_all_meshes(self._detail, self._geometry,
                                        self._selection, self._layers)
            return

        # Resolved vpaths, not the scene's raw `textures/x.dds` references:
        # those are relative to the scene and resolving them without it is a
        # guess that two scenes in different folders can make differently.
        wanted = [("model", self._model_ref(call))]
        wanted += [(role, ref) for role, ref, _ in
                   texture_refs(self._mesh_for(call), call)]
        workers.run(
            self.session.describe_assets,
            wanted,
            on_result=self.linked.show_assets,
            on_error=self._failed,
        )


    def _highlight_blinker(self, position=None, size: float = 0.2) -> None:
        """Draw the selected blinker in red, whatever the layer switches say.

        Selecting a row is a question, and a blank answer because the blinker
        layer happens to be off would be the wrong one.
        """
        self.viewport.show_blinker(position, size)

    def _preview_blinkers(self, node_path, blinkers) -> None:
        """Re-cut one group's markers, and show only that group.

        63 groups at once is a cloud of dots with nothing tying any of them to
        the table below.
        """
        self.viewport.set_blinker_group(node_path)
        self.viewport.update_blinker_group(node_path, blinkers)

    def _mesh_for(self, call) -> Optional[dict]:
        """The scene-detail entry a draw call came from."""
        return mesh_for(self._detail, call)

    def _model_ref(self, call) -> Optional[str]:
        """The .3do behind a draw call, via the scene detail we already have."""
        mesh = self._mesh_for(call)
        if mesh is None:
            return None
        return mesh["model_vpath"] or mesh["model"]

    def _effect_for(self, call) -> Optional[dict]:
        # Via `mesh_for`, so the effect shown is the one belonging to the
        # submesh that was clicked.  Keyed by node name this returned an
        # arbitrary variant's effect while `_node_path` still aimed the write
        # at the clicked one -- so Apply copied another variant's shader and
        # material over it.
        mesh = self._mesh_for(call)
        if mesh is None:
            return None
        slots = mesh["slots"]
        if call.index < len(slots):
            slot = dict(slots[call.index])
            slot["_node_path"] = call.node_path
            return slot
        return None

    def edit_effect(self) -> None:
        """Open the effect editor for the selected submesh."""
        from PySide6.QtWidgets import QDialog, QMessageBox

        items = self.parts.selectedItems()
        if not items:
            QMessageBox.information(
                self, 'Shader options',
                'Pick a submesh first — an effect belongs to one submesh.')
            return
        call = items[0].data(0, Qt.ItemDataRole.UserRole)
        effect = self._effect_for(call)
        if effect is None:
            QMessageBox.information(
                self, 'Shader options',
                f'{call.name} has no EffectContainer to edit.')
            return
        # The viewport shades from the draw call's own numbers, so a preview is
        # just those numbers swapped.  Snapshot them first: on Cancel they have
        # to go back, or the viewport would keep showing values the scene does
        # not contain and nothing on screen would say so.
        original = (dict(call.parameters or {}), tuple(call.material or ()))

        def preview(parameters, material):
            self.viewport.preview_shading(call, parameters, material)

        def pick(slot):
            from ..texture_picker import pick_texture

            bound = (effect.get("texture_vpaths") or [])
            current = bound[slot] if slot < len(bound) else None
            names = effect.get("slot_names") or []
            label = names[slot] if slot < len(names) else f"slot {slot}"
            return pick_texture(self, self.session, current=current,
                                title=f"Texture for {label}")

        dialog = EffectDialog(self, call.name, effect, on_preview=preview,
                              on_pick_texture=pick)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        if not accepted:
            self.viewport.preview_shading(call, *original)
            return
        self._apply_effect_edit(*dialog.result_payload)

    def add_submesh(self) -> None:
        """One more EffectContainer on the selected mesh."""
        self._submesh_edit("add")

    def remove_submesh(self) -> None:
        """Drop the selected submesh's EffectContainer."""
        self._submesh_edit("remove")

    def _submesh_edit(self, what: str) -> None:
        from PySide6.QtWidgets import QMessageBox

        from dsotools.errors import DsoError

        items = self.parts.selectedItems()
        if not items or self._scene is None:
            QMessageBox.information(self, "Submeshes", "Pick a submesh first.")
            return
        call = items[0].data(0, Qt.ItemDataRole.UserRole)
        if call is None:
            return
        try:
            if what == "add":
                routed = self.session.add_submesh(self._scene, call.node_path)
            else:
                routed = self.session.remove_submesh(
                    self._scene, call.node_path, call.index)
        except DsoError as exc:
            QMessageBox.warning(self, "Submeshes", str(exc))
            return
        self.window.status_label.setText(
            f"saved {', '.join(routed)} into {self.session.mod.name}")
        self._reload_current()

    def change_model(self, call=None) -> None:
        """Point the selected mesh at a different ``.3do``.

        The other half of adding a model, and the reason the button sits next
        to *Shader options…* rather than in a menu: a model in the mod that no
        scene names is a file doing nothing, which is exactly the state this
        app exists to make visible.
        """
        from PySide6.QtWidgets import QInputDialog, QMessageBox

        from dsotools.errors import DsoError

        if call is None:
            items = self.parts.selectedItems()
            if not items or self._scene is None:
                QMessageBox.information(self, "Change model",
                                        "Pick a submesh first.")
                return
            call = items[0].data(0, Qt.ItemDataRole.UserRole)
        if call is None or self._scene is None:
            return

        rows = self.session.bindable_models(self._scene)
        if not rows:
            QMessageBox.information(
                self, "Change model",
                "No .3do is reachable from this scene. A scene resolves "
                "references against its own folder and 3DView/, so the model "
                "has to live under one of those.")
            return
        choices = [r["vpath"] for r in rows]
        node = self._mesh_for(call)
        current = (node or {}).get("model_vpath")
        start = choices.index(current) if current in choices else 0
        chosen, ok = QInputDialog.getItem(
            self, "Change model",
            f"Point {call.node_path} at:", choices, start, False)
        if not ok or not chosen:
            return

        fit = self.session.mesh_model_fit(self._scene, call.node_path, chosen)
        if fit.get("fits") is False:
            # SCN001, before the write.  Not a refusal: re-cutting a mesh and
            # fixing the scene up afterwards is a legitimate two-step edit.
            if QMessageBox.question(
                self, "Change model",
                f"<b>{chosen}</b> has {fit['submesh_total']} submesh(es) and "
                f"this mesh carries {fit['effects']} EffectContainer(s).<br><br>"
                "The engine expects one per submesh (SCN001); with a mismatch "
                "it binds the wrong material to the wrong surface, or none at "
                "all.<br><br>Bind it anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
                QMessageBox.StandardButton.Cancel,
            ) != QMessageBox.StandardButton.Yes:
                return

        try:
            routed = self.session.set_mesh_model(
                self._scene, call.node_path, chosen)
        except DsoError as exc:
            QMessageBox.warning(self, "Change model", str(exc))
            return
        self.window.status_label.setText(
            f"saved {', '.join(routed)} into {self.session.mod.name}")
        self._reload_current()

    def _apply_effect_edit(self, shader, parameters, material,
                           textures=None) -> None:
        from PySide6.QtWidgets import QMessageBox

        from dsotools.errors import DsoError

        items = self.parts.selectedItems()
        if not items or self._scene is None:
            return
        call = items[0].data(0, Qt.ItemDataRole.UserRole)
        if call is None:
            return
        try:
            routed = self.session.set_effect(
                self._scene, call.node_path, call.index,
                shader=shader, parameters=parameters, material=material,
                textures=textures,
            )
        except DsoError as exc:
            QMessageBox.warning(self, "Apply to mod", str(exc))
            return
        self.window.status_label.setText(
            f"saved {', '.join(routed)} into {self.session.mod.name}"
        )
        self._reload_current()

    def _reload_current(self) -> None:
        """Reopen the scene so the viewport shows what is now on disk.

        Keeps the viewport and the selected submesh: an edit that snapped
        the camera back and dropped the selection made every Apply feel
        like the tab had reset itself.
        """
        if not self._scene:
            return
        items = self.parts.selectedItems()
        call = items[0].data(0, Qt.ItemDataRole.UserRole) if items else None
        self.open_scene(
            self._scene,
            self.lod_combo.currentData() or 0,
            restore=self.viewport.view_state(),
            restore_submesh=(_call_key(call) if call is not None else None),
        )

    def reveal(self, vpath: str) -> None:
        """Select ``vpath`` in the scene browser, or open it if it is a scene."""
        low = vpath.lower()
        self.filter_edit.blockSignals(True)
        self.filter_edit.clear()
        self.filter_edit.blockSignals(False)
        self._apply_filter()
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            row = item.data(0, Qt.ItemDataRole.UserRole)
            if row and row["vpath"].lower() == low:
                self.tree.setCurrentItem(item)
                self.tree.scrollToItem(item)
                return
        # Not a scene (a .3do, say) -- say so rather than doing nothing at all.
        self.title.setText(
            f"<b>{vpath}</b><br>Not a scene. Models are shown through the scene "
            "that binds them; use the filter to find one."
        )

    def show_all(self) -> None:
        self.parts.clearSelection()
        self.viewport.set_isolated(None)
        self.btn_effect.setEnabled(False)
        self.btn_all.setEnabled(False)
        self._show_linked(None)

    def _failed(self, message: str, _traceback: str) -> None:
        self._busy_done()
        self.title.setText(f"<b>failed:</b> {message}")
        self.notes.clear()


__all__ = ["ModelsTab"]
