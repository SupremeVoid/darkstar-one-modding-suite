"""
Reading Lua source as text, for the checks that judge a script.

The game's scripts are Lua 4, and this module deliberately does **not** parse
them.  A parser is the wrong tool twice over: the editor has to say something
useful about a file the author is halfway through writing, which no parser will
accept, and the questions asked here -- *which functions does this call*, *what
does it define*, *does this ``Init`` return* -- are answerable from the token
stream.  Syntax is checked separately, by the game's own compiler, which is the
only authority worth having on it.

Everything here works on **comment-stripped** text where comments are blanked
in place rather than removed, so a line number computed afterwards is still the
line number in the file the user is looking at.

This module holds no policy.  It is given the tables to judge against --
what the executable registers, what the reference documents, what the Lua in
play defines -- and reports what it finds.  That is what lets one
implementation serve both the editor, which checks the file on screen, and the
validator, which checks every script in a mod.

Nothing here imports Qt.
"""

from __future__ import annotations

import re
from typing import Dict, Iterable, List, Optional, Sequence, Set

from . import scriptdoc

VERSION = "1.0"

#: What a StringId looks like: ALL_CAPS with underscores and digits.
LOOKS_LIKE_ID = re.compile(r"^[A-Z][A-Z0-9_]{2,}$")

#: ``NComm.AddMessage(`` -- a call to a namespace function.
CALL = re.compile(r"\b([A-Z]\w*)\.(\w+)\s*\(")

#: The two ways the shipped libraries declare one:
#: ``function MissionLib.X(`` and ``MissionLib.X = function``.
_DEF = re.compile(
    r"(?:^[ \t]*function[ \t]+([A-Za-z_]\w*)\.(\w+)[ \t]*\()"
    r"|(?:^[ \t]*([A-Za-z_]\w*)\.(\w+)[ \t]*=[ \t]*function)",
    re.M,
)

#: A long bracket at the point it opens: ``[[``, ``[=[``, ``[==[`` ...
_LONG_OPEN = re.compile(r"\[(=*)\[")


def strip_comments(text: str) -> str:
    """Blank out comments, keeping every line and column where it was.

    Line numbers still have to be right afterwards, so the text is replaced
    rather than removed.

    Long comments are matched at their own level: ``--[==[`` ends at ``]==]``
    and not at the first ``]]`` inside it.  Stock scripts use only ``--[[``,
    but a hand-written one closing a long comment early would otherwise have
    the rest of the file read as code.
    """
    out = list(text)
    i, n = 0, len(text)

    def blank(start: int, stop: int, keep_newlines: bool = True) -> None:
        for j in range(start, stop):
            if out[j] != "\n" or not keep_newlines:
                out[j] = " "

    while i < n:
        if text.startswith("--", i):
            opener = _LONG_OPEN.match(text, i + 2)
            if opener:
                close = "]" + opener.group(1) + "]"
                end = text.find(close, opener.end())
                end = n if end < 0 else end + len(close)
            else:
                end = text.find("\n", i)
                end = n if end < 0 else end
            blank(i, end)
            i = end
        else:
            i += 1
    return "".join(out)


def strip_strings(code: str) -> str:
    """Blank the contents of string literals, keeping the quotes and length.

    For the scans that read *structure* -- matching a bracket, finding the
    ``end`` that closes a ``function``.  A display string containing a bracket
    or the word ``end`` is not common in mission scripts, but it costs nothing
    to be right about it, and being wrong would mis-attribute a whole function
    body.

    Comments should be stripped first; this does not know about them.
    """
    out = list(code)
    i, n = 0, len(code)
    while i < n:
        ch = code[i]
        if ch in "\"'":
            j = i + 1
            while j < n and code[j] != ch and code[j] != "\n":
                j += 2 if code[j] == "\\" else 1
            for k in range(i + 1, min(j, n)):
                if out[k] != "\n":
                    out[k] = " "
            i = min(j + 1, n)
            continue
        opener = _LONG_OPEN.match(code, i)
        if opener:
            close = "]" + opener.group(1) + "]"
            end = code.find(close, opener.end())
            end = n if end < 0 else end
            for k in range(opener.end(), end):
                if out[k] != "\n":
                    out[k] = " "
            i = n if end == n else end + len(close)
            continue
        i += 1
    return "".join(out)


def definitions(code: str) -> Set[str]:
    """``{'MissionLib.RestoreShip', ...}`` -- what this source defines."""
    return {f"{a or c}.{b or d}" for a, b, c, d in _DEF.findall(code)}


def defined_in(sources: Iterable[str]) -> Set[str]:
    """Every function a batch of Lua sources defines, comments ignored."""
    found: Set[str] = set()
    for text in sources:
        found |= definitions(strip_comments(text))
    return found


