"""
Turning a scene into something a GPU can draw.

This is the data half of the Models tab, and it is deliberately in the library
rather than the app: it imports no Qt, so the whole "does this scene resolve to
geometry and pixels" question is answerable in a test instead of by looking at
a window.  ``tools/spike_viewport.py`` proved the shape; this is that pipeline
with the findings folded in and the shortcuts removed.

WHAT THE SPIKE GOT WRONG, AND WHY IT MATTERS HERE
-------------------------------------------------
The ``_nrm`` textures are **not** RGB normal maps.  They are DXT5nm-style: X in
alpha, Y in green, Z reconstructed.  Measured, not assumed -- read as RGB the
vectors have median length 0.345; unswizzled, reconstructed Z has median 0.994.
Feed the raw RGB to a PBR material and the ship renders black and speckled.
:func:`unswizzle_normal` is not optional.

The slot *convention* (base colour 0, normal 2) was right all along; it was the
encoding that was wrong.  ``.bsd9`` is still undecoded, so the slots remain a
convention -- if a model looks wrong, suspect this before suspecting geometry.
"""

from __future__ import annotations

import re
import struct
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from ..errors import DsoError
from ..formats import bsd9, dds, scene as scenefmt, threedo
from .. import vfs as vfsmod

VERSION = "1.0"

try:                                    # optional, behind the `image` extra
    import numpy as _np
except ImportError:                     # pragma: no cover - stdlib-only install
    _np = None

#: Slot order for ``blender/mat_main.bsd9`` (specs/scene.md 3.1).  **Fallback
#: only** since `.bsd9` was decoded: the shader names its own slots, and
#: :func:`pick_slots` asks it first.  These stay for the shaders that cannot be
#: resolved -- 466 effects reference a `.bsd9` that is not in the installation.
SLOT_BASECOLOR = 0
SLOT_LIGHTMAP = 1
SLOT_NORMAL = 2
SLOT_ENVIRONMENT = 3

#: Shader slot names that *say* what the slot is, lowercased.
#:
#: Deliberately excludes the generic ones -- ``tex0``, ``tex1``, ``Texture0``,
#: ``t_Texture0`` -- because they carry no meaning and the filename really is
#: the better evidence there.  Measured: ``tex0`` receives ``_flat`` 473 times,
#: ``_lgh`` 446, no recognised suffix 431 and ``_col`` 62, so there is nothing
#: to learn from the name.
SHADER_BASE_SLOTS = frozenset({"t_color", "t_albedo", "t_diffuse", "diffusetexture"})
SHADER_NORMAL_SLOTS = frozenset({"t_normal"})

#: pos3 + nrm3 + uv2 + tan4, interleaved, float32.
FLOATS_PER_VERTEX = 12
STRIDE = FLOATS_PER_VERTEX * 4

ATTRIBUTE_OFFSETS = {
    "position": 0,
    "normal": 12,
    "uv": 24,
    "tangent": 32,
}


def unswizzle_normal(rgba: bytes) -> bytes:
    """Rebuild a tangent-space normal map from DSO's DXT5nm-style storage.

    X lives in alpha and Y in green, with Z implied -- the standard trick that
    spends DXT5's well-interpolated alpha channel on the component that matters
    and leaves RGB carrying nothing usable.

    Uses numpy when importable, mirroring :mod:`dsotools.formats.dds`.  This is
    not a micro-optimisation: the pure-Python loop is a per-pixel pass over
    every normal map in a scene, and it accounted for **3.7 of the 6.1 seconds**
    it took to open ``PlayerShip.xml`` -- more than the DXT decoding, the model
    parsing and the vertex packing put together.
    """
    if _np is not None:
        return _unswizzle_numpy(rgba)
    return _unswizzle_python(rgba)


def _unswizzle_numpy(rgba: bytes) -> bytes:
    px = _np.frombuffer(rgba, dtype=_np.uint8).reshape(-1, 4).astype(_np.float32)
    x = px[:, 3] / 127.5 - 1.0
    y = px[:, 1] / 127.5 - 1.0
    z = _np.sqrt(_np.maximum(0.0, 1.0 - x * x - y * y))

    out = _np.empty_like(px, dtype=_np.uint8)
    out[:, 0] = px[:, 3].astype(_np.uint8)
    out[:, 1] = px[:, 1].astype(_np.uint8)
    out[:, 2] = (z * 127.5 + 127.5).astype(_np.uint8)
    out[:, 3] = 255
    return out.tobytes()


def _unswizzle_python(rgba: bytes) -> bytes:
    out = bytearray(len(rgba))
    for i in range(0, len(rgba), 4):
        x = rgba[i + 3] / 127.5 - 1.0
        y = rgba[i + 1] / 127.5 - 1.0
        z = max(0.0, 1.0 - x * x - y * y) ** 0.5
        out[i] = rgba[i + 3]
        out[i + 1] = rgba[i + 1]
        out[i + 2] = int(z * 127.5 + 127.5)
        out[i + 3] = 255
    return bytes(out)


