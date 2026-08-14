"""
Finding the game without asking the user.

The application should open, find Darkstar One, and be usable.  Making someone
type a path -- or worse, extract 12,767 files first -- is a burden the tool
exists to remove, and every step between launching and working is a step where
people give up.

So this module looks in the places the game actually installs to, in order of
how likely they are, and validates each candidate rather than trusting a
registry key that may point at an uninstalled game.  Manual selection stays as
the fallback, not the default.

Pure standard library.  ``winreg`` is imported lazily so the module works
everywhere; on non-Windows only the explicit and environment paths apply, which
is what CI and this project's own development environment use.
"""

from __future__ import annotations

import os
import re
from typing import List, Optional, Tuple

VERSION = "1.0"

#: A folder is the game if it has the executable and at least one archive.
#: Checking both matters: an uninstalled Steam entry can leave the folder behind
#: with nothing in it, and a stray folder named "DarkStar One" proves nothing.
EXECUTABLE = "DarkStarOne.exe"
ARCHIVE_SUFFIX = ".cpr"

#: Environment override, mostly for CI and scripted runs.
ENV_VAR = "DSO_GAME_DIR"

_FOLDER_NAMES = ("DarkStar One", "Darkstar One", "DarkStarOne", "Darkstar_One")


class Candidate:
    """A possible installation, with where it came from and whether it is real."""

    __slots__ = ("path", "source", "has_exe", "archives")

    def __init__(self, path: str, source: str) -> None:
        self.path = os.path.normpath(path)
        self.source = source
        self.has_exe = os.path.isfile(os.path.join(self.path, EXECUTABLE))
        try:
            self.archives = sorted(
                f for f in os.listdir(self.path) if f.lower().endswith(ARCHIVE_SUFFIX)
            )
        except OSError:
            self.archives = []

    @property
    def valid(self) -> bool:
        return self.has_exe and bool(self.archives)

    @property
    def why_not(self) -> str:
        if not os.path.isdir(self.path):
            return "folder does not exist"
        if not self.has_exe:
            return f"no {EXECUTABLE}"
        if not self.archives:
            return "no .cpr archives"
        return ""

    def __repr__(self) -> str:  # pragma: no cover
        state = "ok" if self.valid else f"rejected: {self.why_not}"
        return f"<Candidate {self.path!r} via {self.source} ({state})>"


# --------------------------------------------------------------------------
# sources
# --------------------------------------------------------------------------


def _reg_value(root, key: str, name: str) -> Optional[str]:
    try:
        import winreg
    except ImportError:
        return None
    try:
        with winreg.OpenKey(root, key) as handle:
            value, _kind = winreg.QueryValueEx(handle, name)
            return str(value)
    except OSError:
        return None


