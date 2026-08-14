"""
The asset index: every file in the game, and what references what.

WHY AN INDEX AT ALL
-------------------
A full install is ~11,400 virtual paths across six archives, and answering
"which scenes use this texture?" by re-parsing 1,040 XML files takes seconds.
The app needs it in milliseconds, on every selection change.

More importantly, the *reverse* direction is what makes the app worth using.
Forward questions ("what does this scene reference?") are answerable by opening
one file. Backward questions are not:

    what uses this texture?
    what breaks if I change this model?
    is anything still pointing at the file I just deleted?

Those are the questions a modder actually has, and none of them can be answered
without an index. So the ``refs`` table -- the binding graph -- is the point of
this module; the file listing is just what it hangs off.

STORAGE
-------
SQLite, from the standard library. No dependency, one file, and the query
planner does the joins better than hand-rolled dicts would. An index is
**derived and disposable**: it records a fingerprint of the layers it was built
from, and rebuilds itself when they change rather than trying to be clever about
partial invalidation.

Nothing here imports Qt. Building takes a ``progress`` callback rather than
printing, because the GUI runs it on a worker thread and the CLI wants a
different display.

THREADS
-------
The GUI *builds* an index on a worker thread and then *queries* it from the UI
thread -- that is the whole point of building it in the background. Python's
``sqlite3`` refuses this by default: a connection may only be used on the thread
that created it, and the violation surfaces as a ``ProgrammingError`` at the
first query after a rebuild, i.e. at the moment the feature appears to have
worked.

So the connection is opened with ``check_same_thread=False`` and **every**
statement goes through :attr:`AssetIndex._lock`. Those two go together: the flag
alone would merely move the failure from a clear exception to occasional
corruption under concurrent access, which is a strictly worse trade. The lock is
what actually makes it safe; the flag just stops sqlite3 vetoing a pattern that
is now legitimate.

Held locks are short -- a query, or one batch commit -- and the index is
read-mostly, so contention is not a concern. What is *not* supported is two
threads writing at once; there is one writer (``build``) by construction.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import struct
import threading
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from . import vfs as vfsmod
from .errors import DsoError
from .formats import a2d, dds, scene as scenefmt, sounddb

VERSION = "1.0"

SCHEMA_VERSION = 1

#: Reference kinds stored in the graph.
REF_MODEL = "model"        # scene -> .3do
REF_TEXTURE = "texture"    # scene -> .dds/.aim
REF_SHADER = "shader"      # scene -> .bsd9
REF_PAGE = "page"          # .tex  -> atlas .aim
REF_SOUND = "sound"        # sound db -> .wav/.mp3

_SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE assets (
    vpath      TEXT PRIMARY KEY,   -- lowercased virtual path
    display    TEXT NOT NULL,      -- original spelling
    origin     TEXT NOT NULL,      -- layer name that won
    size       INTEGER NOT NULL,
    format     TEXT,               -- '3do', 'scene', 'dds', ...
    meta       TEXT                -- format-specific JSON
);
CREATE INDEX assets_format ON assets(format);

CREATE TABLE refs (
    src   TEXT NOT NULL,           -- lowercased vpath of the referencing file
    kind  TEXT NOT NULL,
    raw   TEXT NOT NULL,           -- the reference exactly as written
    dst   TEXT,                    -- resolved lowercased vpath, NULL if unresolved
    slot  INTEGER,                 -- texture slot / record index, where meaningful
    node  TEXT                     -- object name inside the source, where meaningful
);
CREATE INDEX refs_src ON refs(src);
CREATE INDEX refs_dst ON refs(dst);
CREATE INDEX refs_unresolved ON refs(dst) WHERE dst IS NULL;
"""

_EXT_FORMAT = {
    ".3do": "3do",
    ".shd": "shd",
    ".dds": "dds",
    ".aim": "aim",
    ".tex": "tex",
    ".anim": "anim",
    ".screen": "screen",
    ".xml": "xml",
    ".ini": "ini",
    ".lua": "lua",
    ".bsd9": "shader",
    ".wav": "audio",
    ".mp3": "audio",
    ".gr2": "granny",
    ".cat": "camera",
    ".res": "resource",
}


