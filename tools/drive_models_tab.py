#!/usr/bin/env python3
"""
Open a scene in the real Models tab, grab frames, and report what is drawn.

    python tools/drive_models_tab.py --game "<install>" --scene 3DView/ComSat.xml
    python tools/drive_models_tab.py --game "<install>" --scene 3DView/ComSat.xml \
        --layers geometry,blinker --shots out/

WHY THIS EXISTS
---------------
Because the object model lies about the picture, and only the picture is the
product.  Every viewport defect in this project's history was invisible to
assertions and obvious in a screenshot:

* appending one draw call to ``calls`` made ``Repeater3D`` rebuild every
  delegate, which destroyed the ``QQuick3DGeometry`` each ``Model`` held -- the
  viewport went black while the model still reported "4 of 7 shown";
* a ``baseColor`` binding silently did not apply, and the marker rendered white
  among white markers, with no QML warning anywhere;
* the ``<Material>`` "emissive" row rendered ``ComSat``'s hull as a white
  silhouette with its texture invisible -- measurable only as pixel statistics;
* matching draw calls by object identity broke the moment one was rebuilt, and
  a whole layer quietly stopped being drawn.

None of those raise.  A frame and a histogram catch all four.

**It needs a real platform plugin.**  ``QT_QPA_PLATFORM=offscreen`` cannot
render Qt Quick 3D at all -- ``grabFramebuffer`` returns a null image -- so this
opens a genuine window.  That is the price of seeing what the user sees.
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


def brightness_report(img) -> str:
    """Coarse statistics -- enough to tell "black", "white-out" and "lit"."""
    w, h = img.width(), img.height()
    lit = white = total = 0
    rs = gs = bs = 0
    for y in range(0, h, 3):
        for x in range(0, w, 3):
            c = img.pixelColor(x, y)
            r, g, b = c.red(), c.green(), c.blue()
            total += 1
            if (r + g + b) / 3.0 > 12:
                lit += 1
                rs += r
                gs += g
                bs += b
                if min(r, g, b) > 200:
                    white += 1
    if not lit:
        return f"{w}x{h}: nothing lit -- the viewport is black"
    return (f"{w}x{h}: {lit * 100.0 / total:.0f}% lit, mean rgb "
            f"({rs // lit},{gs // lit},{bs // lit}), {white} near-white samples")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.strip().split("\n")[0])
    ap.add_argument("--game", required=True, help="the installation to open")
    ap.add_argument("--scene", required=True, help="e.g. 3DView/ComSat.xml")
    ap.add_argument("--layers", default="geometry",
                    help="comma-separated layer names to switch on")
    ap.add_argument("--shots", help="directory to write PNG frames into")
    ap.add_argument("--wait", type=int, default=20,
                    help="seconds to allow for the scene to load")
    ap.add_argument("--mod", help="a mod folder to open as well (use a copy)")
    ap.add_argument("--reset", metavar="VPATH",
                    help="after the first frame, 'reset to stock' that path the "
                         "way the Project tab does, and report again. The tab "
                         "is supposed to rebuild; it used to keep showing the "
                         "texture that had just been removed")
    ap.add_argument("--menu", metavar="NAME",
                    help="print the submesh row's context menu (built, not "
                         "shown) for that submesh. `exec` opens a modal loop, "
                         "so this is the only way to see that the menu "
                         "assembles and what it offers")
    ap.add_argument("--isolate", metavar="NAME",
                    help="after the first frame, click that submesh's row in "
                         "the parts table and report what is left on screen. "
                         "This is how the isolate-by-row-index bug was caught: "
                         "the model reported one call shown while the viewport "
                         "was entirely black, because the row belonged to a "
                         "variant the selector had hidden")
    args = ap.parse_args(argv)

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    # Deliberately not offscreen; see the module docstring.
    os.environ.pop("QT_QPA_PLATFORM", None)
    app = QApplication([])

    from dso_app.main_window import MainWindow

    win = MainWindow()
    win.session.open_game(args.game)
    if args.mod:
        win.session.open_mod(args.mod)
    win.resize(1100, 850)
    win.show()
    tab = win.models_tab

    def grab(tag: str):
        img = tab.viewport.quick.grabFramebuffer()
        if img.isNull():
            pixmap = tab.viewport.grab()
            img = pixmap.toImage() if not pixmap.isNull() else img
        if img.isNull():
            print(f"  {tag}: could not grab the viewport")
            return
        print(f"  {tag}: {brightness_report(img)}")
        if args.shots:
            os.makedirs(args.shots, exist_ok=True)
            img.save(os.path.join(args.shots, f"{tag}.png"))

    def show_state(tag: str):
        shown = [i for i in tab.viewport.model.calls if i.shown]
        geometry = tab._geometry
        if geometry is None:
            # Worth printing rather than crashing: "the tab has no geometry but
            # the window still shows a scene" is exactly the stale state that
            # `--reset` exists to catch.
            print(f"{tag}: the tab holds NO geometry (scene={tab._scene})")
            grab(tag)
            return
        print(f"{tag}: {len(geometry.calls)} draw calls, {len(shown)} shown, "
              f"{tab.parts.topLevelItemCount()} row(s) in the parts table")
        for item in shown:
            print(f"   {item.call.layer:10s} {item.call.name:28s} "
                  f"{item.call.triangle_count:6d} tris  opacity={item.opacity:.2f}")
        # The readout the user actually reads, which is a separate claim from
        # what is drawn -- it counted the generated blinker markers as the
        # scene's own submeshes and triangles.
        print("   readout: " + re.sub(r"<[^>]+>", " ", tab.title.text()).strip())
        if tab.viewport.errors:
            print("QML errors:", tab.viewport.errors)
        grab(tag)

    def dump_menu():
        rows = [tab.parts.topLevelItem(i) for i in range(tab.parts.topLevelItemCount())]
        match = next((r for r in rows if args.menu.lower() in r.text(0).lower()), None)
        if match is None:
            print(f"no parts row matching {args.menu!r}; rows are "
                  f"{[r.text(0) for r in rows]}")
            return
        call = match.data(0, tab_role())
        menu = tab.build_parts_menu(call)
        print(f"\ncontext menu for {match.text(0)!r}:")
        if menu is None:
            print("   (empty)")
            return

        def walk(m, depth=1):
            for act in m.actions():
                pad = "   " * depth
                if act.isSeparator():
                    # addSection is a separator that carries a title.
                    print(f"{pad}-- {act.text()}" if act.text() else f"{pad}--")
                    continue
                # Qt defaults an action's tooltip to its own text, so only an
                # explicitly-set one is a reason worth printing.
                reason = act.toolTip() if act.toolTip() != act.text() else ""
                state = "" if act.isEnabled() else "  [disabled: %s]" % (
                    reason or "no reason given")
                print(f"{pad}{act.text()}{state}")
                if act.menu() is not None:
                    walk(act.menu(), depth + 1)

        walk(menu)

    def tab_role():
        from PySide6.QtCore import Qt

        return Qt.ItemDataRole.UserRole

    def report():
        geometry = tab._geometry
        if geometry is None:
            print("scene did not load")
            app.quit()
            return
        tab._layers = [ly.strip() for ly in args.layers.split(",") if ly.strip()]
        tab._apply_visibility()
        print(f"{args.scene}: layers present {geometry.layers()}")
        show_state("frame")
        if args.menu:
            dump_menu()
        if not args.isolate and not args.reset:
            app.quit()

    def isolate():
        rows = [tab.parts.topLevelItem(i) for i in range(tab.parts.topLevelItemCount())]
        names = [r.text(0) for r in rows]
        match = next((r for r in rows if r.text(0) == args.isolate), None)
        if match is None:
            match = next((r for r in rows if args.isolate.lower() in r.text(0).lower()), None)
        if match is None:
            print(f"no parts row matching {args.isolate!r}; rows are {names}")
            app.quit()
            return
        print(f"\nisolating row {tab.parts.indexOfTopLevelItem(match)} "
              f"({match.text(0)!r}) of {len(rows)}")
        tab.parts.setCurrentItem(match)

    def after_isolate():
        show_state("isolated")
        app.quit()

    def reset():
        print(f"\nresetting {args.reset} to stock (as the Project tab does)")
        removed = win.session.reset_to_stock(args.reset)
        print(f"   removed: {removed}")

    def after_reset():
        show_state("after-reset")
        app.quit()

    QTimer.singleShot(200, lambda: tab.open_scene(args.scene, 0))
    QTimer.singleShot(args.wait * 1000, report)
    if args.reset:
        QTimer.singleShot(args.wait * 1000 + 1500, reset)
        QTimer.singleShot(args.wait * 1000 + 1500 + args.wait * 1000, after_reset)
    elif args.isolate:
        QTimer.singleShot(args.wait * 1000 + 1500, isolate)
        QTimer.singleShot(args.wait * 1000 + 4000, after_isolate)
    QTimer.singleShot((args.wait * 3 + 40) * 1000, app.quit)
    app.exec()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