class Texture:
    """One decoded texture, ready to upload."""

    __slots__ = ("vpath", "width", "height", "rgba", "is_normal")

    def __init__(self, vpath, width, height, rgba, is_normal=False):
        self.vpath = vpath
        self.width = width
        self.height = height
        self.rgba = rgba
        self.is_normal = is_normal

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Texture {self.vpath} {self.width}x{self.height}>"


#: Draw calls are sorted into layers so the viewport can leave some off.
#:
#: ``collision`` is the one that matters today: 1,456 meshes across the corpus
#: are named ``CollisionShape`` and bound to ``collisionshape*.3do``, and they
#: are invisible in game.  Drawing them wraps a station in a grey polyhedron --
#: which is exactly what ``HideOut.xml`` looked like.  The shader is *not* a
#: usable signal (1,253 of them use plain ``phong1_1.bsd9``); the node name and
#: the model filename are.
LAYER_GEOMETRY = "geometry"
LAYER_COLLISION = "collision"
LAYER_GLOW = "glow"
LAYER_DISTORTION = "distortion"
LAYER_SHINE = "shine"
LAYER_SHIELD = "shield"
LAYER_BLINKER = "blinker"

#: How big a blinker marker is drawn, as a multiple of the size the scene gives
#: it.  The stored size is the *sprite's* extent, and a sphere of that radius is
#: a golf ball next to the hull; this is an indicator, not a reproduction.
BLINKER_MARKER_SCALE = 0.5

#: Scene object types that are **not** ``CMesh`` yet still reference a ``.3do``
#: and carry their own ``EffectContainer`` -- so they can be drawn, and their
#: materials edited, exactly like a mesh; they just belong on their own layer.
#:
#: Measured over the whole installation: 1,460 ``CGlowObject``, 538
#: ``CDistortionObject``, 304 ``CShineObject``, 254 ``CShieldMesh`` -- and
#: **every single one** has both a model and an effect.  2,556 objects that
#: were parsed and then never drawn.
#:
#: ``CBlinkerGroup`` is deliberately absent, and that is a correction to the
#: plan rather than an oversight.  All 621 of them have no model, no effect and
#: no children: a blinker group is a *point-sprite emitter* -- a ``Texture``
#: attribute plus a list of ``<Blinker displacement=… vrow=… animtime=…/>``.
#: Drawing one means billboards, not triangles, so it is not a geometry layer,
#: and putting invented shapes on screen would be worse than leaving it out.
NON_MESH_LAYERS = {
    "CGlowObject": LAYER_GLOW,
    "CDistortionObject": LAYER_DISTORTION,
    "CShineObject": LAYER_SHINE,
    "CShieldMesh": LAYER_SHIELD,
}

#: Only the hull is on by default.  These layers are additive shells drawn over
#: the ship -- a glow hull rendered as opaque geometry simply hides it.
DEFAULT_LAYERS = (LAYER_GEOMETRY,)

#: Layers whose draw calls are **editor affordances rather than scene content**.
#:
#: A blinker is a point-sprite emitter with no model and no submesh; what gets
#: drawn is a cluster of little spheres this code generates so the lights can be
#: placed.  So it is drawable, and it is *not* a submesh: it does not belong in
#: a list of submeshes, its generated triangles are not the scene's triangles,
#: and it has no material to edit through the parts table.
#:
#: The rule already existed in the viewport, which exempts these from the
#: variant filter and the isolate button; it lives here now because the readout
#: needs the same rule and two copies of it drift.
MARKER_LAYERS = frozenset({LAYER_BLINKER})

#: Display order for the layer switches; anything unlisted sorts after.
LAYER_ORDER = (
    LAYER_GEOMETRY, LAYER_COLLISION, LAYER_GLOW, LAYER_SHINE,
    LAYER_DISTORTION, LAYER_SHIELD, LAYER_BLINKER,
)


def classify_layer(node_name: str, model_ref: str,
                   obj_type: Optional[str] = None) -> str:
    """Which layer a drawable belongs to.

    ``obj_type`` is the scene object's demangled type and wins where it names a
    non-mesh drawable: a ``CGlowObject`` is a glow whatever it is called.  For a
    ``CMesh`` the node name and the model filename are the only signal, and a
    good one -- see the note above ``LAYER_GEOMETRY``.
    """
    if obj_type in NON_MESH_LAYERS:
        return NON_MESH_LAYERS[obj_type]
    haystack = f"{node_name or ''} {model_ref or ''}".lower()
    if "collision" in haystack or "/coll" in haystack:
        return LAYER_COLLISION
    return LAYER_GEOMETRY


