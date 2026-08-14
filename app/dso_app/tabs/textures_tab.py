"""
The Textures tab: standalone images, and the TexPage atlas editor.

WHY THIS TAB EXISTS AT ALL
--------------------------
Most of the game's interface art is not a file you can open.  A graphic lives at
a rectangle inside an atlas page, its coordinates are in a ``.tex``, and its
drawn size is repeated in a ``.anim``.  Three files, one truth.  Editing that by
hand means opening a 1024x1024 page in an image editor, finding the right
150x119 region among 331 of them, and not moving it by a pixel.

So this tab does the thing a CLI genuinely cannot: it *shows* you the page with
the rectangles drawn on it, lets you click one, and replaces exactly that region
-- keeping the index and the drawables in step.

WHAT LIVES WHERE
----------------
Nothing here decodes anything.  ``Session`` owns the work
(:meth:`Session.open_atlas`, :meth:`Session.decode_preview`,
:meth:`Session.commit_atlas`) because that half can be tested; this file is
widgets, and is meant to stay thin enough to review by reading.

Every load runs on a worker.  A page plus its 266 drawables is fast, but a
1024x1024 DDS is not, and the rule in docs/ARCHITECTURE.md is not negotiable: parse,
decode, index and validate never run on the GUI thread.
"""

from __future__ import annotations

import os
import posixpath
from typing import Optional

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QBrush, QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
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

#: Rows above this and the tree becomes the slow part of opening the tab.  The
#: filter box is the real navigation tool; this only bounds the initial render.
MAX_ROWS = 4000

_SPRITE_PEN = QColor("#38bdf8")
_SELECTED_PEN = QColor("#f97316")


def rgba_to_pixmap(width: int, height: int, rgba: bytes) -> QPixmap:
    """RGBA bytes to a pixmap, with the buffer copied.

    ``QImage`` does **not** take ownership of the buffer it is handed.  Without
    the ``copy()`` the pixels are freed the moment ``rgba`` goes out of scope
    and the image renders as garbage or crashes -- intermittently, which is the
    worst way to meet this.
    """
    image = QImage(rgba, width, height, width * 4, QImage.Format.Format_RGBA8888)
    return QPixmap.fromImage(image.copy())


