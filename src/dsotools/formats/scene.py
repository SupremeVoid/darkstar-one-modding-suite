"""
Ascaron ``WalhallaScene`` scene graph (``3DView/*.xml``).  v1.0

WHY THIS MODULE EXISTS
----------------------
A ``.3do`` contains geometry and nothing else -- no material, no texture, no
shader.  For a long time that made "show the model with its textures" look like
an unsolved reverse-engineering problem.  It is not.  The binding lives here, in
1,040 plain-XML scene files the game ships uncompiled:

    <Object Type=".?AVCMesh@@" Name="main_" Resrc3DO="objects/main_.3do">
      <EffectContainer Path="blender/mat_main.bsd9">
        <Material> ...17 floats... </Material>
        <Parameters><Float semantic="Bumpiness" value="+1.000000" /> ...</Parameters>
        <Textures Number="4">
          textures/playership_body_00_col.dds
          ...
        </Textures>
      </EffectContainer>
      ...one EffectContainer per submesh, in submesh order...
    </Object>

See ``specs/scene.md`` for the full format description and the measurements
behind the invariants asserted here.

ROUND-TRIP STRATEGY
-------------------
These files are hand-edited by modders and byte-exactness matters: a diff
against stock is one of the app's core features, and a serialiser that
reformats every file turns a one-line change into a 40,000-line diff.

So the ElementTree *is* the model.  The dataclass-shaped accessors below are
typed views onto live elements, not copies.  Writing re-emits each element's
``text`` and ``tail`` verbatim and reconstructs only the tags, which makes
round-trip exact by construction rather than by careful reimplementation of
Ascaron's formatting (CRLF, tab indent, ``%+f`` floats, ``<Tag />`` for empties).

Verified byte-identical on the sample corpus; ``tests/test_scene.py`` runs it
over every scene it can find.

TYPE NAMES
----------
``Type=".?AVCMesh@@"`` is a raw MSVC RTTI decorated name -- the exporter
serialised ``typeid(*obj).raw_name()``.  ``decode_type()`` turns it back into
``CMesh``.  Do not "clean these up" on write; the engine matches the raw string.
"""

from __future__ import annotations

import copy
import re
import xml.etree.ElementTree as ET
from typing import Dict, Iterator, List, Optional, Sequence, Tuple

from ..errors import ParseError, ValidationError
from . import xmldoc

VERSION = "1.0"

XML_DECL = b'<?xml version="1.0"?>\r\n'

#: Object types that own an ``EffectContainer`` but are *not* meshes bound to a
#: ``.3do`` submesh list.  A textual scan for ``<EffectContainer>`` attributes
#: these to whatever mesh happens to precede them, which silently corrupts any
#: submesh/material check.  Recorded here because it produced a 623-false-
#: positive run before it was caught.
NON_MESH_EFFECT_OWNERS = frozenset(
    {"CGlowObject", "CShineObject", "CShieldMesh", "CDistortionObject"}
)

_TYPE_RE = re.compile(r"^\.\?AV(?P<name>[A-Za-z_][A-Za-z0-9_]*)@@$")


def decode_type(raw: Optional[str]) -> Optional[str]:
    """``'.?AVCMesh@@'`` -> ``'CMesh'``.  Returns the input if it does not match."""
    if raw is None:
        return None
    m = _TYPE_RE.match(raw)
    return m.group("name") if m else raw


def encode_type(name: str) -> str:
    """``'CMesh'`` -> ``'.?AVCMesh@@'``."""
    return name if name.startswith(".?AV") else f".?AV{name}@@"


# --------------------------------------------------------------------------
# exact serialisation
# --------------------------------------------------------------------------

_ESCAPES = (("&", "&amp;"), ("<", "&lt;"), (">", "&gt;"), ('"', "&quot;"))


# The byte-exact machinery lives in :mod:`xmldoc`.  Scenes are what proved it --
# 1,187 of them round-tripping byte-identically -- and the sound database now
# shares it rather than growing a second copy of the same subtleties.  What
# stays here is genuinely scene-specific: the declaration these files carry,
# and the close-tag case repair below.

