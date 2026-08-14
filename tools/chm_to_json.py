#!/usr/bin/env python3
"""
Turn the modding tools' Lua reference into the API database the app ships.

    python tools/chm_to_json.py
    python tools/chm_to_json.py --chm "C:\\...\\Documentation\\ds1doc_eng.chm"
    python tools/chm_to_json.py --keep-pages out/chm    # to look at the HTML

Run once, on Windows, whenever the reference changes -- which is never, the
file is from 2006. The result is committed, so nobody else needs the modding
tools installed to get completion and signature help.

WHY A TOOL AND NOT A BUILD STEP
-------------------------------
Decompiling a `.chm` needs Windows' own `hh.exe`, and `ds1doc_eng.chm` ships
with Ascaron's *Darkstar One Modding Tools*, not with the game. Neither is
present in CI or on a Linux machine, so this cannot run there -- and a build
step that silently produces an empty database would be worse than one that
does not exist. The generated JSON is the artefact; this is how it was made.

The original is never touched: the CHM is copied into a temporary folder and
decompiled there.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.join(ROOT, "src") not in sys.path:
    sys.path.insert(0, os.path.join(ROOT, "src"))

DEFAULT_OUT = os.path.join(ROOT, "src", "dsotools", "data", "lua_api.json")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.strip().split("\n")[0])
    ap.add_argument("--chm", help="the reference; found automatically if omitted")
    ap.add_argument("--tools", help="an installation of the modding tools to search")
    ap.add_argument("--out", default=DEFAULT_OUT, help=f"default: {DEFAULT_OUT}")
    ap.add_argument("--keep-pages", metavar="DIR",
                    help="also keep the decompiled HTML here, to read it")
    args = ap.parse_args(argv)

    from dsotools import scriptdoc
    from dsotools.errors import DsoError

    chm = args.chm or scriptdoc.find_chm(*( [args.tools] if args.tools else [] ))
    if not chm:
        print("no ds1doc_eng.chm found. Pass --chm, or --tools with the folder "
              "the Darkstar One Modding Tools are installed in.")
        return 2
    print(f"reference: {chm}")

    work = args.keep_pages or tempfile.mkdtemp(prefix="ds1doc_")
    try:
        # A copy, decompiled: hh.exe writes into the folder it is given, and
        # the user's installation is not ours to write into.
        os.makedirs(work, exist_ok=True)
        local = os.path.join(work, os.path.basename(chm))
        shutil.copy2(chm, local)
        pages = os.path.join(work, "pages")
        print(f"decompiling into {pages} ...")
        try:
            scriptdoc.extract(local, pages)
        except DsoError as exc:
            print(f"failed: {exc}")
            return 1

        database = scriptdoc.build(pages, source=os.path.basename(chm))
        written = scriptdoc.save(database, args.out)
    finally:
        if not args.keep_pages:
            shutil.rmtree(work, ignore_errors=True)

    symbols = database["symbols"]
    constants = sum(len(v) for v in database["constants"].values())
    print(f"\n{written}")
    print(f"   {len(symbols)} symbols in {len(database['namespaces'])} namespaces, "
          f"{constants} constants in {len(database['constants'])} groups")
    for kind in ("command", "event", "camera"):
        count = sum(1 for s in symbols if s["kind"] == kind)
        print(f"   {kind:8s} {count:4d}")
    documented = sum(1 for s in symbols if s["summary"])
    examples = sum(1 for s in symbols if s["example"])
    print(f"   {documented} have a description, {examples} carry an example")
    if database.get("duplicate_pages"):
        print(f"   documented twice, kept once: "
              f"{', '.join(database['duplicate_pages'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