# ---------------------------------------------------------------------------
# what is wrong with a script
# ---------------------------------------------------------------------------


def check(
    text: str,
    *,
    symbols: Dict[str, dict],
    engine: Optional[dict] = None,
    string_ids: Optional[Dict[str, Sequence[str]]] = None,
    defined: Iterable[str] = (),
) -> List[dict]:
    """What is wrong with this script, judged against the real build.

    Four kinds of finding, each measured rather than assumed:

    ``absent``
        The reference documents it and **the executable does not register
        it**.  Five functions are in this state, the whole ``NTutorial``
        namespace among them; a call to one fails at runtime with nothing to
        explain it.
    ``stub``
        Registered, but it does nothing -- ``NDebug.Message`` is 39 bytes that
        never read the argument.  The call succeeds and is silently lost,
        which cost this project two failed experiments.
    ``literal``
        A literal string passed where the documentation says the parameter is
        a **StringId** from ``user_strings.res``.  Nothing is displayed and
        nothing is reported: the single most confusing failure in mod
        scripting.
    ``unknown``
        Neither documented, nor registered, nor defined by the Lua in play.
        Usually a typo.

    ``defined`` is every function the *other* Lua in play defines -- the
    game's libraries and the mod's own.  What this file defines is added to
    it, so a script that calls its own helper stays quiet.  Passing nothing
    turns every library call into an ``unknown``, so a caller that cannot
    gather the sources is better off not running the check at all.

    Without ``symbols`` there is nothing to judge against and the result is
    empty rather than a wall of guesses.
    """
    if not symbols:
        return []

    code = strip_comments(text)
    engine_namespaces = set((engine or {}).get("namespaces", {}))
    implemented = scriptdoc.implemented(engine) if engine else set()
    stubs = scriptdoc.stubs(engine) if engine else {}
    documented_namespaces = {s["namespace"] for s in symbols.values()
                             if s["namespace"]}
    known = set(defined) | definitions(code)

    out: List[dict] = []
    seen = set()

    def add(kind, symbol, at, detail):
        key = (kind, symbol)
        if key in seen:
            return
        seen.add(key)
        out.append({
            "symbol": symbol,
            "kind": kind,
            "detail": detail,
            "line": code.count("\n", 0, at) + 1,
        })

    for match in CALL.finditer(code):
        namespace, name = match.group(1), match.group(2)
        qualified = f"{namespace}.{name}"

        if qualified in stubs:
            add("stub", qualified, match.start(), stubs[qualified])
            continue
        if namespace in engine_namespaces:
            if qualified in implemented:
                continue
            if qualified in symbols:
                add("absent", qualified, match.start(),
                    "documented, but this build does not register it")
            elif qualified not in known:
                add("unknown", qualified, match.start(),
                    "not in the reference, not in the build, and not "
                    "defined by the Lua in play")
        elif namespace in documented_namespaces:
            # A library that ships as Lua source -- MissionLib and friends.
            if qualified not in symbols and qualified not in known:
                add("unknown", qualified, match.start(),
                    "not in the reference and not defined by the Lua in play")

    out.extend(literal_string_ids(code, string_ids or {}))
    out.sort(key=lambda row: (row["line"], row["symbol"]))
    return out


def literal_string_ids(code: str,
                       wanted: Dict[str, Sequence[str]]) -> List[dict]:
    """Literals passed where a StringId is required.

    The reference names the offending parameters itself -- it describes them
    as "StringId from user_strings.res" -- so the list is derived rather than
    hand-maintained.  An identifier is ALL_CAPS with underscores; anything
    with a space or a lower-case letter is prose that will silently display
    nothing.

    ``code`` must already be comment-stripped, so that the reported line is
    the line in the file.
    """
    if not wanted:
        return []
    out: List[dict] = []
    seen = set()
    for match in CALL.finditer(code):
        qualified = f"{match.group(1)}.{match.group(2)}"
        names = wanted.get(qualified)
        if not names:
            continue
        window = code[match.end():match.end() + 400]
        for field in names:
            for assign in re.finditer(
                    rf'\b{re.escape(field)}\s*=\s*"([^"]*)"', window):
                value = assign.group(1)
                if LOOKS_LIKE_ID.match(value):
                    continue
                key = (qualified, field, value)
                if key in seen:
                    continue
                seen.add(key)
                out.append({
                    "symbol": f"{qualified} ({field})",
                    "kind": "literal",
                    "detail": f'"{value}" is text, but this parameter is a '
                              f"StringId from user_strings.res -- nothing "
                              f"will be shown",
                    "line": code.count("\n", 0, match.start()) + 1,
                })
    return out


__all__ = [
    "VERSION", "LOOKS_LIKE_ID", "CALL",
    "strip_comments", "strip_strings", "definitions", "defined_in",
    "check", "literal_string_ids",
]
