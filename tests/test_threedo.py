"""
The ``.3do`` structural scan, and the MDL001-MDL007 rules built on it.

Format tests and rule tests live together here because they share one fixture:
a ``.3do`` assembled **by hand** from the layout in ``threedo.py``'s docstring,
never through ``threedo.build``.  That is the same reasoning as
``test_scene._submesh_total``: a validator checked with files its own writer
produced can pass because the reader and the writer share a misunderstanding,
and every defect below is one a writer would never emit.

Every rule gets a positive *and* a negative case.  The negatives matter more
than usual here: all seven rules fire zero times across the 3,110 stock models
and the 150 modder-authored ones, and a rule that starts crying wolf on
Ascaron's own data is a rule that gets switched off -- taking the real findings
with it.
"""

from __future__ import annotations

import struct

import pytest

from dsotools import validate
from dsotools.errors import DsoError
from dsotools.formats import shd, threedo
from dsotools.validate import Severity

# D3DFVF_XYZ | D3DFVF_NORMAL | D3DFVF_TEX1 -- 12 + 12 + 8 = 32 bytes, and no
# declaration block, which keeps the fixtures readable.
FVF_XYZ_NORMAL_TEX1 = 0x112
FVF_STRIDE = 32

#: The one triangle every fixture is built from, and the bounding box it
#: implies.  Written out rather than computed, so that a fixture cannot inherit
#: a mistake from the code under test -- and so that MDL004 stays quiet in the
#: fixtures aimed at the other six rules.
TRIANGLE = [(-1.0, -1.0, 0.0), (1.0, -1.0, 0.0), (1.0, 1.0, 0.0)]
TRIANGLE_BBOX = ((0.0, 0.0, 0.0), (1.0, 1.0, 0.0))
EMPTY_BBOX = ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))


def lod_chunk(
    *,
    positions=(),
    indices=(),
    submeshes=(),
    vertex_count=None,
    stride=FVF_STRIDE,
    declared_stride=None,
    fvf=FVF_XYZ_NORMAL_TEX1,
    index_count=None,
    submesh_count=None,
    truncate=0,
):
    """One LOD chunk.

    ``vertex_count`` builds a zero-filled buffer of that many vertices when no
    positions are given -- 70,000 of them is 2 MB of nothing, which is what
    MDL001 needs and what a per-vertex loop would make slow.
    """
    n_vertices = len(positions) if positions else (vertex_count or 0)
    vbuf = bytearray(stride * n_vertices)
    for i, p in enumerate(positions):
        struct.pack_into("<3f", vbuf, i * stride, *p)

    ibuf = struct.pack(f"<{len(indices)}H", *indices)
    pad = (-len(ibuf)) % 4

    head = struct.pack(
        "<4sIIIIII",
        threedo.MAGIC_LOD,
        0,
        len(submeshes) if submesh_count is None else submesh_count,
        fvf,
        len(indices) if index_count is None else index_count,
        n_vertices if vertex_count is None else vertex_count,
        stride if declared_stride is None else declared_stride,
    )
    trailers = b"".join(
        struct.pack("<4sIIIII", threedo.MAGIC_ATTR, *sm) for sm in submeshes
    )
    out = head + ibuf + b"\x00" * pad + bytes(vbuf) + trailers
    return out[: len(out) - truncate] if truncate else out


def model_bytes(
    chunks,
    submesh_counts,
    *,
    name="fixture",
    bbox=TRIANGLE_BBOX,
    submesh_total=None,
    trailing=b"",
    lod_count=None,
):
    """Root header + MESH header + the given LOD chunks."""
    total = sum(submesh_counts) if submesh_total is None else submesh_total
    root = bytearray()
    root += threedo.MAGIC_3DO + b"00.2" + struct.pack("<II", 0, 1)
    root += struct.pack("<3f", *bbox[0])
    root += struct.pack("<f", 1.0)
    root += struct.pack("<3f", *bbox[1])
    root += struct.pack("<I", 0)
    root += struct.pack("<I", total)
    nb = name.encode("ascii")[: threedo.NAME_LEN - 1]
    root += nb + b"\x00" * (threedo.NAME_LEN - len(nb))
    root += b"".join(
        struct.pack("<HH", s, li)
        for li, n in enumerate(submesh_counts)
        for s in range(n)
    )
    root += b"\x00" * ((-len(root)) % 16)

    mesh = threedo.MAGIC_MESH + b"00.1" + struct.pack(
        "<II", 0, len(chunks) if lod_count is None else lod_count
    )
    return bytes(root) + mesh + b"".join(chunks) + trailing


