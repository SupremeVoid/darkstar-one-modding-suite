"""
Virtual file system: the game's assets as one namespace.  v1.0

WHY THIS IS NOT JUST A DICT OF PATHS
------------------------------------
Darkstar One does not have one asset tree per archive.  It has *one* tree that
several archives contribute to at once:

    ds_3dgen  ->  3DView/*.xml, 3DView/blender/, 3DView/lua/, animations/
    ds_3dobj  ->  3DView/objects/*.3do, *.shd
    ds_3dtex  ->  3DView/textures/, ClusterTextures/, Textures_Planets_*/
    ds_3dadd  ->  per-scene overlays
    ds_add    ->  inifiles/, scripts/, more overlays

Measured on the shipped data: 11,386 distinct virtual paths across six
archives, of which **1,368 exist in more than one archive**.
``3DView/BlackHole.xml`` is in three.  So "which copy wins" is a real question
with a real answer, and getting it wrong means the app shows the user an asset
the game will not load.

That is what :class:`Vfs` is: an ordered stack of layers, a case-insensitive
lookup, and -- crucially -- a record of *which layer answered*, because
"where did this come from?" is a feature of the UI, not a debugging aid.

THE ARCHIVES ARE ZIP FILES
--------------------------
``.cpr`` is a plain ZIP container -- ``PK\x03\x04``, deflate, standard central
directory, even UNIX timestamp extra fields.  All six shipped archives open with
``zipfile`` in about 20 ms and hold 12,767 files between them.

That means :func:`from_install` can serve a game installation directly and no
user ever has to extract anything.  :func:`from_extracted` remains for people
who already have an extracted tree, and for tests.

LOAD ORDER
----------
Highest priority first, per ``specs/3do_shd.md`` plus this project's own
measurements:

  1. Loose files in the game install directory
  2. ``user_data.zip`` in the active mod's root
  3. The ``.cpr`` archives
  4. A loose folder in the mod directory  -- believed never loaded

**Rule 4 is confirmed.**  Tested in game with four probe mods carrying the same
file at the same virtual path, differing only in delivery:

===========================  ==========================  ==================
probe                        via ``user_data.zip``       loose in mod root
===========================  ==========================  ==================
replaced hull texture        hull renders magenta        no change
deliberately broken scene    game crashes on load        loads fine
===========================  ==========================  ==================

The second row is the decisive one: a scene XML broken on purpose crashes the
game when delivered in the zip and does nothing at all when delivered loose.
A crash cannot be mistaken for "loaded but looks the same", so this
distinguishes *not read* from *read with no visible effect*.  It also extends
the original finding, which concerned ``3DView\\objects\\``: **the whole
``3DView/`` subtree is ignored in a mod's loose tree**, textures included.

Ascaron's own tutorial mod ships a loose ``3DView/`` and no ``user_data.zip``,
so that folder never loads either -- it is the authoring workspace for
``3doConv.exe``, not runtime content.  The apparent conflict is resolved.

The loose tree is still registered as a real (lowest-priority, ``loaded=False``)
layer, because the app has to *warn* about files sitting in it.

Archive precedence is also settled, by the same Process Monitor capture -- see
:data:`DEFAULT_ARCHIVE_ORDER`.  :meth:`Vfs.contested` still reports the 16 paths
whose copies differ, because "which archive did this come from" remains worth
showing even when the winner is known.

CASE
----
Windows is case-insensitive; the archives are not internally consistent about
case (``3DView`` vs ``3dview``, ``TexPage_8_2.aim`` vs lowercase references).
Lookups fold case.  The *original* spelling is preserved on every entry, because
that is what has to be written back out.
"""

from __future__ import annotations

import os
import posixpath
import zipfile
from typing import Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

from .errors import ResolutionError

VERSION = "1.0"

