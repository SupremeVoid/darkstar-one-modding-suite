#!/usr/bin/env python3
"""
Record what a **stock** installation contains, so anything else is additive.

    python tools/stock_baseline.py --game "<install>" --edition gog
    python tools/stock_baseline.py --game "<install>" --edition steam   # merges

WHY THIS EXISTS
---------------
Mods that reach into the game folder are the one irreversible thing in this
whole toolchain, and until now the suite had no way to tell a stock file from a
modded one. That cost real accuracy: measurements taken on an installation
carrying a mod's own libraries were written down as facts about the
game -- "13 files under ``lua/``" when a stock install has 9, "1,018 functions
the libraries define" when a stock install defines 179.

So the stock state is recorded **once, from a known-clean installation**, and
everything found on a particular machine is then classified against it:
unchanged, modified, or added. That is also what makes an honest "reset to
stock" possible for the loose install roots, and what lets the Project tab say
which files a mod put there before this tool existed.

BOTH EDITIONS, ONE BASELINE
---------------------------
The GOG and Steam builds are the same game. Measured: of the 2,687 loose
content files under the roots below, **none differ**, and Steam merely adds 37
German localisation files. The executables differ only in ``.text`` (the Steam
copy is DRM-wrapped) and in the wrapper's extra ``.bind`` section -- ``.rdata``
and ``.data`` are byte-identical, which is why the API scan works on both and
why the build is identified by a fingerprint of those two sections rather than
of the whole file.

Shared files therefore live in one table; per-edition extras live beside it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for sub in ("src", "tools"):
    path = os.path.join(ROOT, sub)
    if path not in sys.path:
        sys.path.insert(0, path)

DEFAULT_OUT = os.path.join(ROOT, "src", "dsotools", "data", "stock_baseline.json")

#: The loose content roots -- the ones no archive holds, which are therefore
#: the only ones a mod can change by overwriting the installation.
#: See specs/mod_packaging.md 1.
ROOTS = ("lua", "effects", "frontend", "strings", "video", "particlescripts",
         "interface3d", "objectfieldscripts")

#: Files the game itself rewrites; they are never "modified by a mod".
VOLATILE = ("lua/config.lua",)


def digest(path):
    sha = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            sha.update(chunk)
    return sha.hexdigest()


def inventory(game_root, roots=ROOTS):
    """``{relative path: [size, sha256]}`` over the loose content roots."""
    out = {}
    for root in roots:
        base = os.path.join(game_root, root)
        if not os.path.isdir(base):
            continue
        for dirpath, _dirnames, filenames in os.walk(base):
            for filename in sorted(filenames):
                full = os.path.join(dirpath, filename)
                relative = os.path.relpath(full, game_root).replace("\\", "/").lower()
                out[relative] = [os.path.getsize(full), digest(full)]
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.strip().split("\n")[0])
    ap.add_argument("--game", required=True, help="a KNOWN-CLEAN installation")
    ap.add_argument("--edition", required=True, choices=("gog", "steam"))
    ap.add_argument("--out", default=DEFAULT_OUT)
    args = ap.parse_args(argv)

    exe = os.path.join(args.game, "DarkStarOne.exe")
    if not os.path.isfile(exe):
        print(f"no DarkStarOne.exe in {args.game}")
        return 2

    import exe_api_scan

    image = exe_api_scan.Image(exe)
    fingerprint = image.data_fingerprint()

    found = inventory(args.game)
    archives = {}
    for name in sorted(os.listdir(args.game)):
        if name.lower().endswith(".cpr"):
            full = os.path.join(args.game, name)
            archives[name.lower()] = [os.path.getsize(full), digest(full)]

    if os.path.isfile(args.out):
        with open(args.out, encoding="utf-8") as handle:
            base = json.load(handle)
    else:
        base = {"schema": 1, "build": {}, "roots": list(ROOTS),
                "volatile": list(VOLATILE), "shared": {}, "editions": {}}

    known = base.get("build", {}).get("data_fingerprint")
    if known and known != fingerprint:
        print(f"refusing to merge: this executable's data fingerprint is "
              f"{fingerprint[:12]}, the baseline records {known[:12]}. "
              f"A different build needs its own baseline.")
        return 1
    base.setdefault("build", {})["data_fingerprint"] = fingerprint
    base["build"].setdefault("editions", {})[args.edition] = {
        "exe_size": os.path.getsize(exe),
        "exe_sha256": digest(exe),
        "text_wrapped": image.is_wrapped(),
        "archives": archives,
    }

    if not base["shared"]:
        base["shared"] = found
        extra = {}
    else:
        shared = base["shared"]
        conflicting = sorted(k for k in set(shared) & set(found)
                             if shared[k][1] != found[k][1])
        if conflicting:
            print(f"{len(conflicting)} file(s) differ between editions, which "
                  f"contradicts the measurement that none do: "
                  f"{conflicting[:3]}")
            return 1
        extra = {k: v for k, v in found.items() if k not in shared}
    base["editions"][args.edition] = extra

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(base, handle, ensure_ascii=False, indent=0, sort_keys=True)

    print(f"{args.out}")
    print(f"   build {fingerprint[:12]}, editions "
          f"{', '.join(sorted(base['build']['editions']))}")
    print(f"   {len(base['shared'])} shared files, "
          f"{len(extra)} extra in {args.edition}, {len(archives)} archives")
    print(f"   {os.path.getsize(args.out) / 1024:.0f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