#: One triangle, one submesh, one LOD: the smallest thing that is a model.
def one_triangle(**kw):
    kw.setdefault("positions", TRIANGLE)
    kw.setdefault("indices", [0, 1, 2])
    kw.setdefault("submeshes", [(0, 0, 1, 0, 3)])
    return kw


def clean_model(**kw):
    """A model with nothing wrong with it. Every rule's negative case."""
    return model_bytes([lod_chunk(**one_triangle(**kw))], [1])


def codes(diags):
    return sorted(d.code for d in diags)


def shd_bytes(n_lods: int) -> bytes:
    """A shadow volume with ``n_lods`` empty levels."""
    out = shd.MAGIC_HWSV + b"00.1" + struct.pack("<II", n_lods, 0)
    return out + n_lods * (shd.MAGIC_SLOD + struct.pack("<III", 0, 0, 0))


# --------------------------------------------------------------------------
# the scan itself
# --------------------------------------------------------------------------


def test_scan_reads_the_headers():
    sc = threedo.scan(clean_model())
    assert sc.name == "fixture"
    assert sc.lod_count == 1 and len(sc.lods) == 1
    assert sc.submesh_total == 1
    assert sc.stopped is None and sc.trailing == 0
    (lod,) = sc.lods
    assert (lod.vertex_count, lod.index_count, lod.submesh_count) == (3, 3, 1)
    assert lod.declared_stride == lod.computed_stride == FVF_STRIDE
    assert lod.max_index == 2
    assert lod.face_count == 1


def test_scan_agrees_with_parse_on_the_same_file():
    """Two readers of one file, and they must not drift apart."""
    raw = clean_model()
    sc = threedo.scan(raw)
    model = threedo.parse(raw)
    assert len(sc.lods) == len(model.lods)
    for a, b in zip(sc.lods, model.lods):
        assert a.vertex_count == len(b.vertices)
        assert a.index_count == len(b.indices)
        assert a.submesh_count == len(b.submeshes)
        assert a.computed_stride == b.stride


def test_scan_reports_a_short_walk_rather_than_raising():
    """A truncated second LOD must not throw away what the first one said.

    ``parse`` refuses the file outright, which is right for reading it and
    wrong for checking it: a check that stops early reports the first cause as
    if it were the only one.
    """
    good = lod_chunk(**one_triangle())
    bad = lod_chunk(**one_triangle(), truncate=8)
    raw = model_bytes([good, bad], [1, 1])

    sc = threedo.scan(raw)
    assert len(sc.lods) == 1
    assert sc.stopped and "LOD #1" in sc.stopped
    assert sc.lods[0].vertex_count == 3

    with pytest.raises(DsoError):
        threedo.parse(raw)


def test_scan_refuses_something_that_is_not_a_model():
    for raw in (b"", b"nonsense", threedo.MAGIC_3DO + b"00.2" + b"\x00" * 8):
        with pytest.raises(DsoError):
            threedo.scan(raw)


# D3DFVF_XYZ | NORMAL | DIFFUSE | TEX1: 12 + 12 + 4 + 8 = 36 bytes, and the
# D3DCOLOR in the middle is not a float -- so this is the layout that cannot
# take the bulk-unpack path.
FVF_WITH_COLOUR = 0x152
FVF_COLOUR_STRIDE = 36


def test_a_vertex_that_is_all_floats_takes_the_bulk_path():
    """3,106 of the 3,110 stock models look like this."""
    lod = threedo.parse(clean_model()).lods[0]
    assert threedo._float_layout(lod.elements, lod.stride) == [
        ((0, 0), 3), ((3, 0), 3), ((5, 0), 2),
    ]


def test_a_non_float_element_falls_back_and_still_round_trips():
    """The other four carry a D3DCOLOR, which is kept as raw bytes.

    The fast path exists because ten million per-element struct calls were half
    the cost of reading the corpus; this is the case it must refuse to take,
    and refusing it wrongly would corrupt a colour into a float.
    """
    raw = model_bytes(
        [lod_chunk(**one_triangle(fvf=FVF_WITH_COLOUR, stride=FVF_COLOUR_STRIDE))],
        [1],
    )
    model = threedo.parse(raw)
    (lod,) = model.lods

    assert threedo._float_layout(lod.elements, lod.stride) is None
    # The colour survives as the raw dword it is, not as a float.
    assert lod.vertices[0].attrs[(10, 0)] == (0,)
    assert threedo.build(model) == raw
    assert validate.check_model(raw, "x.3do") == []