_Layout = xmldoc.Layout
_LayoutMap = xmldoc.LayoutMap
_esc_attr = xmldoc.esc_attr
_esc_text = xmldoc.esc_text
_scan_tag_end = xmldoc.scan_tag_end
_capture_layout = xmldoc.capture_layout
_write_element = xmldoc.write_element


def serialise(root: ET.Element, *, newline: str = "\r\n", trailing_newline: bool = True,
              layout: Optional[_LayoutMap] = None) -> bytes:
    """Serialise a scene root back to bytes, preserving the original layout.

    A thin wrapper over :func:`xmldoc.serialise` supplying the declaration every
    scene carries.  See that function for why ``newline`` and
    ``trailing_newline`` are parameters rather than conventions.
    """
    return xmldoc.serialise(
        root,
        decl=XML_DECL.replace(b"\r\n", newline.encode()),
        newline=newline,
        trailing_newline=trailing_newline,
        layout=layout,
        encoding="cp1252",
    )


#: Bound on the repair loop below.  Each pass fixes one tag, and stock's worst
#: file needs one; the bound only stops a pathological file spinning.
_MAX_CASE_REPAIRS = 64


def _repair_close_tag_case(data: bytes) -> Tuple[bytes, List[str]]:
    """Accept ``</object>`` closing ``<Object>``, and say which tags it fixed.

    Three of Ascaron's own scenes -- ``AsteroidsVolume05``, ``10`` and ``20`` --
    carry exactly this, one occurrence each, and the engine loads them, so its
    reader matches close tags case-insensitively and ours had better too.  This
    is deliberately the *narrowest* rule that covers the evidence: no other
    malformed ``WalhallaScene`` exists anywhere in the corpus.

    The repair only changes a tag's **case**, so it never changes its length and
    every byte offset survives it -- which is what lets :func:`_capture_layout`
    give the file back its own ``</object>`` and keep round-trip byte-exact.

    Anything that is not a case-only tag mismatch is left alone, so a genuinely
    broken file still fails to parse rather than being silently half-read.
    """
    import xml.parsers.expat
    from xml.parsers.expat import errors as expat_errors

    fixed: List[str] = []
    for _ in range(_MAX_CASE_REPAIRS):
        p = xml.parsers.expat.ParserCreate()
        open_tags: List[str] = []
        p.StartElementHandler = lambda name, attrs: open_tags.append(name)
        p.EndElementHandler = lambda name: open_tags.pop()
        try:
            p.Parse(data, True)
            return data, fixed
        except xml.parsers.expat.ExpatError as exc:
            if (
                xml.parsers.expat.ErrorString(exc.code)
                != expat_errors.XML_ERROR_TAG_MISMATCH
                or not open_tags
            ):
                return data, fixed
            # expat raises *before* the end handler runs, so the element that
            # should have been closed is still on top.
            expected = open_tags[-1]
            # expat points at the offending tag's *name*, not at its `<`, but
            # that is not contractual -- accept either and give up if neither
            # lands on a close tag.
            i = p.ErrorByteIndex
            if data[i : i + 2] == b"</":
                j = i + 2
            elif data[i - 2 : i] == b"</":
                j = i
            else:
                return data, fixed
            k = j
            while k < len(data) and data[k : k + 1] not in (b">", b" ", b"\t", b"\r", b"\n"):
                k += 1
            actual = data[j:k].decode("utf-8", "replace")
            # Case-only, and therefore length-preserving.  Anything else is a
            # different defect and is not this function's business.
            if actual.lower() != expected.lower() or len(actual) != len(expected):
                return data, fixed
            data = data[:j] + expected.encode("utf-8") + data[k:]
            fixed.append(actual)
    return data, fixed


# --------------------------------------------------------------------------
# typed views
# --------------------------------------------------------------------------


