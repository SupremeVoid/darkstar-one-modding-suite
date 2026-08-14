"""
Virtual file system: precedence, case folding, and reference resolution.

The corpus tests here double as regression guards on the measurements recorded
in ``specs/scene.md``.  If a change to path handling drops model resolution from
97.2% back to 90.6%, that shows up as a number moving rather than as a vague
"some textures went missing" bug report weeks later.
"""

from __future__ import annotations

import os
import zipfile

import pytest

from conftest import collect, require_full_corpus
from dsotools import vfs
from dsotools.errors import ResolutionError
from dsotools.formats import scene


@pytest.fixture()
def layered(tmp_path):
    """Two directory layers with one shared path, plus a zip on top."""
    low = tmp_path / "low"
    high = tmp_path / "high"
    (low / "3DView" / "objects").mkdir(parents=True)
    (high / "3DView" / "objects").mkdir(parents=True)
    (low / "3DView" / "objects" / "shared.3do").write_bytes(b"LOW")
    (low / "3DView" / "objects" / "only_low.3do").write_bytes(b"LOWONLY")
    (high / "3DView" / "objects" / "shared.3do").write_bytes(b"HIGH")

    zpath = tmp_path / "user_data.zip"
    with zipfile.ZipFile(zpath, "w") as zf:
        zf.writestr("3DView/objects/shared.3do", b"ZIP")

    v = vfs.Vfs()
    v.add(vfs.DirectoryLayer(str(low), name="cpr:low", priority=10))
    v.add(vfs.DirectoryLayer(str(high), name="cpr:high", priority=20))
    v.add(vfs.ZipLayer(str(zpath), name="mod:zip", priority=100))
    return v


def test_highest_priority_layer_wins(layered):
    assert layered.read("3DView/objects/shared.3do") == b"ZIP"


def test_candidates_lists_every_shadowed_copy(layered):
    got = [e.origin for e in layered.candidates("3DView/objects/shared.3do")]
    assert got == ["mod:zip", "cpr:high", "cpr:low"]


def test_lower_layer_still_reachable_when_unique(layered):
    assert layered.read("3DView/objects/only_low.3do") == b"LOWONLY"


def test_lookup_is_case_insensitive(layered):
    assert layered.read("3dview/OBJECTS/Shared.3DO") == b"ZIP"


def test_backslash_references_normalise(layered):
    """One reference in a real mod uses ``\\``; portable code must cope."""
    assert layered.read(r"3DView\objects\shared.3do") == b"ZIP"


def test_missing_path_reports_where_it_looked(layered):
    with pytest.raises(ResolutionError) as exc:
        layered.read("3DView/objects/nope.3do")
    assert exc.value.tried
    assert exc.value.code == "VFS001"


def test_ambiguous_reports_multi_layer_paths(layered):
    amb = layered.ambiguous()
    assert "3dview/objects/shared.3do" in amb
    assert "3dview/objects/only_low.3do" not in amb


def test_contested_reports_only_real_content_differences(tmp_path):
    """Shadowing is common; disagreement is rare.

    On stock data 1,368 paths are shadowed but only 16 differ.  Warning about
    all 1,368 would train users to ignore the warning, so ``contested`` must
    filter to the paths where precedence actually changes what is loaded.
    """
    a = tmp_path / "a"
    b = tmp_path / "b"
    for root in (a, b):
        (root / "d").mkdir(parents=True)
    (a / "d" / "same.txt").write_bytes(b"IDENTICAL")
    (b / "d" / "same.txt").write_bytes(b"IDENTICAL")
    (a / "d" / "differs.txt").write_bytes(b"AAAA")
    (b / "d" / "differs.txt").write_bytes(b"BBBB")          # same size, differs
    (a / "d" / "sized.txt").write_bytes(b"SHORT")
    (b / "d" / "sized.txt").write_bytes(b"MUCH LONGER TEXT")  # size shortcut
    (a / "d" / "only_a.txt").write_bytes(b"X")

    v = vfs.Vfs()
    v.add(vfs.DirectoryLayer(str(a), name="cpr:a", priority=20))
    v.add(vfs.DirectoryLayer(str(b), name="cpr:b", priority=10))

    assert set(v.ambiguous()) == {"d/same.txt", "d/differs.txt", "d/sized.txt"}
    assert set(v.contested()) == {"d/differs.txt", "d/sized.txt"}
    assert [e.origin for e in v.contested()["d/differs.txt"]] == ["cpr:a", "cpr:b"]


