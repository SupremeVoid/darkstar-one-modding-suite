"""
The assets bound to whatever you have selected, and what you can do to them.

WHY THIS IS SHARED
------------------
The Models tab and the Textures tab ask the same question from opposite ends.
In Models you have a submesh and want its model, shader and textures.  In
Textures you have a page and want to know which scenes draw it.  Both answers
are a list of assets with the same five actions attached, and having written
that list twice we would have two context menus that drift.

So it is one widget.  The tabs supply the rows and say where "open this" goes;
everything else -- the menu, the replace rules, the reverse lookup -- lives
here.

WHAT "REPLACE" MEANS DEPENDS ON THE TARGET
------------------------------------------
`.dds` is installed byte for byte, because this project has no DDS writer and
will not pretend otherwise: re-encoding means choosing a DXT compressor and
silently changing mipmaps and quality on the author's behalf.  `.3do` accepts a
`.glb` through the existing importer.  Everything else is a verbatim copy.
`Session.replace_asset` is where those rules actually live.
"""

from __future__ import annotations

import os
import posixpath
from typing import Callable, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QFileDialog,
    QMenu,
    QMessageBox,
    QTreeWidget,
    QTreeWidgetItem,
)

from dsotools.errors import DsoError

from .session import mesh_for, texture_refs

def dismissible_notice(parent, session, key: str, title: str, text: str) -> None:
    """Say something once, quietly, with a way to stop saying it.

    Two deliberate differences from ``QMessageBox.information``:

    * **No icon, and therefore no sound.**  On Windows the alert sound is tied
      to the message box's *icon*, so an informational dialog dings every time.
      For guidance the user asked for by opening a file picker, that reads as
      an error.
    * **A "do not show this again" box**, remembered in the settings file.
      Advice worth giving once is not worth giving forty times, and a tool that
      cannot be told to stop gets ignored wholesale.

    Suppression is per ``key``, so silencing one notice never silences another.
    """
    settings = getattr(session, "settings", None)
    if settings is not None and settings.notice_hidden(key):
        return

    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setText(text)
    box.setTextFormat(Qt.TextFormat.RichText)
    box.setIcon(QMessageBox.Icon.NoIcon)          # silence
    box.setStandardButtons(QMessageBox.StandardButton.Ok)
    again = QCheckBox("Do not show this again")
    box.setCheckBox(again)
    box.exec()
    if settings is not None and again.isChecked():
        settings.hide_notice(key)


#: What a file picker should offer for each kind of target.
_REPLACE_FILTERS = {
    ".dds": "DirectDraw Surface (*.dds)",
    ".3do": "glTF binary (*.glb *.gltf);;Darkstar model (*.3do)",
    ".aim": "Ascaron image (*.aim)",
}


