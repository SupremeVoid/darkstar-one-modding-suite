"""
Mod projects: what a mod contains, how it differs from stock, where it deploys.

A mod lives in
``Documents\\Ascaron Entertainment\\Darkstar One\\Customization\\<Name>\\``
and the game reads exactly four things from it:

    darkstarmod.ini            the manifest -- mod_name, mod_desc
    user_data.zip              3DView/ and images/ content; the ONLY route for
                               scenes, models, textures and atlas pages
    inifiles/, scripts/,
    strings/, sound/           read loose

Two engine rules drive most of this module, both established by experiment and
recorded in ``specs/scene.md`` §6b:

* A mod **without ``inifiles/items.ini`` is silently invisible** -- it does not
  appear in the game's mod list and nothing is reported.
* A mod's **loose ``3DView/`` and ``images/`` are never read.**  Files there are
  dead, and editing them appears to do nothing, which is the failure this whole
  toolchain exists to prevent.  Both were established by putting the same file
  in both places and looking at the screen.

Nothing here imports Qt.  The GUI is one consumer; the CLI is another.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import weakref
import zipfile
from typing import Dict, Iterator, List, Optional, Sequence, Tuple

from .errors import DsoError, ProjectError
from .formats import ini as iniformat
from . import vfs as vfsmod
from . import rootfiles

VERSION = "1.0"

MANIFEST = "darkstarmod.ini"
MANIFEST_SECTION = "darkstarmod"
USER_DATA = "user_data.zip"

#: Sits one level *above* the Customization folder and names the selected mod
#: by folder name (``load_mod = original`` means none).  That it keys on the
#: folder rather than the display name is why renaming is a separate operation.
MOD_SELECTION = "mod.ini"
MOD_SELECTION_SECTION = "DarkstarOne"
MOD_SELECTION_NONE = "original"

#: Presence of this file is what makes the game list a mod at all.
VALIDITY_TOKEN = "inifiles/items.ini"

#: Paths inside a mod folder that are not mod *content* and never appear in
#: :meth:`Mod.files`.  The manifest and the archive are the mod's own
#: packaging; ``.dsoproj`` is this tool's sidecar and the game never reads it.
#: Kept lowercase, and compared lowercased.
_NOT_CONTENT = frozenset({MANIFEST.lower(), USER_DATA.lower(), ".dsoproj"})

#: Top-level folders the engine is **confirmed** to read loose from a mod.
#: ``staticImages`` is *not* in this list: it has never been tested, and
#: ``images`` was in it until an in-game test showed it is not read loose.
LOOSE_ROOTS = ("inifiles", "scripts", "strings", "sound")

#: Content here only loads from ``user_data.zip``.
#:
#: ``images`` joined ``3DView`` on 2026-08-15, established the same way and just
#: as decisively: an edited atlas page deployed loose did nothing in game, and
#: the identical file placed in ``user_data.zip`` appeared immediately.  It is
#: the whole reason the Textures tab's first real edit looked like a no-op.
#:
#: ``staticImages`` joined them on 2026-08-23, by the same experiment run both
#: ways at once: an edited ``staticImages\Starmap.dds`` shipped loose did
#: nothing, and the identical file inside ``user_data.zip`` appeared.  Until
#: then it was carried as **untested** rather than assumed loose -- which is
#: what makes the correction a one-line change here instead of a hunt, and
#: which is just as well, because the assumption turned out to be wrong.
#:
#: These are listed separately from ``LOOSE_ROOTS`` on purpose -- "not
#: confirmed loose" and "confirmed zip-only" are different claims, and treating
#: the first as the second is exactly the over-generalisation that broke
#: ``iter_mod_layers``.
ZIP_ONLY_ROOTS = ("3DView", "images", "staticImages")

#: Never part of the mod proper; the author's own data.
IGNORED_ROOTS = ("save", "screenshots")

#: Every top-level folder a mod may put content in, loose or zipped.
#:
#: This is the delivery matrix in one tuple (``specs/mod_packaging.md`` §1),
#: and it is what makes "add a file at a path the game has never had" a
#: checkable request rather than a leap of faith.  A folder outside it is read
#: by nothing: the engine looks in these and nowhere else inside a mod, so a
#: file placed anywhere else is the silent no-op this whole project exists to
#: prevent.
#:
#: ``root/`` is the exception that proves it -- ``dsotools.rootfiles`` uses it
#: to stage files destined for the **game installation**, which is a different
#: mechanism with its own backup and restore, not mod content.
MOD_CONTENT_ROOTS = tuple(sorted(LOOSE_ROOTS + ZIP_ONLY_ROOTS, key=str.lower))


def check_mod_path(vpath: str) -> Optional[str]:
    """``None`` if a mod may hold ``vpath``, else why it may not.

    Asked *before* a file is written rather than after, because every answer
    here describes a mistake that is invisible once made: a file in a folder
    the engine does not read looks exactly like a file that does not work.

    Deliberately about the **path**, not the format.  Whether the bytes are a
    usable ``.dds`` is a different question with a different answer, asked
    elsewhere.
    """
    raw = (vpath or "").strip()
    if not raw:
        return "No path given."
    if raw != vpath.strip() or "\n" in raw or "\r" in raw:
        return "A path cannot contain a line break."
    if ":" in raw or raw.startswith("/") or raw.startswith("\\"):
        return (
            "That looks like a path on this computer. Give the path inside the "
            "mod instead, such as 3DView/textures/mine.dds."
        )

    v = vfsmod.normalise(raw)
    if not v or v == ".":
        return "No path given."
    parts = v.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return "A path cannot step outside the mod with .. or contain empty parts."
    if len(parts) == 1:
        return (
            f"{v} would sit at the top of the mod folder, where the only file "
            f"the game reads is {MANIFEST}. Put it in one of: "
            + ", ".join(f"{r}/" for r in MOD_CONTENT_ROOTS) + "."
        )

    root = parts[0].lower()
    if root in {r.lower() for r in IGNORED_ROOTS}:
        return f"{parts[0]}/ is the player's own data, not part of the mod."
    if root == rootfiles.PAYLOAD_DIR.lower():
        return (
            f"{rootfiles.PAYLOAD_DIR}/ stages files for the game installation "
            "itself, which is a separate operation with its own backup. Add "
            "those through the game-folder files list."
        )
    if root not in {r.lower() for r in MOD_CONTENT_ROOTS}:
        return (
            f"The engine does not read {parts[0]}/ from inside a mod, so a "
            f"file there would do nothing at all. A mod's content lives in: "
            + ", ".join(f"{r}/" for r in MOD_CONTENT_ROOTS) + "."
        )
    return None

#: Every :class:`Mod` currently holding its ``user_data.zip`` open.
#:
#: Windows refuses to replace a file that *anyone* has open, and "anyone"
#: includes a second ``Mod`` object for the same folder -- which is not exotic:
#: a mod picker builds one per discovered mod and keeps them for its labels,
#: and the app then saves through a different instance entirely.  A `Mod` that
#: closes only its own handle cannot fix that, so the library tracks them all
#: and :meth:`Mod._write_zip` closes every handle on the file it is about to
#: replace.
#:
#: Weak, so an abandoned ``Mod`` leaves nothing behind, and keyed by nothing --
#: the set is small (one entry per open mod) and scanned linearly.
_OPEN_ZIP_MODS: "weakref.WeakSet" = weakref.WeakSet()


class FileState:
    """How a mod file relates to stock."""

    OVERRIDE = "override"        # replaces a stock asset
    ADDITION = "addition"        # new path, no stock counterpart
    IDENTICAL = "identical"      # byte-identical to stock: no effect
    DEAD = "dead"                # in a location the engine never reads


class ModFile:
    """One file in a mod, with where it came from and what it does."""

    __slots__ = ("vpath", "source", "size", "_read", "_sha", "state", "stock_origin")

    def __init__(self, vpath, source, size, read):
        #: Virtual path as the engine would see it, e.g. ``3DView/PlayerShip.xml``.
        self.vpath = vpath
        #: ``"zip"`` or ``"loose"``.
        self.source = source
        self.size = size
        self._read = read
        self._sha = None
        self.state: Optional[str] = None
        self.stock_origin: Optional[str] = None

    def read(self) -> bytes:
        return self._read()

    @property
    def sha1(self) -> str:
        if self._sha is None:
            self._sha = hashlib.sha1(self.read()).hexdigest()
        return self._sha

    @property
    def is_dead(self) -> bool:
        """True if the engine will never read this file.

        Loose ``3DView/`` content only -- confirmed by experiment, not assumed.
        """
        root = self.vpath.split("/", 1)[0].lower()
        return self.source == "loose" and root in {r.lower() for r in ZIP_ONLY_ROOTS}

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ModFile {self.vpath} [{self.source}] {self.state or '?'}>"


class Mod:
    """A mod on disk."""

    def __init__(self, root: str) -> None:
        self.root = os.path.abspath(root)
        self.name = os.path.basename(self.root)
        self._manifest: Optional[iniformat.IniFile] = None
        self._files: Optional[Dict[str, ModFile]] = None
        #: Open handle on ``user_data.zip``, held so :meth:`files` can serve
        #: lazy reads.  It must be closable -- see :meth:`close`.
        self._zf: Optional[zipfile.ZipFile] = None

    # -- discovery -----------------------------------------------------------

    @classmethod
    def discover(cls, customization_dir: str) -> List["Mod"]:
        """Every mod folder under a ``Customization`` directory.

        Returns folders that *look* like mods (they have a manifest).  Whether
        the game will actually list them is a separate question -- see
        :meth:`is_listable` -- and keeping those apart is the point: the app
        must be able to show a mod the game is silently ignoring.
        """
        out = []
        if not os.path.isdir(customization_dir):
            return out
        for name in sorted(os.listdir(customization_dir)):
            path = os.path.join(customization_dir, name)
            if os.path.isdir(path) and os.path.exists(os.path.join(path, MANIFEST)):
                out.append(cls(path))
        return out

    @classmethod
    def create(
        cls,
        customization_dir: str,
        name: str,
        description: str = "",
        *,
        stock: Optional["vfsmod.Vfs"] = None,
        folder: Optional[str] = None,
    ) -> "Mod":
        """Create a new mod that the game will actually list.

        Writes the manifest **and** ``inifiles/items.ini``.  The second one is
        not optional: without it the game skips the folder and reports nothing
        (``specs/scene.md`` 6b), so a mod created without it is invisible and the
        author has no way to find out why.  Copied from ``stock`` when a game is
        open; otherwise a minimal stub is written and ``PRJ004`` will say so.

        ``.dsoproj`` records the base game it was authored against, so a later
        session can warn when that changes.
        """
        name = (name or "").strip()
        if not name:
            raise ProjectError("a mod needs a name")
        safe = folder or _safe_folder_name(name)
        root = os.path.join(customization_dir, safe)
        if os.path.exists(root):
            raise ProjectError(f"already exists: {root}", path=root)

        os.makedirs(os.path.join(root, "inifiles"), exist_ok=True)

        manifest = (
            "[darkstarmod]\r\n"
            f"mod_name = {name}\r\n"
            f"mod_desc = {(description or '').strip()}\r\n"
        )
        _atomic_write(os.path.join(root, MANIFEST), manifest.encode("cp1252", "replace"))

        items = None
        if stock is not None:
            entry = stock.find(VALIDITY_TOKEN)
            if entry is not None:
                items = entry.read()
        if items is None:
            # Enough to satisfy the loader; the app flags it so the author can
            # replace it with the stock file once a game folder is open.
            items = b"[items]\r\n"
        _atomic_write(os.path.join(root, "inifiles", "items.ini"), items)

        mod = cls(root)
        proj = ProjectFile()
        if stock is not None:
            proj.record_base_game(stock)
        proj.save(root)
        return mod

    @classmethod
    def default_customization_dir(cls) -> Optional[str]:
        """The standard location, if it exists on this machine."""
        home = os.path.expanduser("~")
        for docs in ("Documents", "Dokumente"):
            p = os.path.join(home, docs, "Ascaron Entertainment", "Darkstar One", "Customization")
            if os.path.isdir(p):
                return p
        return None

    # -- manifest ------------------------------------------------------------

    @property
    def manifest(self) -> iniformat.IniFile:
        if self._manifest is None:
            path = os.path.join(self.root, MANIFEST)
            if not os.path.exists(path):
                raise ProjectError(f"no {MANIFEST}", path=self.root, code="PRJ001")
            with open(path, "rb") as fh:
                self._manifest = iniformat.parse(fh.read(), path=path)
        return self._manifest

    @property
    def display_name(self) -> Optional[str]:
        return self.manifest.get(MANIFEST_SECTION, "mod_name")

    @property
    def description(self) -> Optional[str]:
        return self.manifest.get(MANIFEST_SECTION, "mod_desc")

    def set_metadata(self, name: Optional[str] = None,
                     description: Optional[str] = None) -> None:
        """Rewrite ``mod_name`` / ``mod_desc`` in place.

        Goes through the INI editor rather than regenerating the file, so a
        hand-written manifest keeps its comments, its key order and its
        encoding.  People do edit these by hand; a tool that silently reformats
        one is a tool they stop trusting.

        The mod *folder* is untouched -- see :meth:`rename_folder`.  A rename on
        disk has consequences (the game's ``mod.ini`` may point at the old name)
        that a metadata edit does not, and quietly bundling the two is how a
        harmless-looking edit deselects someone's active mod.
        """
        if name is not None:
            name = name.strip()
            if not name:
                raise ProjectError("a mod needs a name", path=self.root)
        manifest = self.manifest
        section = manifest.section(MANIFEST_SECTION)
        if section is None:
            raise ProjectError(
                f"{MANIFEST} has no [{MANIFEST_SECTION}] section",
                path=os.path.join(self.root, MANIFEST),
                code="PRJ001",
            )
        if name is not None:
            section.set("mod_name", name)
        if description is not None:
            section.set("mod_desc", description.strip())
        _atomic_write(os.path.join(self.root, MANIFEST), manifest.to_bytes())
        self._manifest = None

    def rename_folder(self, new_folder: str) -> "Mod":
        """Rename the mod's directory, returning a :class:`Mod` for the new path.

        Separate from :meth:`set_metadata` on purpose: this changes the mod's
        *identity* on disk.  The game records the selected mod by folder name in
        ``mod.ini``, so renaming the folder of the currently-selected mod
        deselects it until the player picks it again -- a real consequence, and
        one the caller should be able to warn about before it happens.  See
        :meth:`is_selected_in`.

        The instance this is called on is stale afterwards and is left that way
        rather than mutated, so a caller holding the old object gets an obvious
        failure instead of silently reading a directory that no longer exists.
        """
        if not (new_folder or "").strip():
            raise ProjectError("a folder name cannot be empty", path=self.root)
        new_folder = _safe_folder_name(new_folder)
        parent = os.path.dirname(self.root)
        target = os.path.join(parent, new_folder)
        if os.path.normcase(target) == os.path.normcase(self.root):
            return self                                     # nothing to do
        # Case-insensitive on Windows: "test a" -> "Test A" must not be read as
        # a collision with itself, but a genuinely different mod must be.
        if os.path.exists(target):
            raise ProjectError(f"a folder named {new_folder!r} already exists here",
                               path=target)
        try:
            os.rename(self.root, target)
        except OSError as exc:
            raise ProjectError(
                f"could not rename the mod folder: {exc}. "
                "Close anything using the folder (Explorer, the game) and retry.",
                path=self.root,
            ) from None
        return Mod(target)

    def is_selected_in(self, customization_dir: Optional[str] = None) -> bool:
        """Is this the mod the game currently has selected?

        The selection lives in ``mod.ini`` one level above the Customization
        folder and names the *folder*, so renaming a selected mod breaks the
        link.  Returns ``False`` if the file is missing or unreadable -- an
        unknown selection must not block an edit.
        """
        parent = customization_dir or os.path.dirname(self.root)
        path = os.path.join(os.path.dirname(parent), MOD_SELECTION)
        try:
            with open(path, "rb") as fh:
                selected = iniformat.parse(fh.read(), path=path).get(
                    MOD_SELECTION_SECTION, "load_mod"
                )
        except (OSError, DsoError):
            return False
        if not selected or selected.strip().lower() == MOD_SELECTION_NONE:
            return False
        return selected.strip().lower() == self.name.lower()

    # -- contents ------------------------------------------------------------

    @property
    def zip_path(self) -> Optional[str]:
        for cand in (USER_DATA, "User_Data.zip"):
            p = os.path.join(self.root, cand)
            if os.path.exists(p):
                return p
        return None

    def files(self) -> Dict[str, ModFile]:
        """Every file the mod ships, keyed by lowercased virtual path.

        Where the same path exists both in the zip and loose, the zip wins --
        matching the engine.  The loose copy is still returned, under a
        ``loose:`` prefix, so duplication can be reported.
        """
        if self._files is not None:
            return self._files
        out: Dict[str, ModFile] = {}

        zp = self.zip_path
        if zp:
            zf = self._zf = zipfile.ZipFile(zp)
            _OPEN_ZIP_MODS.add(self)
            for zi in zf.infolist():
                if zi.is_dir():
                    continue
                v = vfsmod.normalise(zi.filename)
                out[v.lower()] = ModFile(v, "zip", zi.file_size, lambda z=zi: zf.read(z))

        for dirpath, dirnames, filenames in os.walk(self.root):
            rel_root = os.path.relpath(dirpath, self.root).replace(os.sep, "/")
            if rel_root == ".":
                dirnames[:] = [d for d in dirnames if d.lower() not in IGNORED_ROOTS]
            for fn in filenames:
                full = os.path.join(dirpath, fn)
                v = vfsmod.normalise(os.path.relpath(full, self.root).replace(os.sep, "/"))
                # The manifest, the archive, and `.dsoproj` are not mod
                # *content*: the first two are the mod's own packaging and the
                # third is this tool's sidecar, which the game never reads.
                # Listing `.dsoproj` among the author's files invited them to
                # ask what it overrides, and the answer is "nothing".
                if v.lower() in _NOT_CONTENT:
                    continue
                key = v.lower()
                mf = ModFile(v, "loose", os.path.getsize(full), lambda p=full: open(p, "rb").read())
                if key in out:
                    out["loose:" + key] = mf
                else:
                    out[key] = mf

        self._files = out
        return out

    def close(self) -> None:
        """Release the handle on ``user_data.zip`` and drop the inventory.

        Needed because :meth:`files` keeps the archive open to serve lazy reads.
        Callers that only read can ignore this; anything that *replaces* the zip
        must not be holding it open on Windows (see :meth:`_write_zip`).
        """
        if self._zf is not None:
            try:
                self._zf.close()
            except OSError:                     # already gone; nothing to release
                pass
            self._zf = None
        _OPEN_ZIP_MODS.discard(self)
        self._files = None

    # -- engine rules --------------------------------------------------------

    def is_listable(self) -> bool:
        """Will the game show this mod at all?

        Requires ``inifiles/items.ini``.  Without it the mod folder is skipped
        and nothing is reported -- see ``specs/scene.md`` §6b.

        Answered without building the full inventory.  It used to call
        :meth:`files`, which walks the whole mod *and* leaves ``user_data.zip``
        open -- so listing the mods in a picker was enough to make a later save
        to any of them fail on Windows.  One question about one path does not
        need a handle held open afterwards.
        """
        if os.path.exists(os.path.join(self.root, "inifiles", "items.ini")):
            return True
        zp = self.zip_path
        if not zp:
            return False
        try:
            with zipfile.ZipFile(zp) as zf:
                return any(
                    vfsmod.normalise(n).lower() == VALIDITY_TOKEN.lower()
                    for n in zf.namelist()
                )
        except (OSError, zipfile.BadZipFile):
            return False

    def dead_files(self) -> List[ModFile]:
        """Files the engine will never read."""
        return [f for f in self.files().values() if f.is_dead]

    def duplicated_files(self) -> List[str]:
        """Paths present both in ``user_data.zip`` and loose."""
        keys = self.files().keys()
        return sorted(k[len("loose:") :] for k in keys if k.startswith("loose:"))

    # -- comparison against stock -------------------------------------------

    def classify(self, stock: "vfsmod.Vfs") -> Dict[str, ModFile]:
        """Tag every file with its :class:`FileState` relative to ``stock``.

        ``stock`` must be a VFS containing only the base game -- build it with
        :func:`dsotools.vfs.from_extracted` and do *not* add the mod to it, or
        every file will compare equal to itself.
        """
        files = self.files()
        for mf in files.values():
            if mf.is_dead:
                mf.state = FileState.DEAD
                continue
            entry = stock.find(mf.vpath)
            if entry is None:
                mf.state = FileState.ADDITION
                continue
            mf.stock_origin = entry.origin
            same = entry.size == mf.size and hashlib.sha1(entry.read()).hexdigest() == mf.sha1
            mf.state = FileState.IDENTICAL if same else FileState.OVERRIDE
        return files

    def summary(self, stock: "vfsmod.Vfs") -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for mf in self.classify(stock).values():
            counts[mf.state] = counts.get(mf.state, 0) + 1
        return counts

    # -- deployment ----------------------------------------------------------

    def deploy(self, files: Dict[str, bytes], *, project: Optional["ProjectFile"] = None) -> Dict[str, str]:
        """Write a set of assets into the mod, each to where the engine reads it.

        ``files`` maps virtual path to bytes -- exactly what
        :meth:`dsotools.edit.atlas.AtlasPage.save` returns.  Routing is decided
        per file by :meth:`deploy_target`, so a caller never has to remember
        that ``3DView/`` must go in the zip.

        **All or nothing.**  The zip is rebuilt into a temporary file and
        swapped in; loose files are written through a temp-and-replace.  A
        rescaled page without its rewritten index is worse than no change at
        all, so a partial deploy must not be reachable.

        Returns ``{vpath: "zip" | "loose"}`` for what was written.
        """
        routed: Dict[str, str] = {}
        zip_files: Dict[str, bytes] = {}
        loose_files: Dict[str, bytes] = {}
        for vpath, data in files.items():
            v = vfsmod.normalise(vpath)
            if self.deploy_target(v) == "zip":
                zip_files[v] = data
            else:
                loose_files[v] = data
            routed[v] = self.deploy_target(v)

        if zip_files:
            self._write_zip(zip_files)
        for vpath, data in loose_files.items():
            _atomic_write(os.path.join(self.root, *vpath.split("/")), data)

        if project is not None:
            for vpath, data in files.items():
                project.record(vpath, result_sha1=hashlib.sha1(data).hexdigest())

        self._files = None          # the inventory is now stale
        return routed

    def remove(self, vpaths: Sequence[str]) -> Dict[str, str]:
        """Delete assets from the mod, wherever they live.

        The mod stops overriding those paths, so the engine reads the stock
        file again.  That is the *point* -- writing stock bytes back over the
        mod's copy would leave a file that is byte-identical to stock, which is
        dead weight the app then complains about (``PRJ002``).

        Refuses :data:`VALIDITY_TOKEN`.  A mod with no ``inifiles/items.ini``
        is not listed by the game at all, with no message anywhere, so removing
        it is never what someone meant -- see the module docstring.

        Returns ``{vpath: "zip" | "loose"}`` for what was actually removed;
        paths the mod does not have are skipped rather than raising, because
        "it is already not there" is the outcome the caller wanted.
        """
        files = self.files()
        drop_zip: List[str] = []
        removed: Dict[str, str] = {}
        loose_paths: List[str] = []

        for raw in vpaths:
            v = vfsmod.normalise(raw)
            key = v.lower()
            if key == VALIDITY_TOKEN.lower():
                raise ProjectError(
                    f"{VALIDITY_TOKEN} cannot be removed: without it the game "
                    "does not list this mod at all, and shows no error saying "
                    "why.",
                    path=os.path.join(self.root, *VALIDITY_TOKEN.split("/")),
                )
            mf = files.get(key)
            if mf is None:
                continue
            if mf.source == "zip":
                drop_zip.append(v)
                removed[v] = "zip"
            else:
                loose_paths.append(os.path.join(self.root, *v.split("/")))
                removed[v] = "loose"

        if drop_zip:
            self._write_zip({}, drop=drop_zip)
        for full in loose_paths:
            try:
                os.unlink(full)
            except FileNotFoundError:
                pass
            # Prune the directories the file left behind, but never the mod
            # root: an empty `3DView/` in the tree reads as "there is something
            # here" to the next person who looks.
            parent = os.path.dirname(full)
            while os.path.normpath(parent) != os.path.normpath(self.root):
                try:
                    os.rmdir(parent)
                except OSError:
                    break
                parent = os.path.dirname(parent)

        if removed:
            self._files = None          # the inventory is now stale
        return removed

    def zipped_scripts(self) -> List[str]:
        """Script paths inside ``user_data.zip``, which the engine never reads.

        Pure lookup, so the caller can offer the fix only when there is one.
        """
        target = self.zip_path
        if not target or not os.path.exists(target):
            return []
        try:
            with zipfile.ZipFile(target) as archive:
                return sorted(
                    n for n in archive.namelist()
                    if not n.endswith("/")
                    and vfsmod.normalise(n).lower().startswith("scripts/")
                )
        except (OSError, zipfile.BadZipFile):
            return []

    def unzip_scripts(self) -> Dict[str, List[str]]:
        """Move ``scripts/`` out of ``user_data.zip`` and into the mod folder.

        The mirror of :meth:`apply_deploy_plan`, and it takes the same safety
        ordering for the same reason: the loose copies are written **first**
        and the archive is rewritten without them only afterwards.  A failure
        between the two leaves the script in both places, which the engine
        reads correctly and ``PRJ006`` then reports; the other order would lose
        the file outright if the second step failed.

        A loose file that is already there and **differs** is left alone and
        reported as a conflict.  Both copies are plausible intent -- the loose
        one may be the newer edit, or the forgotten one -- and guessing wrong
        destroys work, which is the same call ``deploy_plan`` makes about a
        duplicated ``3DView/`` file.  Identical bytes are not a conflict; the
        zipped copy is simply dropped.

        Returns ``{"moved": [...], "conflicts": [...], "identical": [...]}``.
        """
        names = self.zipped_scripts()
        out: Dict[str, List[str]] = {"moved": [], "conflicts": [], "identical": []}
        if not names:
            return out

        with zipfile.ZipFile(self.zip_path) as archive:
            payload = {}
            for name in names:
                vpath = vfsmod.normalise(name)
                full = os.path.join(self.root, *vpath.split("/"))
                data = archive.read(name)
                if os.path.exists(full):
                    try:
                        with open(full, "rb") as handle:
                            same = handle.read() == data
                    except OSError:
                        same = False
                    if same:
                        out["identical"].append(vpath)
                    else:
                        out["conflicts"].append(vpath)
                    continue
                payload[name] = (full, data)

        for name, (full, data) in payload.items():
            os.makedirs(os.path.dirname(full), exist_ok=True)
            _atomic_write(full, data)
            out["moved"].append(vfsmod.normalise(name))

        drop = [n for n in names
                if vfsmod.normalise(n) not in out["conflicts"]]
        if drop:
            self._write_zip({}, drop=drop)
            self._files = None
        return out

    def _write_zip(self, updates: Dict[str, bytes],
                   *, drop: Sequence[str] = ()) -> None:
        """Rebuild ``user_data.zip`` with ``updates`` applied.

        zipfile cannot replace an entry in place, so the archive is rewritten.
        Existing entries are copied across unchanged; only the named paths are
        replaced or added.  ``drop`` names entries to leave out entirely, which
        is how :meth:`remove` deletes from the archive.
        """
        target = self.zip_path or os.path.join(self.root, USER_DATA)
        lower = {k.lower(): k for k in updates}
        dropped = {vfsmod.normalise(d).lower() for d in drop}
        os.makedirs(self.root, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.root, suffix=".zip.tmp")
        os.close(fd)
        try:
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as out:
                if os.path.exists(target):
                    with zipfile.ZipFile(target) as src:
                        for zi in src.infolist():
                            if zi.is_dir():
                                continue
                            key = vfsmod.normalise(zi.filename).lower()
                            if key in lower or key in dropped:
                                continue        # replaced below, or removed
                            out.writestr(zi, src.read(zi))
                for vpath, data in updates.items():
                    out.writestr(vpath, data)
            # Drop *every* read handle on the archive we are about to replace,
            # not just this object's.  A second Mod for the same folder is the
            # normal case -- a mod picker keeps one per discovered mod for its
            # labels -- and its handle blocks this write just as effectively.
            #
            # This is a Windows requirement and it is invisible on POSIX, which
            # is why it survived until the build was first run on Windows.
            # ``files()`` keeps ``user_data.zip`` open to serve lazy reads, and
            # Windows refuses to rename over a file that anyone -- including
            # this very object -- still has open, with a bare
            # ``PermissionError: [WinError 5] Access is denied`` naming the
            # temp file rather than the culprit.  POSIX rename-over-open is
            # legal, so every prior test run passed.
            #
            # It happens here, not earlier: everything that needed the old zip
            # (the relocation payload, and ``src`` above) has already been read,
            # and if the write below fails we have released nothing we still
            # need -- ``files()`` simply reopens on next use.
            _close_zip_handles(target)
            os.replace(tmp, target)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    @staticmethod
    def deploy_target(vpath: str) -> str:
        """Where a given asset must be written: ``"zip"`` or ``"loose"``.

        ``3DView/`` content goes into ``user_data.zip`` because the loose tree
        is not read.  Everything else is read loose.  This one function is what
        stops the app from writing files that silently do nothing.
        """
        root = vfsmod.normalise(vpath).split("/", 1)[0].lower()
        if root in {r.lower() for r in ZIP_ONLY_ROOTS}:
            return "zip"
        return "loose"

    # -- publishing ----------------------------------------------------------

    def deploy_plan(self, *, stock: Optional["vfsmod.Vfs"] = None) -> "DeployPlan":
        """Work out what it would take to make this mod load as intended.

        Pure computation: reads the mod, writes nothing.  The GUI shows the plan
        before doing it, and the tests assert on the plan rather than on the
        filesystem, which is why the two halves are separate methods.

        Three things can be wrong with a mod's *layout* -- as opposed to its
        content -- and all three are silent in game:

        ``relocate``
            A loose ``3DView/`` file.  The engine never reads those, so the file
            is dead where it is (``specs/scene.md`` §6b) and belongs in
            ``user_data.zip``.
        ``conflict``
            A loose ``3DView/`` file whose path *also* exists in the zip.  Both
            copies are plausible intent -- the loose one may be a newer edit or
            a forgotten leftover -- and guessing wrong destroys work, so this is
            reported and never acted on.
        ``add-items-ini``
            No ``inifiles/items.ini``.  Without it the game does not list the
            mod at all and says nothing about why.
        """
        plan = DeployPlan(self)
        files = self.files()

        zip_keys = {k for k, f in files.items() if f.source == "zip"}
        for key, mf in sorted(files.items()):
            if not mf.is_dead:
                continue
            # ``files()`` keys a shadowed loose copy as "loose:<path>"; either
            # spelling means the zip already carries this path.
            bare = key[len("loose:"):] if key.startswith("loose:") else key
            if bare in zip_keys:
                plan.conflicts.append(mf.vpath)
            else:
                plan.relocate.append(mf.vpath)

        if not self.is_listable():
            plan.add_items_ini = True
            if stock is not None and stock.find(VALIDITY_TOKEN) is not None:
                plan.items_ini_source = "stock"
            else:
                plan.items_ini_source = "stub"

        return plan

    def apply_deploy_plan(
        self,
        plan: "DeployPlan",
        *,
        stock: Optional["vfsmod.Vfs"] = None,
        project: Optional["ProjectFile"] = None,
    ) -> "DeployResult":
        """Carry out ``plan``.  Ordering here is the safety property.

        The zip is rebuilt and swapped in **first**, and the loose originals are
        deleted only afterwards.  Both steps can fail, and the two orders fail
        very differently:

        * zip first -- a failed delete leaves the file in both places.  The
          engine reads the zip, so the mod is *correct*; there is a stale copy
          on disk and the next plan reports it as a conflict.
        * delete first -- a failed zip write leaves the file nowhere.  The
          author's work is gone.

        So the recoverable failure is the one we take.  Deletes that fail are
        collected in :attr:`DeployResult.not_removed` rather than raised, for
        the same reason: the write already succeeded and unwinding it would
        turn a cosmetic problem into a destructive one.
        """
        if plan.mod_root != self.root:
            raise ProjectError(
                "this plan was made for a different mod", path=plan.mod_root
            )

        result = DeployResult()

        payload: Dict[str, bytes] = {}
        for vpath in plan.relocate:
            mf = self.files().get(vpath.lower())
            if mf is None:                      # changed under us since planning
                result.missing.append(vpath)
                continue
            payload[vpath] = mf.read()

        if plan.add_items_ini:
            data = None
            if stock is not None:
                entry = stock.find(VALIDITY_TOKEN)
                if entry is not None:
                    data = entry.read()
            if data is None:
                data = _items_ini_stub()
                result.items_ini_source = "stub"
            else:
                result.items_ini_source = "stock"
            payload[VALIDITY_TOKEN] = data

        if payload:
            result.written = self.deploy(payload, project=project)

        # Only now, with the zip on disk, do the originals go.
        for vpath in plan.relocate:
            if vpath in result.missing:
                continue
            try:
                self._remove_loose(vpath)
                result.removed.append(vpath)
            except OSError as exc:
                result.not_removed.append((vpath, str(exc)))

        result.conflicts = list(plan.conflicts)
        self._files = None
        return result

    def _remove_loose(self, vpath: str) -> None:
        """Delete a loose file and any directories it leaves empty.

        Pruning matters more than it looks: an empty ``3DView/objects/`` left
        behind still makes the mod folder *look* like it ships loose 3D content,
        which is the exact misconception this operation exists to clear up.
        Pruning stops at the mod root and at any directory that is not empty.
        """
        parts = vfsmod.normalise(vpath).split("/")
        full = os.path.join(self.root, *parts)
        os.remove(full)
        for i in range(len(parts) - 1, 0, -1):
            d = os.path.join(self.root, *parts[:i])
            if os.path.realpath(d) == os.path.realpath(self.root):
                break
            try:
                os.rmdir(d)
            except OSError:
                break                           # not empty, or in use: fine

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Mod {self.name!r} at {self.root}>"


def _items_ini_stub() -> bytes:
    """A minimal ``items.ini`` for when no game is open to copy one from.

    Its only job is to exist: the game checks for the file, and a mod without it
    is never listed.  ``PRJ004`` still reports a stub so the author knows to
    replace it with the real one once a game folder is open.
    """
    return (
        "; Written by the Darkstar One Modding Suite so the game lists this mod.\r\n"
        "; This is a placeholder -- open your game folder and deploy again to\r\n"
        "; replace it with the stock file.\r\n"
    ).encode("cp1252")


class DeployPlan:
    """What deploying would change.  Built by :meth:`Mod.deploy_plan`."""

    __slots__ = ("mod_root", "relocate", "conflicts", "add_items_ini", "items_ini_source")

    def __init__(self, mod: "Mod") -> None:
        self.mod_root = mod.root
        #: Loose ``3DView/`` paths that will move into ``user_data.zip``.
        self.relocate: List[str] = []
        #: Loose ``3DView/`` paths the zip already has.  Never touched.
        self.conflicts: List[str] = []
        self.add_items_ini = False
        #: ``"stock"`` or ``"stub"`` -- which ``items.ini`` would be written.
        self.items_ini_source: Optional[str] = None

    @property
    def empty(self) -> bool:
        """True when there is nothing to do.

        Conflicts do not count: deploying will not resolve one, so a mod whose
        only finding is a conflict has nothing for this operation to do.
        """
        return not self.relocate and not self.add_items_ini

    def summary(self) -> List[str]:
        """Human-readable lines, in the order they matter."""
        out = []
        if self.add_items_ini:
            out.append(
                f"add inifiles/items.ini ({self.items_ini_source}) — "
                "without it the game does not list this mod at all"
            )
        if self.relocate:
            out.append(
                f"move {len(self.relocate)} loose 3DView file(s) into user_data.zip — "
                "the engine never reads them where they are"
            )
        if self.conflicts:
            out.append(
                f"{len(self.conflicts)} loose 3DView file(s) already exist in "
                "user_data.zip and are left alone — decide which copy you want"
            )
        return out

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<DeployPlan relocate={len(self.relocate)} "
            f"conflicts={len(self.conflicts)} items_ini={self.add_items_ini}>"
        )


class DeployResult:
    """What deploying actually did."""

    __slots__ = ("written", "removed", "not_removed", "missing", "conflicts",
                 "items_ini_source")

    def __init__(self) -> None:
        #: ``{vpath: "zip" | "loose"}`` as returned by :meth:`Mod.deploy`.
        self.written: Dict[str, str] = {}
        self.removed: List[str] = []
        #: ``[(vpath, reason)]`` -- written into the zip but the loose original
        #: could not be deleted.  Harmless but worth saying out loud.
        self.not_removed: List[Tuple[str, str]] = []
        #: Planned but gone by the time it ran.
        self.missing: List[str] = []
        self.conflicts: List[str] = []
        self.items_ini_source: Optional[str] = None

    @property
    def clean(self) -> bool:
        return not self.not_removed and not self.missing

    def summary(self) -> str:
        bits = []
        if self.written:
            zipped = sum(1 for v in self.written.values() if v == "zip")
            loose = len(self.written) - zipped
            bits.append(f"{zipped} file(s) into user_data.zip" if zipped else "")
            bits.append(f"{loose} loose file(s)" if loose else "")
        if self.removed:
            bits.append(f"{len(self.removed)} dead loose file(s) removed")
        if self.not_removed:
            bits.append(f"{len(self.not_removed)} could not be removed")
        if self.missing:
            bits.append(f"{len(self.missing)} vanished before writing")
        if self.conflicts:
            bits.append(f"{len(self.conflicts)} conflict(s) left for you")
        return ", ".join(b for b in bits if b) or "nothing to do"

    def __repr__(self) -> str:  # pragma: no cover
        return f"<DeployResult {self.summary()}>"


PROJECT_FILE = ".dsoproj"
PROJECT_SCHEMA = 1


class ProjectFile:
    """The ``.dsoproj`` sidecar: everything the *game* does not need.

    ``darkstarmod.ini`` stays the game's manifest and is generated from this,
    never hand-edited.  This file is additive and the engine ignores it.

    It exists for one reason above the others: **provenance**.  For each modded
    file it records the stock asset it replaced, that asset's hash, and the
    source it was built from (the PNG, the .glb).  That makes a mod
    *rebuildable* rather than merely shippable -- "re-import every texture from
    source" is how people actually work, and it is impossible without this.

    The base-game fingerprint is the other half: it lets the app say "this mod
    was authored against a different game version" instead of failing in ways
    nobody can explain.
    """

    def __init__(self, data: Optional[dict] = None) -> None:
        d = dict(data or {})
        d.setdefault("schema", PROJECT_SCHEMA)
        d.setdefault("tool_version", None)
        d.setdefault("base_game", {})
        d.setdefault("provenance", {})
        d.setdefault("suppressed", [])
        d.setdefault("root_files", {})
        self.data = d

    # -- io ------------------------------------------------------------------

    @classmethod
    def load(cls, mod_root: str) -> "ProjectFile":
        path = os.path.join(mod_root, PROJECT_FILE)
        if not os.path.exists(path):
            return cls()
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as exc:
            raise ProjectError(f"cannot read {PROJECT_FILE}: {exc}", path=path) from None
        if int(data.get("schema", 0)) > PROJECT_SCHEMA:
            raise ProjectError(
                f"{PROJECT_FILE} was written by a newer version of the tools "
                f"(schema {data.get('schema')} > {PROJECT_SCHEMA})",
                path=path,
            )
        return cls(data)

    def save(self, mod_root: str) -> str:
        from . import __version__

        self.data["tool_version"] = __version__
        path = os.path.join(mod_root, PROJECT_FILE)
        _atomic_write(path, json.dumps(self.data, indent=2, sort_keys=True).encode("utf-8"))
        return path

    # -- base game -----------------------------------------------------------

    def record_base_game(self, stock: "vfsmod.Vfs") -> None:
        self.data["base_game"] = {
            "layers": [
                {"name": ly.name, "files": len(ly.index())} for ly in stock.layers
            ],
            "fingerprint": base_game_fingerprint(stock),
        }

    def base_game_matches(self, stock: "vfsmod.Vfs") -> Optional[bool]:
        """``None`` when nothing was recorded, else whether it still matches."""
        fp = self.data.get("base_game", {}).get("fingerprint")
        return None if not fp else fp == base_game_fingerprint(stock)

    # -- provenance ----------------------------------------------------------

    def record(self, vpath: str, *, source: Optional[str] = None,
               operation: Optional[str] = None, stock_sha1: Optional[str] = None,
               result_sha1: Optional[str] = None) -> None:
        """Note how one modded file came to be."""
        entry = self.data["provenance"].setdefault(vfsmod.normalise(vpath).lower(), {})
        for key, value in (
            ("source", source), ("operation", operation),
            ("stock_sha1", stock_sha1), ("result_sha1", result_sha1),
        ):
            if value is not None:
                entry[key] = value

    def forget(self, vpath: str) -> bool:
        """Drop the provenance for a file the mod no longer has.

        Returns whether there was anything to drop.  Left behind, the entry
        claims the mod still builds a file it does not contain, and
        :meth:`rebuildable` would offer to re-import it.
        """
        return self.data["provenance"].pop(
            vfsmod.normalise(vpath).lower(), None
        ) is not None

    def provenance_of(self, vpath: str) -> dict:
        return self.data["provenance"].get(vfsmod.normalise(vpath).lower(), {})

    def rebuildable(self) -> Dict[str, str]:
        """Modded files that record the source they were built from."""
        return {
            k: v["source"]
            for k, v in self.data["provenance"].items()
            if v.get("source")
        }

    # -- files that go into the game installation ----------------------------
    #
    # Some content cannot be delivered from the mod folder at all: no archive
    # holds a single ``lua/`` entry, so a mod that changes the shared mission
    # libraries has to overwrite the installation.  What it puts there is
    # recorded here, in the project rather than in the mod, because it is not
    # something the game reads -- it is what makes the change reversible.
    # ``dsotools.rootfiles`` does the installing.

    def record_root_files(self, files: Dict[str, dict]) -> None:
        """``{game-relative path: {"sha256": ..., "size": ...}}``."""
        self.data["root_files"] = {
            str(k).replace("\\", "/"): {"sha256": v.get("sha256"),
                                        "size": v.get("size")}
            for k, v in files.items()
        }

    @property
    def root_files(self) -> Dict[str, dict]:
        return dict(self.data.get("root_files") or {})

    # A .res stores only the *hash* of each StringId, so an id cannot be read
    # back out of a built table.  The authored (id, text) pairs therefore live
    # here -- the project is the source, ``strings/user_strings.res`` is the
    # build product.  Losing this record means losing the ability to edit the
    # mod's text at all, which is why it is not kept in the mod folder.

    def record_strings(self, pairs) -> None:
        """``[(StringId, text), ...]``, in author order."""
        self.data["strings"] = [[str(k), str(v)] for k, v in pairs]

    @property
    def strings(self):
        """The authored pairs, or ``[]``."""
        return [(str(row[0]), str(row[1]))
                for row in (self.data.get("strings") or ())
                if len(row) >= 2]

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ProjectFile {len(self.data['provenance'])} tracked files>"


#: Characters Windows forbids in a folder name.
_UNSAFE = set('<>:"/\\|?*')


def _safe_folder_name(name: str) -> str:
    """Turn a display name into a folder name Windows will accept."""
    cleaned = "".join("_" if c in _UNSAFE or ord(c) < 32 else c for c in name).strip(" .")
    return cleaned or "New Mod"


def base_game_fingerprint(stock: "vfsmod.Vfs") -> str:
    """Identify a game installation by its layers, file counts and sizes."""
    h = hashlib.sha1()
    for layer in stock.layers:
        idx = layer.index()
        h.update(f"{layer.name}:{len(idx)}:{sum(s for _v, _r, s in idx.values())};".encode())
    return h.hexdigest()


def _atomic_write(path: str, data: bytes) -> None:
    """Write via a temporary file and replace, so a crash cannot truncate.

    Deploy touches several files that must agree with each other; the least this
    layer can do is never leave one of them half-written.
    """
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _close_zip_handles(target: str) -> int:
    """Close every :class:`Mod` handle on ``target``.  Returns how many.

    The one operation that makes replacing a mod's ``user_data.zip`` reliable
    on Windows.  Comparing with ``os.path.normcase(os.path.realpath(...))``
    rather than the raw string, because the same archive reached through a
    different spelling is still the same open file to the OS.
    """
    try:
        key = os.path.normcase(os.path.realpath(target))
    except OSError:                              # pragma: no cover - unreachable path
        key = os.path.normcase(os.path.abspath(target))

    closed = 0
    for mod in list(_OPEN_ZIP_MODS):
        zp = mod.zip_path
        if zp is None:
            continue
        try:
            other = os.path.normcase(os.path.realpath(zp))
        except OSError:                          # pragma: no cover
            continue
        if other == key:
            mod.close()
            closed += 1
    return closed


def iter_mod_layers(mod: Mod) -> Iterator[vfsmod.Layer]:
    """VFS layers for a mod, with the engine's own precedence.

    Three layers, because a mod's loose tree is **not** uniformly dead.  The
    in-game experiment established one thing -- loose ``3DView/`` is never read
    -- and this function used to generalise it to the whole tree, mounting
    everything loose as ``loaded=False`` at a priority below stock.  That
    contradicted the three places that get it right (``ZIP_ONLY_ROOTS``,
    :attr:`ModFile.is_dead`, :meth:`Mod.deploy_target`) and it is refuted by the
    project's own load-bearing fact: ``inifiles/items.ini`` is read loose, which
    is the entire reason ``PRJ004`` and :meth:`Mod.create` exist.

    What it cost while it was wrong: a mod's loose override was invisible to the
    VFS, so the asset index recorded stock rather than the mod, ``check_atlas``
    validated the *stock* page for a mod shipping its own, and a saved atlas
    edit could not be read back by the tool that had just written it.

    Order: the zip wins over the loose tree (matching the engine, and matching
    Deploy's conflict rule), and both win over stock.
    """
    zp = mod.zip_path
    if zp:
        yield vfsmod.ZipLayer(zp, name=f"mod:{os.path.basename(zp)}", priority=2000)

    # Everything the engine really does read loose: inifiles/, scripts/,
    # strings/, sound/ -- i.e. all of it except the zip-only roots.  Above
    # stock (whose install layer is 1000), below the mod's own zip.
    yield vfsmod.DirectoryLayer(
        mod.root,
        name="mod:loose",
        priority=1900,
        loaded=True,
        skip=tuple(IGNORED_ROOTS) + tuple(ZIP_ONLY_ROOTS),
    )

    # The zip-only roots, loose -- registered so the app can still see them and
    # warn, never resolved.  Each was established by its own in-game probe, and
    # none is hypothetical: one large mod ships 424 such paths.
    yield vfsmod.DirectoryLayer(
        mod.root,
        name="mod:loose-zip-only",
        priority=50,
        loaded=False,
        skip=IGNORED_ROOTS,
        only=ZIP_ONLY_ROOTS,
    )


__all__ = [
    "VERSION",
    "Mod",
    "ModFile",
    "FileState",
    "DeployPlan",
    "DeployResult",
    "ProjectFile",
    "PROJECT_FILE",
    "MANIFEST",
    "USER_DATA",
    "VALIDITY_TOKEN",
    "base_game_fingerprint",
    "iter_mod_layers",
]