def test_unloaded_layer_is_hidden_by_default(tmp_path):
    """A mod's loose tree is registered but not served.

    The game does not read it (per SPEC), so showing it would tell the user
    their edit is live when it is not.  It stays reachable explicitly, because
    the app has to warn about the files it contains.
    """
    root = tmp_path / "mod"
    (root / "3DView").mkdir(parents=True)
    (root / "3DView" / "x.xml").write_bytes(b"DEAD")
    v = vfs.Vfs([vfs.DirectoryLayer(str(root), name="mod:loose", priority=5, loaded=False)])
    assert not v.exists("3DView/x.xml")
    assert v.exists("3DView/x.xml", include_unloaded=True)
    assert v.read("3DView/x.xml", include_unloaded=True) == b"DEAD"


def test_precedence_is_reverse_of_observed_mount_order():
    """Guard the measured rule against a well-meaning "tidy up" of the list.

    ProcMon showed the engine mounting archives in one order and searching them
    in exactly the reverse -- a LIFO registry.  If someone re-sorts
    DEFAULT_ARCHIVE_ORDER alphabetically or "by size", this fails.
    """
    observed = [a for a in vfs.DEFAULT_ARCHIVE_ORDER if a in vfs.OBSERVED_MOUNT_ORDER]
    assert observed == list(reversed(vfs.OBSERVED_MOUNT_ORDER))


def test_addon_archives_outrank_base_content():
    """The 16 contested stock files all pit ds_add/ds_3dadd against ds_3dgen."""
    order = list(vfs.DEFAULT_ARCHIVE_ORDER)
    assert order.index("ds_add") < order.index("ds_3dgen")
    assert order.index("ds_3dadd") < order.index("ds_3dgen")


def test_from_extracted_applies_measured_precedence(tmp_path):
    root = tmp_path / "extracted"
    for arch in ("ds_3dgen", "ds_3dadd", "ds_add", "ds_3dobj"):
        d = root / arch / "3DView"
        d.mkdir(parents=True)
        (d / "BlackHole.xml").write_bytes(arch.encode())
    v = vfs.from_extracted(str(root))
    # ds_add is searched first, so it wins -- this is the real BlackHole.xml case
    assert v.read("3DView/BlackHole.xml") == b"ds_add"
    assert [e.origin for e in v.candidates("3DView/BlackHole.xml")] == [
        "cpr:ds_add",
        "cpr:ds_3dadd",
        "cpr:ds_3dobj",
        "cpr:ds_3dgen",
    ]


def test_normalise():
    assert vfs.normalise(r"objects\main_.3do") == "objects/main_.3do"
    assert vfs.normalise("./textures/a.dds") == "textures/a.dds"
    assert vfs.normalise("/3DView//objects/x.3do") == "3DView/objects/x.3do"


def test_reference_candidates_prefer_scene_relative():
    v = vfs.Vfs()
    got = v.reference_candidates("objects/x.3do", scene_path="3DView/Generator/Gen.xml")
    assert got[0] == "3DView/Generator/objects/x.3do"
    assert "3DView/objects/x.3do" in got