class Material:
    """The ``<Material>`` block: 17 floats, D3DMATERIAL9-shaped.

    Four RGBA-ish rows (diffuse, ambient, specular, emissive as best we can
    tell) plus a trailing scalar that behaves like specular power -- ``+200.0``
    for ``mat_main``, ``+20.0`` for ``mat_biotechanim``.  The row meanings are
    inferred from D3D9 convention and are **not** confirmed against the engine,
    so the values are exposed as a flat tuple and the named accessors are
    documented as provisional.
    """

    __slots__ = ("_el",)

    def __init__(self, el: ET.Element) -> None:
        self._el = el

    @property
    def values(self) -> Tuple[float, ...]:
        text = self._el.text or ""
        try:
            return tuple(float(t) for t in text.split())
        except ValueError as exc:
            raise ParseError(f"non-numeric value in <Material>: {exc}") from None

    @property
    def power(self) -> Optional[float]:
        """Provisional: the trailing scalar, D3D9 specular power."""
        v = self.values
        return v[-1] if len(v) == 17 else None

    def rows(self) -> List[Tuple[float, float, float, float]]:
        """Provisional: the four leading RGBA rows."""
        v = self.values
        return [tuple(v[i : i + 4]) for i in range(0, min(16, len(v)), 4)]  # type: ignore[misc]

    def set_values(self, values: Sequence[float]) -> None:
        """Rewrite the block, keeping the shipped layout exactly.

        Shipped files write ``+1.000000`` -- explicit sign, six decimals --
        four per line, tab-indented, with the trailing scalar alone on the last
        line.  The indentation is taken from the existing text rather than
        assumed, because it is nested twelve tabs deep and differs by document;
        rebuilding it from a guess would rewrite every ``<Material>`` in the
        file and drown the real change in a diff against stock.
        """
        values = list(values)
        if len(values) != 17:
            raise ValidationError(
                f"<Material> holds 17 floats in every shipped file; got {len(values)}"
            )

        text = self._el.text or ""
        lines = text.splitlines()
        # The whitespace that precedes a value line, and the run before the
        # closing tag, are what has to survive.
        indent = "\t"
        for line in lines:
            if line.strip():
                indent = line[: len(line) - len(line.lstrip())]
                break
        closing = self._el.text.rsplit("\n", 1)[-1] if "\n" in text else ""
        newline = "\r\n" if "\r\n" in text else "\n"

        rows = [values[i : i + 4] for i in range(0, 16, 4)] + [[values[16]]]
        body = newline.join(
            indent + " ".join(f"{v:+f}" for v in row) for row in rows
        )
        self._el.text = newline + body + newline + closing

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Material {len(self.values)} floats power={self.power}>"


class EffectContainer:
    """One shader + material + texture binding.  One per submesh."""

    __slots__ = ("_el",)

    def __init__(self, el: ET.Element) -> None:
        self._el = el

    @property
    def element(self) -> ET.Element:
        return self._el

    @property
    def shader(self) -> Optional[str]:
        """The ``.bsd9`` effect path, e.g. ``blender/mat_main.bsd9``."""
        return self._el.get("Path")

    @shader.setter
    def shader(self, value: str) -> None:
        self._el.set("Path", value)

    @property
    def material(self) -> Optional[Material]:
        el = self._el.find("Material")
        return Material(el) if el is not None else None

    @property
    def parameters(self) -> Dict[str, Optional[float]]:
        """``{semantic: value}``.

        A ``<Float semantic="X" />`` with no ``value`` attribute is legal and
        common -- it means "shader default" -- and maps to ``None``.  Callers
        must not assume every entry has a number.
        """
        out: Dict[str, Optional[float]] = {}
        params = self._el.find("Parameters")
        if params is None:
            return out
        for f in params.findall("Float"):
            sem = f.get("semantic")
            if sem is None:
                continue
            raw = f.get("value")
            out[sem] = float(raw) if raw is not None else None
        return out

    def set_material(self, values: Sequence[float]) -> None:
        """Rewrite this effect's ``<Material>``.  See :meth:`Material.set_values`."""
        el = self._el.find("Material")
        if el is None:
            raise ParseError("EffectContainer has no <Material> block")
        Material(el).set_values(values)

    def set_parameter(self, semantic: str, value: Optional[float]) -> None:
        params = self._el.find("Parameters")
        if params is None:
            raise ParseError("EffectContainer has no <Parameters> block")
        for f in params.findall("Float"):
            if f.get("semantic") == semantic:
                if value is None:
                    f.attrib.pop("value", None)
                else:
                    f.set("value", f"{value:+f}")
                return
        raise KeyError(semantic)

    @property
    def textures(self) -> List[str]:
        """Texture paths, in slot order.  Slot meaning is shader-defined."""
        el = self._el.find("Textures")
        if el is None or not el.text:
            return []
        return [t for t in el.text.split() if t]

    def set_texture(self, slot: int, path: str) -> None:
        """Replace one texture slot, preserving the block's exact indentation.

        The text node is rewritten token-by-token so the surrounding whitespace
        -- which is what keeps the file byte-identical everywhere else -- is
        left alone.
        """
        el = self._el.find("Textures")
        if el is None or not el.text:
            raise ParseError("EffectContainer has no <Textures> block")
        parts = re.split(r"(\s+)", el.text)
        seen = -1
        for i, tok in enumerate(parts):
            if tok and not tok.isspace():
                seen += 1
                if seen == slot:
                    parts[i] = path
                    el.text = "".join(parts)
                    return
        raise IndexError(f"texture slot {slot} out of range ({seen + 1} slots)")

    @property
    def texture_count(self) -> Optional[int]:
        """The declared ``Number`` attribute, which may disagree with reality."""
        el = self._el.find("Textures")
        if el is None:
            return None
        raw = el.get("Number")
        return int(raw) if raw is not None else None

    def __repr__(self) -> str:  # pragma: no cover
        return f"<EffectContainer {self.shader} textures={len(self.textures)}>"