#: Archive precedence, highest priority first.
#:
#: **Measured**, not guessed.  Process Monitor on ``DarkStarOne.exe`` shows the
#: resolver probing a loose install path first and then each archive in a fixed
#: sequence, repeated verbatim for every resource:
#:
#:     <game>\3DView\...            NAME/PATH NOT FOUND
#:     <game>\3DView\objects        SUCCESS      (directory probe)
#:     ds_add.cpr                   SUCCESS
#:     ds_3dadd.cpr                 SUCCESS
#:     ds_3dtex.cpr                 SUCCESS
#:     ds_3dobj.cpr                 SUCCESS
#:     ds_3dgen.cpr                 SUCCESS
#:     ds_interface.cpr             SUCCESS
#:
#: That is the exact reverse of the mount order observed at startup
#: (ds_interface, ds_3dgen, ds_3dobj, ds_3dtex, ds_3dadd, ds_add) -- a LIFO
#: registry, so an archive mounted later overrides one mounted earlier.  It
#: confirms the add-on archives outrank ``ds_3dgen``, which is what the 16
#: contested files needed (see :meth:`Vfs.contested`).
#:
#: The six above are observed.  ``ds_patch``, ``ds_loca`` and ``ds_main`` are
#: not present in the Steam build that was captured; they are placed by
#: inference from the same LIFO rule and are marked as such.
DEFAULT_ARCHIVE_ORDER: Tuple[str, ...] = (
    # --- inferred (absent from the captured install) ---
    "ds_patch",
    # --- measured, highest priority first ---
    "ds_add",
    "ds_3dadd",
    "ds_3dtex",
    "ds_3dobj",
    "ds_3dgen",
    "ds_interface",
    # --- inferred ---
    "ds_loca",
    "ds_main",
)

#: The archive mount sequence seen at startup, in the order opened.  Precedence
#: is the reverse of this.  Kept separate from :data:`DEFAULT_ARCHIVE_ORDER` so
#: the observation and the derived rule stay distinguishable.
OBSERVED_MOUNT_ORDER: Tuple[str, ...] = (
    "ds_interface",
    "ds_3dgen",
    "ds_3dobj",
    "ds_3dtex",
    "ds_3dadd",
    "ds_add",
)


def normalise(path: str) -> str:
    """Fold a reference to the canonical virtual form.

    Backslashes become forward slashes (scene XML is overwhelmingly ``/`` but
    one large third-party mod contains exactly one ``\\`` reference out of
    21,605 -- portable resolution has to cope), redundant ``.`` and ``//`` are
    collapsed, and leading separators are dropped.
    """
    p = path.replace("\\", "/").strip()
    while p.startswith("./"):
        p = p[2:]
    p = posixpath.normpath(p) if p else p
    return p.lstrip("/")


def _key(path: str) -> str:
    return normalise(path).lower()


class Entry:
    """One resolved file."""

    __slots__ = ("vpath", "layer", "_source", "_ref", "size")

    def __init__(self, vpath: str, layer: "Layer", ref, size: int) -> None:
        #: Virtual path in its original spelling.
        self.vpath = vpath
        self.layer = layer
        self._ref = ref
        self.size = size

    def read(self) -> bytes:
        return self.layer.read_ref(self._ref)

    @property
    def origin(self) -> str:
        """Human-readable source, e.g. ``cpr:ds_3dobj`` or ``mod:user_data.zip``."""
        return self.layer.name

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Entry {self.vpath} from {self.origin}>"


class Layer:
    """One contributor to the namespace.  Subclasses supply the I/O."""

    def __init__(self, name: str, *, priority: int = 0, loaded: bool = True) -> None:
        self.name = name
        self.priority = priority
        #: ``False`` marks a layer the *game* does not read even though it
        #: exists on disk.  It stays in the VFS so the app can warn about it.
        self.loaded = loaded
        self._index: Dict[str, Tuple[str, object, int]] = {}

    def read_ref(self, ref) -> bytes:  # pragma: no cover - abstract
        raise NotImplementedError

    def index(self) -> Dict[str, Tuple[str, object, int]]:
        return self._index

    def close(self) -> None:
        """Release any OS handle this layer holds.  No-op unless overridden.

        Defined on the base so callers can close a heterogeneous stack of
        layers without type-testing each one.  It matters for
        :class:`ZipLayer`, which holds an archive open, and a caller that
        forgets cannot replace that archive on Windows.
        """

    def __repr__(self) -> str:  # pragma: no cover
        return f"<{type(self).__name__} {self.name} files={len(self._index)}>"


class DirectoryLayer(Layer):
    """A directory tree on disk."""

    def __init__(
        self,
        root: str,
        name: Optional[str] = None,
        *,
        priority: int = 0,
        loaded: bool = True,
        skip: Sequence[str] = (),
        only: Optional[Sequence[str]] = None,
    ) -> None:
        """``skip`` drops top-level directories; ``only`` keeps just those.

        ``only`` exists because a mod's loose tree is read by the engine
        everywhere *except* ``3DView/``, so it has to be mounted as two layers
        over the same root -- one loaded, one not -- and the paths in both must
        still be relative to the mod root.  Rooting a layer at
        ``<mod>/3DView`` instead would index ``x.xml`` where the rest of the
        app expects ``3DView/x.xml``.
        """
        super().__init__(name or os.path.basename(root.rstrip("/\\")) or root, priority=priority, loaded=loaded)
        self.root = root
        skip_lower = {s.lower() for s in skip}
        only_lower = {s.lower() for s in only} if only is not None else None
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d.lower() not in skip_lower]
            if only_lower is not None:
                rel_dir = os.path.relpath(dirpath, root).replace(os.sep, "/")
                if rel_dir == ".":
                    # Keep walking only into the named roots, and take no file
                    # sitting loose at the top level.
                    dirnames[:] = [d for d in dirnames if d.lower() in only_lower]
                    continue
            for fn in filenames:
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, root).replace(os.sep, "/")
                try:
                    size = os.path.getsize(full)
                except OSError:
                    continue
                self._index[_key(rel)] = (normalise(rel), full, size)

    def read_ref(self, ref) -> bytes:
        with open(ref, "rb") as fh:
            return fh.read()