class DrawCall:
    """One submesh: interleaved vertices, indices, and the maps bound to it."""

    __slots__ = ("name", "node", "node_path", "lod", "index", "vertices",
                 "indices", "basecolor", "normalmap", "shader", "textures",
                 "layer", "material", "parameters", "slot_names")

    def __init__(self, name, node, lod, index, vertices, indices,
                 basecolor=None, normalmap=None, shader=None, textures=(),
                 node_path="", layer=LAYER_GEOMETRY, material=(),
                 parameters=None, slot_names=()):
        self.name = name
        self.node = node
        #: ``bodys|body_3|main|main_`` -- the scene-graph path, which is what
        #: makes a variant addressable.  See :meth:`SceneGeometry.groups`.
        self.node_path = node_path
        self.lod = lod
        self.index = index
        self.vertices = vertices
        self.indices = indices
        self.basecolor = basecolor
        self.normalmap = normalmap
        self.shader = shader
        #: Every texture reference on this submesh, in slot order.
        self.textures = list(textures)
        #: What the ``.bsd9`` calls each of those slots, positionally aligned
        #: with :attr:`textures`.  **Empty when the shader could not be read**,
        #: which is how a caller tells "the shader says this is the normal map"
        #: from "the filename ends in ``_nrm``" -- a distinction the UI makes,
        #: because only one of the two is a fact.
        self.slot_names = list(slot_names)
        self.layer = layer
        #: The 17 ``<Material>`` floats and the named ``<Parameters>``, carried
        #: so the viewport can shade with the scene's own values instead of
        #: hard-coded ones.
        self.material = tuple(material)
        self.parameters = dict(parameters or {})

    @property
    def vertex_count(self) -> int:
        return len(self.vertices) // STRIDE

    @property
    def triangle_count(self) -> int:
        return len(self.indices) // 12

    def bounds(self) -> Optional[Tuple[Tuple[float, float, float], Tuple[float, float, float]]]:
        n = self.vertex_count
        if not n:
            return None
        lo = [float("inf")] * 3
        hi = [float("-inf")] * 3
        for i in range(n):
            p = struct.unpack_from("<3f", self.vertices, i * STRIDE)
            for a in range(3):
                lo[a] = min(lo[a], p[a])
                hi[a] = max(hi[a], p[a])
        return (tuple(lo), tuple(hi))  # type: ignore[return-value]

    def __repr__(self) -> str:  # pragma: no cover
        return f"<DrawCall {self.name} {self.triangle_count} tris>"


#: Two numbered siblings count as alternatives rather than as separate parts
#: when their bounding boxes overlap by at least this fraction of the smaller
#: box.  See :meth:`SceneGeometry.groups`.
OVERLAP_THRESHOLD = 0.35

_TRAILING_NUMBER = re.compile(r"^(?P<stem>.*?)(?P<number>\d+)$")


class NodeGroup:
    """A set of numbered sibling nodes, and whether they are alternatives.

    ``exclusive`` is the interesting bit.  ``bodys/body_0..body_10`` are eleven
    *alternatives* -- one ship, eleven upgrade levels, all occupying the same
    space -- while ``lod0/CargoDock_0`` and ``CargoDock_1`` are two docks that
    both exist, side by side.  Both look identical to a name-matching rule, so
    the distinction is made from the geometry: alternatives overlap, parts do
    not.
    """

    __slots__ = ("name", "parent_path", "members", "exclusive")

    def __init__(self, name, parent_path, members, exclusive):
        self.name = name
        self.parent_path = parent_path
        #: ``[(label, node_path)]`` in document order.
        self.members = members
        self.exclusive = exclusive

    @property
    def key(self) -> str:
        """What a selection dict is keyed by.  **Not the name.**

        ``PlayerShip.xml`` has *eleven* groups called ``boost`` -- one under
        each of ``booster_0..booster_10`` -- so keying a selection by name
        collapsed all eleven into one entry.  The surviving value pointed into
        ``booster_10``, which is not a member of any other group, so choosing
        booster 5 hid **all eight** of its boost nozzles: the group saw a
        chosen path it did not contain and rejected every member it had.

        The parent path plus the name is unique by construction -- a group is
        the numbered siblings sharing one stem under one parent.
        """
        return f"{self.parent_path}|{self.name}" if self.parent_path else self.name

    def __repr__(self) -> str:  # pragma: no cover
        kind = "one-of" if self.exclusive else "all-of"
        return f"<NodeGroup {self.key} {kind} {len(self.members)}>"


def _boxes_overlap(a, b) -> float:
    """Intersection volume as a fraction of the smaller box."""
    if a is None or b is None:
        return 0.0
    lo = [max(a[0][i], b[0][i]) for i in range(3)]
    hi = [min(a[1][i], b[1][i]) for i in range(3)]
    dims = [max(0.0, hi[i] - lo[i]) for i in range(3)]
    inter = dims[0] * dims[1] * dims[2]
    if inter <= 0:
        return 0.0

    def volume(box):
        return max(
            1e-9,
            (box[1][0] - box[0][0]) * (box[1][1] - box[0][1]) * (box[1][2] - box[0][2]),
        )

    return inter / min(volume(a), volume(b))


