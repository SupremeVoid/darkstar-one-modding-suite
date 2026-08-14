# TODOs

What is planned and not yet done. Items are removed from this file as they land,
and what they turned into is written up in `STATE.md` — this document is the
queue, not the record.

Ordered by when they should happen, not by size.

---

## 1. Next — capability, not polish

**Empty.** Everything the feature-set review of 2026-08-23 asked for has
landed: the two validation rules (`PRJ011`, `PRJ012`), the Problems-tab fixes,
"add new", and the integrated documentation. See `STATE.md`.

Two items were closed without being built, and both are recorded rather than
dropped:

* **"Add new"** — what could be built was built; what could not is in *Parked*
  below with the measurement that settled it.
* **Global undo/redo** — declined. The suite writes loose files, rewrites zip
  archives and installs into the game folder with its own backup ledger; a
  stack that unwinds all three is a large claim, and a half-working one is
  worse than none because people trust it. What exists instead is specific and
  honest: *Revert* in the editors, *Reset to stock*, *Remove from the mod*,
  Deploy's zip-before-delete ordering, and `rootfiles` backups.

What remains is release work.

---

## 2. Finalising, before release

*Holistic docs review done 2026-08-23.* `HANDOVER.md`, `APP_PLAN.md` and the
mod audit are retired; `ARCHITECTURE.md` replaced the plan and the ten source
files that cited it now cite that; every mention of one named third-party mod
was rewritten as the claim it supported; the corpus figures in `specs/` were
brought up to what `verify_all` measures; and the README is the landing page,
with attribution to Ascaron. Nothing personal remains in any `.md` or `.py` —
the only `steamapps` left are in `locate.py`, where finding the game is the job.

*Marked 1.0.0 and the workflows read end to end on 2026-08-23; see `STATE.md`
for the four things that check found.* What is left is the tag itself:

- [ ] **2.3 Tag the release.** `git tag v1.0.0 && git push origin v1.0.0`. Run
      the release workflow's `workflow_dispatch` first — it produces the
      identical artifact without publishing, which is the rehearsal, and the
      release path has never been exercised on this repository.

---

## Parked — deliberately, with reasons

Documented so they are not rediscovered as if new. None is worth doing now.

### Engine questions needing an in-game probe

| Question | Why it is parked |
|---|---|
| **Does `NNPC.Create` load a mod's own `<Race><Model>.xml`?** | The one spawn parameter in the whole reference that becomes an asset name. Stock composes `Model="Ratsmitglied2"` into `MortokRatsmitglied2.xml`, and `Ratsmitglied1`/`Sicherheitschef` appear under several races, which is what composition predicts. If it holds, a mod can spawn a **genuinely new scene** -- the only such route found. One probe mod answers it |
| Do loose `scripts\*.lua` and `user_scripts.bin` both load in one run? | The nearest to worth doing: one probe mod answers it, and a mod shipping both currently has no answer. `[code]` says both are read; never confirmed `[game]` |
| What creates an `MTYPE_USER_SPACE` mission? | Registered and never created, even on hypergate arrival. Niche |
| Can a mission type other than `MTYPE_ALWAYS` act in the *start* system? | Settled for `MTYPE_ALWAYS`: no, not even `Init`. The fallback — top-level code in the script body — already works |
| Why is `Init` asked twice on arrival? | Harmless while `Init` stays pure, which the generated template now says it must be |
| What reads `include.ini`, and from where? | No stock installation ships the folder at all, which suggests it is vestigial |

### Formats

| Item | Why it is parked |
|---|---|
| `.bsd9` shader bytecode and the Direct3D state table | The last of that format. Needs 1,387 shaders disassembled *and* the state table to say what a parameter **does** — the original motivation, and still a large job for a small return |
| Sound: what `Wet` and `Priority` do; other `Select` values | Only `Random2` appears in stock, so anything else is guesswork, and reverb send is hard to measure by ear |
| Which StringIds the stock game defines | 934 of 9,378 recovered (10%) by scanning every byte the suite can read. The rest are inside compiled Lua and A2dLib resources; building the catalogue is a decompiler project. Looking up an id you already know always works |
| One volume `.dds` does not decode | `3DView/textures/noisel8_32x32x32.dds`, 1 of 3,053. Refused by name rather than guessed at. Decide what "preview" means for a 3D texture before touching it |

### Code

| Item | Why it is parked |
|---|---|
| **B905 sweep** — 27 `zip()` calls without `strict=` | Surfaced by the move to Python 3.11. Each site is a real choice between an assertion and a restatement of today's behaviour; some would be genuine wins (`xmldoc.layout_for` length-checks by hand immediately before zipping), some would be wrong (tests zip deliberately unequal line lists). Ignored in `pyproject.toml` with that reasoning, to be done in its own commit |
| A model-centric browse mode | A different way into the Models tab, not a missing capability |
| **A new ship as a new `StarShip.ini` row** | The most valuable version of the added-section probe: `StarShip016_008` with `NationObjectId = 2` plus a `Hunter_A_2.xml`, spawning as an ordinary `WINGTYPE_HUNTER`. A positive answer means new ships without touching the executable. Rests on the same unknown as the row below — does the engine enumerate rows or hold a compiled count? See `specs/scene.md` 4.3.4 |
| **A whole new ini section** (`Ware042`, `Drone06`) | Writable today; the *effect* is unmeasured. Sections are numbered with a fixed prefix and **no stock ini carries a count** — all 299 count-like keys across the 98 files are per-item (`MaxCargo`), never a table size. So the engine either scans until a gap or has the count compiled in, and which one decides whether the section is ever read. One probe mod would settle it |
| **A whole new ini file** | Nothing suggests the engine reads an ini name it does not already know, and no stock ini names another. Would need the same probe, plus a reason to want it |
| **A new scene from a template** | The template half is solved -- `3DView/c_TradeWreck_A_0.xml` is a complete 923-byte stock one-mesh scene to copy. What is missing is a way to *reach* a new scene name. See the row below and `specs/scene.md` 4.3 |
| **A new ship type** | Settled 2026-08-23, negative: `NWing.Create` takes `WINGTYPE_*` and `RACE_*` constants only, and the scene name is composed as `<Family>_<RaceLetter>_<NationObjectId>` where the class-to-family map lives in the executable. A new ship would need a new `StarShip.ini` section *and* that mapping. **Overriding** an existing ship's scene works and is supported |
| **A new `.screen`** | Same shape: `NGUI` has 7 functions and none loads a screen, and no screen references another -- the 83 are a closed set the executable names. Overriding one works and the Interface tab does it |