def test_scene_relative_resolution_beats_base(tmp_path):
    root = tmp_path / "d"
    (root / "3DView" / "Generator" / "objects").mkdir(parents=True)
    (root / "3DView" / "objects").mkdir(parents=True)
    (root / "3DView" / "Generator" / "objects" / "x.3do").write_bytes(b"PRIVATE")
    (root / "3DView" / "objects" / "x.3do").write_bytes(b"SHARED")
    v = vfs.Vfs([vfs.DirectoryLayer(str(root), name="d", priority=1)])
    e = v.resolve_reference("objects/x.3do", scene_path="3DView/Generator/Gen.xml")
    assert e is not None and e.read() == b"PRIVATE"
    e2 = v.resolve_reference("objects/x.3do", scene_path="3DView/Other.xml")
    assert e2 is not None and e2.read() == b"SHARED"


# --------------------------------------------------------------------------
# corpus
# --------------------------------------------------------------------------


@pytest.mark.corpus
def test_corpus_reference_resolution_rate(corpus):
    """Regression guard on the rates recorded in specs/scene.md §4.2.

    Thresholds sit a little under the measured 97.2% / 98.1% so ordinary
    variation in which archives are present does not fail the build, while a
    real regression in path handling does.
    """
    root = corpus
    require_full_corpus(root)
    v = vfs.Vfs()
    subdirs = [d for d in sorted(os.listdir(root)) if os.path.isdir(os.path.join(root, d))]
    if not subdirs:
        pytest.skip("corpus has no archive subdirectories")
    for i, name in enumerate(subdirs):
        v.add(vfs.DirectoryLayer(os.path.join(root, name), name=f"cpr:{name}", priority=100 - i))

    scenes = [p for p in collect(root, "*.xml") if scene.is_scene(p.read_bytes()[:512])]
    if not scenes:
        pytest.skip("no scenes in corpus")

    models = models_ok = tex = tex_ok = 0
    for p in scenes:
        rel = str(p.relative_to(root)).replace(os.sep, "/")
        # strip the archive directory: inside the game these overlay one tree
        vpath = rel.split("/", 1)[1] if "/" in rel else rel
        s = scene.parse(p.read_bytes(), path=vpath)
        for _obj, ref in s.model_references():
            models += 1
            models_ok += v.resolve_reference(ref, scene_path=vpath) is not None
        for _obj, _eff, _slot, t in s.texture_references():
            tex += 1
            tex_ok += v.resolve_reference(t, scene_path=vpath) is not None

    assert models_ok / models > 0.93, f"model resolution fell to {models_ok}/{models}"
    if tex:
        assert tex_ok / tex > 0.95, f"texture resolution fell to {tex_ok}/{tex}"


# --------------------------------------------------------------------------
# reading an installation directly
# --------------------------------------------------------------------------


def test_from_install_rejects_a_folder_with_no_archives(tmp_path):
    (tmp_path / "nope").mkdir()
    with pytest.raises(ResolutionError) as exc:
        vfs.from_install(str(tmp_path / "nope"))
    assert exc.value.code == "VFS003"


def test_from_install_can_omit_the_loose_layer(tmp_path):
    game = tmp_path / "g"
    (game / "3DView").mkdir(parents=True)
    with zipfile.ZipFile(game / "ds_3dgen.cpr", "w") as zf:
        zf.writestr("3DView/A.xml", b"ARCHIVE")
    (game / "3DView" / "A.xml").write_bytes(b"LOOSE")

    assert vfs.from_install(str(game)).read("3DView/A.xml") == b"LOOSE"
    assert vfs.from_install(str(game), include_loose=False).read("3DView/A.xml") == b"ARCHIVE"


def test_from_install_names_layers_after_their_archive(tmp_path):
    game = tmp_path / "g"
    game.mkdir()
    for name in ("ds_add.cpr", "ds_3dgen.cpr"):
        with zipfile.ZipFile(game / name, "w") as zf:
            zf.writestr("x.txt", name.encode())
    v = vfs.from_install(str(game), include_loose=False)
    assert [ly.name for ly in v.layers] == ["cpr:ds_add", "cpr:ds_3dgen"]
    assert v.read("x.txt") == b"ds_add.cpr"


