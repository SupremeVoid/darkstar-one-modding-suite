"""
Scene XML: exact round-trip, the submesh invariant, and reference resolution.

The round-trip test is the important one.  A scene serialiser that reformats is
not merely untidy -- the app's headline feature is diffing a mod against stock,
and a reformatting writer turns every one-line edit into a whole-file diff,
which destroys that feature.  So "byte-identical" is a hard requirement, not a
nicety, and it is asserted over every scene the machine can find.
"""

from __future__ import annotations

import struct

import pytest

from conftest import collect
from dsotools.errors import ParseError
from dsotools.formats import scene


# --------------------------------------------------------------------------
# unit
# --------------------------------------------------------------------------

MINIMAL = (
    b'<?xml version="1.0"?>\r\n'
    b'<WalhallaScene Date="04/07/06" Time="16:49:40" Version="2.00">\r\n'
    b'\t<Object Type=".?AVCWorldRoot@@" Flags="98304">\r\n'
    b"\t\t<AttachedObjects>\r\n"
    b'\t\t\t<Object Type=".?AVCMesh@@" Name="m" Resrc3DO="objects/x.3do">\r\n'
    b'\t\t\t\t<EffectContainer Path="blender/mat_main.bsd9">\r\n'
    b"\t\t\t\t\t<Material>\r\n\t\t\t\t\t\t+1.000000 +0.000000\r\n\t\t\t\t\t</Material>\r\n"
    b"\t\t\t\t\t<Parameters>\r\n"
    b'\t\t\t\t\t\t<Float semantic="Bumpiness" value="+1.000000" />\r\n'
    b'\t\t\t\t\t\t<Float semantic="FresnelBias" />\r\n'
    b"\t\t\t\t\t</Parameters>\r\n"
    b'\t\t\t\t\t<Textures Number="2">\r\n'
    b"\t\t\t\t\t\ttextures/a_col.dds\r\n\t\t\t\t\t\ttextures/a_nrm.dds\r\n"
    b"\t\t\t\t\t</Textures>\r\n"
    b"\t\t\t\t</EffectContainer>\r\n"
    b"\t\t\t</Object>\r\n"
    b"\t\t</AttachedObjects>\r\n"
    b"\t</Object>\r\n"
    b"</WalhallaScene>\r\n"
)


def test_minimal_round_trip_is_byte_identical():
    assert scene.parse(MINIMAL).to_bytes() == MINIMAL


def test_type_demangling():
    assert scene.decode_type(".?AVCMesh@@") == "CMesh"
    assert scene.encode_type("CMesh") == ".?AVCMesh@@"
    # Unrecognised input passes through rather than being mangled further.
    assert scene.decode_type("CMesh") == "CMesh"


def test_reads_model_shader_and_textures():
    s = scene.parse(MINIMAL)
    (mesh,) = s.meshes()
    assert mesh.model == "objects/x.3do"
    (eff,) = mesh.effects
    assert eff.shader == "blender/mat_main.bsd9"
    assert eff.textures == ["textures/a_col.dds", "textures/a_nrm.dds"]
    assert eff.texture_count == 2


def test_parameter_without_value_is_none_not_zero():
    """A ``<Float semantic="X" />`` means 'shader default'.

    Coercing it to 0.0 would silently change the material, so it must stay
    distinguishable.
    """
    (eff,) = scene.parse(MINIMAL).meshes()[0].effects
    params = eff.parameters
    assert params["Bumpiness"] == pytest.approx(1.0)
    assert "FresnelBias" in params
    assert params["FresnelBias"] is None


def test_set_texture_preserves_surrounding_whitespace():
    s = scene.parse(MINIMAL)
    (eff,) = s.meshes()[0].effects
    eff.set_texture(1, "textures/replaced_nrm.dds")
    out = s.to_bytes()
    assert b"textures/replaced_nrm.dds" in out
    assert b"textures/a_col.dds" in out
    # everything except the one token is untouched
    assert out.replace(b"replaced_nrm", b"a_nrm") == MINIMAL


