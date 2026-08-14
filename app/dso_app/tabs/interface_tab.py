"""
The Interface tab: screen layouts, as a picture you can move things on.

WHY A CANVAS AND NOT A TABLE
----------------------------
A ``.screen`` is 1,381 rectangles across 83 files, and a rectangle is not
something anyone reads as four numbers.  The reason this tab waited for the
format to be decoded is that the useful view is the *layout* -- where the button
actually sits on the 1024x768 the file was authored for.

So the canvas is the tab, and the table beside it is the precision half: click a
rectangle to select its row, type into the row to move it by one pixel.

THE CHAIN, RESOLVED
-------------------
Each element names a drawable, and the reference is followed the whole way
before anything is shown::

    element -> scripts/X.anim -> images/Y.aim -> atlas page + rectangle

which reaches real pixels for **1,433 of 1,433** references in the stock
screens.  The preview shows what the game draws, cropped out of the page -- not
the ``images/*.aim`` of the same name, which is the packer's leftover source and
is not what the engine reads.

WHAT IT DELIBERATELY DOES NOT DO
--------------------------------
It draws each element's rectangle, not its artwork.  Compositing a screen means
nine-slice stretching every frame, and a wrong reconstruction of the interface
would mislead more than an honest wireframe does.  The selected element's real
pixels are shown beside the table, one decode at a time.

Widgets only.  ``Session.open_screen`` / ``set_screen_rects`` /
``screen_element_image`` hold the work, and are tested.
"""

from __future__ import annotations

import posixpath
from typing import Optional

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QApplication,
    QDialog,
    QDialogButtonBox,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStyledItemDelegate,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from dsotools.errors import DsoError

from .. import workers

#: Only x/y/w/h can be typed into; the rest of the row is what the file says.
_EDITABLE_COLUMNS = (2, 3, 4, 5)

_OUTLINE = "#38bdf8"
_SELECTED = "#f97316"
_SCREEN = "#7f8c8d"
#: Elements the engine builds and places itself.  Drawn, because leaving a
#: slider's handle out makes the layout look broken, but never as something to
#: grab: its rectangle is an offset from its parent, not a position.
_CHILD = "#94a3b8"


def _at_rest(element):
    """The drawable an element shows when nothing is happening to it.

    A ``CButton`` names one per state and the **disabled** one comes first, so
    reading ``drawables[0]`` reports and previews every button greyed out --
    125 of the 694 drawing elements in the stock screens.
    """
    drawables = element.get("drawables") or []
    if not drawables:
        return None
    return drawables[min(element.get("resting", 0), len(drawables) - 1)]


def _mark_child(item, owner) -> None:
    """Say, in the row itself, that the engine owns this one.

    Its rectangle is an offset from its parent and the game recomputes it, so
    the row is shown -- leaving it out is what made MOD_MANAGER look like it
    was missing most of its elements -- but never offered as editable.
    """
    font = item.font(0)
    font.setItalic(True)
    for col in range(item.columnCount()):
        item.setFont(col, font)
        item.setForeground(col, QBrush(QColor(_CHILD)))
    hint = (f"Part of {owner.text(1)}. The engine builds and places this one; "
            f"its x and y are an offset from its parent, so they are read-only.")
    for col in range(item.columnCount()):
        item.setToolTip(col, hint)


class _RectOnlyDelegate(QStyledItemDelegate):
    """No editor except on the four geometry columns.

    Same reasoning as the Data tab: a column that cannot be edited should not
    offer a cursor, rather than accept text and silently put it back.
    """

    def createEditor(self, parent, option, index):
        if index.column() not in _EDITABLE_COLUMNS:
            return None
        # An element the engine owns is placed by its parent, so a number typed
        # here would move it somewhere the game will not put it.
        element = index.sibling(index.row(), 0).data(Qt.ItemDataRole.UserRole)
        if isinstance(element, dict) and element.get("parent", -1) >= 0:
            return None
        return super().createEditor(parent, option, index)