class Blinker:
    """One blinking light in a :class:`BlinkerGroup`.

    ``displacement`` is four floats: a position relative to the group's node,
    then the sprite's size.  ``vrow`` selects a row in the group's texture --
    the sheet holds several lights and each blinker picks one -- and
    ``animtime`` is how long its cycle takes.
    """

    __slots__ = ("_el",)

    def __init__(self, el: ET.Element) -> None:
        self._el = el

    @property
    def element(self) -> ET.Element:
        return self._el

    def _floats(self, attr: str, count: int) -> Tuple[float, ...]:
        raw = (self._el.get(attr) or "").split()
        try:
            values = tuple(float(v) for v in raw)
        except ValueError as exc:
            raise ParseError(f"non-numeric {attr} on <Blinker>: {exc}") from None
        return values + (0.0,) * (count - len(values)) if len(values) < count else values

    @property
    def position(self) -> Tuple[float, float, float]:
        return self._floats("displacement", 4)[:3]  # type: ignore[return-value]

    @property
    def size(self) -> float:
        return self._floats("displacement", 4)[3]

    @property
    def vrow(self) -> Optional[float]:
        raw = self._el.get("vrow")
        return float(raw) if raw not in (None, "") else None

    @property
    def animtime(self) -> Optional[float]:
        raw = self._el.get("animtime")
        return float(raw) if raw not in (None, "") else None

    def set_values(self, position, size: float, vrow=None, animtime=None) -> None:
        """Rewrite this blinker, in the shipped number format.

        ``+0.000000`` -- explicit sign, six decimals, and a trailing space
        after the last component, which is what every shipped file has.
        Formatting it any other way rewrites lines nobody edited.
        """
        x, y, z = position
        self._el.set(
            "displacement",
            "".join(f"{v:+f} " for v in (x, y, z, size)),
        )
        if vrow is not None:
            self._el.set("vrow", f"{float(vrow):+f}")
        if animtime is not None:
            self._el.set("animtime", f"{float(animtime):+f}")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Blinker {self.position} size={self.size}>"