class SceneGeometry:
    """Every draw call for one scene, plus what could not be built."""

    def __init__(self, path, calls, skipped, lod_counts):
        self.path = path
        self.calls: List[DrawCall] = calls
        #: ``[(reference, reason)]`` -- meshes that produced no geometry.
        self.skipped: List[Tuple[str, str]] = skipped
        #: ``{model vpath: number of LODs}``, so a selector knows its range.
        self.lod_counts: Dict[str, int] = lod_counts
        #: :meth:`groups` is derived from ``calls`` alone and ``calls`` does not
        #: change after construction, so it is computed once.  It is 51 ms on
        #: PlayerShip -- nothing on its own, and 3.3 seconds when something asks
        #: 63 times, which is exactly what the blinker picker started doing.
        self._groups_cache: Optional[List["NodeGroup"]] = None

    @property
    def lod_count(self) -> int:
        return max(self.lod_counts.values(), default=0)

    def bounds(self, layers: Optional[Sequence[str]] = None):
        """``(min, max)`` over the draw calls, or ``None`` if there are none.

        ``layers`` restricts which calls count; ``None`` means all of them.
        **Framing has to follow what is shown.**  Glow and shield hulls extend
        well past the ship, so once they became drawable, including them here
        would have shrunk every model on screen even with those layers switched
        off -- the same failure the ``radius`` docstring already warns about,
        arriving by a different route.

        If the restriction matches nothing, the unrestricted bounds are used
        instead: a scene made entirely of glow objects should still be framed,
        not collapsed to the origin.
        """
        def box_over(calls):
            lo = [float("inf")] * 3
            hi = [float("-inf")] * 3
            found = False
            for call in calls:
                box = call.bounds()
                if box is None:
                    continue
                found = True
                for a in range(3):
                    lo[a] = min(lo[a], box[0][a])
                    hi[a] = max(hi[a], box[1][a])
            return (tuple(lo), tuple(hi)) if found else None

        if layers is not None:
            allowed = set(layers)
            restricted = box_over(c for c in self.calls if c.layer in allowed)
            if restricted is not None:
                return restricted
        return box_over(self.calls)

    def center(self, layers: Optional[Sequence[str]] = None) -> Tuple[float, float, float]:
        box = self.bounds(layers)
        if box is None:
            return (0.0, 0.0, 0.0)
        return tuple((box[0][a] + box[1][a]) / 2.0 for a in range(3))  # type: ignore[return-value]

    def radius(self, layers: Optional[Sequence[str]] = None) -> float:
        """Half the bounding box's diagonal -- what a camera should frame.

        Measured from the box rather than from the largest absolute coordinate.
        The latter is what the spike used, and on a full scene it is wrong: one
        far-flung node (a blinker, an engine glow) inflates it and the ship ends
        up a speck in the middle of the viewport.
        """
        box = self.bounds(layers)
        if box is None:
            return 10.0
        half = [(box[1][a] - box[0][a]) / 2.0 for a in range(3)]
        return (sum(h * h for h in half) ** 0.5) or 10.0

    def triangle_count(self, *, markers: bool = False) -> int:
        """The scene's triangles.

        Marker layers are **excluded by default**: they are geometry this code
        generated to show where the blinkers are, not geometry the game draws,
        and a scene total that moves when you tick a checkbox is not a total.
        """
        return sum(c.triangle_count for c in self.calls
                   if markers or c.layer not in MARKER_LAYERS)

    def bounds_of(self, node_path: str):
        """Union bounds of every call at or under ``node_path``."""
        lo = [float("inf")] * 3
        hi = [float("-inf")] * 3
        found = False
        for call in self.calls:
            if not _under(call.node_path, node_path):
                continue
            box = call.bounds()
            if box is None:
                continue
            found = True
            for a in range(3):
                lo[a] = min(lo[a], box[0][a])
                hi[a] = max(hi[a], box[1][a])
        return (tuple(lo), tuple(hi)) if found else None

    def groups(self) -> List[NodeGroup]:
        """Numbered sibling sets in the scene, classified.

        This is what makes ``PlayerShip.xml`` legible.  It contains eleven
        bodies, eleven wings and a set of boosters, and drawing them all at
        once -- which is what happens without this -- stacks eleven ships in
        the same place.

        Computed once per scene: it walks every call, bounds-checks every
        numbered sibling pair, and nothing it reads changes afterwards.
        """
        if self._groups_cache is not None:
            return self._groups_cache

        # Every node path that carries geometry, plus its ancestors.
        by_parent: Dict[str, List[str]] = {}
        seen = set()
        for call in self.calls:
            parts = call.node_path.split("|") if call.node_path else []
            for depth in range(1, len(parts) + 1):
                path = "|".join(parts[:depth])
                if path in seen:
                    continue
                seen.add(path)
                parent = "|".join(parts[: depth - 1])
                by_parent.setdefault(parent, []).append(path)

        out: List[NodeGroup] = []
        for parent, children in by_parent.items():
            stems: Dict[str, List[tuple]] = {}
            for path in children:
                leaf = path.split("|")[-1]
                match = _TRAILING_NUMBER.match(leaf)
                if not match:
                    continue
                stem = match.group("stem")
                stems.setdefault(stem, []).append(
                    (int(match.group("number")), leaf, path)
                )

            for stem, members in stems.items():
                if len(members) < 2:
                    continue
                members.sort()
                boxes = [self.bounds_of(p) for _n, _leaf, p in members]
                # Compare consecutive members: alternatives sit on top of one
                # another, real parts sit beside one another.
                overlaps = [
                    _boxes_overlap(boxes[i], boxes[i + 1])
                    for i in range(len(boxes) - 1)
                ]
                exclusive = bool(overlaps) and (
                    sum(overlaps) / len(overlaps) >= OVERLAP_THRESHOLD
                )
                out.append(
                    NodeGroup(
                        name=stem.rstrip("_") or (parent.split("|")[-1] or "group"),
                        parent_path=parent,
                        members=[(leaf, path) for _n, leaf, path in members],
                        exclusive=exclusive,
                    )
                )
        out.sort(key=lambda g: (g.parent_path.count("|"), g.name.lower()))
        self._groups_cache = out
        return out

    def default_selection(self) -> Dict[str, str]:
        """``{group name: chosen member path}`` -- the first of each alternative.

        Showing every variant at once is never what anyone wants; the first one
        is a defensible default and the selector makes the rest one click away.
        """
        return {
            g.key: g.members[0][1] for g in self.groups() if g.exclusive
        }

    def rejected_paths(self, selection: Optional[Dict[str, str]] = None) -> List[str]:
        """Node paths the variant selection has switched off.

        Everything at or below one of these is not on screen, whatever else is
        set.  Named once because three things need it: which calls to draw,
        which variant combos can do anything, and -- since the blinker editor
        grew a group picker -- which blinker groups are worth offering.
        """
        if not selection:
            return []
        rejected: List[str] = []
        for group in self.groups():
            chosen = selection.get(group.key)
            if not group.exclusive or chosen is None:
                continue
            rejected.extend(p for _label, p in group.members if p != chosen)
        return rejected

    def reachable(self, node_path: str,
                  selection: Optional[Dict[str, str]] = None) -> bool:
        """Could anything at ``node_path`` be on screen under ``selection``?"""
        return self.reachable_filter(selection)(node_path)

    def reachable_filter(self, selection: Optional[Dict[str, str]] = None):
        """A predicate over node paths, with the rejected set computed **once**.

        For asking about many paths, which is the only way anything asks: the
        blinker picker tests all 63 of ``PlayerShip``'s groups every time a
        variant changes.  Calling :meth:`reachable` in a loop re-derived the
        whole rejected set per path and turned that into 3.3 seconds of a combo
        box refusing to drop down.
        """
        rejected = self.rejected_paths(selection)
        if not rejected:
            return lambda _path: True
        return lambda path: not any(_under(path, r) for r in rejected)

    def reachable_groups(self, selection: Optional[Dict[str, str]] = None):
        """The groups whose combo is worth showing, given ``selection``.

        A group nested under a member that is *not* currently chosen cannot be
        reached: whatever it is set to, none of its nodes will be drawn.
        PlayerShip has eleven ``boost`` groups, one per booster, so ten of the
        eleven combos were controls that could not do anything -- which is
        exactly how they were reported.
        """
        hidden = self.rejected_paths(selection)
        return [
            g for g in self.groups()
            if not any(_under(g.parent_path, h) for h in hidden)
        ]

    def layers(self) -> List[str]:
        """Which layers this scene actually contains, in a stable order."""
        present = {c.layer for c in self.calls}
        return [n for n in LAYER_ORDER if n in present] + sorted(
            present - set(LAYER_ORDER)
        )

    def visible_calls(self, selection: Optional[Dict[str, str]] = None,
                      layers: Optional[Sequence[str]] = None,
                      *, markers: bool = True):
        """The calls to draw under ``selection`` and ``layers``.

        A call is hidden when it sits under a member of an exclusive group that
        is not the chosen one, or when its layer is switched off.  Calls outside
        every group are always drawn.  ``layers=None`` means every layer.

        ``markers=False`` drops the generated marker layers (:data:`MARKER_LAYERS`)
        and answers a different question: not "what is on screen" but "what of
        the scene is on screen".  That is what a submesh list and a triangle
        count want -- PlayerShip's seven blinker clusters are 2,256 triangles
        this project generated, and counting them as the ship's is simply wrong.
        """
        calls = list(self.calls)
        if not markers:
            calls = [c for c in calls if c.layer not in MARKER_LAYERS]
        if layers is not None:
            allowed = set(layers)
            calls = [c for c in calls if c.layer in allowed]

        rejected = self.rejected_paths(selection)
        if rejected:
            calls = [
                c for c in calls
                if not any(_under(c.node_path, r) for r in rejected)
            ]
        return calls

    def __repr__(self) -> str:  # pragma: no cover
        return f"<SceneGeometry {self.path} {len(self.calls)} calls>"


