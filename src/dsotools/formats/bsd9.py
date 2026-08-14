"""
Ascaron ``.bsd9`` shader/effect container (``blender/*.bsd9``).  v1.0

WHAT THIS BUYS
--------------
The scene format binds textures to a submesh *positionally* --
``<Textures Number="4">`` is just four paths in a row -- and says nothing about
what each position means.  For a long time the app inferred that from the
``_col`` / ``_lgh`` / ``_nrm`` filename suffixes, and said so on screen because
it was a convention rather than a fact.

It is a fact now: **the shader names its own texture slots.**

    mat_main.bsd9        t_Color, t_Light, t_Normal, t_Reflection
    mat_main_2.bsd9      t_Color, t_Light
    mat_biotechanim.bsd9 tex0
    phong1_1.bsd9        (none)

Measured over the whole installation: for **15,978 of 15,978** effects the
number of textures a scene binds equals the number of slots its shader
declares.  Not 15,977 -- every one.  No scene binds more than the shader takes,
and none binds fewer, which is what makes the pairing positional and total.

WHERE THE SUFFIX HEURISTIC WAS WRONG
------------------------------------
It was right about base colour almost always (12,720 of 12,728) and wrong about
normal maps far more often than anyone had reason to think: **841 of 5,452**.

The cause is a five-slot shader family --
``t_Color, t_Light, t_SpecialMap, t_Normal, t_Reflection`` -- where
``t_SpecialMap`` is *also* fed a ``_nrm``-suffixed texture.  Scanning for the
first ``_nrm`` finds the special map and stops, so those submeshes were shaded
with the wrong normals.  The shader says which slot is which and does not care
what the file is called.

The eight base-colour cases are ``planet.bsd9`` / ``planet_1.bsd9``, whose
``t_Color`` is ``demo.dds`` -- no suffix at all -- while a *later* slot is
``defaultplanet_cloud_col.dds``.  The heuristic took the cloud layer for the
planet.

LAYOUT
------
Little-endian throughout.  Chunk tags are stored **reversed**, so the file's
``LRAV`` is ``VARL``; the magic is the same trick, and ``XF  90.1`` read as two
reversed four-byte groups is ``  FX`` ``1.09``.

    0x00  char[8]   "XF  90.1"
    0x08  uint32    version (1000 is most of them; 160 .. 120000 seen)
    0x0c  uint32    n_tex -- how many texture slots this shader takes
    0x10  uint32[]  n_tex string indices, one per slot, in slot order
          ...       zero padding
    0x30  uint32    n_str
          ...       n_str strings (see below)
          uint32    blob_len
          byte[]    blob_len bytes -- the compiled effect
          ...       chunk list: reversed tag, uint32 size (header included)
          uint32    terminator

Strings are ``uint32 length``, the bytes, a NUL, then padding to a four-byte
boundary -- i.e. the stored field is always ``align(length + 1, 4)`` bytes.
The header's length **excludes** the NUL; the blob's variable table writes the
same shape with a length that **includes** it.  Both are "round the
NUL-terminated byte count up to four"; only the number written differs.

The index array at 0x10 is ``0, 1, 2, ...`` in all 230 readable files, so the
slots are also just "the first ``n_tex`` strings".  It is read as indices
anyway, because that is what the layout expresses and the two readings cannot
be told apart from shipped data alone.

THE BLOB IS A D3DX9 COMPILED EFFECT
-----------------------------------
Its first dword is ``0xFEFF0901`` -- the Microsoft **D3DX9 effect** tag.  So the
inner format is not Ascaron's at all; it is what ``D3DXCreateEffect`` produced,
and the layout below follows Wine's ``d3dx9_36`` implementation of it.

    +0   uint32   0xFEFF0901
    +4   uint32   offset to the effect header, relative to +8

At that header: ``parameter_count``, ``technique_count``, one unknown dword,
``object_count``.  Then one 16-byte record per parameter -- typedef offset,
value offset, flags, annotation count -- each followed by its annotations, which
are **2 dwords each, not 4** (getting that wrong desynchronises the walk a few
parameters in, and it is the one trap in here).

The typedef at ``typedef offset`` is ``type``, ``class``, name offset, semantic
offset, ``element_count``, then the dimensions -- and the order of those two
depends on the class: **VECTOR is columns-then-rows, SCALAR and the MATRIX
classes are rows-then-columns.**

This gives every parameter a name, a **semantic**, a type and a default value,
which is what the scene's ``<Parameters>`` block is addressing:
``g_Bumpiness`` carries semantic ``Bumpiness``, ``g_Reflectivity`` carries
``Reflectivity``, and so on for all six of the semantics that appear on
essentially every material.

Two things fall out of it that the app uses:

* **The 17-float ``<Material>`` block is confirmed in shape.**  ``mat_main``
  declares ``Diffuse``, ``Specular``, ``Ambient`` and ``Emissive`` as 1x4
  vectors plus a scalar ``SpecularPower`` -- 4x4 + 1 = 17.  It does **not**
  confirm the *order* of those floats in the scene XML, because D3DX binds
  parameters by semantic rather than by position; see ``specs/bsd9.md`` §5.
* **A scene can pass a semantic the shader does not have.**  The exporter
  writes a fixed parameter block regardless, so 17,998 of 82,872 parameter
  writes in stock data are inert -- ``mat_main_2`` has no ``Bumpiness`` and no
  ``Roughness``, which is coherent because it has no normal map either.

Still undecoded: the technique and pass tables, the object table (shader
bytecode and sampler state), and the payloads of the five trailing chunks
(``VARL``, ``ANI ``, ``TRIG``, ``INIT``, ``MAIN``).  The blob is kept verbatim
so nothing is lost.

TWO FILES ARE A DIFFERENT CONTAINER
-----------------------------------
``ObjectFieldScripts/Meshes/Blender/mat_dist_2.bsd9`` and ``mat_dist_3.bsd9``
have no ``XF`` magic: two uint32 sizes then high-entropy data, almost certainly
compressed.  **No scene references either of them.**  They raise
:class:`UnsupportedFormat` rather than being guessed at.
"""

