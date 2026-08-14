"""
The application's model layer -- the half of the GUI that can actually be tested.

``session.py`` and ``diagnostics.py`` import no Qt, which is the point: the
logic behind every tab is exercised here, and the widget code that remains is
thin enough to review by reading.  Without this split the app would be verifiable
only by clicking.
"""

from __future__ import annotations

import os
import pathlib
import sys
import tempfile
import zipfile

import pytest

APP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app")
if APP not in sys.path:
    sys.path.insert(0, APP)

from dso_app import diagnostics  # noqa: E402
from dso_app.session import Session  # noqa: E402
from dsotools.errors import DsoError  # noqa: E402
from dsotools.project import FileState, Mod  # noqa: E402
from dsotools.formats import scene as scenefmt  # noqa: E402


SCENE = (
    b'<?xml version="1.0"?>\r\n<WalhallaScene Version="2.00">\r\n'
    b'\t<Object Type=".?AVCWorldRoot@@" />\r\n</WalhallaScene>\r\n'
)


@pytest.fixture()
def world(tmp_path):
    stock = tmp_path / "extracted"
    (stock / "ds_add" / "inifiles").mkdir(parents=True)
    (stock / "ds_add" / "inifiles" / "items.ini").write_bytes(b"[i]\r\n")
    (stock / "ds_add" / "inifiles" / "Goods.ini").write_bytes(b"[goods]\r\n")
    (stock / "ds_3dgen" / "3DView").mkdir(parents=True)
    (stock / "ds_3dgen" / "3DView" / "A.xml").write_bytes(SCENE)

    mod = tmp_path / "Customization" / "M"
    (mod / "inifiles").mkdir(parents=True)
    (mod / "darkstarmod.ini").write_bytes(
        b"[darkstarmod]\r\nmod_name = Test Mod\r\nmod_desc = d\r\n"
    )
    (mod / "inifiles" / "items.ini").write_bytes(b"[i]\r\n")
    (mod / "inifiles" / "Goods.ini").write_bytes(b"[goods]\r\n")     # identical to stock
    (mod / "inifiles" / "New.ini").write_bytes(b"[new]\r\n")         # addition
    with zipfile.ZipFile(mod / "user_data.zip", "w") as zf:
        zf.writestr("3DView/A.xml", b"<edited/>")                    # override
    (mod / "3DView").mkdir()
    (mod / "3DView" / "Dead.xml").write_bytes(SCENE)                 # never read
    return str(stock), str(mod)


def test_open_game_and_mod(world):
    stock, mod = world
    s = Session()
    s.open_game(stock)
    assert "archives" in s.game_summary
    s.open_mod(mod)
    assert s.mod.display_name == "Test Mod"


def test_open_game_rejects_a_folder_that_is_neither(tmp_path):
    """The message must say what it looked for, not just that it failed."""
    s = Session()
    (tmp_path / "empty").mkdir()
    with pytest.raises(DsoError) as exc:
        s.open_game(str(tmp_path / "empty"))
    text = str(exc.value)
    assert "DarkStarOne.exe" in text and ".cpr" in text and "ds_" in text


def test_open_game_reads_an_installation_directly(tmp_path):
    """A .cpr is a plain ZIP, so no extraction step is ever required."""
    import zipfile

    game = tmp_path / "DarkStar One"
    game.mkdir()
    (game / "DarkStarOne.exe").write_bytes(b"MZ")
    with zipfile.ZipFile(game / "ds_3dgen.cpr", "w") as zf:
        zf.writestr("3DView/A.xml", SCENE)
    with zipfile.ZipFile(game / "ds_add.cpr", "w") as zf:
        zf.writestr("inifiles/items.ini", b"[i]\r\n")

    s = Session()
    s.open_game(str(game))
    assert s.game_kind == "install"
    assert "installation" in s.game_summary
    assert s.stock.read("3DView/A.xml") == SCENE
    assert s.stock.find("inifiles/items.ini").origin == "cpr:ds_add"


def test_installation_archive_precedence_is_applied(tmp_path):
    import zipfile

    game = tmp_path / "DarkStar One"
    game.mkdir()
    (game / "DarkStarOne.exe").write_bytes(b"MZ")
    for name, payload in (("ds_3dgen.cpr", b"OLD"), ("ds_add.cpr", b"NEW")):
        with zipfile.ZipFile(game / name, "w") as zf:
            zf.writestr("3DView/BlackHole.xml", payload)

    s = Session()
    s.open_game(str(game))
    # ds_add is searched first -- the real BlackHole.xml case
    assert s.stock.read("3DView/BlackHole.xml") == b"NEW"


def test_loose_install_files_beat_the_archives(tmp_path):
    import zipfile

    game = tmp_path / "DarkStar One"
    (game / "3DView").mkdir(parents=True)
    (game / "DarkStarOne.exe").write_bytes(b"MZ")
    with zipfile.ZipFile(game / "ds_3dgen.cpr", "w") as zf:
        zf.writestr("3DView/A.xml", b"ARCHIVE")
    (game / "3DView" / "A.xml").write_bytes(b"LOOSE")

    s = Session()
    s.open_game(str(game))
    assert s.stock.read("3DView/A.xml") == b"LOOSE"


def test_open_game_rejects_a_missing_path():
    with pytest.raises(DsoError):
        Session().open_game("/definitely/not/here")


def test_open_mod_without_a_manifest_raises(tmp_path):
    (tmp_path / "notamod").mkdir()
    with pytest.raises(DsoError):
        Session().open_mod(str(tmp_path / "notamod"))


def test_mod_tree_classifies_and_orders_by_consequence(world):
    """Dead files first: they are the ones that cost people hours."""
    stock, mod = world
    s = Session()
    s.open_game(stock)
    s.open_mod(mod)
    rows = s.mod_tree()
    assert rows[0]["state"] == FileState.DEAD
    assert rows[0]["vpath"] == "3DView/Dead.xml"

    states = {r["vpath"]: r["state"] for r in rows}
    assert states["3DView/A.xml"] == FileState.OVERRIDE
    assert states["inifiles/Goods.ini"] == FileState.IDENTICAL
    assert states["inifiles/New.ini"] == FileState.ADDITION


def test_mod_tree_reports_where_each_file_must_be_delivered(world):
    stock, mod = world
    s = Session()
    s.open_game(stock)
    s.open_mod(mod)
    targets = {r["vpath"]: r["target"] for r in s.mod_tree()}
    assert targets["3DView/A.xml"] == "zip"
    assert targets["inifiles/New.ini"] == "loose"


def test_mod_tree_names_the_archive_an_override_shadows(world):
    stock, mod = world
    s = Session()
    s.open_game(stock)
    s.open_mod(mod)
    row = next(r for r in s.mod_tree() if r["vpath"] == "3DView/A.xml")
    assert row["stock_origin"] == "cpr:ds_3dgen"


def test_mod_summary_counts_states(world):
    stock, mod = world
    s = Session()
    s.open_game(stock)
    s.open_mod(mod)
    counts = s.mod_summary()
    assert counts[FileState.DEAD] == 1
    assert counts[FileState.OVERRIDE] == 1
    # items.ini and Goods.ini are both byte-identical to their stock copies
    assert counts[FileState.IDENTICAL] == 2
    assert counts[FileState.ADDITION] == 1


def test_works_without_game_data(world):
    """A mod must be inspectable before the game has been located."""
    _stock, mod = world
    s = Session()
    s.open_mod(mod)
    rows = s.mod_tree()
    assert rows
    assert all(r["state"] in ("unknown", FileState.DEAD) for r in rows)


def test_validate_and_index_flow(world):
    stock, mod = world
    s = Session()
    s.open_game(stock)
    s.open_mod(mod)
    report = s.validate()
    assert "PRJ005" in report.by_code()       # the dead loose file
    idx = s.build_index()
    assert idx.stats()["assets"] > 0
    # the index must see the mod's override, not stock's copy
    assert idx.asset("3DView/A.xml")["origin"].startswith("mod:")


def test_index_progress_is_reported(world):
    stock, mod = world
    s = Session()
    s.open_game(stock)
    seen = []
    s.build_index(progress=lambda d, t, p: seen.append(d))
    assert seen


def test_build_index_without_a_game_raises():
    with pytest.raises(DsoError):
        Session().build_index()


def test_validate_without_a_mod_raises(world):
    stock, _mod = world
    s = Session()
    s.open_game(stock)
    with pytest.raises(DsoError):
        s.validate()


def test_subscribers_are_notified(world):
    stock, mod = world
    s = Session()
    events = []
    s.subscribe(events.append)
    s.open_game(stock)
    s.open_mod(mod)
    s.validate()
    assert events == ["game", "mod", "report"]


def test_every_write_into_the_mod_announces_itself(world):
    """Otherwise the Project tab shows a mod without the file just written.

    ``save_strings`` dropped the mod's cached file index but never emitted, so
    ``strings/user_strings.res`` stayed invisible until some unrelated action --
    pressing Validate -- happened to refresh the tab. Reported from the running
    app on 2026-08-22. Each write below is one the user can trigger from a
    dialog and then look for in the file list.
    """
    _stock, mod = world
    s = Session()
    s.open_mod(mod)
    heard = []
    s.subscribe(heard.append)

    s.save_strings([("ID_A", "text")])
    assert heard == ["mod"], "save_strings did not announce the new file"
    assert "strings/user_strings.res" in s.mod.files()

    heard.clear()
    s.create_mission("MY_PATROL")
    assert heard == ["mod"], "create_mission did not announce the new file"
    assert "scripts/my_patrol.lua" in {k.lower() for k in s.mod.files()}


def test_base_game_status_reports_unknown_then_match(world):
    stock, mod = world
    s = Session()
    assert s.base_game_state() == "unknown"
    s.open_game(stock)
    s.open_mod(mod)
    assert s.base_game_state() == "unrecorded"
    s.project.record_base_game(s.stock)
    assert s.base_game_state() == "matches"


def test_base_game_wording_says_what_it_means(world):
    """"not recorded" was a true statement nobody could act on.

    A user asked what it meant, which is the measurable failure of a status
    line.  Every state must name a consequence, and the unrecorded one must say
    it is normal -- it is the state every mod not made in this app is in.
    """
    stock, mod = world
    s = Session()
    s.open_game(stock)
    s.open_mod(mod)

    short = s.base_game_status()
    long = s.base_game_explanation()
    assert "nothing to compare" in short
    assert "not a problem" in long
    assert ".dsoproj" in long

    for state, (short, long) in Session.BASE_GAME_TEXT.items():
        assert short and long, state
        assert len(long) > len(short), state


def test_create_mod_opens_it(world, tmp_path):
    stock, _mod = world
    s = Session()
    s.open_game(stock)
    custom = str(tmp_path / "Customization")
    s.create_mod("Brand New", "desc", customization_dir=custom)
    assert s.mod.display_name == "Brand New"
    assert s.mod.is_listable()
    assert "Brand New" in s.status_line()


def test_create_mod_without_a_customization_folder_explains(monkeypatch):
    from dsotools.project import Mod as ModCls

    monkeypatch.setattr(ModCls, "default_customization_dir", classmethod(lambda cls: None))
    with pytest.raises(DsoError) as exc:
        Session().create_mod("X")
    assert "Customization" in str(exc.value)


def test_status_line_tracks_state(world):
    """The status bar left "Validating…" on screen after a finished run.

    The text is derived from state rather than written once at the start of a
    task, so it cannot get stuck describing something that already finished.
    """
    stock, mod = world
    s = Session()
    assert s.status_line() == "Open a game folder to begin."

    s.open_game(stock)
    assert "layers" in s.status_line()
    assert "not validated" not in s.status_line()      # no mod yet

    s.open_mod(mod)
    line = s.status_line()
    assert "Test Mod" in line and "file(s)" in line and "not validated" in line

    s.validate()
    line = s.status_line()
    assert "not validated" not in line
    assert "warning" in line or "error" in line or "no findings" in line


def test_status_line_resets_when_the_mod_changes(world, tmp_path):
    """Switching mods must not leave the previous mod's result on screen."""
    stock, mod = world
    s = Session()
    s.open_game(stock)
    s.open_mod(mod)
    s.validate()
    assert "not validated" not in s.status_line()

    other = tmp_path / "Customization" / "Other"
    (other / "inifiles").mkdir(parents=True)
    (other / "darkstarmod.ini").write_bytes(
        b"[darkstarmod]\r\nmod_name = Other\r\nmod_desc = d\r\n"
    )
    (other / "inifiles" / "items.ini").write_bytes(b"[i]\r\n")
    s.open_mod(str(other))
    line = s.status_line()
    assert "Other" in line
    assert "Test Mod" not in line
    assert "not validated" in line          # the stale result is gone


# --------------------------------------------------------------------------
# diagnostics
# --------------------------------------------------------------------------


def test_log_buffer_captures_and_notifies():
    diagnostics.install()
    seen = []
    diagnostics.subscribe(seen.append)
    diagnostics.LOG.info("a message for the log panel")
    assert any("a message for the log panel" in line for line in seen)
    assert any("a message for the log panel" in line for line in diagnostics.history())


def test_crash_report_is_written_and_readable(tmp_path, monkeypatch):
    """A packaged build has no console; the report is the only evidence."""
    monkeypatch.chdir(tmp_path)
    try:
        raise ValueError("something specific went wrong")
    except ValueError:
        exc_type, exc, tb = sys.exc_info()
        path = diagnostics.write_crash_report(exc_type, exc, tb)
    assert path and os.path.exists(path)
    text = open(path, encoding="utf-8").read()
    assert "something specific went wrong" in text
    assert "python" in text and "platform" in text
    assert "--- recent log ---" in text


# --------------------------------------------------------------------------
# mod properties
# --------------------------------------------------------------------------


def test_update_mod_metadata_without_renaming(world):
    stock, mod = world
    s = Session()
    s.open_game(stock)
    s.open_mod(mod)
    root_before = s.mod.root

    s.update_mod_metadata("Renamed In Game Only", "New description.")
    assert s.mod.display_name == "Renamed In Game Only"
    assert s.mod.description == "New description."
    assert s.mod.root == root_before


def test_update_mod_metadata_with_a_folder_rename_reopens_the_new_path(world):
    stock, mod = world
    s = Session()
    s.open_game(stock)
    s.open_mod(mod)

    s.update_mod_metadata("Display", "d", folder="new_folder_name")
    assert os.path.basename(s.mod.root) == "new_folder_name"
    assert s.mod.display_name == "Display"
    assert s.project is not None                # reopened, not left stale
    assert os.path.isdir(s.mod.root)


def test_metadata_is_written_before_the_rename(world, monkeypatch):
    """If the rename fails, the metadata edit must still have landed.

    Renaming can fail for reasons outside the app -- the folder open in
    Explorer, the game holding it.  Doing the rename first would leave a folder
    with the wrong name inside it; this order leaves a coherent mod.
    """
    from dsotools.errors import ProjectError

    stock, mod = world
    s = Session()
    s.open_game(stock)
    s.open_mod(mod)

    def boom(self, folder):
        raise ProjectError("folder is locked", path=self.root)

    monkeypatch.setattr(Mod, "rename_folder", boom)
    with pytest.raises(ProjectError):
        s.update_mod_metadata("Edited Anyway", "still saved", folder="nope")

    assert Mod(mod).display_name == "Edited Anyway"


def test_rename_warning_fires_only_for_the_selected_mod(world):
    stock, mod = world
    s = Session()
    s.open_game(stock)
    s.open_mod(mod)

    assert s.mod_rename_warning("something_else") is None    # no mod.ini

    game_dir = os.path.dirname(os.path.dirname(s.mod.root))
    with open(os.path.join(game_dir, "mod.ini"), "wb") as fh:
        fh.write(f"[DarkstarOne]\r\nload_mod = {s.mod.name}\r\n".encode())

    assert s.mod_rename_warning(s.mod.name) is None          # not a rename
    warning = s.mod_rename_warning("something_else")
    assert warning and "deselect" in warning


# --------------------------------------------------------------------------
# the deploy gate
# --------------------------------------------------------------------------


def test_deploy_preview_plans_and_validates(world):
    stock, mod = world
    s = Session()
    s.open_game(stock)
    s.open_mod(mod)

    gate = s.deploy_preview()

    assert gate.plan.relocate == ["3DView/Dead.xml"]
    assert not gate.unvalidated
    assert not gate.blocked


def test_deploy_moves_the_dead_file_and_the_warning_goes_with_it(world):
    stock, mod = world
    s = Session()
    s.open_game(stock)
    s.open_mod(mod)

    result = s.deploy()

    assert result.removed == ["3DView/Dead.xml"]
    with zipfile.ZipFile(os.path.join(mod, "user_data.zip")) as zf:
        assert "3DView/Dead.xml" in zf.namelist()
    assert not os.path.exists(os.path.join(mod, "3DView", "Dead.xml"))
    assert "PRJ005" not in s.validate().by_code()


def test_deploy_invalidates_the_report_it_made(world):
    """A report describing the mod as it was is worse than none at all."""
    stock, mod = world
    s = Session()
    s.open_game(stock)
    s.open_mod(mod)
    s.validate()
    assert s.report is not None

    s.deploy()

    assert s.report is None


def test_an_error_deploy_cannot_fix_blocks_it(world):
    stock, mod = world
    with open(os.path.join(mod, "darkstarmod.ini"), "wb") as fh:
        fh.write(b"[darkstarmod]\r\nmod_name = \r\nmod_desc = d\r\n")   # PRJ001
    s = Session()
    s.open_game(stock)
    s.open_mod(mod)

    gate = s.deploy_preview()
    assert gate.blocked
    assert any(line.startswith("PRJ001") for line in gate.blocker_lines())

    with pytest.raises(DsoError):
        s.deploy(gate)


def test_a_blocked_deploy_can_be_forced(world):
    stock, mod = world
    with open(os.path.join(mod, "darkstarmod.ini"), "wb") as fh:
        fh.write(b"[darkstarmod]\r\nmod_name = \r\nmod_desc = d\r\n")
    s = Session()
    s.open_game(stock)
    s.open_mod(mod)
    gate = s.deploy_preview()

    result = s.deploy(gate, force=True)

    assert result.removed == ["3DView/Dead.xml"]


def test_the_error_deploy_repairs_does_not_block_the_repair(tmp_path, world):
    """PRJ004 is an error, and adding items.ini is deploy's own first act.

    Counting it as a blocker would make the app refuse to apply the fix because
    the fix had not been applied.
    """
    stock, _ = world
    root = tmp_path / "Customization" / "Invisible"
    (root).mkdir(parents=True)
    (root / "darkstarmod.ini").write_bytes(
        b"[darkstarmod]\r\nmod_name = Invisible\r\nmod_desc = d\r\n"
    )
    s = Session()
    s.open_game(stock)
    s.open_mod(str(root))

    gate = s.deploy_preview()
    assert "PRJ004" in gate.report.by_code()      # the rule still fires...
    assert not gate.blocked                       # ...and still does not block

    s.deploy(gate)
    assert Mod(str(root)).is_listable()