def _under(path: str, ancestor: str) -> bool:
    """Is ``path`` at or below ``ancestor`` in the ``a|b|c`` node hierarchy?"""
    if not ancestor:
        return True
    return path == ancestor or path.startswith(ancestor + "|")


def pick_slots(
    refs: List[str], slot_names: Optional[Sequence[str]] = None
) -> Tuple[Optional[str], Optional[str]]:
    """Choose the base-colour and normal textures from a submesh's slot list.

    **The shader first, the filename second, position last.**

    ``slot_names`` is what the ``.bsd9`` calls each slot, positionally aligned
    with ``refs`` (:mod:`dsotools.formats.bsd9`).  Where the shader names a slot
    it is simply right, and it is the only source here that is not an inference.

    Falling back matters as much as asking.  Only some names *mean* anything --
    ``t_Color`` does, ``tex0`` does not -- and 466 effects reference a shader
    that is not in the installation at all.  For those the suffix convention is
    still the best evidence available, so it stays exactly as it was.

    What the shader changed, measured over the whole installation:

    * **normal maps: 841 of 5,452 were wrong.**  A five-slot family binds a
      ``_nrm``-suffixed texture to ``t_SpecialMap`` *as well as* ``t_Normal``,
      and scanning for the first ``_nrm`` stopped at the special map.
    * **base colour: 8 of 12,728 were wrong** -- ``planet.bsd9``, whose
      ``t_Color`` is ``demo.dds`` while a later slot is
      ``defaultplanet_cloud_col.dds``.  The heuristic took the cloud layer.

    The old reasoning below still holds for the fallback, and is why the
    fallback is by name before position: ``mat_biotechanim`` has exactly one
    slot and it is fed a light map, so using slot 0 blindly painted 28 of
    PlayerShip's submeshes with a lightmap as albedo.
    """
    base = normal = None

    if slot_names:
        for name, ref in zip(slot_names, refs):
            low = name.lower()
            if base is None and low in SHADER_BASE_SLOTS:
                base = ref
            elif normal is None and low in SHADER_NORMAL_SLOTS:
                normal = ref
        if base is not None and normal is not None:
            return base, normal

    for ref in refs:
        low = ref.lower()
        if base is None and "_col" in low:
            base = ref
        elif normal is None and "_nrm" in low:
            normal = ref

    if base is None and len(refs) > SLOT_BASECOLOR:
        candidate = refs[SLOT_BASECOLOR]
        # A lone lightmap is better than nothing to look at, but never pretend
        # a normal map is albedo.
        if "_nrm" not in candidate.lower():
            base = candidate
    if normal is None and len(refs) > SLOT_NORMAL:
        candidate = refs[SLOT_NORMAL]
        if "_nrm" in candidate.lower() or len(refs) > SLOT_ENVIRONMENT:
            normal = candidate
    return base, normal