class ZipLayer(Layer):
    """A zip archive -- in practice a mod's ``user_data.zip``."""

    def __init__(
        self, path: str, name: Optional[str] = None, *, priority: int = 0, loaded: bool = True
    ) -> None:
        super().__init__(name or os.path.basename(path), priority=priority, loaded=loaded)
        self.path = path
        self._zf = zipfile.ZipFile(path)
        for zi in self._zf.infolist():
            if zi.is_dir():
                continue
            rel = zi.filename.replace("\\", "/")
            self._index[_key(rel)] = (normalise(rel), zi, zi.file_size)

    def read_ref(self, ref) -> bytes:
        return self._zf.read(ref)

    def close(self) -> None:
        self._zf.close()


class Vfs:
    """An ordered stack of layers presenting one asset namespace."""

    def __init__(self, layers: Iterable[Layer] = ()) -> None:
        self._layers: List[Layer] = []
        for layer in layers:
            self.add(layer)

    # -- construction --------------------------------------------------------

    def add(self, layer: Layer) -> "Vfs":
        """Add a layer.  Layers are kept sorted by descending priority."""
        self._layers.append(layer)
        self._layers.sort(key=lambda ly: -ly.priority)
        return self

    @property
    def layers(self) -> List[Layer]:
        return list(self._layers)

    # -- lookup --------------------------------------------------------------

    def find(self, path: str, *, include_unloaded: bool = False) -> Optional[Entry]:
        """Resolve one virtual path, or ``None``.

        ``include_unloaded`` brings in layers the game does not read.  Off by
        default: the app must show the user what the *game* sees, not what the
        filesystem contains.
        """
        k = _key(path)
        for layer in self._layers:
            if not layer.loaded and not include_unloaded:
                continue
            hit = layer.index().get(k)
            if hit is not None:
                vpath, ref, size = hit
                return Entry(vpath, layer, ref, size)
        return None

    def exists(self, path: str, *, include_unloaded: bool = False) -> bool:
        return self.find(path, include_unloaded=include_unloaded) is not None

    def read(self, path: str, *, include_unloaded: bool = False) -> bytes:
        entry = self.find(path, include_unloaded=include_unloaded)
        if entry is None:
            raise ResolutionError(
                f"not found in any layer: {path}",
                reference=path,
                tried=[ly.name for ly in self._layers],
                code="VFS001",
            )
        return entry.read()

    def candidates(self, path: str) -> List[Entry]:
        """Every layer holding this path, in priority order.

        The first is what the game loads; the rest are shadowed.  This is what
        the "load order" panel renders.
        """
        k = _key(path)
        out = []
        for layer in self._layers:
            hit = layer.index().get(k)
            if hit is not None:
                vpath, ref, size = hit
                out.append(Entry(vpath, layer, ref, size))
        return out

    def iter_paths(self, *, include_unloaded: bool = False) -> Iterator[str]:
        seen = set()
        for layer in self._layers:
            if not layer.loaded and not include_unloaded:
                continue
            for k, (vpath, _ref, _size) in layer.index().items():
                if k not in seen:
                    seen.add(k)
                    yield vpath

    def __len__(self) -> int:
        return sum(1 for _ in self.iter_paths())

    # -- reference resolution ------------------------------------------------

    def resolve_reference(
        self, reference: str, *, scene_path: Optional[str] = None, base: str = "3DView"
    ) -> Optional[Entry]:
        """Resolve a scene-internal reference such as ``objects/main_.3do``.

        Order, established by measurement (``specs/scene.md`` §4):

        1. relative to the referencing scene's own directory
        2. relative to ``base`` (``3DView/``)

        Applying (1) before (2) took model resolution from 90.6% to 97.2% on
        the stock corpus, because scenes with private asset folders
        (``3DView/Generator/objects/...``) depend on it entirely.
        """
        for cand in self.reference_candidates(reference, scene_path=scene_path, base=base):
            entry = self.find(cand)
            if entry is not None:
                return entry
        return None

    def reference_candidates(
        self, reference: str, *, scene_path: Optional[str] = None, base: str = "3DView"
    ) -> List[str]:
        """The paths :meth:`resolve_reference` will try, in order.

        Exposed so a failure can tell the user exactly where it looked.
        """
        ref = normalise(reference)
        out: List[str] = []
        if scene_path:
            scene_dir = posixpath.dirname(normalise(scene_path))
            if scene_dir:
                out.append(posixpath.join(scene_dir, ref))
        if base:
            out.append(posixpath.join(normalise(base), ref))
        out.append(ref)
        seen = set()
        return [c for c in out if not (c.lower() in seen or seen.add(c.lower()))]

    def reference_for(
        self, vpath: str, *, scene_path: Optional[str] = None,
        base: str = "3DView", strict: bool = False
    ) -> Optional[str]:
        """How ``scene_path`` must spell ``vpath`` to reach it.

        The inverse of :meth:`resolve_reference`, and the reason it has to
        exist: **a scene does not name its assets by virtual path.**  It writes
        ``textures/x.dds``, resolved first against its own directory and only
        then against ``3DView/``.  Writing a vpath into a scene therefore
        produces a reference that resolves to nothing, or -- worse -- to a
        different file that happens to sit at that name.

        Every candidate spelling is checked by **resolving it back**.  A
        shorter form is preferred, but only when it actually returns the file
        asked for: a texture in the scene's own folder can be shadowed by one
        of the same name under ``3DView/``, and in that case the short spelling
        silently means something else.

        ``None`` when no spelling reaches ``vpath`` from this scene, which is a
        real answer and not a failure -- an asset outside both search roots
        cannot be named from here at all, and a caller must refuse rather than
        write something that does not resolve.

        ``strict`` drops the bare-path candidate that :meth:`resolve_reference`
        tries last.  That candidate is a convenience of *this* reader, not a
        measured engine rule: across all 1,006 stock scenes, **0 of 45,322
        resolving references need it** -- every one resolves under ``3DView/``
        or the scene's own folder.  So a reader may accept it, and anything
        that **writes** a reference into a scene must not, or the result works
        in this app and resolves to nothing in game.
        """
        want = _key(vpath)
        target = self.find(vpath)
        if target is None:
            return None

        ref = normalise(vpath)
        candidates: List[str] = []
        if scene_path:
            scene_dir = posixpath.dirname(normalise(scene_path))
            if scene_dir and (ref.lower() + "/").startswith(scene_dir.lower() + "/"):
                candidates.append(ref[len(scene_dir) + 1:])
        base_dir = normalise(base) if base else ""
        if base_dir and (ref.lower() + "/").startswith(base_dir.lower() + "/"):
            candidates.append(ref[len(base_dir) + 1:])
        if not strict:
            candidates.append(ref)

        for cand in candidates:
            if not cand:
                continue
            back = self.resolve_reference(cand, scene_path=scene_path, base=base)
            if back is not None and _key(back.vpath) == want:
                return cand
        return None

    # -- diagnostics ---------------------------------------------------------

    def ambiguous(self) -> Dict[str, List[str]]:
        """Paths present in more than one *loaded* layer.

        Maps a virtual path to the layer names holding it, in precedence order.
        Note this is the *shadowing* set, not the *problem* set -- on stock data
        it is 1,368 paths, of which only 16 actually matter.  Use
        :meth:`contested` for the set worth showing a user.
        """
        counts: Dict[str, List[str]] = {}
        for layer in self._layers:
            if not layer.loaded:
                continue
            for k, (_vpath, _r, _s) in layer.index().items():
                counts.setdefault(k, []).append(layer.name)
        return {k: v for k, v in counts.items() if len(v) > 1}

    def contested(self) -> Dict[str, List[Entry]]:
        """Shadowed paths whose copies actually **differ in content**.

        This is the set where :data:`DEFAULT_ARCHIVE_ORDER` -- which is a
        reasoned guess, not a verified fact -- changes what the user sees.

        Measured on the shipped archives: of 1,368 shadowed paths, **1,352 are
        byte-identical** and only **16 differ**.  Reporting the 1,368 would be
        noise that trains people to ignore the warning; reporting the 16 is
        actionable.  That reduction is the whole point of this method, and it is
        why the app does not need the precedence question answered to be
        correct -- it can show both copies and say so.

        Cost is bounded: only shadowed paths are read, and differing sizes
        short-circuit before hashing.
        """
        import hashlib

        out: Dict[str, List[Entry]] = {}
        for key in self.ambiguous():
            entries = self.candidates(key)
            if len({e.size for e in entries}) > 1:
                out[key] = entries
                continue
            digests = set()
            for e in entries:
                try:
                    digests.add(hashlib.sha1(e.read()).digest())
                except OSError:
                    digests.add(None)
            if len(digests) > 1:
                out[key] = entries
        return out