def test_deploy_records_the_base_game_it_was_deployed_against(world):
    stock, mod = world
    s = Session()
    s.open_game(stock)
    s.open_mod(mod)
    assert s.base_game_state() == "unrecorded"

    s.deploy()
    s.open_mod(mod)                                # reload from disk

    assert s.base_game_state() == "matches"


def test_deploy_without_a_mod_raises():
    s = Session()
    with pytest.raises(DsoError):
        s.deploy_preview()
    with pytest.raises(DsoError):
        s.deploy()


def test_a_gate_with_no_report_says_so_rather_than_passing_quietly(world):
    """`unvalidated` exists so 'no blockers' is never mistaken for 'checked'."""
    from dso_app.session import DeployGate

    stock, mod = world
    s = Session()
    s.open_game(stock)
    s.open_mod(mod)

    gate = DeployGate(s.mod.deploy_plan(stock=s.stock), None)

    assert gate.unvalidated
    assert not gate.blocked
    assert gate.blockers == []


# --------------------------------------------------------------------------
# textures
# --------------------------------------------------------------------------


def _need_pillow():
    """Skip at run time rather than with @skipif.

    tools/offline_test_runner.py implements only part of pytest on purpose, and
    its mark.skipif is not a decorator -- using it takes the whole module down
    there while passing under real pytest.
    """
    if not _has_pillow():
        pytest.skip("Pillow not installed")


def _need_qt():
    """Skip when PySide6 is absent, the same way Pillow is handled.

    Only the Qt *plumbing* layer needs this -- ``session.py`` is Qt-free and
    its tests run everywhere.  ``workers.py`` imports QtCore at module scope,
    which is right for what it is, so a test that reaches into it cannot run
    without Qt installed.
    """
    try:
        import PySide6  # noqa: F401
    except ImportError:
        pytest.skip("PySide6 not installed; the Qt plumbing cannot be imported")


def _has_pillow():
    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        return False
    return True


def _anim_bytes(source, w, h):
    """A minimal but valid drawable record, same shape as test_edit.py's."""
    import struct

    from dsotools.formats import anim

    b = bytearray(anim.RECORD_SIZE)
    b[0:8] = anim.TAG
    struct.pack_into("<I", b, anim.OFF_FRAMES, 1)
    for off, v in (
        (anim.OFF_WIDTH, w), (anim.OFF_WIDTH2, w),
        (anim.OFF_HEIGHT, h), (anim.OFF_HEIGHT2, h),
    ):
        struct.pack_into("<I", b, off, v)
    raw = source.encode("cp1252")
    b[anim.OFF_SOURCE : anim.OFF_SOURCE + len(raw)] = raw
    return bytes(b)


def _atlas_world(tmp_path):
    """A stock atlas -- a .tex, the .aim page it names, and one drawable."""
    from PIL import Image

    from dsotools.formats import a2d, aim

    stock = tmp_path / "extracted"
    scripts = stock / "ds_interface" / "scripts"
    images = stock / "ds_interface" / "images"
    scripts.mkdir(parents=True)
    images.mkdir(parents=True)
    (stock / "ds_add" / "inifiles").mkdir(parents=True)
    (stock / "ds_add" / "inifiles" / "items.ini").write_bytes(b"[i]\r\n")

    page_name = r"images\Page_0_0.aim"
    page = Image.new("RGBA", (64, 32), (0, 0, 255, 255))
    (images / "Page_0_0.aim").write_bytes(aim.from_image(page))

    subs = [
        a2d.SubImage(r"images\Alpha.aim", 0, 0, 16, 16, page_name),
        a2d.SubImage(r"images\Beta.aim", 16, 0, 16, 16, page_name),
    ]
    (scripts / "TexPage1.tex").write_bytes(a2d.build(a2d.TexturePage(page_name, subs)))
    (scripts / "Alpha.anim").write_bytes(_anim_bytes(r"images\Alpha.aim", 16, 16))

    mod = tmp_path / "Customization" / "M"
    mod.mkdir(parents=True)
    (mod / "darkstarmod.ini").write_bytes(
        b"[darkstarmod]\r\nmod_name = Test Mod\r\nmod_desc = d\r\n"
    )
    return str(stock), str(mod)


def test_texture_assets_lists_images_and_says_where_each_came_from(world):
    stock, mod = world
    s = Session()
    s.open_game(stock)
    s.open_mod(mod)

    rows = s.texture_assets()

    # the fixture's only image-ish file is the .dds inside the mod's zip
    by_path = {r["vpath"].lower(): r for r in rows}
    assert "3dview/textures/new.dds" not in by_path or by_path[
        "3dview/textures/new.dds"]["in_mod"]
    assert all(r["kind"] in Session.TEXTURE_KINDS.values() for r in rows)


def test_open_vfs_releases_the_mod_zip_so_a_save_can_replace_it(world):
    """The Textures tab must not be the reason a later write fails.

    Windows will not replace a file anyone still has open, so a VFS held past
    the read is exactly the bug that broke Deploy.  Reading textures and then
    writing must work in that order.
    """
    stock, mod = world
    s = Session()
    s.open_game(stock)
    s.open_mod(mod)

    s.texture_assets()                     # mounts, and must unmount, the zip
    routed = s.mod.deploy({"3DView/A.xml": b"<written/>"})

    assert routed == {"3DView/A.xml": "zip"}
    with zipfile.ZipFile(os.path.join(mod, "user_data.zip")) as zf:
        assert zf.read("3DView/A.xml") == b"<written/>"


def test_texture_assets_without_a_game_says_so():
    s = Session()
    with pytest.raises(DsoError):
        s.texture_assets()


def test_replacing_a_sprite_writes_only_the_page(tmp_path):
    _need_pillow()
    from PIL import Image

    stock, mod = _atlas_world(tmp_path)
    s = Session()
    s.open_game(stock)
    s.open_mod(mod)

    page = s.open_atlas("scripts/TexPage1.tex")
    page.replace("Alpha", Image.new("RGBA", (16, 16), (255, 0, 0, 255)))
    routed = s.commit_atlas(page, source="red.png", operation="replace-sprite")

    # the rectangle did not move, so the index and the drawable are untouched
    assert list(routed) == ["images/Page_0_0.aim"]
    assert s.project.provenance_of("images/Page_0_0.aim")["source"] == "red.png"

    # and the mod's copy is what the app now reads
    reopened = s.open_atlas("scripts/TexPage1.tex")
    assert reopened.image.getpixel((2, 2)) == (255, 0, 0, 255)


def test_rescale_writes_page_index_and_drawable_together(tmp_path):
    """The whole point of the operation: three file types, one commit."""
    _need_pillow()
    stock, mod = _atlas_world(tmp_path)
    s = Session()
    s.open_game(stock)
    s.open_mod(mod)

    page = s.open_atlas("scripts/TexPage1.tex")
    page.rescale(2.0)
    routed = s.commit_atlas(page, operation="rescale")

    assert set(routed) == {
        "images/Page_0_0.aim",
        "scripts/TexPage1.tex",
        "scripts/Alpha.anim",
    }
    reopened = s.open_atlas("scripts/TexPage1.tex")
    assert reopened.size == (128, 64)
    assert reopened.sprite("Alpha").w == 32
    assert s.atlas_problems(reopened) == []


def test_a_wrong_sized_replacement_is_refused_with_the_sizes_named(tmp_path):
    _need_pillow()
    from PIL import Image

    from dsotools.errors import ValidationError

    stock, mod = _atlas_world(tmp_path)
    s = Session()
    s.open_game(stock)
    s.open_mod(mod)
    page = s.open_atlas("scripts/TexPage1.tex")

    with pytest.raises(ValidationError) as exc:
        page.replace("Alpha", Image.new("RGBA", (24, 24)))

    assert "16x16" in str(exc.value) and "24x24" in str(exc.value)


def test_decode_preview_reads_a_dds_and_says_what_it_is(tmp_path):
    """The summary matters: an atlas page and a model texture look identical
    on screen and are edited in completely different ways."""
    import struct

    from dsotools.formats import dds

    stock = tmp_path / "extracted" / "ds_3dtex" / "3DView" / "textures"
    stock.mkdir(parents=True)
    header = bytearray(4 + 124)
    header[0:4] = b"DDS "
    struct.pack_into("<7I", header, 4, 124, dds.DDSD_MIPMAPCOUNT, 4, 4, 0, 0, 1)
    pf = 4 + 72
    struct.pack_into("<2I", header, pf, 32, dds.DDPF_FOURCC)
    header[pf + 8 : pf + 12] = b"DXT1"
    block = struct.pack("<HHI", 0xFFFF, 0x0000, 0b11100100)
    (stock / "t.dds").write_bytes(bytes(header) + block)

    s = Session()
    s.open_game(str(tmp_path / "extracted"))
    decoded = s.decode_preview("3DView/textures/t.dds")

    assert (decoded["width"], decoded["height"]) == (4, 4)
    assert len(decoded["rgba"]) == 4 * 4 * 4          # RGBA8888, ready for QImage
    assert decoded["summary"].startswith("DDS")
    assert "DXT1" in decoded["summary"]


def test_decode_preview_reads_an_aim(tmp_path):
    _need_pillow()
    stock, mod = _atlas_world(tmp_path)
    s = Session()
    s.open_game(stock)

    decoded = s.decode_preview("images/Page_0_0.aim")

    assert (decoded["width"], decoded["height"]) == (64, 32)
    assert len(decoded["rgba"]) == 64 * 32 * 4
    assert decoded["summary"].startswith("AIM")


def test_decode_preview_refuses_a_file_that_is_not_an_image(world):
    stock, mod = world
    s = Session()
    s.open_game(stock)

    with pytest.raises(DsoError):
        s.decode_preview("inifiles/items.ini")


def test_page_writable_says_yes_for_a_format_we_can_write(tmp_path):
    _need_pillow()
    stock, mod = _atlas_world(tmp_path)
    s = Session()
    s.open_game(stock)

    assert s.page_writable(s.open_atlas("scripts/TexPage1.tex")) is None


def test_page_writable_names_the_encoding_it_cannot_write(tmp_path):
    """Two stock pages are IMJPG24A; saying so up front beats failing at save.

    The Textures tab derives Replace/Rescale from this on every page change
    rather than disabling them in place -- doing the latter left the buttons
    dead for every page opened after an IMJPG24A one.
    """
    _need_pillow()
    stock, mod = _atlas_world(tmp_path)
    s = Session()
    s.open_game(stock)
    page = s.open_atlas("scripts/TexPage1.tex")
    page.source.tiles[0].encoding = "IMJPG24A"

    why = s.page_writable(page)

    assert why and "IMJPG24A" in why


def test_page_writable_tolerates_a_page_with_no_source():
    """AtlasPage can be built in memory; that is not a reason to refuse."""
    class _Bare:
        source = None

    assert Session.page_writable(_Bare()) is None


# --------------------------------------------------------------------------
# worker shutdown
# --------------------------------------------------------------------------


def test_a_worker_emit_survives_a_deleted_signal_source():
    """Closing the window mid-scan must not write a crash report.

    Qt deletes the C++ side of WorkerSignals at shutdown while the runnable may
    still be on the pool; every emit then raises RuntimeError. Each emit is
    guarded separately, because the failure used to cascade through the except
    branch and the finally as well.
    """
    _need_qt()
    from dso_app import workers

    class _Dead:
        def emit(self, *args):
            raise RuntimeError("Signal source has been deleted")

    worker = workers.Worker.__new__(workers.Worker)

    assert worker._emit(_Dead()) is False
    assert worker._emit(_Dead(), "a", "b") is False


def test_a_worker_emit_reports_success_when_the_source_is_alive():
    _need_qt()
    from dso_app import workers

    sent = []

    class _Live:
        def emit(self, *args):
            sent.append(args)

    worker = workers.Worker.__new__(workers.Worker)

    assert worker._emit(_Live(), 1, 2) is True
    assert sent == [(1, 2)]


def test_a_worker_emit_does_not_swallow_other_errors():
    """Only a destroyed source is expected; anything else is a real bug."""
    _need_qt()
    from dso_app import workers

    class _Broken:
        def emit(self, *args):
            raise ValueError("something else entirely")

    worker = workers.Worker.__new__(workers.Worker)

    with pytest.raises(ValueError):
        worker._emit(_Broken())


# --------------------------------------------------------------------------
# scenes and models
# --------------------------------------------------------------------------


def test_scenes_lists_3dview_xml_and_hides_low_twins(world):
    """394 scenes have a _low twin carrying independent bindings (SCN004).

    Showing both doubles the browser with entries that look like duplicates and
    are not, so the twins are opt-in.
    """
    stock, mod = world
    stock_root = os.path.dirname(os.path.dirname(os.path.join(stock, "x")))
    low = os.path.join(stock, "ds_3dgen", "3DView", "A_low.xml")
    with open(low, "wb") as fh:
        fh.write(SCENE)
    del stock_root

    s = Session()
    s.open_game(stock)

    names = {r["name"] for r in s.scenes()}
    assert "A" in names
    assert "A_low" not in names
    assert "A_low" in {r["name"] for r in s.scenes(include_low=True)}


def test_scenes_sorts_top_level_before_subfolders(world):
    """The ships live at the top of 3DView/; 42 camera scenes live below it."""
    stock, mod = world
    sub = os.path.join(stock, "ds_3dgen", "3DView", "ActionCams")
    os.makedirs(sub, exist_ok=True)
    with open(os.path.join(sub, "AAA.xml"), "wb") as fh:
        fh.write(SCENE)

    s = Session()
    s.open_game(stock)
    names = [r["name"] for r in s.scenes()]

    # "ActionCams/AAA" sorts before "A" alphabetically; depth must win
    assert names.index("A") < names.index("ActionCams/AAA")


def test_scene_detail_reports_the_scn001_check(world):
    stock, mod = world
    s = Session()
    s.open_game(stock)

    detail = s.scene_detail("3DView/A.xml")

    assert detail["path"] == "3DView/A.xml"
    assert isinstance(detail["meshes"], list)


def test_scene_detail_on_a_scene_with_no_meshes_is_empty_not_an_error(world):
    stock, mod = world
    s = Session()
    s.open_game(stock)

    assert s.scene_detail("3DView/A.xml")["meshes"] == []


def test_scenes_without_a_game_says_so():
    s = Session()
    with pytest.raises(DsoError):
        s.scenes()


# --------------------------------------------------------------------------
# scene detail: resolved texture paths, and matching a draw call to its mesh
#
# Both exist because of one bug.  A scene names its textures *relative to
# itself* ("textures/a_col.dds"), and the linked-assets panel handed that
# string straight to Preview / Export / Open-in-tab, every one of which reads
# the VFS and fails with VFS001.  The mesh's `.3do` had carried a resolved
# `model_vpath` all along; textures had no equivalent.
#
# Fixing that exposed the second half: meshes were matched to draw calls by
# *name*, and names are not unique.  PlayerShip.xml has 85 meshes under 28
# names because each of its eleven body variants contains one called `main_`,
# so body_0's submesh was reading body_10's effects.
# --------------------------------------------------------------------------


#: Two variants, both containing a mesh named `main_`, bound to different
#: textures.  That is the shape that tells a path match from a name match.
VARIANT_SCENE = (
    b'<?xml version="1.0"?>\r\n<WalhallaScene Version="2.00">\r\n'
    b'\t<Object Type=".?AVCWorldRoot@@">\r\n\t\t<AttachedObjects>\r\n'
    b'\t\t\t<Object Type=".?AVCObject@@" Name="bodys">\r\n'
    b"\t\t\t\t<AttachedObjects>\r\n"
    b'\t\t\t\t\t<Object Type=".?AVCObject@@" Name="body_0">\r\n'
    b"\t\t\t\t\t\t<AttachedObjects>\r\n"
    b'\t\t\t\t\t\t\t<Object Type=".?AVCMesh@@" Name="main_" '
    b'Resrc3DO="objects/x.3do">\r\n'
    b'\t\t\t\t\t\t\t\t<EffectContainer Path="blender/mat_main.bsd9">\r\n'
    b'\t\t\t\t\t\t\t\t\t<Textures Number="2">\r\n'
    b"\t\t\t\t\t\t\t\t\t\ttextures/a_col.dds\r\n"
    b"\t\t\t\t\t\t\t\t\t\ttextures/gone.dds\r\n"
    b"\t\t\t\t\t\t\t\t\t</Textures>\r\n"
    b"\t\t\t\t\t\t\t\t</EffectContainer>\r\n\t\t\t\t\t\t\t</Object>\r\n"
    b"\t\t\t\t\t\t</AttachedObjects>\r\n\t\t\t\t\t</Object>\r\n"
    b'\t\t\t\t\t<Object Type=".?AVCObject@@" Name="body_1">\r\n'
    b"\t\t\t\t\t\t<AttachedObjects>\r\n"
    b'\t\t\t\t\t\t\t<Object Type=".?AVCMesh@@" Name="main_" '
    b'Resrc3DO="objects/y.3do">\r\n'
    b'\t\t\t\t\t\t\t\t<EffectContainer Path="blender/mat_main.bsd9">\r\n'
    b'\t\t\t\t\t\t\t\t\t<Textures Number="1">\r\n'
    b"\t\t\t\t\t\t\t\t\t\ttextures/b_col.dds\r\n"
    b"\t\t\t\t\t\t\t\t\t</Textures>\r\n"
    b"\t\t\t\t\t\t\t\t</EffectContainer>\r\n\t\t\t\t\t\t\t</Object>\r\n"
    b"\t\t\t\t\t\t</AttachedObjects>\r\n\t\t\t\t\t</Object>\r\n"
    b"\t\t\t\t</AttachedObjects>\r\n\t\t\t</Object>\r\n"
    b"\t\t</AttachedObjects>\r\n\t</Object>\r\n</WalhallaScene>\r\n"
)


class _Call:
    """The parts of a ``meshview.DrawCall`` these two functions look at."""

    def __init__(self, node, node_path, index, textures, slot_names=()):
        self.node = node
        self.node_path = node_path
        self.index = index
        self.textures = list(textures)
        self.slot_names = list(slot_names)