class ScreenCanvas(QGraphicsView):
    """The layout, drawn to scale, one rectangle per element."""

    def __init__(self, on_pick, on_move=None) -> None:
        super().__init__()
        self._on_pick = on_pick
        #: Called with ``(index, (x, y, w, h))`` while an element is dragged.
        self._on_move = on_move
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setBackgroundBrush(QBrush(QColor("#2b2b2b")))
        self._rects = {}
        self._labels = {}
        self._art = {}
        #: Indices the engine owns; not draggable.
        self._children = set()
        self._selected = None
        #: What is drawn.  Frames off leaves only the selected element outlined,
        #: which is the point of the artwork view: you cannot see what you are
        #: dragging through 60 boxes.
        self.show_frames = True
        self.show_labels = True
        #: ``(index, grab point, rect when the drag started)`` while dragging.
        self._drag = None

    def clear_screen(self) -> None:
        self._scene.clear()
        self._rects = {}
        self._labels = {}
        self._art = {}
        self._children = set()
        # An index means nothing across screens -- element 1 of one layout is
        # not element 1 of the next -- so a selection that survives the switch
        # highlights an unrelated rectangle.
        self._selected = None

    def show_screen(self, rect, elements) -> None:
        self.clear_screen()
        # Only the size is used.  Two of the 83 screens sit at a non-zero
        # origin -- STATUSLEISTE at (300, 0) -- and their elements are laid out
        # *inside* that box, so subtracting it dragged half of them off the
        # left edge of the canvas.
        w, h = max(rect[2], 1), max(rect[3], 1)
        frame = self._scene.addRect(
            QRectF(0, 0, w, h), QPen(QColor(_SCREEN)), QBrush(QColor("#1b1b1b"))
        )
        frame.setZValue(-1)

        for element in elements:
            ex, ey, ew, eh = element["rect"]
            # Where it *is*, which for a child is its parent's corner plus its
            # own rectangle -- MOD_MANAGER's slider parts are stored at
            # (0, 372) and (1, -14) and belong on a slider at (444, 59).
            ox, oy = element.get("origin", (ex, ey))
            owned = element.get("parent", -1) >= 0
            if owned:
                self._children.add(element["index"])
            pen = QPen(QColor(_CHILD if owned else _OUTLINE))
            pen.setCosmetic(True)
            if owned:
                pen.setStyle(Qt.PenStyle.DashLine)
            # The *position* carries where it is and the rect only its size, so
            # that moving an element moves everything parented to it.  With the
            # offset baked into the rect instead, the label stayed behind --
            # which is exactly how this was reported.
            item = QGraphicsRectItem(0, 0, max(ew, 1), max(eh, 1))
            item.setPos(ox, oy)
            item.setPen(pen)
            item.setData(0, element["index"])
            item.setToolTip(
                f"{element['class']}  {element['name']}\n({ex}, {ey})  {ew}x{eh}"
                + (f"\npart of another element, drawn at ({ox}, {oy})"
                   if owned else "")
            )
            self._scene.addItem(item)
            self._rects[element["index"]] = item

            # Labelled where the label fits.  60 unlabelled rectangles is a
            # puzzle, but 60 labels stacked on top of each other is worse --
            # PLASMATREE's buttons are 30x28 and its statics overlap, so a
            # name is drawn only where its own box can hold one, and the
            # tooltip carries it everywhere else.
            if ew >= 40 and eh >= 11:
                label = QGraphicsSimpleTextItem(element["name"], item)
                label.setBrush(QBrush(QColor("#c8c8c8")))
                font = label.font()
                font.setPointSizeF(max(5.0, min(8.0, eh / 3.0)))
                label.setFont(font)
                label.setPos(2, 1)
                if label.boundingRect().width() > ew:
                    label.setText(element["name"][:max(3, int(ew / 5))] + "…")
                label.setZValue(3)
                label.setVisible(self.show_labels)
                self._labels[element["index"]] = label

        self._scene.setSceneRect(QRectF(0, 0, w, h))
        self.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
        # The switches are the canvas's state, not the screen's: a new layout
        # has to be drawn the way the user last asked for, and these items were
        # built with a solid pen regardless -- which is why frames came back
        # every time the screen changed.
        self._apply_visibility()

    def set_artwork(self, images) -> None:
        """Draw what each element draws, underneath its outline.

        ``images`` is what ``Session.screen_artwork`` returns.  Placed as a
        child of the element's own item so it moves with a drag, and behind the
        outline so the outline stays visible on top of it.
        """
        from PySide6.QtGui import QPixmap

        from .textures_tab import rgba_to_pixmap

        for item in self._art.values():
            scene = item.scene()
            if scene is not None:
                scene.removeItem(item)
        self._art = {}

        for entry in images or ():
            parent = self._rects.get(entry["index"])
            if parent is None:
                continue
            pixmap: QPixmap = rgba_to_pixmap(
                entry["width"], entry["height"], entry["rgba"])
            art = QGraphicsPixmapItem(pixmap, parent)
            art.setPos(0, 0)
            art.setZValue(-1)          # under the outline, over the backdrop
            art.setTransformationMode(Qt.TransformationMode.SmoothTransformation)
            self._art[entry["index"]] = art
        self._apply_visibility()

    def clear_artwork(self) -> None:
        for item in self._art.values():
            scene = item.scene()
            if scene is not None:
                scene.removeItem(item)
        self._art = {}

    def set_visibility(self, *, frames=None, labels=None) -> None:
        if frames is not None:
            self.show_frames = frames
        if labels is not None:
            self.show_labels = labels
        self._apply_visibility()

    def _apply_visibility(self, selected: Optional[int] = None) -> None:
        """Frames off hides every outline **except the selected one**.

        Hiding that one too would answer "what am I dragging?" with "nothing",
        which is the question the toggles exist for.
        """
        if selected is None:
            selected = self._selected
        for index, item in self._rects.items():
            chosen = index == selected
            pen = item.pen()
            if self.show_frames or chosen:
                pen.setStyle(Qt.PenStyle.DashLine if index in self._children
                             else Qt.PenStyle.SolidLine)
            else:
                pen.setStyle(Qt.PenStyle.NoPen)
            item.setPen(pen)
        for label in self._labels.values():
            label.setVisible(self.show_labels)

    def select(self, index: Optional[int]) -> None:
        self._selected = index
        for key, item in self._rects.items():
            chosen = key == index
            resting = _CHILD if key in self._children else _OUTLINE
            pen = QPen(QColor(_SELECTED if chosen else resting))
            pen.setCosmetic(True)
            pen.setWidth(3 if chosen else 1)
            item.setPen(pen)
            item.setZValue(2 if chosen else 0)
        self._apply_visibility(index)

    def update_rect(self, index: int, rect, origin=None) -> None:
        """Move and resize one element, label and all."""
        item = self._rects.get(index)
        if item is None:
            return
        x, y, w, h = rect
        item.setPos(x, y)
        item.setRect(0, 0, max(w, 1), max(h, 1))
        art = self._art.get(index)
        if art is not None and art.pixmap().width():
            # The artwork was composed for the old size; scale the pixmap we
            # have rather than recompose on every mouse move -- a resize is
            # re-rendered properly when the toggle is switched again.
            art.setScale(1.0)
            sx = max(w, 1) / art.pixmap().width()
            sy = max(h, 1) / art.pixmap().height()
            art.setTransform(art.transform().fromScale(sx, sy))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self._scene.sceneRect().width():
            self.fitInView(self._scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)

    def _at(self, pos):
        """The element under ``pos``, smallest first.

        A background covers the whole screen and is never the thing you meant
        to click, so the smallest rectangle containing the point wins -- the
        same rule the TexPage editor uses for nested sprites.
        """
        best = None
        for index, item in self._rects.items():
            box = item.sceneBoundingRect()
            if box.contains(pos):
                area = box.width() * box.height()
                if best is None or area < best[1]:
                    best = (index, area)
        return None if best is None else best[0]

    def mousePressEvent(self, event) -> None:
        pos = self.mapToScene(event.position().toPoint())
        index = self._at(pos)
        if index is not None:
            self._on_pick(index)
            if (event.button() == Qt.MouseButton.LeftButton
                    and index not in self._children):
                item = self._rects[index]
                self._drag = (index, pos, item.pos(), item.rect())
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        """Drag the picked element, in whole layout pixels.

        Rounded rather than continuous: the file stores integers, so a drag
        that reported 184.6 would be a number nobody could type back in.
        """
        if self._drag is not None:
            index, grabbed, start, rect = self._drag
            pos = self.mapToScene(event.position().toPoint())
            dx = round(pos.x() - grabbed.x())
            dy = round(pos.y() - grabbed.y())
            item = self._rects[index]
            item.setPos(start.x() + dx, start.y() + dy)
            if self._on_move is not None:
                self._on_move(index, (
                    int(item.pos().x()), int(item.pos().y()),
                    int(rect.width()), int(rect.height()),
                ))
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._drag = None
        super().mouseReleaseEvent(event)


