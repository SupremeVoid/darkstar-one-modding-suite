#!/usr/bin/env python3
"""
Build a mod that replaces the main menu music, to settle how sound databases
combine.

    python tools/make_music_mod.py --game "<install>" --out "<folder>"

WHAT IT IS FOR
--------------
``specs/sound.md`` §6 lists one open question: does a mod's ``user_sounds.xml``
*add to* the game's ``KlangErzeugerDefault.xml``, or replace it? The suite
assumes additive because that is how mods in the wild are written, but nobody
has measured it.

Declaring the stock name ``Mainmenu/MUSIC_Mainmenu`` against a different file
answers it, and the main menu is the ideal place to ask: the music starts
before anything else, needs no save game, and is recognisable in two seconds.

The replacement is **one of the game's own tracks** -- a combat loop -- rather
than an imported file. That keeps the format, bitrate and sample rate identical
to what already works in that slot, so the database entry is the only thing
that differs. A test that changes two things at once answers neither.

HOW TO READ THE RESULT
----------------------
Select the mod, start the game, and listen at the main menu.

* **Combat music** -- the mod's entry won. Databases are additive and a mod can
  override a stock sound by declaring its group and name. This is what the
  suite assumes.
* **The usual menu theme** -- the stock entry won. A mod can then only *add*
  sounds, never replace one, and changing a stock sound would mean overwriting
  the file in the installation through the game-folder payload instead.
* **Silence, or the menu works but other sounds have gone** -- the mod's
  database replaced the game's outright. That would make ``user_sounds.xml`` a
  whole-database override, and every mod that ships a small one is quietly
  removing 442 sounds.

The third case is the one worth listening past the menu for: click around the
menu and see whether the interface still makes its usual noises.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for sub in ("src", "app", "tools"):
    path = os.path.join(ROOT, sub)
    if path not in sys.path:
        sys.path.insert(0, path)

MOD_NAME = "DSO Menu Music Swap"

#: The stock sound to shadow: group path and name, which together are its
#: address. The name alone would be ambiguous in general -- 38 stock names are
#: used by more than one group -- though this one happens to be unique.
TARGET_GROUP = "Mainmenu"
TARGET_NAME = "MUSIC_Mainmenu"

#: What to play instead. A combat loop: same encoder, same 44.1 kHz stereo MP3,
#: nothing like a menu theme.
DEFAULT_TRACK = "MUSIC/FightLoop/39_combat_loop_var_piecemaker"


def _mirror_group_attributes(session, stock, group_path: str) -> dict:
    """Give the mod's group the same attributes the stock one has.

    Returns what was copied, so the caller can say so rather than assume it.
    """
    from dsotools.formats import sounddb

    theirs = stock.group(group_path)
    if theirs is None:
        return {}
    extra = {k: v for k, v in theirs.attrs.items() if k != "Name"}
    if not extra:
        return {}

    full = os.path.join(session.mod.root, session.SOUND_DB)
    with open(full, "rb") as handle:
        mine = sounddb.parse(handle.read(), path=full)
    mine_group = mine.group(group_path)
    if mine_group is None or mine_group._el is None:
        return {}
    for key, value in extra.items():
        mine_group._el.set(key, value)
        mine_group.attrs[key] = value
    with open(full, "wb") as handle:
        handle.write(mine.to_bytes())
    return extra


def main(argv=None) -> int:
    from dso_app.session import Session
    from dsotools import validate
    from dsotools.formats import sounddb
    from dsotools.project import Mod

    ap = argparse.ArgumentParser(description=__doc__.strip().split("\n")[0])
    ap.add_argument("--game", required=True)
    ap.add_argument("--out", required=True, help="folder to build in; it is replaced")
    ap.add_argument("--track", default=DEFAULT_TRACK,
                    help="stock sound to use instead, as group/name")
    args = ap.parse_args(argv)

    stock_db_path = os.path.join(args.game, Session.STOCK_SOUND_DB)
    if not os.path.isfile(stock_db_path):
        print(f"no sound database at {stock_db_path}")
        return 2
    with open(stock_db_path, "rb") as handle:
        stock = sounddb.parse(handle.read(), path=stock_db_path)

    target = stock.resolve(f"{TARGET_GROUP}/{TARGET_NAME}")
    source = stock.resolve(args.track)
    if target is None:
        print(f"this installation has no {TARGET_GROUP}/{TARGET_NAME}")
        return 1
    if source is None:
        print(f"no stock sound called {args.track!r}")
        return 1

    track_file = os.path.join(args.game, source.path().replace("/", os.sep))
    if not os.path.isfile(track_file):
        print(f"the replacement track is not on disk: {track_file}")
        return 1

    parent = os.path.dirname(os.path.abspath(args.out)) or "."
    folder = os.path.basename(os.path.abspath(args.out))
    if os.path.exists(args.out):
        shutil.rmtree(args.out)
    os.makedirs(parent, exist_ok=True)

    session = Session()
    session.open_game(args.game)
    mod = Mod.create(
        parent, MOD_NAME,
        "Replaces the main menu music, to find out whether a mod's sound "
        "database adds to the game's or replaces it.",
        stock=session.stock, folder=folder)
    session.open_mod(mod.root)

    added = session.add_sound(track_file, name=TARGET_NAME, kind=target.kind,
                              group=TARGET_GROUP)

    # Mirror the stock group's own attributes. `Mainmenu` carries Wet="0.0",
    # and a mod group without it would differ from stock in two ways rather
    # than one -- if the databases merge by group the difference is moot, and
    # if they do not it changes the reverb send. Either way it is a variable
    # this test has no business introducing.
    carried = _mirror_group_attributes(session, stock, TARGET_GROUP)

    print(f"{mod.root}")
    print(f"   shadowing  {TARGET_GROUP}/{TARGET_NAME}  "
          f"({target.kind}, {target.seconds:.0f}s)")
    print(f"   with       {args.track}  ({source.seconds:.0f}s)")
    print(f"   declared   {added['resource']}")
    print(f"   copied     {os.path.getsize(added['path']):,} bytes")
    if carried:
        print("   group      carried from stock: "
              + ", ".join(f"{k}={v}" for k, v in sorted(carried.items())))

    # The declaration has to be exactly the shape the stock one is, or the test
    # measures the wrong thing.
    with open(os.path.join(mod.root, Session.SOUND_DB), "rb") as handle:
        written = sounddb.parse(handle.read())
    mine = written.resolve(f"{TARGET_GROUP}/{TARGET_NAME}")
    ok = True
    if mine is None:
        print("\n   the entry did not come back out of the file")
        ok = False
    else:
        if mine.kind != target.kind:
            print(f"\n   kind is {mine.kind}, stock uses {target.kind}")
            ok = False
        if mine.frequency != source.frequency or mine.channels != source.channels:
            print(f"\n   declared {mine.frequency} Hz / {mine.channels} ch, "
                  f"file is {source.frequency} / {source.channels}")
            ok = False

    report = validate.validate_mod(Mod(mod.root), session.stock)
    print()
    for finding in report:
        print(f"   {finding.code} {finding.severity} {finding.message}")
        if finding.severity == validate.Severity.ERROR:
            ok = False

    print("\nready — start the game and listen at the main menu"
          if ok else "\nsomething above needs fixing")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