def _mini_bsd9(slots=("t_Color", "t_Normal"), semantics=("Bumpiness",)):
    """The smallest structurally exact `.bsd9`, with a D3DX9 parameter table.

    Built here rather than imported from ``test_bsd9`` so the pytest-free
    fallback runner, which execs each test file on its own, can still run this.
    """
    import struct

    def hstr(text):                      # header string: length excludes the NUL
        raw = text.encode("latin-1")
        field = raw + b"\0"
        field += b"\0" * (-len(field) % 4)
        return struct.pack("<I", len(raw)) + field

    # -- the D3DX9 blob: one float parameter per semantic
    data = bytearray(b"\0" * 4)
    placed = []

    def dstr(text):                      # D3DX string: length includes the NUL
        off = len(data)
        raw = text.encode("latin-1") + b"\0"
        data.extend(struct.pack("<I", len(raw)) + raw)
        data.extend(b"\0" * (-len(data) % 4))
        return off

    for sem in semantics:
        n_off, s_off = dstr("g_" + sem), dstr(sem)
        v_off = len(data)
        data.extend(struct.pack("<f", 1.0))
        t_off = len(data)
        data.extend(struct.pack("<5I", 3, 0, n_off, s_off, 0))   # float scalar
        data.extend(struct.pack("<2I", 1, 1))
        placed.append((t_off, v_off))
    start = len(data)
    data.extend(struct.pack("<4I", len(placed), 0, 0, 0))
    for t_off, v_off in placed:
        data.extend(struct.pack("<4I", t_off, v_off, 0, 0))
    blob = struct.pack("<2I", 0xFEFF0901, start) + bytes(data)

    out = bytearray(b"XF  90.1")
    out += struct.pack("<2I", 1000, len(slots))
    if slots:
        out += struct.pack(f"<{len(slots)}I", *range(len(slots)))
    out += b"\0" * (0x30 - len(out))
    names = list(slots) + ["DoIt"]
    out += struct.pack("<I", len(names))
    for nm in names:
        out += hstr(nm)
    out += struct.pack("<I", len(blob)) + blob
    out += b"LRAV" + struct.pack("<I", 12) + b"\0\0\0\0"     # one VARL chunk
    out += b"\0\0\0\0"                                       # terminator
    return bytes(out)


def _variant_world(stock, shader=True):
    """Add the two-variant scene and the textures it resolves to."""
    view = os.path.join(stock, "ds_3dgen", "3DView")
    os.makedirs(os.path.join(view, "textures"), exist_ok=True)
    with open(os.path.join(view, "V.xml"), "wb") as fh:
        fh.write(VARIANT_SCENE)
    for name in ("a_col.dds", "b_col.dds"):
        with open(os.path.join(view, "textures", name), "wb") as fh:
            fh.write(b"DDS ")
    # "textures/gone.dds" is deliberately absent.
    if shader:
        os.makedirs(os.path.join(view, "blender"), exist_ok=True)
        with open(os.path.join(view, "blender", "mat_main.bsd9"), "wb") as fh:
            fh.write(_mini_bsd9())


def test_scene_detail_resolves_texture_references_to_vpaths(world):
    """A scene-relative reference is not a path anything can read.

    The panel used to show ``textures/a_col.dds`` and hand it to Preview,
    which reads the VFS and fails.  ``texture_vpaths`` is the resolved form,
    computed where the VFS is open and the scene path is known -- which is
    also the only place it *can* be resolved correctly, since the reference is
    relative to this scene and nothing else.
    """
    stock, mod = world
    _variant_world(stock)
    s = Session()
    s.open_game(stock)

    detail = s.scene_detail("3DView/V.xml")
    slot = detail["meshes"][0]["slots"][0]

    # The raw reference is kept -- it is what the author wrote and what an
    # edit writes back.
    assert slot["textures"][0] == "textures/a_col.dds"
    assert slot["texture_vpaths"][0] == "3DView/textures/a_col.dds"
    assert slot["resolved"][0] is True

    # A reference that resolves to nothing says so rather than inventing one.
    assert slot["texture_vpaths"][1] is None
    assert slot["resolved"][1] is False


def test_scene_detail_carries_what_the_shader_declares(world):
    """`.bsd9` names its texture slots and its addressable semantics."""
    stock, mod = world
    _variant_world(stock)
    s = Session()
    s.open_game(stock)

    slot = s.scene_detail("3DView/V.xml")["meshes"][0]["slots"][0]
    assert slot["slot_names"] == ["t_Color", "t_Normal"]
    assert slot["semantics"] == ["Bumpiness"]


def test_scene_detail_carries_the_shader_defaults(world):
    """What "Reset to shader defaults" resets *to*.

    Only knowable since `.bsd9` was decoded; before that the editor had no
    defensible value to offer and the button could not have existed.
    """
    stock, mod = world
    _variant_world(stock)
    s = Session()
    s.open_game(stock)

    slot = s.scene_detail("3DView/V.xml")["meshes"][0]["slots"][0]
    assert slot["defaults"] == {"Bumpiness": 1.0}


def test_material_default_needs_all_five_or_offers_none(world):
    """A half-filled material would put invented numbers in four boxes."""
    from dso_app.session import _material_default

    class _P:
        def __init__(self, default):
            self.default = default

    full = {
        "Diffuse": _P((1.0, 1.0, 1.0, 1.0)),
        "Ambient": _P((0.0, 0.0, 0.0, 1.0)),
        "Specular": _P((1.0, 1.0, 1.0, 1.0)),
        "Emissive": _P((0.0, 0.0, 0.0, 1.0)),
        "SpecularPower": _P((20.0,)),
    }
    out = _material_default(full)
    assert out is not None and len(out) == 17
    assert out[:4] == [1.0, 1.0, 1.0, 1.0]
    assert out[16] == 20.0

    missing = dict(full)
    del missing["Ambient"]
    assert _material_default(missing) is None

    wrong_width = dict(full)
    wrong_width["Specular"] = _P((1.0,))
    assert _material_default(wrong_width) is None


# -- what an effect edit writes, and what "reset to defaults" means ----------
#
# Both of these were reported from the app: "Reset to shader defaults puts
# other numbers than there were initially" on Container.xml.  The values were
# right -- they really are `mat_main.bsd9`'s defaults -- but two rules were
# wrong underneath, and there was no button to simply undo an edit.


def test_an_unset_parameter_is_never_written():
    """`<Float semantic="X" />` with no value already *means* the default.

    The widget's sentinel for unset is the spin box's minimum, and the old
    check only skipped it when the scene had it unset too -- so unsetting a
    parameter that *did* have a value wrote -10000 into the scene as a number.
    """
    from dso_app.session import effect_parameter_edits

    original = {"Bumpiness": 1.0, "FresnelPower": None}
    assert effect_parameter_edits(original, {"Bumpiness": 1.0, "FresnelPower": None}) == {}
    # Was set, now unset: not expressible as an edit, so nothing is written --
    # and above all not the sentinel.
    assert effect_parameter_edits({"Bumpiness": 1.0}, {"Bumpiness": None}) == {}


def test_only_genuine_changes_are_written():
    from dso_app.session import effect_parameter_edits

    original = {"Bumpiness": 1.0, "Roughness": 0.25}
    assert effect_parameter_edits(original, dict(original)) == {}
    assert effect_parameter_edits(original, {"Bumpiness": 1.0, "Roughness": 1.0}) == {
        "Roughness": 1.0
    }


def test_reset_to_defaults_leaves_an_unset_parameter_unset():
    """Container.xml leaves FresnelPower and FresnelBias unset.

    Filling in the shader's 2.0 and 0.0 adds attributes the file does not have
    and changes nothing the engine does -- a diff for no reason.
    """
    from dso_app.session import effect_default_values

    original = {"Reflectivity": 0.6, "FresnelPower": None, "FresnelBias": None}
    defaults = {"Reflectivity": 1.0, "FresnelPower": 2.0, "FresnelBias": 0.0}

    assert effect_default_values(original, defaults) == {
        "Reflectivity": 1.0,
        "FresnelPower": None,
        "FresnelBias": None,
    }


def test_reset_to_defaults_leaves_a_parameter_the_shader_does_not_declare():
    """`mat_main_2` has no Bumpiness; there is no default to reset it to."""
    from dso_app.session import effect_default_values

    out = effect_default_values({"Bumpiness": 0.3, "Roughness": 0.2}, {"Roughness": 1.0})
    assert out == {"Bumpiness": 0.3, "Roughness": 1.0}


def test_reset_to_defaults_with_no_shader_changes_nothing():
    from dso_app.session import effect_default_values

    original = {"Bumpiness": 0.3}
    assert effect_default_values(original, None) == original


def test_a_shader_that_cannot_be_read_says_none_not_empty(world):
    """"Declares nothing" and "could not be read" must stay distinguishable.

    466 stock effects name a shader that is not installed; telling their author
    every parameter is inert would be a lie.
    """
    stock, mod = world
    _variant_world(stock, shader=False)
    s = Session()
    s.open_game(stock)

    slot = s.scene_detail("3DView/V.xml")["meshes"][0]["slots"][0]
    assert slot["slot_names"] is None
    assert slot["semantics"] is None
    assert slot["defaults"] is None
    assert slot["material_default"] is None


def _imported_model(*counts):
    """A **real** ``ThreeDOModel`` with ``counts[i]`` submeshes in LOD ``i``.

    It used to be a stub with nothing but ``lods[].submeshes``, which was
    enough while ``preflight_glb`` only counted them.  It now also runs the
    MDL rules over the bytes the import would write, and those need a model
    that can actually be built -- which is the honest fixture anyway: the
    thing under test is what lands on disk.

    Built through the same calls ``gltf.import_glb`` uses, one degenerate
    triangle per submesh, partitioning both buffers the way stock models do.
    """
    from dsotools.formats.threedo import (
        LOD, MAGIC_LOD, Submesh, ThreeDOModel, Vertex, VertexElement,
        build_mesh_header, build_root_prefix,
    )

    elements = [
        VertexElement(0, 0, 2, 0, 0, 0),    # POSITION FLOAT3
        VertexElement(0, 12, 2, 0, 3, 0),   # NORMAL   FLOAT3
        VertexElement(0, 24, 1, 0, 5, 0),   # TEXCOORD FLOAT2
    ]
    lods = []
    for n in counts:
        vertices, indices, submeshes = [], [], []
        for s in range(n):
            base = 3 * s
            for corner in ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0)):
                vertices.append(Vertex.make(corner, (0.0, 0.0, 1.0), (0.0, 0.0), ()))
            indices += [base, base + 1, base + 2]
            submeshes.append(Submesh(s, s, 1, base, 3))
        # The tag comes from the template -- build() patches the counts around
        # it and never writes it -- so a zero-filled header produces a file with
        # no LOD chunk in it at all.
        lods.append(LOD(indices=indices, vertices=vertices, submeshes=submeshes,
                        lod_header_template=bytearray(MAGIC_LOD + bytes(24)),
                        elements=elements))
    root = bytearray(build_root_prefix("fixture", lods))
    return ThreeDOModel(name="fixture", lods=lods, root_prefix_template=root,
                        mesh_header_template=bytearray(build_mesh_header(len(lods))))


def test_preflight_glb_counts_submeshes_across_every_lod(world, monkeypatch):
    """SCN001 is one EffectContainer per submesh *across all LODs*.

    Counting LOD 0 alone is the mistake that once produced 623 false positives
    on a real mod, and it is preserved in specs/scene.md next to the right
    version.  Guarded here so it cannot come back through this door.
    """
    from dsotools.convert import gltf

    stock, mod = world
    s = Session()
    s.open_game(stock)
    s.open_mod(mod)
    monkeypatch.setattr(gltf, "import_glb", lambda p: _imported_model(4, 4, 2))

    check = s.preflight_glb("3DView/objects/nope.3do", "whatever.glb")
    assert check["submesh_total"] == 10
    assert check["lods"] == 3


def test_preflight_glb_says_so_when_it_could_not_check(world, monkeypatch):
    """A check that could not run must not read as a check that passed.

    Without an asset index there is no reverse lookup, so no scene can be
    examined -- and reporting "no conflicts" would be a clean bill of health
    nobody earned.  `indexed` is what lets the caller say that out loud.
    """
    from dsotools.convert import gltf

    stock, mod = world
    s = Session()
    s.open_game(stock)
    s.open_mod(mod)
    monkeypatch.setattr(gltf, "import_glb", lambda p: _imported_model(2))

    check = s.preflight_glb("3DView/objects/nope.3do", "whatever.glb")
    assert check["indexed"] is False
    assert check["conflicts"] == []
    assert check["scenes"] == 0


def test_asset_info_says_where_a_file_comes_from_and_whether_it_can_be_reset(world):
    """The facts every asset row shows, and the reason it was wrong.

    Reported from real use: after replacing a texture, the linked-assets panel
    gave no sign that the mod now supplied it -- the Source column was blank.
    The panel's *other* view had the facts all along; this is the one call both
    of them use now.
    """
    stock, mod = world
    s = Session()
    s.open_game(stock)
    s.open_mod(mod)

    info = s.asset_info([
        "3DView/A.xml",          # the mod overrides this one, in user_data.zip
        "inifiles/items.ini",    # in the mod, and must never be reset
        "3DView/Nope.xml",       # nowhere at all
    ])

    over = info["3DView/A.xml"]
    assert over["in_mod"] is True
    assert "mod" in over["source"]
    assert over["reset_reason"] is None          # i.e. it can be reset

    keep = info["inifiles/items.ini"]
    assert keep["in_mod"] is True
    assert "items.ini" in keep["reset_reason"]   # disabled, and says why

    missing = info["3DView/Nope.xml"]
    assert missing["resolved"] is False
    assert missing["source"] == ""


def test_asset_info_and_describe_assets_cannot_disagree(world):
    """One implementation behind both, because they drifted once already."""
    stock, mod = world
    s = Session()
    s.open_game(stock)
    s.open_mod(mod)

    (row,) = s.describe_assets([("scene", "3DView/A.xml")])
    facts = s.asset_info(["3DView/A.xml"])["3DView/A.xml"]

    assert row["role"] == "scene"
    assert {k: row[k] for k in facts} == facts


# --------------------------------------------------------------------------
# the data tables (inifiles/)
#
# 68 files, 13,091 sections, 120,034 entries -- the largest moddable surface in
# the game, and the one that needed no reverse engineering.
# --------------------------------------------------------------------------

SHIP_INI = (
    b"; Schiffe\r\n"
    b"[StarShip002_000]\r\n"
    b"Hitpoints = 1200 ; Trefferpunkte\r\n"
    b"Speed = 55.500000\r\n"
    b"Name = Fighter\r\n"
    b"\r\n"
    b"[StarShip002_001]\r\n"
    b"Hitpoints = 1400\r\n"
    b"Speed = 61.000000\r\n"
)


def _ini_world(stock, mod):
    (pathlib.Path(stock) / "ds_add" / "inifiles" / "StarShip.ini").write_bytes(SHIP_INI)
    s = Session()
    s.open_game(stock)
    s.open_mod(mod)
    return s


def test_ini_files_lists_the_tables_with_where_they_come_from(world):
    stock, mod = world
    s = _ini_world(stock, mod)

    rows = {r["name"]: r for r in s.ini_files()}

    assert "StarShip.ini" in rows
    assert rows["StarShip.ini"]["in_mod"] is False
    # The mod ships its own items.ini and Goods.ini, so those say so.
    assert rows["items.ini"]["in_mod"] is True


def test_open_ini_returns_sections_entries_and_their_comments(world):
    stock, mod = world
    s = _ini_world(stock, mod)

    data = s.open_ini("inifiles/StarShip.ini")

    assert [x["name"] for x in data["sections"]] == [
        "StarShip002_000", "StarShip002_001",
    ]
    first = data["sections"][0]["entries"]
    assert [e["key"] for e in first] == ["Hitpoints", "Speed", "Name"]
    assert first[0]["value"] == "1200"
    # The comment is kept apart from the value: `1200 ; Trefferpunkte` as a
    # value is what breaks every numeric read downstream.
    assert "Trefferpunkte" in first[0]["comment"]


def test_setting_a_value_rewrites_one_line_and_nothing_else(world):
    """The whole point of the line-preserving parser, end to end.

    Diff-against-stock is worthless if saving one number reformats a 3 MB
    table, and `Planets.ini` really is 3 MB.
    """
    stock, mod = world
    s = _ini_world(stock, mod)

    s.set_ini_values("inifiles/StarShip.ini",
                     [("StarShip002_000", "Hitpoints", "1500")])

    written = s.read_asset("inifiles/StarShip.ini")
    assert written.count(b"\r\n") == SHIP_INI.count(b"\r\n")
    before = SHIP_INI.split(b"\r\n")
    after = written.split(b"\r\n")
    changed = [i for i, (a, b) in enumerate(zip(before, after)) if a != b]
    assert len(changed) == 1
    # The comment on that line survives the edit.
    assert after[changed[0]] == b"Hitpoints = 1500 ; Trefferpunkte"


def test_setting_a_value_refuses_a_key_that_is_not_there(world):
    """It edits values; it does not invent schema.

    Nothing writes down which keys the engine reads, so a key this tool made up
    is one nobody can say has any effect -- and the failure mode is silence.
    """
    stock, mod = world
    s = _ini_world(stock, mod)

    with pytest.raises(DsoError):
        s.set_ini_values("inifiles/StarShip.ini",
                         [("StarShip002_000", "Invented", "1")])
    with pytest.raises(DsoError):
        s.set_ini_values("inifiles/StarShip.ini", [("NoSuchShip", "Speed", "1")])
    # And nothing was written on the way to refusing.
    assert s.read_asset("inifiles/StarShip.ini") == SHIP_INI


def test_a_saved_table_is_read_back_from_the_mod(world):
    stock, mod = world
    s = _ini_world(stock, mod)

    s.set_ini_values("inifiles/StarShip.ini",
                     [("StarShip002_001", "Speed", "99.000000")])

    assert {r["name"]: r["in_mod"] for r in s.ini_files()}["StarShip.ini"] is True
    reread = s.open_ini("inifiles/StarShip.ini")
    speed = [e for e in reread["sections"][1]["entries"] if e["key"] == "Speed"]
    assert speed[0]["value"] == "99.000000"


def test_the_texture_cache_is_dropped_when_the_mod_changes(world):
    """Decoded pixels are kept between scene builds -- but only while valid.

    Reopening a scene is now routine: every save, replace and reset does it,
    and decoding PlayerShip's 69 textures is most of what that costs (1.58s
    first, 0.41s after).  The cache is cleared on the same signal as the
    preview cache, because serving the *old* pixels after a replace is the
    "my edit did nothing" trap this project keeps meeting.
    """
    stock, mod = world
    s = Session()
    s.open_game(stock)
    s.open_mod(mod)
    s._texture_cache[("textures/x.dds", False, 1)] = None

    s.reset_to_stock("inifiles/Goods.ini")

    assert not s._texture_cache


def test_the_texture_cache_is_bounded(world):
    """A viewer that browses 612 scenes must not grow without limit."""
    class _Fake:
        def __init__(self, n):
            self.rgba = bytes(n)

    stock, mod = world
    s = Session()
    s.open_game(stock)
    over = Session.TEXTURE_CACHE_BYTES // 4 + 1
    for i in range(5):
        s._texture_cache[i] = _Fake(over)
    s._trim_texture_cache()

    kept = sum(len(v.rgba) for v in s._texture_cache.values())
    assert kept <= Session.TEXTURE_CACHE_BYTES
    assert s._texture_cache                     # not emptied, just trimmed


