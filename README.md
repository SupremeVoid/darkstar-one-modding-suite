# Darkstar One Modding Suite

Modding tools for **Darkstar One** — a standalone Python library, a desktop
application built on it, and a set of command-line converters.

> *Darkstar One* was developed by **Ascaron Entertainment** and released in
> 2006. This project is an independent, unofficial toolset: it ships none of
> the game's data and requires an installed copy. All game formats, names and
> trade marks belong to their respective owners.

https://github.com/user-attachments/assets/4ae70d5c-18b1-4e41-8308-c8bbd6011998

## Why it exists

The game shipped a modding kit but it is very barebones and the engine almost never says
when a mod is wrong. It loads what it understands, ignores what it does not,
and reports neither. A file in the wrong folder, a script that returns nothing,
a sound nobody declared: each produces a game that starts normally and simply
does not contain your change.

So this is not primarily an editor. It is a tool that knows **which mistakes
are silent**, and says so before you ship:

* every diagnostic corresponds to a failure that produces no error in game, and
  each one records how that was established;
* every format is round-tripped byte-for-byte before it is written, because a
  parser that reproduces its input has proved it understood the fields it does
  not use;
* nothing is guessed — where a value cannot be derived, the operation is
  refused with a message saying why.

### Where to start

| You want to | Read |
|---|---|
| mod the game | **[`specs/modding_guide.md`](specs/modding_guide.md)** — what works and what fails quietly. Also inside the app, under **Help ▸ Documentation** |
| understand the data | **[`specs/README.md`](specs/README.md)** — the asset chain, archive precedence, the mod rules |
| work on this project | [`CONTRIBUTING.md`](CONTRIBUTING.md), then [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) |
| use the command line | [`cli/README.md`](cli/README.md) — all nine tools |

## The library

Standalone by design. It imports no UI toolkit, no CLI code, and its core has no
third-party dependencies — `import dsotools` works on a bare interpreter, which
is how the converters keep their "no third-party packages" property. Pixel work
lives behind an extra.

```bash
pip install -e .            # core, stdlib only
pip install -e '.[image]'   # + Pillow, numpy for image and DXT decoding
```

```python
from dsotools import vfs
from dsotools.formats import scene

game = vfs.from_install(r"C:\Games\DarkStar One")   # reads .cpr directly

s = scene.parse(game.read("3DView/PlayerShip.xml"))
for mesh in s.meshes():
    model = game.resolve_reference(mesh.model, scene_path="3DView/PlayerShip.xml")
    for effect in mesh.effects:                      # one per submesh
        print(effect.shader, effect.textures, effect.parameters)
```

Two rules the library holds itself to:

* **Nothing prints and nothing exits.** Errors are typed exceptions carrying the
  path and, where meaningful, the byte offset. The CLI turns those into exit
  codes; the GUI turns them into clickable diagnostics.
* **Edits preserve everything they did not touch.** `scene.parse(x).to_bytes()`
  is byte-identical to `x`, and changing one texture changes one token. A
  serialiser that reformats would turn every one-line mod edit into a whole-file
  diff and destroy the diff-against-stock feature the app is built around.

## The application

```bash
pip install PySide6
python app/main.py
```

**No extraction step.** The `.cpr` archives are ordinary ZIP files, so the app
reads an installed game directly. The installation is found automatically via the
Steam registry key and its library folders, GOG and uninstall entries, then the
usual paths; every candidate is *validated* (executable **and** archives present)
rather than trusted, because an uninstalled Steam entry leaves the folder behind.
`--game` overrides it, and `--data` still accepts a pre-extracted tree.

Creating a mod writes the manifest **and** a stock `inifiles/items.ini`. That
second file is not optional: without it the game skips the folder entirely and
reports nothing, so a mod created without it is invisible with no way to find out
why.

Working today: **Project** (mod discovery, what each file *does* — override,
addition, identical-to-stock, or dead — where it must be delivered, and what in
the game installation is not stock), **Models**, **Textures**, **Data**,
**Interface**, **Scripting** (a Lua editor checked against the API the
executable really registers, plus the mod's string table), **Audio** (both sound
databases in one tree, with playback, and adding a sound reads its rate and
length out of the file) and **Problems** (the diagnostics engine, grouped by
rule). Nothing is stubbed any more.

**Help ▸ Documentation** (F1) opens the guide and every format reference inside
the app, rendered from the same markdown this repository holds — so what ships
and what is written cannot drift.

Two rules keep it reviewable:

* **The GUI never touches bytes and the model never imports Qt.** All state and
  logic live in `session.py`, which is unit-tested headlessly; the widget code is
  thin enough to check by reading.
* **Nothing slow runs on the UI thread.** `ds_3dtex` alone is 1.5 GB, and a
  frozen window reads as a broken program.

If Qt fails to load, the app runs `tools/pyside_doctor.py` instead of printing a
traceback. Anything that escapes is written to a crash report next to the
executable — a packaged build has no console, so "it didn't work" has to become a
specific sentence somewhere.

## Checking a mod

```bash
python tools/validate_mod.py --list
python tools/validate_mod.py --mod "<...>/Customization/<Mod>"
```

Exits 1 if any ERROR was found, so it works as a pre-release gate. The game is
autodetected; `--game` or `--data` override. Without a baseline the structural
rules still run and the baseline rules say they were skipped rather than silently
passing.

Every rule corresponds to a failure that is **silent in game** — the engine
ignores it, crashes with no message, or renders something subtly wrong. Rules
that merely encode taste are deliberately absent.

