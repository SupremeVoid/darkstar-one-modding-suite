"""
Turning a scene into draw calls.

The normal-map test is the one that matters. Reading `_nrm` as RGB is not a
subtle error -- it lights every surface from a garbage normal and the model
renders black and speckled -- and it is invisible to any structural check.
"""

from __future__ import annotations

import math
import struct

import pytest

from dsotools.edit import meshview


def test_unswizzle_reconstructs_a_unit_normal():
    """X from alpha, Y from green, Z implied. Measured, not assumed."""
    # a pixel whose stored X/Y are both zero => a normal pointing straight out
    flat = bytes([0, 128, 0, 128])
    out = meshview.unswizzle_normal(flat)

    assert out[0] == 128            # X carried over from alpha
    assert out[1] == 128            # Y carried over from green
    # Z reconstructed to (very nearly) full: 128 encodes +0.0039, not exactly
    # zero -- the exact centre is 127.5 and is not representable in a byte.
    assert out[2] >= 254
    assert out[3] == 255

    x = out[0] / 127.5 - 1.0
    y = out[1] / 127.5 - 1.0
    z = out[2] / 127.5 - 1.0
    assert math.isclose(math.sqrt(x * x + y * y + z * z), 1.0, abs_tol=0.02)


def test_unswizzle_clamps_instead_of_taking_a_negative_root():
    """A pixel whose X/Y exceed the unit circle must not raise."""
    out = meshview.unswizzle_normal(bytes([0, 255, 0, 255]))
    assert out[2] == 127            # z = 0, encoded


def test_the_numpy_and_python_unswizzles_agree():
    if meshview._np is None:                     # pragma: no cover
        pytest.skip("numpy not installed")
    data = bytes(range(256)) * 8
    assert meshview._unswizzle_numpy(data) == meshview._unswizzle_python(data)


def test_pick_slots_prefers_names_over_positions():
    """mat_biotechanim has one slot and it is a light map, not albedo."""
    main = [
        "textures/a_col.dds", "textures/a_lgh.dds",
        "textures/a_nrm.dds", "textures/defaultspace.dds",
    ]
    assert meshview.pick_slots(main) == ("textures/a_col.dds", "textures/a_nrm.dds")

    biotech = ["textures/b_lgh.dds"]
    base, normal = meshview.pick_slots(biotech)
    assert base == "textures/b_lgh.dds"          # something to look at
    assert normal is None                        # but never a fabricated normal


def test_pick_slots_never_uses_a_normal_map_as_albedo():
    base, _ = meshview.pick_slots(["textures/only_nrm.dds"])
    assert base is None


def test_pick_slots_handles_an_empty_slot_list():
    assert meshview.pick_slots([]) == (None, None)


# -- the shader has the last word -------------------------------------------
#
# `.bsd9` was decoded on 2026-08-16 and it names its own slots.  The suffix
# rules above are now the *fallback*, for shaders that name nothing useful and
# for the 466 effects whose shader is not in the installation.


def test_the_shader_beats_the_filename_for_the_normal_map():
    """The 841-submesh bug, in one case.

    A five-slot family binds a `_nrm`-suffixed texture to `t_SpecialMap` *as
    well as* `t_Normal`.  Scanning for the first `_nrm` stops at the special
    map -- which is a different texture from a different set.
    """
    refs = [
        "textures/a_plates1_col.dds",
        "textures/arrackjijuhead_lgh.dds",
        "textures/testtechstuffdxt_nrm.dds",     # t_SpecialMap
        "textures/a_plates1_nrm.dds",            # t_Normal
        "textures/defaultspace.dds",
    ]
    slots = ["t_Color", "t_Light", "t_SpecialMap", "t_Normal", "t_Reflection"]

    assert meshview.pick_slots(refs)[1] == "textures/testtechstuffdxt_nrm.dds"
    assert meshview.pick_slots(refs, slots)[1] == "textures/a_plates1_nrm.dds"


def test_the_shader_beats_the_filename_for_the_base_colour():
    """planet.bsd9: t_Color is `demo.dds`, and a later slot is a `_col`."""
    refs = ["textures/demo.dds", "textures/p_lgh.dds", "textures/p_cloud_col.dds"]
    slots = ["t_Color", "t_Light", "t_Cloud"]

    assert meshview.pick_slots(refs)[0] == "textures/p_cloud_col.dds"
    assert meshview.pick_slots(refs, slots)[0] == "textures/demo.dds"


