"""
The official Lua API reference, turned into something a program can use.

WHY THIS EXISTS
---------------
``ds1doc_eng.chm``, shipped with Ascaron's *Darkstar One Modding Tools*, is a
complete reference for the scripting surface: every ``N*`` namespace function,
every ``MissionLib`` helper, every event, with parameter tables, return tables
and worked Lua examples.  That is exactly the database an editor needs for
completion, signature help and "is this function real?" -- and it is a parsing
job, not a reverse-engineering one.  Nothing in this module guesses at
anything: it reads documentation.

The CHM is a compiled HTML help file.  ``hh.exe -decompile`` (part of Windows)
unpacks it into a folder of ``.htm`` pages; :func:`extract` runs that, and
:func:`build` turns the folder into the database.

WHAT IS IN THERE
----------------
325 pages in three shapes, and the shape is what the page is:

* **command pages** (``Commands/<namespace>/<name>.htm``) and **MissionLib
  pages** -- a signature line, a description, a *Parameter table*, a *Return
  table* and usually an *Example*.  Both take a single Lua table argument and
  return one: ``NComm.AddMessage( { Text, Voice, ... } ) : { Message }``.
* **event pages** (``Events/<category>/<name>.htm``) -- the same, plus a
  *Trigger* section saying what fires the event, and no callable signature.
* **camera pages** (``Camera/<name>.htm``) -- ``CameraLib`` helpers, which are
  plain positional Lua functions and are documented as *Syntax* /
  *Description* / *Example* instead of with tables.

Plus ``contants.htm`` [sic], which lists the scripting constants by group.

OPTIONAL PARAMETERS ARE ITALICS
-------------------------------
The documentation marks an optional parameter by italicising it, in the
signature and again in the parameter table -- ``<i>Video</i>``.  There is no
other marker, so that is what :data:`Parameter.optional` reads.  Losing it
would turn "you may pass a video" into "you must".

COPYRIGHT
---------
The text belongs to Ascaron.  This module extracts it from a copy the user
already has, on their machine; it does not carry any of it.
"""

from __future__ import annotations

import html
import json
import os
import re
import subprocess
import sys
from html.parser import HTMLParser
from typing import Dict, List, Optional, Sequence

from .errors import DsoError, ParseError

VERSION = "1.0"

#: What the database format is; bumped when the shape below changes, so an app
#: reading a cached file can tell it is stale.
SCHEMA = 1

#: The three kinds of page, which are also the three kinds of symbol.
KIND_COMMAND = "command"
KIND_EVENT = "event"
KIND_CAMERA = "camera"

#: Sections a page is cut into, by their bold headings.
_SECTIONS = ("Parameter table", "Return table", "Example", "Trigger",
             "Syntax", "Description")


class Parameter:
    """One row of a parameter or return table."""

    __slots__ = ("name", "type", "values", "comment", "optional")

    def __init__(self, name, type="", values="", comment="", optional=False):
        self.name = name
        self.type = type
        self.values = values
        self.comment = comment
        self.optional = optional

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "type": self.type,
            "values": self.values,
            "comment": self.comment,
            "optional": self.optional,
        }

    def __repr__(self) -> str:                      # pragma: no cover - debug
        return f"<Parameter {self.name}{' (optional)' if self.optional else ''}>"


