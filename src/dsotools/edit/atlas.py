"""
The TexPage editor: coordinated edits across an atlas and everything that
points into it.

THE PROBLEM THIS SOLVES
-----------------------
Most of the game's interface art is not a standalone file.  ``images\\*.aim``
are the packer's *sources*; at runtime a graphic comes out of an atlas page at a
rectangle recorded in a ``.tex``, and its drawn size is repeated again in a
``.anim``.  Three files, one truth, and nothing keeps them in step.

So "upscale the TexPage" is not one edit.  It is:

    the .aim   -- rescale the pixels
    the .tex   -- rewrite every rectangle
    each .anim -- update the declared width and height

Do two of the three and the UI draws the wrong region of the right image, with
no error anywhere.  :meth:`AtlasPage.rescale` does all three as one operation,
which is the difference between a validator that complains and a tool that
fixes.

WHY save() RETURNS BYTES INSTEAD OF WRITING
-------------------------------------------
Every operation here touches several files at once, and a half-applied rescale
is worse than none.  :meth:`AtlasPage.save` returns ``{vpath: bytes}`` and
writes nothing; the caller commits the whole map atomically -- into
``user_data.zip`` or loose, as ``project.Mod.deploy_target`` decides.  That also
makes every operation testable without a filesystem.

Requires the ``image`` extra (Pillow).
"""

from __future__ import annotations

import posixpath
from typing import Dict, List, Optional, Tuple

from ..errors import DsoError, ParseError, ValidationError
from ..formats import a2d, aim, anim
from .. import vfs as vfsmod

VERSION = "1.0"


def have_pillow() -> bool:
    """Is the ``image`` extra installed?

    Exists so callers can *skip* pixel work rather than provoke an error and
    report it.  ``validate`` used to do the latter: with Pillow absent every
    ``.tex`` in a mod produced a TEX005 error and the mod was declared "not
    deployable" -- for a missing optional dependency, on a default install.
    A check that cannot run has to say so, not fail the thing it was checking.
    """
    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        return False
    return True


def _require_pillow():
    if not have_pillow():
        raise DsoError(
            "Pillow is required for atlas editing; install dsotools[image]"
        )
    from PIL import Image

    return Image


class Sprite:
    """One named graphic inside an atlas page."""

    __slots__ = ("name", "stem", "x", "y", "w", "h", "_sub")

    def __init__(self, sub: "a2d.SubImage") -> None:
        self.name = sub.name
        self.stem = sub.stem
        self.x, self.y, self.w, self.h = sub.x, sub.y, sub.w, sub.h
        self._sub = sub

    @property
    def box(self) -> Tuple[int, int, int, int]:
        return (self.x, self.y, self.x + self.w, self.y + self.h)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Sprite {self.stem} {self.w}x{self.h} at ({self.x},{self.y})>"


