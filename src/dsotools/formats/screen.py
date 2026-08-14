"""
Parser/writer for Ascaron A2dLib ``.screen`` interface layouts (``SH_SCRN``).

The last undecoded format in the interface chain, and the one that gates the
Interface tab:

    .screen  ->  elements  ->  scripts\\X.anim  ->  images\\Y.aim
                                     |
                               (via the .tex indexes) page + rectangle

Container, confirmed on all 83 shipped files
--------------------------------------------
The same ``A2DFILE`` container as ``.tex`` and ``.anim`` (``specs/interface_formats.md``):
a 28-byte file header, then records of ``tag[8] + u32 size + payload``, where
**size counts the 12-byte prefix**.

A screen is one element -- the ``CScreen`` -- followed by its elements, laid out
flat rather than nested:

    28 bytes   A2DFILE header (size 28, the constant 17, three zeros)
    element    the screen itself: SH_DWFAB + SH_DWB + SH_SCRN + u32 count
    element*   count is what the SH_SCRN record is followed by

Every element begins the same way:

    SH_DWFAB  416 bytes
      +0x00  "SH_DWFAB", +0x08 u32 416, +0x0c 0xffffffff
      +0x10  class name, NUL-padded      CButton, CStatic, CTextBox, ...
      +0x50  element name, NUL-padded    longest shipped is 54 characters
      +0x150 two dwords, 0xffffffff in every sample
    SH_DWB    304 bytes
      +0x0c  x, y, w, h as signed int32  -- the element's rectangle
    then zero or more **length-prefixed blocks**: a u32 size followed by that
    many bytes, class-specific, repeated until the element ends.  Optionally an
    8-byte trailer, seen on 14 elements of 1,381.

Why this is trusted
-------------------
Two independent methods agree, on every file:

* the structural walk above -- which never searches for a tag -- finds **exactly
  the same element offsets** as scanning the file for ``SH_DWFAB``, and lands on
  the final byte, in **83 of 83** files;
* the references embedded in the class blocks resolve: ``scripts\\*.anim``
  **1,433 of 1,433**, ``fonts\\*.res`` 470 of 470, ``sfx\\*.res`` 342 of 342,
  ``staticImages\\`` 2 of 2 -- **2,247 of 2,251, 99.8%**.

The four that do not resolve are one malformed reference in Ascaron's own data:
``\\Pr2_Slider_DragBtn_nr.anim`` and its ``_hl`` twin carry a leading backslash
and no folder.  They are reported, not repaired.

The rectangle reading is corroborated the same way: 1,260 of 1,381 elements fit
strictly inside the 1024x768 the filename declares, and the ones that do not are
deliberate -- letterbox bars at ``y=-1``, a 1024x768 background at ``(-3,-3)``.
Only 10 have a non-positive width or height.

What is NOT claimed
-------------------
**The parent/child structure is not in the file.**  The ``CScreen`` count
agrees with the element count in only **64 of 83** files, so 133 elements are
parts of another element -- but no field says which.  Searched rather than
sampled: requiring a candidate child count to read 0 for all 948 elements of
the 64 flat screens leaves 551 offsets in the 720-byte common part, and none of
them reads 4 at either slider that owns four children; the same filter finds
nothing that marks a child either.

So this module keeps the flat list it can prove, and the tree is *derived*
next door in :mod:`dsotools.edit.screentree`, which answers to the declared
count on all 83 files.  Anything drawing a layout needs it: a child's rectangle
is an offset from its parent, not a position.
"""

from __future__ import annotations

import re
import struct
from typing import List, Optional, Tuple

from ..errors import ParseError, UnsupportedFormat

VERSION = "1.0"

MAGIC = b"A2DFILE\0"
HEADER_LEN = 28
TAG_DWFAB = b"SH_DWFAB"
TAG_DWB = b"SH_DWB\0\0"
TAG_SCRN = b"SH_SCRN\0"

