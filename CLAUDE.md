# Working rules for this repository

Rules established while building this project. They are here because each one
was learned by getting it wrong first.

Read [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) before changing structure,
and [`docs/TODOS.md`](docs/TODOS.md) before starting work — it records what was
*declined* as well as what is queued.

---

## 1. Evidence, not plausibility

**Never guess at a format.** A field that has not been established is preserved
verbatim, never defaulted. Where a value genuinely cannot be derived, refuse the
operation and say why rather than writing something plausible.

**Byte-exact round-trip is the acceptance test.** A parser that reproduces its
input byte for byte has proved it understood every field, including the ones it
does not use. Every format here round-trips before anything writes it.

**Mark how you know.** The specs use `[code]` (read from the executable),
`[data]` (measured across the game's files), `[doc]` (Ascaron's documentation),
`[game]` (confirmed by running the game) and `[open]` (not established). Do not
promote a marker without doing the work — `[open]` is a legitimate answer and
the honest one more often than it looks.

**A check that could not run must never report as passing.** Skip it, and say
it was skipped. "No findings" from a run that checked nothing is a lie of
omission, and the more reassuring it looks the worse it is.

**Prefer running a shipped tool over reading it.** The `.res` hash was guessed
at for a long time and every guess failed; loading the shipped converter and
calling its hash method settled it in minutes.

---

## 2. Layers

```
src/dsotools/    the library      — never imports Qt, the app, or the CLI
app/dso_app/     the application  — never touches bytes
  session.py       all logic and state, no Qt
  tabs/            widgets only: no parsing, no decoding, no writing
cli/             thin wrappers over the library
```

`tools/check_app.py` enforces this and runs in CI. If a tab needs to parse
something, the parsing belongs in the library and the answer belongs in
`session.py`.

**Frame edits as operations on an existing asset, never as encoding a new
file.** This is the decision everything else rests on — see
`docs/ARCHITECTURE.md` §2.

**One statement of each rule.** Delivery rules live in `project.check_mod_path`;
the "what uses this" wording lives in `linked_assets.show_users`; the sound
tolerance lives in `validate.sound_metadata_drift`. Two copies drift, and the
drift is silent.

---

## 3. Testing

* **Library and session logic get unit tests.** `tests/` runs without a game
  installation; corpus tests **skip**, never fail, when game data is absent.
* **Qt widgets get drivers, not tests.** `tools/drive_*.py` open the real
  window and report what happened. A `QApplication` inside the test suite once
  left threads behind that turned an eleven-second run into a hang.
* **Every rule gets a positive and a negative case.** A validator that only
  ever fires is as useless as one that never does.
* Run the full suite, `ruff check`, and `tools/check_app.py` before saying
  anything is done. `tools/verify_all.py --game <install>` re-measures the
  corpus figures the specs quote.

---

## 4. Writing things down

* **`docs/STATE.md` is the record**, `docs/TODOS.md` is the queue. Items leave
  TODOS when they land, and what they became goes into STATE.
* **Record what was declined, with the measurement that settled it**, so it is
  not rediscovered as if the question were open.
* **Correct over-claims.** If a doc says something stronger than what was
  measured, weaken it and say what was actually observed.
* Numbers get the corpus they were measured on. A figure with no corpus
  attached looks stale rather than superseded.
* `specs/` ships inside the application, so it is user-facing: no personal
  paths, no named third-party mods, no working notes.

---

## 5. Safety

* **Never modify a real mod or a game installation to test something.** Build a
  throwaway copy. Anything that writes into the game folder goes through
  `dsotools.rootfiles`, which keeps a backup ledger.
* **Ask before anything irreversible or outward-facing.** Committing, pushing,
  and installing into the game folder are the author's calls.
* **Order writes so the recoverable failure is the one that happens.** Write
  the archive before deleting the loose originals; write the loose copies
  before rewriting the archive without them. A file in two places is a warning;
  a file in neither is lost work.
* Do not weaken a rule to make a corpus pass. Tolerate known-bad data **by
  name** so a third instance still fails.

---

## 6. Style

* Comments say **why**, and especially why *not* the obvious alternative. The
  code already says what.
* Match the surrounding file: its comment density, its naming, its idiom.
* No `SystemExit` and no printing in library code. Errors are typed exceptions
  carrying the path and, where meaningful, the byte offset. Progress is a
  callback.
* Messages a user will read should name the failure and the fix. "Disabled" with
  no reason is how people conclude the app is broken.
