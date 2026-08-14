"""
Mod projects and the Ascaron INI dialect.

The fixtures here are deliberately modelled on defects found in a real mod
(a large third-party one): a fully duplicated ``user_data.zip``, files identical to
stock, and a missing ``items.ini``.  Testing against invented problems risks
building a validator for problems nobody has.
"""

from __future__ import annotations

import os
import pathlib
import zipfile

import pytest

from dsotools import vfs as vfsmod
from dsotools.errors import ProjectError
from dsotools.formats import ini
from dsotools.project import (
    MOD_CONTENT_ROOTS,
    ZIP_ONLY_ROOTS,
    FileState,
    Mod,
    check_mod_path,
)


# --------------------------------------------------------------------------
# INI dialect
# --------------------------------------------------------------------------

SAMPLE = (
    b"[darkstarmod]\r\n"
    b"; Name der Modifikation\r\n"
    b"mod_name = Example Mod v2.8.4\r\n"
    b"\r\n"
    b"mod_desc = Mehr Artefakte, h\xf6here Geschwindigkeit.\r\n"
)

STARSHIP = (
    b"[StarShip002_000] ; SPACE_SHIP\r\n"
    b"Radius = 15.403557 ; Radius der Huellkugel\r\n"
    b"BayNum = 0 ; Anzahl der Halterungen\r\n"
)


def test_ini_round_trip_is_byte_identical():
    assert ini.parse(SAMPLE).to_bytes() == SAMPLE


def test_ini_strips_trailing_comment_from_value():
    """configparser would hand back '15.403557 ; Radius der Huellkugel'."""
    f = ini.parse(STARSHIP)
    sec = f.section("StarShip002_000")
    assert sec.get("Radius") == "15.403557"
    assert sec.entry("Radius").as_float() == pytest.approx(15.403557)
    assert sec.entry("BayNum").as_int() == 0


def test_ini_reads_cp1252_umlauts():
    f = ini.parse(SAMPLE)
    assert "höhere" in f.get("darkstarmod", "mod_desc")


def test_ini_edit_rewrites_only_that_line():
    f = ini.parse(SAMPLE)
    f.section("darkstarmod").set("mod_name", "Something Else")
    out = f.to_bytes()
    changed = [
        (a, b) for a, b in zip(SAMPLE.split(b"\r\n"), out.split(b"\r\n")) if a != b
    ]
    assert len(changed) == 1
    assert b"; Name der Modifikation" in out          # comment survives
    assert b"h\xf6here" in out                        # encoding survives


def test_ini_reports_duplicates_instead_of_raising():
    """configparser raises on these. Real files contain them."""
    data = b"[a]\r\nx = 1\r\nx = 2\r\n[a]\r\ny = 3\r\n"
    f = ini.parse(data)
    assert f.duplicate_sections() == ["a"]
    assert f.sections[0].duplicate_keys() == ["x"]
    assert f.to_bytes() == data


def test_ini_comment_only_lines_are_not_entries():
    f = ini.parse(b"[s]\r\n; a = b\r\nreal = 1\r\n")
    assert [e.key.strip() for e in f.section("s").entries] == ["real"]


# --------------------------------------------------------------------------
# mods
# --------------------------------------------------------------------------