def test_set_texture_out_of_range():
    (eff,) = scene.parse(MINIMAL).meshes()[0].effects
    with pytest.raises(IndexError):
        eff.set_texture(5, "x.dds")


def test_missing_trailing_newline_is_preserved():
    """Found in a modder-authored scene, not in any Ascaron file.

    Every stock scene ends with CRLF, so appending one unconditionally passed
    the whole stock corpus and still broke byte-exact round-trip on the one
    file in the wild that lacked it.
    """
    no_nl = MINIMAL.rstrip(b"\r\n")
    assert scene.parse(no_nl).to_bytes() == no_nl
    assert scene.parse(MINIMAL).to_bytes() == MINIMAL


def test_lf_source_stays_lf():
    lf = MINIMAL.replace(b"\r\n", b"\n")
    assert scene.parse(lf).to_bytes() == lf


def test_rejects_non_scene_root():
    from dsotools.errors import ParseError

    with pytest.raises(ParseError):
        scene.parse(b'<?xml version="1.0"?><ASE_Database/>')


def test_object_path_matches_loddesc_style():
    s = scene.parse(MINIMAL)
    (mesh,) = s.meshes()
    assert mesh.path() == "m"


# --------------------------------------------------------------------------
# corpus
# --------------------------------------------------------------------------


@pytest.mark.corpus
def test_corpus_round_trip_byte_identical(corpus):
    files = [p for p in collect(corpus, "*.xml") if scene.is_scene(p.read_bytes()[:512])]
    if not files:
        pytest.skip("no scene XML in corpus")
    bad = []
    for p in files:
        raw = p.read_bytes()
        if scene.parse(raw, path=str(p)).to_bytes() != raw:
            bad.append(p.name)
    assert not bad, f"{len(bad)}/{len(files)} scenes did not round-trip: {bad[:10]}"


def _submesh_total(data: bytes) -> int:
    """Read ``submesh_total`` straight from the ``.3do`` root header.

    Deliberately independent of ``formats.threedo`` so this test cannot pass
    because the parser and the writer share a misunderstanding.
    """
    assert data[:4] == b"OD3 "
    return struct.unpack_from("<I", data, 0x30)[0]


# --------------------------------------------------------------------------
# the source's own formatting, and the one malformedness we tolerate
#
# Both settled 2026-08-16 against the real installation.  Before it: 3 of
# Ascaron's own scenes could not be parsed at all, and behind them sat 55 that
# parsed but re-serialised with different formatting -- invisible, because the
# round-trip check aborted on the first of the 3 and never reached them.
# --------------------------------------------------------------------------


#: `</object>` closing `<Object>`.  Exactly what AsteroidsVolume05/10/20 carry,
#: one occurrence each, and the engine loads them.
MISMATCHED_CASE = (
    b'<?xml version="1.0"?>\r\n'
    b'<WalhallaScene Version="2.00">\r\n'
    b'\t<Object Type=".?AVCWorldRoot@@">\r\n'
    b'\t\t<AABB SpcExt="+1.0 +1.0 +1.0 +0.000000 " />\r\n'
    b"\t</object>\r\n"
    b"</WalhallaScene>\r\n"
)

#: A start tag broken across lines with the `=` signs aligned, and a self-close
#: with no space before the slash.  Both appear in stock `CPostModVolume` nodes.
ODD_LAYOUT = (
    b'<?xml version="1.0"?>\r\n'
    b'<WalhallaScene Version="2.00">\r\n'
    b'\t<Object Type=".?AVCPostModVolume@@"\r\n'
    b'\t\t\tName                = "DefaultNebel"\r\n'
    b'\t\t\tFogEnable           = "1"\r\n'
    b'\t\t\tFogEnd              = "+1500"/>\r\n'
    b'\t<AABB SpcExt="+1.0 +1.0"/>\r\n'
    b"</WalhallaScene>\r\n"
)


