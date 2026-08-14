#!/usr/bin/env python3
"""
Build the pair of mods that settle where ``staticImages/`` has to live.

    python tools/make_staticimages_mod.py --game "<install>" --out "<folder>"

WHAT IT IS FOR
--------------
``specs/README.md`` §5 has a table of which mod folders the engine reads loose
and which it only reads from ``user_data.zip``. ``inifiles/``, ``sound/``,
``scripts/`` and ``strings/`` are read loose; ``3DView/`` and ``images/`` are
not, and each of those was established by a probe mod. ``staticImages/`` is
listed as **untested**, and deliberately so: assuming it behaves like the
folders around it is exactly what kept ``images/`` wrong.

It is not academic. ``Mod.deploy_target`` -- whose docstring says it is "what
stops the app from writing files that silently do nothing" -- currently sends
``staticImages/`` loose, on nothing but that assumption. If the answer is the
zip, the suite has been writing dead files.

WHY IT SWAPS STOCK IMAGES RATHER THAN EDITING ONE
-------------------------------------------------
An altered ``.aim`` would confound the test. Most of these are ``IMSLD32``,
which this project can read and cannot write, and re-encoding to a writable
format is known to produce files the game silently ignores -- a mod whose image
did not appear would then be ambiguous between "the folder is not read" and
"the file was rejected". So each target is overwritten with the **bytes of
another stock image of identical geometry**: same encoding, same tile grid,
same stored size. The only thing that varies is which folder it is delivered
in.

HOW TO READ THE RESULT
----------------------
Two mods are built. Run them one at a time -- the game loads one mod.

Land at any station and look around; the arrival hall and the shipyard are the
loud ones. In the **Loose** mod, an image showing the wrong room means the
engine reads ``staticImages/`` loose. In the **Zipped** mod, the same means it
reads it from ``user_data.zip``. Whichever shows nothing unusual is the answer
that does *not* hold.

If **neither** changes anything, the likeliest cause is that these particular
images are not shown where expected rather than that both routes fail -- say
so, and the pairs below can be pointed at something else.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for sub in ("src", "app", "tools"):
    path = os.path.join(ROOT, sub)
    if path not in sys.path:
        sys.path.insert(0, path)

LOOSE_NAME = "DSO StaticImages Loose"
ZIPPED_NAME = "DSO StaticImages Zipped"

#: ``target <- replacement``. Every pair is matched on encoding, tile grid and
#: stored size, so nothing but the picture differs. The last two are the only
#: ``staticImages`` entries any ``.screen`` references, which makes them the
#: ones guaranteed to be looked up at all; the first two are the loudest.
SWAPS = (
    ("Ankunftshalle_01.aim", "Handel_Terraner.aim"),   # arrival hall -> trade hall
    ("Werft_Terraner.aim", "Bar_human_10.aim"),        # shipyard -> bar
    ("Laptop_01.aim", "Monitor_stripes01.aim"),        # STATION_WERFT's laptop
    ("Cockpit_video_back.aim", "HUD.aim"),             # VIDEO_MONITOR's backdrop
)

FOLDER = "staticImages"


def _decoding_shape(described: str) -> str:
    """The part of a description a reader acts on: stored size onwards."""
    _declared, _, rest = described.partition("(stored ")
    return rest


def _collect(game: str) -> dict:
    """The replacement bytes, keyed by the name they will be written under."""
    from dsotools import vfs as vfsmod
    from dsotools.formats import aim

    stock = vfsmod.from_install(game)
    out = {}
    for target, replacement in SWAPS:
        source = f"{FOLDER}/{replacement}"
        blob = stock.read(source)
        shape = aim.describe(aim.parse(blob))
        original = aim.describe(aim.parse(stock.read(f"{FOLDER}/{target}")))
        out[target] = (blob, replacement, shape, original)
    return out


def _make_mod(parent: str, folder: str, name: str, description: str,
              payload: dict, *, zipped: bool):
    from dso_app.session import Session
    from dsotools.project import Mod

    full = os.path.join(parent, folder)
    if os.path.exists(full):
        shutil.rmtree(full)
    session = Session()
    mod = Mod.create(parent, name, description, folder=folder)
    session.open_mod(mod.root)

    if zipped:
        with zipfile.ZipFile(os.path.join(mod.root, "user_data.zip"), "w",
                             zipfile.ZIP_DEFLATED) as archive:
            for target, (blob, _r, _s, _o) in payload.items():
                archive.writestr(f"{FOLDER}/{target}", blob)
    else:
        target_dir = os.path.join(mod.root, FOLDER)
        os.makedirs(target_dir, exist_ok=True)
        for target, (blob, _r, _s, _o) in payload.items():
            with open(os.path.join(target_dir, target), "wb") as handle:
                handle.write(blob)
    return mod


def main(argv=None) -> int:
    from dsotools import validate
    from dsotools.project import Mod, ZIP_ONLY_ROOTS

    ap = argparse.ArgumentParser(description=__doc__.strip().split("\n")[0])
    ap.add_argument("--game", required=True)
    ap.add_argument("--out", required=True,
                    help="folder to build both mods in; each is replaced")
    args = ap.parse_args(argv)

    try:
        payload = _collect(args.game)
    except Exception as exc:  # noqa: BLE001 - report, do not traceback
        print(f"could not read the stock images: {exc}")
        return 1

    print("swapping, matched on what decides whether the file decodes at all:")
    for target, (_blob, replacement, shape, original) in payload.items():
        print(f"   {target:26s} <- {replacement}")
        print(f"      {shape}")
        # Stored size, encoding and tile grid have to match: those are what a
        # reader acts on, and a mismatch would risk a scrambled image, which
        # this test could not tell apart from "the folder is not read".
        if _decoding_shape(shape) != _decoding_shape(original):
            print(f"      MISMATCH: the target is {original}")
            return 1
        # The *declared* size may differ. It only changes how large the picture
        # is drawn, and a wrong picture at a slightly wrong size answers the
        # question just as well as a wrong picture at the right one.
        if shape.split(" (stored")[0] != original.split(" (stored")[0]:
            print(f"      (drawn at {shape.split(' (stored')[0]} where the "
                  f"original is {original.split(' (stored')[0]} — still "
                  f"unmistakable)")

    os.makedirs(args.out, exist_ok=True)
    loose = _make_mod(args.out, LOOSE_NAME, LOOSE_NAME,
                      "staticImages/ delivered loose in the mod folder.",
                      payload, zipped=False)
    zipped = _make_mod(args.out, ZIPPED_NAME, ZIPPED_NAME,
                       "staticImages/ delivered inside user_data.zip.",
                       payload, zipped=True)

    ok = True
    for mod, where in ((loose, "loose"), (zipped, "user_data.zip")):
        report = validate.validate_mod(Mod(mod.root))
        print(f"\n{mod.root}   ({where})")
        for finding in report:
            print(f"   {finding.code} {finding.severity} {finding.message}")
            if finding.severity == validate.Severity.ERROR:
                ok = False
        if not list(report):
            print("   (no findings)")

    assumed = ("user_data.zip"
               if FOLDER.lower() in {r.lower() for r in ZIP_ONLY_ROOTS}
               else "loose")
    print(f"\nThe suite currently delivers {FOLDER}/ {assumed} "
          f"(ZIP_ONLY_ROOTS = {ZIP_ONLY_ROOTS}) — that is the assumption "
          f"under test.")
    print("\nready — run each mod in turn and land at a station"
          if ok else "\nsomething above needs fixing")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