from __future__ import annotations

import struct
from typing import Dict, List, Optional, Tuple

from ..errors import ParseError, UnsupportedFormat

VERSION = "1.0"

MAGIC = b"XF  90.1"

#: Where the string table starts.  The texture index array sits between 0x10
#: and here, zero-padded; no shipped file needs more than eight slots.
_STRINGS_AT = 0x30

#: Refuse absurd counts rather than allocating on a corrupt file.
_MAX_SLOTS = 64
_MAX_STRINGS = 512


def _read_string(data: bytes, off: int, *, path: Optional[str]) -> Tuple[str, int]:
    """One length-prefixed, NUL-terminated, four-byte-aligned string."""
    if off + 4 > len(data):
        raise ParseError("string table runs past the end", path=path, offset=off)
    (length,) = struct.unpack_from("<I", data, off)
    off += 4
    if off + length > len(data):
        raise ParseError(
            f"string of {length} bytes runs past the end", path=path, offset=off
        )
    text = data[off : off + length].decode("latin-1")
    # length + NUL, rounded up to four.  cp1252/latin-1 rather than utf-8:
    # these are identifiers written by a Windows tool and are ASCII in practice,
    # but latin-1 cannot fail and so cannot lose a file to one odd byte.
    return text, off + ((length + 4) & ~3)


#: The D3DX9 effect tag that opens the blob.
D3DX9_TAG = 0xFEFF0901

#: ``D3DXPARAMETERCLASS``.
CLASS_SCALAR = 0
CLASS_VECTOR = 1
CLASS_MATRIX_ROWS = 2
CLASS_MATRIX_COLUMNS = 3
CLASS_OBJECT = 4
CLASS_STRUCT = 5

CLASS_NAMES = {
    CLASS_SCALAR: "scalar", CLASS_VECTOR: "vector",
    CLASS_MATRIX_ROWS: "matrix_rows", CLASS_MATRIX_COLUMNS: "matrix_columns",
    CLASS_OBJECT: "object", CLASS_STRUCT: "struct",
}