def test_a_case_mismatched_close_tag_is_accepted():
    """The engine's reader is this lenient and ours has to be.

    Narrowest rule that covers the evidence: no other malformed
    ``WalhallaScene`` exists anywhere in the corpus.
    """
    s = scene.parse(MISMATCHED_CASE)
    assert s.repaired_tags == ["object"]
    assert len(s.root_objects) == 1


def test_a_repaired_close_tag_is_written_back_exactly_as_it_was():
    """Tolerating it must not mean rewriting it.

    Byte-exact round-trip is what makes diff-against-stock trustworthy, so the
    file keeps its own `</object>` -- the repair exists to read the file, not to
    correct the author.
    """
    assert scene.parse(MISMATCHED_CASE).to_bytes() == MISMATCHED_CASE


def test_a_tolerated_file_still_says_it_was_malformed():
    """Silently accepting it would hide a real defect in a modder's scene."""
    assert scene.parse(MISMATCHED_CASE).repaired_tags
    assert scene.parse(MINIMAL).repaired_tags == []


def test_tolerance_does_not_extend_to_other_malformedness():
    """Only case.  A bare `&` has no single right re-escaping, so it is refused."""
    broken = MINIMAL.replace(b'Name="m"', b'Name="a & b"')
    with pytest.raises(ParseError):
        scene.parse(broken)


def test_a_genuinely_mismatched_tag_is_still_an_error():
    """`</Other>` closing `<Object>` is not a case difference."""
    broken = MISMATCHED_CASE.replace(b"</object>", b"</Nother>")
    with pytest.raises(ParseError):
        scene.parse(broken)


def test_multiline_attributes_and_tight_self_close_survive_round_trip():
    """The 55-file bug: content was never lost, only Ascaron's layout."""
    assert scene.parse(ODD_LAYOUT).to_bytes() == ODD_LAYOUT


def test_editing_one_attribute_reformats_only_that_tag():
    """The recorded spelling stops applying exactly where it stops being true.

    The edited tag is rebuilt; every other tag keeps the source's layout, so a
    one-attribute change stays a one-tag diff.
    """
    s = scene.parse(ODD_LAYOUT)
    s.element.find("Object").set("FogEnd", "+2500")
    out = s.to_bytes()

    assert b'FogEnd="+2500"' in out
    # The untouched sibling keeps its tight self-close.
    assert b'<AABB SpcExt="+1.0 +1.0"/>' in out
    # The edited one is rebuilt, so it is no longer spread over four lines.
    assert b'Name                = "DefaultNebel"' not in out


def test_editing_text_keeps_the_surrounding_tags_verbatim():
    """A `<Material>` edit changes text, not tags -- so no tag may be reflowed."""
    s = scene.parse(ODD_LAYOUT)
    s.element.find("Object").set("FogEnable", "0")
    out = s.to_bytes()
    assert out.startswith(b'<?xml version="1.0"?>\r\n<WalhallaScene Version="2.00">\r\n')


@pytest.mark.corpus
def test_effectcontainer_count_equals_submesh_total(corpus):
    """SCN001: one EffectContainer per submesh, across all LODs.

    Measured at 9,557/9,559 on stock data.  The two known exceptions are
    ``objects/mainshape_20.3do`` in TunnelVersion1{,_low}.xml -- a defect in
    Ascaron's shipped data, so they are tolerated by name rather than by
    loosening the rule.

    An earlier draft asserted this against the LOD-0 submesh count instead.
    That was wrong, and it produced 623 false positives on a real mod.  The
    check below is the corrected one; do not "simplify" it back.
    """
    KNOWN_BAD = {"mainshape_20.3do"}

    models = {p.name.lower(): p for p in collect(corpus, "*.3do")}
    scenes = [p for p in collect(corpus, "*.xml") if scene.is_scene(p.read_bytes()[:512])]
    if not models or not scenes:
        pytest.skip("corpus lacks models or scenes")

    checked = 0
    bad = []
    for p in scenes:
        s = scene.parse(p.read_bytes(), path=str(p))
        for mesh in s.meshes():
            ref = mesh.model
            if not ref:
                continue
            name = ref.replace("\\", "/").rsplit("/", 1)[-1].lower()
            target = models.get(name)
            if target is None:
                continue
            total = _submesh_total(target.read_bytes()[:0x2000])
            checked += 1
            if len(mesh.effects) != total and name not in KNOWN_BAD:
                bad.append((p.name, ref, total, len(mesh.effects)))
    if not checked:
        pytest.skip("no mesh reference resolved against the corpus")
    assert not bad, f"{len(bad)}/{checked} meshes violate SCN001: {bad[:8]}"


