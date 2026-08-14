"""
Composing a stretched interface frame out of its nine tiles.

WHY THIS EXISTS
---------------
An interface element is a rectangle and a drawable, and the two are often not
the same size.  Measured over all 83 shipped screens, of the 694 elements that
draw something:

* **399** draw a sprite at exactly its own size -- crop and place, no question;
* **135** are nine-slice frames, whose drawable names the **top-left tile only**
  (``ND_ListBoxWithBGTL``) while the atlas holds its ``TC TR ML MC MR BL BC BR``
  siblings beside it.  All nine tiles are present for **135 of 135**;
* **160** are stretched and are not a tile family, so the single sprite is
  scaled and the result is an approximation.

Drawing that middle group by stretching the corner tile is what makes a preview
look wrong: ``Background_blaugrau`` is a 3x3 corner drawn at 511x215.  Composing
it properly is nine crops and eight resizes, and then the picture is the picture.

THE SUFFIX CONVENTION
---------------------
``<base>TL``/``TC``/``TR``/``ML``/``MC``/``MR``/``BL``/``BC``/``BR``: top,
middle, bottom row; left, centre, right column.  Corners keep their natural
size, the edges stretch along one axis, the centre stretches along both.  Read
off the shipped data rather than assumed -- the sizes only add up that way.

Pillow only; this module is part of the ``image`` extra like the rest of
``edit/atlas``.
"""

from __future__ import annotations

import re

from typing import Dict, Optional, Tuple

VERSION = "1.0"

#: Row-major, the order the tiles are placed in.
SUFFIXES = ("TL", "TC", "TR", "ML", "MC", "MR", "BL", "BC", "BR")

CORNERS = ("TL", "TR", "BL", "BR")


#: ``<base><suffix><digits>.<ext>`` -- the digits are part of the convention,
#: not noise: ``ND_ScreenFrameTL1`` and ``ND_ScreenFrame_02TL1`` are both real
#: families, and requiring the suffix to be last cost 12 of the 135 frames.
_TILE = re.compile(
    r"^(?P<base>.*?)(?P<suffix>TL|TC|TR|ML|MC|MR|BL|BC|BR)(?P<tail>\d*)$",
    re.IGNORECASE,
)


def family_of(source: str) -> Optional[Tuple[str, str, str]]:
    """``'images/ND_FrameTL1.aim'`` -> ``('images/ND_Frame', '1', '.aim')``.

    ``None`` when the name does not carry a tile suffix, which is how a caller
    tells a nine-slice frame from an ordinary sprite.
    """
    stem, _, ext = source.rpartition(".")
    if not stem:
        stem, ext = source, ""
    match = _TILE.match(stem)
    if match is None or not match.group("base"):
        return None
    return match.group("base"), match.group("tail"), ("." + ext if ext else "")


def sibling_names(source: str) -> Optional[Dict[str, str]]:
    """``{suffix: filename}`` for every tile of ``source``'s family."""
    found = family_of(source)
    if found is None:
        return None
    base, tail, ext = found
    return {suffix: f"{base}{suffix}{tail}{ext}" for suffix in SUFFIXES}


def compose(tiles, size):
    """Build a ``size`` image from ``{suffix: PIL image}``.

    Corners at their own size, edges stretched along their axis, the centre
    stretched both ways.  A target smaller than the corners clamps rather than
    raising: layouts really do place a frame in a box narrower than its own
    corners, and refusing to draw that would leave a hole in the preview where
    the game shows something.
    """
    from PIL import Image

    missing = [s for s in SUFFIXES if s not in tiles]
    if missing:
        raise KeyError(f"missing tiles: {', '.join(missing)}")

    width, height = max(int(size[0]), 1), max(int(size[1]), 1)
    left = min(tiles["TL"].width, width)
    right = min(tiles["TR"].width, max(width - left, 0))
    top = min(tiles["TL"].height, height)
    bottom = min(tiles["BL"].height, max(height - top, 0))
    middle_w = max(width - left - right, 0)
    middle_h = max(height - top - bottom, 0)

    out = Image.new("RGBA", (width, height), (0, 0, 0, 0))

    def place(suffix, x, y, w, h):
        if w <= 0 or h <= 0:
            return
        tile = tiles[suffix].convert("RGBA")
        if (tile.width, tile.height) != (w, h):
            tile = tile.resize((w, h), Image.Resampling.NEAREST)
        out.alpha_composite(tile, (x, y))

    place("TL", 0, 0, left, top)
    place("TC", left, 0, middle_w, top)
    place("TR", left + middle_w, 0, right, top)
    place("ML", 0, top, left, middle_h)
    place("MC", left, top, middle_w, middle_h)
    place("MR", left + middle_w, top, right, middle_h)
    place("BL", 0, top + middle_h, left, bottom)
    place("BC", left, top + middle_h, middle_w, bottom)
    place("BR", left + middle_w, top + middle_h, right, bottom)
    return out


__all__ = ["VERSION", "SUFFIXES", "family_of", "sibling_names", "compose"]
