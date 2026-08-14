"""
Ascaron ``.anim`` drawable (A2dLib resource ``SH_ANIM``).  v1.0

Every one of the 1,107 shipped files is exactly 3,220 bytes and a single fixed
record.  Comparing any two, the *only* bytes that differ are the width, the
height, the source filename, and a repeat of the size later in the record.
Everything else is identical boilerplate.

So this module deliberately does not model the format.  It reads four fields and
patches three, and passes the remaining 3,200-odd bytes through untouched.
Modelling boilerplate nobody understands would invite "tidying" it, and the
engine is the only thing that knows what it means.

    0x000  char[8]  "SH_ANIM\\0"
    0x00c  u32      frame count -- 1 in every shipped file
    0x010  u32      width
    0x014  u32      height
    0x068  char[]   source image, e.g. "images\\Auftraege.aim"
    0x1a0  u32      width again
    0x1a4  u32      height again

WHY IT MATTERS
--------------
The declared size must agree with the sub-image rectangle in the ``.tex`` that
holds the same graphic (``Auftraege.anim`` says 27x38; ``TexPage3.tex`` places
``Auftraege.aim`` at 27x38).  Rescaling an atlas page without updating these
leaves the UI drawing the wrong region -- so ``edit.atlas`` patches them as part
of the same operation, and ``validate`` checks the agreement.
"""

from __future__ import annotations

import struct
from typing import Optional, Tuple

from ..errors import ParseError

VERSION = "1.0"

TAG = b"SH_ANIM\0"
RECORD_SIZE = 3220

OFF_FRAMES = 0x00C
OFF_WIDTH = 0x010
OFF_HEIGHT = 0x014
OFF_SOURCE = 0x068
OFF_WIDTH2 = 0x1A0
OFF_HEIGHT2 = 0x1A4

#: Bytes available for the source path before the next known field.
_SOURCE_MAX = 0x1A0 - OFF_SOURCE


class Anim:
    """A drawable: a named source image and the size it is drawn at."""

    __slots__ = ("_data", "path")

    def __init__(self, data: bytes, path: Optional[str] = None) -> None:
        self._data = bytearray(data)
        self.path = path

    @property
    def frames(self) -> int:
        return struct.unpack_from("<I", self._data, OFF_FRAMES)[0]

    @property
    def size(self) -> Tuple[int, int]:
        return (
            struct.unpack_from("<I", self._data, OFF_WIDTH)[0],
            struct.unpack_from("<I", self._data, OFF_HEIGHT)[0],
        )

    @property
    def width(self) -> int:
        return self.size[0]

    @property
    def height(self) -> int:
        return self.size[1]

    @property
    def source_size(self) -> Tuple[int, int]:
        """The size of the **source image**, which is the atlas rectangle.

        Equal to :attr:`size` for an ordinary drawable, and smaller for a
        stretched nine-slice frame, where the source is only the corner tile.
        This is the pair that must agree with the ``.tex`` -- see the module
        docstring.
        """
        return (
            struct.unpack_from("<I", self._data, OFF_WIDTH2)[0],
            struct.unpack_from("<I", self._data, OFF_HEIGHT2)[0],
        )

    @property
    def stretched(self) -> bool:
        """Is this drawn at a different size from its source image?

        True for 57 of the 1,107 shipped drawables, all of them nine-slice
        frames.  **Not** an inconsistency, which is what it was taken for.
        """
        return self.source_size != self.size

    @property
    def source(self) -> str:
        end = self._data.find(b"\0", OFF_SOURCE)
        if end < 0:
            end = OFF_SOURCE
        return self._data[OFF_SOURCE:end].decode("cp1252", "replace")

    def set_size(self, width: int, height: int) -> None:
        """Set the size the drawable is **drawn** at.

        Only that.  This used to write the second pair as well, which is fine
        for the 1,050 drawables where the two agree and destroys the other 57:
        a stretched frame's drawn size would have been overwritten with its
        corner tile's size.
        """
        if width < 0 or height < 0:
            raise ValueError("size must be non-negative")
        struct.pack_into("<I", self._data, OFF_WIDTH, width)
        struct.pack_into("<I", self._data, OFF_HEIGHT, height)

    def set_source_size(self, width: int, height: int) -> None:
        """Set the size of the source image -- the atlas rectangle."""
        if width < 0 or height < 0:
            raise ValueError("size must be non-negative")
        struct.pack_into("<I", self._data, OFF_WIDTH2, width)
        struct.pack_into("<I", self._data, OFF_HEIGHT2, height)

    def set_source(self, source: str) -> None:
        raw = source.encode("cp1252", "replace")
        if len(raw) + 1 > _SOURCE_MAX:
            raise ValueError(f"source path is too long ({len(raw)} > {_SOURCE_MAX - 1})")
        self._data[OFF_SOURCE : OFF_SOURCE + _SOURCE_MAX] = raw.ljust(_SOURCE_MAX, b"\0")

    def to_bytes(self) -> bytes:
        return bytes(self._data)

    def __repr__(self) -> str:  # pragma: no cover
        w, h = self.size
        return f"<Anim {self.source!r} {w}x{h}>"


def parse(data: bytes, *, path: Optional[str] = None) -> Anim:
    if len(data) < OFF_HEIGHT2 + 4:
        raise ParseError("file is too short to be a .anim", path=path)
    if data[:8] != TAG:
        raise ParseError(f"bad tag {data[:8]!r}, expected {TAG!r}", path=path)
    if len(data) != RECORD_SIZE:
        # Tolerated rather than rejected: every shipped file is 3,220 bytes, but
        # the record is self-describing enough to read either way, and refusing
        # would hide a file the user can plainly see.
        pass
    return Anim(data, path=path)


def is_anim(data: bytes) -> bool:
    return data[:8] == TAG


__all__ = ["VERSION", "Anim", "parse", "is_anim", "TAG", "RECORD_SIZE"]
