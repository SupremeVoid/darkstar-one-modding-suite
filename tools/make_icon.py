#!/usr/bin/env python3
"""
Generate the application icon from its source image.

    python tools/make_icon.py

WHY THIS IS A SCRIPT
--------------------
``app/dso_app/resources/icon.ico`` is a build product, and a build product with
no generator is one nobody can reproduce or correct. The source of truth is the
PNG beside it.

THE AWKWARD PART
----------------
The source is **33x29** -- a hand-edited copy of the game's own 32x32 icon with
a wrench added. Windows asks for sizes up to 256x256, so most of what goes in
the file has to be invented.

Two things make that tolerable rather than mushy:

* the image is **padded to a square** first, transparently and centred, so
  nothing is stretched -- an icon squashed to fit is worse than one with air
  around it;
* every enlargement goes through a **nearest-neighbour multiply first** and is
  only then resampled down to the target. Scaling 33px straight to 256 with a
  smooth filter turns crisp pixel art into porridge; going 33 -> 264 by exact
  integer steps and back down to 256 keeps the edges.

If a higher-resolution original ever exists, drop it in as ``icon.png`` and
rerun this -- the code needs no change, and the results will simply be better.
"""

from __future__ import annotations

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESOURCES = os.path.join(ROOT, "app", "dso_app", "resources")

#: What Windows looks for. 256 is what Explorer's large view and the Alt-Tab
#: switcher use; 16 and 32 are what everything else actually shows.
SIZES = (16, 24, 32, 48, 64, 128, 256)


def square(image):
    """Pad to a square without stretching, keeping the art centred."""
    side = max(image.size)
    if image.size == (side, side):
        return image
    from PIL import Image

    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(image,
                 ((side - image.width) // 2, (side - image.height) // 2))
    return canvas


def master(image, at_least: int):
    """One crisp enlargement that every size is then derived from.

    Nearest-neighbour by a whole number, so pixel edges stay on pixel
    boundaries and nothing is invented between them.
    """
    from PIL import Image

    factor = max(1, -(-at_least // image.width))      # ceil
    return image.resize((image.width * factor, image.height * factor),
                        Image.Resampling.NEAREST)


def scaled(source, size: int):
    """One icon size, resampled down from the master.

    Every size comes from the *same* enlarged copy rather than from the 33px
    original, which matters most at the small end: resampling 33 -> 16 directly
    has too little to average and comes out muddy, while 264 -> 16 has plenty.
    The 16px icon is the one Windows shows in the title bar and the tray, so it
    is the one worth getting right.
    """
    from PIL import Image

    if source.width == size:
        return source
    return source.resize((size, size), Image.Resampling.LANCZOS)


def main(argv=None) -> int:
    try:
        from PIL import Image
    except ImportError:
        print("Pillow is needed to build the icon:  pip install pillow")
        return 1

    ap = argparse.ArgumentParser(description=__doc__.strip().split("\n")[0])
    ap.add_argument("--source", default=os.path.join(RESOURCES, "icon.png"))
    ap.add_argument("--out", default=os.path.join(RESOURCES, "icon.ico"))
    args = ap.parse_args(argv)

    if not os.path.isfile(args.source):
        print(f"no source image at {args.source}")
        return 2

    source = Image.open(args.source).convert("RGBA")
    print(f"source: {source.width}x{source.height}")
    base = square(source)
    if base.size != source.size:
        print(f"   padded to {base.width}x{base.height} (centred, not stretched)")

    big = master(base, max(SIZES))
    print(f"   enlarged {base.width} -> {big.width} by whole pixels, and every "
          f"size resampled down from that")
    frames = [scaled(big, size) for size in SIZES]
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    frames[-1].save(args.out, format="ICO",
                    sizes=[(s, s) for s in SIZES])

    written = Image.open(args.out)
    print(f"\n{args.out}")
    print(f"   {os.path.getsize(args.out):,} bytes, "
          f"sizes {sorted(s for s, _ in written.ico.sizes())}")
    if source.width < 64:
        print("\n   NOTE: the source is smaller than the largest icon size, so "
              "everything above it is enlarged. A bigger original would look "
              "better and needs no change here.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