| Code | Severity | Catches |
|---|---|---|
| `PRJ004` | error | No `inifiles/items.ini` — the game will not list the mod, and reports nothing |
| `PRJ005` | warning | Loose `3DView/` file; never read by the engine |
| `PRJ006` | warning | Same path in `user_data.zip` and loose — the loose copy is dead |
| `PRJ002` | info | Byte-identical to stock; the override does nothing |
| `SCN001` | error | `EffectContainer` count ≠ submesh total — what a DCC tool breaks on export |
| `SCN002` | error | Model or texture reference does not resolve |
| `SCN003` | error | Malformed scene XML — crashes the engine on load, it is not skipped |
| `SCN004` | hint | Scene edited without its `_low` twin |
| `SND001` | error | Sound declared in `user_sounds.xml` but the file is absent |
| `SND002` | info | Audio shipped but never referenced |

`SND001` and `SND002` are checked as a pair on purpose: a path typo puts the same
sound in both lists at once, and that pairing is what makes it a diagnosis rather
than a guess. It found two dead voice lines in the first mod it was pointed at.

## The reference graph

```bash
python tools/asset_index.py --db game.db --build
python tools/asset_index.py --db game.db --used-by 3DView/textures/playership_body_00_col.dds
```

Forward questions ("what does this scene reference?") need one file open.
**Backward questions do not** — *what uses this texture, what breaks if I change
this model, is anything still pointing at the file I just deleted* — and those
are the ones a modder actually has. The index exists for those; the file listing
is just what it hangs off.

Broken references are **recorded, not dropped**: an unresolved binding is a row
with a null destination, which is what makes `--unresolved` able to list every
one project-wide.

## Command-line tools

**[`cli/README.md`](cli/README.md)** lists all nine and how they are used;
[`cli/cli_3do.md`](cli/cli_3do.md) and [`cli/cli_aim.md`](cli/cli_aim.md) are
the full manuals for the model and image round trips.

```bash
python cli/3do2gltf.py  MODEL.3do  out.gltf     # and gltf23do.py back
python cli/3do2obj.py   MODEL.3do  out.obj
python cli/dsvalidate.py <folder>               # .3do/.shd round-trip gate
python cli/aim2png.py   PAGE.aim   out.png      # and png2aim.py back
python cli/aimatlas.py  find|list|extract|patch # atlas pages via their .tex
python cli/aimfind.py   SPRITE.png PAGE.aim ... # locate a sprite in a page
python cli/aimvalidate.py <folder>
```

They are thin fronts over `dsotools` — every one of their operations is available
as a library call with typed exceptions instead of `sys.exit`.

## Verifying everything

```bash
python tools/verify_all.py --game "<...>/DarkStar One" --mod "<...>/Mod"
```

The unit tests prove the code does what it was written to do. This proves the
**claims in the specs** still hold against real files — a different question, and
the one that has caught every real bug here so far: the DXT5 alpha weights (both
implementations wrong the same way), the missing trailing newline (only a
modder-authored file had none), the `.tex` uninitialised tail bytes.

```bash
python tools/check_app.py
```

The Qt layer cannot be unit-tested in CI, so it gets a static check instead:
`self.x` read in a class that never defines it, duplicate method names, targets
that resolve to nothing. It exists because a bad edit once moved two methods out
of `ProjectTab` into a dialog defined below it — the file compiled, every import
succeeded, and the app died on launch. It runs in a tenth of a second and is part
of `verify_all.py`.

## Tests

```bash
pytest                                    # unit tests only; corpus tests skip
DSO_GAME_DATA=/path/to/extracted pytest   # + corpus tests
```

Corpus tests skip rather than fail when game data is absent, so a clean checkout
is green while a developer with the game gets the exhaustive run.

Reference figures the corpus tests guard, measured on the full stock extraction:

| Check | Result |
|---|---|
| Scene XML byte-identical round-trip | all scenes |
| `EffectContainer` count == `submesh_total` | 9,806 / 9,806 |
| Scene model references resolve | 12,354 / 12,635 (97.8%) |
| Scene texture references resolve | 48,163 / 49,012 (98.3%) |
| Sound database round-trip, and every declared number reproduced | 442 / 442 |
| `.res` string tables round-trip | 4 tables, 28,222 entries |

Two `EffectContainer` mismatches exist in the shipped data —
`objects/mainshape_20.3do` in `TunnelVersion1{,_low}.xml` — and are tolerated
**by name** rather than by weakening the rule, which is why the count above is
exact rather than a percentage.

If no package index is reachable, `python3 tools/offline_test_runner.py` runs the
same suite with a minimal pytest shim. It is a convenience for locked-down
environments, not a second test framework.

## Requirements

Library core and the `.3do` tools: **Python 3.11+**, standard library only.
`dsotools[image]` and the `.aim` tools: Pillow, plus numpy for `aimfind.py` and
for fast DXT decoding. The application additionally needs PySide6.

## Contributing

Issues and pull requests are welcome —
[`CONTRIBUTING.md`](CONTRIBUTING.md) explains what a change needs, and the
short version is that **nothing here is guessed**: a claim about a format or
about the engine carries a marker saying how it was established.

The application has **Help ▸ Report a bug…**, which opens a prefilled issue
containing the build and platform.

## Licence

MIT — see [`LICENSE`](LICENSE). Third-party components and their licences are listed in
`packaging/THIRD_PARTY_LICENSES.md`; PySide6 is LGPL and is used unmodified
through dynamic linking.

This project is not affiliated with or endorsed by Ascaron Entertainment or any
present rights holder. It contains no game assets.