def test_a_generic_slot_name_falls_back_to_the_filename():
    """`tex0` means nothing: it gets `_flat`, `_lgh`, `_col` and unsuffixed
    textures in shipped data, so the name is no evidence at all."""
    refs = ["textures/b_lgh.dds"]
    assert meshview.pick_slots(refs, ["tex0"]) == meshview.pick_slots(refs)


def test_no_slot_names_is_exactly_the_old_behaviour():
    """466 effects reference a shader that is not installed."""
    refs = ["textures/a_col.dds", "textures/a_lgh.dds", "textures/a_nrm.dds"]
    assert meshview.pick_slots(refs, None) == meshview.pick_slots(refs)


def test_shader_names_shorter_than_the_texture_list_still_work():
    """zip() stops at the shorter one; the rest falls through to the suffixes."""
    refs = ["textures/x_col.dds", "textures/x_lgh.dds", "textures/x_nrm.dds"]
    base, normal = meshview.pick_slots(refs, ["t_Color"])
    assert base == "textures/x_col.dds"
    assert normal == "textures/x_nrm.dds"      # found by suffix, not by shader


# --------------------------------------------------------------------------
# geometry
# --------------------------------------------------------------------------


def _draw_call(points):
    verts = b"".join(
        struct.pack("<12f", x, y, z, 0, 0, 1, 0, 0, 0, 0, 0, 1) for x, y, z in points
    )
    return meshview.DrawCall("m[0]", "m", 0, 0, verts, b"")


def test_bounds_and_centre_come_from_the_box_not_the_origin():
    call = _draw_call([(10, 0, 0), (20, 4, 8)])
    geo = meshview.SceneGeometry("s.xml", [call], [], {})

    assert geo.bounds() == ((10.0, 0.0, 0.0), (20.0, 4.0, 8.0))
    assert geo.center() == (15.0, 2.0, 4.0)


def test_radius_is_the_box_diagonal_not_the_furthest_coordinate():
    """A far-flung blinker used to inflate the frame until the ship was a speck."""
    call = _draw_call([(-1, -1, -1), (1, 1, 1)])
    geo = meshview.SceneGeometry("s.xml", [call], [], {})

    assert math.isclose(geo.radius(), math.sqrt(3), rel_tol=1e-6)


def test_an_empty_scene_still_yields_a_usable_camera_distance():
    geo = meshview.SceneGeometry("s.xml", [], [], {})
    assert geo.radius() == 10.0
    assert geo.center() == (0.0, 0.0, 0.0)


def test_lod_count_is_the_deepest_model_in_the_scene():
    geo = meshview.SceneGeometry("s.xml", [], [], {"a.3do": 1, "b.3do": 3})
    assert geo.lod_count == 3


# --------------------------------------------------------------------------
# the non-mesh drawable layers
#
# 1,460 CGlowObject, 538 CDistortionObject, 304 CShineObject and 254
# CShieldMesh reference a .3do and carry their own EffectContainer -- every
# one of them -- and none was ever drawn.
# --------------------------------------------------------------------------


def test_a_non_mesh_drawable_is_classified_by_its_type():
    """A CGlowObject is a glow whatever the artist called the node."""
    assert meshview.classify_layer("engine", "objects/x.3do", "CGlowObject") \
        == meshview.LAYER_GLOW
    assert meshview.classify_layer("x", "y.3do", "CShieldMesh") \
        == meshview.LAYER_SHIELD
    assert meshview.classify_layer("x", "y.3do", "CDistortionObject") \
        == meshview.LAYER_DISTORTION
    assert meshview.classify_layer("x", "y.3do", "CShineObject") \
        == meshview.LAYER_SHINE


def test_a_mesh_is_still_classified_by_name_and_model():
    """Type only wins where it names a non-mesh drawable."""
    assert meshview.classify_layer("CollisionShape", "collisionshape_1.3do", "CMesh") \
        == meshview.LAYER_COLLISION
    assert meshview.classify_layer("main_", "objects/main.3do", "CMesh") \
        == meshview.LAYER_GEOMETRY
    # No type at all is the old two-argument call, unchanged.
    assert meshview.classify_layer("main_", "objects/main.3do") \
        == meshview.LAYER_GEOMETRY


def test_a_blinker_group_is_not_a_drawable_layer():
    """All 621 have no model, no effect and no children.

    A CBlinkerGroup is a point-sprite emitter -- a Texture plus a list of
    `<Blinker displacement=… vrow=… animtime=…/>`.  Drawing one means
    billboards, not triangles, so treating it as geometry would put invented
    shapes on screen.
    """
    assert "CBlinkerGroup" not in meshview.NON_MESH_LAYERS