#: ``D3DXPARAMETERTYPE``.
TYPE_FLOAT = 3
TYPE_TEXTURE = 5

TYPE_NAMES = {
    0: "void", 1: "bool", 2: "int", 3: "float", 4: "string", 5: "texture",
    6: "texture1d", 7: "texture2d", 8: "texture3d", 9: "texturecube",
    10: "sampler", 11: "sampler1d", 12: "sampler2d", 13: "sampler3d",
    14: "samplercube", 15: "pixelshader", 16: "vertexshader",
    17: "pixelfragment", 18: "vertexfragment", 19: "unsupported",
}

#: Texture-ish parameter types, for telling a binding from a constant.
TEXTURE_TYPES = frozenset({5, 6, 7, 8, 9})


class Parameter:
    """One effect parameter: what it is called, and what the engine calls it."""

    __slots__ = ("name", "semantic", "type", "cls", "rows", "columns",
                 "element_count", "default")

    def __init__(self, name, semantic, type_, cls, rows, columns,
                 element_count, default) -> None:
        #: The shader's own identifier, e.g. ``g_Bumpiness``.
        self.name = name
        #: What a scene addresses it by -- ``<Float semantic="Bumpiness">`` --
        #: or ``None`` for a parameter the engine never sets by semantic.
        self.semantic = semantic
        self.type = type_
        self.cls = cls
        self.rows = rows
        self.columns = columns
        self.element_count = element_count
        #: The compiled-in default, for float scalars and vectors only.
        self.default = default

    @property
    def type_name(self) -> str:
        return TYPE_NAMES.get(self.type, str(self.type))

    @property
    def class_name(self) -> str:
        return CLASS_NAMES.get(self.cls, str(self.cls))

    @property
    def is_texture(self) -> bool:
        return self.type in TEXTURE_TYPES

    def describe(self) -> str:
        dims = ""
        if self.rows and self.columns and self.cls != CLASS_OBJECT:
            dims = f"{self.rows}x{self.columns} "
        out = f"{self.type_name} {dims}{self.name}"
        if self.semantic:
            out += f" : {self.semantic}"
        if self.default:
            out += "  = " + ", ".join(f"{x:g}" for x in self.default)
        return out

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Parameter {self.describe()}>"


class Pass:
    """One pass of a technique, and the objects the pass uses."""

    __slots__ = ("name", "annotations", "state_count", "objects")

    def __init__(self, name, annotations, state_count) -> None:
        self.name = name
        self.annotations = annotations
        #: How many render/sampler states the pass assigns.  Their *values*
        #: are not decoded -- that needs the D3D state table -- but the count
        #: is what makes the walk land exactly, so it is read rather than
        #: skipped.
        self.state_count = state_count
        #: :class:`EffectObject` entries whose header names this pass.
        self.objects: List["EffectObject"] = []

    @property
    def shaders(self) -> List["EffectObject"]:
        """The objects that are compiled shader bytecode."""
        return [o for o in self.objects if o.shader_model]

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Pass {self.name!r} {self.state_count} state(s)>"


class Technique:
    """One technique: a name and its passes, in order."""

    __slots__ = ("name", "annotations", "passes")

    def __init__(self, name, annotations, passes) -> None:
        self.name = name
        self.annotations = annotations
        self.passes: List[Pass] = passes

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Technique {self.name!r} {len(self.passes)} pass(es)>"