def shader_slots(vfs, ref, scene_path, cache) -> Optional[List[str]]:
    """The ``.bsd9``'s own names for its texture slots, or ``None``.

    ``None`` covers three different situations and none of them is an error:
    the effect names no shader, the shader is not in this installation (466
    effects reference one that is not), or it is one of the two files in a
    different container.  Every one falls back to the filename convention.
    """
    if not ref:
        return None
    key = ("shader", ref.lower(), scene_path.rsplit("/", 1)[0].lower())
    if key in cache:
        return cache[key]
    out = None
    entry = vfs.resolve_reference(ref, scene_path=scene_path)
    if entry is not None:
        try:
            out = bsd9.parse(entry.read(), path=entry.vpath).texture_slots
        except DsoError:
            out = None
    cache[key] = out
    return out


def _decode_texture(vfs, ref, scene_path, cache, *, normal=False, mip=0):
    """Decode one texture reference, or ``None`` if it cannot be drawn."""
    if not ref:
        return None
    key = (ref.lower(), normal, mip)
    if key in cache:
        return cache[key]

    entry = vfs.resolve_reference(ref, scene_path=scene_path)
    out = None
    if entry is not None and entry.vpath.lower().endswith(".dds"):
        try:
            img = dds.parse(entry.read(), path=entry.vpath)
            level = min(mip, len(img.levels) - 1) if img.levels else 0
            surf = img.surface(max(0, level))
            rgba = unswizzle_normal(surf.rgba) if normal else surf.rgba
            out = Texture(entry.vpath, surf.width, surf.height, rgba, normal)
        except DsoError:
            out = None
    cache[key] = out
    return out


#: A marker's mesh resolution.  Deliberately coarse: a scene can hold hundreds
#: of blinkers and these are dots on screen, not subjects.
_MARKER_SEGMENTS = 6
_MARKER_RINGS = 4

#: Fully white and fully emissive, so a marker reads the same against a dark
#: hull and a bright one and does not pretend to be lit geometry.
#: Diffuse rows 0-3, emissive rows 12-15, in the D3DMATERIAL9 order the rest of
#: the app uses.
_MARKER_MATERIAL = (
    1.0, 1.0, 1.0, 1.0,
    0.0, 0.0, 0.0, 0.0,
    0.0, 0.0, 0.0, 0.0,
    1.0, 1.0, 1.0, 1.0,
    0.0,
)