class Symbol:
    """One documented thing: a function, an event, or a camera helper."""

    __slots__ = ("name", "namespace", "category", "kind", "signature",
                 "summary", "parameters", "returns", "example", "trigger",
                 "page")

    def __init__(self, name, namespace, kind, signature="", summary="",
                 parameters=None, returns=None, example="", trigger="",
                 page="", category=""):
        self.name = name
        self.namespace = namespace
        #: How the documentation files it -- ``Cargo_Container`` for a
        #: MissionLib helper, ``Missionen`` for an event.  Grouping, not
        #: addressing: it is no part of how a script names the symbol.
        self.category = category
        self.kind = kind
        self.signature = signature
        self.summary = summary
        self.parameters: List[Parameter] = list(parameters or ())
        self.returns: List[Parameter] = list(returns or ())
        self.example = example
        self.trigger = trigger
        self.page = page

    @property
    def qualified(self) -> str:
        """How the symbol is written in a script.

        ``NComm.AddMessage`` for a function; for an **event**, the bare name --
        an event is named by the string in a script's event table, and
        ``Missionen.Timer`` is not a thing anyone can write.
        """
        if self.kind == KIND_EVENT or not self.namespace:
            return self.name
        return f"{self.namespace}.{self.name}"

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "namespace": self.namespace,
            "category": self.category,
            "kind": self.kind,
            "qualified": self.qualified,
            "signature": self.signature,
            "summary": self.summary,
            "parameters": [p.to_dict() for p in self.parameters],
            "returns": [p.to_dict() for p in self.returns],
            "example": self.example,
            "trigger": self.trigger,
            "page": self.page,
        }

    def __repr__(self) -> str:                      # pragma: no cover - debug
        return f"<Symbol {self.qualified}>"


# --------------------------------------------------------------------------
# reading one page
# --------------------------------------------------------------------------


class _Page(HTMLParser):
    """A page as a flat event stream, which is all the shapes have in common.

    The pages are hand-written HTML from 2006: tags are left unclosed, the
    ``<p>`` nesting does not survive a strict reading, and one file closes a
    table it never opened.  Recording a stream and cutting it into sections
    afterwards copes with that, where a tree walk would need every page to be
    well-formed.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.functionname = ""
        #: ``[(kind, payload)]`` -- ``("bold", text)``, ``("text", text)``,
        #: ``("pre", text)``, ``("table", [[cell, ...], ...])``.
        self.stream: List[tuple] = []
        self._in = []                    # open tags we care about
        self._buf: List[str] = []
        self._pre: List[str] = []
        self._table: Optional[List[List[str]]] = None
        self._row: Optional[List[str]] = None
        self._cell: Optional[List[str]] = None
        self._cell_italic = False
        self._italic_depth = 0

    # -- helpers ----------------------------------------------------------
    def _flush_text(self) -> None:
        text = _clean("".join(self._buf))
        self._buf = []
        if text:
            self.stream.append(("text", text))

    # -- HTMLParser -------------------------------------------------------
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "title":
            self._in.append("title")
        elif tag == "p" and attrs.get("id") == "functionname":
            self._flush_text()
            self._in.append("functionname")
        elif tag == "b":
            self._flush_text()
            self._in.append("bold")
        elif tag == "pre":
            self._flush_text()
            self._in.append("pre")
        elif tag == "table":
            self._flush_text()
            self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []
            self._cell_italic = False
        elif tag == "i":
            self._italic_depth += 1
            if self._cell is not None and not "".join(self._cell).strip():
                # Italic at the start of a cell is how an optional parameter
                # is marked; italic later in a cell is emphasis in prose.
                self._cell_italic = True
        elif tag == "br":
            if self._pre is not None and "pre" in self._in:
                self._pre.append("\n")

    def handle_endtag(self, tag):
        if tag == "title" and "title" in self._in:
            self._in.remove("title")
        elif tag == "p" and "functionname" in self._in:
            self._in.remove("functionname")
        elif tag == "b" and "bold" in self._in:
            self._in.remove("bold")
            text = _clean("".join(self._buf))
            self._buf = []
            if text:
                self.stream.append(("bold", text))
        elif tag == "pre" and "pre" in self._in:
            self._in.remove("pre")
            self.stream.append(("pre", _unindent("".join(self._pre))))
            self._pre = []
        elif tag in ("td", "th") and self._cell is not None:
            cell = _clean("".join(self._cell))
            if self._cell_italic:
                cell = "\x00" + cell          # carried out of the HTML, not shown
            self._row.append(cell)
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if any(c.strip() for c in self._row):
                self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            self.stream.append(("table", self._table))
            self._table = None
        elif tag == "i" and self._italic_depth:
            self._italic_depth -= 1

    def handle_data(self, data):
        if "title" in self._in:
            self.title += data
        elif "pre" in self._in:
            self._pre.append(data)
        elif self._cell is not None:
            self._cell.append(data)
        elif "functionname" in self._in:
            self.functionname += ("*" if self._italic_depth else "") + data
        else:
            self._buf.append(data)

    def close(self):
        super().close()
        self._flush_text()
        return self


def _clean(text: str) -> str:
    """Collapse the whitespace 2006 hand-written HTML is full of."""
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _unindent(text: str) -> str:
    """Keep an example's own indentation, drop the blank frame around it."""
    return text.replace("\r\n", "\n").strip("\n").rstrip()


