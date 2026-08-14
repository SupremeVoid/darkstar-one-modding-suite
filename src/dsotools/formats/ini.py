"""
Ascaron INI dialect.  v1.0

Not ``configparser``.  The game's ``.ini`` files break it in three ways that
matter, and silently:

* **Trailing comments on values.**  ``Radius = 15.4 ; Radius der Huellkugel``.
  ``configparser`` keeps ``15.4 ; Radius der Huellkugel`` as the value and every
  numeric read then fails, or worse, succeeds after a sloppy ``split()``.
* **cp1252, not UTF-8.**  German comments are full of ``ä ö ü ß``.  Decoding as
  UTF-8 raises; decoding as latin-1 silently mangles the curly quotes Ascaron's
  editor emitted.
* **Duplicate keys and sections.**  ``configparser`` raises on these by default.
  Real files contain them and the engine evidently tolerates them, so the parser
  must too -- and *report* them, because a duplicate key is usually a modder's
  mistake that the game resolves in a way they did not intend.

Round-trip is line-preserving: reading and writing an untouched file returns
identical bytes, and changing one value rewrites one line.  Same reasoning as
``scene.py`` -- the app's diff-against-stock feature is worthless if saving
reformats.
"""

from __future__ import annotations

import re
from typing import Dict, Iterator, List, Optional, Tuple

from ..errors import BuildError, ParseError

VERSION = "1.0"

ENCODING = "cp1252"

_SECTION_RE = re.compile(r"^(\s*)\[([^\]]*)\](.*)$")
_ENTRY_RE = re.compile(r"^(\s*)([^=;\[][^=]*?)(\s*)=(\s*)(.*)$")

#: Whitespace that has to become a plain space in a value.  A value occupies one
#: line by definition, so a newline in it does not "wrap" -- it ends the entry
#: and leaves the remainder as a line that parses as nothing at all.
_COLLAPSE = re.compile(r"[\r\n\t\v\f\x00-\x1f]+")


def check_value(value: str) -> str:
    """Return ``value`` in a form the dialect can actually store.

    Two different problems, handled differently on purpose.

    **Newlines and control characters are collapsed to spaces.**  An INI value
    is one line; there is no continuation syntax.  Writing a multi-line string
    verbatim produced a file where the description silently truncated at the
    first newline *and* the remainder sat there as an unparsable line that no
    later edit could remove, because nothing recognised it as an entry.  The
    orphan then accumulated on every save.  Collapsing is lossless in the way
    that matters -- the text is still all there, on the one line the format
    allows.

    **A ``;`` is rejected.**  It cannot be collapsed away without changing what
    the user wrote, and it cannot be escaped: no quoting or escaping exists in
    this dialect, so ``desc = adds ships; fixes bugs`` is read back by *this
    parser and by the game* as ``adds ships``.  Silently storing something the
    engine will truncate is worse than refusing, so this raises and the caller
    tells the user before they lose half a sentence.
    """
    text = _COLLAPSE.sub(" ", value or "").strip()
    if ";" in text:
        raise BuildError(
            "a value cannot contain ';' -- the game reads everything after it "
            "as a comment, so the text would be cut off at that point"
        )
    return text


def _split_comment(raw: str) -> Tuple[str, str]:
    """Split a value from its trailing ``;`` comment.

    No quoting or escaping has been observed in the shipped data, so the first
    ``;`` wins.  If that ever proves wrong it will show up as a value with a
    stray semicolon, which is easier to notice than a silently truncated one.

    **The gap before the ``;`` belongs to the comment, not to the value.**
    ``Hitpoints = 1200 ; Trefferpunkte`` has the value ``1200``, and keeping the
    trailing space on it had two costs: every reader had to ``strip()`` (and
    ``as_float`` did, while ``value`` did not, so the two disagreed), and
    *setting* a new value dropped the space -- rewriting the line as
    ``1200; Trefferpunkte`` and making a one-number edit show up as a
    formatting change in the diff.  Round-trip is unaffected: ``render``
    concatenates the two back together.
    """
    idx = raw.find(";")
    if idx < 0:
        return raw, ""
    value = raw[:idx]
    gap = len(value) - len(value.rstrip())
    return (value[: len(value) - gap], raw[idx - gap:]) if gap else (value, raw[idx:])


