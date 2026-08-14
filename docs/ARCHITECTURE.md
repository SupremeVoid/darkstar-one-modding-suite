# Architecture

The rules the code enforces, and why each exists. This replaced `APP_PLAN.md`,
which was a *plan* — written before the suite existed, addressed to a reader
deciding whether to build it, and by the end describing work that was finished.
What survived is the part the source files actually cite: the layering, the
library's shape, and the one API decision everything else rests on.

Where the code says "see `APP_PLAN.md` §4", it means §1 here; "§3.4" means §2.

---

## 1. The four layers, and what each may not do

```
src/dsotools/   the library                        15,700 lines   never imports Qt
app/dso_app/    PySide6: tabs, viewport, dialogs   15,900 lines   never touches bytes
  session.py      ALL application logic, no Qt      3,900
  tabs/           thin widgets over Session         6,000
cli/            argparse wrappers                    1,300 lines
tests/          pytest + a partial-pytest fallback  13,800 lines
tools/          verify_all, check_app, drivers       5,600 lines
```

**`src/dsotools/` — the library.** Stdlib-only core; pixels behind the `image`
extra. Never imports Qt, never imports the app or the CLI. Every exception
derives from `DsoError`, carries the file path, and where meaningful the byte
offset — the GUI turns those directly into clickable diagnostics.
`UnsupportedFormat` stays distinct from `ParseError`: *I know what this is and
will not guess* is a different statement from *this is malformed*.

**`app/dso_app/session.py` — the model.** Everything the application knows and
does, with no Qt: open game and mod, index, validate, deploy, and every asset
operation. This boundary is what makes the app testable at all —
`tests/test_app_session.py` is 239 tests against it, and the widget layer has
none by design.

**`app/dso_app/tabs/` and the shared widgets — the view.** Widgets only. They
may import enums and constants from the library (`FileState`, `Severity`,
`meshview.LAYER_COLLISION`) but **must not parse, decode or write anything**.
Shared components — `viewport.py`, `linked_assets.py`, `effect_editor.py`,
`asset_preview.py`, `docs_window.py` — live one level up because more than one
tab uses them. `theme.py` is the only place the app styles Qt, and it exists
because the platform's selected-row colours discard the colour coding every
list here depends on (measured: 1.1:1 against the accent blue).

**`cli/` — thin wrappers**, so the CLI and the GUI cannot drift.

### How this is kept true

Not by convention. `tools/check_app.py` reads every file under `app/` and fails
if a tab imports a format module, if anything under `app/` parses bytes, or if
`dsotools` imports Qt. `tools/verify_all.py` checks that `import dsotools`
works with no optional dependency installed. Both run in CI.

---

## 2. The API shape that matters most

**Frame every edit as an operation on an existing asset, never as encoding a
new file.**

```python
# not this
write_aim(path, pixels, format="DXT1", mips=7, flags=...)

# this
page = aim.parse(data)
page.replace_pixels(pixels)          # keeps format, mip count, flags, padding
data = page.to_bytes()
```

The reason is the whole project in one line: these formats carry fields nobody
has decoded. An encoder that writes a file from parameters must invent values
for them. An editor that changes one thing and copies the rest forward cannot.

This is why round-trip equality is the acceptance test for every format here,
and why the specs are written as *what was measured* rather than *what the
format is*. A parser that reproduces the input byte for byte has proved it
understood every field, including the ones it does not use.

The same rule is what makes "reset to stock" a **removal** rather than a
rewrite: putting the stock bytes back would leave a file identical to stock,
which is dead weight the validator then reports (`PRJ002`).

---

## 3. Stack

**Python 3.11+ / PySide6 (Qt 6).** Qt is the only Python option that covers all
four hard UI requirements without a second ecosystem: `QGraphicsView` for the
atlas editor, a real tree/table/dock shell, competent text editing for Lua, and
a 3D viewport. PySide6 is LGPL and used unmodified through dynamic linking,
which is what keeps distributing this application legitimate — see
`packaging/THIRD_PARTY_LICENSES.md`.

Python because the validated format code is the project's real asset. Every
alternative either rewrites it or wraps it in IPC.

---

## 4. What the library must never do

* **No `SystemExit`.** A library raises typed exceptions and lets the caller
  decide.
* **No printing.** Progress is a callback; logging goes through `logging`.
* **No guessing.** A field that has not been established is preserved
  verbatim, not defaulted. Where a value genuinely cannot be derived, the
  operation is refused with a message saying so.
* **No Qt, no CLI, no GUI knowledge.** `dsotools` must be usable from a
  five-line script by someone who has never heard of this application.
