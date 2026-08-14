"""
The diagnostics engine and the sound database.

Every rule gets a positive and a negative case.  A validator that only ever
fires is as useless as one that never does -- both get ignored, and then the
real findings go with them.

The sound fixtures reproduce a defect found in a real mod: two voice lines
declared under ``grp_VOICE\\STATION\\`` while the files ship under
``grp_VOICE\\TRADER\\``.  That typo shows up in *both* directions at once, and
that pairing is what makes it a diagnosis rather than a guess.
"""

from __future__ import annotations

import zipfile

import pytest

from dsotools import validate
from dsotools import vfs as vfsmod
from dsotools.formats import sounddb
from dsotools.project import Mod
from dsotools.validate import Severity


# --------------------------------------------------------------------------
# sound database
# --------------------------------------------------------------------------

SOUNDS = b"""<?xml version="1.0" encoding="ISO-8859-1"?>
<ASE_Database>
  <DocumentProperties><Author>x</Author><Version>2.8.1</Version></DocumentProperties>
  <Group Name="USER" Volume="2.0" Priority="10">
    <Stream Name="Track" Resrc="%MOD%sound\\music(stream)\\grp_USER\\Track.mp3"
            Channels="2" Duration=":110" Freq="44100" />
    <Stream Name="Missing" Resrc="%MOD%sound\\music(stream)\\grp_USER\\Gone.mp3" />
    <Sound2D Name="Click" Resrc="sound\\sfx(2d)\\grp_FX\\Click.wav" Channels="1" Freq="22050" />
  </Group>
</ASE_Database>
"""


def test_sounddb_parses_groups_and_entries():
    db = sounddb.parse(SOUNDS)
    assert db.version == "2.8.1"
    names = [e.name for e in db.entries()]
    assert names == ["Track", "Missing", "Click"]


def test_sounddb_mod_prefix_and_backslashes():
    """%MOD% expands to the mod root; separators here are backslashes."""
    db = sounddb.parse(SOUNDS)
    e = db.resolve("Track")
    assert e.is_mod_relative
    assert e.path() == "sound/music(stream)/grp_USER/Track.mp3"
    game = db.resolve("Click")
    assert not game.is_mod_relative
    assert game.path() == "sound/sfx(2d)/grp_FX/Click.wav"
    assert game.frequency == 22050


def test_sounddb_missing_and_unreferenced_are_both_reported():
    db = sounddb.parse(SOUNDS)
    have = {"sound/music(stream)/grp_user/track.mp3", "sound/sfx(2d)/grp_fx/spare.wav"}
    missing = db.missing(lambda p, mod_rel: p.lower() in have)
    assert [e.name for e in missing] == ["Missing", "Click"]
    assert db.unreferenced(sorted(have)) == ["sound/sfx(2d)/grp_fx/spare.wav"]


def test_sounddb_rejects_wrong_root():
    from dsotools.errors import ParseError

    with pytest.raises(ParseError):
        sounddb.parse(b"<?xml version='1.0'?><WalhallaScene/>")


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------


def _w(p, data=b"x"):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


SCENE_OK = (
    b'<?xml version="1.0"?>\r\n'
    b'<WalhallaScene Version="2.00">\r\n'
    b'\t<Object Type=".?AVCWorldRoot@@">\r\n\t\t<AttachedObjects>\r\n'
    b'\t\t\t<Object Type=".?AVCMesh@@" Name="m" Resrc3DO="objects/x.3do">\r\n'
    b'\t\t\t\t<EffectContainer Path="blender/mat_main.bsd9">\r\n'
    b'\t\t\t\t\t<Textures Number="1">\r\n\t\t\t\t\t\ttextures/a_col.dds\r\n\t\t\t\t\t</Textures>\r\n'
    b"\t\t\t\t</EffectContainer>\r\n"
    b"\t\t\t</Object>\r\n\t\t</AttachedObjects>\r\n\t</Object>\r\n</WalhallaScene>\r\n"
)

# a .3do root header declaring submesh_total = 1
def _model(submesh_total=1):
    import struct

    b = bytearray(0x48 + 4 * max(1, submesh_total))
    b[0:4] = b"OD3 "
    struct.pack_into("<I", b, 0x30, submesh_total)
    return bytes(b)