class EffectObject:
    """One entry of the object table: bytecode, a string, or a texture name.

    ``technique``/``pass_``/``state`` locate it. A resource written for a pass
    carries real indices; one written for a *parameter* stores ``0xFFFFFFFF``
    in ``technique`` and the parameter's index in ``pass_``, which is why the
    two are kept raw rather than resolved into names here.
    """

    __slots__ = ("technique", "pass_", "element", "state", "usage", "data")

    #: What the format writes where an index does not apply.
    NONE = 0xFFFFFFFF

    def __init__(self, technique, pass_, element, state, usage, data) -> None:
        self.technique = technique
        self.pass_ = pass_
        self.element = element
        self.state = state
        self.usage = usage
        #: The payload, verbatim.  Shader bytecode for most of them.
        self.data = data

    @property
    def belongs_to_pass(self) -> bool:
        return self.technique != self.NONE

    @property
    def shader_model(self) -> Optional[str]:
        """``vs_1_1``, ``ps_2_0`` ... or ``None`` if this is not bytecode.

        A Direct3D 9 shader opens with a version token whose high word is
        ``0xFFFE`` for a vertex shader and ``0xFFFF`` for a pixel one; the low
        word is the version. Anything else is a string, a texture name or a
        state blob.
        """
        if len(self.data) < 4:
            return None
        (token,) = struct.unpack_from("<I", self.data, 0)
        kind = token >> 16
        if kind not in (0xFFFE, 0xFFFF):
            return None
        prefix = "vs" if kind == 0xFFFE else "ps"
        return f"{prefix}_{(token >> 8) & 0xFF}_{token & 0xFF}"

    def __repr__(self) -> str:  # pragma: no cover
        what = self.shader_model or f"{len(self.data)} bytes"
        return f"<EffectObject {what}>"


class Chunk:
    """One trailing chunk: a reversed four-character tag and its payload."""

    __slots__ = ("tag", "offset", "size", "payload")

    def __init__(self, tag: str, offset: int, size: int, payload: bytes) -> None:
        self.tag = tag
        self.offset = offset
        #: Includes the eight-byte header, which is how the file stores it.
        self.size = size
        self.payload = payload

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Chunk {self.tag!r} {self.size} bytes at 0x{self.offset:x}>"


def _d3dx_name(data: bytes, off: int) -> Optional[str]:
    """A D3DX string: ``uint32 size`` then ``size`` bytes, NUL included."""
    if off <= 0 or off + 4 > len(data):
        return None
    (size,) = struct.unpack_from("<I", data, off)
    if size == 0 or off + 4 + size > len(data):
        return None
    return data[off + 4 : off + 4 + size].rstrip(b"\0").decode("latin-1")


def _read_parameter(data: bytes, type_off: int, value_off: int, index: int, *,
                    path) -> Parameter:
    """One parameter, from its typedef and value offsets."""
    if type_off + 20 > len(data):
        raise ParseError(f"parameter {index} typedef is out of range", path=path)
    type_, cls, name_off, sem_off, elements = struct.unpack_from(
        "<5I", data, type_off
    )
    at = type_off + 20
    rows = columns = 0
    if cls == CLASS_VECTOR:
        # Columns before rows for vectors, the other way round for the rest.
        # This asymmetry is in the format, not a typo.
        columns, rows = struct.unpack_from("<2I", data, at)
    elif cls in (CLASS_SCALAR, CLASS_MATRIX_ROWS, CLASS_MATRIX_COLUMNS):
        rows, columns = struct.unpack_from("<2I", data, at)

    default = None
    if type_ == TYPE_FLOAT and cls in (CLASS_SCALAR, CLASS_VECTOR):
        n = max(1, rows) * max(1, columns)
        if value_off and value_off + 4 * n <= len(data):
            default = struct.unpack_from(f"<{n}f", data, value_off)

    return Parameter(
        _d3dx_name(data, name_off),
        _d3dx_name(data, sem_off),
        type_,
        cls,
        rows,
        columns,
        elements,
        default,
    )


def parse_parameters(blob: bytes, *, path: Optional[str] = None) -> List[Parameter]:
    """The parameter table of a D3DX9 effect blob.

    Returns ``[]`` for a blob that is not a D3DX9 effect rather than raising:
    the container is useful on its own, and a caller asking for parameters can
    treat "none" and "not available" the same way.  A blob that *is* one but
    does not walk raises, because a desynchronised walk yields plausible-looking
    nonsense and this feeds an editor.

    Reads only as far as the parameters.  :func:`parse_effect` continues into
    the technique and object tables and is the one that proves the walk by
    landing exactly on the end of the blob.
    """
    if len(blob) < 8 or struct.unpack_from("<I", blob, 0)[0] != D3DX9_TAG:
        return []
    (start,) = struct.unpack_from("<I", blob, 4)
    data = blob[8:]                      # every offset below is relative to here
    if start + 16 > len(data):
        raise ParseError("effect header runs past the blob", path=path)
    count = struct.unpack_from("<I", data, start)[0]
    parameters, _off = _walk_parameters(data, start, count, path=path)
    return parameters


