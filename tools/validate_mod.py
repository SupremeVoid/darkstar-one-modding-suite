#!/usr/bin/env python3
"""
Run the diagnostics engine over a mod and print the results.

    python tools/validate_mod.py --mod "<...>/Customization/<Mod Name>" \
                                 --data "<...>/extracted game data"

    python tools/validate_mod.py --list          # every mod on this machine
    python tools/validate_mod.py --mod <path> --json > report.json

``--data`` is optional.  Without it the structural rules still run, so a mod can
be checked before the game has been located; the rules that need a baseline
(identical-to-stock, unresolved references, the submesh invariant) are skipped
and said to be skipped rather than silently passing.

Exit code is 1 if any ERROR was found, which makes this usable as a pre-release
gate in CI.  This is the CLI face of ``dsotools.validate``; the GUI will render
the same :class:`Report` as a problem list.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from dsotools import validate, vfs  # noqa: E402
from dsotools.errors import DsoError  # noqa: E402
from dsotools.project import Mod  # noqa: E402
from dsotools import locate  # noqa: E402


def _open_stock(args):
    """Game installation, extracted tree, or autodetected -- in that order.

    Autodetection last so an explicit flag always wins, but present at all so
    the common case needs no flag: the .cpr archives are plain ZIPs, and the
    installed game is directly readable.
    """
    if args.game:
        return vfs.from_install(args.game)
    if args.data:
        return vfs.from_extracted(args.data)
    found = locate.find_game()
    if found:
        print(f"  using detected installation: {found}", file=sys.stderr)
        return vfs.from_install(found)
    return None

COLOURS = {
    "error": "\033[31m",
    "warning": "\033[33m",
    "info": "\033[36m",
    "hint": "\033[90m",
}
RESET = "\033[0m"


def _paint(text, severity, enabled):
    if not enabled:
        return text
    return f"{COLOURS.get(severity, '')}{text}{RESET}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.strip().split("\n")[0])
    ap.add_argument("--mod", help="path to a mod folder")
    ap.add_argument("--game", help="Darkstar One installation folder (reads .cpr directly)")
    ap.add_argument("--data", help="folder of already-extracted archives")
    ap.add_argument("--list", action="store_true", help="list mods found on this machine")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of text")
    ap.add_argument("--no-colour", action="store_true")
    ap.add_argument("--quiet", action="store_true", help="only the summary")
    args = ap.parse_args()

    if args.list:
        custom = Mod.default_customization_dir()
        if not custom:
            print("No Customization folder found under your Documents directory.")
            return 1
        print(custom)
        for m in Mod.discover(custom):
            try:
                listable = "" if m.is_listable() else "   [NOT LISTED BY THE GAME - no inifiles/items.ini]"
                print(f"  {m.name:<34} {m.display_name or '?'}{listable}")
            except DsoError as exc:
                print(f"  {m.name:<34} <{exc}>")
        return 0

    if not args.mod:
        ap.error("--mod is required unless --list is given")

    mod = Mod(args.mod)
    stock = _open_stock(args)

    report = validate.validate_mod(mod, stock)

    if args.json:
        json.dump(
            {
                "mod": mod.name,
                "display_name": mod.display_name,
                "stock_available": stock is not None,
                "counts": report.counts(),
                "skipped": report.skipped,
                "ok": report.ok,
                "diagnostics": [d.as_dict() for d in report],
            },
            sys.stdout,
            indent=2,
        )
        print()
        return 0 if report.ok else 1

    colour = not args.no_colour and sys.stdout.isatty()
    print(f"{mod.display_name or mod.name}  ({mod.root})")
    if stock is None:
        print("  no --data given: baseline rules skipped (PRJ002, SCN001, SCN002)")
    print()

    if not args.quiet:
        by_code = report.by_code()
        for code in sorted(by_code, key=lambda c: (validate.Severity.rank(by_code[c][0].severity), c)):
            group = by_code[code]
            sev = group[0].severity
            head = f"{sev.upper():<8} {code}  ({len(group)})"
            print(_paint(head, sev, colour))
            for d in group[:12]:
                where = f"{d.path}" if d.path else ""
                loc = f" ({d.location})" if d.location else ""
                print(f"    {where}{loc}")
                print(f"      {d.message}")
                if d.fix:
                    print(f"      fix: {d.fix}")
            if len(group) > 12:
                print(f"    ... and {len(group) - 12} more")
            print()

    for rule, why in sorted(report.skipped.items()):
        print(f"NOT CHECKED  {rule}")
        print(f"    {why}")
    if report.skipped:
        print()

    counts = report.counts()
    parts = [f"{counts.get(k, 0)} {k}" for k in ("error", "warning", "info", "hint") if counts.get(k)]
    print("  " + (", ".join(parts) if parts else "no findings"))
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
