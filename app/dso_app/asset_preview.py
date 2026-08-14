"""
One asset, on its own, in a dialog.

Exists so following a binding does not cost you your place.  From a submesh's
texture list you usually just want to see *which* texture that is; switching
tabs to find out means losing the scene, the variant and the camera.

Generic by design: it takes a vpath and works out what to show.  Images decode
to a picture, a ``.3do`` gets a bare 3D view, and anything else at least
reports what it is rather than presenting an empty grey rectangle.

A ``.3do`` here is shown **raw** -- geometry only, no textures.  That is not a
shortcut: a model carries no material binding, so there is no such thing as
"this model's textures" outside a scene, and inventing one would be a guess.
"""

from __future__ import annotations

import posixpath

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QScrollArea,
    QVBoxLayout,
)

from dsotools.errors import DsoError

#: Extensions that decode to a picture.
IMAGE_EXTENSIONS = (".dds", ".aim")
#: Extensions that get a 3D view.
MODEL_EXTENSIONS = (".3do",)


class AssetPreviewDialog(QDialog):
    def __init__(self, parent, session, vpath: str) -> None:
        super().__init__(parent)
        self.session = session
        self.vpath = vpath
        self.setWindowTitle(f"Preview — {posixpath.basename(vpath)}")
        self.resize(760, 660)

        self._layout = QVBoxLayout(self)
        self.caption = QLabel(vpath)
        self.caption.setWordWrap(True)
        self.caption.setTextFormat(Qt.TextFormat.RichText)
        self._layout.addWidget(self.caption)

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.buttons.rejected.connect(self.reject)
        self.buttons.accepted.connect(self.accept)

        self._load()
        self._layout.addWidget(self.buttons)

    # -- dispatch -------------------------------------------------------------

    def _load(self) -> None:
        ext = posixpath.splitext(self.vpath)[1].lower()
        try:
            if ext in IMAGE_EXTENSIONS:
                self._show_image()
            elif ext in MODEL_EXTENSIONS:
                self._show_model()
            else:
                self._show_unknown(ext)
        except DsoError as exc:
            self._show_message(str(exc))

    def _body(self, widget) -> None:
        self._layout.addWidget(widget, 1)

    def _show_message(self, text: str) -> None:
        label = QLabel(text)
        label.setWordWrap(True)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._body(label)

    # -- kinds ----------------------------------------------------------------

    def _show_image(self) -> None:
        from .tabs.textures_tab import rgba_to_pixmap

        decoded = self.session.decode_preview(self.vpath)
        view = QLabel()
        view.setAlignment(Qt.AlignmentFlag.AlignCenter)
        view.setStyleSheet("background: #3a3a3a;")
        view.setPixmap(
            rgba_to_pixmap(decoded["width"], decoded["height"], decoded["rgba"])
        )
        scroll = QScrollArea()
        scroll.setWidget(view)
        scroll.setWidgetResizable(True)
        self._body(scroll)
        self.caption.setText(
            f"<b>{self.vpath}</b><br>{decoded['summary']} — "
            f"{decoded['width']}×{decoded['height']}"
        )

    def _show_model(self) -> None:
        from .viewport import ModelViewport

        geometry = self.session.model_geometry(self.vpath)
        viewport = ModelViewport()
        if not viewport.ensure_loaded():
            self._show_message(
                "The 3D viewport could not start.\n\n"
                + "\n".join(viewport.errors)
            )
            return
        viewport.show_geometry(geometry)
        self._body(viewport)
        lods = max(geometry.lod_counts.values(), default=1)
        self.caption.setText(
            f"<b>{self.vpath}</b><br>{len(geometry.calls)} submesh(es) · "
            f"{geometry.triangle_count():,} triangles · {lods} LOD(s)"
            "<br><span style='color: palette(mid);'>Geometry only — a model "
            "carries no textures of its own; the binding lives in whichever "
            "scene references it.</span>"
        )

    def _show_unknown(self, ext: str) -> None:
        data = self.session.read_asset(self.vpath)
        kind = ext.lstrip(".").upper() or "file"
        head = " ".join(f"{b:02x}" for b in data[:16])
        self._show_message(
            f"{kind} — {len(data):,} bytes\n\n"
            f"first bytes: {head}\n\n"
            "No preview for this format yet."
        )


__all__ = ["AssetPreviewDialog"]