class LinkedAssets(QTreeWidget):
    """A table of bound assets with a context menu.

    ``on_open`` is called with a vpath when the user asks to jump to it; the
    main window decides which tab that is.
    """

    def __init__(self, session, on_open: Optional[Callable[[str], None]] = None,
                 on_changed: Optional[Callable[[], None]] = None) -> None:
        super().__init__()
        self.session = session
        self._on_open = on_open
        self._on_changed = on_changed

        self.setColumnCount(4)
        # "Format" is the answer to the question that decides whether a
        # replacement looks right, and it is here rather than only in the
        # Replace… notice because that notice can be silenced for good. The
        # information is too load-bearing to live only behind a dismissible.
        #
        # There is no separate "Resolves" column: it could only ever say
        # MISSING, and a file that does not resolve has no source either -- so
        # the two are one column that says where the bytes come from, or that
        # there are none.  Four columns fit the pane; five did not, and the
        # clipped one was this.
        self.setHeaderLabels(["Role", "Asset", "Format", "Source"])
        #: ``{vpath: format}``, filled on a worker by whoever owns this panel.
        self._formats: dict = {}
        self.setRootIsDecorated(False)
        self.setAlternatingRowColors(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)
        self.itemDoubleClicked.connect(self._open_item)

    # -- contents ------------------------------------------------------------

    def set_asset_info(self, info: dict) -> None:
        """Cache ``{vpath: facts}`` from :meth:`Session.asset_info`.

        Filled by the tab on a worker, because a texture's format means reading
        its header: ~4 ms each, nothing off the GUI thread and a visible hitch
        on it for a scene with a hundred textures.
        """
        self._formats = dict(info or {})

    def _facts(self, row) -> dict:
        """The row's own facts, filled in from the cache where it has none.

        The whole-scene tree builds rows from the parsed scene and knows only
        whether a reference resolves; everything else -- the source, the mod
        marker, the format -- comes from here.  Without it a texture the mod had
        just replaced looked identical to one it had not.
        """
        if not isinstance(row, dict):
            return {}
        known = self._formats.get(row.get("vpath")) or {}
        if isinstance(known, str):          # tolerate a bare {vpath: format} map
            known = {"format": known}
        merged = dict(known)
        merged.update({k: v for k, v in row.items() if v not in (None, "")})
        return merged

    def show_assets(self, rows: List[dict]) -> None:
        """``rows`` are ``{role, vpath, resolved, source, format}`` dicts."""
        self.clear()
        for r in rows:
            item = QTreeWidgetItem([r.get("role", "")] + _asset_columns(self._facts(r)))
            item.setData(0, Qt.ItemDataRole.UserRole, r)
            _paint_asset(item, self._facts(r))
            self.addTopLevelItem(item)
        for c in range(4):
            self.resizeColumnToContents(c)
        # A vpath is long enough to push everything after it off the edge, and
        # what is after it -- the format, and where the file comes from -- is
        # the part you cannot get by reading the name.
        self.setColumnWidth(1, min(self.columnWidth(1), 320))

    def show_all_meshes(self, detail, geometry, selection, layers) -> None:
        """Every visible submesh as a branch, its assets as leaves.

        With nothing selected this panel used to be blank, which is a table
        occupying space to say nothing.  A tree of "mesh → what it binds" is
        the same information the readout above shows, plus the actions.
        """
        self.clear()
        if detail is None or geometry is None:
            return
        # `markers=False` for the same reason the parts table uses it: a blinker
        # group is a point-sprite emitter, not a submesh.  It has no model, no
        # shader and no material, so every column here would be a dash -- and
        # its texture belongs to the blinker editor, which is where the group is
        # actually edited.
        for call in geometry.visible_calls(selection, layers, markers=False):
            mesh = mesh_for(detail, call)
            parent = QTreeWidgetItem([
                call.name,
                posixpath.basename(call.shader or "") or "—",
                "",
                "",
            ])
            self.addTopLevelItem(parent)
            refs = [(
                "model",
                (mesh or {}).get("model_vpath") or (mesh or {}).get("model"),
                bool((mesh or {}).get("model_vpath")),
            )]
            refs += texture_refs(mesh, call)
            for role, ref, ok in refs:
                row = {"role": role, "vpath": ref, "resolved": ok}
                facts = self._facts(row)
                child = QTreeWidgetItem([role] + _asset_columns(facts))
                child.setData(0, Qt.ItemDataRole.UserRole, row)
                _paint_asset(child, facts)
                parent.addChild(child)
            parent.setExpanded(True)
        self.setRootIsDecorated(True)
        for c in range(4):
            self.resizeColumnToContents(c)
        # A vpath is long enough to push everything after it off the edge, and
        # what is after it -- the format, and where the file comes from -- is
        # the part you cannot get by reading the name.
        self.setColumnWidth(1, min(self.columnWidth(1), 320))

    def selected(self) -> Optional[dict]:
        items = self.selectedItems()
        return items[0].data(0, Qt.ItemDataRole.UserRole) if items else None

    # -- actions -------------------------------------------------------------

    def _menu(self, pos) -> None:
        item = self.itemAt(pos)
        if item is None:
            return
        # A branch row is a submesh, not an asset, and carries no data.  It used
        # to reach row.get() and take the app down with an AttributeError.
        row = item.data(0, Qt.ItemDataRole.UserRole)
        vpath = row.get("vpath") if isinstance(row, dict) else None
        if not vpath:
            return

        menu = QMenu(self)
        act_preview = menu.addAction("Preview…")
        act_replace = menu.addAction("Replace…")
        act_replace.setEnabled(bool(self.session.mod) and row.get("resolved", True))
        if not self.session.mod:
            act_replace.setToolTip("Open a mod first — replacements are saved into it")
        act_open = menu.addAction("Open in its tab")
        act_open.setEnabled(self._on_open is not None)
        act_uses = menu.addAction("What uses this…")
        act_reset = menu.addAction("Reset to stock…")
        why = self.session.can_reset_to_stock(vpath)
        act_reset.setEnabled(why is None)
        if why is not None:
            # Disabled *and* explained: a greyed-out item with no reason is how
            # people conclude the app is broken.
            act_reset.setToolTip(why)
        menu.addSeparator()
        act_export = menu.addAction("Export…")
        act_copy = menu.addAction("Copy path")
        menu.setToolTipsVisible(True)

        chosen = menu.exec(self.viewport().mapToGlobal(pos))
        if chosen is act_preview:
            self.preview(vpath)
        elif chosen is act_replace:
            self.replace(vpath)
        elif chosen is act_open:
            self._open(vpath)
        elif chosen is act_uses:
            self.show_users(vpath)
        elif chosen is act_reset:
            self.reset_to_stock(vpath)
        elif chosen is act_export:
            self.export(vpath)
        elif chosen is act_copy:
            QApplication.clipboard().setText(vpath)

    def _open_item(self, item, _column) -> None:
        row = item.data(0, Qt.ItemDataRole.UserRole)
        if isinstance(row, dict) and row.get("vpath"):
            self._open(row["vpath"])

    def _open(self, vpath: str) -> None:
        if self._on_open is not None:
            self._on_open(vpath)

    def replace(self, vpath: str) -> None:
        if replace_asset_dialog(self, self.session, vpath) and self._on_changed:
            self._on_changed()

    def preview(self, vpath: str) -> None:
        """Show one asset in a dialog, without leaving the tab.

        Jumping to the other tab loses your place; often you only want to see
        which texture this actually is.
        """
        from .asset_preview import AssetPreviewDialog

        try:
            AssetPreviewDialog(self, self.session, vpath).exec()
        except DsoError as exc:
            QMessageBox.warning(self, "Preview", str(exc))

    def export(self, vpath: str) -> None:
        export_asset(self, self.session, vpath)

    def reset_to_stock(self, vpath: str) -> None:
        if reset_asset_to_stock(self, self.session, vpath) and self._on_changed:
            # Same callback a Replace uses: the scene has to be rebuilt, or the
            # viewport keeps showing the texture that was just removed.
            self._on_changed()

    def show_users(self, vpath: str) -> None:
        show_users(self, self.session, vpath)


