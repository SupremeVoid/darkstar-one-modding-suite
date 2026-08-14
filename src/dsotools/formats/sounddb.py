"""
Ascaron ``ASE_Database`` sound definitions.  v2.0

The game's audio is ordinary WAV and MP3; what needed reverse engineering was
only the index that names them.  It is plain XML, in two places:

* ``KlangErzeugerDefault.xml`` at the game root -- the stock database, 285
  groups and 442 sounds;
* ``user_sounds.xml`` at a mod's root -- the same format, additive.

```
<ASE_Database>
  <Group Name="MUSIC" Volume="0.88" Wet="0.0">
    <Group Name="Death" Select="Random2">
      <Stream Name="66_gameover_final" Resrc="sound\\music(stream)\\..."
              Channels="2" Duration=":2048256" Freq="44100" />
    </Group>
  </Group>
  <Group Name="USER">
    <Sound2D Name="Click" Resrc="%MOD%sound\\sfx(2d)\\grp_USER\\Click.wav" ... />
  </Group>
</ASE_Database>
```

Four details that bite:

* **Groups nest.** 60 of the stock database's groups contain other groups, and
  a reader that only looks one level down finds **3 of 442** sounds -- which is
  exactly what the first version of this module did, and why the sound checks
  were quietly near-blind on any database but a flat one. A group's identity is
  therefore its *path*, ``MUSIC/Death``, not its name: names repeat.
* ``%MOD%`` expands to the mod root.  An unprefixed path resolves against the
  game.  Mixing the two up makes every reference look broken.
* Separators here are **backslashes**, unlike scene XML which uses forward
  slashes.  Both formats are read by the same engine; only the authoring tools
  differed.
* ``Select="Random2"`` on a group makes the engine choose among its children
  rather than play them all.  It is carried through edits untouched -- dropping
  it would silently turn a random pool into a chorus.

Checking this file in both directions -- declared-but-missing and
present-but-unreferenced -- found two genuinely broken voice lines in the first
mod it was pointed at, so both directions are exposed as first-class queries
rather than left to the caller.

Writing goes through :mod:`xmldoc`, so a database edited here comes back byte
for byte identical everywhere it was not touched.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Dict, Iterator, List, Optional

from ..errors import BuildError, ParseError
from . import xmldoc

VERSION = "2.0"

MOD_PREFIX = "%MOD%"

#: Element names that declare a playable resource.
SOUND_TAGS = ("Stream", "Sound2D", "Sound3D")

#: What the engine plays them through, for a caller that wants to explain it.
KIND_SUMMARY = {
    "Stream": "streamed from disk (music, radio, long atmospheres)",
    "Sound2D": "loaded, played without a position (UI, notifications)",
    "Sound3D": "loaded, positioned in the world",
}

#: Extensions the game ships.  MP3 for anything long, WAV for short effects.
AUDIO_SUFFIXES = (".mp3", ".wav")

#: The declaration, stripped before parsing so one encoding path serves every
#: file.  The original bytes are kept and re-emitted verbatim.
_DECL_RE = re.compile(r"^\s*<\?xml[^>]*\?>[\r\n]*")


class SoundEntry:
    """One declared sound."""

    __slots__ = ("kind", "name", "resource", "group", "attrs", "_el")

    def __init__(self, kind, name, resource, group, attrs, el=None):
        self.kind = kind                # Stream | Sound2D | Sound3D
        self.name = name
        self.resource = resource        # verbatim, including any %MOD%
        #: The owning group's **path**, e.g. ``MUSIC/Death``.  Names repeat;
        #: paths do not.
        self.group = group
        self.attrs = attrs
        self._el = el

    @property
    def is_mod_relative(self) -> bool:
        return self.resource.upper().startswith(MOD_PREFIX)

    def path(self) -> str:
        """The resource path with ``%MOD%`` stripped and separators normalised."""
        r = self.resource
        if self.is_mod_relative:
            r = r[len(MOD_PREFIX):]
        return r.replace("\\", "/").lstrip("/")

    def set_metadata(self, values: Dict[str, str]) -> None:
        """Rewrite the declared numbers -- ``Channels``, ``Freq``, ``Duration``.

        The engine reads these three from the database rather than from the
        file, so they are not documentation: a stale ``Duration`` cuts playback
        off where the database says the sound ends, which in game is
        indistinguishable from a corrupt file.  Both callers that rewrite them
        -- replacing a sound's file, and correcting a declaration that drifted
        -- go through here rather than through the element, so the element and
        the parsed attributes cannot fall out of step.
        """
        if self._el is None:
            raise BuildError(f"{self.name!r} has no element behind it")
        for key, value in values.items():
            self._el.set(key, value)
            self.attrs[key] = value

    @property
    def channels(self) -> Optional[int]:
        v = self.attrs.get("Channels")
        return int(v) if v and v.isdigit() else None

    @property
    def frequency(self) -> Optional[int]:
        v = self.attrs.get("Freq")
        return int(v) if v and v.isdigit() else None

    @property
    def duration(self) -> Optional[int]:
        """The ``Duration`` attribute in **samples**, or ``None``.

        Written as ``:1312128`` -- a leading colon, then a number. Samples, not
        milliseconds: dividing by ``Freq`` reproduces the real playing time to
        within 0.2% on every file checked against a decoder, and exactly on
        several. The leading colon is decoration; no file uses it to separate
        anything.
        """
        v = (self.attrs.get("Duration") or "").lstrip(":")
        return int(v) if v.isdigit() else None

    @property
    def seconds(self) -> Optional[float]:
        """Playing time, from ``Duration / Freq``.

        The database is the cheap source for this -- reading it costs nothing,
        where decoding 5,769 files to ask them would not.
        """
        samples, rate = self.duration, self.frequency
        if not samples or not rate:
            return None
        return samples / rate

    def __repr__(self) -> str:  # pragma: no cover
        return f"<{self.kind} {self.name!r} -> {self.path()}>"


class SoundGroup:
    """A group, which may hold sounds, other groups, or both."""

    __slots__ = ("name", "path", "attrs", "entries", "groups", "_el")

    def __init__(self, name, path, attrs, el=None):
        self.name = name
        #: Slash-joined ancestry, e.g. ``MUSIC/Death``.
        self.path = path
        self.attrs: Dict[str, str] = attrs
        self.entries: List[SoundEntry] = []
        self.groups: List["SoundGroup"] = []
        self._el = el

    @property
    def volume(self) -> Optional[str]:
        return self.attrs.get("Volume")

    @property
    def priority(self) -> Optional[str]:
        return self.attrs.get("Priority")

    @property
    def select(self) -> Optional[str]:
        """``Random2`` and friends: the engine picks one child, not all."""
        return self.attrs.get("Select")

    def walk(self) -> Iterator["SoundGroup"]:
        """This group and every group beneath it."""
        yield self
        for child in self.groups:
            yield from child.walk()

    def all_entries(self) -> Iterator[SoundEntry]:
        for group in self.walk():
            yield from group.entries

    def __repr__(self) -> str:  # pragma: no cover
        return (f"<SoundGroup {self.path!r} {len(self.entries)} sounds, "
                f"{len(self.groups)} groups>")


class SoundDatabase:
    """A parsed ``ASE_Database``.

    Holds the element tree and the source's own spelling so that an edit
    rewrites only what changed; see :mod:`xmldoc`.
    """

    __slots__ = ("groups", "loose", "properties", "path", "_root", "_layout",
                 "_decl", "_newline", "_trailing_newline")

    def __init__(self, groups, properties, path=None, loose=None, root=None,
                 layout=None, decl=b"", newline="\r\n", trailing_newline=True):
        #: Top-level groups.  Nested ones hang off these.
        self.groups: List[SoundGroup] = groups
        #: Sounds declared outside any group.
        self.loose: List[SoundEntry] = list(loose or ())
        self.properties: Dict[str, str] = properties
        self.path = path
        self._root = root
        self._layout = layout or {}
        self._decl = decl
        self._newline = newline
        self._trailing_newline = trailing_newline

    # -- reading -------------------------------------------------------------

    def all_groups(self) -> Iterator[SoundGroup]:
        for group in self.groups:
            yield from group.walk()

    def entries(self) -> Iterator[SoundEntry]:
        """Every sound, at any depth, in document order."""
        yield from self.loose
        for group in self.groups:
            yield from group.all_entries()

    def by_name(self) -> Dict[str, List[SoundEntry]]:
        """Name -> every entry with that name.

        A **list**, because names are group-scoped and repeat: the stock
        database has 38 names used more than once, every one of them in a
        different group and pointing at a different file
        (``FX_FighterExplosionDistant-03`` exists under three explosion
        groups). A flat name->entry index looks tidier and quietly loses them.
        """
        out: Dict[str, List[SoundEntry]] = {}
        for e in self.entries():
            out.setdefault(e.name, []).append(e)
        return out

    def by_qualified(self) -> Dict[str, SoundEntry]:
        """``group/name`` -> entry.  This is the index that is actually unique."""
        return {qualified(e): e for e in self.entries()}

    def find(self, name: str, group: Optional[str] = None) -> List[SoundEntry]:
        """Entries matching a name, optionally within one group path."""
        hits = self.by_name().get(name, [])
        if group is None:
            return list(hits)
        wanted = group.strip("/").lower()
        return [e for e in hits if e.group.lower() == wanted]

    def resolve(self, reference: str) -> Optional[SoundEntry]:
        """One entry from a ``group/name`` path, or a bare name if unambiguous."""
        exact = self.by_qualified().get(reference.strip("/"))
        if exact is not None:
            return exact
        hits = self.by_name().get(reference, [])
        return hits[0] if len(hits) == 1 else None

    def group(self, path: str) -> Optional[SoundGroup]:
        wanted = path.strip("/").lower()
        for group in self.all_groups():
            if group.path.lower() == wanted:
                return group
        return None

    @property
    def version(self) -> Optional[str]:
        return self.properties.get("Version")

    def referenced_paths(self) -> Dict[str, List[SoundEntry]]:
        """Lowercased resource path -> the entries naming it."""
        out: Dict[str, List[SoundEntry]] = {}
        for e in self.entries():
            out.setdefault(e.path().lower(), []).append(e)
        return out

    def duplicate_names(self) -> Dict[str, List[SoundEntry]]:
        """``group/name`` declared more than once -- a real clash.

        Scoped to one group deliberately. The same *name* in two groups is
        normal and the stock database does it 38 times; the same name twice in
        one group is the case where one entry cannot be addressed.
        """
        seen: Dict[str, List[SoundEntry]] = {}
        for e in self.entries():
            seen.setdefault(qualified(e), []).append(e)
        return {n: v for n, v in seen.items() if len(v) > 1}

    def missing(self, exists) -> List[SoundEntry]:
        """Entries whose file cannot be found.

        ``exists(path, mod_relative) -> bool`` decides; the caller owns the
        lookup because a mod-relative path and a game path resolve through
        different layers.
        """
        return [e for e in self.entries() if not exists(e.path(), e.is_mod_relative)]

    def unreferenced(self, shipped_paths) -> List[str]:
        """Files present in the mod that no entry names.

        The counterpart of :meth:`missing`.  Together they catch a mistyped
        folder, which shows up in *both* lists at once -- that pairing is what
        turns a guess into a diagnosis.
        """
        refd = set(self.referenced_paths())
        return sorted(p for p in shipped_paths if p.lower() not in refd)

    # -- writing -------------------------------------------------------------

    def to_bytes(self) -> bytes:
        """Serialise, preserving the source's own spelling where untouched."""
        if self._root is None:
            raise BuildError("this database was not parsed from a document",
                             path=self.path)
        return xmldoc.serialise(
            self._root, decl=self._decl, newline=self._newline,
            trailing_newline=self._trailing_newline, layout=self._layout,
            encoding=self._encoding(),
        )

    def _encoding(self) -> str:
        """Honour the declaration: the stock database says ISO-8859-1."""
        head = self._decl.decode("ascii", "replace").lower()
        if "iso-8859-1" in head or "latin" in head:
            return "latin-1"
        if "utf-8" in head:
            return "utf-8"
        return "cp1252"

    def add_entry(self, kind: str, name: str, resource: str, *,
                  group: Optional[str] = None, **attrs) -> SoundEntry:
        """Declare a sound.  ``group`` is a path; it is created if absent.

        Refuses a duplicate name, because the engine resolves by name and the
        loser of a clash is simply never heard.
        """
        if kind not in SOUND_TAGS:
            raise BuildError(f"{kind!r} is not one of {', '.join(SOUND_TAGS)}")
        if not name:
            raise BuildError("a sound needs a Name")
        # Only a clash *within the target group* is a problem: the stock
        # database reuses names freely across groups.
        if self.find(name, group or ""):
            where = f"group {group!r}" if group else "the top level"
            raise BuildError(f"a sound called {name!r} is already declared in "
                             f"{where}; one of the two could never be addressed")
        if self._root is None:
            raise BuildError("this database was not parsed from a document")

        parent_el, parent_group = self._ensure_group(group)
        el = ET.SubElement(parent_el, kind)
        el.set("Name", name)
        el.set("Resrc", resource)
        for k, v in attrs.items():
            if v is not None:
                el.set(k, str(v))
        _place(parent_el, el, _depth(parent_group) + 1)
        entry = SoundEntry(kind, name, resource,
                           parent_group.path if parent_group else "",
                           dict(el.attrib), el)
        if parent_group is not None:
            parent_group.entries.append(entry)
        else:
            self.loose.append(entry)
        return entry

    def remove_entry(self, reference: str) -> bool:
        """Delete a sound named by ``group/name``.  ``False`` if there was none.

        A bare name works only when it is unambiguous -- deleting the wrong one
        of three identically-named explosions would be silent and annoying to
        track down.
        """
        target = self.resolve(reference)
        if target is None:
            return False
        for group in list(self.all_groups()):
            if target in group.entries:
                if target._el is not None and group._el is not None:
                    group._el.remove(target._el)
                group.entries.remove(target)
                return True
        if target in self.loose:
            if target._el is not None and self._root is not None:
                self._root.remove(target._el)
            self.loose.remove(target)
            return True
        return False

    def set_resource(self, reference: str, resource: str) -> None:
        """Repoint a sound, named by ``group/name``, at a different file."""
        entry = self.resolve(reference)
        if entry is None:
            raise BuildError(f"no sound matches {reference!r}")
        if entry._el is None:
            raise BuildError(f"{reference!r} has no element behind it")
        entry._el.set("Resrc", resource)
        entry.resource = resource
        entry.attrs["Resrc"] = resource

    def _ensure_group(self, path: Optional[str]):
        """Return ``(element, SoundGroup or None)`` for a group path."""
        if not path:
            return self._root, None
        parts = [p for p in path.replace("\\", "/").split("/") if p]
        parent_el = self._root
        parent_group = None
        walked = []
        for part in parts:
            walked.append(part)
            here = "/".join(walked)
            found = self.group(here)
            if found is None:
                el = ET.SubElement(parent_el, "Group")
                el.set("Name", part)
                _place(parent_el, el, len(walked))
                found = SoundGroup(part, here, dict(el.attrib), el)
                if parent_group is None:
                    self.groups.append(found)
                else:
                    parent_group.groups.append(found)
            parent_el = found._el
            parent_group = found
        return parent_el, parent_group

    def __repr__(self) -> str:  # pragma: no cover
        return (f"<SoundDatabase {sum(1 for _ in self.all_groups())} groups, "
                f"{sum(1 for _ in self.entries())} sounds>")