def test_blinker_labels_say_which_part_the_lights_belong_to():
    """The name does not: PlayerShip has 63 groups under 5 names.

    `blinks_0` alone appears 33 times, because every body, wing and booster
    variant carries its own -- so the combo listed 63 entries that could not be
    told apart.  The node path is what distinguishes them.
    """
    groups = [
        {"name": "blinks_0", "path": "bodys|body_0|blinks_0"},
        {"name": "blinks_0", "path": "bodys|body_7|blinks_0"},
        {"name": "blinks_3", "path": "wings|wing_0|backwing_l|blinks_3"},
        {"name": "blinks_0", "path": "boosts|booster_10|blinks_0"},
        {"name": "lonely", "path": "lonely"},
    ]
    Session._blinker_labels(groups)

    assert [g["label"] for g in groups] == [
        "body_0 · blinks_0",
        "body_7 · blinks_0",
        "wing_0 / backwing_l · blinks_3",
        "booster_10 · blinks_0",
        "lonely",
    ]


def test_blinker_labels_keep_the_whole_path_when_shortening_would_collide():
    """A shortened label that no longer identifies the thing is worse than a
    long one -- the same "names are not keys" trap, one level up."""
    groups = [
        {"name": "blinks_0", "path": "left|turret|blinks_0"},
        {"name": "blinks_0", "path": "right|turret|blinks_0"},
        {"name": "blinks_0", "path": "hull|nose|blinks_0"},
    ]
    Session._blinker_labels(groups)

    assert [g["label"] for g in groups] == [
        "left / turret · blinks_0",
        "right / turret · blinks_0",
        "nose · blinks_0",          # no collision, so it stays short
    ]


def test_blinker_groups_carry_the_resolved_texture_path(world):
    """A scene's texture reference is relative to that scene.

    The blinker pane offers Replace on this path, and a raw ``textures/x.dds``
    would fail with VFS001 the moment anything read it -- the same defect that
    once broke Preview, Export and Open-in-its-tab on every texture row.
    """
    stock, mod = world
    root = pathlib.Path(stock) / "ds_3dgen" / "3DView"
    (root / "textures").mkdir(parents=True, exist_ok=True)
    (root / "textures" / "blink.dds").write_bytes(b"DDS ")
    (root / "B.xml").write_bytes(
        b'<?xml version="1.0"?>\r\n<WalhallaScene Version="2.00">\r\n'
        b'\t<Object Type=".?AVCWorldRoot@@">\r\n\t\t<AttachedObjects>\r\n'
        b'\t\t\t<Object Type=".?AVCBlinkerGroup@@" Name="blinks_0" '
        b'Texture="textures/blink.dds">\r\n'
        b'\t\t\t\t<Blinker displacement="+0.0 +1.0 +2.0 +0.2 " vrow="+0.111" '
        b'animtime="+1.0" />\r\n'
        b"\t\t\t</Object>\r\n\t\t</AttachedObjects>\r\n\t</Object>\r\n"
        b"</WalhallaScene>\r\n"
    )

    s = Session()
    s.open_game(stock)
    (group,) = s.blinker_groups("3DView/B.xml")

    assert group["texture"] == "textures/blink.dds"          # as the scene says
    assert group["texture_vpath"] == "3DView/textures/blink.dds"
    assert s.read_asset(group["texture_vpath"]) == b"DDS "   # and it is readable


def test_texture_format_names_what_the_file_actually_is(world, tmp_path):
    """The answer to "what should I save mine as?" is what this one already is.

    Header only: it is read while a dialog opens, and the question is about the
    format, not the pixels.
    """
    import struct

    stock, mod = world
    textures = pathlib.Path(stock) / "ds_3dgen" / "3DView" / "textures"
    textures.mkdir(parents=True, exist_ok=True)

    # A minimal DXT5 header: 8x8, two mip levels.
    header = bytearray(128)
    header[0:4] = b"DDS "
    struct.pack_into("<I", header, 4, 124)
    struct.pack_into("<I", header, 8, 0x0002100F)       # CAPS|HEIGHT|WIDTH|PIXELFORMAT|MIPMAP|LINEARSIZE
    struct.pack_into("<I", header, 12, 8)               # height
    struct.pack_into("<I", header, 16, 8)               # width
    struct.pack_into("<I", header, 28, 2)               # mip count
    struct.pack_into("<I", header, 76, 32)              # pixelformat size
    struct.pack_into("<I", header, 80, 0x4)             # DDPF_FOURCC
    header[84:88] = b"DXT5"
    # 8x8 DXT5 is 64 bytes and its 4x4 mip another 16.  Sized properly because
    # the parser refuses a file whose last mip is not all there -- correct of
    # it, and a lazy fixture fails on exactly that.
    (textures / "x_nrm.dds").write_bytes(bytes(header) + b"\x00" * 80)

    s = Session()
    s.open_game(stock)

    assert s.texture_format("3DView/textures/x_nrm.dds") == "8x8 DXT5, 2 mip(s)"
    # Not a texture: no answer rather than a made-up one.
    assert s.texture_format("3DView/A.xml") is None
    assert s.texture_format("3DView/textures/gone.dds") is None


def test_export_asset_writes_a_model_as_glb_or_as_its_own_bytes(world, tmp_path):
    """Reported from real use: a model could only ever come out as `.3do`.

    The glTF exporter existed and only the CLI could reach it, so the one
    export people actually want -- the model, in something a DCC tool opens --
    was the one the app could not do.
    """
    from dsotools.convert import gltf
    from dsotools.formats import threedo

    stock, mod = world
    raw = threedo.build(_imported_model(2))
    objects = pathlib.Path(stock) / "ds_3dgen" / "3DView" / "objects"
    objects.mkdir(parents=True, exist_ok=True)
    (objects / "wing.3do").write_bytes(raw)

    s = Session()
    s.open_game(stock)

    plain = tmp_path / "wing.3do"
    s.export_asset("3DView/objects/wing.3do", str(plain))
    assert plain.read_bytes() == raw          # unchanged bytes, byte for byte

    glb = tmp_path / "wing.glb"
    s.export_asset("3DView/objects/wing.3do", str(glb))
    # The point of exporting glTF is bringing it back, so the test is the round
    # trip and not "a file appeared".
    assert threedo.build(gltf.import_glb(str(glb))) == raw


def test_export_asset_refuses_glb_it_cannot_honestly_write(world, tmp_path):
    """Refuse by name rather than write something the name misdescribes."""
    stock, mod = world
    s = Session()
    s.open_game(stock)

    with pytest.raises(DsoError):
        # `.gltf` is the JSON-plus-sidecars form; this converter writes the
        # single-file binary one, and a GLB called `.gltf` is a trap.
        s.export_asset("3DView/A.xml", str(tmp_path / "a.gltf"))
    with pytest.raises(DsoError):
        s.export_asset("3DView/A.xml", str(tmp_path / "a.glb"))


def test_preflight_glb_reports_structural_problems_in_the_bytes_it_would_write(
    world, monkeypatch
):
    """MDL002 on an import, before it lands.

    A model that has been through a DCC tool is the one place these rules
    realistically fire -- they fire on none of the 3,110 stock models -- so
    the import gate is where they earn their keep.
    """
    from dsotools.convert import gltf
    from dsotools.formats.threedo import Submesh

    stock, mod = world
    s = Session()
    s.open_game(stock)
    s.open_mod(mod)

    broken = _imported_model(2)
    # Second submesh draws the first one's triangle as well as its own.
    broken.lods[0].submeshes[1] = Submesh(1, 0, 2, 3, 3)
    monkeypatch.setattr(gltf, "import_glb", lambda p: broken)

    check = s.preflight_glb("3DView/objects/nope.3do", "whatever.glb")
    assert [code for code, _sev, _msg in check["problems"]] == ["MDL002"]

    monkeypatch.setattr(gltf, "import_glb", lambda p: _imported_model(2))
    assert s.preflight_glb("3DView/objects/nope.3do", "whatever.glb")["problems"] == []


def test_scene_detail_texture_vpaths_can_be_read_back(world):
    """The end of the bug: what the panel offers, Preview must be able to open."""
    stock, mod = world
    _variant_world(stock)
    s = Session()
    s.open_game(stock)

    detail = s.scene_detail("3DView/V.xml")
    vpath = detail["meshes"][0]["slots"][0]["texture_vpaths"][0]

    assert s.read_asset(vpath) == b"DDS "


def test_mesh_for_matches_the_scene_graph_path_not_the_node_name(world):
    """Names are not unique; paths are.

    Both meshes here are called ``main_``.  Keyed by name, the second variant's
    submesh reads the first's textures -- which is exactly what PlayerShip did
    across eleven bodies, in the direction that showed four texture slots for a
    submesh that has one.
    """
    from dso_app.session import mesh_for

    stock, mod = world
    _variant_world(stock)
    s = Session()
    s.open_game(stock)
    detail = s.scene_detail("3DView/V.xml")

    names = [m["name"] for m in detail["meshes"]]
    paths = [m["path"] for m in detail["meshes"]]
    assert names == ["main_", "main_"]          # the collision this guards
    assert len(set(paths)) == 2

    second = mesh_for(detail, _Call("main_", "bodys|body_1|main_", 0, []))
    assert second is not None
    assert second["model"] == "objects/y.3do"
    assert second["slots"][0]["textures"] == ["textures/b_col.dds"]


def test_mesh_for_falls_back_to_the_name_when_the_path_is_unknown(world):
    """A degraded match beats no match; it must not raise."""
    from dso_app.session import mesh_for

    stock, mod = world
    _variant_world(stock)
    s = Session()
    s.open_game(stock)
    detail = s.scene_detail("3DView/V.xml")

    found = mesh_for(detail, _Call("main_", "", 0, []))
    assert found is detail["meshes"][0]
    assert mesh_for(detail, _Call("nope", "no|such|path", 0, [])) is None
    assert mesh_for(None, _Call("main_", "bodys|body_0|main_", 0, [])) is None


def test_texture_refs_hands_out_paths_the_vfs_can_read(world):
    """The regression itself, stated as a rule.

    Every row the linked-assets panel offers must carry a vpath, because every
    action on it -- Preview, Export, Open in its tab -- reads the VFS.
    """
    from dso_app.session import mesh_for, texture_refs

    stock, mod = world
    _variant_world(stock)
    s = Session()
    s.open_game(stock)
    detail = s.scene_detail("3DView/V.xml")

    call = _Call("main_", "bodys|body_0|main_", 0,
                 ["textures/a_col.dds", "textures/gone.dds"])
    rows = texture_refs(mesh_for(detail, call), call)

    assert rows == [
        ("texture 0", "3DView/textures/a_col.dds", True),
        # Unresolvable: the raw reference is all there is to show, and the row
        # is marked so the panel says MISSING instead of offering a dead path.
        ("texture 1", "textures/gone.dds", False),
    ]
    assert s.read_asset(rows[0][1]) == b"DDS "


def test_texture_refs_labels_rows_with_the_shader_slot_name(world):
    """`t_Normal` says what the binding is for; `texture 3` says only where it
    sits.  The name comes from the `.bsd9`, so it is only used when there is
    one to read."""
    from dso_app.session import mesh_for, texture_refs

    stock, mod = world
    _variant_world(stock)
    s = Session()
    s.open_game(stock)
    detail = s.scene_detail("3DView/V.xml")

    call = _Call("main_", "bodys|body_0|main_", 0,
                 ["textures/a_col.dds", "textures/gone.dds"],
                 slot_names=["t_Color", "t_Normal"])
    rows = texture_refs(mesh_for(detail, call), call)
    assert [r[0] for r in rows] == ["t_Color", "t_Normal"]

    # No shader read: fall back to the position, which is all that is known.
    call.slot_names = []
    assert [r[0] for r in texture_refs(mesh_for(detail, call), call)] == [
        "texture 0", "texture 1"
    ]


def test_texture_refs_without_a_matching_slot_keeps_the_raw_reference(world):
    """No slot means no better answer -- but never a silent claim it resolves."""
    from dso_app.session import texture_refs

    call = _Call("main_", "bodys|body_0|main_", 0, ["textures/a_col.dds"])
    assert texture_refs(None, call) == [("texture 0", "textures/a_col.dds", False)]


def test_texture_refs_uses_the_first_slot_when_the_index_runs_past_the_end(world):
    """meshview's own fallback for a submesh with no effect of its own.

    Matching it keeps the panel describing the submesh the viewport drew.
    """
    from dso_app.session import mesh_for, texture_refs

    stock, mod = world
    _variant_world(stock)
    s = Session()
    s.open_game(stock)
    detail = s.scene_detail("3DView/V.xml")

    call = _Call("main_", "bodys|body_1|main_", 7, ["textures/b_col.dds"])
    rows = texture_refs(mesh_for(detail, call), call)
    assert rows == [("texture 0", "3DView/textures/b_col.dds", True)]


# --------------------------------------------------------------------------
# reset to stock, and the two files that are not mod content
# --------------------------------------------------------------------------


def test_the_dsoproj_sidecar_is_not_listed_as_mod_content(world):
    """It is this tool's file; the game never reads it.

    Listed among the author's files it invited the question "what does this
    override?", and the answer is nothing.
    """
    stock, mod = world
    with open(os.path.join(mod, ".dsoproj"), "w", encoding="utf-8") as fh:
        fh.write('{"schema": 1}')

    s = Session()
    s.open_game(stock)
    s.open_mod(mod)

    listed = {r["vpath"].lower() for r in s.mod_tree()}
    assert ".dsoproj" not in listed
    assert "darkstarmod.ini" not in listed        # the manifest, likewise
    assert "inifiles/new.ini" in listed           # real content still shows


def test_items_ini_is_flagged_required(world):
    """Its presence is load-bearing whatever its contents."""
    stock, mod = world
    s = Session()
    s.open_game(stock)
    s.open_mod(mod)

    rows = {r["vpath"].lower(): r for r in s.mod_tree()}
    assert rows["inifiles/items.ini"]["required"] is True
    assert rows["inifiles/goods.ini"]["required"] is False


def test_reset_to_stock_removes_the_override(world):
    """Removed, not overwritten.

    Writing the stock bytes back would leave a file byte-identical to stock --
    dead weight the app then reports as having no effect.
    """
    stock, mod = world
    s = Session()
    s.open_game(stock)
    s.open_mod(mod)
    assert "inifiles/goods.ini" in {r["vpath"].lower() for r in s.mod_tree()}

    assert s.can_reset_to_stock("inifiles/Goods.ini") is None
    removed = s.reset_to_stock("inifiles/Goods.ini")

    assert removed == {"inifiles/Goods.ini": "loose"}
    assert not os.path.exists(os.path.join(mod, "inifiles", "Goods.ini"))
    assert "inifiles/goods.ini" not in {r["vpath"].lower() for r in s.mod_tree()}
    # The stock file is untouched -- this only ever writes inside the mod.
    assert os.path.exists(os.path.join(stock, "ds_add", "inifiles", "Goods.ini"))


def test_reset_to_stock_tells_the_tabs_and_drops_the_stale_preview(world):
    """The notification the Models tab rebuilds from.

    Reported from real use: after a reset the Models tab still showed the
    texture that had just been removed.  The tab's own handling was the bug,
    but this is the contract underneath it -- a listener must hear about the
    removal, and the decoded-preview cache must not keep serving the picture
    of a file that is gone.
    """
    stock, mod = world
    s = Session()
    s.open_game(stock)
    s.open_mod(mod)

    heard = []
    s.subscribe(heard.append)
    s._preview_cache["inifiles/goods.ini"] = "stale"

    s.reset_to_stock("inifiles/Goods.ini")

    assert "mod" in heard
    assert not s._preview_cache


def test_reset_to_stock_refuses_items_ini(world):
    """The one file whose removal silently unlists the whole mod."""
    stock, mod = world
    s = Session()
    s.open_game(stock)
    s.open_mod(mod)

    why = s.can_reset_to_stock("inifiles/items.ini")
    assert why and "items.ini" in why
    with pytest.raises(DsoError):
        s.reset_to_stock("inifiles/items.ini")
    assert os.path.exists(os.path.join(mod, "inifiles", "items.ini"))


def test_reset_to_stock_refuses_an_addition(world):
    """No stock version means nothing to reset to; say so rather than delete."""
    stock, mod = world
    s = Session()
    s.open_game(stock)
    s.open_mod(mod)

    why = s.can_reset_to_stock("inifiles/New.ini")
    assert why and "adds" in why
    with pytest.raises(DsoError):
        s.reset_to_stock("inifiles/New.ini")
    assert os.path.exists(os.path.join(mod, "inifiles", "New.ini"))


def test_reset_to_stock_removes_from_the_zip_too(world):
    """3DView content lives in user_data.zip, so removal has to rewrite it."""
    stock, mod = world
    view = os.path.join(stock, "ds_3dgen", "3DView")
    os.makedirs(view, exist_ok=True)
    with open(os.path.join(view, "A.xml"), "wb") as fh:
        fh.write(SCENE)

    s = Session()
    s.open_game(stock)
    s.open_mod(mod)
    assert "3dview/a.xml" in {r["vpath"].lower() for r in s.mod_tree()}

    assert s.reset_to_stock("3DView/A.xml") == {"3DView/A.xml": "zip"}

    with zipfile.ZipFile(os.path.join(mod, "user_data.zip")) as zf:
        assert "3DView/A.xml" not in zf.namelist()
    assert "3dview/a.xml" not in {r["vpath"].lower() for r in s.mod_tree()}


def test_reset_to_stock_notifies_so_every_tab_refreshes(world):
    stock, mod = world
    s = Session()
    s.open_game(stock)
    s.open_mod(mod)
    seen = []
    s.subscribe(seen.append)

    s.reset_to_stock("inifiles/Goods.ini")
    assert "mod" in seen


def test_reset_to_stock_forgets_the_provenance(world):
    """A record claiming the mod builds a file it no longer has is a lie."""
    stock, mod = world
    s = Session()
    s.open_game(stock)
    s.open_mod(mod)
    s.project.record("inifiles/Goods.ini", source="somewhere.png")
    assert s.project.provenance_of("inifiles/Goods.ini")

    s.reset_to_stock("inifiles/Goods.ini")
    assert s.project.provenance_of("inifiles/Goods.ini") == {}


# --------------------------------------------------------------------------
# replacing bound assets
# --------------------------------------------------------------------------