def test_only_the_hull_is_on_by_default():
    """These layers are additive shells; drawn opaque they hide the ship."""
    assert meshview.DEFAULT_LAYERS == (meshview.LAYER_GEOMETRY,)


def test_framing_ignores_the_layers_that_are_switched_off():
    """Otherwise every model shrinks to make room for shells nobody asked for.

    The same failure the `radius` docstring warns about, arriving by a
    different route: a glow hull reaching well past the ship.
    """
    hull = _draw_call([(-1, -1, -1), (1, 1, 1)])
    glow = _draw_call([(-50, -50, -50), (50, 50, 50)])
    glow.layer = meshview.LAYER_GLOW
    geo = meshview.SceneGeometry("s.xml", [hull, glow], [], {})

    assert geo.bounds(meshview.DEFAULT_LAYERS) == ((-1.0, -1.0, -1.0), (1.0, 1.0, 1.0))
    assert geo.bounds() == ((-50.0, -50.0, -50.0), (50.0, 50.0, 50.0))
    assert geo.radius(meshview.DEFAULT_LAYERS) < geo.radius()


def test_framing_falls_back_when_the_chosen_layers_are_empty():
    """A scene made only of glow objects must still be framed, not collapsed."""
    glow = _draw_call([(-4, -4, -4), (4, 4, 4)])
    glow.layer = meshview.LAYER_GLOW
    geo = meshview.SceneGeometry("s.xml", [glow], [], {})

    assert geo.bounds(meshview.DEFAULT_LAYERS) == ((-4.0, -4.0, -4.0), (4.0, 4.0, 4.0))
    assert geo.radius(meshview.DEFAULT_LAYERS) > 0


def test_layers_are_listed_in_a_stable_reading_order():
    calls = []
    for layer in (meshview.LAYER_SHIELD, meshview.LAYER_GLOW,
                  meshview.LAYER_GEOMETRY):
        c = _draw_call([(0, 0, 0), (1, 1, 1)])
        c.layer = layer
        calls.append(c)
    geo = meshview.SceneGeometry("s.xml", calls, [], {})

    assert geo.layers() == [
        meshview.LAYER_GEOMETRY, meshview.LAYER_GLOW, meshview.LAYER_SHIELD
    ]


# --------------------------------------------------------------------------
# variant groups
# --------------------------------------------------------------------------


def _call_at(node_path, lo, hi):
    """A draw call occupying the box (lo, hi)."""
    pts = [lo, hi]
    verts = b"".join(
        struct.pack("<12f", x, y, z, 0, 0, 1, 0, 0, 0, 0, 0, 1) for x, y, z in pts
    )
    return meshview.DrawCall(
        node_path.split("|")[-1], node_path.split("|")[-1], 0, 0, verts, b"",
        node_path=node_path,
    )


def test_overlapping_numbered_siblings_are_alternatives():
    """bodys/body_0..2 are eleven upgrade levels of one ship, not three parts."""
    calls = [
        _call_at("bodys|body_0|main", (0, 0, 0), (10, 10, 10)),
        _call_at("bodys|body_1|main", (0, 0, 0), (10, 10, 10)),
        _call_at("bodys|body_2|main", (1, 1, 1), (11, 11, 11)),
    ]
    geo = meshview.SceneGeometry("s.xml", calls, [], {})

    groups = geo.groups()

    assert len(groups) == 1
    assert groups[0].name == "body"
    assert groups[0].exclusive
    assert [m[0] for m in groups[0].members] == ["body_0", "body_1", "body_2"]


def test_side_by_side_numbered_siblings_are_parts_not_alternatives():
    """CargoDock_0 and CargoDock_1 both exist; they are not a choice."""
    calls = [
        _call_at("lod0|CargoDock_0", (0, 0, 0), (5, 5, 5)),
        _call_at("lod0|CargoDock_1", (100, 0, 0), (105, 5, 5)),
    ]
    geo = meshview.SceneGeometry("s.xml", calls, [], {})

    groups = geo.groups()

    assert len(groups) == 1
    assert not groups[0].exclusive


def test_default_selection_takes_the_first_of_each_alternative():
    calls = [
        _call_at("bodys|body_0|main", (0, 0, 0), (10, 10, 10)),
        _call_at("bodys|body_1|main", (0, 0, 0), (10, 10, 10)),
    ]
    geo = meshview.SceneGeometry("s.xml", calls, [], {})

    assert geo.default_selection() == {"bodys|body": "bodys|body_0"}


