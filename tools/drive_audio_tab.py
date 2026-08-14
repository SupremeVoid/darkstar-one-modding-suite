#!/usr/bin/env python3
"""
Open the Audio tab for real and exercise the transport.

    python tools/drive_audio_tab.py --game "<install>" [--mod "<copy>"] \\
        --sound Mainmenu/MUSIC_Mainmenu

WHY THIS EXISTS
---------------
The same reason the Models, Interface and Scripting drivers do: the object
model is not the product. A transport that reports the right numbers to a unit
test can still show a frozen bar, resume from the wrong place, or keep counting
through a track nobody is looking at any more.

It is a script rather than a test because the suite never builds a
``QApplication`` -- importing the media stack for that once left threads behind
that turned an eleven-second run into a hang. The arithmetic is unit-tested in
``tests/test_transport_view.py``; what needs a running app is checked here.

Playback is real: run it with the volume up and you will hear the file.
Use a **copy** of a mod, never the real one -- ``--mod`` can write.
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
    ap.add_argument("--sound", default="Mainmenu/MUSIC_Mainmenu",
                    help="group/name to select, as the tab shows it")
    ap.add_argument("--seconds", type=float, default=2.0,
                    help="how long to let it play at each step")
    ap.add_argument("--silent", action="store_true",
                    help="run offscreen with the volume down")
    args = ap.parse_args(argv)

    if args.silent:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtCore import Qt, QEventLoop, QTimer
    from PySide6.QtWidgets import QApplication

    from dso_app.main_window import MainWindow
    from dso_app.transport_view import clock

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.session.open_game(args.game)
    if args.mod:
        window.session.open_mod(args.mod)
    for _ in range(4):
        app.processEvents()
    tab = window.audio_tab
    if args.silent:
        tab.volume.setValue(0)

    def settle(seconds):
        loop = QEventLoop()
        QTimer.singleShot(int(seconds * 1000), loop.quit)
        loop.exec()

    def find(reference):
        hit = []

        def walk(item):
            row = item.data(0, Qt.ItemDataRole.UserRole)
            if row is not None and row["reference"] == reference:
                hit.append(item)
            for i in range(item.childCount()):
                walk(item.child(i))

        for i in range(tab.tree.topLevelItemCount()):
            walk(tab.tree.topLevelItem(i))
        return hit[0] if hit else None

    print(f"{len(tab._rows)} sounds listed")
    item = find(args.sound)
    if item is None:
        print(f"no sound called {args.sound!r}")
        return 1
    tab.tree.setCurrentItem(item)
    row = tab.selected()
    print(f"\nselected {row['reference']}  ({row['kind']}, {row['where']})")
    print(f"   declared length shown before playing: {tab.total.text()}")
    print(f"   play enabled: {tab.btn_play.isEnabled()}  "
          f"seek enabled: {tab.seek.isEnabled()}")

    tab.toggle_play()
    settle(args.seconds)
    print(f"\nplaying: state={tab.player.playbackState().name}")
    print(f"   {tab.elapsed.text()} / {tab.total.text()}   "
          f"(decoder says {clock(tab.player.duration())})")
    advanced = tab.seek.value()
    if advanced == 0:
        print("   NOTE: position still 0 -- with no audio device the clock may "
              "not advance; run without --silent to hear it")

    tab.toggle_play()
    settle(0.4)
    at_pause = tab.seek.value()
    settle(0.6)
    print(f"\npaused at {clock(at_pause)}: "
          f"{'held' if abs(tab.seek.value() - at_pause) < 100 else 'STILL MOVING'}")

    tab.toggle_play()
    settle(args.seconds)
    print(f"resumed to {clock(tab.seek.value())}: "
          f"{'from the pause' if tab.seek.value() >= at_pause else 'RESTARTED'}")

    if tab.seek.maximum() > 30000:
        tab._seek_started()
        tab.seek.setValue(30000)
        tab._seek_preview(30000)
        shown = tab.elapsed.text()
        tab._seek_finished()
        settle(0.8)
        print(f"\nscrubbed to {shown}; player is at {clock(tab.player.position())}")

    other = next((r for r in tab._rows if r["reference"] != args.sound
                  and r["exists"]), None)
    if other:
        tab.tree.setCurrentItem(find(other["reference"]))
        print(f"\nselected {other['reference']} instead")
        print(f"   playback: {tab.player.playbackState().name} (want Stopped)")
        print(f"   transport reset to 0:00 / {tab.total.text()}")

    tab.stop()
    print("\ndone")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
