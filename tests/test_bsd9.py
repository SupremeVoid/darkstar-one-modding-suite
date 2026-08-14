"""
The ``.bsd9`` shader container, and the slot semantics it settles.

The corpus tests are the ones that matter here -- the whole point of decoding
this format was to replace an inference with a measurement, so the evidence has
to be checked against real shipped files rather than against fixtures this
module wrote itself.  The unit tests below cover the layout rules and the
refusals; ``test_corpus_*`` covers the claims.
"""

from __future__ import annotations

import struct

import pytest

from conftest import collect
from dsotools.errors import ParseError, UnsupportedFormat
from dsotools.formats import bsd9


def _string(text: str) -> bytes:
    """A header string: length without the NUL, then the bytes, NUL, pad to 4."""
    raw = text.encode("latin-1")
    field = raw + b"\0"
    field += b"\0" * (-len(field) % 4)
    return struct.pack("<I", len(raw)) + field


def build(slots=("t_Color", "t_Normal"), extra=("DoIt",), blob=b"\x01\x09\xff\xfe",
          chunks=(("VARL", b"\xff\xff\xff\x7f"),), version=1000):
    """A minimal but structurally exact .bsd9."""
    names = list(slots) + list(extra)
    out = bytearray()
    out += bsd9.MAGIC
    out += struct.pack("<2I", version, len(slots))
    out += struct.pack(f"<{len(slots)}I", *range(len(slots))) if slots else b""
    out += b"\0" * (0x30 - len(out))
    out += struct.pack("<I", len(names))
    for nm in names:
        out += _string(nm)
    out += struct.pack("<I", len(blob)) + blob
    for tag, payload in chunks:
        out += tag.encode("latin-1")[::-1] + struct.pack("<I", 8 + len(payload)) + payload
    out += b"\0\0\0\0"                      # terminator
    return bytes(out)


# -- layout ------------------------------------------------------------------


def test_reads_the_texture_slot_names():
    sh = bsd9.parse(build())
    assert sh.texture_slots == ["t_Color", "t_Normal"]
    assert sh.strings == ["t_Color", "t_Normal", "DoIt"]
    assert sh.version == 1000


def test_slot_order_is_the_binding_order():
    """A scene's <Textures> list is positional, so this list must be too."""
    sh = bsd9.parse(build(slots=("t_Color", "t_Light", "t_SpecialMap", "t_Normal")))
    assert sh.slot(0) == "t_Color"
    assert sh.slot(3) == "t_Normal"
    assert sh.slot(4) is None
    assert sh.slot(-1) is None


def test_a_shader_with_no_textures_is_normal_not_an_error():
    """phong1_1.bsd9 really does declare none."""
    sh = bsd9.parse(build(slots=()))
    assert sh.texture_slots == []
    assert "no textures" in sh.describe()


def test_string_padding_is_the_nul_terminated_length_rounded_to_four():
    """The rule the whole header depends on; an off-by-one desyncs every name."""
    # 7 chars -> 7+NUL = 8, already aligned.  8 chars -> 9 -> padded to 12.
    sh = bsd9.parse(build(slots=("t_Color", "t_Normal"), extra=("ab", "abc", "abcd")))
    assert sh.strings == ["t_Color", "t_Normal", "ab", "abc", "abcd"]


def test_chunk_tags_are_stored_reversed():
    sh = bsd9.parse(build(chunks=(("VARL", b"\x00" * 4), ("MAIN", b"\x00" * 4))))
    assert [c.tag for c in sh.chunks] == ["VARL", "MAIN"]
    assert sh.chunks[0].size == 12          # payload plus the 8-byte header


def test_the_blob_is_kept_verbatim():
    """It is undecoded, so it must at least come back untouched."""
    blob = bytes(range(64))
    assert bsd9.parse(build(blob=blob)).blob == blob


# -- refusals ----------------------------------------------------------------