#: Fixed record sizes.  Identical in all 83 files and all 1,464 elements.
DWFAB_LEN = 416
DWB_LEN = 304
SCRN_LEN = 568

CLASS_OFFSET = 0x10
CLASS_FIELD = 0x40
NAME_OFFSET = 0x50
NAME_FIELD = 0x100          # up to the two dwords at +0x150
RECT_OFFSET = 0x0C          # within SH_DWB

#: A reference is a Windows-style path into one of the resource folders.
_REFERENCE = re.compile(rb"[ -~]{4,}")
_FOLDERS = ("scripts", "fonts", "sfx", "staticimages", "images")


def _text(raw: bytes) -> str:
    return raw.split(b"\x00", 1)[0].decode("cp1252", "replace")


class Element:
    """One control on a screen, and the bytes it came from.

    The bytes are kept verbatim: everything this class does not understand --
    and it does not understand the class-specific blocks -- survives a
    round-trip untouched, which is what makes editing a layout safe.
    """

    __slots__ = ("raw", "offset", "_blocks", "trailer")

    def __init__(self, raw: bytearray, offset: int, blocks, trailer: int) -> None:
        self.raw = raw
        #: Offset of this element inside the file it was read from.
        self.offset = offset
        self._blocks = list(blocks)
        self.trailer = trailer

    # -- what it is ----------------------------------------------------------

    @property
    def class_name(self) -> str:
        return _text(bytes(self.raw[CLASS_OFFSET:CLASS_OFFSET + CLASS_FIELD]))

    @property
    def name(self) -> str:
        return _text(bytes(self.raw[NAME_OFFSET:NAME_OFFSET + NAME_FIELD]))

    @name.setter
    def name(self, value: str) -> None:
        encoded = value.encode("cp1252")
        # NUL-terminated inside a fixed field: a name that does not fit cannot
        # be written without overrunning into whatever follows it.
        if len(encoded) >= NAME_FIELD:
            raise UnsupportedFormat(
                f"element name is {len(encoded)} bytes; the field holds "
                f"{NAME_FIELD - 1} plus a terminator"
            )
        self.raw[NAME_OFFSET:NAME_OFFSET + NAME_FIELD] = (
            encoded + b"\x00" * (NAME_FIELD - len(encoded))
        )

    @property
    def is_screen(self) -> bool:
        return self.raw[DWFAB_LEN + DWB_LEN:DWFAB_LEN + DWB_LEN + 8] == TAG_SCRN

    # -- where it is ---------------------------------------------------------

    @property
    def rect(self) -> Tuple[int, int, int, int]:
        """``(x, y, w, h)``, signed: shipped layouts really do use ``-1``."""
        at = DWFAB_LEN + RECT_OFFSET
        return struct.unpack_from("<4i", self.raw, at)

    @rect.setter
    def rect(self, value) -> None:
        x, y, w, h = value
        struct.pack_into("<4i", self.raw, DWFAB_LEN + RECT_OFFSET, x, y, w, h)

    # -- what it draws -------------------------------------------------------

    def references(self) -> List[str]:
        """Resource paths named by the class-specific blocks, in order.

        ``scripts\\X.anim`` for drawables, ``fonts\\X.res``, ``sfx\\X.res``.
        Read out of the blocks rather than from a known offset, because the
        block layout differs per class and is not decoded -- the strings,
        however, are unambiguous, and 2,247 of the 2,251 in the corpus resolve.
        """
        out = []
        for start, size in self._blocks:
            for match in _REFERENCE.finditer(bytes(self.raw[start:start + size])):
                text = match.group().decode("cp1252", "replace")
                if "\\" in text and text.split("\\", 1)[0].lower() in _FOLDERS:
                    out.append(text)
        return out

    def set_reference(self, old: str, new: str) -> bool:
        """Replace one resource path in place.  ``False`` if it was not there.

        In place and length-checked: the string sits in a fixed-width field
        inside a block whose layout is not decoded, so the only safe edit is
        one that writes the same field and re-terminates it.
        """
        encoded_old = old.encode("cp1252")
        encoded_new = new.encode("cp1252")
        for start, size in self._blocks:
            block = bytes(self.raw[start:start + size])
            at = block.find(encoded_old + b"\x00")
            if at < 0:
                continue
            # How much room the field has: the run of NULs after the string.
            room = len(encoded_old)
            probe = at + len(encoded_old)
            while probe < size and block[probe] == 0:
                room += 1
                probe += 1
            if len(encoded_new) >= room:
                raise UnsupportedFormat(
                    f"{new!r} needs {len(encoded_new) + 1} bytes; the field "
                    f"holds {room}"
                )
            here = start + at
            self.raw[here:here + room] = (
                encoded_new + b"\x00" * (room - len(encoded_new))
            )
            return True
        return False

    def to_bytes(self) -> bytes:
        return bytes(self.raw)

    def __repr__(self) -> str:  # pragma: no cover
        x, y, w, h = self.rect
        return f"<{self.class_name} {self.name!r} at ({x},{y}) {w}x{h}>"


