# Contributing

Thanks for looking. This project reverse-engineers a 2006 game's file formats
and builds tools on top of them, so the bar for a change is a little unusual:
**it is about evidence more than about code.**

## The one rule that matters

**Nothing here is guessed.** If a change claims something about a file format
or about the engine's behaviour, it has to say how that was established. The
specs in [`specs/`](specs/) use five markers, and a pull request that adds a
claim should carry one:

| Marker | Means |
|---|---|
| `[code]` | read out of the executable |
| `[data]` | measured across the game's own files |
| `[doc]` | from Ascaron's documentation |
| `[game]` | confirmed by running the game |
| `[open]` | not established — stated as unknown rather than guessed |

`[open]` is a perfectly good answer, and often the honest one. A field nobody
has decoded gets preserved verbatim, never defaulted.

## Getting set up

```bash
git clone https://github.com/SupremeVoid/darkstar-one-modding-suite
cd darkstar-one-modding-suite
pip install -e '.[image]'
pip install PySide6            # only if you are working on the application
```

You need Python **3.11+**. A copy of the game is not required to run the test
suite — corpus tests skip rather than fail without one — but you will need one
to work on anything that touches real data.

## Before you open a pull request

```bash
python -m pytest                          # the suite
python -m ruff check src app cli tests tools
python tools/check_app.py                 # the layering rules
python tools/verify_all.py --game "<install>"   # if you have the game
```

All four should be clean. `check_app.py` is the one people trip over: it fails
if a widget parses bytes or if the library imports Qt. That boundary is what
makes the application testable, and it is enforced rather than encouraged —
see [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## What a good change looks like

* **Round-trip first.** Every format here reproduces its input byte for byte
  before anything writes it. A parser that round-trips has proved it understood
  the fields it does not use.
* **A rule needs a failing case and a passing one.** A validator that only ever
  fires is as useless as one that never does.
* **Comments say *why*, and especially why not the obvious alternative.** The
  code already says what.
* **Qt widgets are driven, not unit-tested.** Logic belongs in `session.py`,
  which has no Qt and is tested headlessly; the widget layer is exercised by
  the `tools/drive_*.py` scripts.
* **A check that could not run must never report as passing.** Skip it and say
  so.

[`CLAUDE.md`](CLAUDE.md) has the full set of working rules, all of them learned
by getting something wrong first.

## Reporting a bug

The application has **Help ▸ Report a bug…**, which opens a prefilled issue
with the build and platform already filled in. It deliberately includes no
paths — please keep it that way when you add detail. If a crash report was
written, attaching it helps a great deal; Help ▸ About says where those go.

## Scope

Things that are welcome: format findings backed by evidence, validation rules
for failures that are silent in game, bug fixes, and documentation that makes
an existing finding easier to act on.

Things to raise in an issue first: new dependencies, anything that changes the
layering, and large features — [`docs/TODOS.md`](docs/TODOS.md) records what has
already been considered and **declined**, with the measurement that settled it,
so it is worth a look before starting.

## Legal

*Darkstar One* was developed by **Ascaron Entertainment**. This project is
independent and unofficial, ships no game assets, and requires an installed
copy of the game. Do not contribute game data, extracted assets, or anything
derived from the executable beyond the factual descriptions the specs contain.

Contributions are accepted under the same MIT licence as the project.