def test_the_other_container_is_refused_by_name():
    """mat_dist_2/3 have no XF magic and no scene references them."""
    with pytest.raises(UnsupportedFormat) as exc:
        bsd9.parse(b"\x07\x15\x01\x00\x00\x20\x02\x00" + b"\x00" * 0x40)
    assert "mat_dist" in str(exc.value)


def test_a_short_file_is_refused():
    with pytest.raises(ParseError):
        bsd9.parse(bsd9.MAGIC + b"\x00" * 4)


def test_a_chunk_walk_that_misses_the_end_is_an_error():
    """Every byte must be accounted for, or the layout is wrong for this file.

    A parser that shrugs at leftover bytes is one whose output nobody can rely
    on -- and this file is about to decide which texture is a normal map.
    """
    good = bytearray(build())
    good += b"\x99\x99\x99\x99"              # unexplained trailing bytes
    with pytest.raises(ParseError) as exc:
        bsd9.parse(bytes(good))
    assert "chunk walk" in str(exc.value)


def test_a_slot_index_past_the_string_table_is_an_error():
    d = bytearray(build(slots=("t_Color",), extra=()))
    struct.pack_into("<I", d, 16, 99)        # slot 0 -> string 99
    with pytest.raises(ParseError):
        bsd9.parse(bytes(d))


def test_is_shader_sniffs_without_parsing():
    assert bsd9.is_shader(build())
    assert not bsd9.is_shader(b"DDS " + b"\x00" * 32)


# -- the D3DX9 blob ----------------------------------------------------------


def d3dx_blob(params):
    """A minimal D3DX9 effect blob. ``params`` is [(name, semantic, type, cls,
    rows, cols, values, n_annotations)]."""
    # Data section: strings and values first, then the effect header.
    data = bytearray(b"\0" * 4)          # offset 0 is "absent", keep it unused
    placed = []

    def put_name(text):
        if text is None:
            return 0
        off = len(data)
        raw = text.encode("latin-1") + b"\0"
        data.extend(struct.pack("<I", len(raw)) + raw)
        data.extend(b"\0" * (-len(data) % 4))
        return off

    for nm, sem, typ, cls, rows, cols, vals, ann in params:
        n_off = put_name(nm)
        s_off = put_name(sem)
        v_off = 0
        if vals:
            v_off = len(data)
            data.extend(struct.pack(f"<{len(vals)}f", *vals))
        t_off = len(data)
        data.extend(struct.pack("<5I", typ, cls, n_off, s_off, 0))
        if cls == bsd9.CLASS_VECTOR:
            data.extend(struct.pack("<2I", cols, rows))
        elif cls in (bsd9.CLASS_SCALAR, bsd9.CLASS_MATRIX_ROWS,
                     bsd9.CLASS_MATRIX_COLUMNS):
            data.extend(struct.pack("<2I", rows, cols))
        placed.append((t_off, v_off, ann))

    start = len(data)
    data.extend(struct.pack("<4I", len(params), 0, 0, 0))
    for t_off, v_off, ann in placed:
        data.extend(struct.pack("<4I", t_off, v_off, 0, ann))
        data.extend(b"\0" * (ann * 8))       # annotations: two dwords each
    return struct.pack("<2I", bsd9.D3DX9_TAG, start) + bytes(data)


def test_reads_parameter_names_semantics_and_defaults():
    blob = d3dx_blob([
        ("g_Bumpiness", "Bumpiness", 3, bsd9.CLASS_SCALAR, 1, 1, (1.0,), 0),
        ("g_MaterialDiffuse", "Diffuse", 3, bsd9.CLASS_VECTOR, 1, 4,
         (1.0, 1.0, 1.0, 1.0), 0),
        ("t_Color", None, 5, bsd9.CLASS_OBJECT, 0, 0, None, 0),
    ])
    ps = bsd9.parse_parameters(blob)

    assert [p.name for p in ps] == ["g_Bumpiness", "g_MaterialDiffuse", "t_Color"]
    assert ps[0].semantic == "Bumpiness"
    assert ps[0].default == (1.0,)
    assert ps[1].default == (1.0, 1.0, 1.0, 1.0)
    assert ps[1].rows == 1 and ps[1].columns == 4
    assert ps[2].semantic is None
    assert ps[2].is_texture and not ps[0].is_texture