def _rows_to_parameters(table: Sequence[Sequence[str]]) -> List[Parameter]:
    """A documentation table -> parameters, dropping its header row."""
    out: List[Parameter] = []
    for row in table:
        cells = [c for c in row]
        if not cells or not cells[0].strip("\x00 "):
            continue
        name = cells[0]
        optional = name.startswith("\x00")
        name = name.lstrip("\x00").strip()
        # The header row names the columns; it is not a parameter.
        if name.lower() in ("identifier", "field identifier", "name"):
            continue
        values = [c.lstrip("\x00").strip() for c in cells[1:]]
        out.append(Parameter(
            name=name,
            type=values[0] if len(values) > 0 else "",
            values=values[1] if len(values) > 1 else "",
            comment=values[2] if len(values) > 2 else "",
            optional=optional,
        ))
    return out


def parse_page(text: str, *, namespace: str = "", kind: str = KIND_COMMAND,
               page: str = "", category: str = "") -> Symbol:
    """One ``.htm`` page -> one :class:`Symbol`."""
    parsed = _Page()
    try:
        parsed.feed(text)
        parsed.close()
    except Exception as exc:                        # noqa: BLE001
        raise ParseError(f"cannot read the page: {exc}", path=page) from exc

    signature = _clean(parsed.functionname.replace("*", ""))
    # The **signature** names the symbol, not the page title.  Two event pages
    # carry a copied title: ``ActionCamStart.htm`` is titled ActionCamEnd, and
    # ``canyon.htm`` is titled Create.  A script writes what the signature
    # says, so a database keyed on the title would list one event twice and
    # the other not at all.
    from_signature = re.match(r"\s*([A-Za-z_]\w*)", signature)
    name = (from_signature.group(1) if from_signature
            else _clean(parsed.title) or "")
    symbol = Symbol(name=name, namespace=namespace, kind=kind,
                    signature=signature, page=page, category=category)

    # Cut the stream into sections at the bold headings.  Anything before the
    # first heading is the description.
    section = ""
    syntax = ""
    summary: List[str] = []
    for what, payload in parsed.stream:
        if what == "bold" and payload.strip(": ") in _SECTIONS:
            section = payload.strip(": ")
            continue
        if section == "":
            if what == "text":
                summary.append(payload)
        elif section == "Parameter table" and what == "table":
            symbol.parameters = _rows_to_parameters(payload)
        elif section == "Return table" and what == "table":
            symbol.returns = _rows_to_parameters(payload)
        elif section == "Example" and what == "pre":
            symbol.example = payload
        elif section == "Trigger" and what == "text":
            symbol.trigger = (symbol.trigger + " " + payload).strip()
        elif section == "Description" and what == "text":
            summary.append(payload)
        elif section == "Syntax" and what in ("text", "bold"):
            # CameraLib and some MissionLib pages carry the call in a Syntax
            # section instead of in the functionname paragraph, and repeat the
            # bare name there -- "LoadCargo" + "LoadCargo (Ship)".  Collected
            # separately so the name is not printed twice.
            syntax = (syntax + " " + payload).strip()

    # A Syntax section wins only when the functionname line is the bare name:
    # a command page's signature already carries its argument and return
    # tables, which is more than "(Ship)" says.
    if syntax and "(" not in symbol.signature and ":" not in symbol.signature:
        symbol.signature = _clean(syntax)

    symbol.summary = " ".join(s for s in summary if s).strip()
    if not symbol.name:
        symbol.name = _clean(parsed.title)
    if not symbol.name:
        raise ParseError("page has no title to name the symbol by", path=page)

    # Optionality is in the signature too, and the signature is what a page
    # always has -- a few tables mark it only there.
    for optional in re.findall(r"\*(\w+)", parsed.functionname):
        for candidate in symbol.parameters:
            if candidate.name == optional:
                candidate.optional = True
    return symbol