def qualified(entry: SoundEntry) -> str:
    """``group/name``, the only reference that identifies a sound uniquely."""
    return f"{entry.group}/{entry.name}" if entry.group else entry.name


#: Two spaces a level, which is what every shipped database uses.
_INDENT = "  "


def _depth(group) -> int:
    """How many levels down a group sits; the root's children are 1."""
    return len(group.path.split("/")) if group is not None else 0


def _place(parent: ET.Element, added: ET.Element, depth: int) -> None:
    """Give a newly created element the whitespace the file would have used.

    Only new elements need this -- existing ones carry their own tails through
    from the source, which is what keeps an edit to a diff of one line. The
    element before this one has to give up its trailing whitespace, because
    that whitespace used to sit in front of the closing tag and now sits in
    front of a sibling.
    """
    inner = "\n" + _INDENT * depth
    outer = "\n" + _INDENT * (depth - 1)
    children = list(parent)
    if len(children) > 1:
        children[-2].tail = inner
    else:
        parent.text = inner
    added.tail = outer


def _read_group(el: ET.Element, prefix: str) -> SoundGroup:
    name = el.get("Name", "")
    path = f"{prefix}/{name}" if prefix else name
    group = SoundGroup(name, path, dict(el.attrib), el)
    for child in el:
        if child.tag == "Group":
            group.groups.append(_read_group(child, path))
        elif child.tag in SOUND_TAGS:
            res = child.get("Resrc")
            if res is not None:
                group.entries.append(
                    SoundEntry(child.tag, child.get("Name", ""), res, path,
                               dict(child.attrib), child))
    return group


