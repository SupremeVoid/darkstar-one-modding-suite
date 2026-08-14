"""
The asset index and its binding graph.

The reverse lookup is the reason this module exists, so it gets the most
attention. "What uses this texture?" cannot be answered by opening one file, and
it is the question that decides whether editing an asset is safe.
"""

from __future__ import annotations

import struct

import pytest

from conftest import require_full_corpus
from dsotools import index as idxmod
from dsotools import vfs as vfsmod


SCENE = (
    b'<?xml version="1.0"?>\r\n'
    b'<WalhallaScene Version="2.00">\r\n'
    b'\t<Object Type=".?AVCWorldRoot@@">\r\n\t\t<AttachedObjects>\r\n'
    b'\t\t\t<Object Type=".?AVCMesh@@" Name="hull" Resrc3DO="objects/hull.3do">\r\n'
    b'\t\t\t\t<EffectContainer Path="blender/mat_main.bsd9">\r\n'
    b'\t\t\t\t\t<Textures Number="2">\r\n'
    b"\t\t\t\t\t\ttextures/shared_col.dds\r\n\t\t\t\t\t\ttextures/gone.dds\r\n"
    b"\t\t\t\t\t</Textures>\r\n"
    b"\t\t\t\t</EffectContainer>\r\n"
    b"\t\t\t</Object>\r\n\t\t</AttachedObjects>\r\n\t</Object>\r\n</WalhallaScene>\r\n"
)

SCENE_B = SCENE.replace(b'Name="hull"', b'Name="wing"').replace(
    b"objects/hull.3do", b"objects/wing.3do"
)


def _model(submeshes=1):
    b = bytearray(0x60)
    b[0:4] = b"OD3 "
    struct.pack_into("<I", b, 0x30, submeshes)
    return bytes(b)


