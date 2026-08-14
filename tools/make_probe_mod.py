#!/usr/bin/env python3
"""
Build the in-game probe for mission overrides and custom text.

    python tools/make_probe_mod.py --game "<install>" --out "<folder>"

WHAT IT IS FOR
--------------
Two things the suite now generates have been verified everywhere except in a
running game:

1. **A generated mission override.** The mechanism is measured -- registering a
   stock ``Name`` overwrites ``SCRIPT_TABLE[Name]`` [code], and a hand-written
   ``ALWAYS_000`` ran [game] -- but the *generated template* has only been
   proved to parse.
2. **A mod's own ``.res`` string table.** The format is decoded and round-trips
   byte for byte, and the engine composes ``<mod>\\strings\\user_strings.res``
   [code], but no text from a suite-written table has ever appeared on screen.

Both fail *silently* when they fail, so the probe is instrumented with credits
rather than with log lines: ``NDebug.Message`` is a registered stub that does
nothing, which cost two whole experiment cycles here before it was understood.

HOW TO READ THE RESULT
----------------------
Note the credit balance before starting, then hypergate to a new system --
``MTYPE_ALWAYS`` missions are created on *arriving somewhere new*, not at game
start [game]. Subtract the starting balance; each decimal digit is one callback:

        1  override  Init          1 000  new mission  Init
       10  override  Create       10 000  new mission  Create
      100  override  Destroy     100 000  new mission  Destroy

So ``+11 011`` means both missions initialised and were created -- the override
works. ``+11 000`` means only the new mission ran, so registering a stock name
did *not* take effect. ``+11`` means the opposite. ``+0`` means neither ran, and
the mod is not being loaded at all.

Two info messages should also appear on screen. They come from the mod's own
string table; if the credits move but no text shows, the table is the part that
did not work.

The mod is built through the suite's own API -- ``create_mission_override``,
``create_mission`` and ``save_strings`` -- so it tests what ships, not a
hand-written copy of it.
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

MOD_NAME = "DSO Override Probe"

#: state -> (credits, StringId to show or None).  The digit code above.
OVERRIDE_STEPS = {
    "Init": (1, None),
    "Create": (10, "ID_PROBE_OVERRIDE"),
    "Destroy": (100, None),
}
NEW_STEPS = {
    "Init": (1000, None),
    "Create": (10000, "ID_PROBE_NEW"),
    "Destroy": (100000, None),
}

STRINGS = [
    ("ID_PROBE_OVERRIDE", "Probe: the stock ALWAYS_000 was replaced by this mod."),
    ("ID_PROBE_NEW", "Probe: a mod-defined mission was created."),
]


def _insert(text: str, state: str, body: str) -> str:
    """Put ``body`` inside the generated stub for ``state``.

    Deliberately edits the generated file rather than writing Lua from scratch:
    the point of the probe is to test what the generator produces.
    """
    marker = f'nil, nil, "{state}",\n            function( V, Data )\n'
    if marker not in text:
        raise SystemExit(f"the template has no {state} stub to fill")
    return text.replace(marker, marker + body, 1)


def _body(credits: int, string_id) -> str:
    lines = [f"                NPlayer.AddCredits( {{ Credits = {credits} }} )\n"]
    if string_id:
        lines.append(f'                NGUI.ShowInfoText( {{ Text = "{string_id}" }} )\n')
    return "".join(lines)


def main(argv=None) -> int:
    from dso_app.session import Session
    from dsotools import luac, validate
    from dsotools.project import Mod

    ap = argparse.ArgumentParser(description=__doc__.strip().split("\n")[0])
    ap.add_argument("--game", help="a game installation, for the stock items.ini")
    ap.add_argument("--out", required=True,
                    help="folder to build the mod in; it is replaced")
    args = ap.parse_args(argv)

    parent = os.path.dirname(os.path.abspath(args.out)) or "."
    folder = os.path.basename(os.path.abspath(args.out))
    if os.path.exists(args.out):
        shutil.rmtree(args.out)
    os.makedirs(parent, exist_ok=True)

    session = Session()
    if args.game:
        session.open_game(args.game)
    mod = Mod.create(parent, MOD_NAME,
                     "Tests a generated mission override and a mod-written "
                     "string table. Adds credits so the result is readable.",
                     stock=session.stock, folder=folder)
    session.open_mod(mod.root)

    # 1. the override, generated from the stock record
    override = session.create_mission_override("ALWAYS_000")
    with open(override, encoding="utf-8") as handle:
        text = handle.read()
    for state, (credits, string_id) in OVERRIDE_STEPS.items():
        text = _insert(text, state, _body(credits, string_id))
    with open(override, "w", encoding="utf-8", newline="\r\n") as handle:
        handle.write(text)

    # 2. the control: a mission that replaces nothing
    fresh = session.create_mission("PROBE_NEW", type="MTYPE_ALWAYS",
                                   states=list(NEW_STEPS))
    with open(fresh, encoding="utf-8") as handle:
        text = handle.read()
    for state, (credits, string_id) in NEW_STEPS.items():
        text = _insert(text, state, _body(credits, string_id))
    with open(fresh, "w", encoding="utf-8", newline="\r\n") as handle:
        handle.write(text)

    # 3. the text both of them try to show
    table = session.save_strings(STRINGS)

    print(f"{mod.root}")
    for path in (override, fresh, table):
        print(f"   {os.path.relpath(path, mod.root)}  "
              f"{os.path.getsize(path)} bytes")

    # Everything below is the point: a probe that does not parse teaches
    # nothing, and neither does one whose own validator objects to it.
    ok = True
    for path in (override, fresh):
        with open(path, encoding="utf-8") as handle:
            source = handle.read()
        parsed, message = luac.check_syntax(source, name=os.path.basename(path))
        print(f"\n   {os.path.basename(path)}: {message}")
        ok = ok and parsed
        for finding in session.check_script(source):
            print(f"      line {finding['line']}  {finding['kind']}  "
                  f"{finding['symbol']}")
            ok = False

    report = validate.validate_mod(Mod(mod.root), session.stock)
    print()
    for finding in report:
        print(f"   {finding.code} {finding.severity} {finding.message}")
        if finding.severity == validate.Severity.ERROR:
            ok = False

    print("\nready" if ok else "\nsomething above needs fixing")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