def test_visible_calls_hides_the_variants_not_chosen():
    calls = [
        _call_at("bodys|body_0|main", (0, 0, 0), (10, 10, 10)),
        _call_at("bodys|body_1|main", (0, 0, 0), (10, 10, 10)),
        _call_at("cockpit", (0, 0, 0), (1, 1, 1)),          # outside every group
    ]
    geo = meshview.SceneGeometry("s.xml", calls, [], {})

    visible = geo.visible_calls({"bodys|body": "bodys|body_1"})

    paths = {c.node_path for c in visible}
    assert paths == {"bodys|body_1|main", "cockpit"}


def test_visible_calls_with_no_selection_shows_everything():
    calls = [_call_at("bodys|body_0|main", (0, 0, 0), (1, 1, 1))]
    geo = meshview.SceneGeometry("s.xml", calls, [], {})

    assert len(geo.visible_calls(None)) == 1
    assert len(geo.visible_calls({})) == 1


def test_markers_are_drawn_but_are_not_scene_content():
    """A blinker cluster is on screen and is still not a submesh.

    Reported from real use: the parts table listed the blinker groups among
    the submeshes and their generated spheres were counted in the triangle
    total, so ticking the `blinker` layer added 2,256 triangles to
    PlayerShip's 4,440 and put seven unclickable rows in the readout.
    """
    hull = _call_at("hull", (0, 0, 0), (1, 1, 1))
    marker = _call_at("blinks_0[blinkers]", (0, 0, 0), (1, 1, 1))
    marker.layer = meshview.LAYER_BLINKER
    # Real index buffers, or the triangle assertions below compare 0 with 0 and
    # would hold whether or not the marker was excluded.
    hull.indices = struct.pack("<3I", 0, 1, 2)                    # 1 triangle
    marker.indices = struct.pack("<6I", 0, 1, 2, 0, 1, 2)         # 2 triangles
    assert (hull.triangle_count, marker.triangle_count) == (1, 2)
    geo = meshview.SceneGeometry("s.xml", [hull, marker], [], {})
    layers = [meshview.LAYER_GEOMETRY, meshview.LAYER_BLINKER]

    # Drawn: the layer is on, so the viewport must still show it.
    assert len(geo.visible_calls(None, layers)) == 2
    # Not scene content: it is not a submesh and its triangles are not the
    # scene's, so neither the readout nor the total may count it.
    assert [c.node_path for c in geo.visible_calls(None, layers, markers=False)] == ["hull"]
    assert geo.triangle_count() == hull.triangle_count
    assert geo.triangle_count(markers=True) == hull.triangle_count + marker.triangle_count


def test_reachable_answers_for_a_path_the_variant_selector_switched_off():
    """What the blinker picker asks about each group.

    63 of PlayerShip's blinker groups hang off a body, wing or booster; 56 of
    them belong to a variant that is not selected, so offering them means
    offering lights that cannot be seen while they are edited.
    """
    calls = [
        _call_at("bodys|body_0|main", (0, 0, 0), (10, 10, 10)),
        _call_at("bodys|body_1|main", (0, 0, 0), (10, 10, 10)),
        _call_at("cockpit", (0, 0, 0), (1, 1, 1)),
    ]
    geo = meshview.SceneGeometry("s.xml", calls, [], {})
    selection = {"bodys|body": "bodys|body_1"}

    assert geo.rejected_paths(selection) == ["bodys|body_0"]
    assert geo.reachable("bodys|body_1|blinks_0", selection)
    assert not geo.reachable("bodys|body_0|blinks_0", selection)
    # Outside every group, and with no selection at all, everything is reachable.
    assert geo.reachable("cockpit", selection)
    assert geo.reachable("bodys|body_0|blinks_0", None)


def test_a_lone_numbered_node_is_not_a_group():
    calls = [_call_at("blinks_0", (0, 0, 0), (1, 1, 1))]
    geo = meshview.SceneGeometry("s.xml", calls, [], {})

    assert geo.groups() == []


# --------------------------------------------------------------------------
# layers
# --------------------------------------------------------------------------


def test_collision_meshes_are_their_own_layer():
    """1,456 meshes across the corpus are collision shells, invisible in game.

    Drawing one wraps a station in a grey polyhedron -- which is what HideOut
    looked like. The shader is not a usable signal (1,253 of them use plain
    phong1_1.bsd9); the node name and the model filename are.
    """
    assert meshview.classify_layer("CollisionShape", "objects/x.3do") == "collision"
    assert meshview.classify_layer("main", "objects/collisionshape14_1.3do") == "collision"
    assert meshview.classify_layer("HideOut", "objects/hideout.3do") == "geometry"


