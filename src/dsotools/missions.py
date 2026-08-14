"""
The stock mission table, and how a mod replaces an entry in it.

``NScript.Register`` stores its record as ``SCRIPT_TABLE[Name] = record`` --
keyed by name, so registering a name that already exists **overwrites** it
rather than adding a second mission [code].  The script loader reads
``lua/mission/missions.bin`` first and a mod's ``scripts\\`` afterwards, so a
mod script always registers last and always wins.  That is the whole mechanism
behind overriding a stock mission; there is no dedicated API for it.

To offer that in a UI, the suite has to know what the stock missions *are*.
They exist only as Lua 4 bytecode inside ``missions.bin``, but the modding
tools' ``ScriptCompiler.exe`` disassembles it with string constants intact, and
the registration record sits in each chunk's main body in a fixed shape:

    GETGLOBAL   ; NScript
    GETDOTTED   ; Register
    CREATETABLE 4
    PUSHSTRING  ; "Name"          PUSHSTRING ; "<the mission name>"
    PUSHSTRING  ; "Group"         PUSHINT    <n>
    PUSHSTRING  ; "Type"          GETGLOBAL  ; MTYPE_*
    PUSHSTRING  ; "Transitions"
      per state: CREATETABLE 4, PUSHNIL 2, PUSHSTRING ; "<state>", CLOSURE

which is exactly the ``{float, float, string, function}`` tuple the reference
documents for ``Transitions``.  Reading it back out is therefore parsing, not
guessing -- but it is parsing a *disassembly*, so every record is checked for a
name and the extraction rate is reported rather than assumed.

Running the compiler needs Windows and the modding tools, so the result is
generated once by ``tools/stock_missions.py`` and shipped as
``data/stock_missions.json``.  The suite works from the shipped table; the tool
is only needed to regenerate it.
"""

from __future__ import annotations

import json
import os
import re
from typing import Dict, List, Optional

from . import luascan
from .errors import DsoError

VERSION = "1.0"
SCHEMA = 1

#: Where the generated table lives inside the package.
BUNDLED = "stock_missions.json"

#: The bundle the stock missions come from, relative to a game install.
STOCK_BUNDLE = "lua/mission/missions.bin"

#: Mission types the reference documents, in the order it lists them.
MISSION_TYPES = (
    "MTYPE_GLOBAL", "MTYPE_TERMINAL", "MTYPE_SPACE", "MTYPE_BAR",
    "MTYPE_VIDEO", "MTYPE_STORY", "MTYPE_STORY_TERMINAL", "MTYPE_ALWAYS",
)

_CHUNK = re.compile(r"^main <\d+:@(.*?)> \(")
_PUSHSTRING = re.compile(r"PUSHSTRING\s+\d+\s*;\s*\"(.*)\"$")
_GETGLOBAL = re.compile(r"GETGLOBAL\s+\d+\s*;\s*(\S+)$")
_PUSHINT = re.compile(r"PUSHINT\s+(-?\d+)$")
_CALL = re.compile(r"\bCALL\b")


class Mission:
    """One registered mission."""

    __slots__ = ("name", "type", "group", "states", "source")

    def __init__(self, name: str, type: Optional[str] = None,
                 group: Optional[int] = None,
                 states: Optional[List[str]] = None,
                 source: Optional[str] = None) -> None:
        self.name = name
        self.type = type
        self.group = group
        self.states = list(states or ())
        #: The chunk it came from, e.g. ``Game/lua/mission/ALWAYS_000.lua``.
        self.source = source

    @property
    def file_name(self) -> str:
        """What a script overriding this mission should be called."""
        return f"{self.name}.lua"

    def to_dict(self) -> dict:
        return {"name": self.name, "type": self.type, "group": self.group,
                "states": list(self.states), "source": self.source}

    @classmethod
    def from_dict(cls, d: dict) -> "Mission":
        return cls(d["name"], d.get("type"), d.get("group"),
                   d.get("states"), d.get("source"))

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Mission {self.name} {self.type} {len(self.states)} state(s)>"


