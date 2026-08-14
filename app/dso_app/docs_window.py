"""
The documentation window: the specs, readable from inside the app.

**Non-modal and never blocking.** It is a ``QWidget`` window rather than a
``QDialog.exec()`` for one reason that matters: documentation is what you read
*while* doing the thing it describes. A modal help window that has to be closed
before you can touch the mod again is a worse place to put the rules than the
repository they came from.

The window owns no knowledge. Which documents exist, what they are called, how
a link between them resolves and what a search finds all live in
:mod:`docs_library`, which is Qt-free and tested. This file lays them out.

Markdown is rendered by ``QTextBrowser.setMarkdown`` -- Qt's own, no third
dependency. It handles headings, tables, lists, code and links, which is what
the specs are made of.
"""

from __future__ import annotations

from typing import List, Optional

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import docs_library, theme


class DocsWindow(QWidget):
    """A reader for the markdown this build ships."""

    def __init__(self, parent=None) -> None:
        # No parent on purpose: a parented QWidget with the Window flag still
        # stays in front of its parent, and this is meant to sit beside the
        # main window rather than over it.
        super().__init__(None)
        self.setWindowTitle("Darkstar One Modding Suite — Documentation")
        self.resize(1080, 760)
        self._history: List[str] = []
        self._at = -1

        layout = QVBoxLayout(self)

        top = QHBoxLayout()
        self.btn_back = QPushButton("◀")
        self.btn_back.setFixedWidth(36)
        self.btn_back.setToolTip("Back")
        self.btn_back.clicked.connect(self.back)
        self.btn_forward = QPushButton("▶")
        self.btn_forward.setFixedWidth(36)
        self.btn_forward.setToolTip("Forward")
        self.btn_forward.clicked.connect(self.forward)
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText(
            "search every document — try items.ini, PRJ005, Ready…")
        self.search_edit.returnPressed.connect(self.run_search)
        btn_search = QPushButton("Search")
        btn_search.clicked.connect(self.run_search)
        top.addWidget(self.btn_back)
        top.addWidget(self.btn_forward)
        top.addWidget(self.search_edit, 1)
        top.addWidget(btn_search)
        layout.addLayout(top)

        split = QSplitter(Qt.Orientation.Horizontal)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setMinimumWidth(240)
        self.tree.itemSelectionChanged.connect(self._chosen)
        split.addWidget(self.tree)

        self.view = QTextBrowser()
        self.view.setOpenLinks(False)          # links are routed, see _link
        self.view.anchorClicked.connect(self._link)
        split.addWidget(self.view)
        split.setStretchFactor(1, 1)
        layout.addWidget(split, 1)

        self.status = QLabel("")
        self.status.setStyleSheet(f"color: {theme.GREY};")
        layout.addWidget(self.status)

        self._fill()

    # -- the list ------------------------------------------------------------

    def _fill(self) -> None:
        self.tree.clear()
        sections = {}
        found = docs_library.documents()
        for entry in found:
            parent = sections.get(entry["section"])
            if parent is None:
                parent = QTreeWidgetItem([entry["section"]])
                parent.setFlags(Qt.ItemFlag.ItemIsEnabled)
                self.tree.addTopLevelItem(parent)
                sections[entry["section"]] = parent
            item = QTreeWidgetItem([entry["title"]])
            item.setData(0, Qt.ItemDataRole.UserRole, entry["id"])
            item.setToolTip(0, entry["id"])
            parent.addChild(item)
        self.tree.expandAll()
        if not found:
            # Say it rather than showing an empty window: a build that shipped
            # without its documentation is a packaging fault, and a blank pane
            # reads as "still loading".
            self.view.setMarkdown(
                "## No documentation found\n\nThis build did not ship the "
                "`specs/` folder. That is a packaging fault rather than "
                "something you can fix here.")
            self.status.setText("nothing to show")
            return
        self.show_document(found[0]["id"])

    def _chosen(self) -> None:
        items = self.tree.selectedItems()
        if not items:
            return
        document_id = items[0].data(0, Qt.ItemDataRole.UserRole)
        if document_id and document_id != self.current():
            self.show_document(document_id)

    # -- showing one --------------------------------------------------------

    def current(self) -> Optional[str]:
        return self._history[self._at] if 0 <= self._at < len(self._history) else None

    def show_document(self, document_id: str, *, record: bool = True) -> None:
        try:
            text = docs_library.read(document_id)
        except OSError as exc:
            self.view.setMarkdown(f"## Could not open\n\n`{document_id}`\n\n{exc}")
            return
        self.view.setMarkdown(text)
        self.view.verticalScrollBar().setValue(0)
        if record:
            del self._history[self._at + 1:]
            self._history.append(document_id)
            self._at = len(self._history) - 1
        self._sync(document_id)

    def _sync(self, document_id: str) -> None:
        self.btn_back.setEnabled(self._at > 0)
        self.btn_forward.setEnabled(self._at < len(self._history) - 1)
        headings = docs_library.outline(docs_library.read(document_id))
        self.status.setText(f"{document_id} — {len(headings)} section(s)")
        for i in range(self.tree.topLevelItemCount()):
            parent = self.tree.topLevelItem(i)
            for j in range(parent.childCount()):
                child = parent.child(j)
                if child.data(0, Qt.ItemDataRole.UserRole) == document_id:
                    self.tree.blockSignals(True)
                    self.tree.setCurrentItem(child)
                    self.tree.blockSignals(False)
                    return

    # -- navigation ----------------------------------------------------------

    def back(self) -> None:
        if self._at > 0:
            self._at -= 1
            self.show_document(self._history[self._at], record=False)

    def forward(self) -> None:
        if self._at < len(self._history) - 1:
            self._at += 1
            self.show_document(self._history[self._at], record=False)

    def _link(self, url: QUrl) -> None:
        """Follow a link between documents; hand anything else to the browser.

        ``setOpenLinks(False)`` is what makes this possible: left to itself
        ``QTextBrowser`` would try to load ``scene.md`` as a file relative to
        nothing and show an empty page.
        """
        href = url.toString()
        if url.scheme() in ("http", "https"):
            QDesktopServices.openUrl(url)
            return
        if href.startswith("#"):
            self.view.scrollToAnchor(href[1:])
            return
        target = docs_library.resolve_link(self.current() or "", href)
        if target:
            self.show_document(target)
        else:
            self.status.setText(f"{href} is not one of the shipped documents")

    # -- search --------------------------------------------------------------

    def run_search(self) -> None:
        needle = self.search_edit.text().strip()
        if not needle:
            self._fill()
            return
        hits = docs_library.search(needle)
        if not hits:
            self.status.setText(f"no document mentions {needle!r}")
            return
        lines = [f"## {len(hits)} document(s) mention `{needle}`", ""]
        for hit in hits:
            lines.append(f"### [{hit['title']}]({hit['id']})")
            lines.append(f"{hit['count']} line(s). First: `{hit['first']}`")
            lines.append("")
        self.view.setMarkdown("\n".join(lines))
        self.status.setText(f"{len(hits)} document(s) for {needle!r}")


def open_docs(parent=None) -> DocsWindow:
    """Show the documentation window, reusing the one already open.

    A second window would split the reader's place in two and double the
    memory for no gain, so the instance is kept on the function.
    """
    existing = getattr(open_docs, "_window", None)
    if existing is None:
        existing = DocsWindow(parent)
        open_docs._window = existing
    existing.show()
    existing.raise_()
    existing.activateWindow()
    return existing


__all__ = ["DocsWindow", "open_docs"]