def parse(data: bytes, *, path: Optional[str] = None) -> SoundDatabase:
    decl = xmldoc.split_declaration(data)
    newline = "\r\n" if b"\r\n" in data[:4096] else "\n"
    trailing_newline = data.endswith(b"\n")

    # Offsets for the layout capture must index the same bytes expat reads, so
    # everything below works on UTF-8 regardless of what the file declared.
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = data.decode("latin-1")
        except UnicodeDecodeError as exc:  # pragma: no cover - latin-1 is total
            raise ParseError(f"cannot decode sound database: {exc}",
                             path=path) from None
    # The declaration is dropped *before* the bytes are frozen, so the string
    # expat indexes and the string the layout slices are the same length. The
    # stock file declares ISO-8859-1 while these bytes are now UTF-8, and a
    # declaration that lies is worse than none; the original is kept in `decl`
    # and re-emitted verbatim.
    text = _DECL_RE.sub("", text, count=1)
    source = text.encode("utf-8")
    readable = source

    try:
        root = ET.fromstring(readable)
    except ET.ParseError as exc:
        raise ParseError(f"malformed sound database: {exc}", path=path) from None
    if root.tag != "ASE_Database":
        raise ParseError(f"root is <{root.tag}>, expected <ASE_Database>", path=path)

    props = {}
    dp = root.find("DocumentProperties")
    if dp is not None:
        for child in dp:
            props[child.tag] = (child.text or "").strip()

    groups = [_read_group(g, "") for g in root.findall("Group")]

    loose = []
    for child in root:
        if child.tag in SOUND_TAGS:
            res = child.get("Resrc")
            if res is not None:
                loose.append(SoundEntry(child.tag, child.get("Name", ""), res,
                                        "", dict(child.attrib), child))

    return SoundDatabase(
        groups, props, path=path, loose=loose, root=root,
        layout=xmldoc.layout_for(root, readable, source),
        decl=decl, newline=newline, trailing_newline=trailing_newline,
    )


def is_sound_database(data: bytes) -> bool:
    return b"<ASE_Database" in data[:512]


__all__ = [
    "VERSION",
    "SoundDatabase",
    "SoundGroup",
    "SoundEntry",
    "parse",
    "is_sound_database",
    "MOD_PREFIX",
    "qualified",
    "SOUND_TAGS",
    "KIND_SUMMARY",
    "AUDIO_SUFFIXES",
]