@pytest.mark.corpus
def test_non_mesh_effect_owners_are_excluded(corpus):
    """Glow/shine/shield/distortion nodes must not be counted as meshes.

    This is the exact bug that produced the 623 false positives: a textual scan
    attributes their EffectContainers to whatever mesh precedes them.
    """
    scenes = [p for p in collect(corpus, "*.xml") if scene.is_scene(p.read_bytes()[:512])]
    if not scenes:
        pytest.skip("no scenes")
    saw_owner = False
    for p in scenes:
        s = scene.parse(p.read_bytes(), path=str(p))
        for obj in s.walk():
            if obj.type in scene.NON_MESH_EFFECT_OWNERS:
                saw_owner = True
                assert obj not in s.meshes()
                assert not obj.is_mesh
    if not saw_owner:
        pytest.skip("corpus contains no non-mesh effect owners")


# --------------------------------------------------------------------------
# editing an EffectContainer
# --------------------------------------------------------------------------

MATERIAL_SCENE = (
    b'<?xml version="1.0"?>\r\n<WalhallaScene Version="2.00">\r\n'
    b'\t<Object Type=".?AVCMesh@@" Name="m" Resrc3DO="objects/x.3do">\r\n'
    b'\t\t<EffectContainer Path="blender/mat_main.bsd9">\r\n'
    b'\t\t\t<Material>\r\n'
    b'\t\t\t\t+1.000000 +1.000000 +1.000000 +0.000000\r\n'
    b'\t\t\t\t+1.000000 +1.000000 +1.000000 +0.000000\r\n'
    b'\t\t\t\t+1.000000 +1.000000 +1.000000 +0.000000\r\n'
    b'\t\t\t\t+0.000000 +0.000000 +0.000000 +0.000000\r\n'
    b'\t\t\t\t+200.000000\r\n'
    b'\t\t\t</Material>\r\n'
    b'\t\t\t<Parameters>\r\n'
    b'\t\t\t\t<Float semantic="Bumpiness" value="+1.000000" />\r\n'
    b'\t\t\t\t<Float semantic="Roughness" />\r\n'
    b'\t\t\t</Parameters>\r\n'
    b'\t\t</EffectContainer>\r\n'
    b'\t</Object>\r\n</WalhallaScene>\r\n'
)


def test_material_reads_seventeen_floats():
    sc = scene.parse(MATERIAL_SCENE)
    mat = sc.meshes()[0].effects[0].material
    assert len(mat.values) == 17
    assert mat.power == 200.0
    assert mat.rows()[0] == (1.0, 1.0, 1.0, 0.0)


def test_rewriting_a_material_with_its_own_values_changes_nothing():
    """Byte-exactness is what makes diff-against-stock worth reading."""
    sc = scene.parse(MATERIAL_SCENE)
    effect = sc.meshes()[0].effects[0]
    effect.set_material(list(effect.material.values))
    assert sc.to_bytes() == MATERIAL_SCENE


def test_changing_the_material_touches_only_that_block():
    sc = scene.parse(MATERIAL_SCENE)
    effect = sc.meshes()[0].effects[0]
    values = list(effect.material.values)
    values[16] = 42.0
    effect.set_material(values)

    out = sc.to_bytes()
    assert b"+42.000000" in out
    before = MATERIAL_SCENE.split(b"\r\n")
    after = out.split(b"\r\n")
    assert sum(1 for a, b in zip(before, after) if a != b) == 1