def _record(lines: List[str]) -> Optional[dict]:
    """Read the ``NScript.Register`` record out of one chunk's instructions."""
    start = None
    for i, line in enumerate(lines):
        if "GETDOTTED" in line and line.rstrip().endswith("; Register"):
            if i and "; NScript" in lines[i - 1]:
                start = i + 1
                break
    if start is None:
        return None

    found: dict = {"name": None, "group": None, "type": None, "states": []}
    key = None
    after_two_nils = False
    for line in lines[start:]:
        body = line.strip()
        if "PUSHNIL" in body:
            # A transition entry opens with two nil floats; the string that
            # follows is the state name.
            after_two_nils = body.endswith("2")
            continue
        match = _PUSHSTRING.search(body)
        if match:
            value = match.group(1)
            if after_two_nils:
                found["states"].append(value)
                after_two_nils = False
                key = None
            elif key == "Name":
                found["name"] = value
                key = None
            else:
                key = value
            continue
        after_two_nils = False
        match = _GETGLOBAL.search(body)
        if match:
            if key == "Type":
                found["type"] = match.group(1)
            key = None
            continue
        match = _PUSHINT.search(body)
        if match:
            if key == "Group":
                found["group"] = int(match.group(1))
            key = None
            continue
        if _CALL.search(body):
            break
    return found if found["name"] else None


def parse_listing(text: str) -> List[Mission]:
    """Read every registration out of a ``ScriptCompiler -l`` disassembly.

    Chunks that register nothing are skipped rather than reported: a bundle
    legitimately carries libraries alongside missions.
    """
    chunks: Dict[str, List[str]] = {}
    current: Optional[str] = None
    for line in text.splitlines():
        if not line:
            continue
        match = _CHUNK.match(line)
        if match:
            current = match.group(1)
            chunks[current] = []
            continue
        if current is not None:
            chunks[current].append(line)

    out: List[Mission] = []
    for source, lines in chunks.items():
        found = _record(lines)
        if found:
            out.append(Mission(found["name"], found["type"], found["group"],
                               found["states"], source))
    return out


def index(bundle: str, compiler: Optional[str] = None) -> List[Mission]:
    """Disassemble a bundle and read its registrations.

    Needs ``ScriptCompiler.exe``; raises :class:`DsoError` without one rather
    than returning an empty list, because "no missions" and "no compiler" are
    very different answers.
    """
    from . import luac

    if not (compiler or luac.find_compiler()):
        raise DsoError(
            "ScriptCompiler.exe is needed to read a compiled bundle; install "
            "the Darkstar One Modding Tools or pass its path",
            path=bundle,
        )
    return parse_listing(luac.list_bundle(bundle, compiler=compiler))


# ---------------------------------------------------------------------------
# what a mod registers
# ---------------------------------------------------------------------------

#: The ``Name`` field of a registration.  That field, not the file name, is
#: what decides which mission a script is -- two stock chunks prove it:
#: ``BAR_006_02.lua`` registers ``BAR_006``.
_NAME_FIELD = re.compile(r"""\bName\s*=\s*["']([^"']+)["']""")



def registered_names(text: str) -> List[str]:
    """Every mission name a Lua source registers, in order, comments ignored.

    Deliberately a text scan rather than a parse: it has to work on a file the
    author is halfway through writing, which no parser will accept.  It only
    looks for the ``Name`` field, so a table with a ``Name`` that is not a
    registration would be a false positive -- none occurs in the stock scripts
    or in any mod examined.
    """
    seen: List[str] = []
    for found in _NAME_FIELD.findall(luascan.strip_comments(text)):
        if found not in seen:
            seen.append(found)
    return seen


def registrations(folder: str) -> Dict[str, List[str]]:
    """``{mission name: [script file, ...]}`` for a mod's ``scripts\\`` folder.

    A name in more than one file is a real problem: only the last registration
    survives, and which one that is depends on load order.
    """
    out: Dict[str, List[str]] = {}
    if not os.path.isdir(folder):
        return out
    for name in sorted(os.listdir(folder)):
        if not name.lower().endswith(".lua"):
            continue
        try:
            with open(os.path.join(folder, name), "rb") as handle:
                text = handle.read().decode("latin-1")
        except OSError:
            continue
        for mission in registered_names(text):
            out.setdefault(mission, []).append(name)
    return out


# ---------------------------------------------------------------------------
# does Init say yes?
# ---------------------------------------------------------------------------

#: ``Init`` returned a table carrying ``Ready``.
INIT_READY = "ready"
#: ``Init`` has no ``return`` in it at all -- the mission is never created.
INIT_NO_RETURN = "no-return"
#: ``Init`` returns something this scan cannot read, a helper call most
#: likely.  Reported by nothing: guessing here would cry wolf.
INIT_UNCLEAR = "unclear"
#: The registration declares no ``Init`` transition.  What the engine does
#: then has never been measured, so nothing is claimed about it.
INIT_ABSENT = "absent"