class Entry:
    """One ``key = value`` line."""

    __slots__ = ("key", "_value", "comment", "_indent", "_pre", "_post", "dirty", "line_no")

    def __init__(self, key, value, comment="", indent="", pre="", post=" "):
        self.key = key
        self._value = value
        self.comment = comment
        self._indent = indent
        self._pre = pre    # whitespace before '='
        self._post = post  # whitespace after '='
        self.dirty = False
        #: Index into IniFile._lines, so a changed value rewrites exactly one line.
        self.line_no = None

    @property
    def value(self) -> str:
        return self._value

    @value.setter
    def value(self, new: str) -> None:
        self._value = new
        self.dirty = True

    def as_float(self, default=None):
        try:
            return float(self._value.strip())
        except ValueError:
            return default

    def as_int(self, default=None):
        try:
            return int(self._value.strip(), 0)
        except ValueError:
            return default

    def render(self) -> str:
        return f"{self._indent}{self.key}{self._pre}={self._post}{self._value}{self.comment}"

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Entry {self.key}={self._value!r}>"


class Section:
    """One ``[name]`` block."""

    def __init__(self, name: str, header_line: Optional[int] = None) -> None:
        self.name = name
        self.entries: List[Entry] = []
        self.header_line = header_line

    def get(self, key: str, default=None) -> Optional[str]:
        for e in self.entries:
            if e.key.strip().lower() == key.strip().lower():
                return e.value.strip()
        return default

    def entry(self, key: str) -> Optional[Entry]:
        for e in self.entries:
            if e.key.strip().lower() == key.strip().lower():
                return e
        return None

    def set(self, key: str, value: str) -> Entry:
        """Change a key's value, adding the key if it is not there.

        Adding matters for real files: a hand-written ``darkstarmod.ini`` may
        carry only ``mod_name``, and refusing to write a description into it
        would make the app's own metadata editor fail on exactly the manifests
        people wrote themselves.
        """
        e = self.entry(key)
        if e is None:
            return self.add(key, value)
        e.value = check_value(value)
        return e

    def add(self, key: str, value: str) -> Entry:
        """Append a new ``key = value`` line to this section.

        Formatting is copied from the section's last entry so an added line
        looks like the ones around it rather than announcing that a tool wrote
        it.
        """
        model = self.entries[-1] if self.entries else None
        e = Entry(
            key,
            check_value(value),
            comment="",
            indent=model._indent if model else "",
            pre=model._pre if model else " ",
            post=model._post if model else " ",
        )
        e.dirty = True          # line_no stays None: to_bytes() splices it in
        self.entries.append(e)
        return e

    def duplicate_keys(self) -> List[str]:
        seen, dupes = set(), []
        for e in self.entries:
            k = e.key.strip().lower()
            if k in seen and k not in dupes:
                dupes.append(e.key.strip())
            seen.add(k)
        return dupes

    def __contains__(self, key) -> bool:
        return self.entry(key) is not None

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Section [{self.name}] {len(self.entries)} entries>"