def _walk_parameters(data: bytes, start: int, count: int, *, path):
    """Read the parameter records and return where the technique table begins."""
    off = start + 16
    out: List[Parameter] = []
    for i in range(count):
        if off + 16 > len(data):
            raise ParseError(
                f"parameter {i} of {count} runs past the blob", path=path, offset=off
            )
        type_off, value_off, _flags, annotations = struct.unpack_from("<4I", data, off)
        # An annotation is two dwords -- a typedef offset and a value offset --
        # not a full 16-byte parameter record.  Assuming otherwise desyncs the
        # walk a few parameters in and every name after it is garbage.
        off += 16 + annotations * 8
        out.append(_read_parameter(data, type_off, value_off, i, path=path))
    return out, off


def parse_effect(blob: bytes, *, path: Optional[str] = None):
    """The whole D3DX9 effect: parameters, techniques and the object table.

    Returns ``(parameters, techniques, objects)``, all empty for a blob that is
    not a D3DX9 effect.

    The walk **accounts for every byte** of the blob, which is the only
    honest check available here: a desynchronised D3DX walk does not raise, it
    yields plausible-looking records with garbage in them. Landing exactly on
    the end is what says the layout below is the real one, and it does so on
    230 of 230 shipped shaders.

    Layout after the parameter table::

        per technique:  name offset, annotation count, pass count
                        annotations (2 dwords each)
                        passes
        per pass:       name offset, annotation count, state count
                        annotations (2 dwords each)
                        states (4 dwords each)

        uint32 inline_count            objects stored by id
        uint32 resource_count
        inline_count  * { uint32 id, uint32 size, bytes padded to 4 }
        resource_count * { uint32 technique, pass, element, state, usage,
                           uint32 size, bytes padded to 4 }

    The five-dword resource header was settled by trying four, five and six
    across the corpus: four and six leave most files unaccounted for, five
    lands every one of them exactly.
    """
    if len(blob) < 8 or struct.unpack_from("<I", blob, 0)[0] != D3DX9_TAG:
        return [], [], []
    (start,) = struct.unpack_from("<I", blob, 4)
    data = blob[8:]                      # every offset below is relative to here
    if start + 16 > len(data):
        raise ParseError("effect header runs past the blob", path=path)

    parameter_count, technique_count, _unknown, _object_count = struct.unpack_from(
        "<4I", data, start)
    parameters, off = _walk_parameters(data, start, parameter_count, path=path)

    techniques: List[Technique] = []
    for i in range(technique_count):
        if off + 12 > len(data):
            raise ParseError(f"technique {i} runs past the blob", path=path,
                             offset=off)
        name_off, annotations, pass_count = struct.unpack_from("<3I", data, off)
        off += 12 + annotations * 8
        passes: List[Pass] = []
        for j in range(pass_count):
            if off + 12 > len(data):
                raise ParseError(f"technique {i} pass {j} runs past the blob",
                                 path=path, offset=off)
            pass_name_off, pass_annotations, states = struct.unpack_from(
                "<3I", data, off)
            # A state is four dwords: operation, index, typedef, value.
            off += 12 + pass_annotations * 8 + states * 16
            passes.append(Pass(_d3dx_name(data, pass_name_off),
                               pass_annotations, states))
        techniques.append(Technique(_d3dx_name(data, name_off), annotations,
                                    passes))

    objects: List[EffectObject] = []
    if off + 8 > len(data):
        raise ParseError("object table header runs past the blob", path=path,
                         offset=off)
    inline_count, resource_count = struct.unpack_from("<2I", data, off)
    off += 8
    for i in range(inline_count):
        if off + 8 > len(data):
            raise ParseError(f"inline object {i} runs past the blob", path=path,
                             offset=off)
        _id, size = struct.unpack_from("<2I", data, off)
        body = data[off + 8:off + 8 + size]
        off += 8 + ((size + 3) & ~3)
        objects.append(EffectObject(EffectObject.NONE, _id, 0, 0, 0, body))
    for i in range(resource_count):
        if off + 24 > len(data):
            raise ParseError(f"resource {i} runs past the blob", path=path,
                             offset=off)
        technique, pass_, element, state, usage = struct.unpack_from(
            "<5I", data, off)
        (size,) = struct.unpack_from("<I", data, off + 20)
        body = data[off + 24:off + 24 + size]
        off += 24 + ((size + 3) & ~3)
        if off > len(data):
            raise ParseError(f"resource {i} body runs past the blob", path=path,
                             offset=off)
        objects.append(EffectObject(technique, pass_, element, state, usage, body))

    if off != len(data):
        raise ParseError(
            f"the effect walk ended at 0x{off:x} of 0x{len(data):x}; "
            f"{len(data) - off} bytes are unaccounted for",
            path=path, offset=off)

    # Hand each pass the objects whose header names it, so a caller can ask a
    # pass what it runs rather than filtering a flat list.
    for obj in objects:
        if not obj.belongs_to_pass:
            continue
        if obj.technique < len(techniques):
            technique = techniques[obj.technique]
            if obj.pass_ < len(technique.passes):
                technique.passes[obj.pass_].objects.append(obj)

    return parameters, techniques, objects