class BlinkerGroup:
    """A ``CBlinkerGroup``: a texture and a list of point sprites.

    **Not geometry.**  All 621 in the corpus have no ``Resrc3DO``, no
    ``EffectContainer`` and no child objects -- a blinker group is an emitter,
    and the engine draws each entry as a camera-facing sprite cut from
    :attr:`texture`.  That is why it is not one of ``meshview``'s drawable
    layers and why the viewport shows markers rather than a mesh.
    """

    __slots__ = ("_obj",)

    def __init__(self, obj: "SceneObject") -> None:
        self._obj = obj

    @property
    def object(self) -> "SceneObject":
        return self._obj

    @property
    def name(self) -> Optional[str]:
        return self._obj.name

    @property
    def path(self) -> str:
        return self._obj.path()

    @property
    def texture(self) -> Optional[str]:
        return self._obj.element.get("Texture")

    def set_texture(self, value: str) -> None:
        self._obj.element.set("Texture", value)

    @property
    def blinkers(self) -> List[Blinker]:
        return [Blinker(e) for e in self._obj.element.findall("Blinker")]

    def add(self, position=(0.0, 0.0, 0.0), size: float = 0.2,
            vrow: float = 0.0, animtime: float = 1.0) -> Blinker:
        """Append one, copying the indentation of the ones already there.

        Matching the surrounding whitespace is not cosmetic: an added line that
        does not is a second difference in the diff against stock, on top of
        the one the author meant.
        """
        el = self._obj.element
        existing = el.findall("Blinker")
        new = ET.SubElement(el, "Blinker")
        if existing:
            # Insert after the last one so document order is append order, and
            # inherit its tail so the indentation carries.
            el.remove(new)
            last = existing[-1]
            el.insert(list(el).index(last) + 1, new)
            new.tail = last.tail
        blinker = Blinker(new)
        blinker.set_values(position, size, vrow, animtime)
        return blinker

    def remove(self, index: int) -> None:
        existing = self._obj.element.findall("Blinker")
        if not 0 <= index < len(existing):
            raise IndexError(f"blinker {index} of {len(existing)}")
        victim = existing[index]
        # The last child's tail closes the element, so removing it would take
        # the closing tag's indentation with it.
        if index == len(existing) - 1 and index > 0:
            existing[index - 1].tail = victim.tail
        self._obj.element.remove(victim)

    def __len__(self) -> int:
        return len(self._obj.element.findall("Blinker"))

    def __repr__(self) -> str:  # pragma: no cover
        return f"<BlinkerGroup {self.name!r} {len(self)} blinkers>"