def test_replace_refuses_to_invent_a_dds(world, tmp_path):
    """There is no DDS writer here, and the refusal has to say what to do.

    Re-encoding would mean choosing a DXT compressor and silently changing
    mipmaps and quality on the author's behalf.
    """
    stock, mod = world
    s = Session()
    s.open_game(stock)
    s.open_mod(mod)
    png = tmp_path / "new.png"
    png.write_bytes(b"\x89PNG\r\n")

    with pytest.raises(DsoError) as exc:
        s.replace_asset("3DView/textures/new.dds", str(png))

    assert ".dds" in str(exc.value).lower()


def test_replace_installs_a_dds_byte_for_byte(world, tmp_path):
    stock, mod = world
    s = Session()
    s.open_game(stock)
    s.open_mod(mod)
    src = tmp_path / "hull.dds"
    src.write_bytes(b"DDS " + bytes(range(64)))

    routed = s.replace_asset("3DView/textures/new.dds", str(src))

    assert routed == {"3DView/textures/new.dds": "zip"}
    with zipfile.ZipFile(os.path.join(mod, "user_data.zip")) as zf:
        assert zf.read("3DView/textures/new.dds") == src.read_bytes()


def test_replace_records_where_it_came_from(world, tmp_path):
    """Provenance is what makes a mod rebuildable rather than just shippable."""
    stock, mod = world
    s = Session()
    s.open_game(stock)
    s.open_mod(mod)
    src = tmp_path / "hull.dds"
    src.write_bytes(b"DDS ")

    s.replace_asset("3DView/textures/new.dds", str(src))

    assert s.project.provenance_of("3DView/textures/new.dds")["source"] == str(src)


def test_replace_without_a_mod_raises(world, tmp_path):
    stock, mod = world
    s = Session()
    s.open_game(stock)
    src = tmp_path / "x.dds"
    src.write_bytes(b"DDS ")

    with pytest.raises(DsoError):
        s.replace_asset("3DView/textures/new.dds", str(src))


def test_used_by_says_the_index_is_missing_rather_than_returning_nothing(world):
    """An empty answer and an unanswerable question must not look alike."""
    stock, mod = world
    s = Session()
    s.open_game(stock)

    with pytest.raises(DsoError) as exc:
        s.used_by("3DView/A.xml")

    assert "index" in str(exc.value).lower()


def test_describe_assets_marks_what_does_not_resolve(world):
    stock, mod = world
    s = Session()
    s.open_game(stock)
    s.open_mod(mod)

    rows = s.describe_assets([
        ("model", "3DView/A.xml"),
        ("texture 0", "textures/absent.dds"),
        ("texture 1", None),
    ])

    assert rows[0]["resolved"] and rows[0]["source"]
    assert not rows[1]["resolved"]
    assert rows[2]["vpath"] is None


# --------------------------------------------------------------------------
# settings, and the notices they silence
# --------------------------------------------------------------------------


def test_settings_round_trip(tmp_path):
    from dso_app.settings import Settings

    path = str(tmp_path / "s.json")
    s = Settings(path)
    s.set("a", 1)
    s.hide_notice("replace_dds")

    again = Settings(path)
    assert again.get("a") == 1
    assert again.notice_hidden("replace_dds") is True
    assert again.notice_hidden("something_else") is False


def test_a_corrupt_settings_file_falls_back_to_defaults(tmp_path):
    """Losing a preference is a nuisance; refusing to start is absurd."""
    from dso_app.settings import Settings

    path = tmp_path / "s.json"
    path.write_text("{not json at all", encoding="utf-8")

    s = Settings(str(path))
    assert s.get("anything") is None
    assert s.notice_hidden("replace_dds") is False
    assert s.save() is True                      # and it repairs itself


def test_settings_never_raise_when_the_folder_is_unwritable(tmp_path):
    from dso_app.settings import Settings

    s = Settings(str(tmp_path / "no" / "such" / "dir" / "s.json"))
    s.set("a", 1)                                # must not raise
    assert s.get("a") == 1


def test_show_all_notices_undoes_every_suppression(tmp_path):
    from dso_app.settings import Settings

    path = str(tmp_path / "s.json")
    s = Settings(path)
    s.set("keep_me", "yes")
    s.hide_notice("one")
    s.hide_notice("two")

    s.show_all_notices()
    assert not s.notice_hidden("one") and not s.notice_hidden("two")
    assert s.get("keep_me") == "yes"             # ordinary settings survive
    assert not Settings(path).notice_hidden("one")


def test_the_settings_file_sits_next_to_the_app_when_that_is_writable():
    from dso_app import frozen, settings as settings_mod

    path = settings_mod.settings_path()
    assert path.endswith(settings_mod.FILENAME)
    # Either beside the app, or the per-user folder when that is not writable.
    assert (
        os.path.dirname(path) == frozen.install_dir()
        or os.path.dirname(path) == frozen.user_data_dir()
        or os.path.dirname(path) == tempfile.gettempdir()
    )


# --------------------------------------------------------------------------
# interface layouts (.screen)
#
# The chain the Interface tab follows, and the edit it exists to make:
#     element -> scripts/X.anim -> images/Y.aim -> atlas page + rectangle
# --------------------------------------------------------------------------


def _screen_bytes(elements, declared=None, name="TESTSCREEN"):
    """A .screen built the way tests/test_screen.py builds them."""
    from test_screen import a_screen

    return a_screen(b"".join(elements), declared=declared, name=name)


def _screen_world(stock, mod):
    from test_screen import block, element

    scripts = pathlib.Path(stock) / "ds_interface" / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    body = _screen_bytes(
        [
            element("CStatic", "BG", (0, 0, 640, 480),
                    block(b"scripts\\Frame.anim\x00" + b"\x00" * 40)),
            element("CButton", "OK", (100, 200, 96, 35)),
        ],
        declared=2,
    )
    (scripts / "MAIN_1024x768.screen").write_bytes(body)

    s = Session()
    s.open_game(stock)
    s.open_mod(mod)
    return s


def test_screens_are_listed_with_where_they_come_from(world):
    stock, mod = world
    s = _screen_world(stock, mod)

    rows = {r["name"]: r for r in s.screens()}

    assert "MAIN_1024x768.screen" in rows
    assert rows["MAIN_1024x768.screen"]["in_mod"] is False


def test_open_screen_reports_every_element_with_its_rectangle(world):
    stock, mod = world
    s = _screen_world(stock, mod)

    data = s.open_screen("scripts/MAIN_1024x768.screen")

    assert data["name"] == "TESTSCREEN"
    assert [e["class"] for e in data["elements"]] == ["CStatic", "CButton"]
    assert data["elements"][1]["rect"] == (100, 200, 96, 35)
    # The reference is reported even when the .anim behind it is absent: an
    # unresolvable drawable is a finding, not a reason to hide the element.
    assert data["elements"][0]["references"] == ["scripts\\Frame.anim"]
    assert data["elements"][0]["drawables"][0]["anim_vpath"] is None


def test_moving_an_element_rewrites_only_its_rectangle(world):
    """What the canvas does when you drag a row's numbers.

    The parser keeps every element's bytes verbatim, so a moved button changes
    the four integers that say where it is -- which is what keeps a 216 KB
    layout's diff readable.
    """
    stock, mod = world
    s = _screen_world(stock, mod)
    before = s.read_asset("scripts/MAIN_1024x768.screen")

    s.set_screen_rects("scripts/MAIN_1024x768.screen", [(1, (120, 200, 96, 35))])

    after = s.read_asset("scripts/MAIN_1024x768.screen")
    assert len(after) == len(before)
    assert sum(1 for a, b in zip(before, after) if a != b) == 1
    reread = s.open_screen("scripts/MAIN_1024x768.screen")
    assert reread["elements"][1]["rect"] == (120, 200, 96, 35)
    assert {r["name"]: r["in_mod"] for r in s.screens()}["MAIN_1024x768.screen"]


def test_moving_an_element_that_is_not_there_is_refused(world):
    stock, mod = world
    s = _screen_world(stock, mod)

    with pytest.raises(DsoError):
        s.set_screen_rects("scripts/MAIN_1024x768.screen", [(9, (0, 0, 1, 1))])
    # And nothing was written on the way to refusing.
    assert {r["name"]: r["in_mod"] for r in s.screens()}["MAIN_1024x768.screen"] is False


def test_an_element_that_draws_nothing_says_so_rather_than_guessing(world):
    stock, mod = world
    s = _screen_world(stock, mod)

    with pytest.raises(DsoError):
        s.screen_element_image("scripts/MAIN_1024x768.screen", 1)


def _nested_screen_world(stock, mod):
    """A screen holding a slider and the four sub-controls the engine builds.

    Two top-level elements, six records -- the shape of 19 of the 83 shipped
    layouts.
    """
    from test_screen import element

    scripts = pathlib.Path(stock) / "ds_interface" / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    body = _screen_bytes(
        [
            element("CStatic", "BG", (0, 0, 640, 480)),
            element("CSlider", "Panel_VSlider", (444, 59, 26, 297)),
            element("CStatic", "Panel_VSlider_Background", (7, -1, 7, 398)),
            element("CButton", "Panel_VSlider_Button +", (0, 372, 17, 18)),
            element("CButton", "Panel_VSlider_Button -", (1, -14, 14, 15)),
            element("CButton", "Panel_VSlider_Button Drag", (2, 48, 17, 30)),
        ],
        declared=2,
    )
    (scripts / "NEST_1024x768.screen").write_bytes(body)

    s = Session()
    s.open_game(stock)
    s.open_mod(mod)
    return s


def test_a_child_is_reported_where_it_is_drawn_not_where_it_is_stored(world):
    """``Button Drag`` is stored at (2, 48) and belongs on a slider at (444, 59).

    Read flat it lands in the top-left corner of the window, which is what made
    MOD_MANAGER look like it was missing most of its elements.
    """
    stock, mod = world
    s = _nested_screen_world(stock, mod)

    data = s.open_screen("scripts/NEST_1024x768.screen")
    drag = data["elements"][5]

    assert drag["rect"] == (2, 48, 17, 30)
    assert drag["origin"] == (446, 107)
    assert drag["parent"] == 1
    assert drag["depth"] == 1
    assert data["elements"][0]["parent"] == -1
    assert data["tree_consistent"] is True


def test_moving_a_child_is_refused_rather_than_silently_kept(world):
    """The engine places it, so an edit here would not hold in the game."""
    stock, mod = world
    s = _nested_screen_world(stock, mod)

    with pytest.raises(DsoError) as caught:
        s.set_screen_rects("scripts/NEST_1024x768.screen", [(5, (10, 10, 17, 30))])

    assert "Panel_VSlider" in str(caught.value)
    assert s.read_asset("scripts/NEST_1024x768.screen") == (
        pathlib.Path(stock) / "ds_interface" / "scripts"
        / "NEST_1024x768.screen").read_bytes()


def test_the_element_a_button_draws_is_the_one_it_shows_at_rest(world):
    """A button lists its disabled artwork first; that is not its resting state."""
    from test_screen import block, element

    stock, mod = world
    scripts = pathlib.Path(stock) / "ds_interface" / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    sep = chr(92)                      # the separator the game writes
    states = block(
        f"scripts{sep}ND_B_disabled.anim".encode("cp1252") + bytes(1)
        + f"scripts{sep}ND_B_normal.anim".encode("cp1252") + bytes(1)
        + f"scripts{sep}ND_B_pressed.anim".encode("cp1252") + bytes(40)
    )
    (scripts / "STATE_1024x768.screen").write_bytes(_screen_bytes(
        [element("CButton", "OK", (10, 10, 96, 35), states)], declared=1))

    s = Session()
    s.open_game(stock)
    s.open_mod(mod)
    element_row = s.open_screen("scripts/STATE_1024x768.screen")["elements"][0]

    assert element_row["resting"] == 1
    assert element_row["references"][element_row["resting"]].endswith(
        "ND_B_normal.anim")


# --------------------------------------------------------------------------
# files the mod puts into the game installation
#
# No .cpr archive holds a single lua/ entry, so a mod that changes the shared
# mission libraries has to overwrite the installation -- and the original has
# no other copy anywhere.  See specs/mod_installation.md.
# --------------------------------------------------------------------------


def _root_world(stock, mod):
    """A mod with an install-folder payload, and a stock library to displace."""
    from dsotools import rootfiles

    library = pathlib.Path(stock) / "lua" / "mission" / "MissionLib.lua"
    library.parent.mkdir(parents=True, exist_ok=True)
    library.write_text("-- stock\n", encoding="utf-8")

    payload = pathlib.Path(mod) / rootfiles.PAYLOAD_DIR / "lua" / "mission"
    payload.mkdir(parents=True, exist_ok=True)
    (payload / "MissionLib.lua").write_text("-- from the mod\n", encoding="utf-8")
    (payload / "BattleLibEx.lua").write_text("-- added\n", encoding="utf-8")

    s = Session()
    s.open_game(stock)
    s.open_mod(mod)
    return s


def test_the_payload_is_listed_with_whether_it_is_installed(world):
    stock, mod = world
    s = _root_world(stock, mod)

    rows = {r["path"]: r for r in s.root_payload()}

    assert set(rows) == {"lua/mission/MissionLib.lua", "lua/mission/BattleLibEx.lua"}
    assert rows["lua/mission/MissionLib.lua"]["installed"] is False
    assert rows["lua/mission/MissionLib.lua"]["in_game"] is True    # stock is there


def test_installing_and_uninstalling_returns_the_installation_to_stock(world):
    stock, mod = world
    s = _root_world(stock, mod)
    library = pathlib.Path(stock) / "lua" / "mission" / "MissionLib.lua"

    installed = s.install_root_files()

    assert library.read_text() == "-- from the mod\n"
    assert installed["backed_up"] == ["lua/mission/MissionLib.lua"]
    assert [m["name"] for m in s.installed_root_mods()] == [s.mod.name]

    removed = s.uninstall_root_files()

    assert library.read_text() == "-- stock\n"
    assert removed["clean"] and s.installed_root_mods() == []


def test_installing_records_the_manifest_in_the_project_file(world):
    """So the mod carries what it delivers, not just what it happens to hold."""
    stock, mod = world
    s = _root_world(stock, mod)

    s.install_root_files()

    from dsotools.project import ProjectFile
    saved = ProjectFile.load(mod).root_files
    assert set(saved) == {"lua/mission/MissionLib.lua", "lua/mission/BattleLibEx.lua"}
    from dsotools import rootfiles
    on_disk = (pathlib.Path(mod) / rootfiles.PAYLOAD_DIR / "lua" / "mission"
               / "BattleLibEx.lua").stat().st_size
    assert saved["lua/mission/BattleLibEx.lua"]["size"] == on_disk
    assert saved["lua/mission/MissionLib.lua"]["sha256"]


def test_a_payload_already_in_place_can_be_adopted(world):
    """The state this tool usually meets: copied in by hand, original gone."""
    stock, mod = world
    s = _root_world(stock, mod)
    library = pathlib.Path(stock) / "lua" / "mission" / "MissionLib.lua"
    library.write_text("-- from the mod\n", encoding="utf-8")     # as if by hand

    unclaimed = s.unclaimed_root_files()
    assert [u["path"] for u in unclaimed] == ["lua/mission/MissionLib.lua"]
    assert unclaimed[0]["identical"] is True

    s.adopt_root_files([u["path"] for u in unclaimed])

    assert s.installed_root_mods()[0]["adopted"] is True
    # Nothing was backed up, so nothing claims to be restorable.
    assert s.installed_root_mods()[0]["restorable"] == 0


def test_importing_a_game_root_archive_skips_the_modders_lock_file(world, tmp_path):
    stock, mod = world
    s = _root_world(stock, mod)
    archive = tmp_path / "Lua.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("lua/mission/Tools.lua", "-- tools\n")
        z.writestr("lua/mission/sync.ffs_lock", "lock")

    written = s.import_root_zip(str(archive))

    assert written == ["lua/mission/Tools.lua"]


def test_the_payload_needs_a_game_before_it_can_be_installed(world):
    stock, mod = world
    _root_world(stock, mod)
    s = Session()
    s.open_mod(mod)

    with pytest.raises(DsoError):
        s.install_root_files()


# --------------------------------------------------------------------------
# Lua scripting
#
# Two kinds of script, delivered completely differently -- a mission script
# from the mod's scripts/ folder, a library only from the game installation.
# Where an edit lands is the thing worth testing.
# --------------------------------------------------------------------------


def _lua_world(stock, mod):
    library = pathlib.Path(stock) / "lua" / "mission" / "MissionLib.lua"
    library.parent.mkdir(parents=True, exist_ok=True)
    library.write_text(
        "MissionLib = {}\n"
        "MissionLib.Rnd = function( n )\n"
        "\treturn NObject.GetPosition( { Object = n } )\n"
        "end\n",
        encoding="utf-8")

    scripts = pathlib.Path(mod) / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / "mymission.lua").write_text(
        'NScript.Register( { Name = "X" } )\n', encoding="utf-8")

    s = Session()
    s.open_game(stock)
    s.open_mod(mod)
    return s


def test_scripts_are_listed_with_the_kind_that_decides_their_delivery(world):
    stock, mod = world
    s = _lua_world(stock, mod)

    rows = {r["key"]: r for r in s.scripts()}

    assert rows["mod:scripts/mymission.lua"]["kind"] == "mission script"
    assert rows["root:lua/mission/MissionLib.lua"]["kind"] == "library"
    assert rows["root:lua/mission/MissionLib.lua"]["where"] == "game folder"


def test_editing_a_library_saves_into_the_mod_payload_not_the_game(world):
    """The whole point: the game folder is never written by an edit.

    A library exists in no archive, so overwriting it in place would be
    unrecoverable.  It goes into the mod's root/ payload and is installed from
    there, with a backup.
    """
    stock, mod = world
    s = _lua_world(stock, mod)
    live = pathlib.Path(stock) / "lua" / "mission" / "MissionLib.lua"
    before = live.read_bytes()

    written = s.save_script("root:lua/mission/MissionLib.lua", "-- mine\n")

    assert live.read_bytes() == before, "the installation must be untouched"
    assert pathlib.Path(written) == (pathlib.Path(mod) / "root" / "lua"
                                     / "mission" / "MissionLib.lua")
    # And it is now the mod's, so the tab offers the mod's copy next time.
    rows = {r["key"]: r for r in s.scripts()}
    assert rows["root:lua/mission/MissionLib.lua"]["where"] == "mod payload"


def test_editing_a_mission_script_saves_in_place(world):
    stock, mod = world
    s = _lua_world(stock, mod)

    written = s.save_script("mod:scripts/mymission.lua", "-- edited\n")

    assert pathlib.Path(written) == pathlib.Path(mod) / "scripts" / "mymission.lua"
    assert s.read_script("mod:scripts/mymission.lua") == "-- edited\n"


def test_a_script_cannot_be_saved_without_a_mod_to_save_it_into(world):
    stock, _mod = world
    s = Session()
    s.open_game(stock)

    with pytest.raises(DsoError):
        s.save_script("root:lua/mission/MissionLib.lua", "-- nope\n")