class PageView(QGraphicsView):
    """The page, with every sprite rectangle drawn on top of it."""

    def __init__(self, on_pick) -> None:
        super().__init__()
        self._on_pick = on_pick
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        # A checkerboard would be better; a flat mid grey is enough to tell
        # "transparent" from "black", which is the distinction that matters.
        self.setBackgroundBrush(QBrush(QColor("#3a3a3a")))
        self._rects = {}
        self._selected: Optional[str] = None
        self._pixmap_item: Optional[QGraphicsPixmapItem] = None
        self._fitted = False
        #: Once someone has zoomed by hand, stop re-fitting under them.
        self._user_zoomed = False

    def clear_page(self) -> None:
        """Show nothing.

        Needed because "the file this came from is gone" and "here is the file"
        are different states and only one of them had a way to be displayed:
        the last pixmap stayed on screen until something replaced it, which is
        how a reset-to-stock left the removed texture visible.
        """
        self._scene.clear()
        self._rects = {}
        self._selected = None
        self._pixmap_item = None
        self._fitted = False

    def show_page(self, pixmap: QPixmap, sprites, restore=None) -> None:
        self._scene.clear()
        self._rects = {}
        self._selected = None
        self._pixmap_item = self._scene.addPixmap(pixmap)
        self._scene.setSceneRect(QRectF(pixmap.rect()))

        pen = QPen(_SPRITE_PEN)
        pen.setCosmetic(True)          # 1px on screen at any zoom
        pen.setWidth(1)
        for sp in sprites:
            item = QGraphicsRectItem(sp.x, sp.y, sp.w, sp.h)
            item.setPen(pen)
            item.setData(0, sp.stem)
            item.setToolTip(f"{sp.stem}  {sp.w}x{sp.h} at ({sp.x},{sp.y})")
            self._scene.addItem(item)
            self._rects[sp.stem.lower()] = item
        if restore is not None:
            self.restore_view_state(restore)
            return
        # Deferred: fitInView measures the viewport, and at this point the
        # splitter has not laid out yet, so fitting now scales to a stale size
        # and the page lands as a small island in the middle of the pane.
        self._fitted = False
        QTimer.singleShot(0, self.reset_zoom)

    def reset_zoom(self) -> None:
        """Fit the page, and hand control back to the automatic fit."""
        if self._pixmap_item is not None:
            self.fitInView(self._pixmap_item, Qt.AspectRatioMode.KeepAspectRatio)
            self._fitted = True
            self._user_zoomed = False

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        # Keep it fitted until the user takes over with ctrl+wheel; after that,
        # re-fitting on every resize would fight them.
        if not self._user_zoomed:
            self.reset_zoom()

    def view_state(self):
        """Zoom and scroll, so a reload can put them back.

        Saving a sprite reopens the page from disk to prove what was written.
        Losing the viewport in the process means hunting for the sprite again
        after every single edit, which is most of the work in a page of 331.
        """
        return (
            self.transform(),
            self.horizontalScrollBar().value(),
            self.verticalScrollBar().value(),
            self._user_zoomed,
        )

    def restore_view_state(self, state) -> None:
        if not state:
            return
        transform, hx, vy, zoomed = state
        self.setTransform(transform)
        self.horizontalScrollBar().setValue(hx)
        self.verticalScrollBar().setValue(vy)
        self._user_zoomed = zoomed

    def select(self, stem: str) -> None:
        for key, item in self._rects.items():
            pen = QPen(_SELECTED_PEN if key == stem.lower() else _SPRITE_PEN)
            pen.setCosmetic(True)
            pen.setWidth(3 if key == stem.lower() else 1)
            item.setPen(pen)
            item.setZValue(1 if key == stem.lower() else 0)
        self._selected = stem
        item = self._rects.get(stem.lower())
        if item is not None:
            self.ensureVisible(item)

    # -- interaction ---------------------------------------------------------

    def mousePressEvent(self, event) -> None:
        pos = self.mapToScene(event.position().toPoint())
        # Smallest rectangle under the cursor wins: sprites can nest, and the
        # big background panel is never the one you meant to click.
        best = None
        for item in self._rects.values():
            if item.rect().contains(pos):
                if best is None or item.rect().width() * item.rect().height() < \
                        best.rect().width() * best.rect().height():
                    best = item
        if best is not None:
            self._on_pick(best.data(0))
        super().mousePressEvent(event)

    def wheelEvent(self, event) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
            self.scale(factor, factor)
            self._user_zoomed = True
            event.accept()
            return
        super().wheelEvent(event)