@pytest.fixture()
def world(tmp_path):
    """A stock tree plus a mod, wired for the scene rules."""
    stock_root = tmp_path / "extracted" / "ds_3dgen" / "3DView"
    (stock_root / "objects").mkdir(parents=True)
    (stock_root / "textures").mkdir(parents=True)
    (stock_root / "objects" / "x.3do").write_bytes(_model(1))
    (stock_root / "textures" / "a_col.dds").write_bytes(b"DDS ")
    (tmp_path / "extracted" / "ds_add" / "inifiles").mkdir(parents=True)
    (tmp_path / "extracted" / "ds_add" / "inifiles" / "items.ini").write_bytes(b"[i]\r\n")
    stock = vfsmod.from_extracted(str(tmp_path / "extracted"))

    mod = tmp_path / "Customization" / "M"
    mod.mkdir(parents=True)
    (mod / "darkstarmod.ini").write_bytes(b"[darkstarmod]\r\nmod_name = M\r\nmod_desc = d\r\n")
    _w(mod / "inifiles" / "items.ini", b"[i]\r\n")
    return stock, mod


def _zip(mod, entries):
    with zipfile.ZipFile(mod / "user_data.zip", "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)


# --------------------------------------------------------------------------
# rules
# --------------------------------------------------------------------------


def test_clean_mod_produces_no_errors(world):
    stock, mod = world
    _zip(mod, {"3DView/PlayerShip.xml": SCENE_OK})
    report = validate.validate_mod(Mod(str(mod)), stock)
    assert report.ok, [d for d in report if d.severity == Severity.ERROR]


def test_missing_items_ini_is_an_error(world):
    stock, mod = world
    (mod / "inifiles" / "items.ini").unlink()
    report = validate.validate_mod(Mod(str(mod)), stock)
    codes = report.by_code()
    assert "PRJ004" in codes
    assert codes["PRJ004"][0].severity == Severity.ERROR
    assert codes["PRJ004"][0].fix                        # offers the stock file
    assert not report.ok


def test_loose_3dview_file_is_flagged_dead(world):
    stock, mod = world
    _w(mod / "3DView" / "Thing.xml", SCENE_OK)
    report = validate.validate_mod(Mod(str(mod)), stock)
    prj005 = report.by_code().get("PRJ005", [])
    assert [d.path for d in prj005] == ["3DView/Thing.xml"]
    assert prj005[0].severity == Severity.WARNING


def test_zip_and_loose_duplication_is_flagged(world):
    stock, mod = world
    _zip(mod, {"3DView/PlayerShip.xml": SCENE_OK})
    _w(mod / "3DView" / "PlayerShip.xml", SCENE_OK)
    report = validate.validate_mod(Mod(str(mod)), stock)
    assert "PRJ006" in report.by_code()


def test_model_rules_run_over_a_mods_own_models(world):
    """The MDL family, end to end through `validate_mod`.

    The rules themselves are covered in `test_threedo.py`; this is the wiring,
    which is its own kind of bug -- a rule nothing calls is worth nothing.
    """
    from test_threedo import TRIANGLE, lod_chunk, model_bytes, one_triangle

    stock, mod = world
    # Second submesh draws the first one's triangle too, each with the other's
    # material: MDL002, and an error.
    broken = model_bytes(
        [lod_chunk(**one_triangle(
            positions=TRIANGLE * 2,
            indices=[0, 1, 2, 3, 4, 5],
            submeshes=[(0, 0, 1, 0, 3), (1, 0, 2, 3, 3)],
        ))],
        [2],
    )
    _zip(mod, {"3DView/objects/broken.3do": broken})

    report = validate.validate_mod(Mod(str(mod)), stock)
    mdl = report.by_code().get("MDL002", [])
    assert [d.path for d in mdl] == ["3DView/objects/broken.3do"]
    assert mdl[0].severity == Severity.ERROR
    assert not report.ok


def test_model_rules_leave_a_loose_3dview_model_to_prj005(world):
    """A file the engine never reads has exactly one thing wrong with it.

    Reporting its internals as well would bury that, and the fix for a dead
    file is Deploy, not a mesh edit.
    """
    from test_threedo import clean_model

    stock, mod = world
    _w(mod / "3DView" / "objects" / "loose.3do", clean_model())
    report = validate.validate_mod(Mod(str(mod)), stock)
    codes = report.by_code()
    assert [d.path for d in codes.get("PRJ005", [])] == ["3DView/objects/loose.3do"]
    assert not [c for c in codes if c.startswith("MDL")]


def test_file_identical_to_stock_is_info_not_error(world):
    stock, mod = world
    _w(mod / "inifiles" / "Goods.ini", b"[goods]\r\n")
    _w(mod.parent.parent / "extracted" / "ds_add" / "inifiles" / "Goods.ini", b"[goods]\r\n")
    stock2 = vfsmod.from_extracted(str(mod.parent.parent / "extracted"))
    report = validate.validate_mod(Mod(str(mod)), stock2)
    prj002 = report.by_code().get("PRJ002", [])
    assert any(d.path.lower().endswith("goods.ini") for d in prj002)
    assert all(d.severity == Severity.INFO for d in prj002)
    assert report.ok


def test_unresolved_reference_is_an_error(world):
    stock, mod = world
    broken = SCENE_OK.replace(b"textures/a_col.dds", b"textures/nope.dds")
    _zip(mod, {"3DView/Broken.xml": broken})
    report = validate.validate_mod(Mod(str(mod)), stock)
    scn002 = report.by_code().get("SCN002", [])
    assert scn002 and scn002[0].severity == Severity.ERROR
    assert "nope.dds" in scn002[0].message


def test_effectcontainer_count_mismatch_is_an_error(world):
    """SCN001: the invariant a DCC tool breaks silently on export."""
    stock, mod = world
    # model now declares 3 submeshes; the scene still has 1 EffectContainer
    (mod.parent.parent / "extracted" / "ds_3dgen" / "3DView" / "objects" / "x.3do").write_bytes(
        _model(3)
    )
    stock2 = vfsmod.from_extracted(str(mod.parent.parent / "extracted"))
    _zip(mod, {"3DView/PlayerShip.xml": SCENE_OK})
    report = validate.validate_mod(Mod(str(mod)), stock2)
    scn001 = report.by_code().get("SCN001", [])
    assert scn001, [d.code for d in report]
    assert "3 submesh" in scn001[0].message
    assert scn001[0].severity == Severity.ERROR


def test_malformed_scene_is_an_error_not_a_crash(world):
    stock, mod = world
    _zip(mod, {"3DView/Bad.xml": b'<?xml version="1.0"?><WalhallaScene><oops</WalhallaScene>'})
    report = validate.validate_mod(Mod(str(mod)), stock)
    assert "SCN003" in report.by_code()
    assert not report.ok


def test_sound_typo_appears_in_both_directions(world):
    """A defect met in a real mod, reproduced.

    Declared under STATION, shipped under TRADER: one SND001 for the missing
    target and one SND002 for the orphaned file. Seeing both is what identifies
    it as a typo rather than a deliberate omission.
    """
    stock, mod = world
    (mod / "user_sounds.xml").write_bytes(
        b'<?xml version="1.0"?><ASE_Database><Group Name="V">'
        b'<Stream Name="Line" Resrc="%MOD%sound\\radio(stream)\\grp_VOICE\\STATION\\a.mp3" />'
        b"</Group></ASE_Database>"
    )
    _w(mod / "sound" / "radio(stream)" / "grp_VOICE" / "TRADER" / "a.mp3", b"ID3")
    report = validate.validate_mod(Mod(str(mod)), stock)
    codes = report.by_code()
    assert "SND001" in codes and "SND002" in codes
    assert "STATION" in codes["SND001"][0].location
    assert "TRADER" in codes["SND002"][0].path


def test_report_caps_findings_per_rule_but_keeps_the_count(world):
    """One broken reference can produce thousands of identical rows.

    The first version of this produced 24,000 findings and took the window down
    with it. The cap keeps the exact count and only trims the examples -- and it
    reports what it trimmed, because a silent truncation reads as "that's all
    there is".
    """
    from dsotools.validate import Diagnostic, Report

    r = Report(limit_per_rule=3)
    for i in range(10):
        r.add(Diagnostic("SCN002", Severity.ERROR, f"missing {i}", path=f"a{i}.xml"))
    r.add(Diagnostic("PRJ002", Severity.INFO, "redundant", path="b.ini"))

    assert len(r.diagnostics) == 4                 # 3 kept + 1 other rule
    assert r.totals["SCN002"] == 10                # the count is exact
    assert r.truncated() == {"SCN002": 7}          # and the loss is stated
    assert not r.ok


def test_report_with_no_cap_keeps_everything(world):
    from dsotools.validate import Diagnostic, Report

    r = Report(limit_per_rule=0)
    for i in range(50):
        r.add(Diagnostic("X", Severity.WARNING, str(i)))
    assert len(r.diagnostics) == 50
    assert r.truncated() == {}


def test_validation_reports_progress(world):
    stock, mod = world
    _zip(mod, {"3DView/A.xml": SCENE_OK, "3DView/B.xml": SCENE_OK})
    seen = []
    validate.validate_mod(Mod(str(mod)), stock, progress=lambda d, t, l: seen.append((d, t)))
    assert seen, "a long validation with no sign of life is indistinguishable from a hang"


def test_report_ordering_and_summary(world):
    stock, mod = world
    (mod / "inifiles" / "items.ini").unlink()
    _w(mod / "3DView" / "Dead.xml", SCENE_OK)
    report = validate.validate_mod(Mod(str(mod)), stock)
    sevs = [d.severity for d in report]
    assert sevs == sorted(sevs, key=Severity.rank)      # errors first
    counts = report.counts()
    assert counts.get(Severity.ERROR, 0) >= 1
    assert all(k in d for d in [report.diagnostics[0].as_dict()] for k in ("code", "severity"))


def test_atlas_rules_catch_a_page_resized_without_its_index(tmp_path):
    """TEX002: the exact failure AtlasPage.rescale() exists to prevent."""
    try:
        from PIL import Image
    except ImportError:
        pytest.skip("Pillow not installed")

    from dsotools.formats import a2d, aim

    root = tmp_path / "extracted" / "ds_interface"
    (root / "scripts").mkdir(parents=True)
    (root / "images").mkdir(parents=True)
    # index still describes a 64x64 page; the page itself is now 32x32
    (root / "images" / "Page.aim").write_bytes(
        aim.from_image(Image.new("RGBA", (32, 32), (0, 0, 0, 255)))
    )
    subs = [a2d.SubImage(r"images\A.aim", 32, 32, 16, 16, r"images\Page.aim")]
    (root / "scripts" / "TexPage1.tex").write_bytes(
        a2d.build(a2d.TexturePage(r"images\Page.aim", subs))
    )
    stock = vfsmod.from_extracted(str(tmp_path / "extracted"))

    diags = validate.check_atlas(stock, "scripts/TexPage1.tex")
    codes = {d.code for d in diags}
    assert "TEX002" in codes
    assert all(d.severity == Severity.ERROR for d in diags if d.code == "TEX002")


def test_atlas_rules_are_quiet_on_a_consistent_page(tmp_path):
    try:
        from PIL import Image
    except ImportError:
        pytest.skip("Pillow not installed")

    from dsotools.formats import a2d, aim

    root = tmp_path / "extracted" / "ds_interface"
    (root / "scripts").mkdir(parents=True)
    (root / "images").mkdir(parents=True)
    (root / "images" / "Page.aim").write_bytes(
        aim.from_image(Image.new("RGBA", (64, 64), (0, 0, 0, 255)))
    )
    subs = [a2d.SubImage(r"images\A.aim", 0, 0, 16, 16, r"images\Page.aim")]
    (root / "scripts" / "TexPage1.tex").write_bytes(
        a2d.build(a2d.TexturePage(r"images\Page.aim", subs))
    )
    stock = vfsmod.from_extracted(str(tmp_path / "extracted"))
    assert validate.check_atlas(stock, "scripts/TexPage1.tex") == []


def test_validation_works_without_a_stock_tree(world):
    """A mod can be checked before the game has been located."""
    _stock, mod = world
    _w(mod / "3DView" / "Dead.xml", SCENE_OK)
    report = validate.validate_mod(Mod(str(mod)), None)
    assert "PRJ005" in report.by_code()


# --------------------------------------------------------------------------
# progress reporting
# --------------------------------------------------------------------------


def _progress_calls(mod, stock=None):
    calls = []
    validate.validate_mod(mod, stock, progress=lambda d, t, l: calls.append((d, t, l)))
    return calls


def test_progress_never_reports_a_zero_total(world):
    """The bug: a texture-only mod made the bar sit at 0% for the whole run.

    Such a mod has no .tex and no .xml, so the only progress call was the file
    loops' final ``tick(0, 0)``.  The window turned that into a *determinate*
    bar at 0% -- which reads as "stuck", and is indistinguishable from a hang.
    """
    stock, mod = world
    _zip(mod, {"3DView/textures/hull_col.dds": b"DDS "})
    calls = _progress_calls(Mod(str(mod)), stock)
    assert calls, "a run with no scenes still has to report progress"
    assert all(total > 0 for _done, total, _label in calls)


def test_progress_ends_at_one_hundred_percent(world):
    """Reporting before the work meant the last call could never be total/total."""
    stock, mod = world
    _zip(mod, {"3DView/PlayerShip.xml": SCENE_OK})
    calls = _progress_calls(Mod(str(mod)), stock)
    done, total, _label = calls[-1]
    assert done == total


def test_progress_is_monotonic_and_bounded(world):
    stock, mod = world
    _zip(mod, {"3DView/PlayerShip.xml": SCENE_OK})
    calls = _progress_calls(Mod(str(mod)), stock)
    dones = [d for d, _t, _l in calls]
    assert dones == sorted(dones)
    assert all(0 <= d <= t for d, t, _l in calls)


def test_progress_covers_the_structural_rules_too(world):
    """Without a baseline there are no file loops at all, only rules.

    Those rules *are* the run for most mods, so leaving them unreported is what
    made a working validation look dead.
    """
    _stock, mod = world
    calls = _progress_calls(Mod(str(mod)), None)
    assert len(calls) > 1
    assert calls[-1][0] == calls[-1][1]


def test_progress_total_matches_the_work_actually_done(world):
    """A total that drifts from reality is the other way to look stuck."""
    stock, mod = world
    _zip(mod, {
        "3DView/A.xml": SCENE_OK,
        "3DView/B.xml": SCENE_OK,
        "3DView/textures/hull_col.dds": b"DDS ",
    })
    calls = _progress_calls(Mod(str(mod)), stock)
    totals = {t for _d, t, _l in calls}
    assert len(totals) == 1, "total must be known up front, not grow as it goes"


# --------------------------------------------------------------------------
# a bad file must not take the whole report with it
# --------------------------------------------------------------------------


def test_a_malformed_tex_becomes_a_diagnostic_not_an_exception(world):
    """One unreadable file must degrade to a finding, like every other rule.

    ``a2d.UnsupportedTex`` derived from plain ``Exception`` rather than
    ``DsoError``, so ``check_atlas``'s guard missed it and the exception escaped
    ``validate_mod`` -- throwing away every other finding in the report because
    of one bad file, and leaving the progress bar short of 100%.
    """
    stock, mod = world
    _zip(mod, {"scripts/P.tex": b"not an A2DFILE at all"})
    report = validate.validate_mod(Mod(str(mod)), stock)     # must not raise
    assert isinstance(report, validate.Report)


def test_every_library_exception_is_a_dsoerror():
    """The contract errors.py states, enforced rather than assumed."""
    import pkgutil
    import importlib

    from dsotools.errors import DsoError
    import dsotools

    offenders = []
    for mod in pkgutil.walk_packages(dsotools.__path__, "dsotools."):
        try:
            m = importlib.import_module(mod.name)
        except ImportError:                     # optional extras
            continue
        for name in dir(m):
            obj = getattr(m, name)
            if (
                isinstance(obj, type)
                and issubclass(obj, Exception)
                and obj.__module__ == m.__name__
                and not issubclass(obj, DsoError)
            ):
                offenders.append(f"{m.__name__}.{name}")
    assert not offenders, offenders


def test_missing_pillow_skips_atlas_rules_instead_of_erroring(world, monkeypatch):
    """Pillow is an optional extra; its absence is not a defect in the mod.

    Without it every .tex produced a TEX005 *error* and the mod was reported
    "not deployable" -- on a default install, for a dependency the user was
    never told they needed.
    """
    import dsotools.edit.atlas as atlasmod

    stock, mod = world
    _zip(mod, {"scripts/P.tex": b"not a tex"})
    monkeypatch.setattr(atlasmod, "have_pillow", lambda: False)

    report = validate.validate_mod(Mod(str(mod)), stock)
    assert not [d for d in report if d.code.startswith("TEX")]
    assert any("Pillow" in reason for reason in report.skipped.values())


def test_skipped_rules_are_recorded_when_there_is_no_baseline(world):
    """"Not checked" must never be presented as "clean"."""
    _stock, mod = world
    report = validate.validate_mod(Mod(str(mod)), None)
    assert report.skipped, "rules needing stock were skipped silently"


def test_prj002_does_not_tell_you_to_delete_the_file_the_mod_needs(world):
    """items.ini is identical to stock in every good mod, and must stay.

    Advising its removal is instructions to break the mod: without it the game
    silently refuses to list the folder at all (PRJ004).
    """
    stock, mod = world
    # the fixture already ships items.ini byte-identical to stock, which is
    # what every well-formed mod does

    report = validate.validate_mod(Mod(str(mod)), stock)
    found = [d for d in report.by_code().get("PRJ002", [])
             if d.path.lower().endswith("items.ini")]

    assert found, "items.ini is identical to stock, so PRJ002 should still fire"
    assert "remove" not in found[0].fix.lower()
    assert "required" in found[0].message.lower()


# --------------------------------------------------------------------------
# string tables
# --------------------------------------------------------------------------


def _stock_with_strings(tmp_path, pairs):
    """Add a stock ``global.res`` to the extracted tree and rebuild the VFS."""
    from dsotools.formats import res as resfmt

    target = tmp_path / "extracted" / "ds_add" / "strings" / "ENG"
    target.mkdir(parents=True, exist_ok=True)
    (target / "global.res").write_bytes(resfmt.build(resfmt.from_pairs(pairs)))
    return vfsmod.from_extracted(str(tmp_path / "extracted"))


def test_a_mod_without_a_string_table_says_nothing(world):
    _stock, mod = world
    assert validate.check_string_table(Mod(str(mod))) == []


def test_an_unreadable_string_table_is_an_error(world):
    _stock, mod = world
    _w(mod / "strings" / "user_strings.res", b"\xff\xff\xff\xff garbage")
    found = validate.check_string_table(Mod(str(mod)))
    assert [d.code for d in found] == ["PRJ008"]
    assert found[0].severity == Severity.ERROR


def test_a_colliding_string_table_is_a_warning(world):
    from dsotools.formats import res as resfmt

    _stock, mod = world
    table = resfmt.StringTable([resfmt.StringEntry(9, "one"), resfmt.StringEntry(9, "two")])
    _w(mod / "strings" / "user_strings.res", resfmt.build(table))
    found = validate.check_string_table(Mod(str(mod)))
    assert [d.code for d in found] == ["PRJ008"]
    assert found[0].severity == Severity.WARNING


def test_a_good_string_table_is_quiet(world):
    from dsotools.formats import res as resfmt

    _stock, mod = world
    _w(mod / "strings" / "user_strings.res",
       resfmt.build(resfmt.from_pairs([("ID_MINE", "hello")])))
    assert validate.check_string_table(Mod(str(mod))) == []


def test_an_undefined_string_id_is_reported(world, tmp_path):
    _stock, mod = world
    stock = _stock_with_strings(tmp_path, [("ID_STOCK", "stock text")])
    _w(mod / "scripts" / "m.lua",
       b'NGUI.ShowInfoText("ID_TYPPO")\nNGUI.ShowInfoText("ID_STOCK")\n')
    found = validate.check_string_ids(Mod(str(mod)), stock)
    assert [d.code for d in found] == ["PRJ009"]
    assert "ID_TYPPO" in found[0].detail
    assert "ID_STOCK" not in found[0].detail


def test_an_id_the_mod_defines_itself_is_accepted(world, tmp_path):
    from dsotools.formats import res as resfmt

    _stock, mod = world
    stock = _stock_with_strings(tmp_path, [("ID_STOCK", "stock text")])
    _w(mod / "strings" / "user_strings.res",
       resfmt.build(resfmt.from_pairs([("ID_MINE", "my text")])))
    _w(mod / "scripts" / "m.lua", b'NGUI.ShowInfoText("ID_MINE")\n')
    assert validate.check_string_ids(Mod(str(mod)), stock) == []


def test_string_ids_are_not_checked_without_a_game_folder(world):
    _stock, mod = world
    _w(mod / "scripts" / "m.lua", b'NGUI.ShowInfoText("ID_TYPPO")\n')
    assert validate.check_string_ids(Mod(str(mod)), None) == []


def test_ordinary_text_is_not_mistaken_for_an_id(world, tmp_path):
    """The engine takes any string, so the pattern has to stay narrow."""
    _stock, mod = world
    stock = _stock_with_strings(tmp_path, [("ID_STOCK", "stock text")])
    _w(mod / "scripts" / "m.lua",
       b'local s = "Identity crisis"\nprint("id_lowercase", "IDX")\n')
    assert validate.check_string_ids(Mod(str(mod)), stock) == []

# --------------------------------------------------------------------------
# missions
# --------------------------------------------------------------------------


def test_a_mod_that_registers_nothing_says_nothing(world):
    _stock, mod = world
    assert validate.check_missions(Mod(str(mod))) == []


def test_the_same_mission_registered_twice_is_an_error(world):
    """Only one registration survives, and which one is not defined."""
    _stock, mod = world
    _w(mod / "scripts" / "a.lua", b'NScript.Register( { Name = "MINE" } )\n')
    _w(mod / "scripts" / "b.lua", b'NScript.Register( { Name = "MINE" } )\n')
    found = validate.check_missions(Mod(str(mod)))
    assert [d.severity for d in found] == [Severity.ERROR]
    assert "MINE" in found[0].detail


def test_replacing_a_stock_mission_is_reported_as_information(world):
    from dsotools import missions as missionsmod

    _stock, mod = world
    if not missionsmod.stock():
        pytest.skip("this build ships no stock_missions.json")
    _w(mod / "scripts" / "x.lua", b'NScript.Register( { Name = "BAR_006" } )\n')
    found = validate.check_missions(Mod(str(mod)))
    assert [d.code for d in found] == ["PRJ010"]
    assert found[0].severity == Severity.INFO
    assert "BAR_006" in found[0].detail


def test_a_mission_of_the_mods_own_is_not_reported(world):
    _stock, mod = world
    _w(mod / "scripts" / "x.lua", b'NScript.Register( { Name = "MY_OWN_THING" } )\n')
    assert validate.check_missions(Mod(str(mod))) == []


def _sound_mod(mod, declared):
    """A mod with one 22,050 Hz mono WAV of exactly 22,050 samples."""
    import struct

    data = b"\x00" * (22050 * 2)
    chunks = (struct.pack("<4sIHHIIHH", b"fmt ", 16, 1, 1, 22050, 44100, 2, 16)
              + struct.pack("<4sI", b"data", len(data)) + data)
    _w(mod / "sound" / "sfx(2d)" / "grp_USER" / "Beep.wav",
       b"RIFF" + struct.pack("<I", 4 + len(chunks)) + b"WAVE" + chunks)
    _w(mod / "user_sounds.xml",
       b'<?xml version="1.0" encoding="ISO-8859-1"?>\r\n<ASE_Database>\r\n'
       b'  <Group Name="USER">\r\n'
       b'    <Sound2D Name="Beep" Resrc="%MOD%sound\\sfx(2d)\\grp_USER\\Beep.wav" '
       + declared + b' />\r\n'
       b"  </Group>\r\n</ASE_Database>\r\n")
    return [d for d in validate.check_sounds(Mod(str(mod))) if d.code == "SND004"]


def test_a_sound_declared_differently_from_its_file_is_flagged(world):
    """The engine believes the database, so a stale Duration truncates it."""
    _stock, mod = world
    found = _sound_mod(mod, b'Channels="2" Duration=":999" Freq="44100"')
    assert len(found) == 1
    detail = found[0].detail
    assert "rate 44100 declared, 22050 in the file" in detail
    assert "2 channel(s) declared, 1 in the file" in detail
    assert "999 samples declared, 22050 in the file" in detail
    assert found[0].severity == Severity.WARNING


def test_a_sound_that_matches_its_file_is_quiet(world):
    _stock, mod = world
    assert _sound_mod(mod, b'Channels="1" Duration=":22050" Freq="22050"') == []


def test_a_small_length_difference_is_tolerated(world):
    """MP3 length is derived, so an exact match is not a fair demand."""
    _stock, mod = world
    assert _sound_mod(mod, b'Channels="1" Duration=":22300" Freq="22050"') == []


# --------------------------------------------------------------------------
# PRJ011: Init never says Ready
# --------------------------------------------------------------------------

_INIT = (
    'NScript.Register( {\n'
    '    Name = "MINE",\n'
    '    Transitions = {\n'
    '        { nil, nil, "Init",\n'
    '            function( V, Data )\n%s\n            end\n'
    '        },\n'
    '    },\n'
    '} )\n'
)


def test_a_mission_whose_init_never_returns_is_an_error(world):
    """The mission is never created, and nothing anywhere says so."""
    _stock, mod = world
    _w(mod / "scripts" / "m.lua", (_INIT % "                local x = 1").encode())
    found = validate.check_mission_init(Mod(str(mod)))
    assert [d.code for d in found] == ["PRJ011"]
    assert found[0].severity == Severity.ERROR
    assert "MINE in m.lua" in found[0].detail
    assert "Ready = true" in found[0].fix


def test_a_mission_that_returns_ready_is_quiet(world):
    _stock, mod = world
    _w(mod / "scripts" / "m.lua",
       (_INIT % "                return { Ready = true }").encode())
    assert validate.check_mission_init(Mod(str(mod))) == []


def test_an_unreadable_return_is_not_reported(world):
    """A helper call is legitimate; a validator that cries wolf gets muted."""
    _stock, mod = world
    _w(mod / "scripts" / "m.lua",
       (_INIT % "                return MissionLib.Decide( V )").encode())
    assert validate.check_mission_init(Mod(str(mod))) == []


def test_a_mod_with_no_scripts_says_nothing_about_init(world):
    _stock, mod = world
    assert validate.check_mission_init(Mod(str(mod))) == []


# --------------------------------------------------------------------------
# PRJ012: the script API, across the whole mod
# --------------------------------------------------------------------------


def test_the_script_api_is_not_judged_without_a_game_folder(world):
    """Every library call would read as unknown; better to run nothing."""
    _stock, mod = world
    _w(mod / "scripts" / "m.lua", b"NComm.Nonesuch( { } )\n")
    assert validate.check_script_api(Mod(str(mod)), None) == []


def test_a_typo_in_a_script_is_found_by_the_validator(world):
    _stock, mod = world
    if not _has_script_reference():
        pytest.skip("this build ships no lua_api.json")
    _w(mod / "scripts" / "m.lua", b"NComm.Nonesuch( { } )\n")
    found = validate.check_script_api(Mod(str(mod)), _stock)
    assert [d.code for d in found] == ["PRJ012"]
    assert "m.lua:1" in found[0].detail


def test_a_clean_script_is_quiet(world):
    _stock, mod = world
    if not _has_script_reference():
        pytest.skip("this build ships no lua_api.json")
    _w(mod / "scripts" / "m.lua", b"local x = 1\n")
    assert validate.check_script_api(Mod(str(mod)), _stock) == []


def test_prose_where_a_stringid_belongs_is_found_across_the_mod(world):
    """The failure that cost this project an experiment cycle."""
    from dsotools import scriptdoc

    _stock, mod = world
    if not scriptdoc.string_id_parameters(scriptdoc.bundled()):
        pytest.skip("this build ships no lua_api.json")
    _w(mod / "scripts" / "m.lua",
       b'NGUI.ShowInfoText( { Text = "hello there" } )\n')
    found = [d for d in validate.check_script_api(Mod(str(mod)), _stock)
             if "StringId" in d.message]
    assert len(found) == 1
    assert found[0].severity == Severity.WARNING


def _has_script_reference():
    from dsotools import scriptdoc

    return bool(scriptdoc.bundled())