def test_the_two_vertex_paths_agree_byte_for_byte():
    """Two implementations of one thing, compared on the same bytes.

    The fast one is taken by 3,106 of 3,110 stock models, so a disagreement
    between them would be a corpus-wide corruption that the four files on the
    other path would never reveal.
    """
    elements = threedo.elements_from_fvf(FVF_XYZ_NORMAL_TEX1)
    chunk = lod_chunk(**one_triangle())
    # Vertices start after the 28-byte header and the index buffer: three
    # uint16s (6 bytes) padded to 8.
    vtx_off = 28 + 8
    plan = threedo._float_layout(elements, FVF_STRIDE)
    assert plan is not None

    packed = threedo._read_vertices_packed(chunk, vtx_off, 3, FVF_STRIDE, plan)
    general = threedo._read_vertices_general(chunk, vtx_off, 3, FVF_STRIDE, elements)

    assert [v.attrs for v in packed] == [v.attrs for v in general]
    assert [v.position for v in packed] == TRIANGLE
    # And the writers agree too, which is what byte-exact round-trip rests on.
    assert (threedo._write_vertices(packed, FVF_STRIDE, elements)
            == threedo._write_vertices_general(general, FVF_STRIDE, elements))


def test_submesh_index_is_two_u16s():
    """Measured over all 3,110 stock models; see the Submesh docstring."""
    sm = threedo.Submesh(0x20000, 0, 0, 0, 0)
    assert (sm.index_in_lod, sm.lod_index) == (0, 2)
    assert threedo.Submesh(1, 0, 0, 0, 0).index_in_lod == 1


# --------------------------------------------------------------------------
# recompute_bbox -- MDL004's fix
# --------------------------------------------------------------------------


def test_recompute_bbox_rewrites_a_stale_box():
    raw = model_bytes(
        [lod_chunk(**one_triangle())], [1], bbox=((9.0, 9.0, 9.0), (9.0, 9.0, 9.0))
    )
    model = threedo.parse(raw)

    # Round-tripping alone must NOT quietly fix it: build() reuses the original
    # bytes when the geometry is unchanged, and rewriting a field nobody asked
    # it to touch is how byte-exactness dies.
    assert threedo.build(model) == raw

    assert threedo.recompute_bbox(model) is True
    fixed = threedo.build(model)
    assert fixed != raw
    assert threedo.scan(fixed).stored_bbox == threedo.scan(fixed).computed_bbox
    assert not validate.check_model(fixed, "m.3do")


def test_recompute_bbox_reports_when_nothing_moved():
    model = threedo.parse(clean_model())
    assert threedo.recompute_bbox(model) is False
    assert threedo.build(model) == clean_model()


# --------------------------------------------------------------------------
# the rules: negative case first
# --------------------------------------------------------------------------


def test_a_well_formed_model_produces_nothing():
    assert validate.check_model(clean_model(), "3DView/objects/x.3do") == []


def test_a_well_formed_pair_produces_nothing():
    assert validate.check_model(clean_model(), "x.3do", shd_bytes(1), "x.shd") == []


# --- MDL001 ----------------------------------------------------------------


def test_mdl001_vertex_count_above_the_uint16_ceiling():
    chunk = lod_chunk(
        vertex_count=70000, indices=[0, 1, 2], submeshes=[(0, 0, 1, 0, 70000)]
    )
    diags = validate.check_model(model_bytes([chunk], [1], bbox=EMPTY_BBOX), "x.3do")
    assert codes(diags) == ["MDL001"]
    assert diags[0].severity == Severity.ERROR
    assert "70000" in diags[0].message


def test_mdl001_does_not_fire_just_below_the_ceiling():
    chunk = lod_chunk(
        vertex_count=0xFFFF, indices=[0, 1, 2], submeshes=[(0, 0, 1, 0, 0xFFFF)]
    )
    assert validate.check_model(model_bytes([chunk], [1], bbox=EMPTY_BBOX), "x.3do") == []


# --- MDL002 ----------------------------------------------------------------


def test_mdl002_face_range_past_the_end_of_the_buffer():
    chunk = lod_chunk(**one_triangle(submeshes=[(0, 0, 4, 0, 3)]))
    (d,) = validate.check_model(model_bytes([chunk], [1]), "x.3do")
    assert d.code == "MDL002" and d.severity == Severity.ERROR