class Shader:
    """A parsed ``.bsd9``."""

    __slots__ = ("version", "texture_slots", "strings", "blob", "chunks", "path",
                 "_parameters", "_effect")

    def __init__(
        self,
        *,
        version: int,
        texture_slots: List[str],
        strings: List[str],
        blob: bytes,
        chunks: List[Chunk],
        path: Optional[str] = None,
    ) -> None:
        self._parameters: Optional[List[Parameter]] = None
        self._effect = None
        self.version = version
        #: The shader's own name for each texture slot, **in slot order**.
        #: Positionally aligned with a scene's ``<Textures>`` list.
        self.texture_slots = texture_slots
        #: Every string in the header table.  The first ``len(texture_slots)``
        #: are the slots; the rest are technique and parameter names, which are
        #: not separable from the header alone.
        self.strings = strings
        #: The compiled effect, verbatim and undecoded.
        self.blob = blob
        self.chunks = chunks
        self.path = path

    @property
    def parameters(self) -> List[Parameter]:
        """The effect's parameters, parsed on first use.

        Lazy because most callers only want :attr:`texture_slots`, and walking
        the parameter table of 230 shaders to answer "which texture is the
        normal map" would be work nobody asked for.
        """
        if self._parameters is None:
            self._parameters = parse_parameters(self.blob, path=self.path)
        return self._parameters

    @property
    def techniques(self) -> List[Technique]:
        """The techniques the effect declares, each with its passes.

        Not the same list as the leftover header strings.  ``mat_main`` carries
        ``DoIt``, ``V20P20``, ``FFPHigh`` and ``FFPLow`` in its header but
        declares ``V20P20``, ``ShadowMapV20P20`` and ``Occluded`` here -- so the
        header's spare strings are Ascaron's own, not a copy of this table.
        """
        return self._parsed_effect()[1]

    @property
    def objects(self) -> List["EffectObject"]:
        """The object table: shader bytecode, strings and texture names."""
        return self._parsed_effect()[2]

    def _parsed_effect(self):
        if self._effect is None:
            self._effect = parse_effect(self.blob, path=self.path)
        return self._effect

    def shader_models(self) -> List[str]:
        """Every shader model the effect compiles for, e.g. ``vs_1_1``."""
        seen = []
        for obj in self.objects:
            model = obj.shader_model
            if model and model not in seen:
                seen.append(model)
        return sorted(seen)

    def semantics(self) -> Dict[str, Parameter]:
        """``{semantic: parameter}`` -- what a scene can actually address.

        This is the set a ``<Parameters>`` block is writing into.  A semantic
        *not* in here is inert: the exporter wrote a fixed block regardless of
        the shader, and 17,998 of 82,872 parameter writes in stock data land
        nowhere.
        """
        return {p.semantic: p for p in self.parameters if p.semantic}

    def slot(self, index: int) -> Optional[str]:
        """The name of texture slot ``index``, or ``None`` if it has none."""
        if 0 <= index < len(self.texture_slots):
            return self.texture_slots[index]
        return None

    def describe(self) -> str:
        slots = ", ".join(self.texture_slots) if self.texture_slots else "no textures"
        return f"v{self.version}, {slots}"

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Shader {self.path or '<memory>'} {self.describe()}>"