def test_a_call_the_reference_does_not_document_is_reported(world):
    stock, mod = world
    s = _lua_world(stock, mod)
    if not s.lua_api():
        pytest.skip("this build ships no API database")

    findings = s.check_script("NComm.AddMessage( {} )\nNComm.Nonesuch( {} )\n")

    assert [f["symbol"] for f in findings] == ["NComm.Nonesuch"]
    assert findings[0]["line"] == 2


def test_a_call_in_a_comment_is_not_a_call(world):
    """Two of the stock hits were commented-out lines in MAIN_MENU.lua."""
    stock, mod = world
    s = _lua_world(stock, mod)
    if not s.lua_api():
        pytest.skip("this build ships no API database")

    assert s.check_script("-- NComm.Nonesuch( {} )\n") == []
    assert s.check_script("--[[\nNComm.Nonesuch( {} )\n]]\n") == []


def test_a_function_the_lua_sources_define_themselves_is_not_undocumented(world):
    """``MissionLib.Rnd = function(...)`` is how the shipped libraries write it.

    Without resolving that, this check reports 153 hits on stock scripts
    instead of 15, and nobody would leave it switched on.
    """
    stock, mod = world
    s = _lua_world(stock, mod)
    if not s.lua_api():
        pytest.skip("this build ships no API database")

    assert s.check_script("MissionLib.Rnd( 3 )\n") == []


# --------------------------------------------------------------------------
# checking a script against what the executable really registers
#
# The reference and the build disagree in both directions: five documented
# functions are absent, thirty undocumented ones are present, and two that are
# present do nothing at all.  See specs/mod_packaging.md.
# --------------------------------------------------------------------------


def _checker(stock, mod):
    s = _lua_world(stock, mod)
    if not s.lua_api() or not s._engine_table():
        pytest.skip("this build ships no API databases")
    return s


def test_a_documented_function_the_build_lacks_is_an_error(world):
    """`NGUI.Enable` is in the manual and in no executable."""
    stock, mod = world
    s = _checker(stock, mod)

    found = {f["symbol"]: f for f in s.check_script("NGUI.Enable( { } )\n")}

    assert found["NGUI.Enable"]["kind"] == "absent"
    assert "does not register" in found["NGUI.Enable"]["detail"]


def test_a_function_that_does_nothing_is_reported_as_such(world):
    """NDebug.Message accepts the call and discards it."""
    stock, mod = world
    s = _checker(stock, mod)

    found = {f["symbol"]: f for f in s.check_script('NDebug.Message( { Message = "x" } )\n')}

    assert found["NDebug.Message"]["kind"] == "stub"


def test_an_undocumented_but_real_function_is_not_reported(world):
    """Thirty exist that nobody wrote about; flagging them is noise."""
    stock, mod = world
    s = _checker(stock, mod)

    assert s.check_script("NPlayer.FirePlasma( { } )\n") == []


def test_a_literal_where_a_string_id_belongs_is_reported(world):
    """The call succeeds and shows nothing -- the worst kind of failure."""
    stock, mod = world
    s = _checker(stock, mod)

    found = s.check_script('NGUI.ShowInfoText( { Text = "DSO TEST" } )\n')

    assert [f["kind"] for f in found] == ["literal"]
    assert "StringId" in found[0]["detail"]


def test_a_real_string_id_is_left_alone(world):
    stock, mod = world
    s = _checker(stock, mod)

    assert s.check_script('NGUI.ShowInfoText( { Text = "IDM_NEW_EMAIL" } )\n') == []


def test_a_typo_is_still_caught(world):
    stock, mod = world
    s = _checker(stock, mod)

    found = s.check_script("NComm.AddMessag( { } )\n")

    assert [f["kind"] for f in found] == ["unknown"]


def test_the_stock_scripts_raise_only_stub_findings(world):
    """Validating against the build removes every false positive.

    Against the documentation alone the same scan reported fifteen calls as
    unknown; all fifteen are real functions the 2006 reference omits.  What
    remains is the game's own use of NDebug.Message, which really does nothing.
    """
    stock, _mod = world
    s = Session()
    s.open_game(stock)
    if not s.lua_api() or not s._engine_table():
        pytest.skip("this build ships no API databases")
    library = pathlib.Path(stock) / "lua" / "mission" / "MissionLib.lua"
    library.parent.mkdir(parents=True, exist_ok=True)
    library.write_text("MissionLib = {}\n"
                       "MissionLib.Helper = function( n )\n"
                       "\tNObject.GetPosition( { Object = n } )\n"
                       "\tNDebug.Message( { Message = \"x\" } )\n"
                       "end\n", encoding="utf-8")

    kinds = {f["kind"] for row in s.scripts()
             for f in s.check_script(s.read_script(row["key"]))}

    assert kinds <= {"stub"}, f"unexpected findings: {kinds}"


def test_non_stock_files_names_who_is_tracking_each_difference(world, monkeypatch):
    """The question the Project tab asks: what is not stock, and who owns it?

    A difference nobody tracks is the dangerous kind -- nothing can put it
    back -- so it must be distinguishable from one a mod installed through
    this tool.
    """
    from dsotools import baseline

    stock, mod = world
    s = _root_world(stock, mod)
    s.install_root_files()          # the mod now owns lua/mission/MissionLib.lua

    # A second file nobody has ever heard of.
    stray = pathlib.Path(stock) / "lua" / "mission" / "Stray.lua"
    stray.write_text("-- by hand\n", encoding="utf-8")

    monkeypatch.setattr(baseline, "bundled", lambda: {
        "schema": 1, "roots": ["lua"], "volatile": [],
        "build": {"data_fingerprint": "x", "editions": {}},
        "shared": {}, "editions": {},
    })

    rows = {r["path"]: r for r in s.non_stock_files()}

    assert rows["lua/mission/missionlib.lua"]["owner"] == s.mod.name
    assert rows["lua/mission/stray.lua"]["owner"] is None
    assert {r["state"] for r in rows.values()} == {"added"}


# --------------------------------------------------------------------------
# string tables
# --------------------------------------------------------------------------
#
# The one asymmetry worth remembering: a .res stores hashed ids, so the
# authored pairs have to survive somewhere else or the mod's text becomes
# uneditable.  These tests pin that the project is that somewhere.


def _string_world(world):
    stock, mod = world
    s = Session()
    s.open_game(stock)
    s.open_mod(mod)
    return s


def test_a_mod_with_no_text_has_no_string_rows(world):
    assert _string_world(world).strings() == []


def test_saving_text_writes_the_table_and_records_the_ids(world):
    from dsotools.formats import res as resfmt

    s = _string_world(world)
    written = s.save_strings([("ID_HELLO", "Hello there"), ("ID_BYE", "Goodbye")])
    assert os.path.basename(written) == "user_strings.res"

    table = resfmt.parse(open(written, "rb").read())
    assert table.text("ID_HELLO") == "Hello there"
    assert table.text("ID_BYE") == "Goodbye"

    # Reopening the mod recovers the ids, which only the project can supply.
    again = Session()
    again.open_mod(s.mod.root)
    assert [(r["id"], r["text"]) for r in again.strings()] == [
        ("ID_HELLO", "Hello there"), ("ID_BYE", "Goodbye")]
    assert all(r["in_table"] for r in again.strings())


def test_an_authored_id_missing_from_the_built_table_is_flagged(world):
    s = _string_world(world)
    s.save_strings([("ID_HELLO", "Hello")])
    s.project.record_strings([("ID_HELLO", "Hello"), ("ID_NEW", "not built yet")])
    rows = {r["id"]: r for r in s.strings()}
    assert rows["ID_HELLO"]["in_table"] is True
    assert rows["ID_NEW"]["in_table"] is False


def test_a_key_no_authored_id_accounts_for_is_reported_as_orphaned(world):
    from dsotools.formats import res as resfmt

    s = _string_world(world)
    s.save_strings([("ID_HELLO", "Hello")])
    # Simulate a hand-edited table: an extra key with no id behind it.
    full = os.path.join(s.mod.root, "strings", "user_strings.res")
    table = resfmt.parse(open(full, "rb").read())
    table.entries.append(resfmt.StringEntry(0x1234, "mystery"))
    open(full, "wb").write(resfmt.build(table))
    assert s.orphan_strings() == [0x1234]


def test_a_collision_is_refused_at_save_time(world):
    s = _string_world(world)
    with pytest.raises(DsoError):
        s.save_strings([("ID_BRCBHZSVVRVG", "one"), ("ID_CICQUQNQSTPJ", "two")])


def test_saving_text_needs_a_mod(world):
    stock, _mod = world
    s = Session()
    s.open_game(stock)
    with pytest.raises(DsoError):
        s.save_strings([("ID_X", "x")])


def test_stock_text_can_be_looked_up_by_id(world, tmp_path):
    from dsotools.formats import res as resfmt

    stock, mod = world
    target = pathlib.Path(stock) / "ds_add" / "strings" / "ENG"
    target.mkdir(parents=True)
    (target / "global.res").write_bytes(
        resfmt.build(resfmt.from_pairs([("ID_STOCK", "stock text")])))
    s = Session()
    s.open_game(stock)
    s.open_mod(mod)
    assert s.stock_string("ID_STOCK") == "stock text"
    assert s.stock_string("ID_NOT_A_THING") is None


def test_stock_text_is_unavailable_without_a_game(world):
    _stock, mod = world
    s = Session()
    s.open_mod(mod)
    assert s.stock_string("ID_STOCK") is None

# --------------------------------------------------------------------------
# missions
# --------------------------------------------------------------------------
#
# There is no "replace a mission" call: registering an existing Name overwrites
# that mission. So the dangerous case is doing it by accident, and these tests
# pin that the session distinguishes the two intentions.


def _mission_session(world):
    _stock, mod = world
    s = Session()
    s.open_mod(mod)
    return s


def test_the_stock_missions_are_listed_without_a_game_folder(world):
    """The table is shipped data, not something read from an installation."""
    rows = _mission_session(world).stock_missions()
    if not rows:
        pytest.skip("this build ships no stock_missions.json")
    assert len(rows) == 150
    assert all(r["name"] and r["type"] for r in rows)
    assert all(r["overridden"] is None for r in rows)


def test_overriding_a_stock_mission_writes_a_script_named_after_the_mission(world):
    s = _mission_session(world)
    if not s.stock_missions():
        pytest.skip("this build ships no stock_missions.json")
    written = s.create_mission_override("BAR_006")
    assert os.path.basename(written) == "BAR_006.lua"
    text = open(written, encoding="utf-8").read()
    assert 'Name = "BAR_006"' in text
    assert "Type = MTYPE_BAR" in text
    assert s.registered_in_mod() == {"BAR_006": "BAR_006.lua"}
    row = [r for r in s.stock_missions() if r["name"] == "BAR_006"][0]
    assert row["overridden"] == "BAR_006.lua"


def test_overriding_something_that_is_not_a_stock_mission_is_an_error(world):
    s = _mission_session(world)
    if not s.stock_missions():
        pytest.skip("this build ships no stock_missions.json")
    with pytest.raises(DsoError):
        s.create_mission_override("NOT_A_STOCK_MISSION")


def test_a_new_mission_that_clashes_with_a_stock_name_is_refused(world):
    """Doing this by accident silently disables a stock mission."""
    s = _mission_session(world)
    if not s.stock_missions():
        pytest.skip("this build ships no stock_missions.json")
    with pytest.raises(DsoError):
        s.create_mission("BAR_006")
    assert not os.path.exists(os.path.join(s.mod.root, "scripts", "BAR_006.lua"))


def test_a_new_mission_writes_the_states_that_were_asked_for(world):
    s = _mission_session(world)
    written = s.create_mission("MY_PATROL", type="MTYPE_SPACE", group=2,
                               states=["Init", "Wing"])
    text = open(written, encoding="utf-8").read()
    assert "Type = MTYPE_SPACE" in text
    assert "Group = 2" in text
    assert '"Wing"' in text and '"Create"' not in text


def test_an_existing_script_is_not_overwritten_by_accident(world):
    s = _mission_session(world)
    s.create_mission("MY_PATROL")
    with pytest.raises(DsoError):
        s.create_mission("MY_PATROL")
    s.create_mission("MY_PATROL", overwrite=True)      # asked for, so allowed


def test_a_mission_needs_a_mod_to_be_written_into(world):
    stock, _mod = world
    s = Session()
    s.open_game(stock)
    with pytest.raises(DsoError):
        s.create_mission("MY_PATROL")


def test_the_state_vocabulary_comes_from_the_stock_missions(world):
    states = _mission_session(world).mission_states()
    assert "Init" in states and "Create" in states
    assert states == sorted(states)


# --------------------------------------------------------------------------
# sound
# --------------------------------------------------------------------------
#
# The engine plays what a database declares, not what is in a folder, so every
# operation here is really an edit to user_sounds.xml with a file copy attached.


def _wav(path, frames=22050, rate=22050, channels=1):
    import struct

    data = b"\0" * (frames * channels * 2)
    chunks = (struct.pack("<4sIHHIIHH", b"fmt ", 16, 1, channels, rate,
                          rate * channels * 2, channels * 2, 16)
              + struct.pack("<4sI", b"data", len(data)) + data)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"RIFF" + struct.pack("<I", 4 + len(chunks)) + b"WAVE" + chunks)
    return str(path)


def test_a_mod_with_no_database_lists_nothing(world):
    _stock, mod = world
    s = Session()
    s.open_mod(mod)
    assert s.sounds() == []


def test_adding_a_sound_copies_the_file_and_declares_it(world, tmp_path):
    _stock, mod = world
    s = Session()
    s.open_mod(mod)
    source = _wav(tmp_path / "Beep.wav")

    row = s.add_sound(source, name="Beep", kind="Sound2D", group="USER")

    assert row["reference"] == "USER/Beep"
    assert row["exists"] and row["where"] == "mod"
    assert os.path.exists(os.path.join(mod, "sound", "sfx(2d)", "grp_USER", "Beep.wav"))
    assert os.path.exists(os.path.join(mod, "user_sounds.xml"))
    # Reopening finds it, so it really went into the database.
    again = Session()
    again.open_mod(mod)
    assert [r["reference"] for r in again.sounds()] == ["USER/Beep"]


def test_the_declared_metadata_comes_from_the_file(world, tmp_path):
    """The engine reads rate and length from the database, not the file."""
    _stock, mod = world
    s = Session()
    s.open_mod(mod)
    s.add_sound(_wav(tmp_path / "Beep.wav", frames=11025, rate=22050),
                name="Beep", group="USER")
    text = open(os.path.join(mod, "user_sounds.xml"), encoding="latin-1").read()
    assert 'Freq="22050"' in text
    assert 'Duration=":11025"' in text
    assert 'Channels="1"' in text


def test_a_sound_that_is_not_wav_or_mp3_is_refused(world, tmp_path):
    _stock, mod = world
    s = Session()
    s.open_mod(mod)
    junk = tmp_path / "notes.txt"
    junk.write_bytes(b"this is not audio" * 50)
    with pytest.raises(DsoError):
        s.add_sound(str(junk), name="Nope", group="USER")
    assert not os.path.exists(os.path.join(mod, "user_sounds.xml"))


def test_two_files_with_one_name_do_not_overwrite_each_other(world, tmp_path):
    """The second copy would silently change what the first entry plays."""
    _stock, mod = world
    s = Session()
    s.open_mod(mod)
    first = _wav(tmp_path / "a" / "Click.wav", frames=1000)
    second = _wav(tmp_path / "b" / "Click.wav", frames=5000)
    s.add_sound(first, name="First", group="USER")
    with pytest.raises(DsoError):
        s.add_sound(second, name="Second", group="USER")
    assert [r["reference"] for r in s.sounds()] == ["USER/First"]


def test_re_adding_the_identical_file_is_not_a_conflict(world, tmp_path):
    _stock, mod = world
    s = Session()
    s.open_mod(mod)
    source = _wav(tmp_path / "Click.wav")
    s.add_sound(source, name="One", group="USER")
    s.add_sound(source, name="Two", group="FX")
    assert {r["reference"] for r in s.sounds()} == {"USER/One", "FX/Two"}


def test_the_same_name_in_two_groups_is_allowed(world, tmp_path):
    _stock, mod = world
    s = Session()
    s.open_mod(mod)
    s.add_sound(_wav(tmp_path / "a.wav"), name="Hit", group="FX/Near")
    s.add_sound(_wav(tmp_path / "b.wav"), name="Hit", group="FX/Far")
    assert {r["reference"] for r in s.sounds()} == {"FX/Near/Hit", "FX/Far/Hit"}


def test_the_same_name_in_one_group_is_refused(world, tmp_path):
    _stock, mod = world
    s = Session()
    s.open_mod(mod)
    s.add_sound(_wav(tmp_path / "a.wav"), name="Hit", group="FX")
    with pytest.raises(DsoError):
        s.add_sound(_wav(tmp_path / "b.wav"), name="Hit", group="FX")


def test_removing_a_sound_can_keep_or_delete_the_file(world, tmp_path):
    _stock, mod = world
    s = Session()
    s.open_mod(mod)
    s.add_sound(_wav(tmp_path / "Keep.wav"), name="Keep", group="USER")
    s.add_sound(_wav(tmp_path / "Gone.wav"), name="Gone", group="USER")

    assert s.remove_sound("USER/Keep") is True
    assert os.path.exists(os.path.join(mod, "sound", "sfx(2d)", "grp_USER", "Keep.wav"))
    assert s.remove_sound("USER/Gone", delete_file=True) is True
    assert not os.path.exists(os.path.join(mod, "sound", "sfx(2d)", "grp_USER", "Gone.wav"))
    assert s.sounds() == []


def test_removing_something_absent_says_so(world):
    _stock, mod = world
    s = Session()
    s.open_mod(mod)
    assert s.remove_sound("USER/Nope") is False


def test_replacing_a_file_rewrites_the_declared_metadata(world, tmp_path):
    """A stale Duration truncates playback and looks like a corrupt file."""
    _stock, mod = world
    s = Session()
    s.open_mod(mod)
    s.add_sound(_wav(tmp_path / "Short.wav", frames=1000), name="Cue", group="USER")
    s.replace_sound_file("USER/Cue", _wav(tmp_path / "Long.wav", frames=40000))
    text = open(os.path.join(mod, "user_sounds.xml"), encoding="latin-1").read()
    assert 'Duration=":40000"' in text
    assert 'Duration=":1000"' not in text
    assert s.sounds()[0]["seconds"] == pytest.approx(40000 / 22050)


def test_writing_a_sound_announces_the_change(world, tmp_path):
    """Otherwise the Project tab shows a mod without the file just written."""
    _stock, mod = world
    s = Session()
    s.open_mod(mod)
    heard = []
    s.subscribe(heard.append)
    s.add_sound(_wav(tmp_path / "Beep.wav"), name="Beep", group="USER")
    assert heard == ["mod"]
    assert "user_sounds.xml" in s.mod.files()


