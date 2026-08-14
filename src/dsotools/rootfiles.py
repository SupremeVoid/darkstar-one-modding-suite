"""
Mod files that have to go into the **game installation folder**, and how to
put them there without losing what was underneath.

WHY THIS EXISTS
---------------
A mod folder under ``Customization\\`` is self-contained and reversible: delete
it and the game is stock again.  Some content cannot be delivered that way at
all.

Measured on a stock installation: **no ``.cpr`` archive holds a single
``lua/`` entry**.  The shared mission libraries -- ``MissionLib.lua``,
``BattleLib.lua``, ``CameraLib.lua`` and the compiled ``missions.bin`` -- exist
only as loose files in the game root, and a mission script says
``source "lua/mission/MissionLib.lua"``, a path resolved against that root.  A
mod that changes them has no choice but to overwrite the installation.  Seven
content roots are loose-only in the same way: ``lua`` (9 files in a stock
install), ``effects`` (23), ``frontend`` (3), ``interface3d`` (611),
``objectfieldscripts`` (342), ``particlescripts`` (1,501) and ``strings`` (3)
-- and ``video/subtitles``, which the VFS does not index at all.

One large mod is the worked example: it ships **two** archives, one for
``Customization\\`` and one whose readme says *"copy into game root"*, holding
nine ``lua/mission`` files and three subtitle XMLs.

WHAT GOES WRONG WITHOUT THIS
----------------------------
Overwriting is irreversible by hand.  On the machine this was written on, all
twelve of that mod's root files were already in place, byte-identical -- and
the stock ``MissionLib.lua`` they replaced **no longer exists anywhere**, not
in an archive, not in a backup.  Uninstalling that mod means verifying the game
files through Steam.  Swapping to another mod that touches the same files means
doing it blind.

So: a mod's install-folder payload lives in ``<mod>/root/``, mirroring the game
root; the manifest of what it delivers is recorded in the project's
``.dsoproj``; and this module installs it while **backing up whatever it
displaces**, into a ledger that lives in the game folder next to the files it
describes -- so an uninstall works even from a different machine, a fresh copy
of this tool, or after the mod folder is gone.

WHAT IS DELIBERATELY REFUSED
----------------------------
* Installing over a file another mod owns, unless asked to swap.
* Removing or restoring a file whose bytes changed since it was installed --
  that is somebody's edit, and it is reported rather than discarded.
* Claiming a stock file can be restored when there was never a copy of it.
  Files adopted from an install that already had them carry
  ``stock=unknown``, and uninstall says so instead of pretending.

Nothing here imports Qt, and nothing here touches ``user_data.zip`` -- that is
:mod:`dsotools.project`'s job.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import shutil
import zipfile
from typing import Dict, Iterable, List, Optional, Sequence

from .errors import DsoError, ProjectError

VERSION = "1.0"

#: Inside a mod folder: the tree that mirrors the game root.
PAYLOAD_DIR = "root"

#: Inside the game folder: what is installed, and what it displaced.  Kept
#: beside the files rather than in the app's settings, because the files
#: outlive any one machine's copy of this tool.
LEDGER = ".dso_installed.json"
BACKUP_DIR = ".dso_backup"
LEDGER_SCHEMA = 1

#: Never delivered into a game installation, whatever a payload folder holds.
#: The first two are this tool's own bookkeeping; the rest are the game's
#: identity and would turn a mod into a patch of the executable.
REFUSED = (
    LEDGER.lower(),
    BACKUP_DIR.lower() + "/",
    "darkstarone.exe",
    "unins000.exe",
    "unins000.dat",
)

#: Extensions that are somebody's synchronisation droppings rather than mod
#: content.  One mod ships ``lua/mission/sync.ffs_lock``, a
#: FreeFileSync lock file that has been sitting in the game folder ever since.
JUNK_SUFFIXES = (".ffs_lock", ".ffs_db", "thumbs.db", "desktop.ini", ".ds_store")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _now() -> str:
    return datetime.datetime.now().replace(microsecond=0).isoformat()


def normalise(relative: str) -> str:
    """A game-root-relative path, in the one spelling everything here uses."""
    text = str(relative).replace("\\", "/").strip("/")
    while "//" in text:
        text = text.replace("//", "/")
    return text


def is_refused(relative: str) -> bool:
    low = normalise(relative).lower()
    if any(low == r or low.startswith(r) for r in REFUSED):
        return True
    # Nothing may escape the game folder, and nothing may be absolute.
    if low.startswith("../") or "/../" in low or os.path.isabs(relative):
        return True
    return False


def is_junk(relative: str) -> bool:
    low = normalise(relative).lower()
    return low.endswith(JUNK_SUFFIXES) or os.path.basename(low) in JUNK_SUFFIXES


# --------------------------------------------------------------------------
# what a mod delivers
# --------------------------------------------------------------------------


class PayloadFile:
    """One file a mod wants placed in the game folder."""

    __slots__ = ("path", "size", "sha256", "source")

    def __init__(self, path: str, size: int, sha256: str, source: str = ""):
        #: Game-root-relative, forward slashes: ``lua/mission/Tools.lua``.
        self.path = path
        self.size = size
        self.sha256 = sha256
        #: Where the bytes come from on disk.
        self.source = source

    def to_dict(self) -> dict:
        return {"path": self.path, "size": self.size, "sha256": self.sha256}

    def __repr__(self) -> str:                      # pragma: no cover - debug
        return f"<PayloadFile {self.path} {self.size}B>"


def payload_dir(mod_root: str) -> str:
    return os.path.join(mod_root, PAYLOAD_DIR)


def payload(mod_root: str) -> Dict[str, PayloadFile]:
    """Scan ``<mod>/root/`` -- what this mod delivers into the installation."""
    base = payload_dir(mod_root)
    out: Dict[str, PayloadFile] = {}
    if not os.path.isdir(base):
        return out
    for dirpath, _dirnames, filenames in os.walk(base):
        for filename in sorted(filenames):
            full = os.path.join(dirpath, filename)
            relative = normalise(os.path.relpath(full, base))
            if is_refused(relative):
                continue
            out[relative] = PayloadFile(
                path=relative,
                size=os.path.getsize(full),
                sha256=_sha256_file(full),
                source=full,
            )
    return out


def import_zip(zip_path: str, mod_root: str, *,
               only: Optional[Sequence[str]] = None,
               skip_junk: bool = True) -> List[str]:
    """Unpack a "copy this into the game root" archive into ``<mod>/root/``.

    That is the shape these mods are distributed in -- the measured mod's
    second archive is exactly a game root fragment.  Importing it makes the
    payload a tracked part of the project instead of something the user copies
    by hand and cannot undo.

    ``only`` keeps just the given top-level folders.  Junk (a stray
    ``sync.ffs_lock``) is skipped unless asked for: it is not mod content, and
    installing it puts somebody's synchronisation droppings in a game folder.
    """
    base = payload_dir(mod_root)
    written: List[str] = []
    with zipfile.ZipFile(zip_path) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            relative = normalise(info.filename)
            if not relative or is_refused(relative):
                continue
            if skip_junk and is_junk(relative):
                continue
            if only and relative.split("/")[0].lower() not in {
                    o.lower().strip("/") for o in only}:
                continue
            target = os.path.join(base, relative.replace("/", os.sep))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with archive.open(info) as source, open(target, "wb") as sink:
                shutil.copyfileobj(source, sink)
            written.append(relative)
    return sorted(written)


# --------------------------------------------------------------------------
# the ledger, which lives with the installation
# --------------------------------------------------------------------------


def ledger_path(game_root: str) -> str:
    return os.path.join(game_root, LEDGER)


def load_ledger(game_root: str) -> dict:
    """What this installation currently carries.  Empty when nothing does."""
    path = ledger_path(game_root)
    if not os.path.isfile(path):
        return {"schema": LEDGER_SCHEMA, "mods": {}}
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        raise ProjectError(
            f"the install ledger is unreadable: {exc}. Move it aside to start "
            f"over -- but then nothing can be uninstalled automatically",
            path=path) from exc
    if data.get("schema") != LEDGER_SCHEMA:
        raise ProjectError(
            f"the install ledger is schema {data.get('schema')}, this build "
            f"reads {LEDGER_SCHEMA}", path=path)
    data.setdefault("mods", {})
    return data


def save_ledger(game_root: str, ledger: dict) -> str:
    path = ledger_path(game_root)
    ledger["schema"] = LEDGER_SCHEMA
    ledger["updated"] = _now()
    payload_text = json.dumps(ledger, indent=1, sort_keys=True)
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        handle.write(payload_text)
    os.replace(temporary, path)
    return path


def installed_mods(game_root: str) -> List[str]:
    return sorted(load_ledger(game_root).get("mods", {}))


def owner_of(ledger: dict, relative: str) -> Optional[str]:
    """Which mod put this file here, if any.

    Compared case-insensitively: the ledger records a path as the payload
    spells it (``lua/mission/MissionLib.lua``) while other parts of the suite
    normalise to lower case, and on Windows those are the same file.  Matching
    exactly made every tracked file look untracked, which in the Project tab
    read as "nothing owns this" and offered to adopt it a second time.
    """
    wanted = normalise(relative).lower()
    for name, record in ledger.get("mods", {}).items():
        for path in record.get("files", {}):
            if normalise(path).lower() == wanted:
                return name
    return None


# --------------------------------------------------------------------------
# planning
# --------------------------------------------------------------------------

#: What installing one file would do.
NEW = "new"                 # nothing there now
REPLACE_STOCK = "replace"   # something there, nobody claims it
REPLACE_MINE = "update"     # this mod already installed a version
CONFLICT = "conflict"       # another mod owns it
SAME = "same"               # byte-identical to what is already there


class Action:
    """One line of an install plan."""

    __slots__ = ("path", "what", "owner", "size")

    def __init__(self, path, what, owner=None, size=0):
        self.path = path
        self.what = what
        self.owner = owner
        self.size = size

    def __repr__(self) -> str:                      # pragma: no cover - debug
        return f"<{self.what} {self.path}>"


def plan(game_root: str, mod_name: str,
         files: Dict[str, PayloadFile]) -> List[Action]:
    """What installing ``files`` as ``mod_name`` would do to this game folder."""
    ledger = load_ledger(game_root)
    mine = ledger.get("mods", {}).get(mod_name, {}).get("files", {})
    out: List[Action] = []
    for relative, item in sorted(files.items()):
        target = os.path.join(game_root, relative.replace("/", os.sep))
        owner = owner_of(ledger, relative)
        if not os.path.exists(target):
            what = NEW
        elif _sha256_file(target) == item.sha256:
            what = SAME
        elif relative in mine:
            what = REPLACE_MINE
        elif owner and owner != mod_name:
            what = CONFLICT
        else:
            what = REPLACE_STOCK
        out.append(Action(relative, what, owner, item.size))
    return out


class Result:
    """What an install or uninstall actually did."""

    def __init__(self) -> None:
        self.written: List[str] = []
        self.removed: List[str] = []
        self.restored: List[str] = []
        self.skipped: Dict[str, str] = {}
        self.backed_up: List[str] = []

    @property
    def clean(self) -> bool:
        return not self.skipped

    def summary(self) -> str:
        bits = []
        for label, items in (("written", self.written), ("removed", self.removed),
                             ("restored", self.restored)):
            if items:
                bits.append(f"{len(items)} {label}")
        if self.backed_up:
            bits.append(f"{len(self.backed_up)} backed up")
        if self.skipped:
            bits.append(f"{len(self.skipped)} skipped")
        return ", ".join(bits) or "nothing to do"

    def __repr__(self) -> str:                      # pragma: no cover - debug
        return f"<Result {self.summary()}>"


# --------------------------------------------------------------------------
# doing it
# --------------------------------------------------------------------------


def _backup(game_root: str, relative: str) -> str:
    """Copy what is at ``relative`` into the vault; return the vault name.

    Named by content, so installing three mods that displace the same stock
    file keeps one copy of it and every ledger entry can point at it.
    """
    source = os.path.join(game_root, relative.replace("/", os.sep))
    digest = _sha256_file(source)
    vault = os.path.join(game_root, BACKUP_DIR)
    os.makedirs(vault, exist_ok=True)
    name = f"{digest}.bin"
    target = os.path.join(vault, name)
    if not os.path.exists(target):
        shutil.copy2(source, target)
    return name


def install(game_root: str, mod_name: str, mod_root: str, *,
            allow_conflicts: bool = False,
            files: Optional[Dict[str, PayloadFile]] = None) -> Result:
    """Place a mod's ``root/`` payload into the game folder.

    Every file that already exists and is not already backed up is copied into
    the vault first, so the state before this mod can be put back.  A file
    another mod owns stops the install unless ``allow_conflicts``.
    """
    if not os.path.isdir(game_root):
        raise DsoError(f"no such game folder: {game_root}", path=game_root)
    items = payload(mod_root) if files is None else files
    if not items:
        raise ProjectError(
            f"{mod_name} has no install-folder payload: {payload_dir(mod_root)} "
            f"is empty or missing", path=mod_root)

    steps = plan(game_root, mod_name, items)
    conflicts = [a for a in steps if a.what == CONFLICT]
    if conflicts and not allow_conflicts:
        owners = sorted({a.owner for a in conflicts if a.owner})
        raise ProjectError(
            f"{len(conflicts)} file(s) are owned by {', '.join(owners)}: "
            f"{', '.join(a.path for a in conflicts[:3])}. Uninstall that mod "
            f"first, or swap to this one", path=game_root)

    ledger = load_ledger(game_root)
    record = ledger["mods"].setdefault(
        mod_name, {"installed": _now(), "files": {}})
    record["updated"] = _now()
    result = Result()

    for action in steps:
        item = items[action.path]
        target = os.path.join(game_root, action.path.replace("/", os.sep))
        previous = record["files"].get(action.path, {})
        entry = {
            "sha256": item.sha256,
            "size": item.size,
            # Preserved across a re-install: what was here before *this mod*
            # first touched the path, which is what uninstall must restore.
            "displaced": previous.get("displaced"),
            "was_absent": previous.get("was_absent", action.what == NEW),
        }
        if action.what == SAME and action.path in record["files"]:
            result.skipped[action.path] = "already installed"
            record["files"][action.path] = {**entry, **previous,
                                            "sha256": item.sha256}
            continue
        if os.path.exists(target) and not previous:
            entry["displaced"] = _backup(game_root, action.path)
            entry["was_absent"] = False
            result.backed_up.append(action.path)
        os.makedirs(os.path.dirname(target) or game_root, exist_ok=True)
        shutil.copy2(item.source, target)
        record["files"][action.path] = entry
        result.written.append(action.path)

    save_ledger(game_root, ledger)
    return result


def uninstall(game_root: str, mod_name: str, *, force: bool = False) -> Result:
    """Undo an install: restore what was displaced, remove what was added.

    A file whose bytes no longer match what was installed is left alone and
    reported -- somebody edited it, and this is not the place to discard that.
    """
    ledger = load_ledger(game_root)
    record = ledger.get("mods", {}).get(mod_name)
    if record is None:
        raise ProjectError(
            f"{mod_name} is not recorded as installed in {game_root}",
            path=game_root)

    result = Result()
    for relative, entry in sorted(record.get("files", {}).items()):
        target = os.path.join(game_root, relative.replace("/", os.sep))
        if os.path.exists(target):
            current = _sha256_file(target)
            if current != entry.get("sha256") and not force:
                result.skipped[relative] = "changed since it was installed"
                continue

        displaced = entry.get("displaced")
        if displaced:
            vault = os.path.join(game_root, BACKUP_DIR, displaced)
            if not os.path.isfile(vault):
                result.skipped[relative] = "the backup of the original is gone"
                continue
            os.makedirs(os.path.dirname(target) or game_root, exist_ok=True)
            shutil.copy2(vault, target)
            result.restored.append(relative)
        elif entry.get("was_absent", True):
            if os.path.exists(target):
                os.remove(target)
            result.removed.append(relative)
        else:
            # Adopted: the file was already here and no copy of the original
            # was ever taken.  Removing it would leave the game short of a
            # file it needs, so it stays, and the caller is told.
            result.skipped[relative] = (
                "no original was ever backed up (adopted); left in place")

    if result.clean or force:
        ledger["mods"].pop(mod_name, None)
    else:
        record["files"] = {k: v for k, v in record["files"].items()
                           if k in result.skipped}
        record["updated"] = _now()
    save_ledger(game_root, ledger)
    _prune_vault(game_root, ledger)
    return result


def swap(game_root: str, from_mod: str, to_mod: str, to_mod_root: str) -> Result:
    """Uninstall one mod's root payload and install another's.

    One operation because the two halves must not be interleaved: installing
    first would back up the *other mod's* files as if they were originals.
    """
    combined = Result()
    if from_mod in load_ledger(game_root).get("mods", {}):
        first = uninstall(game_root, from_mod)
        combined.removed += first.removed
        combined.restored += first.restored
        combined.skipped.update(first.skipped)
    second = install(game_root, to_mod, to_mod_root)
    combined.written += second.written
    combined.backed_up += second.backed_up
    combined.skipped.update(second.skipped)
    return combined


def adopt(game_root: str, mod_name: str, paths: Iterable[str]) -> Result:
    """Record files already sitting in the installation as a mod's.

    For the situation this tool will usually meet: the payload was copied in
    by hand, months ago, and nobody knows what it replaced.  Adopting makes
    the state *describable* -- which mod owns what -- without inventing a
    backup that does not exist.  Those entries carry no original, and
    :func:`uninstall` refuses to guess for them.
    """
    ledger = load_ledger(game_root)
    record = ledger["mods"].setdefault(
        mod_name, {"installed": _now(), "files": {}})
    record["adopted"] = True
    record["updated"] = _now()
    result = Result()
    for relative in paths:
        relative = normalise(relative)
        target = os.path.join(game_root, relative.replace("/", os.sep))
        if not os.path.isfile(target):
            result.skipped[relative] = "not in the game folder"
            continue
        owner = owner_of(ledger, relative)
        if owner and owner != mod_name:
            result.skipped[relative] = f"already recorded as {owner}'s"
            continue
        record["files"][relative] = {
            "sha256": _sha256_file(target),
            "size": os.path.getsize(target),
            "displaced": None,
            "was_absent": False,          # it was here; we did not put it here
            "adopted": True,
        }
        result.written.append(relative)
    save_ledger(game_root, ledger)
    return result


def verify(game_root: str, mod_name: Optional[str] = None) -> Dict[str, str]:
    """``{path: what is wrong}`` for everything the ledger claims.

    Empty means the installation is exactly what the ledger says.
    """
    ledger = load_ledger(game_root)
    out: Dict[str, str] = {}
    for name, record in sorted(ledger.get("mods", {}).items()):
        if mod_name and name != mod_name:
            continue
        for relative, entry in sorted(record.get("files", {}).items()):
            target = os.path.join(game_root, relative.replace("/", os.sep))
            if not os.path.exists(target):
                out[relative] = f"{name}: missing from the game folder"
            elif _sha256_file(target) != entry.get("sha256"):
                out[relative] = f"{name}: changed since it was installed"
    return out


def _prune_vault(game_root: str, ledger: dict) -> int:
    """Drop backups no ledger entry points at any more."""
    vault = os.path.join(game_root, BACKUP_DIR)
    if not os.path.isdir(vault):
        return 0
    wanted = {entry.get("displaced")
              for record in ledger.get("mods", {}).values()
              for entry in record.get("files", {}).values()
              if entry.get("displaced")}
    dropped = 0
    for name in os.listdir(vault):
        if name not in wanted:
            try:
                os.remove(os.path.join(vault, name))
                dropped += 1
            except OSError:
                pass
    if not os.listdir(vault):
        try:
            os.rmdir(vault)
        except OSError:
            pass
    return dropped


__all__ = [
    "VERSION", "PAYLOAD_DIR", "LEDGER", "BACKUP_DIR", "JUNK_SUFFIXES",
    "NEW", "REPLACE_STOCK", "REPLACE_MINE", "CONFLICT", "SAME",
    "PayloadFile", "Action", "Result",
    "normalise", "is_refused", "is_junk", "payload", "payload_dir",
    "import_zip", "load_ledger", "save_ledger", "installed_mods", "owner_of",
    "plan", "install", "uninstall", "swap", "adopt", "verify",
]