def parse(data: bytes, *, path: Optional[str] = None) -> Shader:
    """Parse a ``.bsd9``.

    Reads the header in full and locates -- but does not interpret -- the
    compiled blob and the trailing chunks.  The parse **accounts for every byte
    of the file**: the chunk walk has to land exactly on the terminator, and a
    file where it does not is a file this module has misunderstood, so it raises
    rather than returning a half-read result.
    """
    if len(data) < _STRINGS_AT + 4:
        raise ParseError("file too short to be a .bsd9", path=path)
    if data[:8] != MAGIC:
        raise UnsupportedFormat(
            f"not a .bsd9 effect: magic is {data[:8]!r}, expected {MAGIC!r}. "
            "Two shipped files (mat_dist_2, mat_dist_3) are a different, "
            "apparently compressed container and no scene references them.",
            path=path,
        )

    version, n_tex = struct.unpack_from("<2I", data, 8)
    if n_tex > _MAX_SLOTS:
        raise ParseError(f"{n_tex} texture slots is not plausible", path=path, offset=12)
    indices = list(struct.unpack_from(f"<{n_tex}I", data, 16)) if n_tex else []

    off = _STRINGS_AT
    (n_str,) = struct.unpack_from("<I", data, off)
    off += 4
    if n_str > _MAX_STRINGS:
        raise ParseError(f"{n_str} strings is not plausible", path=path, offset=off - 4)
    strings: List[str] = []
    for _ in range(n_str):
        text, off = _read_string(data, off, path=path)
        strings.append(text)

    bad = [i for i in indices if i >= len(strings)]
    if bad:
        raise ParseError(
            f"texture slot names index {bad} past the {len(strings)}-entry string table",
            path=path,
        )
    slots = [strings[i] for i in indices]

    if off + 4 > len(data):
        raise ParseError("no blob length after the string table", path=path, offset=off)
    (blob_len,) = struct.unpack_from("<I", data, off)
    off += 4
    if off + blob_len > len(data):
        raise ParseError(
            f"blob of {blob_len} bytes runs past the end", path=path, offset=off
        )
    blob = data[off : off + blob_len]
    off += blob_len

    chunks: List[Chunk] = []
    while off + 8 <= len(data) - 4:
        tag = data[off : off + 4][::-1].decode("latin-1")
        (size,) = struct.unpack_from("<I", data, off + 4)
        if size < 8 or off + size > len(data):
            raise ParseError(
                f"chunk {tag!r} declares {size} bytes", path=path, offset=off
            )
        chunks.append(Chunk(tag, off, size, data[off + 8 : off + size]))
        off += size

    # The file ends with a four-byte terminator.  Landing anywhere else means
    # the layout above is wrong for this file, and a parser that shrugs at that
    # is a parser whose output nobody can rely on.
    if off != len(data) - 4:
        raise ParseError(
            f"chunk walk ended at {off} with {len(data)} bytes in the file",
            path=path,
            offset=off,
        )

    return Shader(
        version=version,
        texture_slots=slots,
        strings=strings,
        blob=blob,
        chunks=chunks,
        path=path,
    )


def is_shader(data: bytes) -> bool:
    """Cheap sniff, matching the other formats' ``is_*`` helpers."""
    return data[:8] == MAGIC


__all__ = [
    "VERSION",
    "MAGIC",
    "D3DX9_TAG",
    "Shader",
    "Parameter",
    "Technique",
    "Pass",
    "EffectObject",
    "Chunk",
    "parse",
    "parse_parameters",
    "parse_effect",
    "is_shader",
    "TYPE_NAMES",
    "CLASS_NAMES",
    "TEXTURE_TYPES",
]