def test_material_refuses_the_wrong_number_of_floats():
    from dsotools.errors import ValidationError

    sc = scene.parse(MATERIAL_SCENE)
    with pytest.raises(ValidationError):
        sc.meshes()[0].effects[0].set_material([1.0, 2.0, 3.0])


def test_a_parameter_with_no_value_means_shader_default():
    """A <Float> with no value attribute is legal and common."""
    sc = scene.parse(MATERIAL_SCENE)
    params = sc.meshes()[0].effects[0].parameters
    assert params["Bumpiness"] == 1.0
    assert params["Roughness"] is None


def test_setting_a_parameter_uses_the_shipped_float_format():
    sc = scene.parse(MATERIAL_SCENE)
    sc.meshes()[0].effects[0].set_parameter("Roughness", 0.25)
    assert b'semantic="Roughness" value="+0.250000"' in sc.to_bytes()


# --------------------------------------------------------------------------
# blinker groups
#
# The one thing in a scene with no geometry and no material: 621 across the
# corpus, all the same shape -- a Texture attribute and a list of point
# sprites.  Drawing one means billboards, which is why meshview shows markers.
# --------------------------------------------------------------------------

BLINKERS = (
    b'<?xml version="1.0"?>\r\n<WalhallaScene Version="2.00">\r\n'
    b'\t<Object Type=".?AVCWorldRoot@@">\r\n\t\t<AttachedObjects>\r\n'
    b'\t\t\t<Object Type=".?AVCBlinkerGroup@@" Name="blinks_0" '
    b'Texture="textures/comsat_blink.dds">\r\n'
    b'\t\t\t\t<Blinker displacement="-0.000000 +1.496899 +5.060813 +0.200000 " '
    b'vrow="+0.111000" animtime="+1.000000" />\r\n'
    b'\t\t\t\t<Blinker displacement="-0.000000 +1.057755 +6.728370 +0.200000 " '
    b'vrow="+0.111000" animtime="+1.000000" />\r\n'
    b"\t\t\t</Object>\r\n"
    b"\t\t</AttachedObjects>\r\n\t</Object>\r\n</WalhallaScene>\r\n"
)


def test_a_blinker_group_reads_its_texture_and_its_lights():
    (group,) = scene.parse(BLINKERS).blinker_groups()

    assert group.name == "blinks_0"
    assert group.texture == "textures/comsat_blink.dds"
    assert len(group) == 2

    first = group.blinkers[0]
    # Component by component: the offline runner's `approx` stub takes scalars,
    # and tests must stay inside that surface (see STATE.md).
    x, y, z = first.position
    assert x == pytest.approx(0.0)
    assert y == pytest.approx(1.496899)
    assert z == pytest.approx(5.060813)
    assert first.size == pytest.approx(0.2)
    assert first.vrow == pytest.approx(0.111)
    assert first.animtime == pytest.approx(1.0)


def test_a_blinker_group_has_no_model_and_no_effect():
    """Which is why it is not a drawable layer."""
    (group,) = scene.parse(BLINKERS).blinker_groups()
    assert group.object.model is None
    assert group.object.effects == []
    assert list(group.object.children) == []


def test_adding_then_removing_a_blinker_round_trips_byte_exactly():
    """The whole point of the layout-preserving serialiser, applied here."""
    sc = scene.parse(BLINKERS)
    (group,) = sc.blinker_groups()

    group.add((1.0, 2.0, 3.0), 0.25, 0.5, 2.0)
    assert len(group) == 3
    assert sc.to_bytes() != BLINKERS

    group.remove(2)
    assert len(group) == 2
    assert sc.to_bytes() == BLINKERS