class InterfaceTab(QWidget):
    def __init__(self, window) -> None:
        super().__init__()
        self.window = window
        self.session = window.session
        self._rows: list = []
        self._file: Optional[str] = None
        self._data: Optional[dict] = None
        #: ``{element index: (x, y, w, h)}`` -- moves not yet written.
        self._edits: dict = {}
        self._loading = False

        layout = QVBoxLayout(self)

        row = QHBoxLayout()
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("filter screens...")
        self.filter_edit.textChanged.connect(self._apply_filter)
        self.btn_reload = QPushButton("Reload")
        self.btn_reload.clicked.connect(self.reload)
        row.addWidget(self.filter_edit, 1)
        row.addWidget(self.btn_reload)
        layout.addLayout(row)

        split = QSplitter(Qt.Orientation.Horizontal)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["Screen", "In mod", "Source"])
        self.tree.setRootIsDecorated(False)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.tree.itemSelectionChanged.connect(self._open_selected)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._file_menu)
        split.addWidget(self.tree)

        right = QWidget()
        rl = QVBoxLayout(right)
        self.title = QLabel("Open a game folder, then pick a screen.")
        self.title.setWordWrap(True)
        rl.addWidget(self.title)

        vsplit = QSplitter(Qt.Orientation.Vertical)

        top = QWidget()
        tl = QVBoxLayout(top)
        tl.setContentsMargins(0, 0, 0, 0)
        self.canvas = ScreenCanvas(self._picked, self._dragged)
        self.canvas.setMinimumHeight(240)
        tl.addWidget(self.canvas, 1)

        toggles = QHBoxLayout()
        self.chk_frames = QCheckBox("Frames")
        self.chk_frames.setChecked(True)
        self.chk_frames.setToolTip(
            "Outline every element. Off leaves only the selected one outlined, "
            "which is how you see what you are dragging.")
        self.chk_frames.toggled.connect(
            lambda on: self.canvas.set_visibility(frames=on))
        self.chk_labels = QCheckBox("Labels")
        self.chk_labels.setChecked(True)
        self.chk_labels.setToolTip("Name each element, where the name fits")
        self.chk_labels.toggled.connect(
            lambda on: self.canvas.set_visibility(labels=on))
        self.chk_art = QCheckBox("Artwork")
        self.chk_art.setToolTip(
            "Draw what each element draws, instead of an empty box. Frames are "
            "composed from their nine tiles; a stretched sprite that is not a "
            "tile family is scaled, and says so.")
        self.chk_art.toggled.connect(self._artwork_toggled)
        for box in (self.chk_frames, self.chk_labels, self.chk_art):
            toggles.addWidget(box)
        self.art_note = QLabel("")
        toggles.addWidget(self.art_note, 1)
        tl.addLayout(toggles)
        vsplit.addWidget(top)

        lower = QWidget()
        ll = QVBoxLayout(lower)
        ll.setContentsMargins(0, 0, 0, 0)

        self.elements = QTreeWidget()
        self.elements.setColumnCount(7)
        self.elements.setHeaderLabels(["Class", "Name", "x", "y", "w", "h", "Draws"])
        # Nested, because 133 elements across the stock screens are parts of
        # another element rather than of the screen, and their coordinates only
        # make sense against their parent.
        self.elements.setRootIsDecorated(True)
        self.elements.setAlternatingRowColors(True)
        self.elements.setItemDelegate(_RectOnlyDelegate(self.elements))
        self.elements.itemSelectionChanged.connect(self._row_selected)
        self.elements.itemChanged.connect(self._cell_changed)
        self.elements.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.elements.customContextMenuRequested.connect(self._element_menu)
        ll.addWidget(self.elements, 1)

        actions = QHBoxLayout()
        self.preview = QLabel("")
        self.preview.setMinimumHeight(48)
        self.preview.setToolTip("The pixels the selected element draws")
        actions.addWidget(self.preview)
        self.notes = QLabel("")
        self.notes.setWordWrap(True)
        actions.addWidget(self.notes, 1)
        self.btn_revert = QPushButton("Revert")
        self.btn_revert.clicked.connect(self.revert)
        self.btn_save = QPushButton("Save to mod")
        self.btn_save.clicked.connect(self.save)
        actions.addWidget(self.btn_revert)
        actions.addWidget(self.btn_save)
        ll.addLayout(actions)

        vsplit.addWidget(lower)
        vsplit.setStretchFactor(0, 3)
        vsplit.setStretchFactor(1, 2)
        rl.addWidget(vsplit, 1)

        split.addWidget(right)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 3)
        split.setSizes([320, 900])
        layout.addWidget(split, 1)
        self._sync_controls()

    # -- browsing ------------------------------------------------------------

    def refresh(self) -> None:
        self._data = None
        self._edits = {}
        self.elements.clear()
        self.canvas.clear_screen()
        self.reload()

    def reload(self) -> None:
        if not self.session.stock:
            self.tree.clear()
            self.title.setText("Open a game folder, then pick a screen.")
            return
        self.btn_reload.setEnabled(False)
        workers.run(
            self.session.screens,
            on_result=self._rows_ready,
            on_error=self._failed,
            on_done=lambda: self.btn_reload.setEnabled(True),
        )

    def _rows_ready(self, rows) -> None:
        self._rows = rows
        self._apply_filter()
        if self._file:
            self._open_file(self._file)

    def _apply_filter(self) -> None:
        needle = self.filter_edit.text().strip().lower()
        # Signals off while the list is rebuilt: repopulating changes the
        # selection, which would open whatever landed in row 0.
        self.tree.blockSignals(True)
        self.tree.clear()
        for r in self._rows:
            if needle and needle not in r["name"].lower():
                continue
            item = QTreeWidgetItem(
                [r["name"], "yes" if r["in_mod"] else "", r["source"]])
            item.setData(0, Qt.ItemDataRole.UserRole, r)
            item.setToolTip(0, f"{r['vpath']}  ({r['size']:,} bytes)")
            if r["in_mod"]:
                item.setForeground(0, QBrush(QColor("#2980b9")))
            self.tree.addTopLevelItem(item)
        if self._file:
            for i in range(self.tree.topLevelItemCount()):
                item = self.tree.topLevelItem(i)
                row = item.data(0, Qt.ItemDataRole.UserRole)
                if row and row["vpath"].lower() == self._file.lower():
                    self.tree.setCurrentItem(item)
                    break
        self.tree.blockSignals(False)
        for c in range(3):
            self.tree.resizeColumnToContents(c)

    def _open_selected(self) -> None:
        items = self.tree.selectedItems()
        if not items:
            return
        row = items[0].data(0, Qt.ItemDataRole.UserRole)
        if row and self._confirm_discard():
            self._open_file(row["vpath"])

    def reveal(self, vpath: str) -> None:
        """Open ``vpath`` here, for the cross-tab jump."""
        self.filter_edit.blockSignals(True)
        self.filter_edit.clear()
        self.filter_edit.blockSignals(False)
        self._file = vpath
        self._apply_filter()
        self._open_file(vpath)

    # -- one screen ----------------------------------------------------------

    def _open_file(self, vpath: str) -> None:
        self._file = vpath
        self.title.setText(f"{vpath} - reading...")
        workers.run(
            self.session.open_screen,
            vpath,
            on_result=lambda data, p=vpath: self._screen_ready(p, data),
            on_error=self._failed,
        )

    def _screen_ready(self, vpath: str, data: dict) -> None:
        if vpath != self._file:
            return                      # the user moved on while this was read
        self._data = data
        self._edits = {}
        self.preview.clear()
        _x, _y, w, h = data["rect"]
        self.title.setText(
            f"<b>{data['name']}</b> - {len(data['elements'])} elements, {w}x{h}")

        note = ""
        if data["declared_children"] != len(data["elements"]):
            # Said out loud rather than hidden: the screen's own count
            # disagrees in 19 of the 83 stock files, which is what element
            # nesting looks like -- and the nesting is not decoded.
            note = (f"<span style='color:#9a6f09'>The screen declares "
                    f"{data['declared_children']} children and the file holds "
                    f"{len(data['elements'])} elements: some belong to others, "
                    f"and which is not established.</span>")
        self.notes.setText(note)
        self.canvas.show_screen(data["rect"], data["elements"])
        if self.chk_art.isChecked():
            self._artwork_toggled(True)
        self._fill_elements()
        self._sync_controls()

    def _fill_elements(self) -> None:
        self._loading = True
        try:
            self.elements.clear()
            by_index = {}
            for element in (self._data or {}).get("elements", []):
                rect = self._edits.get(element["index"], element["rect"])
                draws = ""
                if element["drawables"]:
                    # What it shows at rest: a button lists its disabled
                    # artwork first, and that is not the state to report.
                    at_rest = element["drawables"][
                        min(element.get("resting", 0), len(element["drawables"]) - 1)]
                    draws = at_rest["source"] or at_rest["reference"]
                item = QTreeWidgetItem(
                    [element["class"], element["name"]]
                    + [str(v) for v in rect] + [draws]
                )
                item.setData(0, Qt.ItemDataRole.UserRole, element)
                owner = by_index.get(element.get("parent", -1))
                if owner is None:
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
                    item.setToolTip(0, "Read-only: this moves elements, it does "
                                       "not change what they are.")
                    self.elements.addTopLevelItem(item)
                else:
                    _mark_child(item, owner)
                    owner.addChild(item)
                    owner.setExpanded(True)
                by_index[element["index"]] = item
                if element["index"] in self._edits:
                    for col in _EDITABLE_COLUMNS:
                        item.setForeground(col, QBrush(QColor("#2980b9")))
        finally:
            self._loading = False
        for c in range(7):
            self.elements.resizeColumnToContents(c)

    def _row_selected(self) -> None:
        # `_fill_elements` clears and refills the tree, and Qt emits a
        # selection change while it does -- which selected element 0 of the new
        # screen and outlined it, with the table itself showing no selection.
        # The same guard `_cell_changed` already had.
        if self._loading:
            return
        items = self.elements.selectedItems()
        if not items:
            return
        element = items[0].data(0, Qt.ItemDataRole.UserRole)
        self.canvas.select(element["index"])
        self._show_pixels(element)

    def _element_rows(self):
        """Every row, parents and the elements nested under them alike."""
        stack = [self.elements.topLevelItem(i)
                 for i in range(self.elements.topLevelItemCount())]
        while stack:
            item = stack.pop()
            stack.extend(item.child(i) for i in range(item.childCount()))
            yield item

    def _picked(self, index: int) -> None:
        for item in self._element_rows():
            element = item.data(0, Qt.ItemDataRole.UserRole)
            if element and element["index"] == index:
                self.elements.setCurrentItem(item)
                self.elements.scrollToItem(item)
                return

    def _artwork_toggled(self, on: bool) -> None:
        """Load the pixels on demand -- it is a page decode per atlas page."""
        if not on:
            self.canvas.clear_artwork()
            self.art_note.clear()
            return
        if not self._file:
            return
        self.art_note.setText("drawing...")
        workers.run(
            self.session.screen_artwork,
            self._file,
            on_result=self._artwork_ready,
            on_error=self._artwork_failed,
        )

    def _artwork_ready(self, images) -> None:
        self.canvas.set_artwork(images)
        exact = sum(1 for i in images if i["how"] == "exact")
        nine = sum(1 for i in images if i["how"] == "nine-slice")
        stretched = sum(1 for i in images if i["how"] == "stretched")
        # Said plainly: a stretched sprite is an approximation of what the game
        # draws, and a preview that will not admit that is worse than a box.
        note = f"{exact} exact, {nine} composed from tiles"
        if stretched:
            note += f", {stretched} stretched (approximate)"
        self.art_note.setText(note)

    def _artwork_failed(self, message: str, _traceback: str) -> None:
        self.art_note.setText(f"artwork unavailable: {message}")

    def _dragged(self, index: int, rect) -> None:
        """A drag on the canvas is the same edit as typing in the table.

        It goes through the same ``_edits`` dict and updates the same row, so
        Save, Revert and the blue "changed" marks cannot disagree with what is
        on the picture.
        """
        if self._data is None:
            return
        element = next(
            (e for e in self._data["elements"] if e["index"] == index), None)
        if element is None:
            return
        if tuple(rect) == tuple(element["rect"]):
            self._edits.pop(index, None)
        else:
            self._edits[index] = tuple(rect)

        self._loading = True
        try:
            for item in self._element_rows():
                row = item.data(0, Qt.ItemDataRole.UserRole)
                if not row or row["index"] != index:
                    continue
                for col, value in zip(_EDITABLE_COLUMNS, rect):
                    item.setText(col, str(value))
                    if index in self._edits:
                        item.setForeground(col, QBrush(QColor("#2980b9")))
                    else:
                        item.setData(col, Qt.ItemDataRole.ForegroundRole, None)
                break
        finally:
            self._loading = False
        self._sync_controls()

    def _show_pixels(self, element) -> None:
        """The selected element's real artwork, decoded on a worker."""
        self.preview.clear()
        if not element["drawables"] or not self._file:
            return
        workers.run(
            self.session.screen_element_image,
            self._file,
            element["index"],
            on_result=self._pixels_ready,
            on_error=lambda *_: self.preview.clear(),
        )

    def _pixels_ready(self, decoded) -> None:
        from .textures_tab import rgba_to_pixmap

        pixmap = rgba_to_pixmap(decoded["width"], decoded["height"], decoded["rgba"])
        if pixmap.height() > 44:
            pixmap = pixmap.scaledToHeight(
                44, Qt.TransformationMode.SmoothTransformation)
        self.preview.setPixmap(pixmap)
        self.preview.setToolTip(decoded["summary"])

    def _cell_changed(self, item, column: int) -> None:
        if self._loading or column not in _EDITABLE_COLUMNS:
            return
        element = item.data(0, Qt.ItemDataRole.UserRole)
        if element is None or self._data is None:
            return
        try:
            rect = tuple(int(item.text(c)) for c in _EDITABLE_COLUMNS)
        except ValueError:
            # Not a number: put the row back rather than keep an edit nobody
            # can save.
            self._loading = True
            for col, value in zip(
                _EDITABLE_COLUMNS, self._edits.get(element["index"], element["rect"])
            ):
                item.setText(col, str(value))
            self._loading = False
            return

        if rect == tuple(element["rect"]):
            self._edits.pop(element["index"], None)
            for col in _EDITABLE_COLUMNS:
                item.setData(col, Qt.ItemDataRole.ForegroundRole, None)
        else:
            self._edits[element["index"]] = rect
            for col in _EDITABLE_COLUMNS:
                item.setForeground(col, QBrush(QColor("#2980b9")))
        self.canvas.update_rect(element["index"], rect)
        self._sync_controls()

    # -- saving --------------------------------------------------------------

    def _sync_controls(self) -> None:
        dirty = bool(self._edits)
        self.btn_save.setEnabled(dirty and bool(self.session.mod))
        self.btn_revert.setEnabled(dirty)
        if dirty and not self.session.mod:
            self.btn_save.setToolTip("Open a mod first - edits are saved into it")
        else:
            self.btn_save.setToolTip(
                f"Write {len(self._edits)} moved element(s) into the mod"
                if dirty else "Nothing has been moved"
            )

    def _confirm_discard(self) -> bool:
        if not self._edits:
            return True
        return QMessageBox.question(
            self, "Unsaved changes",
            f"{len(self._edits)} element(s) have been moved but not saved."
            "<br><br>Open another screen and lose that?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        ) == QMessageBox.StandardButton.Yes

    def revert(self) -> None:
        self._edits = {}
        if self._file:
            self._open_file(self._file)

    def save(self) -> None:
        if not self._edits or not self._file:
            return
        edits = sorted(self._edits.items())
        try:
            routed = self.session.set_screen_rects(self._file, edits)
        except (DsoError, OSError) as exc:
            QMessageBox.warning(self, "Save", str(exc))
            return
        self._edits = {}
        QMessageBox.information(
            self, "Save",
            f"{len(edits)} element(s) written into the mod:<pre>"
            + "\n".join(f"{k}   -> {v}" for k, v in routed.items())
            + "</pre>",
        )
        # Reopened from disk, so what is on screen is what was written.
        self._open_file(self._file)

    def _element_menu(self, pos) -> None:
        """What you can do to the thing an element draws.

        The element itself is geometry -- moving it is the canvas and the
        table.  Everything *else* an author wants is about its drawable, and
        that is an asset like any other, so these are the same shared actions
        the rest of the app offers, aimed one link down the chain.
        """
        item = self.elements.itemAt(pos)
        if item is None:
            return
        element = item.data(0, Qt.ItemDataRole.UserRole)
        if element is None:
            return
        menu, actions = self.build_element_menu(element)
        menu.setToolTipsVisible(True)
        self._run_element_menu(
            element, actions, menu.exec(self.elements.viewport().mapToGlobal(pos)))

    def build_element_menu(self, element):
        """The menu for one element, built but not shown.

        Separate from the handler so it can be *inspected*: ``exec`` opens a
        modal loop, so a menu that raised while being assembled would be
        invisible to every check here.  Same split as the Models tab's
        ``build_parts_menu``, and for the same reason.
        """
        drawable = _at_rest(element)
        anim = (drawable or {}).get("anim_vpath")
        tex = (drawable or {}).get("tex")
        source = (drawable or {}).get("source")

        menu = QMenu(self.elements)
        menu.addSection(f"{element['class']}  {element['name']}")
        act_preview = menu.addAction("Preview what it draws...")
        act_preview.setEnabled(bool(drawable and drawable.get("rect")))
        act_sprite = menu.addAction("Open the sprite in Textures")
        act_sprite.setEnabled(bool(tex and source))
        if not tex:
            act_sprite.setToolTip("This element draws nothing that reaches a page")

        menu.addSection(f"Drawable  {posixpath.basename(anim) if anim else '-'}")
        act_export = menu.addAction("Export...")
        act_replace = menu.addAction("Replace...")
        act_reset = menu.addAction("Reset to stock...")
        act_uses = menu.addAction("What uses this...")
        why = self.session.can_reset_to_stock(anim) if anim else "No drawable."
        nothing = "This element draws nothing, so there is no file to act on."
        for act in (act_export, act_replace, act_uses):
            act.setEnabled(bool(anim))
            if not anim:
                act.setToolTip(nothing)
        if anim and not self.session.mod:
            act_replace.setEnabled(False)
            act_replace.setToolTip("Open a mod first - replacements are saved into it")
        act_reset.setEnabled(why is None)
        if why is not None:
            act_reset.setToolTip(why)
        if not act_preview.isEnabled():
            act_preview.setToolTip(nothing)

        menu.addSeparator()
        act_copy_name = menu.addAction("Copy element name")
        act_copy_path = menu.addAction("Copy drawable path")
        act_copy_path.setEnabled(bool(anim))
        if not anim:
            act_copy_path.setToolTip(nothing)

        actions = {
            "preview": act_preview, "sprite": act_sprite, "export": act_export,
            "replace": act_replace, "reset": act_reset, "uses": act_uses,
            "copy_name": act_copy_name, "copy_path": act_copy_path,
        }
        return menu, actions

    def _run_element_menu(self, element, actions, chosen) -> None:
        from ..linked_assets import (
            export_asset,
            replace_asset_dialog,
            reset_asset_to_stock,
            show_users,
        )

        drawable = _at_rest(element)
        anim = (drawable or {}).get("anim_vpath")
        tex = (drawable or {}).get("tex")
        source = (drawable or {}).get("source")

        act_preview = actions["preview"]
        act_sprite = actions["sprite"]
        act_export = actions["export"]
        act_replace = actions["replace"]
        act_reset = actions["reset"]
        act_uses = actions["uses"]
        act_copy_name = actions["copy_name"]
        act_copy_path = actions["copy_path"]

        if chosen is act_preview:
            self._preview_dialog(element)
        elif chosen is act_sprite:
            self.window.tabs.setCurrentWidget(self.window.textures_tab)
            self.window.textures_tab.reveal_sprite(tex, source)
        elif chosen is act_export and anim:
            export_asset(self, self.session, anim)
        elif chosen is act_replace and anim:
            if replace_asset_dialog(self, self.session, anim):
                self.refresh()
        elif chosen is act_reset and anim:
            if reset_asset_to_stock(self, self.session, anim):
                self._edits = {}
        elif chosen is act_uses and anim:
            show_users(self, self.session, anim)
        elif chosen is act_copy_name:
            QApplication.clipboard().setText(element["name"])
        elif chosen is act_copy_path and anim:
            QApplication.clipboard().setText(anim)

    def _preview_dialog(self, element) -> None:
        """The element's artwork, big enough to look at."""
        from .textures_tab import rgba_to_pixmap

        try:
            decoded = self.session.screen_element_image(self._file, element["index"])
        except (DsoError, OSError) as exc:
            QMessageBox.warning(self, "Preview", str(exc))
            return

        dialog = QDialog(self)
        dialog.setWindowTitle(f"{element['name']} - {decoded['summary']}")
        layout = QVBoxLayout(dialog)
        label = QLabel()
        label.setPixmap(rgba_to_pixmap(
            decoded["width"], decoded["height"], decoded["rgba"]))
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(label)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec()

    # -- actions on a file ---------------------------------------------------

    def _file_menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        if item is None:
            return
        row = item.data(0, Qt.ItemDataRole.UserRole)
        if not row:
            return
        vpath = row["vpath"]

        from ..linked_assets import (
            export_asset,
            replace_asset_dialog,
            reset_asset_to_stock,
            show_users,
        )

        menu = QMenu(self.tree)
        act_export = menu.addAction("Export...")
        act_replace = menu.addAction("Replace...")
        act_replace.setEnabled(bool(self.session.mod))
        if not self.session.mod:
            act_replace.setToolTip("Open a mod first - replacements are saved into it")
        act_reset = menu.addAction("Reset to stock...")
        why = self.session.can_reset_to_stock(vpath)
        act_reset.setEnabled(why is None)
        if why is not None:
            act_reset.setToolTip(why)
        act_uses = menu.addAction("What uses this...")
        menu.addSeparator()
        act_copy = menu.addAction("Copy path")
        menu.setToolTipsVisible(True)

        chosen = menu.exec(self.tree.viewport().mapToGlobal(pos))
        if chosen is act_export:
            export_asset(self, self.session, vpath)
        elif chosen is act_replace:
            if replace_asset_dialog(self, self.session, vpath):
                self.refresh()
        elif chosen is act_reset:
            if reset_asset_to_stock(self, self.session, vpath):
                self._edits = {}
        elif chosen is act_uses:
            show_users(self, self.session, vpath)
        elif chosen is act_copy:
            QApplication.clipboard().setText(vpath)

    def _failed(self, message: str, _traceback: str) -> None:
        self.title.setText(f"<b>failed:</b> {message}")


__all__ = ["InterfaceTab", "ScreenCanvas"]
