#!/usr/bin/env python3
"""
Open the Problems tab for real and apply every mechanical fix.

    python tools/drive_problems_tab.py --game "<install>"

WHY THIS EXISTS
---------------
The same reason the Audio, Models, Interface and Scripting drivers do: the
object model is not the product.  ``Session.apply_fix`` is unit-tested, but a
button that never appears, appears for the wrong row, or leaves a stale report
on screen is not something a unit test can see.

It builds its **own deliberately broken mod** in a temporary folder and never
touches a mod of yours.  Four things are wrong with it, one per offered fix:

* no ``inifiles/items.ini``            -- ``PRJ004``
* a loose ``3DView/`` file             -- ``PRJ005``
* a script inside ``user_data.zip``    -- ``PRJ007``
* a sound declared with wrong numbers  -- ``SND004``

and one thing that is wrong on purpose and must **not** be offered a fix: an
audio file nothing declares (``SND002``), which needs a decision no tool can
make for you.
"""

from __future__ import annotations

import argparse
import os
import shutil
import struct
import sys
import tempfile
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for sub in ("src", "app"):
    path = os.path.join(ROOT, sub)
    if path not in sys.path:
        sys.path.insert(0, path)

SCENE = (b'<?xml version="1.0"?>\r\n<WalhallaScene Version="2.00">\r\n'
         b'\t<Object Type=".?AVCWorldRoot@@" />\r\n</WalhallaScene>\r\n')


def _wav(path, *, rate=22050, channels=1, frames=22050):
    data = b"\0" * (frames * channels * 2)
    chunks = (struct.pack("<4sIHHIIHH", b"fmt ", 16, 1, channels, rate,
                          rate * channels * 2, channels * 2, 16)
              + struct.pack("<4sI", b"data", len(data)) + data)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as handle:
        handle.write(b"RIFF" + struct.pack("<I", 4 + len(chunks))
                     + b"WAVE" + chunks)


def broken_mod(root: str) -> str:
    """A mod with exactly one instance of each repairable mistake."""
    mod = os.path.join(root, "Customization", "BrokenOnPurpose")
    os.makedirs(os.path.join(mod, "3DView"), exist_ok=True)
    with open(os.path.join(mod, "darkstarmod.ini"), "wb") as handle:
        handle.write(b"[darkstarmod]\r\nmod_name = Broken On Purpose\r\n"
                     b"mod_desc = built by drive_problems_tab.py\r\n")

    # PRJ005: loose 3DView/ is never read by the engine.
    with open(os.path.join(mod, "3DView", "Dead.xml"), "wb") as handle:
        handle.write(SCENE)

    # PRJ007: scripts inside the archive are never loaded.
    with zipfile.ZipFile(os.path.join(mod, "user_data.zip"), "w") as zf:
        zf.writestr("scripts/mine.lua", b"-- never runs from in here\r\n")

    # SND004: the declaration disagrees with the file it names.
    _wav(os.path.join(mod, "sound", "sfx(2d)", "grp_USER", "Beep.wav"))
    # SND002: shipped, declared by nothing -- deliberately *not* fixable.
    _wav(os.path.join(mod, "sound", "sfx(2d)", "grp_USER", "Orphan.wav"))
    with open(os.path.join(mod, "user_sounds.xml"), "wb") as handle:
        handle.write(
            b'<?xml version="1.0" encoding="ISO-8859-1"?>\r\n<ASE_Database>\r\n'
            b'  <Group Name="USER">\r\n'
            b'    <Sound2D Name="Beep" '
            b'Resrc="%MOD%sound\\sfx(2d)\\grp_USER\\Beep.wav" '
            b'Channels="2" Duration=":999" Freq="44100" />\r\n'
            b"  </Group>\r\n</ASE_Database>\r\n")

    # PRJ004 is the absence of inifiles/items.ini, so nothing is written.
    return mod


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.strip().split("\n")[0])
    ap.add_argument("--game", help="a game folder, so the baseline rules run")
    ap.add_argument("--keep", action="store_true",
                    help="leave the temporary mod on disk to look at")
    ap.add_argument("--show", action="store_true",
                    help="show the window instead of running offscreen")
    args = ap.parse_args(argv)

    if not args.show:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from dso_app.main_window import MainWindow

    work = tempfile.mkdtemp(prefix="dso_problems_")
    mod = broken_mod(work)
    print(f"built a deliberately broken mod at {mod}\n")

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    if args.game:
        window.session.open_game(args.game)
    window.session.open_mod(mod)
    tab = window.problems_tab
    # Shown, and the tab fronted, even offscreen.  ``isVisible()`` is False for
    # every child of a hidden window *and* for every page of a QTabWidget that
    # is not the current one, so without both of these the button checks below
    # would report "hidden" for a button that is working perfectly.
    window.show()
    window.tabs.setCurrentWidget(tab)

    def validate_now():
        window.session.validate()
        tab.refresh()
        for _ in range(3):
            app.processEvents()

    def codes():
        found = {}
        for i in range(tab.tree.topLevelItemCount()):
            item = tab.tree.topLevelItem(i)
            code = item.data(0, Qt.ItemDataRole.UserRole)
            if code:
                found[code] = item
        return found

    validate_now()
    listed = codes()
    print(f"{len(listed)} rule(s) reported: {', '.join(sorted(listed))}")

    for code in ("PRJ004", "PRJ005", "PRJ007", "SND004", "SND002", "PRJ006"):
        offer = window.session.fix_for(code)
        seen = "reported" if code in listed else "not reported"
        print(f"   {code}: {seen}, "
              + (f"fix offered -- {offer[0]!r}" if offer else "no fix offered"))

    failed = 0
    for code in ("PRJ004", "PRJ005", "PRJ007", "SND004"):
        listed = codes()
        if code not in listed:
            print(f"\n{code}: not reported, nothing to fix")
            continue
        tab.tree.setCurrentItem(listed[code])
        app.processEvents()
        print(f"\n{code}: selected")
        print(f"   button visible: {tab.btn_fix.isVisible()}  "
              f"text: {tab.btn_fix.text()!r}")
        # The dialog is what the button opens; the session call is what it
        # does.  Driving the dialog would need a human, so the fix is applied
        # directly and the button state around it is what is checked here.
        print("   " + window.session.apply_fix(code))
        tab.refresh()
        app.processEvents()
        print(f"   report dropped, button hidden: {not tab.btn_fix.isVisible()}")
        validate_now()
        if code in codes():
            print(f"   STILL REPORTED after its own fix -- {code}")
            failed += 1
        else:
            print(f"   gone from a fresh validation: {code}")

    left = sorted(codes())
    print(f"\nwhat is left: {', '.join(left) or 'nothing'}")
    if "SND002" in left:
        print("   SND002 is still there on purpose: an undeclared file needs a "
              "decision, so no fix is offered for it")

    if args.show:
        app.exec()

    if args.keep:
        print(f"\nleft on disk: {work}")
    else:
        # Windows will not delete a folder whose zip is still open, and
        # ``files()`` keeps a handle for lazy reads.
        if window.session.mod:
            window.session.mod.close()
        shutil.rmtree(work, ignore_errors=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