def test_editing_one_blinker_touches_only_its_line():
    sc = scene.parse(BLINKERS)
    (group,) = sc.blinker_groups()
    group.blinkers[0].set_values((9.0, 8.0, 7.0), 0.5, 0.111, 1.0)
    out = sc.to_bytes()

    changed = [
        (a, b) for a, b in zip(BLINKERS.split(b"\r\n"), out.split(b"\r\n")) if a != b
    ]
    assert len(changed) == 1
    assert b"+9.000000" in out
    assert b"+6.728370" in out          # the untouched sibling survives


def test_numbers_are_written_in_the_shipped_format():
    """Explicit sign, six decimals, trailing space -- as every shipped file."""
    sc = scene.parse(BLINKERS)
    (group,) = sc.blinker_groups()
    group.blinkers[0].set_values((1.0, 2.0, 3.0), 0.2, 0.111, 1.0)
    assert b'displacement="+1.000000 +2.000000 +3.000000 +0.200000 "' in sc.to_bytes()


def test_removing_the_last_blinker_keeps_the_closing_tag_indented():
    sc = scene.parse(BLINKERS)
    (group,) = sc.blinker_groups()
    group.remove(1)
    out = sc.to_bytes()
    assert len(scene.parse(out).blinker_groups()[0]) == 1
    assert b"\r\n\t\t\t</Object>" in out


# --------------------------------------------------------------------------
# editing the submesh list
# --------------------------------------------------------------------------
#
# SCN001 -- one EffectContainer per submesh across all LODs -- was reportable
# and not fixable until these existed.  Whitespace lives in element tails, so
# the interesting property is not "it parses afterwards" but "adding one and
# removing it again gives back the original bytes".


def test_adding_a_submesh_copies_the_last_one():
    s = scene.parse(MINIMAL)
    mesh = s.meshes()[0]
    before = mesh.effects[0]

    added = mesh.add_effect()

    assert len(mesh.effects) == 2
    # A blank container draws nothing; the useful starting point is the
    # neighbouring submesh's shader, material and texture slots.
    assert added.shader == before.shader
    assert added.textures == before.textures


def test_add_then_remove_is_byte_identical():
    """The whitespace test.  Tails carry the indentation, so this is the one."""
    s = scene.parse(MINIMAL)
    mesh = s.meshes()[0]
    mesh.add_effect()
    grown = s.to_bytes()
    assert grown != MINIMAL

    s2 = scene.parse(grown)
    s2.meshes()[0].remove_effect(1)

    assert s2.to_bytes() == MINIMAL


def test_the_added_one_is_indented_like_its_neighbours():
    s = scene.parse(MINIMAL)
    s.meshes()[0].add_effect()
    lines = s.to_bytes().decode("cp1252").splitlines()
    opens = [ln for ln in lines if ln.lstrip().startswith("<EffectContainer")]
    assert len(opens) == 2
    # Same leading whitespace, or the file reads as hand-mangled.
    assert len(opens[0]) - len(opens[0].lstrip()) == \
        len(opens[1]) - len(opens[1].lstrip())


def test_removing_the_only_submesh_leaves_a_parsable_scene():
    s = scene.parse(MINIMAL)
    s.meshes()[0].remove_effect(0)
    out = s.to_bytes()
    assert scene.parse(out).meshes()[0].effects == []


def test_removing_an_index_that_is_not_there_raises():
    s = scene.parse(MINIMAL)
    with pytest.raises(ParseError):
        s.meshes()[0].remove_effect(4)
    with pytest.raises(ParseError):
        s.meshes()[0].remove_effect(-1)


def test_a_mesh_with_no_container_gets_a_usable_one():
    """Nothing to copy, so one is built rather than nothing happening."""
    s = scene.parse(MINIMAL)
    mesh = s.meshes()[0]
    mesh.remove_effect(0)

    mesh.add_effect()

    out = scene.parse(s.to_bytes())
    effects = out.meshes()[0].effects
    assert len(effects) == 1
    assert effects[0].material is not None
