#!/usr/bin/env python3
"""
Open screens in the real Interface tab and report what is actually drawn.

    python tools/drive_interface_tab.py --game "<install>" --screen MAINMENU
    python tools/drive_interface_tab.py --game "<install>" --screen HILFE_DIALOG \
        --artwork --then MAINMENU --shots out/

WHY THIS EXISTS
---------------
The same reason `drive_models_tab.py` does: the object model lies about the
picture.  Every defect this tab has had was invisible to assertions --

* a label that stayed behind when its rectangle moved, because the offset lived
  in the rect and the label was a child positioned separately;
* frames reappearing on the next screen with the checkbox still off, because
  new items were built with a solid pen and nothing re-applied the toggle;
* an element outlined orange on a screen whose table showed no selection at
  all, because rebuilding the table emits a selection change and the handler
  had no guard for it.

None of those raise.  `--then` switches screens after loading the first one,
which is exactly the sequence that produced the last two.
"""

from __future__ import annotations

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for sub in ("src", "app"):
    path = os.path.join(ROOT, sub)
    if path not in sys.path:
        sys.path.insert(0, path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.strip().split("\n")[0])
    ap.add_argument("--game", required=True, help="the installation to open")
    ap.add_argument("--mod", help="a mod folder to open as well (use a copy)")
    ap.add_argument("--screen", required=True,
                    help="part of a screen's name, e.g. MAINMENU")
    ap.add_argument("--then", metavar="SCREEN",
                    help="switch to this screen afterwards, and report again. "
                         "Switching is what carries stale state over")
    ap.add_argument("--artwork", action="store_true",
                    help="turn the artwork toggle on before reporting")
    ap.add_argument("--no-frames", action="store_true",
                    help="turn frames off before reporting")
    ap.add_argument("--select", type=int, default=None,
                    help="select this element row before reporting")
    ap.add_argument("--shots", help="directory to write PNG frames into")
    ap.add_argument("--wait", type=int, default=6,
                    help="seconds to allow each screen to load")
    args = ap.parse_args(argv)

    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtWidgets import QApplication

    # Deliberately not offscreen: this is about what is on screen.
    os.environ.pop("QT_QPA_PLATFORM", None)
    app = QApplication([])

    from dso_app.main_window import MainWindow

    win = MainWindow()
    win.session.open_game(args.game)
    if args.mod:
        win.session.open_mod(args.mod)
    win.resize(1350, 950)
    win.show()
    tab = win.interface_tab
    win.tabs.setCurrentWidget(tab)

    def find(fragment):
        for row in win.session.screens():
            if fragment.lower() in row["name"].lower():
                return row["vpath"]
        return None

    def report(tag):
        canvas = tab.canvas
        outlined = sum(1 for i in canvas._rects.values()
                       if i.pen().style() != Qt.PenStyle.NoPen)
        orange = [i for i, it in canvas._rects.items()
                  if it.pen().color().name().lower() == "#f97316"]
        labels = sum(1 for lbl in canvas._labels.values() if lbl.isVisible())
        selected = [i.text(1) for i in tab.elements.selectedItems()]
        # Children: shown, marked, and not offered as editable.
        nested = [it for it in tab._element_rows() if it.parent() is not None]
        editable = [it.text(1) for it in nested
                    if it.flags() & Qt.ItemFlag.ItemIsEditable]
        dashed = sum(1 for it in canvas._rects.values()
                     if it.pen().style() == Qt.PenStyle.DashLine)
        print(f"{tag}")
        print(f"   elements {len(canvas._rects)}, outlined {outlined}, "
              f"labels visible {labels}, artwork {len(canvas._art)}")
        print(f"   canvas selection {canvas._selected} (orange {orange}); "
              f"table selection {selected}")
        print(f"   children: {len(nested)} nested rows, {dashed} dashed on the "
              f"canvas, {len(editable)} editable (want 0) {editable[:3]}")
        print(f"   toggles: frames={tab.chk_frames.isChecked()} "
              f"labels={tab.chk_labels.isChecked()} art={tab.chk_art.isChecked()}")
        if tab.art_note.text():
            print(f"   artwork: {tab.art_note.text()}")
        if args.shots:
            os.makedirs(args.shots, exist_ok=True)
            safe = tag.split()[0].strip(":").lower()
            canvas.grab().save(os.path.join(args.shots, f"{safe}.png"))

    first = find(args.screen)
    if first is None:
        print(f"no screen matching {args.screen!r}")
        return 1

    def open_first():
        tab.reveal(first)
        QTimer.singleShot(args.wait * 1000, configure)

    def configure():
        if args.no_frames:
            tab.chk_frames.setChecked(False)
        if args.select is not None and args.select < tab.elements.topLevelItemCount():
            tab.elements.setCurrentItem(tab.elements.topLevelItem(args.select))
        if args.artwork:
            tab.chk_art.setChecked(True)
        QTimer.singleShot(args.wait * 1000, first_report)

    def first_report():
        report(f"{os.path.basename(first)}:")
        if not args.then:
            app.quit()
            return
        second = find(args.then)
        if second is None:
            print(f"no screen matching {args.then!r}")
            app.quit()
            return
        tab.reveal(second)
        QTimer.singleShot(args.wait * 1000, lambda: second_report(second))

    def second_report(second):
        report(f"\n{os.path.basename(second)}: (after switching)")
        app.quit()

    QTimer.singleShot(1500, open_first)
    QTimer.singleShot((args.wait * 4 + 30) * 1000, app.quit)
    app.exec()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