class SceneObject:
    """A node in the scene graph."""

    __slots__ = ("_el", "_parent")

    def __init__(self, el: ET.Element, parent: Optional["SceneObject"] = None) -> None:
        self._el = el
        self._parent = parent

    @property
    def element(self) -> ET.Element:
        return self._el

    @property
    def raw_type(self) -> Optional[str]:
        return self._el.get("Type")

    @property
    def type(self) -> Optional[str]:
        """Demangled type, e.g. ``CMesh``."""
        return decode_type(self._el.get("Type"))

    @property
    def name(self) -> Optional[str]:
        return self._el.get("Name")

    @property
    def flags(self) -> Optional[int]:
        raw = self._el.get("Flags")
        return int(raw) if raw is not None else None

    @property
    def model(self) -> Optional[str]:
        """``Resrc3DO``, the referenced ``.3do``.  Separator may be ``\\`` or ``/``."""
        return self._el.get("Resrc3DO")

    @model.setter
    def model(self, value: str) -> None:
        self._el.set("Resrc3DO", value)

    @property
    def is_mesh(self) -> bool:
        return self.type == "CMesh"

    @property
    def effects(self) -> List[EffectContainer]:
        """Direct-child ``EffectContainer``s only.

        Direct-child is load-bearing: ``CGlowObject`` and friends nest their own,
        and ``iter()`` would collect those too.
        """
        return [EffectContainer(e) for e in self._el.findall("EffectContainer")]

    def add_effect(self) -> EffectContainer:
        """Append an ``EffectContainer``, shaped like the last one.

        Exists because ``SCN001`` -- one ``EffectContainer`` per submesh across
        all LODs -- is otherwise unsatisfiable from inside the app.  Point a
        mesh at a model with three submeshes when the scene carries two and
        the engine binds the wrong material to the wrong surface, or none at
        all; until now the app could report that and not fix it.

        The new one is a **copy of the last existing container**, not a blank:
        a container needs at least a ``<Material>`` to be useful, and the
        shader, parameter block and texture-slot count that make sense here are
        exactly the ones the neighbouring submesh uses.  Starting from empty
        would produce a container that parses and draws nothing.

        With no container to copy, a minimal one is built -- shader unset, a
        neutral material -- because a mesh with no effects at all is a mesh
        that draws nothing either way.
        """
        existing = self._el.findall("EffectContainer")
        if existing:
            last = existing[-1]
            clone = copy.deepcopy(last)
            # Whitespace is carried in tails, so appending naively puts the new
            # element where the *closing* tag used to sit -- one level out.
            # The last child's tail is the indent before the parent's close;
            # the indent between siblings is whatever precedes the first
            # container.  Swap them: the old last child takes the sibling
            # indent, and the new last child inherits the closing one.
            before = self._indent_before(existing[0])
            clone.tail = last.tail
            if before is not None:
                last.tail = before
            self._el.append(clone)
            return EffectContainer(clone)

        el = ET.SubElement(self._el, "EffectContainer")
        material = ET.SubElement(el, "Material")
        material.text = "\n" + "+1.000000 +0.000000 " * 8 + "+0.000000\n"
        return EffectContainer(el)

    def _indent_before(self, child: ET.Element) -> Optional[str]:
        """The whitespace that precedes ``child``: its predecessor's tail.

        For the first child that is the parent's own ``text``.  Returned so an
        inserted sibling can be laid out like the ones already there instead of
        landing at whatever indent the closing tag happened to use.
        """
        previous = None
        for node in self._el:
            if node is child:
                return self._el.text if previous is None else previous.tail
            previous = node
        return None

    def remove_effect(self, index: int) -> None:
        """Drop one ``EffectContainer``.  Raises rather than silently missing.

        The removed element takes its own tail with it, so the last remaining
        container inherits the closing indent when the last one goes -- which
        is what keeps the parent's closing tag where it was.
        """
        existing = self._el.findall("EffectContainer")
        if not 0 <= index < len(existing):
            raise ParseError(
                f"this mesh has {len(existing)} EffectContainer(s); "
                f"no index {index}")
        victim = existing[index]
        if index == len(existing) - 1 and len(existing) > 1:
            existing[index - 1].tail = victim.tail
        self._el.remove(victim)

    @property
    def children(self) -> List["SceneObject"]:
        attached = self._el.find("AttachedObjects")
        if attached is None:
            return []
        return [SceneObject(e, self) for e in attached.findall("Object")]

    @property
    def parent(self) -> Optional["SceneObject"]:
        return self._parent

    def path(self) -> str:
        """``lod0|main|glow_inner``-style path, matching ``LODDesc`` entries."""
        names = []
        node: Optional[SceneObject] = self
        while node is not None:
            if node.name:
                names.append(node.name)
            node = node.parent
        return "|".join(reversed(names))

    def walk(self) -> Iterator["SceneObject"]:
        yield self
        for c in self.children:
            yield from c.walk()

    def __repr__(self) -> str:  # pragma: no cover
        return f"<SceneObject {self.type} {self.name!r}>"


