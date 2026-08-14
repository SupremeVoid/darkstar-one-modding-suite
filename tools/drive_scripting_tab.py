#!/usr/bin/env python3
"""
Open the Scripting tab for real and report what it shows.

    python tools/drive_scripting_tab.py --game "<install>" --mod "<copy>" \\
        --script MissionLib --shots out/

WHY THIS EXISTS
---------------
The same reason the Models and Interface drivers do: the object model is not
the product.  A tab that lists 11 scripts, highlights nothing, and saves a
library into the game folder instead of the mod's payload passes every unit
test in the suite.  The two things worth checking here cannot be asserted from
the session alone --

* that the **destination notice** matches the kind of script open, because
  saving a library anywhere but the mod payload is irreversible;
* that the reference pane and the undocumented-call list actually populate.

Use a **copy** of a mod, never the real one: this can write.
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
    ap.add_argument("--game", required=True)
    ap.add_argument("--mod", help="a mod folder to open as well (use a copy)")
    ap.add_argument("--script", help="part of a script name to open")
    ap.add_argument("--symbol", help="part of an API symbol to select")
    ap.add_argument("--shots", help="directory to write a PNG into")
    ap.add_argument("--wait", type=int, default=4)
    args = ap.parse_args(argv)

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    os.environ.pop("QT_QPA_PLATFORM", None)
    app = QApplication([])

    from dso_app.main_window import MainWindow

    win = MainWindow()
    win.session.open_game(args.game)
    if args.mod:
        win.session.open_mod(args.mod)
    win.resize(1500, 950)
    win.show()
    tab = win.scripting_tab
    win.tabs.setCurrentWidget(tab)

    def report():
        print(f"scripts listed: {tab.files.topLevelItemCount()}")
        kinds = {}
        for i in range(tab.files.topLevelItemCount()):
            kinds[tab.files.topLevelItem(i).text(1)] = \
                kinds.get(tab.files.topLevelItem(i).text(1), 0) + 1
        for kind, count in sorted(kinds.items()):
            print(f"   {count:3d}  {kind}")
        print(f"API namespaces in the tree: {tab.api.topLevelItemCount()}")
        print(f"title: {tab.title.text()[:100]}")

        if args.script:
            for i in range(tab.files.topLevelItemCount()):
                item = tab.files.topLevelItem(i)
                if args.script.lower() in item.text(0).lower():
                    tab.files.setCurrentItem(item)
                    break
            print(f"\nopened: {tab._key}")
            print(f"   {len(tab.editor.toPlainText().splitlines())} lines, "
                  f"save enabled={tab.btn_save.isEnabled()}")
            print(f"   destination: {_plain(tab.where.text())}")
            print(f"   not in the reference: {tab.problems.topLevelItemCount()} "
                  f"{[tab.problems.topLevelItem(i).text(1) for i in range(min(3, tab.problems.topLevelItemCount()))]}")

        if args.symbol:
            found = None
            for i in range(tab.api.topLevelItemCount()):
                parent = tab.api.topLevelItem(i)
                for j in range(parent.childCount()):
                    if args.symbol.lower() in parent.child(j).text(0).lower():
                        found = parent.child(j)
                        break
                if found:
                    break
            if found:
                tab.api.setCurrentItem(found)
                text = tab.doc.toPlainText()
                print(f"\nreference pane for {found.text(0)}: {len(text)} chars")
                print("   " + "\n   ".join(text.splitlines()[:4]))
            else:
                print(f"\nno API symbol matching {args.symbol!r}")

        if args.shots:
            os.makedirs(args.shots, exist_ok=True)
            win.grab().save(os.path.join(args.shots, "scripting.png"))
            print(f"\nwrote {os.path.join(args.shots, 'scripting.png')}")
        app.quit()

    QTimer.singleShot(args.wait * 1000, report)
    QTimer.singleShot((args.wait + 40) * 1000, app.quit)
    app.exec()
    return 0


def _plain(html: str) -> str:
    import re
    return re.sub(r"<[^>]+>", "", html).strip()


if __name__ == "__main__":
    raise SystemExit(main())