def test_layers_lists_only_what_is_present():
    calls = [
        _call_at("a", (0, 0, 0), (1, 1, 1)),
        _call_at("b", (0, 0, 0), (1, 1, 1)),
    ]
    calls[1].layer = meshview.LAYER_COLLISION
    geo = meshview.SceneGeometry("s.xml", calls, [], {})

    assert geo.layers() == ["geometry", "collision"]


def test_collision_is_off_by_default():
    calls = [
        _call_at("hull", (0, 0, 0), (1, 1, 1)),
        _call_at("shell", (0, 0, 0), (1, 1, 1)),
    ]
    calls[1].layer = meshview.LAYER_COLLISION
    geo = meshview.SceneGeometry("s.xml", calls, [], {})

    visible = geo.visible_calls(None, meshview.DEFAULT_LAYERS)

    assert [c.node_path for c in visible] == ["hull"]
    assert len(geo.visible_calls(None, None)) == 2       # None means every layer


def test_layers_and_variants_filter_together():
    calls = [
        _call_at("bodys|body_0|main", (0, 0, 0), (10, 10, 10)),
        _call_at("bodys|body_1|main", (0, 0, 0), (10, 10, 10)),
        _call_at("bodys|body_0|coll", (0, 0, 0), (10, 10, 10)),
    ]
    calls[2].layer = meshview.LAYER_COLLISION
    geo = meshview.SceneGeometry("s.xml", calls, [], {})

    visible = geo.visible_calls({"bodys|body": "bodys|body_0"}, ["geometry"])

    assert [c.node_path for c in visible] == ["bodys|body_0|main"]


# --------------------------------------------------------------------------
# variant groups keyed by identity, not by name
#
# PlayerShip has *eleven* groups called `boost`, one under each booster.  Keyed
# by name they collapsed into a single selection entry pointing into
# booster_10, so choosing booster 5 hid all eight of its nozzles -- the group
# saw a chosen path it did not contain and rejected every member it had.
# --------------------------------------------------------------------------


def _grouped_calls():
    """Two boosters, each with its own `boost` group of two.

    Every box occupies the same space, so both levels are classified as
    *alternatives* rather than parts -- which is the arrangement PlayerShip
    actually has, and the one where the name collision bites.
    """
    calls = []
    for b in (0, 1):
        for n in (0, 1):
            c = _draw_call([(0, 0, 0), (1, 1, 1)])
            c.node_path = f"boosts|booster_{b}|boost_{n}|nozzle"
            calls.append(c)
    return calls


def test_group_keys_are_unique_where_names_are_not():
    geo = meshview.SceneGeometry("s.xml", _grouped_calls(), [], {})
    boosts = [g for g in geo.groups() if g.name == "boost"]

    assert len(boosts) == 2
    assert len({g.name for g in boosts}) == 1        # the collision
    assert len({g.key for g in boosts}) == 2         # and the fix
    assert len(geo.default_selection()) >= 2


def test_choosing_a_booster_keeps_its_own_nozzle_visible():
    """The bug, stated as a rule.

    Whichever booster is chosen, exactly one of *its* nozzles is drawn.  Keyed
    by name the shared `boost` entry pointed into the other booster, so the
    group rejected every member it had and the chosen booster lost all of them.
    """
    geo = meshview.SceneGeometry("s.xml", _grouped_calls(), [], {})
    booster = next(g for g in geo.groups() if g.name == "booster")
    assert booster.exclusive

    for b in (0, 1):
        selection = dict(geo.default_selection())
        selection[booster.key] = f"boosts|booster_{b}"
        chosen = [
            c for c in geo.visible_calls(selection, None)
            if c.node_path.startswith(f"boosts|booster_{b}|")
        ]
        assert len(chosen) == 1, f"booster_{b} -> {[c.node_path for c in chosen]}"


def test_reachable_groups_drops_the_combos_that_cannot_do_anything():
    """Ten of PlayerShip's eleven `boost` combos controlled hidden nodes."""
    geo = meshview.SceneGeometry("s.xml", _grouped_calls(), [], {})
    selection = geo.default_selection()

    reachable = {g.key for g in geo.reachable_groups(selection)}
    booster_group = next(g for g in geo.groups() if g.name == "booster")
    chosen = selection[booster_group.key]
    for group in geo.groups():
        if group.name != "boost":
            continue
        assert (group.key in reachable) == group.parent_path.startswith(chosen)