def test_the_game_and_the_mod_are_listed_together(world, tmp_path):
    """Two databases, one question: what exists and which are mine."""
    from dsotools.formats import sounddb as sounddbfmt

    stock, mod = world
    # A stock database beside the extracted tree the fixture builds.
    game = tmp_path / "install"
    game.mkdir(exist_ok=True)
    (game / "KlangErzeugerDefault.xml").write_bytes(
        b'<?xml version="1.0" encoding="ISO-8859-1"?>\r\n<ASE_Database>\r\n'
        b'  <Group Name="FX">\r\n'
        b'    <Sound2D Name="Boom" Resrc="sound\\sfx(2d)\\Boom.wav" Freq="44100" />\r\n'
        b"  </Group>\r\n</ASE_Database>\r\n")
    assert sounddbfmt.is_sound_database(
        (game / "KlangErzeugerDefault.xml").read_bytes())

    s = Session()
    s.open_mod(mod)
    s.game_path = str(game)          # the fixture's stock tree has no install root
    s._stock_sound_db = None
    s.add_sound(_wav(tmp_path / "Beep.wav"), name="Beep", group="USER")

    rows = {r["reference"]: r for r in s.sounds()}
    assert set(rows) == {"USER/Beep", "FX/Boom"}
    assert rows["USER/Beep"]["editable"] and rows["USER/Beep"]["exists"]
    assert not rows["FX/Boom"]["editable"]
    assert not rows["FX/Boom"]["exists"]        # declared, file absent
    assert "USER" in s.sound_groups() and "FX" in s.sound_groups()


def _with_stock_sounds(tmp_path, session):
    """Point a session at a small stock sound database."""
    game = tmp_path / "install"
    game.mkdir(exist_ok=True)
    (game / "KlangErzeugerDefault.xml").write_bytes(
        b'<?xml version="1.0" encoding="ISO-8859-1"?>\r\n<ASE_Database>\r\n'
        b'  <Group Name="Mainmenu" Wet="0.0">\r\n'
        b'    <Stream Name="MUSIC_Mainmenu" '
        b'Resrc="sound\\music(stream)\\grp_Mainmenu\\MUSIC_Mainmenu.mp3" '
        b'Channels="2" Duration=":7406208" Freq="44100" />\r\n'
        b"  </Group>\r\n</ASE_Database>\r\n")
    session.game_path = str(game)
    session._stock_sound_db = None
    return game


def test_overriding_a_stock_sound_declares_its_group_and_name(world, tmp_path):
    """There is no 'replace a stock sound' call; declaring the name is it."""
    _stock, mod = world
    s = Session()
    s.open_mod(mod)
    _with_stock_sounds(tmp_path, s)

    row = s.override_sound("Mainmenu/MUSIC_Mainmenu", _wav(tmp_path / "mine.wav"))

    assert row["reference"] == "Mainmenu/MUSIC_Mainmenu"
    assert row["where"] == "mod" and row["editable"]
    # Two declarations now answer to one address, which is the whole point.
    both = [r for r in s.sounds() if r["reference"] == "Mainmenu/MUSIC_Mainmenu"]
    assert {r["where"] for r in both} == {"mod", "game"}


def test_an_override_keeps_the_stock_entry_s_kind(world, tmp_path):
    """A Stream re-declared as Sound2D loads a whole track into memory."""
    _stock, mod = world
    s = Session()
    s.open_mod(mod)
    _with_stock_sounds(tmp_path, s)
    row = s.override_sound("Mainmenu/MUSIC_Mainmenu", _wav(tmp_path / "mine.wav"))
    assert row["kind"] == "Stream"
    assert row["group"] == "Mainmenu"


def test_overriding_something_the_game_does_not_declare_is_an_error(world, tmp_path):
    _stock, mod = world
    s = Session()
    s.open_mod(mod)
    _with_stock_sounds(tmp_path, s)
    with pytest.raises(DsoError):
        s.override_sound("Nope/Missing", _wav(tmp_path / "mine.wav"))


def test_overriding_twice_is_refused_rather_than_duplicated(world, tmp_path):
    _stock, mod = world
    s = Session()
    s.open_mod(mod)
    _with_stock_sounds(tmp_path, s)
    s.override_sound("Mainmenu/MUSIC_Mainmenu", _wav(tmp_path / "one.wav"))
    with pytest.raises(DsoError):
        s.override_sound("Mainmenu/MUSIC_Mainmenu", _wav(tmp_path / "two.wav"))


def test_an_override_needs_a_mod(world, tmp_path):
    stock, _mod = world
    s = Session()
    s.open_game(stock)
    _with_stock_sounds(tmp_path, s)
    with pytest.raises(DsoError):
        s.override_sound("Mainmenu/MUSIC_Mainmenu", _wav(tmp_path / "mine.wav"))


def _mod_audio(mod):
    """Every audio file actually sitting in the mod, mod-relative."""
    found = []
    for folder, _dirs, files in os.walk(os.path.join(mod, "sound")):
        for name in files:
            full = os.path.join(folder, name)
            found.append(os.path.relpath(full, mod).replace(os.sep, "/"))
    return sorted(found)


def test_replacing_a_file_takes_the_old_one_with_it(world, tmp_path):
    """Reported from the app: a swapped 2.8 MB track stayed in the mod.

    Nothing referenced it any more, so it shipped as dead weight and only
    SND002 noticed.
    """
    _stock, mod = world
    s = Session()
    s.open_mod(mod)
    s.add_sound(_wav(tmp_path / "battle.wav", frames=40000),
                name="Menu", kind="Stream", group="Mainmenu")
    assert _mod_audio(mod) == ["sound/music(stream)/grp_USER/battle.wav"]

    s.replace_sound_file("Mainmenu/Menu", _wav(tmp_path / "menu.wav", frames=12000))

    assert _mod_audio(mod) == ["sound/music(stream)/grp_USER/menu.wav"]


def test_replacing_keeps_a_file_another_entry_still_names(world, tmp_path):
    """Two declarations may share one file; deleting it would silence one."""
    _stock, mod = world
    s = Session()
    s.open_mod(mod)
    shared = _wav(tmp_path / "shared.wav")
    s.add_sound(shared, name="A", kind="Sound2D", group="FX")
    s.add_sound(shared, name="B", kind="Sound2D", group="FX2")

    s.replace_sound_file("FX/A", _wav(tmp_path / "other.wav", frames=3000))

    assert "sound/sfx(2d)/grp_USER/shared.wav" in _mod_audio(mod)
    kept = {r["reference"]: r for r in s.sounds()}
    assert kept["FX2/B"]["exists"]


def test_replacing_in_place_does_not_delete_what_it_just_wrote(world, tmp_path):
    """Same filename in and out: the copy is the file, so pruning it is fatal."""
    _stock, mod = world
    s = Session()
    s.open_mod(mod)
    s.add_sound(_wav(tmp_path / "a" / "Cue.wav", frames=1000),
                name="Cue", group="USER")
    s.replace_sound_file("USER/Cue", _wav(tmp_path / "b" / "Cue.wav", frames=9000))

    assert _mod_audio(mod) == ["sound/sfx(2d)/grp_USER/Cue.wav"]
    assert s.sounds()[0]["exists"]
    assert s.sounds()[0]["seconds"] == pytest.approx(9000 / 22050)


def test_replacing_can_be_asked_to_leave_the_old_file(world, tmp_path):
    _stock, mod = world
    s = Session()
    s.open_mod(mod)
    s.add_sound(_wav(tmp_path / "one.wav"), name="Cue", group="USER")
    s.replace_sound_file("USER/Cue", _wav(tmp_path / "two.wav", frames=3000),
                         delete_old=False)
    assert _mod_audio(mod) == ["sound/sfx(2d)/grp_USER/one.wav",
                               "sound/sfx(2d)/grp_USER/two.wav"]


def test_removing_will_not_delete_a_file_another_entry_uses(world, tmp_path):
    """``delete_file=True`` means "if it is yours alone", not "unconditionally"."""
    _stock, mod = world
    s = Session()
    s.open_mod(mod)
    shared = _wav(tmp_path / "shared.wav")
    s.add_sound(shared, name="A", kind="Sound2D", group="FX")
    s.add_sound(shared, name="B", kind="Sound2D", group="FX2")

    assert s.remove_sound("FX/A", delete_file=True) is True

    assert "sound/sfx(2d)/grp_USER/shared.wav" in _mod_audio(mod)
    assert s.sounds()[0]["exists"]


def test_removing_deletes_a_file_nothing_else_needs(world, tmp_path):
    _stock, mod = world
    s = Session()
    s.open_mod(mod)
    s.add_sound(_wav(tmp_path / "only.wav"), name="Only", group="USER")
    assert s.remove_sound("USER/Only", delete_file=True) is True
    assert _mod_audio(mod) == []


def test_nothing_deletes_out_of_the_game_installation(world, tmp_path):
    """A mod entry may name a game path; that file is not the mod's to remove."""
    _stock, mod = world
    game = tmp_path / "install"
    (game / "sound").mkdir(parents=True)
    victim = game / "sound" / "stock.wav"
    _wav(victim)

    database = pathlib.Path(mod) / "user_sounds.xml"
    database.write_bytes(
        b'<?xml version="1.0" encoding="ISO-8859-1"?>\r\n<ASE_Database>\r\n'
        b'  <Group Name="USER">\r\n'
        b'    <Sound2D Name="Borrowed" Resrc="sound\\stock.wav" Freq="22050" />\r\n'
        b"  </Group>\r\n</ASE_Database>\r\n")
    s = Session()
    s.open_mod(mod)
    s.game_path = str(game)

    assert s.remove_sound("USER/Borrowed", delete_file=True) is True
    assert victim.exists(), "a game file was deleted"


# --------------------------------------------------------------------------
# the four mechanical fixes
# --------------------------------------------------------------------------
#
# Each gets three tests: it repairs the mod, the diagnostic it repaired is
# gone from a fresh validation, and it says something truthful when there is
# nothing to do.  The second is the one that matters -- a fix the validator
# still complains about afterwards is a bug that looks like a fix.


def test_only_the_mechanical_repairs_are_offered():
    """The others need a decision, and a menu that guesses is worse than none."""
    s = Session()
    assert set(s.FIXES) == {"PRJ004", "PRJ005", "PRJ007", "SND004"}
    assert s.fix_for("PRJ006") is None
    assert s.fix_for("SND002") is None
    label, explanation = s.fix_for("PRJ005")
    assert label and explanation


def test_a_fix_needs_a_mod():
    s = Session()
    with pytest.raises(DsoError):
        s.apply_fix("PRJ004")


def test_an_unknown_code_is_refused(world):
    stock, mod = world
    s = Session()
    s.open_game(stock)
    s.open_mod(mod)
    with pytest.raises(DsoError) as exc:
        s.apply_fix("PRJ006")
    assert "PRJ006" in str(exc.value)


def test_the_missing_items_ini_is_written(world):
    """Without it the game does not list the mod, and says nothing about it."""
    stock, mod = world
    os.unlink(os.path.join(mod, "inifiles", "items.ini"))
    s = Session()
    s.open_game(stock)
    s.open_mod(mod)
    assert "PRJ004" in s.validate().by_code()

    summary = s.apply_fix("PRJ004")

    assert "items.ini" in summary
    assert os.path.exists(os.path.join(mod, "inifiles", "items.ini"))
    assert "PRJ004" not in s.validate().by_code()


def test_the_items_ini_fix_leaves_the_loose_files_alone(world):
    """One fix, one concern: the dead file is PRJ005's to move."""
    stock, mod = world
    os.unlink(os.path.join(mod, "inifiles", "items.ini"))
    s = Session()
    s.open_game(stock)
    s.open_mod(mod)

    s.apply_fix("PRJ004")

    assert os.path.exists(os.path.join(mod, "3DView", "Dead.xml"))
    assert "PRJ005" in s.validate().by_code()


def test_loose_files_the_engine_never_reads_are_moved_into_the_zip(world):
    stock, mod = world
    s = Session()
    s.open_game(stock)
    s.open_mod(mod)
    assert "PRJ005" in s.validate().by_code()

    summary = s.apply_fix("PRJ005")

    assert "user_data.zip" in summary
    assert not os.path.exists(os.path.join(mod, "3DView", "Dead.xml"))
    with zipfile.ZipFile(os.path.join(mod, "user_data.zip")) as zf:
        assert "3DView/Dead.xml" in zf.namelist()
    assert "PRJ005" not in s.validate().by_code()


def test_a_fix_drops_the_report_it_invalidated(world):
    """Right about one row and stale about the rest is worse than admitting it."""
    stock, mod = world
    s = Session()
    s.open_game(stock)
    s.open_mod(mod)
    s.validate()

    s.apply_fix("PRJ005")

    assert s.report is None


def test_moving_nothing_says_so(world):
    stock, mod = world
    os.unlink(os.path.join(mod, "3DView", "Dead.xml"))
    s = Session()
    s.open_game(stock)
    s.open_mod(mod)
    assert "Nothing" in s.apply_fix("PRJ005")


def test_scripts_are_moved_out_of_the_zip(world):
    """The engine reads scripts as real files and ignores archived copies."""
    stock, mod = world
    with zipfile.ZipFile(os.path.join(mod, "user_data.zip"), "a") as zf:
        zf.writestr("scripts/mine.lua", b"-- hello\n")
    s = Session()
    s.open_game(stock)
    s.open_mod(mod)
    assert "PRJ007" in s.validate().by_code()

    summary = s.apply_fix("PRJ007")

    assert "1 script(s) written loose" in summary
    loose = os.path.join(mod, "scripts", "mine.lua")
    assert os.path.exists(loose)
    with open(loose, "rb") as handle:
        assert handle.read() == b"-- hello\n"
    with zipfile.ZipFile(os.path.join(mod, "user_data.zip")) as zf:
        assert "scripts/mine.lua" not in zf.namelist()
    assert "PRJ007" not in s.validate().by_code()


def test_a_different_loose_script_of_the_same_name_is_not_overwritten(world):
    """Both copies are plausible intent, and guessing wrong destroys work."""
    stock, mod = world
    os.makedirs(os.path.join(mod, "scripts"), exist_ok=True)
    with open(os.path.join(mod, "scripts", "mine.lua"), "wb") as handle:
        handle.write(b"-- the newer edit\n")
    with zipfile.ZipFile(os.path.join(mod, "user_data.zip"), "a") as zf:
        zf.writestr("scripts/mine.lua", b"-- the older one\n")
    s = Session()
    s.open_game(stock)
    s.open_mod(mod)

    summary = s.apply_fix("PRJ007")

    assert "left in the archive" in summary
    with open(os.path.join(mod, "scripts", "mine.lua"), "rb") as handle:
        assert handle.read() == b"-- the newer edit\n"
    with zipfile.ZipFile(os.path.join(mod, "user_data.zip")) as zf:
        assert "scripts/mine.lua" in zf.namelist()


def test_an_identical_loose_script_just_loses_the_archived_copy(world):
    stock, mod = world
    os.makedirs(os.path.join(mod, "scripts"), exist_ok=True)
    with open(os.path.join(mod, "scripts", "mine.lua"), "wb") as handle:
        handle.write(b"-- same\n")
    with zipfile.ZipFile(os.path.join(mod, "user_data.zip"), "a") as zf:
        zf.writestr("scripts/mine.lua", b"-- same\n")
    s = Session()
    s.open_game(stock)
    s.open_mod(mod)

    summary = s.apply_fix("PRJ007")

    assert "identical" in summary
    with zipfile.ZipFile(os.path.join(mod, "user_data.zip")) as zf:
        assert "scripts/mine.lua" not in zf.namelist()


def test_an_archive_with_no_scripts_says_so(world):
    stock, mod = world
    s = Session()
    s.open_game(stock)
    s.open_mod(mod)
    assert "no scripts" in s.apply_fix("PRJ007")


def _sound_mod(mod, declared):
    """A mod declaring one 22,050 Hz mono WAV of exactly 22,050 samples."""
    import pathlib

    _wav(pathlib.Path(mod) / "sound" / "sfx(2d)" / "grp_USER" / "Beep.wav")
    with open(os.path.join(mod, "user_sounds.xml"), "wb") as handle:
        handle.write(
            b'<?xml version="1.0" encoding="ISO-8859-1"?>\r\n<ASE_Database>\r\n'
            b'  <Group Name="USER">\r\n'
            b'    <Sound2D Name="Beep" '
            b'Resrc="%MOD%sound\\sfx(2d)\\grp_USER\\Beep.wav" '
            + declared + b' />\r\n  </Group>\r\n</ASE_Database>\r\n')


def test_a_stale_declaration_is_corrected_from_the_file(world):
    """The engine believes the database, so a wrong Duration truncates it."""
    stock, mod = world
    _sound_mod(mod, b'Channels="2" Duration=":999" Freq="44100"')
    s = Session()
    s.open_game(stock)
    s.open_mod(mod)
    assert "SND004" in s.validate().by_code()

    summary = s.apply_fix("SND004")

    assert "Beep" in summary
    assert "SND004" not in s.validate().by_code()
    with open(os.path.join(mod, "user_sounds.xml"), "rb") as handle:
        written = handle.read()
    assert b'Freq="22050"' in written
    assert b'Channels="1"' in written


def test_declarations_that_already_match_are_left_alone(world):
    stock, mod = world
    _sound_mod(mod, b'Channels="1" Duration=":22050" Freq="22050"')
    s = Session()
    s.open_game(stock)
    s.open_mod(mod)
    with open(os.path.join(mod, "user_sounds.xml"), "rb") as handle:
        before = handle.read()

    assert "already matches" in s.apply_fix("SND004")

    with open(os.path.join(mod, "user_sounds.xml"), "rb") as handle:
        assert handle.read() == before


def test_a_mod_with_no_sounds_says_so(world):
    stock, mod = world
    s = Session()
    s.open_game(stock)
    s.open_mod(mod)
    assert "no sounds" in s.apply_fix("SND004")


# --------------------------------------------------------------------------
# add new, bind it, take it away again
# --------------------------------------------------------------------------
#
# The three only mean anything together.  A file can be written at a new path
# -- the library always could, and nothing in the app ever asked.  A texture at
# a new path does *nothing* until a scene names it.  And a file that can be
# added and not removed leaves the tool apologising, which is what
# ``can_reset_to_stock`` did in as many words.