class Scene:
    """A parsed ``WalhallaScene`` document."""

    def __init__(
        self,
        root: ET.Element,
        *,
        path: Optional[str] = None,
        newline: str = "\r\n",
        trailing_newline: bool = True,
        layout: Optional[_LayoutMap] = None,
        repaired_tags: Optional[List[str]] = None,
    ) -> None:
        if root.tag != "WalhallaScene":
            raise ParseError(
                f"root element is <{root.tag}>, expected <WalhallaScene>", path=path
            )
        self._root = root
        self.path = path
        #: Line ending observed in the source, restored on write.  See serialise().
        self.newline = newline
        #: Whether the source ended with a line break.  Also restored on write.
        self.trailing_newline = trailing_newline
        #: Per-element source spelling of the tags.  See :class:`_Layout`.
        self.layout: _LayoutMap = layout if layout is not None else {}
        #: Close tags whose *case* had to be corrected to read the file at all.
        #: Non-empty for three of Ascaron's own scenes.  Reported rather than
        #: silent: the file is not well-formed XML, even though it loads, and
        #: anyone validating a mod should be told which of theirs are like it.
        self.repaired_tags: List[str] = list(repaired_tags or ())

    # -- document properties -------------------------------------------------

    @property
    def version(self) -> Optional[str]:
        return self._root.get("Version")

    @property
    def date(self) -> Optional[str]:
        return self._root.get("Date")

    @property
    def time(self) -> Optional[str]:
        return self._root.get("Time")

    @property
    def root_objects(self) -> List[SceneObject]:
        return [SceneObject(e) for e in self._root.findall("Object")]

    def walk(self) -> Iterator[SceneObject]:
        for o in self.root_objects:
            yield from o.walk()

    def meshes(self) -> List[SceneObject]:
        """Every ``CMesh`` in the scene, in document order."""
        return [o for o in self.walk() if o.is_mesh]

    def blinker_groups(self) -> List[BlinkerGroup]:
        """Every ``CBlinkerGroup``, in document order.  621 across the corpus."""
        return [BlinkerGroup(o) for o in self.walk() if o.type == "CBlinkerGroup"]

    def model_references(self) -> List[Tuple[SceneObject, str]]:
        """``(object, Resrc3DO)`` for every node that names a ``.3do``.

        Includes non-mesh owners such as ``CGlowObject``, because they too
        reference geometry -- but they are *not* subject to the submesh
        invariant, so callers checking that must filter on :attr:`is_mesh`.
        """
        return [(o, o.model) for o in self.walk() if o.model]

    def texture_references(self) -> List[Tuple[SceneObject, EffectContainer, int, str]]:
        """``(object, effect, slot, path)`` for every texture in the scene."""
        out = []
        for o in self.walk():
            for eff in o.effects:
                for slot, tex in enumerate(eff.textures):
                    out.append((o, eff, slot, tex))
        return out

    def to_bytes(self) -> bytes:
        return serialise(
            self._root,
            newline=self.newline,
            trailing_newline=self.trailing_newline,
            layout=self.layout,
        )

    @property
    def element(self) -> ET.Element:
        return self._root

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Scene {self.path or '<memory>'} objects={len(list(self.walk()))}>"


def parse(data: bytes, *, path: Optional[str] = None) -> Scene:
    """Parse scene XML.

    Files are Windows-authored and land as cp1252 in practice; the declaration
    carries no ``encoding``, so a strict UTF-8 parser rejects the handful of
    scenes containing German text.  Try UTF-8 first, fall back to cp1252, which
    cannot fail.

    Everything below the decode works on **UTF-8 bytes**, and deliberately: the
    tag offsets come from expat, which counts bytes in whatever it was handed.
    Normalising first means one set of offsets for both decodes rather than two
    subtly different ones.
    """
    newline = "\r\n" if b"\r\n" in data[:4096] else "\n"
    trailing_newline = data.endswith(b"\n")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = data.decode("cp1252")
        except UnicodeDecodeError as exc:  # pragma: no cover - cp1252 total
            raise ParseError(f"cannot decode scene: {exc}", path=path) from None
        text = re.sub(r"^<\?xml[^>]*\?>", '<?xml version="1.0"?>', text, count=1)

    source = text.encode("utf-8")
    readable, repaired = _repair_close_tag_case(source)
    try:
        root = ET.fromstring(readable)
    except ET.ParseError as exc:
        raise ParseError(f"malformed scene XML: {exc}", path=path) from None

    layout = xmldoc.layout_for(root, readable, source)

    return Scene(
        root,
        path=path,
        newline=newline,
        trailing_newline=trailing_newline,
        layout=layout,
        repaired_tags=repaired,
    )


def is_scene(data: bytes) -> bool:
    """Cheap sniff: does this look like a WalhallaScene without parsing it?"""
    return b"<WalhallaScene" in data[:512]


__all__ = [
    "VERSION",
    "Scene",
    "SceneObject",
    "Blinker",
    "BlinkerGroup",
    "EffectContainer",
    "Material",
    "parse",
    "serialise",
    "is_scene",
    "decode_type",
    "encode_type",
    "NON_MESH_EFFECT_OWNERS",
]