def _sphere(cx: float, cy: float, cz: float, r: float, base: int):
    """A small UV sphere at a point, in the interleaved vertex format.

    Returns ``(vertex_bytes, index_list)``; ``base`` is the index this sphere's
    vertices start at, so several can be packed into one draw call.
    """
    import math

    verts = bytearray()
    positions = []
    for ring in range(_MARKER_RINGS + 1):
        phi = math.pi * ring / _MARKER_RINGS
        for seg in range(_MARKER_SEGMENTS):
            theta = 2.0 * math.pi * seg / _MARKER_SEGMENTS
            nx = math.sin(phi) * math.cos(theta)
            ny = math.cos(phi)
            nz = math.sin(phi) * math.sin(theta)
            positions.append((cx + nx * r, cy + ny * r, cz + nz * r, nx, ny, nz))
    for px, py, pz, nx, ny, nz in positions:
        verts += struct.pack("<12f", px, py, pz, nx, ny, nz,
                             0.0, 0.0, 1.0, 0.0, 0.0, 1.0)

    indices: List[int] = []
    for ring in range(_MARKER_RINGS):
        for seg in range(_MARKER_SEGMENTS):
            a = base + ring * _MARKER_SEGMENTS + seg
            b = base + ring * _MARKER_SEGMENTS + (seg + 1) % _MARKER_SEGMENTS
            c = a + _MARKER_SEGMENTS
            d = b + _MARKER_SEGMENTS
            indices += [a, c, b, b, c, d]
    return bytes(verts), indices


#: Red, and fully emissive so it reads against any hull.
_HIGHLIGHT_MATERIAL = (
    1.0, 0.1, 0.1, 1.0,
    0.0, 0.0, 0.0, 0.0,
    0.0, 0.0, 0.0, 0.0,
    1.0, 0.05, 0.05, 1.0,
    0.0,
)


def blinker_highlight(position, size: float,
                      *, scale: float = BLINKER_MARKER_SCALE * 1.6) -> DrawCall:
    """One red marker, for the blinker currently being edited.

    A little larger than the white ones so it is findable in a cluster of
    twenty without hunting for a colour difference.
    """
    x, y, z = position
    verts, indices = _sphere(x, y, z, max(1e-4, size * scale), 0)
    return DrawCall(
        name="selected blinker",
        node="selected blinker",
        node_path="",
        lod=0,
        index=0,
        vertices=verts,
        indices=b"".join(struct.pack("<I", i) for i in indices),
        layer=LAYER_BLINKER,
        material=_HIGHLIGHT_MATERIAL,
        parameters={},
    )


def blinker_markers(sc, *, scale: float = BLINKER_MARKER_SCALE) -> List[DrawCall]:
    """One draw call per ``CBlinkerGroup``, as a cluster of small spheres.

    A blinker group carries **no geometry** -- it is a texture plus a list of
    point sprites -- so there is nothing to draw faithfully without a billboard
    renderer.  Markers are the honest alternative: they say *where* the lights
    are, which is what someone editing them needs, without pretending to show
    what they look like in game.

    One call per group rather than per blinker, so isolating a row in the parts
    list isolates a whole group -- which is the unit the scene and the editor
    both work in.
    """
    out: List[DrawCall] = []
    for group in sc.blinker_groups():
        call = blinker_marker_call(
            group.name, group.path, group.texture,
            [(b.position, b.size) for b in group.blinkers],
            scale=scale,
        )
        if call is not None:
            out.append(call)
    return out


def blinker_marker_call(name, node_path, texture, blinkers,
                        *, scale: float = BLINKER_MARKER_SCALE):
    """One group's markers, from plain ``[(position, size)]``.

    Takes data rather than a parsed group so the editor can rebuild the markers
    from *unsaved* table values -- without this the white spheres stayed where
    the file put them while the red one followed the edit, which reads as the
    preview being broken.

    ``None`` when the group has no blinkers: no marker at all beats a marker at
    the origin.
    """
    verts = bytearray()
    indices: List[int] = []
    count = 0
    for position, size in blinkers:
        x, y, z = position
        vb, ib = _sphere(x, y, z, max(1e-4, size * scale), count)
        verts += vb
        indices += ib
        count += (_MARKER_RINGS + 1) * _MARKER_SEGMENTS
    if not count:
        return None
    return DrawCall(
        name=f"{name or '?'}[blinkers]",
        node=name or "?",
        node_path=node_path,
        lod=0,
        index=0,
        vertices=bytes(verts),
        indices=b"".join(struct.pack("<I", i) for i in indices),
        textures=[texture] if texture else [],
        slot_names=["blinker sheet"] if texture else (),
        layer=LAYER_BLINKER,
        material=_MARKER_MATERIAL,
        parameters={},
    )