#: Shown in the Source column when the open mod supplies the file.  A word,
#: not just a colour: "this one comes from your mod" is the single most useful
#: thing this panel can say, and a blue path says it only to someone who
#: already knows the convention -- and not at all to a colour-blind reader.
_FROM_MOD = "from the mod"


def _asset_columns(facts: dict) -> List[str]:
    """Asset / Format / Source, from one asset's facts.

    One function for both of the panel's views.  Written after they drifted: the
    tree view filled the last two with empty strings, so a texture the mod had
    just replaced looked exactly like one it had not.
    """
    if not facts.get("resolved", True):
        source = "MISSING"
    elif facts.get("in_mod"):
        # Just the signal in the cell -- "from the mod (mod:user_data.zip)" is
        # wider than the column and clips to "from the mod  (mo", which reads
        # as a bug.  The exact origin is on the tooltip.
        source = _FROM_MOD
    else:
        source = facts.get("source", "")
    return [facts.get("vpath") or "—", facts.get("format") or "", source]


def _paint_asset(item, facts: dict) -> None:
    if not facts.get("resolved", True):
        item.setForeground(3, QBrush(QColor("#c0392b")))
    elif facts.get("in_mod"):
        # Both the path and the source, so the row reads as "yours" at a glance
        # from either column -- and in words, not only in a colour, which says
        # nothing to a reader who cannot see it.
        for col in (1, 3):
            item.setForeground(col, QBrush(QColor("#2980b9")))
        origin = facts.get("source") or "the mod"
        item.setToolTip(
            3,
            f"This file comes from the open mod ({origin}), not the game "
            "installation.\nReset to stock… puts the game's own version back.",
        )
    if item.text(2):
        item.setToolTip(2, _FORMAT_TIP)