def _dds(w=4, h=4):
    head = bytearray(4 + 124)
    head[0:4] = b"DDS "
    struct.pack_into("<7I", head, 4, 124, 0x20000, h, w, 0, 0, 1)
    pf = 4 + 72
    struct.pack_into("<2I", head, pf, 32, 0x4)
    head[pf + 8 : pf + 12] = b"DXT1"
    # A DXT1 level is ceil(w/4) * ceil(h/4) * 8 bytes.  Supplying less makes
    # parse() reject the file as truncated -- correctly, which is how the first
    # version of this fixture was caught.
    blocks = max(1, (w + 3) // 4) * max(1, (h + 3) // 4)
    return bytes(head) + b"\0" * (blocks * 8)


@pytest.fixture()
def game(tmp_path):
    root = tmp_path / "extracted" / "ds_3dgen" / "3DView"
    (root / "objects").mkdir(parents=True)
    (root / "textures").mkdir(parents=True)
    (root / "blender").mkdir(parents=True)
    (root / "A.xml").write_bytes(SCENE)
    (root / "B.xml").write_bytes(SCENE_B)
    (root / "objects" / "hull.3do").write_bytes(_model(3))
    (root / "objects" / "wing.3do").write_bytes(_model(1))
    (root / "objects" / "unused.3do").write_bytes(_model(1))
    (root / "textures" / "shared_col.dds").write_bytes(_dds(8, 8))
    (root / "blender" / "mat_main.bsd9").write_bytes(b"XF  90.1")
    return vfsmod.from_extracted(str(tmp_path / "extracted"))


@pytest.fixture()
def idx(game):
    return idxmod.build_index(game)


def test_indexes_every_asset(idx):
    fmts = idx.by_format()
    assert fmts["xml"] == 2
    assert fmts["3do"] == 3
    assert fmts["dds"] == 1


def test_extracts_format_metadata(idx):
    import json

    row = idx.asset("3DView/objects/hull.3do")
    assert json.loads(row["meta"])["submesh_total"] == 3
    dds_meta = json.loads(idx.asset("3DView/textures/shared_col.dds")["meta"])
    assert (dds_meta["width"], dds_meta["height"], dds_meta["format"]) == (8, 8, "DXT1")
    scene_meta = json.loads(idx.asset("3DView/A.xml")["meta"])
    assert scene_meta["meshes"] == 1 and scene_meta["effects"] == 1


def test_reverse_lookup_finds_every_user(idx):
    """The query the app exists to make instant."""
    users = idx.used_by("3DView/textures/shared_col.dds")
    assert {r["src"] for r in users} == {"3dview/a.xml", "3dview/b.xml"}
    assert all(r["kind"] == "texture" and r["slot"] == 0 for r in users)


def test_reverse_lookup_distinguishes_kinds(idx):
    model_users = idx.used_by("3DView/objects/hull.3do")
    assert [r["kind"] for r in model_users] == ["model"]
    assert model_users[0]["node"] == "hull"

    shader_users = idx.used_by("3DView/blender/mat_main.bsd9")
    assert {r["src"] for r in shader_users} == {"3dview/a.xml", "3dview/b.xml"}
    assert all(r["kind"] == "shader" for r in shader_users)


def test_forward_references_keep_the_raw_string(idx):
    refs = idx.references_from("3DView/A.xml")
    raws = {r["raw"] for r in refs}
    assert "objects/hull.3do" in raws
    assert "textures/gone.dds" in raws          # verbatim, even though it is broken


def test_unresolved_references_are_recorded_not_dropped(idx):
    """A broken binding must survive indexing; that is what makes it findable."""
    bad = idx.unresolved()
    assert {r["raw"] for r in bad} == {"textures/gone.dds"}
    assert {r["src"] for r in bad} == {"3dview/a.xml", "3dview/b.xml"}


def test_orphans_finds_unreferenced_assets(idx):
    assert idx.orphans("3do") == ["3dview/objects/unused.3do"]


def test_search_filters_by_name_and_format(idx):
    assert [r["display"] for r in idx.search("hull")] == ["3DView/objects/hull.3do"]
    assert idx.search("hull", fmt="dds") == []


def test_stats_reports_resolution_rate(idx):
    s = idx.stats()
    assert s["assets"] == 7
    assert s["unresolved"] == 2
    assert 0.0 < s["resolution"] < 1.0


def test_shallow_build_skips_the_graph(game):
    shallow = idxmod.build_index(game, deep=False)
    assert shallow.stats()["assets"] == 7
    assert shallow.stats()["references"] == 0


def test_progress_callback_is_called(game):
    seen = []
    idxmod.build_index(game, progress=lambda d, t, p: seen.append((d, t)))
    assert seen
    assert seen[-1][0] == seen[-1][1]           # finishes at 100%


def test_fingerprint_detects_a_changed_layer_set(game, tmp_path):
    i = idxmod.build_index(game)
    assert not i.is_stale(game)
    extra = tmp_path / "extracted" / "ds_add" / "inifiles"
    extra.mkdir(parents=True)
    (extra / "items.ini").write_bytes(b"[i]\r\n")
    assert i.is_stale(vfsmod.from_extracted(str(tmp_path / "extracted")))


def test_index_survives_a_corrupt_file(tmp_path):
    """One unreadable asset must not abort a whole scan."""
    root = tmp_path / "extracted" / "ds_3dgen" / "3DView"
    root.mkdir(parents=True)
    (root / "good.xml").write_bytes(SCENE)
    (root / "bad.xml").write_bytes(b'<?xml version="1.0"?><WalhallaScene><oops')
    (root / "bad.dds").write_bytes(b"DDS " + b"\0" * 200)
    i = idxmod.build_index(vfsmod.from_extracted(str(tmp_path / "extracted")))
    assert i.stats()["assets"] == 3
    import json

    assert json.loads(i.asset("3DView/bad.xml")["meta"]).get("unreadable")


def test_roundtrip_through_a_file(game, tmp_path):
    path = str(tmp_path / "index.db")
    idxmod.build_index(game, path).close()
    reopened = idxmod.AssetIndex.open(path)
    assert reopened.used_by("3DView/textures/shared_col.dds")


def test_open_rejects_a_foreign_database(tmp_path):
    import sqlite3

    from dsotools.errors import DsoError

    p = str(tmp_path / "not-an-index.db")
    sqlite3.connect(p).execute("CREATE TABLE meta (key TEXT, value TEXT)")
    with pytest.raises(DsoError):
        idxmod.AssetIndex.open(p)


@pytest.mark.corpus
def test_corpus_index_matches_measured_resolution(corpus):
    """Guards the specs/scene.md figures through the index rather than by hand."""
    require_full_corpus(corpus)
    game = vfsmod.from_extracted(str(corpus))
    i = idxmod.build_index(game)
    s = i.stats()
    assert s["assets"] > 5000
    assert s["resolution"] > 0.95, s


# --------------------------------------------------------------------------
# threads
# --------------------------------------------------------------------------


def test_index_survives_being_used_from_another_thread(tmp_path):
    """The exact shape of the crash the GUI hit on "Rebuild index".

    The app builds the index on a worker thread and then queries it from the UI
    thread when the result comes back.  Python's sqlite3 vetoes that by default:

        sqlite3.ProgrammingError: SQLite objects created in a thread can only
        be used in that same thread.

    It surfaced at ``idx.stats()`` in the completion callback -- i.e. only ever
    after a *successful* build, which is the worst possible time to fail.
    """
    import threading

    root = tmp_path / "extracted" / "ds_add" / "inifiles"
    root.mkdir(parents=True)
    (root / "items.ini").write_bytes(b"[items]\r\n")
    game = vfsmod.from_extracted(str(tmp_path / "extracted"))

    built = {}

    def build():
        built["idx"] = idxmod.build_index(game)

    t = threading.Thread(target=build)
    t.start()
    t.join()

    # Now query from the main thread -- a different one from the builder.
    idx = built["idx"]
    stats = idx.stats()
    assert stats["assets"] >= 1
    assert idx.search("items")


def test_index_queries_are_serialised(tmp_path):
    """Many threads querying at once must not corrupt or raise.

    check_same_thread=False on its own would permit this *and* permit racy
    access; the lock is what makes it correct.  This is the test that would
    catch someone deleting the lock and keeping the flag.
    """
    import threading

    d = tmp_path / "extracted" / "ds_add" / "inifiles"
    d.mkdir(parents=True)
    for i in range(40):
        (d / f"f{i}.ini").write_bytes(b"[s]\r\n")
    game = vfsmod.from_extracted(str(tmp_path / "extracted"))
    idx = idxmod.build_index(game)

    errors = []
    counts = []

    def hammer():
        try:
            for _ in range(25):
                counts.append(idx.stats()["assets"])
                idx.search("f")
                idx.unresolved()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=hammer) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, errors
    assert set(counts) == {40}, "a query saw a partial view of the index"


def test_a_created_but_unbuilt_index_can_be_reopened(tmp_path):
    """A build that fails part-way must leave an empty index, not a broken file.

    create() wrote the schema row inside the transaction that only build()
    committed, so an interrupted build left a file with tables but no schema
    row -- which open() then rejected as "incompatible schema; rebuild it", on
    a file rebuilding could not fix.
    """
    p = str(tmp_path / "half.db")
    idx = idxmod.AssetIndex.create(p)
    idx.close()
    reopened = idxmod.AssetIndex.open(p)        # must not raise
    assert reopened.stats()["assets"] == 0