# --------------------------------------------------------------------------
# reading the whole tree
# --------------------------------------------------------------------------


def _constants(path) -> Dict[str, List[str]]:
    """``contants.htm`` [sic] -> ``{group: [name, ...]}``."""
    text = _read(path)
    out: Dict[str, List[str]] = {}
    group = ""
    for match in re.finditer(r"<h3>(.*?)</h3>|<li>(.*?)</li>", text,
                             re.I | re.S):
        heading, item = match.group(1), match.group(2)
        if heading is not None:
            group = _clean(re.sub(r"<[^>]+>", "", heading))
            out.setdefault(group, [])
        elif group:
            name = _clean(re.sub(r"<[^>]+>", "", item))
            if name:
                out[group].append(name)
    return {k: v for k, v in out.items() if v}


def _read(path) -> str:
    """The pages are cp1252; a stray byte must not lose the page."""
    with open(path, "rb") as handle:
        return handle.read().decode("cp1252", "replace")


def build(root, *, source: str = "") -> dict:
    """A decompiled CHM folder -> the API database.

    Raises rather than returning a half-database: an editor that offers three
    quarters of the API silently is worse than one that says it has none.
    """
    root = os.path.abspath(root)
    if not os.path.isdir(root):
        raise DsoError(f"not a folder: {root}", path=root)

    symbols: List[Symbol] = []
    failures: List[str] = []

    def collect(folder, kind, fixed_namespace=None):
        """Walk one documentation folder.

        The subfolder means different things per section, and conflating them
        would be wrong in an addressable way: under ``Commands`` it is the Lua
        namespace a script types, under ``MissionLib`` and ``Events`` it is
        only how the documentation files the page.
        """
        base = os.path.join(root, folder)
        if not os.path.isdir(base):
            return
        for dirpath, _dirnames, filenames in os.walk(base):
            for filename in sorted(filenames):
                if not filename.lower().endswith(".htm"):
                    continue
                full = os.path.join(dirpath, filename)
                rel = os.path.relpath(full, root).replace("\\", "/")
                parent = os.path.basename(dirpath)
                subfolder = "" if parent == folder else parent
                if fixed_namespace is None:
                    namespace, category = subfolder, ""
                else:
                    namespace, category = fixed_namespace, subfolder
                try:
                    symbols.append(parse_page(
                        _read(full), namespace=namespace, kind=kind, page=rel,
                        category=category))
                except DsoError as exc:
                    failures.append(f"{rel}: {exc}")

    collect("Commands", KIND_COMMAND)
    collect("MissionLib", KIND_COMMAND, "MissionLib")
    collect("Events", KIND_EVENT, "")
    collect("Camera", KIND_CAMERA, "CameraLib")

    # ``MissionLib.SetWingName`` is filed under two categories with the same
    # signature.  One symbol, documented twice: keep the first and say so
    # rather than list it twice in a completion popup.
    unique: List[Symbol] = []
    seen: Dict[str, Symbol] = {}
    duplicates: List[str] = []
    for symbol in symbols:
        previous = seen.get(symbol.qualified)
        if previous is not None and previous.signature == symbol.signature:
            duplicates.append(symbol.page)
            continue
        seen[symbol.qualified] = symbol
        unique.append(symbol)
    symbols = unique

    if failures:
        raise ParseError(
            f"{len(failures)} of the reference pages did not read: "
            + "; ".join(failures[:3]), path=root)
    if not symbols:
        raise DsoError(
            f"{root} holds no reference pages -- is it a decompiled "
            f"ds1doc_eng.chm?", path=root)

    constants = {}
    for candidate in ("contants.htm", "constants.htm"):
        full = os.path.join(root, candidate)
        if os.path.isfile(full):
            constants = _constants(full)
            break

    namespaces: Dict[str, List[str]] = {}
    for symbol in symbols:
        group = symbol.namespace or ("events" if symbol.kind == KIND_EVENT
                                     else "(global)")
        namespaces.setdefault(group, []).append(symbol.name)

    return {
        "schema": SCHEMA,
        "source": source or "ds1doc_eng.chm",
        "duplicate_pages": sorted(duplicates),
        "symbols": [s.to_dict() for s in sorted(
            symbols, key=lambda s: (s.namespace.lower(), s.name.lower()))],
        "constants": constants,
        "namespaces": {k: sorted(v, key=str.lower)
                       for k, v in sorted(namespaces.items())},
    }