#: Said wherever the format is shown, so the rule travels with the fact.
_FORMAT_TIP = (
    "Save a replacement in this same format.\n\n"
    "DXT5 unless it says otherwise (1,973 of the game's 3,053 textures, and "
    "406 of its 422 normal maps).\n"
    "DXT1 has no alpha channel, and a _nrm map keeps the X component there — "
    "so DXT1 destroys half the normal.\n"
    "DXT3 is used by none of the 3,053, and its 4-bit alpha bands.\n"
    "Keep the mipmaps."
)


def _dds_advice(current: Optional[str]) -> str:
    """What to save a replacement texture as, and why.

    Every number here is counted over the 3,053 ``.dds`` in the installation,
    because "use DXT5" on its own is advice the reader has no reason to trust
    and no way to check.  The two wrong answers are the interesting part: both
    were reported from real use, and both are the same defect seen twice.

    A ``_nrm`` texture is **DXT5nm** -- X in the alpha channel, Y in green, Z
    reconstructed (measured in ``specs/``: read as plain RGB the vectors have
    median length 0.345; unswizzled, 0.994).  So the alpha channel is not
    decoration, it is half the normal:

    * **DXT1 has no alpha at all**, so it throws X away entirely.  That is the
      "really weird" look -- the lighting is being computed from a vector whose
      first component is gone.
    * **DXT3's alpha is 4-bit and explicit**, not interpolated, so X survives in
      16 steps.  That is the visible pattern: banding in a channel that is
      supposed to be a smooth gradient.

    ``current`` is what the file being replaced actually is, and it leads,
    because it is a better answer than any rule.
    """
    now = (f"<p>This texture is <b>{current}</b>. Save yours the same way.</p>"
           if current else "")
    return (
        "Pick a <b>.dds</b> file."
        + now +
        "<p>The file is installed <b>byte for byte</b>; this tool does not "
        "convert to DDS, because re-encoding would mean choosing a compressor "
        "and changing mipmap count and quality on your behalf.</p>"
        "<p><b>In Paint.NET's DDS dialog</b> (or nvidia's, or Gimp's):</p>"
        "<ul>"
        "<li><b>DXT5</b> (interpolated alpha) is the safe default — "
        "<b>1,973 of the game's 3,053</b> textures, and <b>406 of its 422</b> "
        "normal maps.</li>"
        "<li><b>DXT1</b> only where the original is DXT1 (683 textures, mostly "
        "<code>_flat</code>). It has <b>no alpha channel</b> — and a "
        "<code>_nrm</code> map keeps the X component <i>in alpha</i>, so DXT1 "
        "destroys half the normal. That is what makes it look wrong rather "
        "than merely worse.</li>"
        "<li><b>DXT3: never.</b> <b>Not one</b> of the 3,053 stock textures "
        "uses it. Its alpha is 4-bit and not interpolated, which is the "
        "banding pattern it puts on a normal map.</li>"
        "<li>If the line above says <b>RGB24/RGB32</b>, the original is "
        "uncompressed — save as B8G8R8A8 rather than any DXT.</li>"
        "<li>Tick <b>Generate Mip Maps</b>: 2,891 of 3,053 stock textures ship "
        "them, and without them the texture crawls and shimmers at distance.</li>"
        "</ul>"
        "<p>Export the original first — it is the best template there is.</p>"
    )