class IniFile:
    """A parsed Ascaron INI, preserving every byte it did not change."""

    def __init__(self, lines, sections, newline="\r\n", path=None,
                 had_final_newline=True, terminators=None):
        self._lines = lines           # raw text lines, no terminators
        #: The exact terminator that followed each line, kept per line rather
        #: than as one file-wide setting.  A modder-authored file can mix CRLF
        #: and LF, and re-joining such a file with a single guessed newline
        #: silently rewrote every line that disagreed -- a whole-file diff from
        #: a parse that changed nothing.  The last entry is "" when the file
        #: does not end in a newline.
        self._terminators: List[str] = (
            list(terminators) if terminators is not None
            else [newline] * len(lines)
        )
        if had_final_newline is False and self._terminators:
            self._terminators[-1] = ""
        self.sections: List[Section] = sections
        self.newline = newline
        self.path = path

    def section(self, name: str) -> Optional[Section]:
        for s in self.sections:
            if s.name.strip().lower() == name.strip().lower():
                return s
        return None

    def get(self, section: str, key: str, default=None) -> Optional[str]:
        s = self.section(section)
        return s.get(key, default) if s else default

    def duplicate_sections(self) -> List[str]:
        seen, dupes = set(), []
        for s in self.sections:
            k = s.name.strip().lower()
            if k in seen and k not in dupes:
                dupes.append(s.name.strip())
            seen.add(k)
        return dupes

    def __iter__(self) -> Iterator[Section]:
        return iter(self.sections)

    def to_bytes(self) -> bytes:
        """Re-render, rewriting only lines whose value was changed.

        Entries with no ``line_no`` were added after parsing and are spliced in
        after the section's last existing line -- not appended to the file,
        which would silently move a key into whatever section happens to come
        last.
        """
        out = list(self._lines)
        ends = list(self._terminators)
        # line index -> lines to insert *after* it
        additions: Dict[int, List[str]] = {}

        for sec in self.sections:
            anchor = sec.header_line
            for e in sec.entries:
                if e.line_no is not None:
                    if e.dirty:
                        out[e.line_no] = e.render()
                    anchor = e.line_no
            for e in sec.entries:
                if e.line_no is None:
                    # -1 means "before everything": a section with no header and
                    # no lines, which only the implicit pre-section can be.
                    additions.setdefault(anchor if anchor is not None else -1, []
                                         ).append(e.render())

        if additions:
            spliced: List[str] = []
            spliced_ends: List[str] = []

            def emit(line, end):
                spliced.append(line)
                spliced_ends.append(end)

            for line in additions.get(-1, ()):
                emit(line, self.newline)
            for i, line in enumerate(out):
                emit(line, ends[i])
                for extra in additions.get(i, ()):
                    # The anchor line may be the last in the file and so carry
                    # no terminator.  It needs one now that something follows
                    # it; the new line inherits what the anchor had.
                    if not spliced_ends[-1]:
                        spliced_ends[-1] = self.newline
                        emit(extra, "")
                    else:
                        emit(extra, self.newline)
            out, ends = spliced, spliced_ends

        text = "".join(line + end for line, end in zip(out, ends))
        return text.encode(ENCODING, errors="replace")

    def __repr__(self) -> str:  # pragma: no cover
        return f"<IniFile {self.path or '<memory>'} {len(self.sections)} sections>"


def parse(data: bytes, *, path: Optional[str] = None) -> IniFile:
    """Parse an Ascaron INI.  Never raises on duplicates -- it reports them."""
    if isinstance(data, str):  # pragma: no cover - convenience
        text = data
        newline = "\r\n" if "\r\n" in data else "\n"
    else:
        try:
            text = data.decode(ENCODING)
        except UnicodeDecodeError as exc:
            raise ParseError(f"cannot decode as {ENCODING}: {exc}", path=path) from None
        newline = "\r\n" if b"\r\n" in data else "\n"

    # Split keeping each line's own terminator, so a file mixing CRLF and LF --
    # which a hand-edited mod file does -- comes back out exactly as it went in.
    lines: List[str] = []
    terminators: List[str] = []
    for raw in text.splitlines(keepends=True):
        stripped = raw.rstrip("\r\n")
        lines.append(stripped)
        terminators.append(raw[len(stripped):])

    sections: List[Section] = []
    current = Section("", None)  # entries before any [section]
    sections.append(current)

    for i, line in enumerate(lines):
        m = _SECTION_RE.match(line)
        if m:
            current = Section(m.group(2), i)
            sections.append(current)
            continue
        stripped = line.strip()
        if not stripped or stripped.startswith(";"):
            continue
        m = _ENTRY_RE.match(line)
        if not m:
            continue
        indent, key, pre, post, rest = m.groups()
        value, comment = _split_comment(rest)
        e = Entry(key, value, comment, indent, pre, post)
        e.line_no = i
        current.entries.append(e)

    if not sections[0].entries:
        sections.pop(0)
    return IniFile(lines, sections, newline=newline, path=path, terminators=terminators)


__all__ = ["VERSION", "ENCODING", "IniFile", "Section", "Entry", "parse", "check_value"]
