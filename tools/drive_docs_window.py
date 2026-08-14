#!/usr/bin/env python3
"""
Open the documentation window and read every page in it.

    python tools/drive_docs_window.py

WHY THIS EXISTS
---------------
The catalogue is unit-tested; the *rendering* is not, and cannot be — the window
layer has no unit tests by design. What only a running window can show is
whether each document actually renders as something a person can read: Qt's
markdown parser is not the one that produced these files, and a spec that comes
out as one undifferentiated wall of text has failed even though every test
passed.

So this opens the real window, walks every document, and reports what came
through — headings found, characters rendered, tables and code blocks seen —
plus the two behaviours that are easy to get silently wrong: following a link
between documents, and the back button.

It reads only. Nothing here writes to a mod or to the installation.
"""

from __future__ import annotations

import argparse
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for sub in ("src", "app"):
    path = os.path.join(ROOT, sub)
    if path not in sys.path:
        sys.path.insert(0, path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.strip().split("\n")[0])
    ap.add_argument("--show", action="store_true",
                    help="leave the window open to look at")
    args = ap.parse_args(argv)

    if not args.show:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtWidgets import QApplication

    from dso_app import docs_library
    from dso_app.docs_window import DocsWindow, open_docs

    app = QApplication.instance() or QApplication([])
    window = DocsWindow()
    window.show()
    app.processEvents()

    failed = 0

    def check(label, got, want):
        nonlocal failed
        ok = got == want
        failed += 0 if ok else 1
        print(f"   {'ok ' if ok else 'FAIL'}  {label}: {got!r}")

    entries = docs_library.documents()
    print(f"{len(entries)} document(s) in the catalogue\n")

    print(f"{'document':34} {'headings':>8} {'rendered':>9}  first heading")
    for entry in entries:
        window.show_document(entry["id"])
        app.processEvents()
        raw = docs_library.read(entry["id"])
        heads = docs_library.outline(raw)
        rendered = window.view.toPlainText()
        # A document that renders to far less than it contains has lost
        # something — usually a table Qt did not recognise.
        ratio = len(rendered) / max(1, len(raw))
        flag = "" if ratio > 0.45 else f"   <-- only {ratio:.0%} of the source"
        print(f"   {entry['title']:31} {len(heads):>8} "
              f"{len(rendered):>9,}{flag}")
        if not rendered.strip():
            print(f"      FAIL renders empty: {entry['id']}")
            failed += 1
        # Compare on words, not on the raw heading: `outline` reads the
        # markdown source, where a heading may carry backticks and emphasis
        # that the renderer quite correctly strips.  Matching the literal
        # string would report a rendering failure every time a spec titles
        # itself with an inline code span, which most of them do.
        if heads:
            words = [w for w in re.findall(r"\w{4,}", heads[0]["text"])]
            if words and not any(w in rendered for w in words):
                print(f"      FAIL first heading missing from the render: "
                      f"{heads[0]['text']!r}")
                failed += 1

    print("\nnavigation:")
    window.show_document("specs/modding_guide.md")
    window.show_document("specs/scene.md")
    check("current after two", window.current(), "specs/scene.md")
    window.back()
    check("back", window.current(), "specs/modding_guide.md")
    window.forward()
    check("forward", window.current(), "specs/scene.md")

    print("\nfollowing a link between documents:")
    from PySide6.QtCore import QUrl

    window.show_document("specs/modding_guide.md")
    window._link(QUrl("scene.md"))
    check("a sibling link navigates", window.current(), "specs/scene.md")
    before = window.current()
    window._link(QUrl("STATE.md"))
    check("a link to something unshipped does not navigate",
          window.current(), before)
    print(f"   and says so: {window.status.text()!r}")

    print("\nsearch:")
    window.search_edit.setText("items.ini")
    window.run_search()
    app.processEvents()
    rendered = window.view.toPlainText()
    check("the results page mentions the term", "items.ini" in rendered, True)
    print(f"   {window.status.text()}")
    window.search_edit.setText("zzz-nothing-here")
    window.run_search()
    print(f"   {window.status.text()}")

    print("\nreuse:")
    a = open_docs()
    b = open_docs()
    check("open_docs returns the same window twice", a is b, True)

    if args.show:
        app.exec()
    print(f"\n{'FAILED' if failed else 'all checks passed'}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