def test_annotations_are_two_dwords_not_four():
    """The one trap in the blob: get it wrong and every later name is garbage."""
    blob = d3dx_blob([
        ("g_First", "First", 3, bsd9.CLASS_SCALAR, 1, 1, (1.0,), 2),
        ("g_Second", "Second", 3, bsd9.CLASS_SCALAR, 1, 1, (2.0,), 0),
    ])
    ps = bsd9.parse_parameters(blob)
    assert [p.name for p in ps] == ["g_First", "g_Second"]
    assert ps[1].default == (2.0,)


def test_vector_dimensions_are_stored_columns_first():
    """Vectors are columns-then-rows; scalars and matrices the other way.

    An asymmetry in the format, not a typo -- a 1x4 vector read the wrong way
    round becomes 4x1 and its default value is read as one float, not four.
    """
    blob = d3dx_blob([
        ("v", "V", 3, bsd9.CLASS_VECTOR, 1, 4, (1.0, 2.0, 3.0, 4.0), 0),
        ("m", "M", 3, bsd9.CLASS_MATRIX_ROWS, 4, 4, None, 0),
    ])
    v, m = bsd9.parse_parameters(blob)
    assert (v.rows, v.columns) == (1, 4)
    assert v.default == (1.0, 2.0, 3.0, 4.0)
    assert (m.rows, m.columns) == (4, 4)


def test_a_blob_that_is_not_a_d3dx_effect_yields_no_parameters():
    """"None available" and "none declared" are the same to a caller here."""
    assert bsd9.parse_parameters(b"") == []
    assert bsd9.parse_parameters(b"\x00" * 64) == []


def test_semantics_maps_what_a_scene_can_address():
    blob = d3dx_blob([
        ("g_Bumpiness", "Bumpiness", 3, bsd9.CLASS_SCALAR, 1, 1, (1.0,), 0),
        ("g_Private", None, 3, bsd9.CLASS_SCALAR, 1, 1, (0.0,), 0),
    ])
    sh = bsd9.parse(build(slots=(), extra=("DoIt",), blob=blob))
    assert set(sh.semantics()) == {"Bumpiness"}
    assert sh.semantics()["Bumpiness"].name == "g_Bumpiness"


# -- the claims, against real files ------------------------------------------


@pytest.mark.corpus
def test_corpus_every_shader_parses_and_accounts_for_every_byte(corpus):
    """230 of the 232 shipped files; the 2 refusals are the other container."""
    files = collect(corpus, "*.bsd9")
    if not files:
        pytest.skip("no .bsd9 in corpus")
    ok, refused = 0, []
    for p in files:
        try:
            bsd9.parse(p.read_bytes(), path=str(p))
            ok += 1
        except UnsupportedFormat:
            refused.append(p.name.lower())
        except ParseError as exc:
            pytest.fail(f"{p.name}: {exc}")
    assert ok
    # Only the two known ones may be refused, and only for the magic.
    assert set(refused) <= {"mat_dist_2.bsd9", "mat_dist_3.bsd9"}, refused


@pytest.mark.corpus
def test_corpus_every_parameter_has_a_plausible_name(corpus):
    """The integrity check for the blob walk.

    A desynchronised walk does not raise -- it yields plausible-*looking*
    records with garbage names, which is exactly the failure that would put
    invented values in an editor.  Requiring every one of ~8,700 names to be a
    C identifier is what catches it.
    """
    import re

    ident = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
    files = collect(corpus, "*.bsd9")
    if not files:
        pytest.skip("no .bsd9 in corpus")
    seen = 0
    for p in files:
        try:
            sh = bsd9.parse(p.read_bytes(), path=str(p))
        except UnsupportedFormat:
            continue
        for param in sh.parameters:
            seen += 1
            assert param.name and ident.match(param.name), (p.name, param.name)
            if param.semantic:
                assert ident.match(param.semantic), (p.name, param.semantic)
    assert seen