#: What "Export…" offers, by the asset's own extension.  First entry is the
#: default, and for a model that is **glTF**: exporting a `.3do` is nearly
#: always the first half of editing it, and the raw bytes are what Explorer
#: could already give you.  The unchanged-bytes option stays because it is the
#: only lossless one -- a model that has been through glTF and back is
#: byte-identical only while nothing has touched it.
_EXPORT_FILTERS = {
    ".3do": "glTF binary, for editing (*.glb);;Darkstar model, unchanged bytes (*.3do)",
    ".dds": "DirectDraw Surface, unchanged bytes (*.dds);;PNG image (*.png)",
    ".aim": "Ascaron image, unchanged bytes (*.aim);;PNG image (*.png)",
}


def _suffix_of(qt_filter: str) -> str:
    """`glTF binary, for editing (*.glb)` -> `.glb`."""
    if "(*." not in qt_filter:
        return ""
    return "." + qt_filter.split("(*.", 1)[1].split(")", 1)[0].split()[0].strip()


def _glb_import_ok(parent, session, vpath: str, source: str, caption: str) -> bool:
    """Run `SCN001` and the model rules against a `.glb` **before** it is written.

    A model re-exported from a DCC tool routinely comes back with a
    different submesh count -- materials merged or split -- and the scene
    that binds it still has the old number of ``EffectContainer``s.  The
    engine says nothing and binds the wrong material to the wrong surface.
    Checking afterwards would mean the mod is already broken by the time
    anyone finds out.

    Not a refusal.  Deliberately re-cutting a mesh *and* updating the scene
    is a legitimate two-step edit, so this names exactly what will not add
    up and lets the author say yes -- the same shape as Deploy's override.
    """
    if posixpath.splitext(vpath)[1].lower() != ".3do":
        return True
    if os.path.splitext(source)[1].lower() not in (".glb", ".gltf"):
        return True
    try:
        check = session.preflight_glb(vpath, source)
    except (DsoError, OSError) as exc:
        QMessageBox.warning(parent, caption, f"Could not read that model:\n{exc}")
        return False

    # Structural findings first. SCN001 is about the model's agreement with
    # the scenes around it; these are about whether the file itself holds
    # together, and there is no point discussing the first if the second
    # already fails.
    problems = check.get("problems") or []
    if problems:
        rows = "".join(
            f"<tr><td><b>{code}</b></td><td>{sev}</td><td>{msg}</td></tr>"
            for code, sev, msg in problems[:8]
        )
        more = f"<br>…and {len(problems) - 8} more." if len(problems) > 8 else ""
        if QMessageBox.warning(
            parent, caption,
            "<b>This model does not match what the game ships.</b><br><br>"
            "These rules fire on none of the 3,110 stock models, so a "
            "finding here is a real difference introduced somewhere between "
            "the original and this file:"
            "<table cellpadding=4><tr><th align='left'>Code</th>"
            "<th align='left'>Severity</th><th align='left'>What</th></tr>"
            + rows + "</table>" + more +
            "<br><br>Import anyway?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        ) != QMessageBox.StandardButton.Yes:
            return False

    if not check["indexed"]:
        return QMessageBox.warning(
            parent, caption,
            f"This model has <b>{check['submesh_total']}</b> submesh(es) "
            f"across {check['lods']} LOD(s).<br><br>"
            "The asset index is not built, so the scenes that bind it "
            "could not be checked (<b>SCN001</b>). Build it from "
            "Tools ▸ Rebuild asset index to have this verified.<br><br>"
            "Import anyway?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        ) == QMessageBox.StandardButton.Yes

    if not check["conflicts"]:
        return True

    rows = "".join(
        f"<tr><td>{s}</td><td>{n}</td>"
        f"<td align='right'>{got}</td><td align='right'>{want}</td></tr>"
        for s, n, want, got in check["conflicts"][:12]
    )
    more = (f"<br>…and {len(check['conflicts']) - 12} more."
            if len(check["conflicts"]) > 12 else "")
    return QMessageBox.warning(
        parent, caption,
        f"<b>SCN001 will not hold after this import.</b><br><br>"
        f"The model you picked has <b>{check['submesh_total']}</b> "
        f"submesh(es) across {check['lods']} LOD(s), but these scenes bind "
        "it with a different number of EffectContainers:"
        "<table cellpadding=4><tr><th align='left'>Scene</th>"
        "<th align='left'>Node</th><th>Has</th><th>Needs</th></tr>"
        + rows + "</table>" + more +
        "<br><br>The engine does not report this — it binds the wrong "
        "material to the wrong surface, or none at all. Fix the scene's "
        "EffectContainers to match, or re-export with the original "
        "material split.<br><br>Import anyway?",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        QMessageBox.StandardButton.Cancel,
    ) == QMessageBox.StandardButton.Yes


def replace_asset_dialog(parent, session, vpath: str) -> bool:
    """Put a file the user picks into the mod at ``vpath``. True if written.

    Module-level so anything holding an asset can offer it with one wording and
    one set of filters -- the linked-assets panel, the Models tab's submesh
    menu, and the blinker editor, whose texture is an asset like any other even
    though it is reached from a completely different pane.
    """
    ext = posixpath.splitext(vpath)[1].lower()
    caption = f"Replace {posixpath.basename(vpath)}"
    # In the picker's own title bar, where it cannot be dismissed and is on
    # screen at the moment the file is chosen.
    fmt = session.texture_format(vpath)
    if fmt:
        caption += f" — the original is {fmt}"
    filters = _REPLACE_FILTERS.get(ext, f"{ext.lstrip('.').upper()} file (*{ext})")
    filters += ";;All files (*)"

    if ext == ".dds":
        # Said before the picker, not after a failed save: there is no DDS
        # writer here, so a PNG is not a thing that can be accepted -- and
        # "save it as DDS" is not enough of an answer, because the choice of
        # DXT flavour is where a texture actually goes wrong.
        dismissible_notice(
            parent, session, "replace_dds_format", caption, _dds_advice(fmt),
        )

    path, _ = QFileDialog.getOpenFileName(parent, caption, "", filters)
    if not path:
        return False
    if not _glb_import_ok(parent, session, vpath, path, caption):
        return False
    try:
        routed = session.replace_asset(vpath, path)
    except (DsoError, OSError) as exc:
        QMessageBox.warning(parent, caption, str(exc))
        return False
    QMessageBox.information(
        parent, caption,
        "Written into the mod:<pre>"
        + "\n".join(f"{k}   → {v}" for k, v in routed.items())
        + "</pre>",
    )
    return True


def reset_asset_to_stock(parent, session, vpath: str) -> bool:
    """Drop the mod's copy of one asset, after confirming. True if it happened.

    Module-level for the third time and for the third reason: the Project tab's
    file list, the linked-assets panel and the Models tab's submesh table all
    offer this now, and a destructive action described three ways is three
    chances to describe it wrongly.

    The confirmation says **remove**, because that is what happens.  Writing the
    stock bytes back instead would leave a file byte-identical to stock -- dead
    weight the app then reports as having no effect (``PRJ002``) -- so "reset"
    here really is a deletion, and the author is told so beforehand.
    """
    why = session.can_reset_to_stock(vpath)
    if why is not None:
        QMessageBox.information(parent, "Reset to stock", why)
        return False
    if QMessageBox.question(
        parent,
        "Reset to stock",
        f"<b>{vpath}</b><br><br>Remove this file from the mod, so the game "
        "reads the stock version again?<br><br>"
        "The file is deleted from the mod folder or its "
        "<code>user_data.zip</code>. Nothing in the game installation is "
        "touched, and this cannot be undone from here.",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
        QMessageBox.StandardButton.Cancel,
    ) != QMessageBox.StandardButton.Yes:
        return False
    try:
        removed = session.reset_to_stock(vpath)
    except (DsoError, OSError) as exc:
        QMessageBox.warning(parent, "Reset to stock", str(exc))
        return False
    if not removed:
        QMessageBox.information(parent, "Reset to stock", f"{vpath} was not in the mod.")
        return False
    return True


def export_asset(parent, session, vpath: str) -> None:
    """Save one asset to disk, offering every form it can honestly take.

    Module-level for the same reason as :func:`show_users`: the Project tab and
    the linked-assets panel both offer "Export…", and two copies of a file
    dialog are two sets of filters that drift apart.  This one used to have no
    filters at all, which meant a model could only ever come out as ``.3do`` --
    the app had a glTF exporter the CLI could reach and the UI could not.
    """
    stem = posixpath.basename(vpath.replace("\\", "/"))
    ext = posixpath.splitext(stem)[1].lower()
    filters = _EXPORT_FILTERS.get(ext, f"{ext.lstrip('.').upper() or 'File'} (*{ext})")
    filters += ";;All files (*)"

    default = _suffix_of(filters)
    suggested = posixpath.splitext(stem)[0] + default if default else stem
    path, chosen = QFileDialog.getSaveFileName(
        parent, f"Export {stem}", suggested, filters
    )
    if not path:
        return
    # Qt's own dialog appends the filter's suffix on some platforms and not on
    # others; without this a name typed as "wing" saves a `.glb` with no
    # extension, and the extension is what decides the format.
    want = _suffix_of(chosen or "")
    if want and not os.path.splitext(path)[1]:
        path += want
    try:
        session.export_asset(vpath, path)
    except (DsoError, OSError) as exc:
        QMessageBox.warning(parent, "Export", str(exc))


def show_users(parent, session, vpath: str) -> None:
    """The reverse lookup: every asset that references this one.

    Module-level so the Project tab can offer the same action without owning a
    :class:`LinkedAssets` -- one dialog, one wording, wherever it is asked from.
    """
    try:
        users = session.used_by(vpath)
    except DsoError as exc:
        QMessageBox.warning(parent, "What uses this", str(exc))
        return

    if not users:
        QMessageBox.information(
            parent,
            "What uses this",
            f"Nothing in the index references <b>{vpath}</b>.<br><br>"
            "That is weaker than it sounds. The index sees references written "
            "down in data \u2014 scenes, screens, atlas indexes. An asset the "
            "game asks for <b>by name from its own code or compiled scripts</b> "
            "is invisible to it, and a great many are: of the 195 files under "
            "<code>staticImages/</code>, exactly two are named by any screen "
            "\u2014 yet the game plainly uses more. Replacing "
            "<code>staticImages/Starmap.dds</code>, which reports as "
            "unreferenced, visibly changes the star map.<br><br>"
            "So it may be genuinely unused, or used from the game\u2019s code, "
            "or the index may simply not be built yet "
            "(Tools \u25b8 Rebuild asset index).",
        )
        return

    listing = "\n".join(
        f"{u['src']}   ({u['kind']}"
        + (f", slot {u['slot']}" if u.get("slot") is not None else "")
        + (f", {u['node']}" if u.get("node") else "")
        + ")"
        for u in users[:40]
    )
    more = f"\n\u2026 and {len(users) - 40} more" if len(users) > 40 else ""
    QMessageBox.information(
        parent,
        "What uses this",
        f"<b>{len(users)}</b> reference(s) to {vpath}:<pre>{listing}{more}</pre>",
    )


__all__ = ["LinkedAssets", "show_users"]