def test_mdl002_vertex_range_past_the_end_of_the_buffer():
    chunk = lod_chunk(**one_triangle(submeshes=[(0, 0, 1, 0, 9)]))
    diags = validate.check_model(model_bytes([chunk], [1]), "x.3do")
    assert [d.code for d in diags] == ["MDL002"]
    assert diags[0].severity == Severity.ERROR


def many_submeshes(n, *, faces_each=1, face_starts=None, total_faces=None):
    """``n`` submeshes, each owning its own three vertices and its own faces.

    Stock submeshes partition *both* buffers, so a fixture where two of them
    shared vertices would fire the vertex half of MDL002 as well and stop the
    test saying anything about the face half.  The index buffer is sized to the
    ranges by default, for the same reason: a short buffer is a different
    defect.
    """
    starts = face_starts if face_starts is not None else [k * faces_each for k in range(n)]
    faces = total_faces if total_faces is not None else max(s + faces_each for s in starts)
    indices = [i for t in range(faces) for i in (3 * (t % n), 3 * (t % n) + 1, 3 * (t % n) + 2)]
    submeshes = [(k, starts[k], faces_each, 3 * k, 3) for k in range(n)]
    return one_triangle(positions=TRIANGLE * n, indices=indices, submeshes=submeshes)


def test_mdl002_overlapping_submeshes_are_an_error():
    """Both submeshes draw triangle 1, each with the other's material."""
    kw = many_submeshes(2, faces_each=2, face_starts=[0, 1])
    diags = validate.check_model(model_bytes([lod_chunk(**kw)], [2]), "x.3do")
    assert [d.code for d in diags] == ["MDL002"]
    assert diags[0].severity == Severity.ERROR
    assert "overlap" in diags[0].message


def test_mdl002_a_gap_is_a_warning_not_an_error():
    """The triangles in the gap are never drawn; the file still loads."""
    kw = many_submeshes(3, face_starts=[0, 2, 3])
    diags = validate.check_model(model_bytes([lod_chunk(**kw)], [3]), "x.3do")
    assert [d.code for d in diags] == ["MDL002"]
    assert diags[0].severity == Severity.WARNING
    assert "gap" in diags[0].message


def test_mdl002_short_coverage_is_reported_once():
    kw = one_triangle(
        positions=TRIANGLE * 2, indices=[0, 1, 2, 3, 4, 5], submeshes=[(0, 0, 1, 0, 6)]
    )
    diags = validate.check_model(model_bytes([lod_chunk(**kw)], [1]), "x.3do")
    assert [d.code for d in diags] == ["MDL002"]
    assert diags[0].severity == Severity.WARNING
    assert "1 of 2" in diags[0].message


def test_mdl002_reports_one_row_per_buffer_not_one_per_submesh():
    """Once the ranges slip, every later submesh disagrees. Say it once."""
    kw = many_submeshes(5, face_starts=[1, 2, 3, 4, 5])
    diags = validate.check_model(model_bytes([lod_chunk(**kw)], [5]), "x.3do")
    assert [d.code for d in diags] == ["MDL002"]


# --- MDL003 ----------------------------------------------------------------


def test_mdl003_index_out_of_range():
    kw = one_triangle(indices=[0, 1, 7])
    (d,) = validate.check_model(model_bytes([lod_chunk(**kw)], [1]), "x.3do")
    assert d.code == "MDL003" and d.severity == Severity.ERROR
    assert "vertex 7 of 3" in d.message


def test_mdl003_declared_stride_disagrees_with_the_declaration():
    """The buffer really is 48-byte-spaced; the FVF says 32.

    Built that way on purpose: a header that lies about the stride *and*
    lays the file out to match its lie is the only version of this defect
    where the rest of the file is still walkable, which is what isolates
    MDL003 from MDL006.
    """
    kw = one_triangle()
    kw["stride"] = 48
    (d,) = validate.check_model(model_bytes([lod_chunk(**kw)], [1]), "x.3do")
    assert d.code == "MDL003" and d.severity == Severity.ERROR
    assert "48" in d.message and "32" in d.message


def test_mdl003_index_count_not_a_whole_number_of_triangles():
    kw = one_triangle(indices=[0, 1, 2, 0], submeshes=[(0, 0, 1, 0, 3)])
    diags = validate.check_model(model_bytes([lod_chunk(**kw)], [1]), "x.3do")
    assert [d.code for d in diags] == ["MDL003"]
    assert diags[0].severity == Severity.WARNING


