#!/usr/bin/env python3
"""
Build and query the asset index.

    # build once, reuse
    python tools/asset_index.py --data "<...>/extracted game data" --db game.db --build

    # the question the app is built around
    python tools/asset_index.py --db game.db --used-by 3DView/textures/playership_body_00_col.dds

    python tools/asset_index.py --db game.db --refs 3DView/PlayerShip.xml
    python tools/asset_index.py --db game.db --search playership --format dds
    python tools/asset_index.py --db game.db --unresolved
    python tools/asset_index.py --db game.db --orphans 3do
    python tools/asset_index.py --db game.db --stats

A mod can be layered on top with ``--mod``, so the index reflects what the game
would actually load rather than stock alone.

This is the CLI face of ``dsotools.index``; the Models and Textures tabs will
run the same queries.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from dsotools import index as idxmod  # noqa: E402
from dsotools import locate  # noqa: E402
from dsotools import vfs as vfsmod  # noqa: E402
from dsotools.project import Mod, iter_mod_layers  # noqa: E402


def _vfs(args):
    if args.game:
        game = vfsmod.from_install(args.game)
    elif args.data:
        game = vfsmod.from_extracted(args.data)
    else:
        found = locate.find_game()
        if not found:
            raise SystemExit(
                "no game found -- pass --game <install folder> or --data <extracted>"
            )
        print(f"  using detected installation: {found}", file=sys.stderr)
        game = vfsmod.from_install(found)
    if args.mod:
        for layer in iter_mod_layers(Mod(args.mod)):
            game.add(layer)
    return game


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.strip().split("\n")[0])
    ap.add_argument("--game", help="Darkstar One installation folder (reads .cpr directly)")
    ap.add_argument("--data", help="folder of already-extracted archives")
    ap.add_argument("--mod", help="also layer this mod on top")
    ap.add_argument("--db", default=":memory:", help="index file (default: in memory)")
    ap.add_argument("--build", action="store_true", help="(re)build the index")
    ap.add_argument("--shallow", action="store_true", help="skip the reference graph")

    ap.add_argument("--used-by", metavar="PATH", help="what references this asset")
    ap.add_argument("--refs", metavar="PATH", help="what this asset references")
    ap.add_argument("--search", metavar="TERM")
    ap.add_argument("--format", metavar="FMT", help="restrict --search to a format")
    ap.add_argument("--unresolved", action="store_true", help="every broken reference")
    ap.add_argument("--orphans", metavar="FMT", help="assets of FMT nothing references")
    ap.add_argument("--stats", action="store_true")
    args = ap.parse_args()

    if args.build or args.db == ":memory:":
        game = _vfs(args)
        t0 = time.time()

        def progress(done, total, path):
            pct = 100 * done // max(1, total)
            print(f"\r  indexing {pct:3d}%  ({done}/{total})", end="", file=sys.stderr)

        if args.db != ":memory:" and os.path.exists(args.db):
            os.remove(args.db)
        idx = idxmod.AssetIndex.create(args.db).build(
            game, progress=progress, deep=not args.shallow
        )
        print(f"\r  indexed in {time.time() - t0:.1f}s{' ' * 24}", file=sys.stderr)
    else:
        idx = idxmod.AssetIndex.open(args.db)

    did = False

    if args.used_by:
        did = True
        rows = idx.used_by(args.used_by)
        print(f"{len(rows)} reference(s) to {args.used_by}:")
        for r in rows:
            slot = f" slot {r['slot']}" if r["slot"] is not None else ""
            node = f"  [{r['node']}]" if r["node"] else ""
            print(f"  {r['src']:<52} {r['kind']}{slot}{node}")

    if args.refs:
        did = True
        rows = idx.references_from(args.refs)
        print(f"{args.refs} references {len(rows)} asset(s):")
        for r in rows:
            print(f"  {r['kind']:<8} {r['raw']:<46} -> {r['dst'] or 'UNRESOLVED'}")

    if args.search:
        did = True
        for r in idx.search(args.search, fmt=args.format):
            print(f"  {r['display']:<58} {r['format'] or '?':<8} {r['origin']}")

    if args.unresolved:
        did = True
        rows = idx.unresolved()
        print(f"{len(rows)} unresolved reference(s):")
        for r in rows[:200]:
            print(f"  {r['src']:<48} {r['kind']:<8} {r['raw']}")
        if len(rows) > 200:
            print(f"  ... and {len(rows) - 200} more")

    if args.orphans:
        did = True
        rows = idx.orphans(args.orphans)
        print(f"{len(rows)} unreferenced {args.orphans} asset(s):")
        for r in rows[:100]:
            print(f"  {r}")
        if len(rows) > 100:
            print(f"  ... and {len(rows) - 100} more")

    if args.stats or not did:
        s = idx.stats()
        print(json.dumps({k: v for k, v in s.items() if k != "formats"}, indent=2))
        print("formats:")
        for fmt, n in s["formats"].items():
            print(f"  {fmt:<10} {n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
