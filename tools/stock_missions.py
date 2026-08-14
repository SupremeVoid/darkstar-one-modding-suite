#!/usr/bin/env python3
"""
Record the stock mission table, so the suite can offer to override one.

    python tools/stock_missions.py --game "<install>"

WHY THIS EXISTS
---------------
``NScript.Register`` keys the mission table by ``Name``, and a mod's
``scripts\\`` is read after ``lua/mission/missions.bin`` -- so registering an
existing name replaces that mission outright. Offering that in the UI needs a
list of what the stock names *are*, and they exist only as Lua 4 bytecode.

``ScriptCompiler.exe`` disassembles the bundle with its string constants
intact, which is enough to read each chunk's registration record: name, type,
group and the ordered list of states. That needs Windows and the modding tools,
so it is done once here and the result ships as
``src/dsotools/data/stock_missions.json``.

WHAT THE RESULT SHOULD LOOK LIKE
--------------------------------
On the stock bundle: **154 chunks, 150 missions and exactly 4 that register
nothing** -- ``BattleLib``, ``BattleLibEx``, ``CameraLib`` and ``MissionLib``.
Every chunk is therefore accounted for, which is what makes the extraction
falsifiable rather than merely plausible. This tool refuses to write a table
that leaves any chunk unexplained unless ``--force`` is given.

Two facts fell out of the first run and are worth keeping in mind:

* **The file name is not the mission name.** ``BAR_006_02.lua`` registers
  ``BAR_006``, and ``STORY_011_01.lua`` registers ``STORY_011``.
* **Two mission types are undocumented.** The reference lists eight; the bundle
  also uses ``MTYPE_STORY_CHAPTER`` (8 missions) and ``MTYPE_MENU`` (1).
"""

from __future__ import annotations

import argparse
import collections
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for sub in ("src", "tools"):
    path = os.path.join(ROOT, sub)
    if path not in sys.path:
        sys.path.insert(0, path)

DEFAULT_OUT = os.path.join(ROOT, "src", "dsotools", "data", "stock_missions.json")

#: Chunks that legitimately register nothing.  Anything else unexplained means
#: the extraction missed a record, which is a defect, not a finding.
LIBRARIES = ("battlelib", "battlelibex", "cameralib", "missionlib")


def main(argv=None) -> int:
    from dsotools import luac, missions

    ap = argparse.ArgumentParser(description=__doc__.strip().split("\n")[0])
    ap.add_argument("--game", required=True, help="a stock game installation")
    ap.add_argument("--bundle", help="override the bundle path")
    ap.add_argument("--compiler", help="path to ScriptCompiler.exe")
    ap.add_argument("--edition", default=None, help="gog | steam, recorded only")
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--force", action="store_true",
                    help="write even if some chunk registers nothing unexpectedly")
    args = ap.parse_args(argv)

    bundle = args.bundle or os.path.join(args.game, *missions.STOCK_BUNDLE.split("/"))
    if not os.path.isfile(bundle):
        print(f"no bundle at {bundle}")
        return 2

    with open(bundle, "rb") as handle:
        chunks = luac.chunk_names(handle.read())
    found = missions.index(bundle, compiler=args.compiler)

    registered = {m.source for m in found}
    silent = [c for c in chunks if c not in registered]
    unexplained = [c for c in silent
                   if os.path.basename(c)[:-4].lower() not in LIBRARIES]

    print(f"{bundle}")
    print(f"   {len(chunks)} chunks, {len(found)} missions, "
          f"{len(silent)} register nothing")
    counts = collections.Counter(m.type for m in found)
    for kind, n in counts.most_common():
        mark = "" if kind in missions.MISSION_TYPES else "   (not in the reference)"
        print(f"      {kind:22s} {n:4d}{mark}")
    renamed = [m for m in found
               if m.source and m.name.lower() != os.path.basename(m.source)[:-4].lower()]
    for m in renamed:
        print(f"   {os.path.basename(m.source)} registers {m.name!r}")

    if unexplained and not args.force:
        print(f"\n{len(unexplained)} chunk(s) register nothing and are not known "
              f"libraries: {unexplained[:5]}")
        print("Refusing to write a table that cannot account for every chunk. "
              "Pass --force if this is expected.")
        return 1

    missions.save(found, args.out, edition=args.edition,
                  bundle=missions.STOCK_BUNDLE)
    print(f"\n{args.out}")
    print(f"   {len(found)} missions, "
          f"{sum(len(m.states) for m in found)} states, "
          f"{os.path.getsize(args.out) / 1024:.0f} KB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