@pytest.mark.corpus
def test_corpus_mat_main_declares_the_material_and_parameter_semantics(corpus):
    """The 17-float <Material> shape, named by the shader itself.

    Four 1x4 vectors plus a scalar is 17.  It does **not** settle the *order*
    of those floats in the scene XML -- D3DX binds by semantic, not position.
    """
    for p in collect(corpus, "*.bsd9"):
        if p.name.lower() != "mat_main.bsd9":
            continue
        sem = bsd9.parse(p.read_bytes(), path=str(p)).semantics()
        for name in ("Diffuse", "Ambient", "Specular", "Emissive"):
            assert sem[name].rows == 1 and sem[name].columns == 4
        assert sem["SpecularPower"].cls == bsd9.CLASS_SCALAR
        # The six semantics that appear on essentially every material.
        for name in ("Bumpiness", "Reflectivity", "EmissiveFactor",
                     "DetailRepeat", "DetailIntensity", "Roughness"):
            assert name in sem, name
        # And the environment slot the old spec could only guess at.
        assert sem["ENVIRONMENT"].name == "t_Reflection"
        return
    pytest.skip("mat_main.bsd9 not in corpus")


@pytest.mark.corpus
def test_corpus_mat_main_names_the_four_slots_the_spec_guessed(corpus):
    """specs/scene.md called slot 3 "environment"; the shader calls it
    t_Reflection.  Same slot, and now it has the shader's own name."""
    for p in collect(corpus, "*.bsd9"):
        if p.name.lower() == "mat_main.bsd9":
            sh = bsd9.parse(p.read_bytes(), path=str(p))
            assert sh.texture_slots == [
                "t_Color", "t_Light", "t_Normal", "t_Reflection"
            ]
            return
    pytest.skip("mat_main.bsd9 not in corpus")


# --------------------------------------------------------------------------
# the technique, pass and object tables
# --------------------------------------------------------------------------
#
# The fixture below is a whole D3DX9 effect built by hand, because the thing
# worth testing is the *walk*: a D3DX walk that has drifted does not raise, it
# keeps producing records. So each test here checks that the walk lands where
# it should, and the corpus test at the bottom checks it against real files.


def _d3dx_string(text: str) -> bytes:
    """A D3DX string: size **including** the NUL, then the bytes, padded."""
    raw = text.encode("latin-1") + b"\0"
    return struct.pack("<I", len(raw)) + raw + b"\0" * (-len(raw) % 4)


def _effect(techniques=(("Main", ("P0",)),), objects=(), inline=()):
    """A structurally exact D3DX9 effect blob with no parameters."""
    # Offset 0 means "no name" in this format -- a parameter with no semantic
    # stores 0 -- so the string table cannot start there.
    strings = bytearray(4)          # offset 0 means "no name" in this format
    offsets = {}

    def add(text):
        if text not in offsets:
            offsets[text] = len(strings)
            strings.extend(_d3dx_string(text))
        return offsets[text]

    for name, passes in techniques:
        add(name)
        for one in passes:
            add(one)

    body = bytearray()
    header_at = len(strings)
    body += struct.pack("<4I", 0, len(techniques), 0, len(objects) + len(inline))
    for name, passes in techniques:
        body += struct.pack("<3I", offsets[name], 0, len(passes))
        for one in passes:
            body += struct.pack("<3I", offsets[one], 0, 0)
    body += struct.pack("<2I", len(inline), len(objects))
    for oid, payload in inline:
        body += struct.pack("<2I", oid, len(payload)) + payload
        body += b"\0" * (-len(payload) % 4)
    for head, payload in objects:
        body += struct.pack("<5I", *head) + struct.pack("<I", len(payload)) + payload
        body += b"\0" * (-len(payload) % 4)

    data = bytes(strings) + bytes(body)
    return struct.pack("<2I", bsd9.D3DX9_TAG, header_at) + data