# --------------------------------------------------------------------------
# convenience builders
# --------------------------------------------------------------------------


def from_install(
    game_dir: str,
    *,
    order: Sequence[str] = DEFAULT_ARCHIVE_ORDER,
    include_loose: bool = True,
) -> Vfs:
    """Build a VFS straight from a game installation.  **No extraction needed.**

    The ``.cpr`` archives are ordinary ZIP files -- ``PK\x03\x04``, deflate,
    standard central directory.  Nothing about them is proprietary, so the app
    reads the installed game directly instead of asking the user to extract
    12,767 files first.

    Layer order reproduces the engine's, which was measured with Process Monitor
    (see :data:`DEFAULT_ARCHIVE_ORDER`): loose files in the install directory
    outrank every archive, then ``ds_add`` down to ``ds_interface``.

    ``include_loose`` mounts the install folder itself.  Leave it on: loose
    overrides are the documented mechanism and the engine probes them first.
    """
    if not os.path.isdir(game_dir):
        raise ResolutionError(f"not a directory: {game_dir}", code="VFS002")

    archives = sorted(
        f for f in os.listdir(game_dir) if f.lower().endswith(".cpr")
    )
    if not archives:
        raise ResolutionError(
            f"no .cpr archives in {game_dir} -- is this the game install folder?",
            code="VFS003",
        )

    vfs = Vfs()
    rank = {n.lower(): i for i, n in enumerate(order)}
    for name in archives:
        stem = os.path.splitext(name)[0]
        idx = rank.get(stem.lower(), len(order) + archives.index(name))
        vfs.add(
            ZipLayer(
                os.path.join(game_dir, name),
                name=f"cpr:{stem}",
                priority=100 - idx,
            )
        )
    if include_loose:
        # Loose files beat every archive.  Skip the archives themselves and the
        # bulky media folders, which contain nothing the tools address and would
        # otherwise dominate an index scan.
        vfs.add(
            DirectoryLayer(
                game_dir,
                name="install",
                priority=1000,
                skip=("video", "voice", "sound", "DirectX", "html"),
            )
        )
    return vfs


