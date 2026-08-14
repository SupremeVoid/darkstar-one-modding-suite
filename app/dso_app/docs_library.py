"""
The documentation the app ships, as data.

The suite's whole argument is that this game fails *silently*, and that what a
modder needs is not a bigger editor but a place where the rules are written
down. Those rules already exist -- in ``specs/``, hard-won and heavily
evidenced -- and until now they lived only in the repository, which is exactly
where the person using the packaged executable is not looking.

Qt-free on purpose, like every other piece of app logic here: finding the
documents, titling them, ordering them and resolving links between them is
answerable without a widget, and answering it here is what lets it be tested at
all -- the window layer has no unit tests by design.

WHERE THE FILES ARE
-------------------
``specs/`` is bundled into the frozen build already (``packaging/dso_app.spec``
``DATAS``), so the same folder name works from a checkout and from
``_internal/``. Nothing is generated and nothing is copied at runtime: the
markdown the repository holds *is* the documentation the app shows, so the two
cannot drift.
"""

from __future__ import annotations

import os
import posixpath
import re
import sys
from typing import List, Optional

VERSION = "1.0"

#: Documents worth showing, in the order a reader wants them, with the folder
#: they live in.  An explicit list rather than a glob:
#:
#: * ``docs/`` holds only this project's own working records -- ``STATE.md``,
#:   ``TODOS.md``, ``ARCHITECTURE.md`` -- which are about *building the
#:   suite* and would be noise, or worse, misleading, to someone modding
#:   the game;
#: * order carries meaning here. The guide comes first because it is the only
#:   document that answers "what do I do"; the format specs answer "how does
#:   this file work", which is the second question, not the first.
CATALOGUE = (
    ("specs/modding_guide.md", "Modding guide", "Start here"),
    ("specs/README.md", "Formats overview", "Start here"),
    ("specs/mod_packaging.md", "Mod packaging and scripting", "Formats"),
    ("specs/scene.md", "Scenes, models and materials", "Formats"),
    ("specs/3do_shd.md", "3DO geometry and shadows", "Formats"),
    ("specs/bsd9.md", "Shaders (.bsd9)", "Formats"),
    ("specs/aim.md", "Images (.aim)", "Formats"),
    ("specs/interface_formats.md", "Interface screens", "Formats"),
    ("specs/sound.md", "Sound", "Formats"),
    ("specs/string_tables.md", "String tables (.res)", "Formats"),
    ("specs/lua_api.md", "The Lua API", "Formats"),
    ("cli/README.md", "Command-line tools", "Command line"),
    ("cli/cli_3do.md", "Models — the round trip", "Command line"),
    ("cli/cli_aim.md", "Images and UI atlases", "Command line"),
)

def roots() -> List[str]:
    """Where the documentation might be, best first.

    Frozen, PyInstaller puts data beside the executable in ``_internal``, which
    is ``sys._MEIPASS``.  From a checkout it is the repository root, three
    levels up from this file.  Both are tried rather than branching on
    :func:`frozen.is_frozen`, because a developer running the frozen build's
    folder in place is a real case and the cost of one extra ``isdir`` is
    nothing.
    """
    found: List[str] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        found.append(meipass)
    here = os.path.dirname(os.path.abspath(__file__))
    found.append(os.path.dirname(os.path.dirname(here)))     # <repo>/app/dso_app
    found.append(os.path.dirname(os.path.abspath(sys.executable)))
    seen, out = set(), []
    for path in found:
        key = os.path.normcase(os.path.abspath(path))
        if key not in seen and os.path.isdir(path):
            seen.add(key)
            out.append(path)
    return out


def locate(relative: str) -> Optional[str]:
    """The full path of one catalogue entry, or ``None`` if it did not ship."""
    parts = relative.split("/")
    for root in roots():
        candidate = os.path.join(root, *parts)
        if os.path.isfile(candidate):
            return candidate
    return None


def documents() -> List[dict]:
    """Every catalogue entry that is actually present, in catalogue order.

    Absent entries are dropped rather than listed and then failing to open: a
    build that shipped without one of the specs should show a shorter list, not
    a list with a broken row in it.
    """
    out = []
    for relative, title, section in CATALOGUE:
        path = locate(relative)
        if path is None:
            continue
        out.append({
            "id": relative,
            "title": title,
            "section": section,
            "path": path,
            "name": posixpath.basename(relative),
        })
    return out


def read(document_id: str) -> str:
    """One document's markdown.

    Decoded as UTF-8 with replacement rather than strictly: these files carry
    measured evidence including names from a German-language game, and a
    mis-encoded byte should cost one character, not the whole page.
    """
    path = locate(document_id)
    if path is None:
        raise FileNotFoundError(document_id)
    with open(path, "rb") as handle:
        return handle.read().decode("utf-8", "replace")


def resolve_link(from_id: str, href: str) -> Optional[str]:
    """The catalogue id a link in ``from_id`` points at, or ``None``.

    Links between these files are written relative to the file holding them
    (``../specs/scene.md`` from ``docs/``, ``scene.md`` from within ``specs/``),
    so following one means resolving it against the *document's* folder and
    then checking the result is something the catalogue knows. Anything else --
    an external URL, a file that is not in the catalogue -- comes back as
    ``None`` so the caller can leave it to the browser rather than opening a
    blank page.
    """
    target = href.split("#", 1)[0]
    if not target or "://" in target:
        return None
    joined = posixpath.normpath(
        posixpath.join(posixpath.dirname(from_id), target))
    known = {entry[0].lower() for entry in CATALOGUE}
    if joined.lower() in known:
        return next(e[0] for e in CATALOGUE if e[0].lower() == joined.lower())
    # A bare file name also resolves, which is how the specs link to each other.
    tail = posixpath.basename(target).lower()
    for relative, _title, _section in CATALOGUE:
        if posixpath.basename(relative).lower() == tail:
            return relative
    return None


def outline(markdown: str) -> List[dict]:
    """``[{level, text}]`` for the ATX headings, for a contents list.

    Fenced code blocks are skipped: ``# comment`` inside a shell example is not
    a heading, and the specs are full of them.
    """
    out: List[dict] = []
    fence = None
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            token = stripped[:3]
            fence = None if fence else token
            continue
        if fence:
            continue
        match = re.match(r"^(#{1,4})\s+(.*\S)\s*$", line)
        if match:
            out.append({"level": len(match.group(1)),
                        "text": match.group(2).strip()})
    return out


def search(needle: str) -> List[dict]:
    """Documents containing ``needle``, with a count and the first line seen.

    Deliberately plain substring matching over the raw markdown. A reader
    looking for ``items.ini`` or ``PRJ005`` wants the file that mentions it,
    and any cleverness here would only mean explaining why an obvious match did
    not appear.
    """
    want = needle.strip().lower()
    if not want:
        return []
    hits = []
    for entry in documents():
        try:
            text = read(entry["id"])
        except OSError:
            continue
        lines = text.splitlines()
        found = [line.strip() for line in lines if want in line.lower()]
        if found:
            hits.append({**entry, "count": len(found), "first": found[0][:200]})
    hits.sort(key=lambda h: -h["count"])
    return hits


__all__ = ["VERSION", "CATALOGUE", "roots", "locate", "documents", "read",
           "resolve_link", "outline", "search"]