def _write(path, data=b"x"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


@pytest.fixture()
def stock(tmp_path):
    root = tmp_path / "extracted"
    d = root / "ds_3dgen" / "3DView"
    d.mkdir(parents=True)
    (d / "PlayerShip.xml").write_bytes(b"<stock scene/>")
    (root / "ds_add" / "inifiles").mkdir(parents=True)
    (root / "ds_add" / "inifiles" / "items.ini").write_bytes(b"[items]\r\n")
    (root / "ds_add" / "inifiles" / "Goods.ini").write_bytes(b"[goods]\r\n")
    return vfsmod.from_extracted(str(root))


@pytest.fixture()
def modroot(tmp_path):
    """A mod shaped like the real one: zip + a full loose duplicate."""
    root = tmp_path / "Customization" / "Test Mod"
    root.mkdir(parents=True)
    (root / "darkstarmod.ini").write_bytes(
        b"[darkstarmod]\r\nmod_name = Test Mod\r\nmod_desc = A mod.\r\n"
    )
    _write(root / "inifiles" / "items.ini", b"[items]\r\n")
    _write(root / "inifiles" / "Goods.ini", b"[goods]\r\n")      # identical to stock
    _write(root / "inifiles" / "Prices.ini", b"[prices]\r\n")    # addition
    with zipfile.ZipFile(root / "user_data.zip", "w") as zf:
        zf.writestr("3DView/PlayerShip.xml", b"<edited scene/>")
        zf.writestr("3DView/textures/new.dds", b"DDS ")
    # the trap: the same content also present loose
    _write(root / "3DView" / "PlayerShip.xml", b"<edited scene/>")
    _write(root / "3DView" / "textures" / "new.dds", b"DDS ")
    _write(root / "Save" / "mine.dss", b"DGF ")                  # must be ignored
    return root


def test_discover_finds_mods_with_a_manifest(modroot):
    mods = Mod.discover(str(modroot.parent))
    assert [m.name for m in mods] == ["Test Mod"]
    assert mods[0].display_name == "Test Mod"


def test_savegames_are_not_part_of_the_mod(modroot):
    keys = Mod(str(modroot)).files()
    assert not any("save/" in k for k in keys)


def test_the_manifest_archive_and_sidecar_are_not_mod_content(modroot):
    """`.dsoproj` is this tool's file; the game never reads it.

    Listed among the author's own files it invites the question "what does
    this override?", and the answer is nothing.
    """
    _write(modroot / ".dsoproj", b'{"schema": 1}')
    keys = set(Mod(str(modroot)).files())
    assert ".dsoproj" not in keys
    assert "darkstarmod.ini" not in keys
    assert "user_data.zip" not in keys
    assert "inifiles/prices.ini" in keys          # real content still there


def test_remove_deletes_loose_files_and_prunes_empty_folders(modroot):
    mod = Mod(str(modroot))
    assert mod.remove(["inifiles/Prices.ini"]) == {"inifiles/Prices.ini": "loose"}
    assert not (modroot / "inifiles" / "Prices.ini").exists()
    assert "inifiles/prices.ini" not in Mod(str(modroot)).files()
    # Its siblings are still there, so the folder must survive.
    assert (modroot / "inifiles" / "items.ini").exists()


def test_remove_rewrites_the_zip_without_the_entry(modroot):
    mod = Mod(str(modroot))
    assert mod.remove(["3DView/textures/new.dds"]) == {
        "3DView/textures/new.dds": "zip"
    }
    with zipfile.ZipFile(modroot / "user_data.zip") as zf:
        names = zf.namelist()
    assert "3DView/textures/new.dds" not in names
    assert "3DView/PlayerShip.xml" in names       # everything else survives


def test_remove_refuses_items_ini(modroot):
    """Removing it makes the game skip the mod entirely, and say nothing."""
    mod = Mod(str(modroot))
    with pytest.raises(ProjectError):
        mod.remove(["inifiles/items.ini"])
    assert (modroot / "inifiles" / "items.ini").exists()


def test_remove_ignores_paths_the_mod_does_not_have(modroot):
    """"It is already gone" is the outcome the caller wanted."""
    assert Mod(str(modroot)).remove(["inifiles/Nope.ini"]) == {}


def test_removing_an_empty_folders_worth_prunes_it_but_never_the_root(tmp_path):
    root = tmp_path / "Customization" / "Solo"
    root.mkdir(parents=True)
    (root / "darkstarmod.ini").write_bytes(b"[darkstarmod]\r\nmod_name = S\r\n")
    _write(root / "inifiles" / "items.ini", b"[items]\r\n")
    _write(root / "strings" / "deep" / "one.res", b"x")

    Mod(str(root)).remove(["strings/deep/one.res"])
    assert not (root / "strings").exists()
    assert root.exists()


def test_zip_content_wins_and_loose_duplicate_is_reported(modroot):
    mod = Mod(str(modroot))
    files = mod.files()
    assert files["3dview/playership.xml"].source == "zip"
    assert mod.duplicated_files() == ["3dview/playership.xml", "3dview/textures/new.dds"]


def test_loose_3dview_is_dead(modroot):
    """Confirmed in game: the engine does not read a mod's loose 3DView/."""
    mod = Mod(str(modroot))
    dead = {f.vpath for f in mod.dead_files()}
    assert dead == {"3DView/PlayerShip.xml", "3DView/textures/new.dds"}
    # inifiles are read loose, so they are never dead
    assert all(not f.vpath.startswith("inifiles/") for f in mod.dead_files())


def test_classify_against_stock(modroot, stock):
    mod = Mod(str(modroot))
    files = mod.classify(stock)
    assert files["3dview/playership.xml"].state == FileState.OVERRIDE
    assert files["inifiles/goods.ini"].state == FileState.IDENTICAL
    assert files["inifiles/prices.ini"].state == FileState.ADDITION
    assert files["loose:3dview/playership.xml"].state == FileState.DEAD


def test_is_listable_requires_items_ini(modroot):
    assert Mod(str(modroot)).is_listable()
    (modroot / "inifiles" / "items.ini").unlink()
    mod = Mod(str(modroot))
    assert not mod.is_listable()


# --------------------------------------------------------------------------
# creating a mod
# --------------------------------------------------------------------------


def test_create_writes_the_manifest_and_items_ini(tmp_path, stock):
    """items.ini is not optional -- without it the game never lists the mod."""
    mod = Mod.create(str(tmp_path / "Customization"), "My Mod", "Does things.", stock=stock)
    assert mod.display_name == "My Mod"
    assert mod.description == "Does things."
    assert mod.is_listable()
    assert (pathlib.Path(mod.root) / "inifiles" / "items.ini").exists()


def test_create_copies_the_stock_items_ini_when_a_game_is_open(tmp_path, stock):
    mod = Mod.create(str(tmp_path / "C"), "M", stock=stock)
    got = (pathlib.Path(mod.root) / "inifiles" / "items.ini").read_bytes()
    assert got == stock.read("inifiles/items.ini")


def test_create_still_works_without_a_game(tmp_path):
    """A stub is written so the mod is listable; PRJ004 can flag it later."""
    mod = Mod.create(str(tmp_path / "C"), "M")
    assert mod.is_listable()


def test_create_records_the_base_game(tmp_path, stock):
    from dsotools.project import ProjectFile

    mod = Mod.create(str(tmp_path / "C"), "M", stock=stock)
    assert ProjectFile.load(mod.root).base_game_matches(stock) is True


def test_create_sanitises_the_folder_name(tmp_path, stock):
    """Windows rejects : / ? and friends in a folder name."""
    mod = Mod.create(str(tmp_path / "C"), 'Bad: name/with?chars', stock=stock)
    assert mod.display_name == "Bad: name/with?chars"      # the display name is intact
    assert not set(os.path.basename(mod.root)) & set('<>:"/\\|?*')


def test_create_refuses_an_empty_name(tmp_path):
    from dsotools.errors import ProjectError

    with pytest.raises(ProjectError):
        Mod.create(str(tmp_path / "C"), "   ")


def test_create_refuses_to_overwrite(tmp_path, stock):
    from dsotools.errors import ProjectError

    Mod.create(str(tmp_path / "C"), "M", stock=stock)
    with pytest.raises(ProjectError):
        Mod.create(str(tmp_path / "C"), "M", stock=stock)


def test_created_mod_validates_clean(tmp_path, stock):
    """A brand-new mod must not greet its author with errors."""
    from dsotools import validate

    mod = Mod.create(str(tmp_path / "C"), "Fresh", "d", stock=stock)
    report = validate.validate_mod(mod, stock)
    assert report.ok, [d for d in report if d.severity == "error"]


def test_deploy_target_routes_3dview_into_the_zip():
    """The single function that stops the app writing files that do nothing."""
    assert Mod.deploy_target("3DView/PlayerShip.xml") == "zip"
    assert Mod.deploy_target("3DView/textures/a.dds") == "zip"
    assert Mod.deploy_target("inifiles/items.ini") == "loose"
    assert Mod.deploy_target("scripts/user_scripts.bin") == "loose"
    assert Mod.deploy_target("sound/sfx(2d)/x.wav") == "loose"


# --------------------------------------------------------------------------
# editing metadata, and renaming as a separate act
# --------------------------------------------------------------------------


def test_set_metadata_rewrites_only_those_lines(modroot):
    """A hand-written manifest keeps its comments and its encoding."""
    (modroot / "darkstarmod.ini").write_bytes(
        b"[darkstarmod]\r\n"
        b"; von Hand geschrieben\r\n"
        b"mod_name = Alt\r\n"
        b"mod_desc = Beschreibung mit \xfcmlaut\r\n"
    )
    mod = Mod(str(modroot))
    mod.set_metadata("Neu", "Andere Beschreibung")

    raw = (modroot / "darkstarmod.ini").read_bytes()
    assert b"; von Hand geschrieben" in raw          # comment survives
    assert b"mod_name = Neu" in raw
    assert b"Alt" not in raw
    assert Mod(str(modroot)).description == "Andere Beschreibung"


def test_set_metadata_adds_a_missing_key(modroot):
    """Manifests in the wild carry only mod_name; the editor must still work."""
    (modroot / "darkstarmod.ini").write_bytes(b"[darkstarmod]\r\nmod_name = Only\r\n")
    mod = Mod(str(modroot))
    mod.set_metadata(description="Now it has one.")
    assert Mod(str(modroot)).description == "Now it has one."
    assert Mod(str(modroot)).display_name == "Only"


def test_set_metadata_refuses_an_empty_name(modroot):
    from dsotools.errors import ProjectError

    with pytest.raises(ProjectError):
        Mod(str(modroot)).set_metadata("   ")


def test_set_metadata_leaves_the_folder_alone(modroot):
    """The whole reason renaming is a separate operation."""
    mod = Mod(str(modroot))
    before = mod.root
    mod.set_metadata("A Completely Different Name")
    assert os.path.isdir(before)
    assert Mod(before).display_name == "A Completely Different Name"


def test_rename_folder_moves_it_and_returns_the_new_mod(modroot):
    mod = Mod(str(modroot))
    renamed = mod.rename_folder("Renamed Mod")
    assert os.path.basename(renamed.root) == "Renamed Mod"
    assert not os.path.exists(str(modroot))
    assert renamed.display_name == "Test Mod"        # metadata untouched
    assert renamed.is_listable()                     # contents came along


def test_rename_folder_sanitises_the_name(modroot):
    renamed = Mod(str(modroot)).rename_folder('bad:name/here')
    assert not set(os.path.basename(renamed.root)) & set('<>:"/\\|?*')


def test_rename_folder_refuses_to_clobber_another_mod(modroot):
    from dsotools.errors import ProjectError

    (modroot.parent / "Occupied").mkdir()
    with pytest.raises(ProjectError):
        Mod(str(modroot)).rename_folder("Occupied")
    assert os.path.isdir(str(modroot))               # nothing moved


def test_rename_folder_to_the_same_name_is_a_no_op(modroot):
    mod = Mod(str(modroot))
    assert Mod(str(modroot)).rename_folder(mod.name).root == mod.root


def test_rename_folder_refuses_an_empty_name(modroot):
    from dsotools.errors import ProjectError

    with pytest.raises(ProjectError):
        Mod(str(modroot)).rename_folder("   ")


def test_is_selected_in_reads_mod_ini(modroot):
    """Renaming the selected mod deselects it -- so the app must know which."""
    game_dir = modroot.parent.parent           # .../Darkstar One/
    (game_dir / "mod.ini").write_bytes(
        b"[DarkstarOne] ;\r\nload_mod = Test Mod ;\r\n"
    )
    assert Mod(str(modroot)).is_selected_in()

    (game_dir / "mod.ini").write_bytes(b"[DarkstarOne]\r\nload_mod = original\r\n")
    assert not Mod(str(modroot)).is_selected_in()


def test_is_selected_in_is_false_without_a_mod_ini(modroot):
    """An unknown selection must not block an edit."""
    assert not Mod(str(modroot)).is_selected_in()


def test_ini_adding_a_key_leaves_other_sections_alone():
    """A naive "append to the file" would move the key into the last section."""
    data = (
        b"[first]\r\n"
        b"a = 1\r\n"
        b"\r\n"
        b"[second]\r\n"
        b"b = 2\r\n"
    )
    f = ini.parse(data)
    f.section("first").set("c", "3")
    out = f.to_bytes()
    lines = out.decode("cp1252").splitlines()
    assert lines.index("c = 3") < lines.index("[second]")
    assert ini.parse(out).get("second", "b") == "2"


def test_ini_adding_a_key_copies_the_local_formatting():
    f = ini.parse(b"[s]\r\nkey=value\r\n")
    f.section("s").set("other", "x")
    assert b"other=x" in f.to_bytes()          # no spaces, like its neighbour


def test_ini_untouched_file_still_round_trips_after_a_parse():
    """The splice path must not disturb files that added nothing."""
    assert ini.parse(SAMPLE).to_bytes() == SAMPLE


def test_ini_value_with_a_newline_is_collapsed_not_written_raw(modroot):
    """A multi-line description used to corrupt the manifest permanently.

    The description box is multi-line, an INI value is not.  Writing the text
    verbatim truncated the value at the first newline *and* left the remainder
    as a line nothing could parse, so no later edit could remove it -- the
    orphan accumulated on every save, and one crafted description could inject
    a second `mod_name` key.
    """
    mod = Mod(str(modroot))
    mod.set_metadata(description="Adds new ships.\nRequires patch 1.2")

    raw = (modroot / "darkstarmod.ini").read_bytes()
    assert b"\r\nRequires patch" not in raw
    assert Mod(str(modroot)).description == "Adds new ships. Requires patch 1.2"

    # and the crafted case: no second mod_name appears
    mod = Mod(str(modroot))
    mod.set_metadata(description="line1\nmod_name = HIJACKED")
    parsed = ini.parse((modroot / "darkstarmod.ini").read_bytes())
    assert parsed.section("darkstarmod").duplicate_keys() == []
    assert Mod(str(modroot)).display_name == "Test Mod"


def test_ini_value_with_a_semicolon_is_refused(modroot):
    """It cannot be represented: the game reads the rest of the line as a comment."""
    from dsotools.errors import BuildError

    with pytest.raises(BuildError) as exc:
        Mod(str(modroot)).set_metadata(description="adds ships; fixes bugs")
    assert ";" in str(exc.value)
    # and nothing was written
    assert Mod(str(modroot)).description == "A mod."


def test_ini_mixed_line_endings_round_trip():
    """A hand-edited file may mix CRLF and LF; joining on one guess rewrote it all."""
    data = b"[a]\r\nk=1\nj=2\r\n"
    assert ini.parse(data).to_bytes() == data


def test_ini_round_trips_without_a_final_newline():
    data = b"[a]\r\nk = 1"
    assert ini.parse(data).to_bytes() == data


def test_ini_splice_into_a_file_with_no_final_newline():
    """The anchor line needs a terminator once something follows it."""
    f = ini.parse(b"[a]\r\nk = 1")
    f.section("a").set("new", "2")
    assert f.to_bytes() == b"[a]\r\nk = 1\r\nnew = 2"


# --------------------------------------------------------------------------
# what the engine reads: the mod's layers, and their precedence
# --------------------------------------------------------------------------


def _merged(stock, modroot):
    from dsotools.project import iter_mod_layers

    v = vfsmod.Vfs(stock.layers)
    for layer in iter_mod_layers(Mod(str(modroot))):
        v.add(layer)
    return v


def test_a_loose_mod_file_outside_3dview_overrides_stock(stock, modroot):
    """The engine reads inifiles/ loose -- the whole mod list depends on it.

    This was wrong for a long time: the confirmed "loose 3DView/ is dead"
    finding had been generalised to the entire loose tree, so a mod's override
    resolved to the *stock* file.  It made the asset index record stock, made
    check_atlas validate the stock page, and made a saved edit unreadable by
    the tool that had just written it.
    """
    _write(modroot / "inifiles" / "Goods.ini", b"[goods]\r\nmodded = 1\r\n")

    entry = _merged(stock, modroot).find("inifiles/Goods.ini")

    assert entry is not None
    assert entry.read() == b"[goods]\r\nmodded = 1\r\n"
    assert entry.origin == "mod:loose"


def test_a_loose_3dview_file_is_still_dead(stock, modroot):
    """The half of the rule that *was* established in game stays established."""
    _write(modroot / "3DView" / "OnlyLoose.xml", b"<scene/>")

    merged = _merged(stock, modroot)

    assert merged.find("3DView/OnlyLoose.xml") is None
    # ...but still visible, so the app can warn rather than shrug
    assert merged.find("3DView/OnlyLoose.xml", include_unloaded=True) is not None


def test_the_zip_beats_the_loose_copy_of_the_same_path(stock, modroot):
    """Matches the engine, and matches Deploy's conflict rule."""
    entry = _merged(stock, modroot).find("3DView/PlayerShip.xml")

    assert entry is not None
    assert entry.origin.startswith("mod:user_data")


def test_the_dead_layer_carries_no_files_from_outside_3dview(stock, modroot):
    """`only=` must not leak the manifest or inifiles into the unloaded layer.

    If it did, every loose mod file would appear twice in an
    include_unloaded listing and "what will the engine ignore?" would be wrong.
    """
    from dsotools.project import iter_mod_layers

    dead = [ly for ly in iter_mod_layers(Mod(str(modroot))) if not ly.loaded]

    assert len(dead) == 1
    assert dead[0].index()                       # it did find the 3DView files
    for vpath, _ref, _size in dead[0].index().values():
        assert vpath.lower().startswith("3dview/")


# --------------------------------------------------------------------------
# deploy: making a mod load the way its author thinks it does
# --------------------------------------------------------------------------


def test_deploy_plan_relocates_a_loose_3dview_file(modroot, stock):
    """The plan reads; it must not write."""
    _write(modroot / "3DView" / "objects" / "Only_Loose.xml", b"<scene/>")
    before = sorted(p.name for p in modroot.iterdir())

    plan = Mod(str(modroot)).deploy_plan(stock=stock)

    assert plan.relocate == ["3DView/objects/Only_Loose.xml"]
    assert not plan.empty
    assert sorted(p.name for p in modroot.iterdir()) == before


def test_deploy_plan_never_touches_a_path_the_zip_already_has(modroot, stock):
    """Two copies of one path is a decision, not a defect the tool may settle.

    The fixture's loose tree duplicates the zip exactly, which is the shape the
    real mod had.  Overwriting the zip from the loose copy would be a coin flip
    with the author's work on one side.
    """
    plan = Mod(str(modroot)).deploy_plan(stock=stock)

    assert sorted(plan.conflicts) == [
        "3DView/PlayerShip.xml",
        "3DView/textures/new.dds",
    ]
    assert plan.relocate == []
    assert plan.empty                       # a conflict is not work deploy can do


def test_deploy_moves_the_file_into_the_zip_and_removes_the_original(modroot, stock):
    _write(modroot / "3DView" / "objects" / "Only_Loose.xml", b"<scene/>")
    mod = Mod(str(modroot))

    result = mod.apply_deploy_plan(mod.deploy_plan(stock=stock), stock=stock)

    with zipfile.ZipFile(modroot / "user_data.zip") as zf:
        assert zf.read("3DView/objects/Only_Loose.xml") == b"<scene/>"
        assert zf.read("3DView/PlayerShip.xml") == b"<edited scene/>"   # untouched
    assert not (modroot / "3DView" / "objects" / "Only_Loose.xml").exists()
    assert result.removed == ["3DView/objects/Only_Loose.xml"]
    assert result.clean


def test_deploy_works_while_the_inventory_holds_the_zip_open(modroot, stock):
    """Deploy must not be blocked by this object's own read handle.

    ``files()`` keeps ``user_data.zip`` open to serve lazy reads, and Deploy
    replaces that same archive.  POSIX allows renaming over an open file, so
    this passed everywhere until the first Windows run, where it failed with a
    bare ``PermissionError: [WinError 5]`` naming the temp file rather than the
    open handle.  Reading the inventory first is what makes the collision
    certain rather than incidental.
    """
    _write(modroot / "3DView" / "objects" / "Only_Loose.xml", b"<scene/>")
    mod = Mod(str(modroot))
    assert mod.files()                       # materialise the open handle

    result = mod.apply_deploy_plan(mod.deploy_plan(stock=stock), stock=stock)

    assert result.clean
    with zipfile.ZipFile(modroot / "user_data.zip") as zf:
        assert zf.read("3DView/objects/Only_Loose.xml") == b"<scene/>"


def test_close_releases_the_zip_and_the_inventory_survives_it(modroot):
    """close() is the documented way out, and must not be one-shot."""
    mod = Mod(str(modroot))
    assert mod.files()
    mod.close()

    assert mod._zf is None
    mod.close()                              # idempotent
    assert mod.files()                       # and reopens on demand


def test_deploy_prunes_the_directory_it_empties(modroot, stock):
    """An empty 3DView/objects/ still says 'this mod ships loose 3D content'."""
    _write(modroot / "3DView" / "objects" / "Only_Loose.xml", b"<scene/>")
    mod = Mod(str(modroot))

    mod.apply_deploy_plan(mod.deploy_plan(stock=stock), stock=stock)

    assert not (modroot / "3DView" / "objects").exists()
    # ...but not the directories that still hold the duplicated files
    assert (modroot / "3DView").exists()
    assert modroot.exists()


def test_deploy_writes_the_zip_before_deleting_anything(modroot, stock, monkeypatch):
    """The failure that loses work must not be reachable.

    If the delete fails the file exists twice and the engine reads the zip, so
    the mod is right.  If the order were reversed, a failed zip write would take
    the only copy with it.
    """
    _write(modroot / "3DView" / "objects" / "Only_Loose.xml", b"<scene/>")
    mod = Mod(str(modroot))

    def refuse(path):
        raise OSError("in use by another process")

    monkeypatch.setattr(os, "remove", refuse)
    result = mod.apply_deploy_plan(mod.deploy_plan(stock=stock), stock=stock)

    with zipfile.ZipFile(modroot / "user_data.zip") as zf:
        assert zf.read("3DView/objects/Only_Loose.xml") == b"<scene/>"
    assert (modroot / "3DView" / "objects" / "Only_Loose.xml").exists()
    assert [p for p, _ in result.not_removed] == ["3DView/objects/Only_Loose.xml"]
    assert not result.clean


def test_deploy_adds_the_items_ini_that_makes_a_mod_visible(tmp_path, stock):
    root = tmp_path / "Customization" / "Invisible"
    root.mkdir(parents=True)
    (root / "darkstarmod.ini").write_bytes(
        b"[darkstarmod]\r\nmod_name = Invisible\r\nmod_desc = d\r\n"
    )
    mod = Mod(str(root))
    assert not mod.is_listable()

    plan = mod.deploy_plan(stock=stock)
    assert plan.add_items_ini and plan.items_ini_source == "stock"

    result = mod.apply_deploy_plan(plan, stock=stock)

    assert Mod(str(root)).is_listable()
    assert (root / "inifiles" / "items.ini").read_bytes() == b"[items]\r\n"
    assert result.items_ini_source == "stock"


def test_deploy_writes_a_stub_items_ini_when_no_game_is_open(tmp_path):
    root = tmp_path / "Customization" / "Invisible"
    root.mkdir(parents=True)
    (root / "darkstarmod.ini").write_bytes(
        b"[darkstarmod]\r\nmod_name = Invisible\r\nmod_desc = d\r\n"
    )
    mod = Mod(str(root))

    plan = mod.deploy_plan()
    assert plan.items_ini_source == "stub"
    result = mod.apply_deploy_plan(plan)

    assert Mod(str(root)).is_listable()
    assert result.items_ini_source == "stub"
    assert b"placeholder" in (root / "inifiles" / "items.ini").read_bytes()


def test_deploy_refuses_a_plan_made_for_another_mod(modroot, tmp_path, stock):
    other = tmp_path / "Customization" / "Other"
    other.mkdir(parents=True)
    (other / "darkstarmod.ini").write_bytes(
        b"[darkstarmod]\r\nmod_name = Other\r\nmod_desc = d\r\n"
    )
    plan = Mod(str(other)).deploy_plan(stock=stock)

    from dsotools.errors import ProjectError

    with pytest.raises(ProjectError):
        Mod(str(modroot)).apply_deploy_plan(plan, stock=stock)


def test_deploy_is_idempotent(modroot, stock):
    _write(modroot / "3DView" / "objects" / "Only_Loose.xml", b"<scene/>")
    mod = Mod(str(modroot))
    mod.apply_deploy_plan(mod.deploy_plan(stock=stock), stock=stock)

    again = Mod(str(modroot))
    plan = again.deploy_plan(stock=stock)

    assert plan.relocate == []
    assert plan.empty


def test_deploy_clears_the_warning_it_exists_to_clear(tmp_path, stock):
    """End to end: PRJ005 before, gone after, and nothing else broken."""
    from dsotools import validate as validatemod

    root = tmp_path / "Customization" / "Dead"
    root.mkdir(parents=True)
    (root / "darkstarmod.ini").write_bytes(
        b"[darkstarmod]\r\nmod_name = Dead\r\nmod_desc = d\r\n"
    )
    _write(root / "inifiles" / "items.ini", b"[items]\r\n")
    _write(root / "3DView" / "Ship.xml", b"<scene/>")

    before = validatemod.validate_mod(Mod(str(root)), stock)
    assert "PRJ005" in before.by_code()

    mod = Mod(str(root))
    mod.apply_deploy_plan(mod.deploy_plan(stock=stock), stock=stock)

    after = validatemod.validate_mod(Mod(str(root)), stock)
    assert "PRJ005" not in after.by_code()
    assert "PRJ006" not in after.by_code()


# --------------------------------------------------------------------------
# open handles: the thing Windows will not let you replace
# --------------------------------------------------------------------------


def test_a_second_mod_object_does_not_block_the_write(modroot):
    """The reported bug: "save to mod" failed whenever a user_data.zip existed.

    A mod picker builds one Mod per discovered mod and keeps them for its
    labels; the app then saves through a different instance. On Windows the
    picker's handle blocks the save, and a Mod that closes only *its own*
    handle cannot fix that. POSIX never notices.
    """
    listing = Mod(str(modroot))
    listing.files()                              # opens and caches user_data.zip
    editing = Mod(str(modroot))

    editing.deploy({"3DView/New.xml": b"<scene/>"})

    with zipfile.ZipFile(modroot / "user_data.zip") as zf:
        assert zf.read("3DView/New.xml") == b"<scene/>"


def test_is_listable_does_not_leave_the_zip_open(modroot):
    """Answering one question about one path must not cost a held handle."""
    from dsotools.project import _OPEN_ZIP_MODS

    mod = Mod(str(modroot))

    assert mod.is_listable()

    assert mod._zf is None
    assert mod not in _OPEN_ZIP_MODS


def test_is_listable_still_finds_items_ini_in_the_zip(tmp_path):
    """The zip is a legitimate place for it; the faster check must still look."""
    root = tmp_path / "Z"
    root.mkdir()
    (root / "darkstarmod.ini").write_bytes(
        b"[darkstarmod]\r\nmod_name = Z\r\nmod_desc = d\r\n"
    )
    with zipfile.ZipFile(root / "user_data.zip", "w") as zf:
        zf.writestr("inifiles/items.ini", b"[items]\r\n")

    assert Mod(str(root)).is_listable()


def test_is_listable_says_no_when_the_file_is_absent(tmp_path):
    root = tmp_path / "Z"
    root.mkdir()
    (root / "darkstarmod.ini").write_bytes(
        b"[darkstarmod]\r\nmod_name = Z\r\nmod_desc = d\r\n"
    )
    with zipfile.ZipFile(root / "user_data.zip", "w") as zf:
        zf.writestr("3DView/A.xml", b"<scene/>")

    assert not Mod(str(root)).is_listable()


def test_close_unregisters_so_an_abandoned_mod_leaves_nothing(modroot):
    from dsotools.project import _OPEN_ZIP_MODS

    mod = Mod(str(modroot))
    mod.files()
    assert mod in _OPEN_ZIP_MODS

    mod.close()

    assert mod not in _OPEN_ZIP_MODS


# --------------------------------------------------------------------------
# which mod folders the engine reads loose
# --------------------------------------------------------------------------


def test_static_images_is_zip_only(tmp_path):
    r"""Measured 2026-08-23, the same way 3DView/ and images/ were.

    An edited ``staticImages\Starmap.dds`` shipped loose in a mod did nothing
    in game; the identical file inside ``user_data.zip`` was picked up. Until
    that run this was carried as *untested* rather than assumed loose -- which
    is the only reason the correction is one line, because the assumption in
    force was the wrong one.
    """
    assert "staticImages" in ZIP_ONLY_ROOTS
    assert Mod.deploy_target("staticImages/Starmap.dds") == "zip"
    assert Mod.deploy_target("staticImages/IconGoods/Ore.aim") == "zip"


@pytest.mark.parametrize("vpath,where", [
    ("3DView/PlayerShip.xml", "zip"),
    ("images/TexPage1.aim", "zip"),
    ("staticImages/HUD.aim", "zip"),
    ("inifiles/items.ini", "loose"),
    ("sound/sfx(2d)/grp_USER/x.wav", "loose"),
    ("strings/user_strings.res", "loose"),
    ("scripts/MY_MISSION.lua", "loose"),
])
def test_every_root_goes_where_the_engine_reads_it(vpath, where):
    """One table, because each row cost an in-game experiment to establish."""
    assert Mod.deploy_target(vpath) == where


def test_a_loose_zip_only_file_is_dead_whichever_root_it_is_in(tmp_path):
    mod_root = tmp_path / "M"
    (mod_root / "inifiles").mkdir(parents=True)
    (mod_root / "darkstarmod.ini").write_bytes(
        b"[darkstarmod]\r\nmod_name = M\r\nmod_desc = d\r\n")
    (mod_root / "inifiles" / "items.ini").write_bytes(b"[i]\r\n")
    for relative in ("3DView/A.xml", "images/B.aim", "staticImages/C.dds",
                     "sound/D.wav"):
        target = mod_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"x")

    dead = {f.vpath for f in Mod(str(mod_root)).dead_files()}

    assert dead == {"3DView/A.xml", "images/B.aim", "staticImages/C.dds"}


