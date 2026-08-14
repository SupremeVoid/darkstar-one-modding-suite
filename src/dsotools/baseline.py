"""
What a stock installation contains -- so everything else can be called additive.

WHY THIS EXISTS
---------------
The loose install roots are the one place a mod can make an irreversible
change, and until this existed the suite could not tell a stock file from a
modded one.  That was not a theoretical gap: measurements taken on an
installation carrying a mod's own libraries were recorded as facts
about the game -- "13 files under ``lua/``" where a stock install has 9,
"1,018 functions the libraries define" where a stock install defines 179.

So the stock state is recorded once, from known-clean installations
(``tools/stock_baseline.py``), and any installation is then **classified**
against it: unchanged, modified, added, missing.

BOTH EDITIONS
-------------
One baseline covers GOG and Steam.  Of the 2,687 loose content files, none
differ between them; Steam adds 37 German localisation files.  The executables
differ only in ``.text`` -- the Steam copy is DRM-wrapped -- so the build is
identified by a fingerprint of ``.rdata`` and ``.data``, which are
byte-identical.  Everything this module reports therefore means the same thing
on either edition.

SPEED, AND WHY SIZE ALONE WILL NOT DO
-------------------------------------
The covered roots hold about 3.3 GB, nearly all of it video.  Comparing sizes
alone is fast and **wrong**: of the twelve files the mod measured puts in
the game folder, two are byte-different at exactly the stock size --
``missions.bin`` (39 single-byte identifier edits) and one subtitle XML.

So the default hashes every file up to :data:`HASH_LIMIT` and compares only the
size above it: 2,653 of the 2,687 known files, 197 MB, about a second.  What is
left unhashed is ``.bik`` video, which no mod has ever been seen to patch in
place.  ``quick=False`` hashes everything and takes as long as reading 3.3 GB.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Dict, List, Optional

from .errors import DsoError

VERSION = "1.0"

BUNDLED = "stock_baseline.json"

#: Files at or below this size are always hashed; larger ones are compared by
#: size unless ``quick=False``.  Chosen so that everything a mod realistically
#: edits is hashed and only video is skipped.
HASH_LIMIT = 4 << 20

#: Classification results.
UNCHANGED = "unchanged"
MODIFIED = "modified"
ADDED = "added"
MISSING = "missing"


def bundled_path() -> Optional[str]:
    from importlib.resources import files

    try:
        candidate = files("dsotools") / "data" / BUNDLED
        return str(candidate) if candidate.is_file() else None
    except (ModuleNotFoundError, FileNotFoundError, TypeError):
        return None


def bundled() -> Optional[dict]:
    """The shipped baseline, or ``None`` when this build has none."""
    path = bundled_path()
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        raise DsoError(f"cannot read the stock baseline: {exc}", path=path) from exc
    if data.get("schema") != 1:
        raise DsoError(f"the stock baseline is schema {data.get('schema')}, "
                       f"this build reads 1", path=path)
    return data


def _digest(path: str) -> str:
    sha = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            sha.update(chunk)
    return sha.hexdigest()


def expected(baseline: Optional[dict] = None,
             edition: Optional[str] = None) -> Dict[str, list]:
    """``{path: [size, sha256]}`` -- what a stock install of ``edition`` holds.

    Without an edition, every known file is expected, which is right for
    "is this file stock anywhere?" and wrong for "is this install complete?".
    """
    baseline = bundled() if baseline is None else baseline
    if not baseline:
        return {}
    out = dict(baseline.get("shared", {}))
    editions = baseline.get("editions", {})
    for name, extra in editions.items():
        if edition is None or name == edition:
            out.update(extra)
    return out


def detect_edition(game_root: str, baseline: Optional[dict] = None) -> Optional[str]:
    """Which edition this installation is, by the executable's size and hash."""
    baseline = bundled() if baseline is None else baseline
    exe = os.path.join(game_root, "DarkStarOne.exe")
    if not baseline or not os.path.isfile(exe):
        return None
    size = os.path.getsize(exe)
    candidates = {name: info for name, info in
                  baseline.get("build", {}).get("editions", {}).items()
                  if info.get("exe_size") == size}
    if len(candidates) == 1:
        return next(iter(candidates))
    digest = _digest(exe)
    for name, info in baseline.get("build", {}).get("editions", {}).items():
        if info.get("exe_sha256") == digest:
            return name
    return None


def classify(game_root: str, *, baseline: Optional[dict] = None,
             edition: Optional[str] = None, quick: bool = True) -> Dict[str, List[str]]:
    """Compare an installation with the stock baseline.

    ``{"unchanged": [...], "modified": [...], "added": [...], "missing": [...]}``

    ``added`` is the interesting one for modding: those are files a mod (or a
    person) put into the game folder, and they are exactly what
    :mod:`dsotools.rootfiles` should be managing.
    """
    baseline = bundled() if baseline is None else baseline
    if not baseline:
        raise DsoError("this build ships no stock baseline")
    edition = edition or detect_edition(game_root, baseline)
    wanted = expected(baseline, edition)
    volatile = {p.lower() for p in baseline.get("volatile", ())}

    out = {UNCHANGED: [], MODIFIED: [], ADDED: [], MISSING: []}
    seen = set()
    for root in baseline.get("roots", ()):
        base = os.path.join(game_root, root)
        if not os.path.isdir(base):
            continue
        for dirpath, _dirnames, filenames in os.walk(base):
            for filename in filenames:
                full = os.path.join(dirpath, filename)
                relative = os.path.relpath(full, game_root).replace("\\", "/").lower()
                seen.add(relative)
                if relative in volatile:
                    continue
                record = wanted.get(relative)
                if record is None:
                    out[ADDED].append(relative)
                    continue
                size, sha = record
                actual = os.path.getsize(full)
                if actual != size:
                    out[MODIFIED].append(relative)
                    continue
                # Same size proves nothing: two of the twelve files the
                # The measured payload replaces are exactly stock-sized.
                if quick and actual > HASH_LIMIT:
                    out[UNCHANGED].append(relative)
                elif _digest(full) == sha:
                    out[UNCHANGED].append(relative)
                else:
                    out[MODIFIED].append(relative)

    for relative in wanted:
        if relative not in seen and relative not in volatile:
            out[MISSING].append(relative)
    for key in out:
        out[key].sort()
    return out


def is_stock(game_root: str, relative: str, *,
             baseline: Optional[dict] = None, quick: bool = False) -> Optional[bool]:
    """Is one file the stock one?  ``None`` when the baseline has never heard of it."""
    baseline = bundled() if baseline is None else baseline
    record = expected(baseline).get(relative.replace("\\", "/").lower())
    if record is None:
        return None
    full = os.path.join(game_root, relative.replace("/", os.sep))
    if not os.path.isfile(full):
        return False
    size, sha = record
    if os.path.getsize(full) != size:
        return False
    if quick and size > HASH_LIMIT:
        return True
    return _digest(full) == sha


def summary(game_root: str, **kwargs) -> str:
    counts = classify(game_root, **kwargs)
    return ", ".join(f"{len(counts[k])} {k}"
                     for k in (UNCHANGED, MODIFIED, ADDED, MISSING))


__all__ = [
    "VERSION", "BUNDLED", "UNCHANGED", "MODIFIED", "ADDED", "MISSING",
    "HASH_LIMIT",
    "bundled", "bundled_path", "expected", "detect_edition", "classify",
    "is_stock", "summary",
]