# --------------------------------------------------------------------------
# getting at the CHM in the first place
# --------------------------------------------------------------------------


def extract(chm_path, dest) -> str:
    """Decompile a ``.chm`` into ``dest`` with Windows' own ``hh.exe``.

    ``hh.exe`` returns before it has finished writing and reports success
    either way, so the result is judged by what appeared on disk, not by its
    exit code.
    """
    chm_path, dest = os.path.abspath(chm_path), os.path.abspath(dest)
    if not os.path.isfile(chm_path):
        raise DsoError(f"no such file: {chm_path}", path=chm_path)
    if sys.platform != "win32":
        raise DsoError(
            "decompiling a .chm needs Windows' hh.exe; on another platform, "
            "run this once on Windows and keep the JSON", path=chm_path)
    os.makedirs(dest, exist_ok=True)

    try:
        subprocess.run(["hh.exe", "-decompile", dest, chm_path],
                       check=False, capture_output=True, timeout=300)
    except (OSError, subprocess.SubprocessError) as exc:
        raise DsoError(f"could not run hh.exe: {exc}", path=chm_path) from exc

    pages = [f for _d, _s, files in os.walk(dest) for f in files
             if f.lower().endswith(".htm")]
    if not pages:
        raise DsoError(
            f"hh.exe wrote no pages into {dest}; the file may not be a "
            f"readable .chm", path=chm_path)
    return dest


def find_chm(*roots) -> Optional[str]:
    """Locate ``ds1doc_eng.chm`` in an installation of the modding tools.

    The English reference is preferred over the German one, which ships beside
    it and documents the same API.
    """
    candidates = list(roots) or [
        r"C:\Program Files\Darkstar One Modding Tools",
        r"C:\Program Files (x86)\Darkstar One Modding Tools",
    ]
    best = None
    for root in candidates:
        if not root or not os.path.isdir(root):
            continue
        for dirpath, _dirnames, filenames in os.walk(root):
            for filename in filenames:
                low = filename.lower()
                if low == "ds1doc_eng.chm":
                    return os.path.join(dirpath, filename)
                if low.endswith(".chm") and low.startswith("ds1doc") and not best:
                    best = os.path.join(dirpath, filename)
    return best


# --------------------------------------------------------------------------
# using the database
# --------------------------------------------------------------------------


def save(database: dict, path) -> str:
    path = os.path.abspath(path)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(database, handle, ensure_ascii=False, indent=1, sort_keys=False)
    return path


def load(path) -> dict:
    """Read a saved database, refusing one written by a different schema."""
    try:
        with open(path, encoding="utf-8") as handle:
            database = json.load(handle)
    except (OSError, ValueError) as exc:
        raise DsoError(f"cannot read the API database: {exc}", path=str(path)) from exc
    if database.get("schema") != SCHEMA:
        raise DsoError(
            f"the API database is schema {database.get('schema')}, this build "
            f"reads {SCHEMA}; re-run tools/chm_to_json.py", path=str(path))
    return database