class AtlasPage:
    """A ``.tex`` index, the ``.aim`` page it names, and its dependent ``.anim``s."""

    def __init__(self, tex_path, index, page_path, image, anims=None, source=None):
        self.tex_path = tex_path
        self.index = index                 # a2d.TexturePage
        self.page_path = page_path         # vpath of the .aim
        self.image = image                 # PIL Image, RGBA
        #: The page's original :class:`aim.AimImage`.  Kept so :meth:`save` can
        #: re-encode the way the asset was encoded rather than imposing this
        #: module's defaults -- see :func:`aim.from_image_like`.
        self.source = source
        #: Encoding to use when the original's cannot be written.  ``None``
        #: refuses instead.  Default on, because the alternative is that two of
        #: the ten shipped atlas pages can never be edited; :attr:`recoded_to`
        #: is how the caller finds out it happened.
        self.fallback_encoding = aim.FALLBACK_ENCODING
        #: ``{vpath: anim.Anim}`` for drawables naming a sprite on this page.
        self.anims: Dict[str, "anim.Anim"] = anims or {}
        self._page_dirty = False
        self._index_dirty = False
        self._dirty_anims: set = set()

    # -- loading -------------------------------------------------------------

    @classmethod
    def open(cls, vfs: "vfsmod.Vfs", tex_path: str, *, load_anims: bool = True) -> "AtlasPage":
        """Load a page and everything bound to it.

        ``load_anims`` scans the same folder as the ``.tex`` for drawables
        naming this page's sprites.  It is the expensive part, and it is what
        makes :meth:`rescale` complete.
        """
        _require_pillow()        # fail here, not deep inside the page load

        index = a2d.parse(vfs.read(tex_path))
        page_ref = index.page.replace("\\", "/")
        entry = vfs.resolve_reference(page_ref, scene_path=tex_path, base="")
        if entry is None:
            entry = vfs.find(page_ref)
        if entry is None:
            raise ParseError(
                f"{tex_path} names a page that does not exist: {index.page}",
                path=tex_path,
                code="TEX006",
            )
        source = aim.parse(entry.read())
        image = aim.to_image(source).convert("RGBA")

        anims: Dict[str, anim.Anim] = {}
        if load_anims:
            stems = {s.stem.lower() for s in index.subimages}
            folder = posixpath.dirname(vfsmod.normalise(tex_path))
            for vpath in vfs.iter_paths():
                if not vpath.lower().endswith(".anim"):
                    continue
                if posixpath.dirname(vpath.lower()) != folder.lower():
                    continue
                stem = posixpath.basename(vpath)[:-5].lower()
                if stem in stems:
                    try:
                        anims[vpath] = anim.parse(vfs.read(vpath), path=vpath)
                    except DsoError:
                        continue
        return cls(tex_path, index, entry.vpath, image, anims, source=source)

    # -- inspection ----------------------------------------------------------

    @property
    def sprites(self) -> List[Sprite]:
        return [Sprite(s) for s in self.index.subimages]

    def sprite(self, name: str) -> Sprite:
        low = name.lower()
        for s in self.index.subimages:
            if s.stem.lower() == low or s.name.lower() == low:
                return Sprite(s)
        raise KeyError(f"{name!r} is not on {self.index.page}")

    @property
    def size(self) -> Tuple[int, int]:
        return self.image.size

    @property
    def page_encoding(self) -> str:
        """How the page is stored on disk, e.g. ``IMTC32`` or ``IMJPG24A``."""
        if self.source is None or not self.source.tiles:
            return ""
        return self.source.tiles[0].encoding.strip()

    @property
    def recoded_to(self) -> Optional[str]:
        """The encoding :meth:`save` will substitute, or ``None`` if it keeps it.

        Saving a page whose codec this library cannot write is allowed, but it
        is not a detail to swallow: the file stops matching stock byte-for-byte
        and stops matching its own siblings, so the caller should say so.
        """
        enc = self.page_encoding
        if not enc or enc in aim.WRITABLE:
            return None
        return self.fallback_encoding

    def extract(self, name: str):
        """Crop one sprite out of the page."""
        return self.image.crop(self.sprite(name).box)

    # -- checks --------------------------------------------------------------

    def out_of_bounds(self) -> List[Sprite]:
        """TEX002: rectangles that fall outside the page."""
        w, h = self.image.size
        return [s for s in self.sprites if s.x < 0 or s.y < 0 or s.x + s.w > w or s.y + s.h > h]

    def overlaps(self) -> List[Tuple[Sprite, Sprite]]:
        """TEX003: pairs of rectangles that intersect.

        Sorted-sweep rather than the obvious O(n^2): a page has up to 331
        sprites and this runs on every edit.
        """
        out = []
        items = sorted(self.sprites, key=lambda s: (s.y, s.x))
        for i, a in enumerate(items):
            for b in items[i + 1 :]:
                if b.y >= a.y + a.h:
                    break
                if a.x < b.x + b.w and b.x < a.x + a.w:
                    out.append((a, b))
        return out

    def anim_mismatches(self) -> List[Tuple[str, Tuple[int, int], Tuple[int, int]]]:
        """TEX004: a drawable whose **source size** disagrees with its rectangle.

        The source size, not the drawn size.  A stretched nine-slice frame is
        drawn far larger than the corner tile it names, and comparing the drawn
        size here reported 36 of Ascaron's own drawables as broken.
        """
        by_stem = {s.stem.lower(): s for s in self.sprites}
        out = []
        for vpath, a in self.anims.items():
            stem = posixpath.basename(vpath)[:-5].lower()
            sp = by_stem.get(stem)
            if sp and a.source_size != (sp.w, sp.h):
                out.append((vpath, a.source_size, (sp.w, sp.h)))
        return out

    # -- editing -------------------------------------------------------------

    def replace(self, name: str, image, *, allow_resize: bool = False) -> None:
        """Composite a new image over one sprite's rectangle.

        Dimensions are locked by default.  The rectangle is fixed by the
        ``.tex``, so a larger replacement would silently overwrite whatever sits
        next to it -- the neighbour's own graphic.  ``allow_resize`` opts into
        rewriting the rectangle instead, and then re-checks for collisions.
        """
        sp = self.sprite(name)
        if image.size != (sp.w, sp.h) and not allow_resize:
            raise ValidationError(
                f"{sp.stem} is {sp.w}x{sp.h} on the page but the replacement is "
                f"{image.size[0]}x{image.size[1]}; pass allow_resize=True to "
                f"rewrite the rectangle, which may collide with its neighbours",
                path=self.page_path,
                code="TEX001",
            )
        if image.mode != "RGBA":
            image = image.convert("RGBA")

        if image.size != (sp.w, sp.h):
            sp._sub.w, sp._sub.h = image.size
            self._index_dirty = True
            self._sync_anim(sp.stem, image.size)
            clashes = [(a, b) for a, b in self.overlaps() if sp.stem in (a.stem, b.stem)]
            if clashes:
                raise ValidationError(
                    f"resizing {sp.stem} to {image.size[0]}x{image.size[1]} would "
                    f"overlap {clashes[0][1].stem}",
                    path=self.tex_path,
                    code="TEX003",
                )

        self.image.paste(image, (sp._sub.x, sp._sub.y))
        self._page_dirty = True

    def rescale(self, factor: float, *, resample=None) -> "AtlasPage":
        """Scale the page **and** every rectangle **and** every drawable.

        This is the operation behind the "you upscaled the page but did not
        update the coordinates" failure.  Rather than warn about it, do it
        properly: one call, one undo step, three file types kept consistent.

        Rectangles are scaled by rounding the edges rather than the origin and
        size independently, so adjacent sprites stay adjacent and no one-pixel
        gaps or overlaps appear along a shared border.
        """
        Image = _require_pillow()
        if factor <= 0:
            raise ValueError("factor must be positive")
        if resample is None:
            resample = Image.LANCZOS

        w, h = self.image.size
        new_size = (max(1, round(w * factor)), max(1, round(h * factor)))
        self.image = self.image.resize(new_size, resample)
        self._page_dirty = True

        for sub in self.index.subimages:
            left = round(sub.x * factor)
            top = round(sub.y * factor)
            right = round((sub.x + sub.w) * factor)
            bottom = round((sub.y + sub.h) * factor)
            sub.x, sub.y = left, top
            sub.w, sub.h = max(1, right - left), max(1, bottom - top)
            self._sync_anim(sub.stem, (sub.w, sub.h))
        self._index_dirty = True
        return self

    def _sync_anim(self, stem: str, size: Tuple[int, int]) -> None:
        """Point a drawable at its rectangle's new size.

        The rectangle *is* the source image, so it sets the source size.  The
        drawn size moves with it **in proportion**, which for an ordinary
        drawable -- where the two are equal -- is exactly the old behaviour, and
        for a stretched frame keeps the frame's proportions instead of
        collapsing it onto its corner tile.
        """
        for vpath, a in self.anims.items():
            if posixpath.basename(vpath)[:-5].lower() != stem.lower():
                continue
            old_source = a.source_size
            if old_source == size and a.size == size:
                continue
            drawn = a.size
            if old_source[0] and old_source[1]:
                drawn = (
                    max(1, round(drawn[0] * size[0] / old_source[0])),
                    max(1, round(drawn[1] * size[1] / old_source[1])),
                )
            else:                       # nothing to scale from; follow directly
                drawn = size
            a.set_source_size(*size)
            a.set_size(*drawn)
            self._dirty_anims.add(vpath)

    # -- output --------------------------------------------------------------

    def save(self) -> Dict[str, bytes]:
        """Every file this edit changed, as ``{vpath: bytes}``.

        Nothing is written.  The caller commits the whole map at once, because a
        page rescaled without its index is worse than one not rescaled at all.
        """
        out: Dict[str, bytes] = {}
        if self._page_dirty:
            # from_image_like, not from_image: the page keeps its own encoding,
            # flags and footer.  Writing a BMPRES page back as IMTC32 produced a
            # file the game silently ignored, and the default footer_extra
            # (0,0,0) replaced the (0,0,1) every shipped page carries.
            if self.source is not None:
                out[self.page_path] = aim.from_image_like(
                    self.source, self.image, fallback=self.fallback_encoding
                )
            else:
                out[self.page_path] = aim.from_image(self.image)
        if self._index_dirty:
            out[self.tex_path] = a2d.build(self.index)
        for vpath in sorted(self._dirty_anims):
            out[vpath] = self.anims[vpath].to_bytes()
        return out

    @property
    def dirty(self) -> bool:
        return bool(self._page_dirty or self._index_dirty or self._dirty_anims)

    def __repr__(self) -> str:  # pragma: no cover
        w, h = self.image.size
        return (
            f"<AtlasPage {self.index.page} {w}x{h}, "
            f"{len(self.index.subimages)} sprites, {len(self.anims)} anims>"
        )


__all__ = ["VERSION", "AtlasPage", "Sprite"]