class RescaleDialog(QDialog):
    """Ask for a factor, and say plainly what it will touch."""

    def __init__(self, parent, page) -> None:
        super().__init__(parent)
        self.setWindowTitle("Rescale page")
        layout = QVBoxLayout(self)

        w, h = page.size
        blurb = QLabel(
            "Rescaling rewrites the page, <b>every one of its "
            f"{len(page.index.subimages)} rectangles</b>, and the declared size "
            f"in {len(page.anims)} drawable(s) — as one edit, so they cannot "
            "fall out of step."
        )
        blurb.setWordWrap(True)
        blurb.setMaximumWidth(460)
        layout.addWidget(blurb)

        form = QFormLayout()
        self.factor = QDoubleSpinBox()
        self.factor.setRange(0.1, 8.0)
        self.factor.setSingleStep(0.5)
        self.factor.setValue(2.0)
        self.factor.setDecimals(2)
        form.addRow("Factor:", self.factor)
        self.result_label = QLabel()
        form.addRow("Result:", self.result_label)
        layout.addLayout(form)

        def update(_=None):
            f = self.factor.value()
            self.result_label.setText(
                f"{w}×{h}  →  {max(1, round(w * f))}×{max(1, round(h * f))}"
            )

        self.factor.valueChanged.connect(update)
        update()

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class TexturesTab(QWidget):
    def __init__(self, window) -> None:
        super().__init__()
        self.window = window
        self.session = window.session
        self.page = None                    # the open AtlasPage, if any
        self._page_path: Optional[str] = None
        self._rows: list = []
        #: The standalone image on screen, if one is: ``(vpath, decoded)``.
        self._preview: Optional[tuple] = None
        #: The asset the user last asked to see.  A decode or an atlas load runs
        #: on a worker, so a slow earlier request must not land after a fast
        #: later one and put the wrong picture on screen.
        self._wanted: Optional[str] = None

        layout = QVBoxLayout(self)

        # -- browser ---------------------------------------------------------
        row = QHBoxLayout()
        self.kind_combo = QComboBox()
        # "Everything" first, and the default: the browser should open showing
        # what is there, not a subset the user has to discover they are inside.
        self.kind_combo.addItem("Everything", [".tex", ".aim", ".dds"])
        self.kind_combo.addItem("Atlas pages (.tex)", [".tex"])
        self.kind_combo.addItem("Images (.aim)", [".aim"])
        self.kind_combo.addItem("Textures (.dds)", [".dds"])
        self.kind_combo.currentIndexChanged.connect(self.reload)
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("filter by path…")
        self.filter_edit.textChanged.connect(self._apply_filter)
        self.btn_reload = QPushButton("Reload")
        self.btn_reload.clicked.connect(self.reload)
        self.btn_add = QPushButton("Add texture…")
        self.btn_add.setToolTip(
            "Bring a .dds into the mod at a path the game does not have.\n"
            "A new texture does nothing until a scene binds it — Shader\n"
            "options in the Models tab is where that happens."
        )
        self.btn_add.clicked.connect(self.add_texture)
        row.addWidget(self.kind_combo)
        row.addWidget(self.filter_edit, 1)
        row.addWidget(self.btn_add)
        row.addWidget(self.btn_reload)
        layout.addLayout(row)

        split = QSplitter(Qt.Orientation.Horizontal)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(["Path", "What it is", "In mod", "Source"])
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.itemSelectionChanged.connect(self._open_selected)
        # The same actions the Models tab's linked-assets panel offers, on the
        # row you are already looking at.  Replacing the *file* and replacing a
        # *sprite inside a page* are different operations, and this tab only
        # had the second one.
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._browser_menu)
        split.addWidget(self.tree)

        # -- viewer ----------------------------------------------------------
        right = QWidget()
        rl = QVBoxLayout(right)

        self.title = QLabel("Open a game folder, then pick an image.")
        self.title.setWordWrap(True)
        rl.addWidget(self.title)

        self.view = PageView(self._pick_sprite)
        rl.addWidget(self.view, 1)

        row = QHBoxLayout()
        self.sprite_combo = QComboBox()
        self.sprite_combo.setEditable(True)
        self.sprite_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.sprite_combo.setPlaceholderText("sprite…")
        self.sprite_combo.activated.connect(self._sprite_chosen)
        self.btn_replace_file = QPushButton("Replace file…")
        self.btn_replace_file.setToolTip(
            "Swap the whole texture for one from disk. For an atlas page that "
            "replaces every sprite on it at once; use “Replace sprite…” for one.")
        self.btn_replace_file.clicked.connect(self.replace_file)
        self.btn_replace = QPushButton("Replace sprite…")
        self.btn_replace.clicked.connect(self.replace_sprite)
        self.btn_rescale = QPushButton("Rescale page…")
        self.btn_rescale.clicked.connect(self.rescale_page)
        self.btn_save = QPushButton("Save to mod")
        self.btn_save.clicked.connect(self.save_to_mod)
        self.btn_export = QPushButton("Export…")
        self.btn_export.setToolTip(
            "Save this image as a PNG or JPEG, or copy out the original file."
        )
        self.btn_export.clicked.connect(self.export_current)
        self.btn_zoom = QPushButton("Reset zoom")
        self.btn_zoom.clicked.connect(self.view.reset_zoom)
        row.addWidget(self.sprite_combo, 1)
        row.addWidget(self.btn_replace_file)
        row.addWidget(self.btn_replace)
        row.addWidget(self.btn_rescale)
        row.addWidget(self.btn_zoom)
        row.addWidget(self.btn_export)
        row.addWidget(self.btn_save)
        rl.addLayout(row)

        #: Shown only while a `.tex` page is open.  A "Replace sprite" button
        #: next to a standalone .dds is not merely disabled-looking, it implies
        #: the .dds *has* sprites, which is the confusion this tab exists to
        #: clear up.
        self._atlas_only = (self.sprite_combo, self.btn_replace,
                            self.btn_rescale, self.btn_save)

        self.problems = QLabel()
        self.problems.setWordWrap(True)
        self.problems.setTextFormat(Qt.TextFormat.RichText)
        rl.addWidget(self.problems)

        split.addWidget(right)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([420, 760])
        layout.addWidget(split, 1)

        self._sync_controls()

    # -- state ---------------------------------------------------------------

    def _sync_controls(self) -> None:
        """Derive every control's state from what is open, from scratch.

        Deliberately not incremental.  This used to be two functions -- one
        showing the atlas controls, another disabling them for a page whose
        encoding cannot be written -- and nothing put ``enabled`` back. Opening
        one IMJPG24A page therefore left "Replace sprite" disabled for every
        page opened afterwards, for the rest of the session.

        Recomputing the whole state on every change is the fix that cannot rot:
        there is no path that sets a flag and forgets to clear it.
        """
        page = self.page
        has_atlas = page is not None

        for w in self._atlas_only:
            w.setVisible(has_atlas)
        for w in (self.sprite_combo, self.btn_replace, self.btn_rescale):
            w.setEnabled(has_atlas)
        # Not atlas-only: replacing the file works for a standalone texture too,
        # which is most of what this tab lists.  Needs a mod to write into.
        selected = self._selected_row()
        self.btn_replace_file.setEnabled(
            bool(self.session.mod and selected))
        self.btn_save.setEnabled(bool(has_atlas and page.dirty))
        self.btn_export.setEnabled(has_atlas or self._preview is not None)

    def refresh(self) -> None:
        """Called when the game or mod changes -- and reopens what was on screen.

        The same defect the Models tab had: this cleared the *state* behind the
        picture and left the picture itself up, so after "Reset to stock" the
        tab still showed the page or texture that had just been removed.  A
        stale image is worse here than anywhere else, because the whole point
        of this tab is looking at one.

        Reopened through ``_pending_reveal``, which the cross-tab jump already
        uses, so there is one path that turns a vpath back into whichever of
        the two editors it belongs in.
        """
        reopen = self._page_path or (self._preview[0] if self._preview else None)
        self.close_page()
        self._preview = None
        self.view.clear_page()
        if reopen:
            self._pending_reveal = reopen
        self.reload()

    def close_page(self) -> None:
        self.page = None
        self._page_path = None
        self.problems.clear()
        self.sprite_combo.clear()
        self._sync_controls()

    # -- browsing ------------------------------------------------------------

    def reload(self) -> None:
        if not self.session.stock:
            self.tree.clear()
            self.title.setText("Open a game folder, then pick an image.")
            return
        kinds = self.kind_combo.currentData()
        self.btn_reload.setEnabled(False)
        workers.run(
            self.session.texture_assets,
            kinds=kinds,
            on_result=self._rows_ready,
            on_error=self._failed,
            on_done=lambda: self.btn_reload.setEnabled(True),
        )

    def _rows_ready(self, rows) -> None:
        self._rows = rows
        self._apply_filter()
        pending, self._pending_reveal = getattr(self, "_pending_reveal", None), None
        if pending:
            self.reveal(pending)

    def _apply_filter(self) -> None:
        needle = self.filter_edit.text().strip().lower()
        # Signals off while the list is rebuilt, for the reason the Models tab
        # already learned: repopulating changes the selection, which fires
        # `_open_selected` and opens whatever lands in row 0.  It showed up here
        # as a refresh that reopened an unrelated texture -- and worse, that
        # decode raced the one the user actually asked for.
        self.tree.blockSignals(True)
        self.tree.clear()
        shown = 0
        for r in self._rows:
            if needle and needle not in r["vpath"].lower():
                continue
            if shown >= MAX_ROWS:
                break
            item = QTreeWidgetItem([
                r["vpath"],
                r["kind"],
                "yes" if r["in_mod"] else "",
                r["source"],
            ])
            item.setData(0, Qt.ItemDataRole.UserRole, r)
            if r["in_mod"]:
                item.setForeground(0, QBrush(QColor("#2980b9")))
            if r["kind"] == "atlas page":
                item.setToolTip(
                    1, f"The bitmap {r['atlas']} draws from. Edit it through "
                       "that atlas index, not directly."
                )
            elif r["kind"].startswith("packer source"):
                item.setForeground(1, QBrush(QColor("#7f8c8d")))
                item.setToolTip(
                    1, "A leftover input to Ascaron's texture packer. The game "
                       "never reads it, so editing it changes nothing on screen."
                )
            self.tree.addTopLevelItem(item)
            shown += 1
        self.tree.blockSignals(False)
        # Wide enough to read a path, capped so it cannot push "What it is" and
        # "In mod" off the edge -- the deepest 3DView texture path is longer
        # than any sane pane.
        self.tree.resizeColumnToContents(0)
        self.tree.setColumnWidth(0, min(self.tree.columnWidth(0), 340))
        self.tree.resizeColumnToContents(1)
        total = len(self._rows)
        more = f"  (showing {shown} of {total})" if shown < total else f"  ({total})"
        self.tree.setHeaderLabels(["Path" + more, "What it is", "In mod", "Source"])

    def reveal_sprite(self, tex_path: str, sprite: str) -> None:
        """Open an atlas page *and* select one sprite in it.

        The Interface tab jumps here: an element draws a sprite, and the place
        to edit that sprite is the page it is packed into, at its own
        rectangle.  Landing on the page with 331 sprites and no selection
        would leave the user to find it by eye.
        """
        self.reveal(tex_path)
        self._open_atlas(tex_path, select=sprite)

    def reveal(self, vpath: str) -> None:
        """Select ``vpath`` in the browser, widening the filter if need be.

        Called when another tab says "open this here".  The filter is cleared
        rather than respected: arriving at a tab that says "0 of 3231" because
        an unrelated filter is still set looks like the jump failed.
        """
        low = vpath.lower()
        if not any(r["vpath"].lower() == low for r in self._rows):
            # Not in the current kind filter -- widen to Everything and retry
            # once the rows come back.
            self._pending_reveal = vpath
            self.kind_combo.setCurrentIndex(0)
            return
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

    def _selected_row(self) -> Optional[dict]:
        items = self.tree.selectedItems()
        if not items:
            return None
        return items[0].data(0, Qt.ItemDataRole.UserRole)

    def _open_selected(self) -> None:
        row = self._selected_row()
        if not row:
            return
        if self._confirm_discard() is False:
            return
        if row["ext"] == ".tex":
            self._open_atlas(row["vpath"])
        else:
            self._open_standalone(row["vpath"])

    # -- standalone images ---------------------------------------------------

    def _open_standalone(self, vpath: str) -> None:
        self.close_page()
        self._preview = None
        # What the user last asked for.  Decoding runs on a worker, so two
        # requests can be in flight and the *slower* one would otherwise land
        # last and win -- putting a texture on screen that nobody selected.
        self._wanted = vpath
        # Said before the work starts, and it has to be: the SLD decoders are
        # pure Python, so they hold the GIL and the window stops repainting for
        # up to a second even though the decode is on a worker.  Session caches
        # the result, so coming back to the same image is instant.
        self.title.setText(f"{vpath} — decoding…")
        workers.run(
            self.session.decode_preview,
            vpath,
            on_result=lambda d, p=vpath: self._preview_ready(p, d),
            on_error=self._failed,
        )

    def _preview_ready(self, vpath: str, decoded: dict) -> None:
        if self._wanted is not None and self._wanted != vpath:
            return          # superseded while it was decoding
        pixmap = rgba_to_pixmap(decoded["width"], decoded["height"], decoded["rgba"])
        self.view.show_page(pixmap, [])
        self._preview = (vpath, decoded)
        self._sync_controls()
        self.title.setText(
            f"<b>{vpath}</b><br>{decoded['summary']}  —  "
            f"{decoded['width']}×{decoded['height']}"
        )

    # -- the atlas editor ----------------------------------------------------

    def _open_atlas(self, tex_path: str, restore=None, select=None) -> None:
        self.close_page()
        self._wanted = tex_path          # see _open_standalone
        self.title.setText(f"{tex_path} — loading page and drawables…")
        workers.run(
            self.session.open_atlas,
            tex_path,
            on_result=lambda page, p=tex_path: self._atlas_ready(p, page, restore, select),
            on_error=self._failed,
        )

    def _atlas_ready(self, tex_path: str, page, restore=None, select=None) -> None:
        if self._wanted is not None and self._wanted != tex_path:
            return          # superseded while it was loading
        self.page = page
        self._page_path = tex_path
        self._preview = None
        self._redraw_page(restore=restore)
        self.sprite_combo.clear()
        for sp in sorted(page.sprites, key=lambda s: s.stem.lower()):
            self.sprite_combo.addItem(f"{sp.stem}  ({sp.w}×{sp.h})", sp.stem)
        self._sync_controls()
        if select:
            self._pick_sprite(select)

    def _redraw_page(self, restore=None) -> None:
        page = self.page
        if page is None:
            return
        w, h = page.size
        pixmap = rgba_to_pixmap(w, h, page.image.convert("RGBA").tobytes())
        self.view.show_page(pixmap, page.sprites, restore=restore)
        dirty = "  •  <b>unsaved changes</b>" if page.dirty else ""
        self.title.setText(
            f"<b>{self._page_path}</b><br>page {page.index.page} — {w}×{h}, "
            f"{len(page.sprites)} sprites, {len(page.anims)} drawable(s){dirty}"
        )
        self._show_problems()
        self._sync_controls()

    def _show_problems(self) -> None:
        if self.page is None:
            self.problems.clear()
            return
        # The re-encode notice comes first because it changes what the file will
        # be, but it is rendered as a note rather than in the findings colour --
        # "this will be saved as IMTC32" is information, not a defect, and
        # colouring it like one trains people to ignore the colour.
        parts = []
        notice = self.session.page_writable(self.page)
        if notice:
            parts.append(f'<span style="color:#7f8c8d">{notice}</span>')
        found = self.session.atlas_problems(self.page)[:6]
        if found:
            parts.append(
                '<span style="color:#c0392b">'
                + "<br>".join(found)
                + "</span>"
            )
        self.problems.setText("<br>".join(parts))

    def _pick_sprite(self, stem: str) -> None:
        idx = self.sprite_combo.findData(stem)
        if idx >= 0:
            self.sprite_combo.setCurrentIndex(idx)
        self.view.select(stem)

    def _sprite_chosen(self, _index: int) -> None:
        stem = self.sprite_combo.currentData()
        if stem:
            self.view.select(stem)

    def current_sprite(self) -> Optional[str]:
        return self.sprite_combo.currentData()

    # -- operations ----------------------------------------------------------

    def _browser_menu(self, pos) -> None:
        """The row's own actions, matching the linked-assets panel elsewhere."""
        from PySide6.QtWidgets import QMenu

        item = self.tree.itemAt(pos)
        if item is None:
            return
        row = item.data(0, Qt.ItemDataRole.UserRole)
        if not row:
            return
        vpath = row["vpath"]

        menu = QMenu(self)
        act_replace = menu.addAction("Replace file…")
        act_replace.setEnabled(bool(self.session.mod))
        act_uses = menu.addAction("What uses this…")
        act_export = menu.addAction("Export…")
        menu.addSeparator()
        act_copy = menu.addAction("Copy path")
        chosen = menu.exec(self.tree.viewport().mapToGlobal(pos))
        if chosen is None:
            return
        if chosen is act_replace:
            self.replace_file(vpath)
        elif chosen is act_uses:
            self.show_uses(vpath)
        elif chosen is act_export:
            self.tree.setCurrentItem(item)
            self.export_current()
        elif chosen is act_copy:
            from PySide6.QtWidgets import QApplication

            QApplication.clipboard().setText(vpath)

    def show_uses(self, vpath: Optional[str] = None) -> None:
        """Which scenes and effects bind this texture.

        Delegates to the shared dialog rather than wording it again: that
        function exists module-level in ``linked_assets`` so there is "one
        dialog, one wording, wherever it is asked from", and this tab briefly
        had a second copy that said something subtly different.
        """
        from ..linked_assets import show_users

        vpath = vpath or (self._selected_row() or {}).get("vpath")
        if vpath:
            show_users(self, self.session, vpath)

    def add_texture(self) -> None:
        """Bring a texture the game has never had into the mod.

        Distinct from *Replace file…* in the way that matters: that swaps the
        bytes behind a path the game already knows, so it takes effect the
        moment it is written.  This creates a path nothing references yet, so
        the dialog says what still has to happen for it to be visible.
        """
        from ..add_asset_dialog import add_asset

        added = add_asset(self, self.session, "texture")
        if not added:
            return
        self.reload()
        self._select_vpath(added)
        self.window.status_label.setText(
            f"added {added} — bind it to a mesh in the Models tab to see it")

    def _select_vpath(self, vpath: str) -> None:
        """Put the cursor on a path in the browser, if it is listed."""
        want = vpath.lower()
        for i in range(self.tree.topLevelItemCount()):
            item = self.tree.topLevelItem(i)
            if item.text(0).lower() == want:
                self.tree.setCurrentItem(item)
                self.tree.scrollToItem(item)
                return

    def replace_file(self, vpath: Optional[str] = None) -> None:
        """Swap the whole texture file, rather than one sprite inside a page.

        Distinct from :meth:`replace_sprite`, and the distinction matters: for
        an atlas page this replaces every sprite on it at once, which is
        occasionally what you want and usually not.
        """
        from PySide6.QtWidgets import QFileDialog

        if not self.session.mod:
            QMessageBox.information(self, "Replace file",
                                    "Open a mod first — the replacement is "
                                    "written into it.")
            return
        vpath = vpath or (self._selected_row() or {}).get("vpath")
        if not vpath:
            QMessageBox.information(self, "Replace file", "Pick a texture first.")
            return
        if self._confirm_discard() is False:
            return

        path, _ = QFileDialog.getOpenFileName(
            self, f"Replace {vpath}", "",
            "Images (*.png *.bmp *.tga *.jpg *.jpeg *.dds *.aim);;All files (*)")
        if not path:
            return
        try:
            written = self.session.replace_asset(vpath, path)
        except DsoError as exc:
            QMessageBox.warning(self, "Replace file", str(exc))
            return
        # `replace_asset` reports where each file landed -- loose or in the zip
        # -- which is the part worth showing, because the two are not
        # interchangeable and the engine only reads one of them per root.
        where = ", ".join(f"{k} → {v}" for k, v in sorted(written.items()))
        QMessageBox.information(
            self, "Replace file",
            f"The mod now supplies this texture.\n\n{where}")
        self.reload()

    def replace_sprite(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        if self.page is None:
            return
        stem = self.current_sprite()
        if not stem:
            QMessageBox.information(self, "Replace sprite", "Pick a sprite first.")
            return
        sp = self.page.sprite(stem)
        path, _ = QFileDialog.getOpenFileName(
            self,
            f"Replace {stem} ({sp.w}×{sp.h})",
            "",
            "Images (*.png *.bmp *.tga *.jpg *.jpeg);;All files (*)",
        )
        if not path:
            return

        try:
            from PIL import Image

            image = Image.open(path).convert("RGBA")
        except Exception as exc:                      # noqa: BLE001 - user file
            QMessageBox.warning(self, "Replace sprite", f"Could not read it:\n{exc}")
            return

        allow_resize = False
        if image.size != (sp.w, sp.h):
            # Dimension locking is the default for a reason: the rectangle is
            # fixed by the .tex, so a bigger replacement lands on top of the
            # neighbouring sprite's pixels.  Offer the alternative, but *name*
            # both choices on the buttons -- "OK" and "Apply" for two different
            # geometry outcomes is a coin flip for the reader.
            box = QMessageBox(self)
            box.setWindowTitle("Different size")
            box.setIcon(QMessageBox.Icon.Question)
            box.setText(
                f"<b>{stem}</b> occupies {sp.w}×{sp.h} on the page, but that "
                f"image is {image.size[0]}×{image.size[1]}."
            )
            box.setInformativeText(
                "Scaling keeps the page layout untouched. Rewriting the "
                "rectangle keeps every pixel of your image, but it can overlap "
                "the sprite next to it — that is checked, and refused if so."
            )
            btn_scale = box.addButton(
                f"Scale to {sp.w}×{sp.h}", QMessageBox.ButtonRole.AcceptRole
            )
            btn_rect = box.addButton(
                f"Enlarge rectangle to {image.size[0]}×{image.size[1]}",
                QMessageBox.ButtonRole.DestructiveRole,
            )
            box.addButton(QMessageBox.StandardButton.Cancel)
            box.setDefaultButton(btn_scale)
            box.exec()

            if box.clickedButton() is btn_scale:
                image = image.resize((sp.w, sp.h), Image.LANCZOS)
            elif box.clickedButton() is btn_rect:
                allow_resize = True
            else:
                return

        try:
            self.page.replace(stem, image, allow_resize=allow_resize)
        except DsoError as exc:
            QMessageBox.warning(self, "Replace sprite", str(exc))
            return

        self._source_hint = path
        self._redraw_page(restore=self.view.view_state())
        self.view.select(stem)

    def export_current(self) -> None:
        """Save what is on screen as a normal file, or copy the original out."""
        from PySide6.QtWidgets import QFileDialog

        if self.page is not None:
            vpath, image = self.page.page_path, self.page.image
        elif self._preview is not None:
            vpath, image = self._preview[0], None
        else:
            return

        stem = posixpath.basename(vpath)
        original_ext = posixpath.splitext(stem)[1].lstrip(".") or "bin"
        path, _ = QFileDialog.getSaveFileName(
            self,
            f"Export {stem}",
            posixpath.splitext(stem)[0] + ".png",
            "PNG image (*.png);;JPEG image (*.jpg);;"
            f"Original {original_ext.upper()} file (*.{original_ext})",
        )
        if not path:
            return
        try:
            self.session.export_asset(vpath, path, image=image)
        except (DsoError, OSError) as exc:
            QMessageBox.warning(self, "Export", f"Could not export:\n{exc}")
            return
        self.window.status_label.setText(f"exported {os.path.basename(path)}")

    def rescale_page(self) -> None:
        if self.page is None:
            return
        dialog = RescaleDialog(self, self.page)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self.page.rescale(dialog.factor.value())
        except (DsoError, ValueError) as exc:
            QMessageBox.warning(self, "Rescale page", str(exc))
            return
        # Not restoring the viewport here on purpose: the page just changed
        # size, so the old zoom no longer means the same thing.
        self._redraw_page()

    def save_to_mod(self) -> None:
        if self.page is None or not self.page.dirty:
            return
        if not self.session.mod:
            QMessageBox.information(
                self, "Save to mod", "Open a mod first — edits are saved into it."
            )
            return

        try:
            files = sorted(self.page.save())
        except DsoError as exc:
            QMessageBox.warning(self, "Save to mod", str(exc))
            return
        # Show where each file lands.  "images/" is routed loose, and unlike
        # inifiles/ and scripts/ that has never been confirmed in game -- see
        # docs/STATE.md.  Naming the destination is what lets an author check.
        from dsotools.project import Mod as _Mod

        listing = "\n".join(f"    {f}   → {_Mod.deploy_target(f)}" for f in files)
        confirm = QMessageBox.question(
            self,
            "Save to mod",
            f"Write {len(files)} file(s) into "
            f"<b>{self.session.mod.display_name or self.session.mod.name}</b>?"
            f"<pre>{listing}</pre>"
            "They are written together, because a page saved without its "
            "rewritten index draws the wrong region and reports nothing.",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Cancel,
        )
        if confirm != QMessageBox.StandardButton.Save:
            return

        source = getattr(self, "_source_hint", None)
        # Captured before the commit: commit_atlas emits "mod", which reaches
        # refresh() -> close_page() and clears _page_path.  That arrives on a
        # queued connection so it cannot land mid-call today, but depending on
        # that ordering to keep a local variable valid is not worth the risk.
        path = self._page_path
        try:
            routed = self.session.commit_atlas(
                self.page, source=source, operation="atlas-edit"
            )
        except DsoError as exc:
            QMessageBox.warning(self, "Save to mod", str(exc))
            return

        self.window.status_label.setText(
            f"saved {len(routed)} file(s) into {self.session.mod.name}"
        )
        # Reopen from disk, so what is on screen is what was written rather
        # than what we hoped was written -- but put the viewport and the
        # selected sprite back, because losing them on every save means hunting
        # for the sprite again each time.
        state = self.view.view_state()
        sprite = self.current_sprite()
        self.close_page()
        QTimer.singleShot(0, lambda: self._open_atlas(path, restore=state, select=sprite))

    # -- helpers -------------------------------------------------------------

    def _confirm_discard(self) -> Optional[bool]:
        """``False`` if the user wants to keep unsaved work, else ``None``."""
        if self.page is None or not self.page.dirty:
            return None
        answer = QMessageBox.question(
            self,
            "Unsaved changes",
            f"{posixpath.basename(self._page_path or '')} has unsaved changes. "
            "Discard them?",
            QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
        )
        if answer != QMessageBox.StandardButton.Discard:
            # Put the selection back where it was, or the tree lies about what
            # is on screen.
            self.tree.blockSignals(True)
            for i in range(self.tree.topLevelItemCount()):
                item = self.tree.topLevelItem(i)
                row = item.data(0, Qt.ItemDataRole.UserRole)
                item.setSelected(bool(row and row["vpath"] == self._page_path))
            self.tree.blockSignals(False)
            return False
        return None

    def _failed(self, message: str, _traceback: str) -> None:
        self.title.setText(f"<b>failed:</b> {message}")
        self.problems.clear()


__all__ = ["TexturesTab", "PageView", "RescaleDialog", "rgba_to_pixmap"]