def index(database: dict) -> Dict[str, dict]:
    """``{'NComm.AddMessage': symbol}``, which is how a script names them."""
    out = {}
    for symbol in database.get("symbols", ()):
        out[symbol.get("qualified") or symbol["name"]] = symbol
    return out


# --------------------------------------------------------------------------
# what the engine actually registers
# --------------------------------------------------------------------------
#
# The documentation is not the build.  ``lua_engine.json`` is scanned out of
# the executable by ``tools/exe_api_scan.py`` and records the API that really
# exists, which differs from the reference in both directions -- five
# documented functions are absent and thirty undocumented ones are present.
# Validating against the documentation alone therefore produces both false
# alarms and false silence.

#: The generated engine table, beside the reference database.
BUNDLED_ENGINE = "lua_engine.json"


def engine_path() -> Optional[str]:
    """The shipped engine table, or ``None`` when this build has none."""
    from importlib.resources import files

    try:
        candidate = files("dsotools") / "data" / BUNDLED_ENGINE
        return str(candidate) if candidate.is_file() else None
    except (ModuleNotFoundError, FileNotFoundError, TypeError):
        return None


def engine() -> Optional[dict]:
    """Load the engine table, or ``None``."""
    path = engine_path()
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError) as exc:
        raise DsoError(f"cannot read the engine API table: {exc}",
                       path=path) from exc


def implemented(table: Optional[dict] = None) -> set:
    """``{'NComm.AddMessage', ...}`` -- every function the build registers."""
    table = engine() if table is None else table
    if not table:
        return set()
    return {f"{ns}.{name}"
            for ns, names in table.get("namespaces", {}).items()
            for name in names}


def stubs(table: Optional[dict] = None) -> Dict[str, str]:
    """``{symbol: why it does nothing}`` -- registered but inert."""
    table = engine() if table is None else table
    return dict((table or {}).get("stubs", {}))


#: A parameter documented like this takes an identifier from a string table,
#: never a literal.  Passing prose is the single most confusing failure in mod
#: scripting: the call succeeds and nothing is displayed.
_STRING_ID = re.compile(r"stringid", re.I)


def string_id_parameters(database: Optional[dict] = None) -> Dict[str, list]:
    """``{'NGUI.ShowInfoText': ['Text'], ...}`` from the reference itself."""
    database = bundled() if database is None else database
    out: Dict[str, list] = {}
    for symbol in (database or {}).get("symbols", ()):
        wanted = [p["name"] for p in symbol.get("parameters", ())
                  if _STRING_ID.search(p.get("values", ""))
                  or _STRING_ID.search(p.get("comment", ""))]
        if wanted:
            out[symbol.get("qualified") or symbol["name"]] = wanted
    return out


__all__ = [
    "VERSION", "SCHEMA", "KIND_COMMAND", "KIND_EVENT", "KIND_CAMERA",
    "Parameter", "Symbol", "parse_page", "build", "extract", "find_chm",
    "save", "load", "index", "bundled", "bundled_path",
    "BUNDLED_ENGINE", "engine", "engine_path", "implemented", "stubs",
    "string_id_parameters",
]


#: Where the generated database lives inside the package.
BUNDLED = "lua_api.json"


def bundled_path() -> Optional[str]:
    """The shipped database, or ``None`` when this build has none.

    A build without it is legitimate -- the file is generated from a CHM that
    ships with the modding tools, not with the game -- so callers ask rather
    than assume, and the Scripting tab says which case it is in.
    """
    from importlib.resources import files

    try:
        candidate = files("dsotools") / "data" / BUNDLED
        return str(candidate) if candidate.is_file() else None
    except (ModuleNotFoundError, FileNotFoundError, TypeError):
        return None


def bundled() -> Optional[dict]:
    """Load the shipped database, or ``None`` if this build has none."""
    path = bundled_path()
    return load(path) if path else None