# --------------------------------------------------------------------------
# where a mod may put a file
# --------------------------------------------------------------------------
#
# check_mod_path is the delivery matrix as a function.  It exists because
# "add a file at a path of your choosing" is only safe if something knows
# which paths the engine actually reads -- a file in the wrong folder looks
# exactly like a file that does not work.


def test_the_content_roots_are_the_delivery_matrix():
    assert set(MOD_CONTENT_ROOTS) == {
        "3DView", "images", "inifiles", "scripts", "sound", "staticImages",
        "strings"}


@pytest.mark.parametrize("vpath", [
    "3DView/textures/mine.dds",
    "inifiles/Goods.ini",
    "scripts/mine.lua",
    "sound/sfx(2d)/grp_USER/beep.wav",
    "strings/user_strings.res",
    "images/page.aim",
    "3DView//textures/./odd.dds",
])
def test_a_path_the_engine_reads_is_allowed(vpath):
    assert check_mod_path(vpath) is None


def test_a_folder_the_engine_never_reads_is_refused():
    why = check_mod_path("mystuff/x.dds")
    assert why is not None and "mystuff/" in why
    # And it says where things *do* go, rather than only what is wrong.
    assert "3DView/" in why


def test_the_top_of_the_mod_folder_is_refused():
    why = check_mod_path("loose.dds")
    assert why is not None and "darkstarmod.ini" in why


def test_a_path_on_this_computer_is_recognised_as_such():
    for bad in ("C:/tmp/x.dds", "/etc/passwd"):
        assert check_mod_path(bad) is not None


def test_stepping_outside_the_mod_is_refused():
    assert check_mod_path("../escape.dds") is not None
    assert check_mod_path("3DView/../../x.dds") is not None


def test_the_players_own_folders_are_not_mod_content():
    assert "player" in (check_mod_path("save/game.sav") or "").lower()


def test_the_game_folder_payload_is_a_different_operation():
    why = check_mod_path("root/lua/mission/X.lua")
    assert why is not None and "game installation" in why


def test_nothing_is_not_a_path():
    assert check_mod_path("") is not None
    assert check_mod_path("   ") is not None


# --------------------------------------------------------------------------
# moving scripts out of the archive
# --------------------------------------------------------------------------


def test_an_archive_with_no_scripts_lists_none(tmp_path):
    mod = Mod.create(str(tmp_path / "M"), name="M")
    assert mod.zipped_scripts() == []
    assert mod.unzip_scripts() == {"moved": [], "conflicts": [], "identical": []}