class Screen:
    """A parsed ``.screen``: a header, the screen element, and its elements."""

    def __init__(self, header: bytes, screen: Element, elements: List[Element],
                 declared_children: int, path: Optional[str] = None) -> None:
        self.header = bytes(header)
        #: The ``CScreen`` element itself -- it carries the screen's own name
        #: and rectangle.
        self.screen = screen
        self.elements: List[Element] = elements
        #: How many elements are **top level**.  Equal to ``len(elements)``
        #: in 64 of the 83 shipped files; where it is smaller, the difference
        #: is elements owned by another element.  Working out which is
        #: :mod:`dsotools.edit.screentree`'s job, and this is what checks it.
        self.declared_children = declared_children
        self.path = path

    @property
    def name(self) -> str:
        return self.screen.name

    def to_bytes(self) -> bytes:
        return b"".join(
            [self.header, self.screen.to_bytes()]
            + [e.to_bytes() for e in self.elements]
        )

    def __iter__(self):
        return iter(self.elements)

    def __len__(self) -> int:
        return len(self.elements)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Screen {self.name!r} {len(self.elements)} elements>"


def _record(data: bytes, off: int, tag: bytes, expect: int, path) -> int:
    """Check one fixed record and return the offset past it.

    The length is verified against the data as well as against the expected
    size: a file cut off inside a record still carries a valid-looking tag and
    length, and advancing on that alone reads a truncated file as a short one.
    """
    if data[off:off + 8] != tag:
        raise ParseError(f"expected {tag!r} at {off:#x}, got "
                         f"{data[off:off + 8]!r}", path=path, offset=off)
    if off + 12 > len(data):
        raise ParseError(f"{tag!r} header runs past the end", path=path, offset=off)
    size = struct.unpack_from("<I", data, off + 8)[0]
    if size != expect:
        raise UnsupportedFormat(
            f"{tag.rstrip(chr(0).encode()).decode()} is {size} bytes, not "
            f"{expect}", path=path, offset=off)
    if off + size > len(data):
        raise ParseError(
            f"{tag!r} needs {size} bytes and only {len(data) - off} remain",
            path=path, offset=off)
    return off + size