# --- MDL004 ----------------------------------------------------------------


def test_mdl004_stale_bounding_box():
    raw = model_bytes(
        [lod_chunk(**one_triangle())], [1], bbox=((0.0, 0.0, 0.0), (9.0, 9.0, 9.0))
    )
    (d,) = validate.check_model(raw, "x.3do")
    assert d.code == "MDL004" and d.severity == Severity.WARNING
    assert d.fix


def test_mdl004_tolerates_the_float32_noise_stock_files_carry():
    """1,074 of 3,110 stock models differ from a recompute; the worst by
    1.19e-07, one float32 ULP. A rule that fired on that would flag a third of
    the game."""
    ulp = struct.unpack("<f", struct.pack("<f", 1.0000001))[0]
    raw = model_bytes(
        [lod_chunk(**one_triangle())], [1], bbox=((0.0, 0.0, 0.0), (ulp, ulp, 0.0))
    )
    assert validate.check_model(raw, "x.3do") == []


# --- MDL005 ----------------------------------------------------------------


def test_mdl005_shadow_lod_count_differs():
    (d,) = validate.check_model(clean_model(), "x.3do", shd_bytes(3), "x.shd")
    assert d.code == "MDL005" and d.severity == Severity.WARNING
    assert d.path == "x.shd"
    assert "3" in d.message and "1" in d.message


def test_mdl005_does_not_run_without_a_shadow_volume():
    """1,372 stock models have no .shd at all. That is not a finding."""
    assert validate.check_model(clean_model(), "x.3do", None) == []


def test_an_unreadable_shadow_volume_is_reported_against_the_shd():
    (d,) = validate.check_model(clean_model(), "x.3do", b"junkjunk", "x.shd")
    assert d.code == "MDL006" and d.path == "x.shd"


# --- MDL006 ----------------------------------------------------------------


def test_mdl006_a_file_that_will_not_load_at_all():
    (d,) = validate.check_model(b"not a model", "x.3do")
    assert d.code == "MDL006" and d.severity == Severity.ERROR


def test_mdl006_trailing_bytes_after_the_last_lod():
    raw = model_bytes([lod_chunk(**one_triangle())], [1], trailing=b"\x00" * 12)
    (d,) = validate.check_model(raw, "x.3do")
    assert d.code == "MDL006" and "12" in d.message


def test_mdl006_keeps_checking_the_lods_it_did_walk():
    """The point of not aborting: the good LOD's defect is still reported."""
    broken = lod_chunk(**one_triangle(indices=[0, 1, 9]))
    truncated = lod_chunk(**one_triangle(), truncate=8)
    raw = model_bytes([broken, truncated], [1, 1])
    assert codes(validate.check_model(raw, "x.3do")) == ["MDL003", "MDL006"]


# --- MDL007 ----------------------------------------------------------------


def test_mdl007_root_header_disagrees_with_the_lods():
    raw = model_bytes([lod_chunk(**one_triangle())], [1], submesh_total=4)
    (d,) = validate.check_model(raw, "x.3do")
    assert d.code == "MDL007" and d.severity == Severity.ERROR
    assert "SCN001" in d.detail


def test_mdl007_is_not_evaluated_when_the_walk_stopped_short():
    """A partial walk cannot say the total is wrong -- it did not see them all."""
    raw = model_bytes([lod_chunk(**one_triangle(), truncate=8)], [1], submesh_total=1)
    assert codes(validate.check_model(raw, "x.3do")) == ["MDL006"]


# --------------------------------------------------------------------------
# corpus
# --------------------------------------------------------------------------


@pytest.mark.corpus
def test_no_stock_model_fires_a_model_rule(corpus):
    """The measurement these severities were chosen from, as a test.

    3,110 stock models, zero findings. If this ever goes red, either the game
    data changed or a rule started crying wolf -- and a rule that cries wolf on
    Ascaron's own files is worse than no rule.
    """
    from conftest import collect, require_full_corpus

    require_full_corpus(corpus)
    found = []
    for p in collect(corpus, "*.3do"):
        sp = p.with_suffix(".shd")
        shadow = sp.read_bytes() if sp.exists() else None
        found += validate.check_model(p.read_bytes(), str(p), shadow, str(sp))
    assert not found, f"{len(found)} finding(s): {[repr(d) for d in found[:5]]}"
