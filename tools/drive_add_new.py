#!/usr/bin/env python3
"""
Add a file the game has never had, bind it, and take it out again.

    python tools/drive_add_new.py --game "<install>"

WHY THIS EXISTS
---------------
The three halves of "add new" only mean something together, and each is
plausible on its own while the chain is broken:

* a file can be written at a new path -- the library always could, and nothing
  in the app ever asked;
* a texture at a new path does **nothing** until a scene names it, and a scene
  names its textures *relative to itself*, so writing a virtual path in
  produces a reference that resolves to nothing;
* and a file that can be added and not removed leaves the app apologising
  ("delete it outside the app if you want it gone").

So this drives the whole chain and checks the one thing a unit test of any
single part cannot: that after the rebind, the scene resolves to the file that
was added, through the same VFS the engine's layering models.

It builds its **own mod** in a temporary folder and never touches a mod of
yours.  A game folder is required -- there is nothing to override without one.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for sub in ("src", "app"):
    path = os.path.join(ROOT, sub)
    if path not in sys.path:
        sys.path.insert(0, path)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.strip().split("\n")[0])
    ap.add_argument("--game", required=True)
    ap.add_argument("--scene", default="3DView/PlayerShip.xml",
                    help="the stock scene to override and rebind")
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--show", action="store_true")
    args = ap.parse_args(argv)

    if not args.show:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from dsotools.formats import scene as scenefmt
    from dsotools.project import Mod, check_mod_path

    from dso_app.main_window import MainWindow

    work = tempfile.mkdtemp(prefix="dso_addnew_")
    mod = Mod.create(os.path.join(work, "Customization", "AddNew"), name="Add New")

    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    window.show()
    window.session.open_game(args.game)
    window.session.open_mod(mod.root)
    session = window.session
    tab = window.project_tab
    window.tabs.setCurrentWidget(tab)
    app.processEvents()
    print(f"mod at {mod.root}\n")

    failed = 0

    def check(label, got, want):
        nonlocal failed
        ok = got == want
        failed += 0 if ok else 1
        print(f"   {'ok ' if ok else 'FAIL'}  {label}: {got!r}")

    # -- the path guard ------------------------------------------------------
    print("what the path guard refuses:")
    for candidate in ("mystuff/x.dds", "loose.dds", "../escape.dds",
                      "save/game.sav", "3DView/textures/fine.dds"):
        why = check_mod_path(candidate)
        print(f"   {candidate:32} {'allowed' if why is None else why[:64]}")

    print("\nwhere each kind is proposed to go:")
    for kind, sample in (("texture", "hull.dds"),
                         ("script", "helper.lua"), ("sound", "beep.wav")):
        print(f"   {kind:8} {sample:12} -> "
              f"{session.suggest_add_path(kind, sample)}")

    # -- 1. override a stock scene so there is something of ours to edit -----
    raw = session.stock.read(args.scene)
    staged = os.path.join(work, os.path.basename(args.scene))
    with open(staged, "wb") as handle:
        handle.write(raw)
    session.replace_asset(args.scene, staged)
    print(f"\noverrode {args.scene}")

    # -- 2. add a texture at a path the game has never had -------------------
    source = None
    for row in session.texture_assets([".dds"]):
        source = row["vpath"]
        break
    staged_tex = os.path.join(work, "mymod_hull.dds")
    with open(staged_tex, "wb") as handle:
        handle.write(session.read_asset(source))
    new_vpath = "3DView/textures/mymod_hull.dds"

    print(f"\nadding {new_vpath}")
    check("check_new_path", session.check_new_path(new_vpath), None)
    print(f"   note: {session.new_path_note(new_vpath)}")
    routed = session.add_asset(new_vpath, staged_tex)
    check("routed to", routed.get(new_vpath), "zip")
    check("adding it twice is refused",
          session.check_new_path(new_vpath) is not None, True)

    # -- 3. bind a scene slot to it ------------------------------------------
    detail = session.scene_detail(args.scene)
    mesh = next(m for m in detail["meshes"] if m["slots"] and m["slots"][0]["textures"])
    slot = mesh["slots"][0]
    names = slot.get("slot_names") or []
    print(f"\nbinding {mesh['path']}  "
          f"{names[0] if names else 'slot 0'} = {slot['textures'][0]}")
    session.set_effect(args.scene, mesh["path"], slot["index"],
                       textures={0: new_vpath})

    with session.open_vfs() as vfs:
        sc = scenefmt.parse(vfs.read(args.scene), path=args.scene)
        target = next(m for m in sc.meshes() if m.path() == mesh["path"])
        reference = target.effects[slot["index"]].textures[0]
        entry = vfs.resolve_reference(reference, scene_path=args.scene)
        print(f"   the scene now says {reference!r}")
        check("it is a scene-relative reference, not a vpath",
              reference.lower() != new_vpath.lower(), True)
        check("and it resolves to the added file",
              entry.vpath.lower() if entry else None, new_vpath.lower())

    # -- 4. what cannot be bound --------------------------------------------
    #
    # A texture that exists and is perfectly readable, but sits outside
    # 3DView/.  It could only be named through the bare-path fallback, which
    # no stock reference uses -- so binding it must be refused rather than
    # written into the scene.
    outside = next(
        (r["vpath"] for r in session.texture_assets([".dds"])
         if not r["vpath"].lower().startswith("3dview/")), None)
    if outside is None:
        print("\n   (no texture outside 3DView/ to test the refusal with)")
    else:
        try:
            session.set_effect(args.scene, mesh["path"], slot["index"],
                               textures={0: outside})
            print(f"   FAIL  binding {outside} was allowed")
            failed += 1
        except Exception as exc:                          # noqa: BLE001
            print(f"\n   ok    {outside} exists, and still cannot be bound:\n"
                  f"         {exc}")

    # -- 5. and what the user actually sees ----------------------------------
    #
    # The session answers above are all correct with a UI that never shows
    # them.  These are the widgets.
    from dso_app.effect_editor import EffectEditor
    from dso_app.texture_picker import TexturePickerDialog

    print("\nthe widgets:")

    picker = TexturePickerDialog(window, session)
    everything = len(session.texture_assets([".dds"]))
    listed = picker.list.count()
    print(f"   picker lists {listed} of {everything} .dds")
    check("nothing outside 3DView/ is offered",
          all(picker.list.item(i).data(Qt.ItemDataRole.UserRole)
              .lower().startswith("3dview/") for i in range(listed)), True)
    picker.mod_only.setChecked(True)
    app.processEvents()
    print(f"   'only this mod' narrows it to {picker.list.count()}")
    picker.deleteLater()

    captured = {}
    editor = EffectEditor(
        lambda *payload: captured.update(zip(
            ("shader", "parameters", "material", "textures"), payload)),
        on_pick_texture=lambda slot: "3DView/textures/mymod_hull.dds",
    )
    editor.load(slot)
    rows = editor.textures_form.rowCount()
    print(f"   effect editor shows {rows} texture row(s) for this slot")
    check("one row per bound texture", rows, len(slot["textures"]))
    names = slot.get("slot_names") or []
    if names:
        label = editor.textures_form.itemAt(
            0, editor.textures_form.ItemRole.LabelRole).widget().text()
        print(f"   first row is labelled {label!r} (from the .bsd9)")
        check("labelled by the shader, not by index", label, f"{names[0]}:")
    editor._pick_texture(0)
    editor._apply()
    check("the rebind reaches Apply",
          captured.get("textures"), {0: "3DView/textures/mymod_hull.dds"})

    # "Undo my changes" has to take a pending rebind with it, or the next
    # Apply writes a binding the author thought they had thrown away.
    captured.clear()
    editor._pick_texture(0)
    editor._revert()
    editor._apply()
    check("Undo my changes drops a pending rebind", captured, {})
    editor.deleteLater()

    # -- 6. the other kinds --------------------------------------------------
    #
    # A texture is the kind with the most moving parts, but it is not the only
    # one an author adds.  A model has the same shape -- inert until a scene
    # names it -- and a script is the one kind that is *not*, because the
    # loader globs and runs every scripts/*.lua.
    # A model is deliberately not addable: nothing a mod writes reaches a new
    # scene name (specs/scene.md 4.3.4).  Repointing a mesh at a model that
    # already exists is the edit that does work, so that is what is checked.
    print("\nbinding a mesh to a different existing model:")
    check("there is no model kind to add", "model" in session.ADD_KINDS, False)
    bindable = session.bindable_models(args.scene)
    current = (mesh.get("model_vpath") or "").lower()
    other = next((r["vpath"] for r in bindable if r["vpath"].lower() != current),
                 None)
    print(f"   {len(bindable)} model(s) bindable from this scene")
    if other:
        fit = session.mesh_model_fit(args.scene, mesh["path"], other)
        print(f"   SCN001 before the bind: {fit['submesh_total']} submesh(es) "
              f"vs {fit['effects']} EffectContainer(s) - fits={fit['fits']}")
        session.set_mesh_model(args.scene, mesh["path"], other)
        with session.open_vfs() as vfs:
            sc = scenefmt.parse(vfs.read(args.scene), path=args.scene)
            target = next(m for m in sc.meshes() if m.path() == mesh["path"])
            back = vfs.resolve_reference(target.model, scene_path=args.scene)
            check("bound to the chosen model",
                  back.vpath.lower() if back else None, other.lower())

    # And the submesh list, which is what makes a mismatched bind fixable.
    print("\nediting the submesh list:")
    node = mesh["path"]
    was = len(session.scene_detail(args.scene)["meshes"][0]["slots"])
    session.add_submesh(args.scene, node)
    grew = len(session.scene_detail(args.scene)["meshes"][0]["slots"])
    check("add_submesh", grew, was + 1)
    session.remove_submesh(args.scene, node, grew - 1)
    check("remove_submesh",
          len(session.scene_detail(args.scene)["meshes"][0]["slots"]), was)

    print("\nadding a script:")
    written = session.new_script("my_helper")
    check("written into scripts/", "scripts" in written.replace("\\", "/"), True)
    check("it is listed as a script the mod ships",
          any(r["name"] == "my_helper.lua" for r in session.scripts()), True)
    check("the same name twice is refused",
          session.check_new_path("scripts/my_helper.lua") is not None, True)
    check("a script is not flagged as inert",
          session.add_kind("script")["inert"], None)

    # The buttons themselves, in the tabs that own each kind.  Construction is
    # not the same as wiring: a button that exists and calls nothing looks
    # identical until someone presses it.
    print("\nthe Add buttons:")
    for tab_name, button, method in (
        ("textures_tab", "btn_add", "add_texture"),
        ("models_tab", "btn_submesh_add", "add_submesh"),
        ("models_tab", "btn_submesh_del", "remove_submesh"),
        ("scripting_tab", "btn_script", "new_script"),
        ("audio_tab", "btn_add", "add_sound"),
    ):
        owner = getattr(window, tab_name)
        widget = getattr(owner, button, None)
        wired = callable(getattr(owner, method, None))
        label = widget.text() if widget is not None else "MISSING"
        check(f"{tab_name}.{method}", widget is not None and wired, True)
        print(f"          labelled {label!r}")

    print("\nthe Project tab menu:")
    for path in (args.scene, "inifiles/items.ini"):
        kind = session.removal_kind(path)
        word = "Reset to stock…" if kind == "reset" else "Remove from the mod…"
        why = (session.can_reset_to_stock(path) if kind == "reset"
               else session.can_remove_from_mod(path))
        state = "enabled" if why is None else f"disabled — {why[:52]}…"
        print(f"   {path:28} {word:24} {state}")

    # -- 6. take it back out -------------------------------------------------
    print(f"\nremoving {new_vpath}")
    check("removal_kind", session.removal_kind(new_vpath), "remove")
    check("can_remove", session.can_remove_from_mod(new_vpath), None)
    for note in session.removal_notes(new_vpath):
        print(f"   note: {note}")
    session.remove_from_mod(new_vpath)
    check("gone from the mod", new_vpath.lower() in session.mod.files(), False)

    print("\nwhat stays put:")
    print(f"   items.ini: {session.can_remove_from_mod('inifiles/items.ini')}")
    check("an overridden stock file is a reset, not a removal",
          session.removal_kind(args.scene), "reset")

    if args.show:
        app.exec()
    if args.keep:
        print(f"\nleft on disk: {work}")
    else:
        session.mod.close()
        shutil.rmtree(work, ignore_errors=True)

    print(f"\n{'FAILED' if failed else 'all checks passed'}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