def from_extracted(
    root: str, *, order: Sequence[str] = DEFAULT_ARCHIVE_ORDER, base_priority: int = 100
) -> Vfs:
    """Build a VFS from a folder of extracted ``.cpr`` archives.

    ``root`` holds one directory per archive (``ds_3dobj/``, ``ds_3dtex/``, ...),
    which is what every existing extraction tool produces.  Archives named in
    ``order`` are prioritised accordingly; anything unrecognised sorts last but
    keeps a stable, alphabetical position rather than a random one.
    """
    vfs = Vfs()
    names = sorted(
        d for d in os.listdir(root) if os.path.isdir(os.path.join(root, d))
    )
    rank = {n.lower(): i for i, n in enumerate(order)}
    for name in names:
        idx = rank.get(name.lower(), len(order) + names.index(name))
        vfs.add(
            DirectoryLayer(
                os.path.join(root, name),
                name=f"cpr:{name}",
                priority=base_priority - idx,
            )
        )
    return vfs


def add_install(vfs: Vfs, install_root: str, *, priority: int = 1000) -> Vfs:
    """Add the game install directory as the highest-priority stock layer.

    Loose files in the install outrank every archive -- this is the documented
    override mechanism and the one that actually works reliably.
    """
    vfs.add(DirectoryLayer(install_root, name="install", priority=priority))
    return vfs


__all__ = [
    "VERSION",
    "DEFAULT_ARCHIVE_ORDER",
    "Vfs",
    "Layer",
    "DirectoryLayer",
    "ZipLayer",
    "Entry",
    "normalise",
    "from_extracted",
    "from_install",
    "add_install",
]

# ``add_mod`` used to live here and was never called.  It carried its own copy
# of "which mod folders are dead loose" -- frozen at ``("3DView",)``, so it had
# been wrong since ``images`` was established and would have been wrong again
# for ``staticImages``.  :func:`dsotools.project.iter_mod_layers` is the one
# that builds a mod's layers, and it reads ``ZIP_ONLY_ROOTS`` rather than
# repeating it.