def test_techniques_and_passes_are_read_in_order():
    blob = _effect(techniques=(("V20P20", ("P0",)),
                               ("Occluded", ("preOcclud", "P0"))))
    _params, techniques, _objects = bsd9.parse_effect(blob)
    assert [t.name for t in techniques] == ["V20P20", "Occluded"]
    assert [p.name for p in techniques[1].passes] == ["preOcclud", "P0"]


def test_a_walk_that_does_not_reach_the_end_is_refused():
    """A drifted D3DX walk keeps producing records; only the end catches it."""
    blob = _effect() + b"\x00\x00\x00\x00"
    with pytest.raises(ParseError) as caught:
        bsd9.parse_effect(blob)
    assert "unaccounted" in str(caught.value)


def test_a_blob_that_is_not_an_effect_yields_nothing_rather_than_raising():
    assert bsd9.parse_effect(b"not an effect at all") == ([], [], [])


def test_shader_bytecode_is_recognised_by_its_version_token():
    """0xFFFE is a vertex shader, 0xFFFF a pixel one; the low word is the version."""
    vs = struct.pack("<I", 0xFFFE0101) + b"\0" * 8
    ps = struct.pack("<I", 0xFFFF0200) + b"\0" * 8
    other = b"CTAB" + b"\0" * 8
    blob = _effect(objects=((( 0, 0, 0xFFFFFFFF, 0, 0), vs),
                            ((0, 0, 0xFFFFFFFF, 1, 0), ps),
                            ((0, 0, 0xFFFFFFFF, 2, 0), other)))
    _params, _techniques, objects = bsd9.parse_effect(blob)
    assert [o.shader_model for o in objects] == ["vs_1_1", "ps_2_0", None]


def test_an_object_is_handed_to_the_pass_that_names_it():
    vs = struct.pack("<I", 0xFFFE0101) + b"\0" * 8
    blob = _effect(techniques=(("T", ("A", "B")),),
                   objects=(((0, 1, 0xFFFFFFFF, 0, 0), vs),))
    _params, techniques, _objects = bsd9.parse_effect(blob)
    assert techniques[0].passes[0].objects == []
    assert len(techniques[0].passes[1].shaders) == 1


def test_an_object_written_for_a_parameter_belongs_to_no_pass():
    """Those store 0xFFFFFFFF as the technique and a parameter index instead."""
    blob = _effect(objects=(((0xFFFFFFFF, 60, 0, 0, 1), b"tex.dds\0"),))
    _params, techniques, objects = bsd9.parse_effect(blob)
    assert objects[0].belongs_to_pass is False
    assert all(not p.objects for t in techniques for p in t.passes)


def test_inline_objects_are_read_too():
    blob = _effect(inline=((3, b"hello\0\0\0"),))
    _params, _techniques, objects = bsd9.parse_effect(blob)
    assert len(objects) == 1
    assert objects[0].data.startswith(b"hello")


# -- the corpus --------------------------------------------------------------


def test_corpus_every_effect_walk_lands_exactly(corpus):
    """230 of 230 shipped shaders, which is what settled the layout.

    The resource header being five dwords was decided here: four and six leave
    most files with bytes unaccounted for.
    """
    import re

    ident = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
    files = collect(corpus, "*.bsd9")
    if not files:
        pytest.skip("no .bsd9 files in the corpus")
    walked = techniques = passes = 0
    for path in files:
        try:
            shader = bsd9.parse(path.read_bytes(), path=str(path))
        except (ParseError, UnsupportedFormat):
            continue
        found = shader.techniques          # raises if the walk does not land
        walked += 1
        techniques += len(found)
        for technique in found:
            assert ident.match(technique.name or ""), (path, technique.name)
            passes += len(technique.passes)
            for one in technique.passes:
                assert ident.match(one.name or ""), (path, one.name)
    assert walked > 100, f"only {walked} shaders walked"
    assert techniques and passes