_REGISTER = re.compile(r"\bNScript\s*\.\s*Register\s*\(")
_INIT_STATE = re.compile(r'"Init"')
_FUNCTION = re.compile(r"\bfunction\b")

#: The words that open a block needing ``end``, and the one that closes it.
#: ``for`` and ``while`` are absent on purpose -- both are followed by ``do``,
#: which is counted, and counting them as well would double every loop.
#: ``repeat`` closes with ``until`` and needs no ``end``.
_BLOCK = re.compile(r"\b(function|if|do|end)\b")


def _closing_paren(masked: str, at: int) -> int:
    """Index just past the ``)`` closing the ``(`` at *at*, or the end."""
    depth = 0
    for i in range(at, len(masked)):
        if masked[i] == "(":
            depth += 1
        elif masked[i] == ")":
            depth -= 1
            if depth == 0:
                return i + 1
    return len(masked)


def _function_body(masked: str, at: int, stop: int):
    """``(start, end)`` of the body of the first function at or after *at*."""
    opener = _FUNCTION.search(masked, at, stop)
    if not opener:
        return None
    depth = 0
    for token in _BLOCK.finditer(masked, opener.start(), stop):
        if token.group(1) == "end":
            depth -= 1
            if depth == 0:
                return opener.end(), token.start()
        else:
            depth += 1
    return None


def init_states(text: str) -> List[tuple]:
    """``[(mission name, verdict), ...]`` for every registration in a source.

    ``Init`` is a readiness question: the engine creates the mission only if
    the call returns a table whose ``Ready`` is true.  Returning nothing is
    not an error and produces no log line -- the mission simply never runs,
    which is indistinguishable from a mod that failed to load.  This project's
    own generated template was wrong about it until stock bytecode was read.

    A text scan, for the same reason :func:`registered_names` is one: it has
    to work on a file that is halfway written.  It reads the ``Init``
    transition's function body and answers only what it can see, so a body
    ending in ``return SomeHelper( V )`` comes back as :data:`INIT_UNCLEAR`
    rather than as a fault.
    """
    code = luascan.strip_comments(text)
    masked = luascan.strip_strings(code)
    out: List[tuple] = []
    for call in _REGISTER.finditer(masked):
        start = call.end() - 1
        stop = _closing_paren(masked, start)
        name = _NAME_FIELD.search(code, start, stop)
        if not name:
            continue
        state = _INIT_STATE.search(code, start, stop)
        if not state:
            out.append((name.group(1), INIT_ABSENT))
            continue
        span = _function_body(masked, state.end(), stop)
        if span is None:
            out.append((name.group(1), INIT_ABSENT))
            continue
        body = masked[span[0]:span[1]]
        if re.search(r"\bReady\b", body):
            verdict = INIT_READY
        elif not re.search(r"\breturn\b", body):
            verdict = INIT_NO_RETURN
        else:
            verdict = INIT_UNCLEAR
        out.append((name.group(1), verdict))
    return out


def init_states_by_file(folder: str) -> List[tuple]:
    """``[(script file, mission name, verdict), ...]`` for a mod's scripts."""
    out: List[tuple] = []
    if not os.path.isdir(folder):
        return out
    for name in sorted(os.listdir(folder)):
        if not name.lower().endswith(".lua"):
            continue
        try:
            with open(os.path.join(folder, name), "rb") as handle:
                text = handle.read().decode("latin-1")
        except OSError:
            continue
        for mission, verdict in init_states(text):
            out.append((name, mission, verdict))
    return out

# ---------------------------------------------------------------------------
# the shipped table
# ---------------------------------------------------------------------------


def bundled_path() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", BUNDLED)


def bundled() -> Optional[dict]:
    """The generated table, or ``None`` when this build ships none."""
    path = bundled_path()
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError) as exc:
        raise DsoError(f"the shipped mission table is unreadable: {exc}",
                       path=path) from exc


def stock(table: Optional[dict] = None) -> List[Mission]:
    """Every stock mission, sorted by name."""
    table = bundled() if table is None else table
    if not table:
        return []
    return sorted((Mission.from_dict(d) for d in table.get("missions", ())),
                  key=lambda m: m.name)