def _read_element(data: bytes, off: int, path) -> Tuple[Element, int]:
    """One element, from ``off``.  Returns it and the offset just past it."""
    start = off
    off = _record(data, off, TAG_DWFAB, DWFAB_LEN, path)
    off = _record(data, off, TAG_DWB, DWB_LEN, path)

    declared = None
    if data[off:off + 8] == TAG_SCRN:
        off = _record(data, off, TAG_SCRN, SCRN_LEN, path)
        if off + 4 > len(data):
            raise ParseError("truncated after SH_SCRN", path=path, offset=off)
        declared = struct.unpack_from("<I", data, off)[0]
        off += 4

    # The class-specific blocks: each is its own length, and they run until the
    # next element begins or the file ends.
    blocks = []
    while off < len(data) and data[off:off + 8] != TAG_DWFAB:
        if off + 4 > len(data):
            raise ParseError("truncated inside an element", path=path, offset=off)
        size = struct.unpack_from("<I", data, off)[0]
        if size < 4 or off + size > len(data):
            break
        blocks.append((off - start, size))
        off += size

    trailer = 0
    if off < len(data) and data[off:off + 8] != TAG_DWFAB:
        # 14 of 1,381 elements carry eight more bytes before the next one.
        # Kept verbatim rather than interpreted: `(0,0)`, `(1,0)` and `(2,0)`
        # are all that ship, and they are not the nesting -- they do not appear
        # on the elements that own children.
        if off + 8 <= len(data) and (
            off + 8 == len(data) or data[off + 8:off + 16] == TAG_DWFAB
        ):
            trailer = 8
            off += 8
        else:
            raise UnsupportedFormat(
                "element does not end on a block boundary", path=path, offset=off)

    element = Element(bytearray(data[start:off]), start, blocks, trailer)
    return element, off, declared


def parse(data: bytes, *, path: Optional[str] = None) -> Screen:
    """Read a ``.screen``.  Raises rather than guessing at anything unexpected."""
    if data[:8] != MAGIC:
        raise ParseError(f"not an A2DFILE container ({data[:8]!r})", path=path)
    header_len = struct.unpack_from("<I", data, 8)[0]
    if header_len != HEADER_LEN:
        raise UnsupportedFormat(
            f"header says {header_len} bytes, not {HEADER_LEN}", path=path)

    screen, off, declared = _read_element(data, HEADER_LEN, path)
    if not screen.is_screen:
        raise UnsupportedFormat(
            "the first element is not a CScreen (no SH_SCRN record)", path=path)

    elements: List[Element] = []
    while off < len(data):
        element, off, _ = _read_element(data, off, path)
        elements.append(element)

    return Screen(data[:HEADER_LEN], screen, elements, declared or 0, path)


#: A ``CButton`` names one drawable per state, in this order.  Read off the
#: shipped data: of the 237 buttons that carry four or more, slot 0 is named
#: ``*_disabled`` (or ``_gr``/``_off``) 127 times and never ``*_normal``, and
#: slot 1 is a plain or ``*_normal`` name 209 times.  Buttons with exactly
#: three skip the disabled state and start at normal (``_nr``, ``_pr``,
#: ``_hl``).
BUTTON_STATES = ("disabled", "normal", "pressed", "highlight", "blink")

_NORMAL_HINTS = ("_normal", "_nr", "_up")
_DISABLED_HINTS = ("_disabled", "_dis", "_gr", "_off")


def resting_index(class_name: str, references) -> int:
    """Which of an element's drawables is the one it shows at rest.

    A button at rest is *enabled and untouched*.  Drawing its first reference
    instead shows every button greyed out, which is not what the layout looks
    like in the game.

    Anything other than a button has one drawable and this is 0.
    """
    names = [str(r).replace("\\", "/").rsplit("/", 1)[-1].lower()
             for r in references]
    if not names:
        return 0
    if class_name != "CButton":
        return 0
    for i, name in enumerate(names):
        if any(hint in name for hint in _NORMAL_HINTS):
            return i
    if len(names) >= 4 and any(hint in names[0] for hint in _DISABLED_HINTS):
        return 1
    return 0


def build(screen: Screen) -> bytes:
    """Serialise.  Byte-identical to the source unless something was edited."""
    return screen.to_bytes()


__all__ = ["VERSION", "BUTTON_STATES", "Screen", "Element", "parse",
           "build", "resting_index"]