def steam_roots() -> List[str]:
    """Every Steam library folder on this machine.

    Steam moved the library list from ``config.vdf`` to
    ``steamapps/libraryfolders.vdf`` and changed its shape more than once, so
    this pulls every quoted absolute path out of the file rather than parsing
    VDF properly.  Over-collecting is harmless -- each candidate is validated.
    """
    try:
        import winreg
    except ImportError:
        return []

    steam = (
        _reg_value(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath")
        or _reg_value(
            winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"
        )
        or _reg_value(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath")
    )
    if not steam:
        return []

    roots = [steam]
    for vdf in (
        os.path.join(steam, "steamapps", "libraryfolders.vdf"),
        os.path.join(steam, "config", "libraryfolders.vdf"),
    ):
        try:
            with open(vdf, "r", encoding="utf-8", errors="replace") as fh:
                text = fh.read()
        except OSError:
            continue
        for match in re.findall(r'"(?:path|\d+)"\s+"([^"]+)"', text):
            path = match.replace("\\\\", "\\")
            if os.path.isabs(path):
                roots.append(path)
    seen, out = set(), []
    for r in roots:
        key = os.path.normcase(os.path.normpath(r))
        if key not in seen:
            seen.add(key)
            out.append(r)
    return out


def _registry_candidates() -> List[Candidate]:
    try:
        import winreg
    except ImportError:
        return []

    out: List[Candidate] = []
    # GOG records an explicit path per game; the id differs per release, so
    # enumerate rather than guess.
    for root, key in (
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\GOG.com\Games"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\GOG.com\Games"),
    ):
        try:
            with winreg.OpenKey(root, key) as handle:
                for i in range(winreg.QueryInfoKey(handle)[0]):
                    sub = winreg.EnumKey(handle, i)
                    name = _reg_value(root, f"{key}\\{sub}", "gameName") or ""
                    if "darkstar" in name.lower():
                        path = _reg_value(root, f"{key}\\{sub}", "path")
                        if path:
                            out.append(Candidate(path, "GOG registry"))
        except OSError:
            pass

    # Retail installers of the period wrote an uninstall entry with the folder.
    for root, key in (
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ):
        try:
            with winreg.OpenKey(root, key) as handle:
                for i in range(winreg.QueryInfoKey(handle)[0]):
                    sub = winreg.EnumKey(handle, i)
                    name = _reg_value(root, f"{key}\\{sub}", "DisplayName") or ""
                    if "darkstar" in name.lower():
                        path = _reg_value(root, f"{key}\\{sub}", "InstallLocation")
                        if path:
                            out.append(Candidate(path, "uninstall registry"))
        except OSError:
            pass
    return out


def _common_paths() -> List[str]:
    out = []
    for root in steam_roots():
        for name in _FOLDER_NAMES:
            out.append(os.path.join(root, "steamapps", "common", name))
    if os.name == "nt":
        bases = [
            os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
            os.environ.get("ProgramFiles", r"C:\Program Files"),
        ]
        for base in bases:
            for name in _FOLDER_NAMES:
                out.append(os.path.join(base, name))
                out.append(os.path.join(base, "Ascaron Entertainment", name))
                out.append(os.path.join(base, "Steam", "steamapps", "common", name))
    return out


# --------------------------------------------------------------------------
# api
# --------------------------------------------------------------------------


def candidates(extra: Optional[List[str]] = None) -> List[Candidate]:
    """Every place the game might be, best guess first, each validated."""
    found: List[Candidate] = []
    seen = set()

    def add(path: str, source: str) -> None:
        if not path:
            return
        key = os.path.normcase(os.path.normpath(path))
        if key in seen:
            return
        seen.add(key)
        found.append(Candidate(path, source))

    for p in extra or []:
        add(p, "given")
    env = os.environ.get(ENV_VAR)
    if env:
        add(env, f"${ENV_VAR}")
    for c in _registry_candidates():
        if os.path.normcase(c.path) not in seen:
            seen.add(os.path.normcase(c.path))
            found.append(c)
    for p in _common_paths():
        add(p, "Steam library" if "steamapps" in p.lower() else "common location")
    return found


def find_game(extra: Optional[List[str]] = None) -> Optional[str]:
    """The best valid installation, or ``None``.

    Returning ``None`` rather than raising is deliberate: "not found" is an
    ordinary state the UI handles by asking, not an error.
    """
    for c in candidates(extra):
        if c.valid:
            return c.path
    return None


def describe(extra: Optional[List[str]] = None) -> List[Tuple[str, str, str]]:
    """``(path, source, status)`` for every candidate, for a diagnostics view.

    Rejected candidates are included with the reason -- "we looked here and it
    had no .cpr files" is far more useful to a confused user than silence.
    """
    return [(c.path, c.source, "ok" if c.valid else c.why_not) for c in candidates(extra)]


def looks_like_game(path: str) -> bool:
    return Candidate(path, "check").valid


def looks_like_extracted(path: str) -> bool:
    """True for a folder of extracted archives (one directory per ``.cpr``)."""
    try:
        return any(
            os.path.isdir(os.path.join(path, d)) and d.lower().startswith("ds_")
            for d in os.listdir(path)
        )
    except OSError:
        return False


__all__ = [
    "VERSION",
    "Candidate",
    "candidates",
    "find_game",
    "describe",
    "looks_like_game",
    "looks_like_extracted",
    "steam_roots",
    "ENV_VAR",
    "EXECUTABLE",
]