def _fmt(vpath: str) -> Optional[str]:
    dot = vpath.rfind(".")
    return _EXT_FORMAT.get(vpath[dot:].lower()) if dot >= 0 else None


class AssetIndex:
    """A queryable index of a :class:`dsotools.vfs.Vfs`."""

    def __init__(self, db: sqlite3.Connection) -> None:
        self.db = db
        self.db.row_factory = sqlite3.Row
        #: Serialises every statement.  See the module docstring: the connection
        #: is deliberately not thread-checked, so this is the only thing keeping
        #: concurrent use safe.  Reentrant so that a future locked method calling
        #: another cannot deadlock; nothing re-enters it today.
        self._lock = threading.RLock()

    # -- lifecycle -----------------------------------------------------------

    @staticmethod
    def _connect(path: str) -> sqlite3.Connection:
        """Open a connection usable from more than one thread.

        ``check_same_thread=False`` is safe *here* only because every statement
        is taken under :attr:`_lock`; do not copy this flag anywhere that lacks
        the lock.
        """
        return sqlite3.connect(path, check_same_thread=False)

    @classmethod
    def create(cls, path: str = ":memory:") -> "AssetIndex":
        db = cls._connect(path)
        db.executescript(_SCHEMA)
        db.execute("INSERT INTO meta VALUES ('schema', ?)", (str(SCHEMA_VERSION),))
        # Commit the schema row immediately.  It used to ride along on build()'s
        # commit, so an index whose build failed part-way left a file with the
        # tables but no schema row -- which open() then rejected as
        # incompatible.  A half-built index should be empty, not unopenable.
        db.commit()
        return cls(db)

    @classmethod
    def open(cls, path: str) -> "AssetIndex":
        db = cls._connect(path)
        row = db.execute("SELECT value FROM meta WHERE key='schema'").fetchone()
        if row is None or int(row[0]) != SCHEMA_VERSION:
            raise DsoError(f"index at {path} has an incompatible schema; rebuild it")
        return cls(db)

    def execute(self, sql: str, args: Iterable = ()) -> List[sqlite3.Row]:
        """Run one statement under the lock and return all rows.

        Every query in this class goes through here.  Returning a list rather
        than a cursor is deliberate: a cursor consumed after the lock is
        released would be exactly the unsynchronised access the lock exists to
        prevent.
        """
        with self._lock:
            return self.db.execute(sql, tuple(args)).fetchall()

    @staticmethod
    def fingerprint(vfs: vfsmod.Vfs) -> str:
        """Identify the layer set, so a stale index can be detected.

        Layer names plus file counts plus total size. Cheap, and it changes
        whenever an archive is added, removed or replaced. It will not notice an
        edit that preserves the total byte count, which is why an index is
        treated as disposable rather than authoritative.
        """
        h = hashlib.sha1()
        for layer in vfs.layers:
            idx = layer.index()
            total = sum(size for _v, _r, size in idx.values())
            h.update(f"{layer.name}:{len(idx)}:{total};".encode())
        return h.hexdigest()

    def is_stale(self, vfs: vfsmod.Vfs) -> bool:
        rows = self.execute("SELECT value FROM meta WHERE key='fingerprint'")
        return not rows or rows[0][0] != self.fingerprint(vfs)

    # -- building ------------------------------------------------------------

    def build(
        self,
        vfs: vfsmod.Vfs,
        *,
        progress: Optional[Callable[[int, int, str], None]] = None,
        deep: bool = True,
    ) -> "AssetIndex":
        """Scan ``vfs`` and populate the index.

        ``deep`` parses scenes, atlases and sound databases to build the
        reference graph. With it off only the file listing is produced, which is
        fast enough to show a tree while the graph builds behind it.

        ``progress(done, total, vpath)`` is called as work proceeds; it must be
        cheap, and it must not touch the database.
        """
        paths = sorted(vfs.iter_paths())
        total = len(paths)

        assets: List[Tuple] = []
        refs: List[Tuple] = []

        for i, vpath in enumerate(paths):
            entry = vfs.find(vpath)
            if entry is None:  # pragma: no cover - iter_paths guarantees it
                continue
            fmt = _fmt(vpath)
            meta: Dict[str, object] = {}

            if deep:
                try:
                    meta, found = self._inspect(vfs, entry, vpath, fmt)
                    refs.extend(found)
                except DsoError:
                    meta = {"unreadable": True}
                except Exception:  # noqa: BLE001 - one bad file must not stop a scan
                    meta = {"unreadable": True}

            assets.append(
                (vpath.lower(), entry.vpath, entry.origin, entry.size, fmt,
                 json.dumps(meta) if meta else None)
            )
            if progress and (i % 64 == 0 or i == total - 1):
                progress(i + 1, total, vpath)

        # One critical section for the whole write.  Scanning happened outside
        # it -- that is the slow part, and holding the lock across ~11,400 file
        # reads would block every query in the UI for the duration.
        fingerprint = self.fingerprint(vfs)
        with self._lock:
            cur = self.db.cursor()
            cur.execute("DELETE FROM assets")
            cur.execute("DELETE FROM refs")
            cur.executemany("INSERT OR REPLACE INTO assets VALUES (?,?,?,?,?,?)", assets)
            cur.executemany("INSERT INTO refs VALUES (?,?,?,?,?,?)", refs)
            cur.execute("INSERT OR REPLACE INTO meta VALUES ('fingerprint', ?)", (fingerprint,))
            self.db.commit()
        return self

    def _inspect(self, vfs, entry, vpath, fmt):
        """Extract metadata and outgoing references from one asset."""
        meta: Dict[str, object] = {}
        refs: List[Tuple] = []
        low = vpath.lower()

        if fmt == "3do":
            head = entry.read()[:0x1000]
            if head[:4] == b"OD3 ":
                meta["submesh_total"] = struct.unpack_from("<I", head, 0x30)[0]

        elif fmt == "xml":
            data = entry.read()
            if scenefmt.is_scene(data):
                meta["format"] = "scene"
                sc = scenefmt.parse(data, path=vpath)
                meshes = sc.meshes()
                meta["meshes"] = len(meshes)
                meta["effects"] = sum(len(m.effects) for m in meshes)
                for obj in sc.walk():
                    if obj.model:
                        refs.append(
                            (low, REF_MODEL, obj.model,
                             self._resolve(vfs, obj.model, vpath), None, obj.name)
                        )
                    for eff in obj.effects:
                        if eff.shader:
                            refs.append(
                                (low, REF_SHADER, eff.shader,
                                 self._resolve(vfs, eff.shader, vpath), None, obj.name)
                            )
                        for slot, tex in enumerate(eff.textures):
                            refs.append(
                                (low, REF_TEXTURE, tex,
                                 self._resolve(vfs, tex, vpath), slot, obj.name)
                            )
            elif sounddb.is_sound_database(data):
                meta["format"] = "sounddb"
                db = sounddb.parse(data, path=vpath)
                for e in db.entries():
                    refs.append((low, REF_SOUND, e.resource,
                                 self._resolve(vfs, e.path(), vpath), None, e.name))

        elif fmt == "dds":
            # Header only matters here; parse() keeps mip payloads as unread
            # slices, so this costs one read and no decoding.
            img = dds.parse(entry.read(), path=vpath)
            meta.update(
                width=img.width, height=img.height, format=img.fourcc,
                mips=img.mip_count, cubemap=img.is_cubemap,
            )

        elif fmt == "tex":
            page = a2d.parse(entry.read())
            meta["page"] = page.page
            meta["subimages"] = len(page.subimages)
            refs.append((low, REF_PAGE, page.page, self._resolve(vfs, page.page, vpath), None, None))

        return meta, refs

    @staticmethod
    def _resolve(vfs, ref, scene_path):
        e = vfs.resolve_reference(ref, scene_path=scene_path)
        return e.vpath.lower() if e else None

    # -- queries -------------------------------------------------------------

    def asset(self, vpath: str) -> Optional[sqlite3.Row]:
        rows = self.execute(
            "SELECT * FROM assets WHERE vpath=?", (vfsmod.normalise(vpath).lower(),)
        )
        return rows[0] if rows else None

    def references_from(self, vpath: str) -> List[sqlite3.Row]:
        """What this file points at."""
        return self.execute(
            "SELECT * FROM refs WHERE src=? ORDER BY kind, slot",
            (vfsmod.normalise(vpath).lower(),),
        )

    def used_by(self, vpath: str) -> List[sqlite3.Row]:
        """**The reverse lookup.** What points at this file.

        This is the query the app exists to make instant: select a texture, see
        every scene that binds it, before deciding whether editing it is safe.
        """
        return self.execute(
            "SELECT DISTINCT src, kind, slot, node FROM refs WHERE dst=? ORDER BY src",
            (vfsmod.normalise(vpath).lower(),),
        )

    def unresolved(self) -> List[sqlite3.Row]:
        """Every reference that does not resolve -- broken bindings, project-wide."""
        return self.execute(
            "SELECT src, kind, raw, node FROM refs WHERE dst IS NULL ORDER BY src, kind"
        )

    def orphans(self, fmt: str) -> List[str]:
        """Assets of a format that nothing references.

        Not automatically a problem -- the engine loads plenty by name from code
        -- but it is where to look for dead weight in a mod.
        """
        rows = self.execute(
            "SELECT vpath FROM assets WHERE format=? AND vpath NOT IN "
            "(SELECT dst FROM refs WHERE dst IS NOT NULL) ORDER BY vpath",
            (fmt,),
        )
        return [r[0] for r in rows]

    def search(self, term: str, *, fmt: Optional[str] = None, limit: int = 200) -> List[sqlite3.Row]:
        sql = "SELECT vpath, display, origin, size, format FROM assets WHERE vpath LIKE ?"
        args: List[object] = [f"%{term.lower()}%"]
        if fmt:
            sql += " AND format=?"
            args.append(fmt)
        sql += " ORDER BY vpath LIMIT ?"
        args.append(limit)
        return self.execute(sql, args)

    def by_format(self) -> Dict[str, int]:
        return {
            r[0] or "(none)": r[1]
            for r in self.execute(
                "SELECT format, COUNT(*) FROM assets GROUP BY format ORDER BY 2 DESC"
            )
        }

    def by_origin(self) -> Dict[str, int]:
        return {
            r[0]: r[1]
            for r in self.execute(
                "SELECT origin, COUNT(*) FROM assets GROUP BY origin ORDER BY 2 DESC"
            )
        }

    def stats(self) -> Dict[str, object]:
        n_assets = self.execute("SELECT COUNT(*) FROM assets")[0][0]
        n_refs = self.execute("SELECT COUNT(*) FROM refs")[0][0]
        n_bad = self.execute("SELECT COUNT(*) FROM refs WHERE dst IS NULL")[0][0]
        return {
            "assets": n_assets,
            "references": n_refs,
            "unresolved": n_bad,
            "resolution": (1.0 - n_bad / n_refs) if n_refs else 1.0,
            "formats": self.by_format(),
        }

    def close(self) -> None:
        with self._lock:
            try:
                self.db.commit()
            finally:
                self.db.close()

    def __repr__(self) -> str:  # pragma: no cover
        try:
            s = self.stats()
            return f"<AssetIndex {s['assets']} assets, {s['references']} refs>"
        except sqlite3.Error:
            return "<AssetIndex closed>"


def build_index(
    vfs: vfsmod.Vfs, path: str = ":memory:", *, progress=None, deep: bool = True
) -> AssetIndex:
    """Convenience: create and populate an index in one call."""
    return AssetIndex.create(path).build(vfs, progress=progress, deep=deep)


__all__ = [
    "VERSION",
    "SCHEMA_VERSION",
    "AssetIndex",
    "build_index",
    "REF_MODEL",
    "REF_TEXTURE",
    "REF_SHADER",
    "REF_PAGE",
    "REF_SOUND",
]