def by_name(name: str, table: Optional[dict] = None) -> Optional[Mission]:
    wanted = name.strip().lower()
    for mission in stock(table):
        if mission.name.lower() == wanted:
            return mission
    return None


def save(missions: List[Mission], path: str, *, edition: Optional[str] = None,
         bundle: Optional[str] = None) -> str:
    payload = {
        "schema": SCHEMA,
        "version": VERSION,
        "edition": edition,
        "bundle": bundle,
        "missions": [m.to_dict() for m in sorted(missions, key=lambda m: m.name)],
    }
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=1, sort_keys=False)
        handle.write("\n")
    return path


# ---------------------------------------------------------------------------
# generating an override
# ---------------------------------------------------------------------------

#: States a mission is given when the stock record could not be read, or when
#: the caller is starting from nothing.  ``Init`` and ``Create`` are what every
#: stock mission declares first.
DEFAULT_STATES = ("Init", "Create")

_HEADER = """\
-- {name} -- overrides the stock mission of the same name.
--
-- NScript.Register keys the mission table by Name, and a mod's scripts\\ is
-- read after lua/mission/missions.bin, so this registration replaces the stock
-- {name} outright. Nothing else has to be done to disable the original.
--
-- Stock record: type {type}, group {group}, {count} state(s).
-- Every state the stock mission declared is listed below. A state left out
-- here does not exist for this mission any more.
"""

_NEW_HEADER = """\
-- {name} -- a new mission.
--
-- The name must be unique: NScript.Register keys the mission table by Name, so
-- reusing an existing one replaces that mission instead of adding this one.
"""

_BODY = """
NScript.Register( {{
    Name = "{name}",
    Group = {group},
    Type = {type},
    Transitions = {{
{states}    }},
}} )
"""

_STATE = """\
        {{
            nil, nil, "{state}",
            function( V, Data )
{body}            end
        }},
"""

#: ``Init`` decides whether the mission is created at all: it must return a
#: table whose ``Ready`` is true, and returning nothing means nothing happens
#: -- no error, no log line, the mission simply never runs.  Stock ``Init``
#: bodies end in exactly this, or in ``{ Ready = false }`` to decline.  A
#: template that left it out would produce a script that looks finished and is
#: inert, which is the worst kind of starting point.
#:
#: It is also **not called once**.  Measured in game on 2026-08-22: two ``Init``
#: calls for one ``Create``, on an overriding mission and on a fresh one alike,
#: both at the arrival that creates the mission and neither in the start system.
#: It is a question the engine re-asks, so a body with side effects pays for
#: them twice -- which the template has to say, because it invites the author
#: to put code exactly there.
STATE_BODIES = {
    "Init": "                -- Init is a readiness question, asked twice on the\n"
            "                -- arrival that creates the mission -- and never in\n"
            "                -- the start system. Keep it free of side effects.\n"
            "                -- Return Ready = false to decline this mission.\n"
            "                return { Ready = true }\n",
}


def _state_block(state: str) -> str:
    return _STATE.format(state=state, body=STATE_BODIES.get(state, ""))


def override_template(mission: Mission) -> str:
    """Lua source that replaces a stock mission, one stub per stock state."""
    states = list(mission.states) or list(DEFAULT_STATES)
    header = _HEADER.format(name=mission.name, type=mission.type or "MTYPE_ALWAYS",
                            group=mission.group if mission.group is not None else 0,
                            count=len(mission.states))
    return header + _BODY.format(
        name=mission.name,
        group=mission.group if mission.group is not None else 0,
        type=mission.type or "MTYPE_ALWAYS",
        states="".join(_state_block(s) for s in states),
    )


def new_template(name: str, type: str = "MTYPE_ALWAYS", group: int = 0,
                 states=DEFAULT_STATES) -> str:
    """Lua source for a mission that does not exist yet."""
    return _NEW_HEADER.format(name=name) + _BODY.format(
        name=name, group=group, type=type,
        states="".join(_state_block(s) for s in states),
    )


__all__ = [
    "VERSION", "SCHEMA", "BUNDLED", "STOCK_BUNDLE", "MISSION_TYPES",
    "DEFAULT_STATES", "Mission", "parse_listing", "index", "bundled",
    "bundled_path", "stock", "by_name", "save", "override_template",
    "registered_names", "registrations", "STATE_BODIES",
    "init_states", "init_states_by_file",
    "INIT_READY", "INIT_NO_RETURN", "INIT_UNCLEAR", "INIT_ABSENT",
    "new_template",
]