def build_scene_geometry(
    vfs: "vfsmod.Vfs",
    scene_path: str,
    *,
    lod: int = 0,
    limit: Optional[int] = None,
    mip: int = 0,
    with_textures: bool = True,
    progress: Optional[Callable[[int, int, str], None]] = None,
    texture_cache: Optional[Dict[tuple, Optional["Texture"]]] = None,
) -> SceneGeometry:
    """Resolve a scene to draw calls.

    ``lod`` selects the level of detail; a model with fewer levels than asked
    for falls back to its last one rather than disappearing, because a scene
    mixes models of different LOD depth and dropping the shallow ones would
    quietly delete parts of the ship.

    ``mip`` trades texture memory for fidelity: a 1024x1024 base level is 4 MB
    of RGBA per texture and a scene can bind dozens.

    ``texture_cache`` lets the caller keep decoded textures **across builds**.
    A scene is rebuilt on every LOD change and after every save, replace or
    reset -- and decoding PlayerShip's 69 distinct textures is most of the time
    that takes.  The caller owns it because only the caller knows when the bytes
    behind a path may have changed; ``Session`` clears it on the same signal
    that clears the preview cache.
    """
    raw = vfs.read(scene_path)
    sc = scenefmt.parse(raw, path=scene_path)
    # Meshes *and* the non-mesh drawables -- glow, shine, distortion, shield.
    # They are built identically: each has a `.3do` and its own effects, and the
    # only difference is the layer they land on, so they go through one loop
    # rather than a near-duplicate of it.  Document order throughout, because
    # `node_path` ordering is what variant grouping reads.
    meshes = [
        o for o in sc.walk()
        if o.is_mesh or (o.type in NON_MESH_LAYERS and o.model)
    ]

    calls: List[DrawCall] = []
    skipped: List[Tuple[str, str]] = []
    lod_counts: Dict[str, int] = {}
    cache: Dict[tuple, Optional[Texture]] = (
        {} if texture_cache is None else texture_cache
    )

    for mesh_no, mesh in enumerate(meshes):
        if limit and len(calls) >= limit:
            break
        if progress:
            progress(mesh_no, len(meshes), mesh.model or "?")

        ref = mesh.model
        entry = vfs.resolve_reference(ref, scene_path=scene_path) if ref else None
        if entry is None:
            skipped.append((ref or "(no Resrc3DO)", "does not resolve"))
            continue
        try:
            model = threedo.parse(entry.read())
        except DsoError as exc:
            skipped.append((ref, str(exc)))
            continue
        if not model.lods:
            skipped.append((ref, "no LODs"))
            continue

        lod_counts[entry.vpath] = len(model.lods)
        level = model.lods[min(lod, len(model.lods) - 1)]
        effects = mesh.effects

        for i, sub in enumerate(level.submeshes):
            # SCN001: one EffectContainer per submesh across ALL LODs, so
            # submesh i of any LOD takes effect i.  Verified 9,557/9,559.
            eff = effects[i] if i < len(effects) else (effects[0] if effects else None)
            refs = eff.textures if eff else []

            vbuf = bytearray()
            lo, hi = sub.vert_start, sub.vert_start + sub.vert_count
            for v in level.vertices[lo:hi]:
                px, py, pz = v.position
                nx, ny, nz = v.normal
                u, vv = v.uv
                t = v.tangent or (0.0, 0.0, 0.0, 1.0)
                vbuf += struct.pack("<12f", px, py, pz, nx, ny, nz, u, vv,
                                    t[0], t[1], t[2], t[3])

            ibuf = bytearray()
            start = sub.face_start * 3
            for idx in level.indices[start : start + sub.face_count * 3]:
                # Rebase onto this submesh's own vertex slice.
                ibuf += struct.pack("<I", max(0, idx - sub.vert_start))

            base = norm = None
            names = shader_slots(vfs, eff.shader if eff else None,
                                 scene_path, cache)
            if with_textures:
                base_ref, normal_ref = pick_slots(refs, names)
                base = _decode_texture(vfs, base_ref, scene_path, cache, mip=mip)
                norm = _decode_texture(vfs, normal_ref, scene_path, cache,
                                       normal=True, mip=mip)

            calls.append(
                DrawCall(
                    name=f"{mesh.name or '?'}[{i}]",
                    node=mesh.name or "?",
                    node_path=mesh.path(),
                    lod=lod,
                    index=i,
                    vertices=bytes(vbuf),
                    indices=bytes(ibuf),
                    basecolor=base,
                    normalmap=norm,
                    shader=(eff.shader if eff else None),
                    textures=refs,
                    slot_names=names or (),
                    layer=classify_layer(mesh.name, ref, mesh.type),
                    material=(eff.material.values if (eff and eff.material) else ()),
                    parameters=(eff.parameters if eff else {}),
                )
            )

    # Blinkers last: they are markers rather than scene geometry, and appending
    # keeps every mesh's index stable for anything matching on position.
    calls.extend(blinker_markers(sc))

    return SceneGeometry(scene_path, calls, skipped, lod_counts)


__all__ = [
    "VERSION",
    "DrawCall",
    "SceneGeometry",
    "Texture",
    "build_scene_geometry",
    "classify_layer",
    "DEFAULT_LAYERS",
    "LAYER_ORDER",
    "LAYER_COLLISION",
    "LAYER_GEOMETRY",
    "LAYER_GLOW",
    "LAYER_SHINE",
    "LAYER_DISTORTION",
    "LAYER_SHIELD",
    "LAYER_BLINKER",
    "blinker_markers",
    "NON_MESH_LAYERS",
    "NodeGroup",
    "unswizzle_normal",
    "FLOATS_PER_VERTEX",
    "STRIDE",
    "ATTRIBUTE_OFFSETS",
    "SLOT_BASECOLOR",
    "SLOT_NORMAL",
]