# --------------------------------------------------------------------------
# naming a vpath the way a scene must
# --------------------------------------------------------------------------
#
# The inverse of reference resolution, and the reason it has to exist: a scene
# does not name its assets by virtual path.  Writing one straight in produces a
# reference that resolves to nothing, or to a different file of that name.


@pytest.fixture()
def refs(tmp_path):
    """A 3DView tree with a name that exists at two depths."""
    root = tmp_path / "extracted" / "ds_3dgen" / "3DView"
    (root / "textures").mkdir(parents=True)
    (root / "Generator" / "textures").mkdir(parents=True)
    (root / "textures" / "hull.dds").write_bytes(b"DDS top")
    (root / "textures" / "only_top.dds").write_bytes(b"DDS only")
    (root / "Generator" / "textures" / "hull.dds").write_bytes(b"DDS near")
    (root / "A.xml").write_bytes(b"<x/>")
    (root / "Generator" / "G.xml").write_bytes(b"<x/>")
    (tmp_path / "extracted" / "ds_add" / "inifiles").mkdir(parents=True)
    (tmp_path / "extracted" / "ds_add" / "inifiles" / "items.ini").write_bytes(b"[i]\r\n")
    return vfs.from_extracted(str(tmp_path / "extracted"))


def test_a_texture_is_named_relative_to_3dview(refs):
    assert refs.reference_for("3DView/textures/hull.dds",
                              scene_path="3DView/A.xml") == "textures/hull.dds"


def test_a_scene_in_its_own_folder_names_its_own_copy(refs):
    """Resolution tries the scene's folder first, so the short form is local."""
    assert refs.reference_for("3DView/Generator/textures/hull.dds",
                              scene_path="3DView/Generator/G.xml") == "textures/hull.dds"


def test_a_shadowed_name_falls_back_to_the_longer_spelling(refs):
    """The short form would mean the *other* file, so it must not be used.

    This is the case that makes checking mandatory rather than tidy: from
    ``Generator/G.xml`` the reference ``textures/hull.dds`` resolves to
    Generator's own copy, so naming the top-level one that way would silently
    bind a different texture.
    """
    got = refs.reference_for("3DView/textures/hull.dds",
                             scene_path="3DView/Generator/G.xml")
    assert got == "textures/hull.dds" or got is not None
    back = refs.resolve_reference(got, scene_path="3DView/Generator/G.xml")
    assert back.vpath.lower() == "3dview/textures/hull.dds"


def test_every_answer_resolves_back_to_what_was_asked_for(refs):
    for vpath in ("3DView/textures/hull.dds", "3DView/textures/only_top.dds",
                  "3DView/Generator/textures/hull.dds"):
        for scene_path in ("3DView/A.xml", "3DView/Generator/G.xml"):
            got = refs.reference_for(vpath, scene_path=scene_path)
            assert got is not None, (vpath, scene_path)
            back = refs.resolve_reference(got, scene_path=scene_path)
            assert back is not None and back.vpath.lower() == vpath.lower()


def test_a_file_that_is_not_there_cannot_be_named(refs):
    assert refs.reference_for("3DView/textures/nope.dds",
                              scene_path="3DView/A.xml") is None


def test_strict_refuses_what_only_the_bare_path_reaches(refs):
    """The bare-path candidate is this reader's convenience, not an engine rule.

    Across the stock corpus no resolving reference needs it, so anything that
    *writes* a reference must not lean on it -- it would work in the app and
    resolve to nothing in game.
    """
    assert refs.reference_for("inifiles/items.ini",
                              scene_path="3DView/A.xml") == "inifiles/items.ini"
    assert refs.reference_for("inifiles/items.ini", scene_path="3DView/A.xml",
                              strict=True) is None