BOUND_SCENE = (
    b'<?xml version="1.0"?>\r\n'
    b'<WalhallaScene Version="2.00">\r\n'
    b'\t<Object Type=".?AVCWorldRoot@@">\r\n'
    b"\t\t<AttachedObjects>\r\n"
    b'\t\t\t<Object Type=".?AVCMesh@@" Name="m" Resrc3DO="objects/x.3do">\r\n'
    b'\t\t\t\t<EffectContainer Path="blender/mat_main.bsd9">\r\n'
    b"\t\t\t\t\t<Material>\r\n\t\t\t\t\t\t+1.000000\r\n\t\t\t\t\t</Material>\r\n"
    b"\t\t\t\t\t<Parameters>\r\n\t\t\t\t\t</Parameters>\r\n"
    b'\t\t\t\t\t<Textures Number="1">\r\n'
    b"\t\t\t\t\t\ttextures/a_col.dds\r\n"
    b"\t\t\t\t\t</Textures>\r\n"
    b"\t\t\t\t</EffectContainer>\r\n"
    b"\t\t\t</Object>\r\n"
    b"\t\t</AttachedObjects>\r\n"
    b"\t</Object>\r\n"
    b"</WalhallaScene>\r\n"
)


@pytest.fixture()
def bound(world, tmp_path):
    """A session whose mod overrides a scene that binds one texture."""
    stock, mod = world
    scene_dir = pathlib.Path(stock) / "ds_3dgen" / "3DView"
    (scene_dir / "textures").mkdir(parents=True, exist_ok=True)
    (scene_dir / "textures" / "a_col.dds").write_bytes(b"DDS stock")
    (scene_dir / "Bound.xml").write_bytes(BOUND_SCENE)

    s = Session()
    s.open_game(stock)
    s.open_mod(mod)
    staged = tmp_path / "Bound.xml"
    staged.write_bytes(BOUND_SCENE)
    s.replace_asset("3DView/Bound.xml", str(staged))
    return s, mod


def _model(submesh_total=1):
    """A minimal .3do root header declaring a submesh count."""
    import struct

    b = bytearray(0x48 + 4 * max(1, submesh_total))
    b[0:4] = b"OD3 "
    struct.pack_into("<I", b, 0x30, submesh_total)
    return bytes(b)


def _dds(tmp_path, name="new.dds", data=b"DDS mine"):
    path = tmp_path / name
    path.write_bytes(data)
    return str(path)


def test_a_path_the_mod_does_not_have_can_be_added(world, tmp_path):
    stock, mod = world
    s = Session()
    s.open_game(stock)
    s.open_mod(mod)
    vpath = "3DView/textures/brand_new.dds"

    assert s.check_new_path(vpath) is None
    routed = s.add_asset(vpath, _dds(tmp_path))

    # 3DView/ is zip-only, and the router knows it without being told.
    assert routed == {vpath: "zip"}
    assert vpath.lower() in Mod(mod).files()


def test_adding_over_a_file_the_mod_already_has_is_refused(world, tmp_path):
    """Replacing is a different intention, and doing it quietly loses work."""
    stock, mod = world
    s = Session()
    s.open_game(stock)
    s.open_mod(mod)
    why = s.check_new_path("inifiles/items.ini")
    assert why is not None and "already has" in why
    with pytest.raises(DsoError):
        s.add_asset("inifiles/items.ini", _dds(tmp_path, "items.ini"))


def test_a_folder_the_engine_never_reads_is_refused(world, tmp_path):
    stock, mod = world
    s = Session()
    s.open_game(stock)
    s.open_mod(mod)
    with pytest.raises(DsoError):
        s.add_asset("mystuff/x.dds", _dds(tmp_path))


def test_adding_needs_a_mod(tmp_path):
    s = Session()
    assert s.check_new_path("3DView/textures/x.dds") == "Open a mod first."


def test_a_new_texture_is_flagged_as_doing_nothing_yet(world):
    """The trap this whole entry point could otherwise create."""
    stock, mod = world
    s = Session()
    s.open_game(stock)
    s.open_mod(mod)
    note = s.new_path_note("3DView/textures/nothing_points_here.dds")
    assert note and "nothing asks for it" in note


def test_a_new_script_or_sound_is_not_flagged(world):
    """Those roots are read by name or scanned, so a new file is enough."""
    stock, mod = world
    s = Session()
    s.open_game(stock)
    s.open_mod(mod)
    assert s.new_path_note("scripts/mine.lua") is None
    assert s.new_path_note("sound/sfx(2d)/grp_USER/beep.wav") is None


def test_a_slot_can_be_pointed_at_an_added_texture(bound, tmp_path):
    """The half that makes adding a texture worth anything."""
    s, mod = bound
    vpath = "3DView/textures/mine.dds"
    s.add_asset(vpath, _dds(tmp_path))

    detail = s.scene_detail("3DView/Bound.xml")
    mesh = detail["meshes"][0]
    s.set_effect("3DView/Bound.xml", mesh["path"], 0, textures={0: vpath})

    with s.open_vfs() as vfs:
        sc = scenefmt.parse(vfs.read("3DView/Bound.xml"), path="3DView/Bound.xml")
        reference = sc.meshes()[0].effects[0].textures[0]
        # Written the way a scene must name it, NOT as a virtual path.
        assert reference == "textures/mine.dds"
        entry = vfs.resolve_reference(reference, scene_path="3DView/Bound.xml")
        assert entry is not None and entry.vpath.lower() == vpath.lower()


def test_binding_something_that_is_not_there_is_refused(bound):
    s, _mod = bound
    detail = s.scene_detail("3DView/Bound.xml")
    with pytest.raises(DsoError) as exc:
        s.set_effect("3DView/Bound.xml", detail["meshes"][0]["path"], 0,
                     textures={0: "3DView/textures/absent.dds"})
    assert "no asset at" in str(exc.value)


def test_binding_a_texture_a_scene_cannot_name_is_refused(bound, tmp_path):
    """It exists and is readable, and still must not be written.

    A scene resolves against its own folder and ``3DView/``. Anything else
    would rely on a spelling no stock reference uses, so it would work in the
    app and resolve to nothing in game.
    """
    s, _mod = bound
    outside = "strings/outside.dds"
    s.add_asset(outside, _dds(tmp_path))
    detail = s.scene_detail("3DView/Bound.xml")
    with pytest.raises(DsoError) as exc:
        s.set_effect("3DView/Bound.xml", detail["meshes"][0]["path"], 0,
                     textures={0: outside})
    assert "cannot be named" in str(exc.value)


def test_binding_a_slot_that_does_not_exist_is_refused(bound, tmp_path):
    s, _mod = bound
    vpath = "3DView/textures/mine.dds"
    s.add_asset(vpath, _dds(tmp_path))
    detail = s.scene_detail("3DView/Bound.xml")
    with pytest.raises(DsoError):
        s.set_effect("3DView/Bound.xml", detail["meshes"][0]["path"], 0,
                     textures={7: vpath})


# -- taking it out again ----------------------------------------------------


def test_an_added_file_is_removed_rather_than_reset(world, tmp_path):
    stock, mod = world
    s = Session()
    s.open_game(stock)
    s.open_mod(mod)
    vpath = "3DView/textures/added.dds"
    s.add_asset(vpath, _dds(tmp_path))

    assert s.removal_kind(vpath) == "remove"
    assert s.can_remove_from_mod(vpath) is None
    assert s.remove_from_mod(vpath) == {vpath: "zip"}
    assert vpath.lower() not in Mod(mod).files()


def test_an_overridden_file_is_a_reset_rather_than_a_removal(world):
    """Two different things to a reader; the menu says which."""
    stock, mod = world
    s = Session()
    s.open_game(stock)
    s.open_mod(mod)
    assert s.removal_kind("inifiles/Goods.ini") == "reset"


def test_items_ini_cannot_be_removed(world):
    stock, mod = world
    s = Session()
    s.open_game(stock)
    s.open_mod(mod)
    why = s.can_remove_from_mod("inifiles/items.ini")
    assert why is not None and "does not list this mod" in why
    with pytest.raises(DsoError):
        s.remove_from_mod("inifiles/items.ini")


def test_a_file_the_mod_does_not_have_cannot_be_removed(world):
    stock, mod = world
    s = Session()
    s.open_game(stock)
    s.open_mod(mod)
    assert s.can_remove_from_mod("3DView/textures/never.dds") is not None


def test_a_declared_sound_points_at_the_audio_tab(world, tmp_path):
    """Removing the file alone would leave the declaration naming nothing."""
    stock, mod = world
    s = Session()
    s.open_game(stock)
    s.open_mod(mod)
    source = _wav(tmp_path / "Beep.wav")
    s.add_sound(source, name="Beep", kind="Sound2D", group="USER")
    vpath = "sound/sfx(2d)/grp_USER/Beep.wav"
    assert vpath.lower() in Mod(mod).files()

    why = s.can_remove_from_mod(vpath)

    assert why is not None and "Audio tab" in why
    assert "USER/Beep" in why


def test_removing_says_what_the_mods_own_scenes_would_lose(bound, tmp_path):
    s, _mod = bound
    vpath = "3DView/textures/mine.dds"
    s.add_asset(vpath, _dds(tmp_path))
    detail = s.scene_detail("3DView/Bound.xml")
    s.set_effect("3DView/Bound.xml", detail["meshes"][0]["path"], 0,
                 textures={0: vpath})

    notes = s.removal_notes(vpath)

    assert any("3DView/Bound.xml" in n for n in notes)
    assert any("simply gone" in n for n in notes)


def test_removing_the_string_table_says_the_texts_survive(world, tmp_path):
    stock, mod = world
    s = Session()
    s.open_game(stock)
    s.open_mod(mod)
    s.save_strings([("IDM_MINE", "mine")])

    notes = s.removal_notes(s.STRINGS_PATH)

    assert any("project file" in n for n in notes)


def test_removing_drops_the_report_it_invalidated(world, tmp_path):
    stock, mod = world
    s = Session()
    s.open_game(stock)
    s.open_mod(mod)
    vpath = "3DView/textures/added.dds"
    s.add_asset(vpath, _dds(tmp_path))
    s.validate()

    s.remove_from_mod(vpath)

    assert s.report is None


def test_removing_a_model_the_mods_scene_uses_is_reported_too(bound, tmp_path):
    """A scene names its models the same relative way as its textures."""
    s, _mod = bound
    model = tmp_path / "x.3do"
    model.write_bytes(b"\x00" * 64)
    s.add_asset("3DView/objects/x.3do", str(model))

    notes = s.removal_notes("3DView/objects/x.3do")

    assert any("3DView/Bound.xml" in n for n in notes)


# --------------------------------------------------------------------------
# adding an asset of a known kind
# --------------------------------------------------------------------------
#
# "Add a file at a path you type" is plumbing.  What an author reaches for is
# "add a texture", from the tab that shows textures -- and the difference that
# matters is not the folder, it is that three of the four kinds do nothing at
# all until something names them.


def test_each_kind_knows_where_it_belongs():
    s = Session()
    assert s.suggest_add_path("texture", "hull.dds") == "3DView/textures/hull.dds"
    assert s.suggest_add_path("script", "helper.lua") == "scripts/helper.lua"


def test_there_is_no_model_kind():
    """A new model needs a scene to name it, and nothing reaches a new one.

    ``specs/scene.md`` 4.3.4: NWing.Create takes fixed constants, no ini maps a
    wing type to a ship family, and the class-to-family mapping is inside the
    executable.  Replacing the .3do an existing scene names is the route that
    works, and the linked-assets panel has always done that.
    """
    s = Session()
    assert "model" not in s.ADD_KINDS
    with pytest.raises(DsoError):
        s.add_kind("model")


def test_a_source_of_the_wrong_format_is_refused_by_kind(tmp_path):
    s = Session()
    png = tmp_path / "x.png"
    png.write_bytes(b"\x89PNG")
    why = s.check_add_source("texture", str(png))
    # Names the formats this kind takes, rather than leaving them to be guessed.
    assert why is not None and ".dds" in why


def test_a_source_of_the_right_format_is_accepted(tmp_path):
    s = Session()
    dds = tmp_path / "x.dds"
    dds.write_bytes(b"DDS ")
    assert s.check_add_source("texture", str(dds)) is None


def test_an_unknown_kind_raises_rather_than_guessing():
    s = Session()
    with pytest.raises(DsoError):
        s.add_kind("spaceship")


def test_the_inert_kinds_say_what_they_need():
    """The whole reason a typed entry point beats a path box."""
    s = Session()
    for kind in ("texture", "sound"):
        assert s.add_kind(kind)["inert"]
    # A script is read by the loader's glob, so it works by existing.
    assert s.add_kind("script")["inert"] is None


# -- binding a model --------------------------------------------------------


def test_a_mesh_can_be_pointed_at_an_added_model(bound, tmp_path):
    s, _mod = bound
    model = tmp_path / "mine.3do"
    model.write_bytes(_model(1))
    vpath = "3DView/objects/mine.3do"
    s.add_asset(vpath, str(model))

    detail = s.scene_detail("3DView/Bound.xml")
    s.set_mesh_model("3DView/Bound.xml", detail["meshes"][0]["path"], vpath)

    with s.open_vfs() as vfs:
        sc = scenefmt.parse(vfs.read("3DView/Bound.xml"), path="3DView/Bound.xml")
        # Scene-relative, exactly as for a texture.
        assert sc.meshes()[0].model == "objects/mine.3do"
        entry = vfs.resolve_reference(sc.meshes()[0].model,
                                      scene_path="3DView/Bound.xml")
        assert entry is not None and entry.vpath.lower() == vpath.lower()


def test_binding_a_model_a_scene_cannot_name_is_refused(bound, tmp_path):
    s, _mod = bound
    model = tmp_path / "outside.3do"
    model.write_bytes(_model(1))
    s.add_asset("scripts/outside.3do", str(model))
    detail = s.scene_detail("3DView/Bound.xml")
    with pytest.raises(DsoError) as exc:
        s.set_mesh_model("3DView/Bound.xml", detail["meshes"][0]["path"],
                         "scripts/outside.3do")
    assert "cannot be named" in str(exc.value)


def test_binding_a_model_that_is_not_there_is_refused(bound):
    s, _mod = bound
    detail = s.scene_detail("3DView/Bound.xml")
    with pytest.raises(DsoError):
        s.set_mesh_model("3DView/Bound.xml", detail["meshes"][0]["path"],
                         "3DView/objects/absent.3do")


def test_the_submesh_count_is_checked_before_the_bind(bound, tmp_path):
    """SCN001 asked in advance: one EffectContainer per submesh."""
    s, _mod = bound
    model = tmp_path / "two.3do"
    model.write_bytes(_model(2))
    s.add_asset("3DView/objects/two.3do", str(model))
    detail = s.scene_detail("3DView/Bound.xml")

    fit = s.mesh_model_fit("3DView/Bound.xml", detail["meshes"][0]["path"],
                           "3DView/objects/two.3do")

    assert fit["effects"] == 1
    assert fit["submesh_total"] == 2
    assert fit["fits"] is False


def test_only_models_a_scene_can_reach_are_offered(bound, tmp_path):
    s, _mod = bound
    (tmp_path / "a.3do").write_bytes(_model(1))
    s.add_asset("3DView/objects/reachable.3do", str(tmp_path / "a.3do"))
    s.add_asset("scripts/unreachable.3do", str(tmp_path / "a.3do"))

    offered = [r["vpath"] for r in s.bindable_models("3DView/Bound.xml")]

    assert "3DView/objects/reachable.3do" in offered
    assert "scripts/unreachable.3do" not in offered


def test_the_mods_own_models_are_offered_first(bound, tmp_path):
    """Binding is nearly always the second half of "I added a model"."""
    s, _mod = bound
    (tmp_path / "a.3do").write_bytes(_model(1))
    s.add_asset("3DView/objects/zzz_mine.3do", str(tmp_path / "a.3do"))

    rows = s.bindable_models("3DView/Bound.xml")

    assert rows[0]["in_mod"] is True
    assert rows[0]["vpath"] == "3DView/objects/zzz_mine.3do"


# -- a plain script ---------------------------------------------------------


def test_a_new_script_is_written_and_is_not_empty(world):
    """An empty .lua is indistinguishable from one that failed to load."""
    stock, mod = world
    s = Session()
    s.open_game(stock)
    s.open_mod(mod)

    written = s.new_script("helper")

    assert os.path.basename(written) == "helper.lua"
    with open(written, "rb") as handle:
        text = handle.read().decode("cp1252")
    assert "helper.lua" in text and text.strip()
    assert "scripts/helper.lua" in Mod(mod).files()


def test_a_new_script_will_not_land_on_an_existing_one(world):
    stock, mod = world
    s = Session()
    s.open_game(stock)
    s.open_mod(mod)
    s.new_script("helper")
    with pytest.raises(DsoError):
        s.new_script("helper")


def test_a_new_script_needs_a_name(world):
    stock, mod = world
    s = Session()
    s.open_game(stock)
    s.open_mod(mod)
    with pytest.raises(DsoError):
        s.new_script("   ")


def test_a_new_script_needs_a_mod():
    s = Session()
    with pytest.raises(DsoError):
        s.new_script("helper")


# -- editing the submesh list from the session ------------------------------


def test_a_submesh_can_be_added_and_removed(bound):
    """SCN001 was reportable and not fixable until this existed."""
    s, _mod = bound
    detail = s.scene_detail("3DView/Bound.xml")
    node = detail["meshes"][0]["path"]
    assert len(detail["meshes"][0]["slots"]) == 1

    s.add_submesh("3DView/Bound.xml", node)
    assert len(s.scene_detail("3DView/Bound.xml")["meshes"][0]["slots"]) == 2

    s.remove_submesh("3DView/Bound.xml", node, 1)
    assert len(s.scene_detail("3DView/Bound.xml")["meshes"][0]["slots"]) == 1


def test_the_added_submesh_carries_the_neighbours_shader(bound):
    s, _mod = bound
    detail = s.scene_detail("3DView/Bound.xml")
    node = detail["meshes"][0]["path"]
    shader = detail["meshes"][0]["slots"][0]["shader"]

    s.add_submesh("3DView/Bound.xml", node)

    slots = s.scene_detail("3DView/Bound.xml")["meshes"][0]["slots"]
    assert slots[1]["shader"] == shader


def test_editing_submeshes_of_a_node_that_is_not_there_is_refused(bound):
    s, _mod = bound
    with pytest.raises(DsoError):
        s.add_submesh("3DView/Bound.xml", "no|such|node")
    with pytest.raises(DsoError):
        s.remove_submesh("3DView/Bound.xml", "no|such|node", 0)


def test_removing_a_submesh_index_that_is_not_there_is_refused(bound):
    s, _mod = bound
    node = s.scene_detail("3DView/Bound.xml")["meshes"][0]["path"]
    with pytest.raises(DsoError):
        s.remove_submesh("3DView/Bound.xml", node, 9)


def test_editing_submeshes_needs_a_mod():
    s = Session()
    with pytest.raises(DsoError):
        s.add_submesh("3DView/x.xml", "n")