# --------------------------------------------------------------------------
# blinker markers
# --------------------------------------------------------------------------


class _FakeBlinker:
    def __init__(self, position, size):
        self.position = position
        self.size = size


class _FakeGroup:
    def __init__(self, name, path, texture, blinkers):
        self.name = name
        self.path = path
        self.texture = texture
        self.blinkers = blinkers


class _FakeScene:
    def __init__(self, groups):
        self._groups = groups

    def blinker_groups(self):
        return self._groups


def test_blinker_markers_are_one_call_per_group():
    """The group is the unit the scene and the editor both work in."""
    sc = _FakeScene([
        _FakeGroup("blinks_0", "a|blinks_0", "textures/b.dds",
                   [_FakeBlinker((0, 0, 0), 0.2), _FakeBlinker((5, 0, 0), 0.2)]),
        _FakeGroup("blinks_1", "a|blinks_1", None, [_FakeBlinker((0, 9, 0), 0.2)]),
    ])
    calls = meshview.blinker_markers(sc)

    assert [c.name for c in calls] == ["blinks_0[blinkers]", "blinks_1[blinkers]"]
    assert all(c.layer == meshview.LAYER_BLINKER for c in calls)
    assert calls[0].triangle_count == 2 * calls[1].triangle_count
    assert calls[0].textures == ["textures/b.dds"]
    assert calls[1].textures == []          # no texture attribute is not a lie


def test_a_marker_sits_where_the_blinker_says():
    sc = _FakeScene([
        _FakeGroup("b", "b", None, [_FakeBlinker((10.0, -3.0, 2.0), 1.0)])
    ])
    (call,) = meshview.blinker_markers(sc, scale=1.0)
    lo, hi = call.bounds()

    for axis, want in enumerate((10.0, -3.0, 2.0)):
        assert lo[axis] <= want <= hi[axis]
        assert math.isclose((lo[axis] + hi[axis]) / 2.0, want, abs_tol=1e-4)


def test_an_empty_blinker_group_draws_nothing():
    """No marker at all beats a marker at the origin."""
    sc = _FakeScene([_FakeGroup("empty", "e", "t.dds", [])])
    assert meshview.blinker_markers(sc) == []


def test_the_highlight_marker_is_red_and_sits_on_the_blinker():
    call = meshview.blinker_highlight((4.0, -1.0, 2.0), 1.0, scale=1.0)

    assert call.layer == meshview.LAYER_BLINKER
    # Red in both the diffuse and the emissive rows, so it reads against a lit
    # hull and against black.
    assert call.material[0] > 0.8 and call.material[1] < 0.2
    assert call.material[12] > 0.8 and call.material[13] < 0.2

    lo, hi = call.bounds()
    for axis, want in enumerate((4.0, -1.0, 2.0)):
        assert math.isclose((lo[axis] + hi[axis]) / 2.0, want, abs_tol=1e-4)


def test_the_highlight_is_bigger_than_a_plain_marker():
    """Findable in a cluster of twenty without comparing colours."""
    plain = meshview.blinker_markers(
        _FakeScene([_FakeGroup("g", "g", None, [_FakeBlinker((0, 0, 0), 1.0)])])
    )[0]
    hot = meshview.blinker_highlight((0, 0, 0), 1.0)

    def extent(call):
        lo, hi = call.bounds()
        return hi[0] - lo[0]

    assert extent(hot) > extent(plain)


def test_markers_can_be_rebuilt_from_unsaved_values():
    """The editor's white spheres have to follow the table, not the file.

    Built only from the parsed scene, they stayed where the file put them while
    the red highlight moved with the edit -- which reads as a broken preview.
    """
    call = meshview.blinker_marker_call(
        "blinks_0", "a|blinks_0", "textures/b.dds",
        [((0.0, 0.0, 0.0), 1.0), ((40.0, 0.0, 0.0), 1.0)],
        scale=1.0,
    )
    lo, hi = call.bounds()
    assert lo[0] <= -1.0 and hi[0] >= 40.0
    assert call.layer == meshview.LAYER_BLINKER
    assert call.node_path == "a|blinks_0"
    assert call.textures == ["textures/b.dds"]


def test_rebuilding_an_emptied_group_yields_nothing():
    """Deleting every row must not leave a marker at the origin."""
    assert meshview.blinker_marker_call("g", "g", None, []) is None
