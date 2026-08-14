# Where the project stands

*Last reviewed 2026-08-23.*

The record: what was built, what was measured, and what turned out to be wrong
on the way. Written so someone with no history here can pick the project up.

Three companions, each with one job:

* **`docs/ARCHITECTURE.md`** — the layering rules the code enforces, and the one
  API decision everything rests on.
* **`docs/TODOS.md`** — the queue. What is *not* done, and what was parked with
  the measurement that settled it.
* **`specs/`** — the formats, as evidence. `specs/modding_guide.md` is the
  reader-facing summary and ships inside the app.

This page is the long one because it is evidence rather than prose, and its real
value is **the things that are easy to get wrong on re-entry**.

## If you read only four things

This file is long because it is evidence, not prose. If you are picking the
project up cold, read these and start working; the rest is reference you will
come back to when a specific thing bites.

1. **"Working agreement"** and **"Read this first"**, immediately below — the
   layer rules and the five commands that decide whether the tree is healthy.
2. **"Things that cost time before"** — every one is a real bug found the hard
   way, and they are the reason the rules exist.
3. **"Look at the picture: `tools/drive_models_tab.py`"** — if you are going to
   touch the viewport, this is not optional. Four consecutive defects were
   invisible to every assertion in the suite.
4. **"Next, in order"** — what to do, with the reasoning already done.

Two sentences carry most of the hard-won judgement, and both were learned by
getting them wrong:

> **Measure the finding before designing around it.** "16 malformed files" was
> 3. "57 corrupt drawables" was 0.

> **The object model lies about the viewport, and only the viewport is the
> product.**

## Working agreement

Edit the repository in place. Do not hand back archives of changed files.

## Read this first

**What it is.** A desktop modding suite for *Darkstar One* (Ascaron, 2006): a
standalone Python library that understands the game's formats, and a PySide6
app on top of it. MIT; PySide6 is LGPL and ships as replaceable DLLs.

**The four layers, and the rules that hold them apart** (enforced by
`verify_all`, not by convention):

| Layer | Rule |
|---|---|
| `src/dsotools/` | never imports Qt; stdlib-only core; every error is a `DsoError` |
| `app/dso_app/session.py` | all application logic, still no Qt — this is what tests can reach |
| `app/dso_app/tabs/` + widgets | widgets only; never parse, decode or write |
| `cli/` | thin wrappers, so CLI and GUI cannot drift |

**How to check it.**

```bash
python -m pytest -q                    # 519 tests
python tools/offline_test_runner.py    # same suite, no pytest (verify_all falls back to it)
python tools/check_app.py              # static check of the widget layer
python -m ruff check .                 # pinned; config in pyproject [tool.ruff]
python tools/verify_all.py --game "<install>"   # 22 checks against real data
python tools/drive_models_tab.py --game "<install>" --scene 3DView/ComSat.xml
                                       # opens the real viewport and reports
                                       # what is drawn -- see below
python packaging/build.py              # Windows one-folder build, self-verifying
```

**Current gate result** (re-run 2026-08-17):

| Gate | Result |
|---|---|
| `pytest -q` | 519 passed, 13 skipped (the 12 need game data) |
| `offline_test_runner.py` | same suite, same result, no pytest |
| `check_app.py` | 20 files, 0 problems |
| `ruff check .` | clean |
| `verify_all --game` | **20 pass / 0 fail / 2 skipped → PARTIAL** |
| `packaging/build.py` | exit 0, 254 MB one-folder build, launched and driven |

**Nothing is red.** The two skips are the mod checks, which need `--mod`; the
run therefore reports **PARTIAL rather than PASS**, and that is the point —
a check that could not run must never look like one that passed. The last
failure, the `.anim` size disagreement, was resolved on 2026-08-17 and turned
out to be a defect in this project rather than in Ascaron's data; see "The
`.anim` sizes".

Notable numbers behind those passes: 1,187/1,187 stock scenes round-trip
byte-exactly, 3,110/3,110 `.3do` models, 1,107/1,107 `.anim` drawables,
`SCN001` at 9,806/9,806 (100%), 15,978/15,978 effects whose shader slot
count matches the textures the scene binds, and **0 of 3,110 models firing any
of the seven new `MDL` rules**.

## Done and verified

- **Phase 0 (RE)** — closed except CHM → JSON, which only gates Scripting.
- **Phase 1 (`dsotools`)** — standalone library, stdlib-only core, all formats.
- **Phase 2 (project layer)** — VFS, mod model, diff-vs-stock, diagnostics,
  asset index, `.dsoproj`, atlas editing.
- **Phase 3 (app shell)** — **closed.** The Windows build has been run,
  launched and exercised; see "The Windows run" below for the defect it found.
- **Phase 2a (viewport spike)** — **passed.** Qt Quick 3D carries the viewport;
  no fallback renderer. See "The viewport spike" below.
- **Phase 4 (Textures tab)** — **built.** Browser, standalone `.aim`/`.dds`
  preview, and the TexPage editor with rectangle overlay, replace and rescale.
  See "The Textures tab" below for the modelling bug it exposed.
- **Phase 5 (Models tab)** — **built**, and running in the packaged build.
  Scene browser, textured Qt Quick 3D viewport, LOD selector, per-submesh
  isolate, shader/texture readout, live `SCN001`.
- **Phase 5b (variants and editing)** — **built.** Variant selector, shared
  linked-assets panel with actions, cross-tab navigation, effect editor
  (shader / parameters / material), and replace-in-place. See "Variants and
  linked assets" below.
- **Phase 5 remainder** — **closed 2026-08-16.** `.glb` import now runs
  `SCN001` *before* it lands, and glow / shine / distortion / shield are
  drawable, toggleable layers. See "The rest of Phase 5" below.
- **Phase 5g (`MDL001`-`MDL007`)** — **closed 2026-08-17.** The last
  validation family that was *specified and absent*. Seven codes, every
  severity chosen from a count over the real corpus rather than from taste, and
  one candidate rule measured out of existence. See "The model rules" below.
- **Phase 5f (editing polish, blinkers, settings)** — **closed 2026-08-17.**
  Effect editor gained reset-to-default *and* reset-to-current with a live
  preview; the Project tab gained reset-to-stock and stopped listing
  `.dsoproj`; blinkers are drawn as markers and edited through a table with a
  red selection marker; layer opacity is tuned per layer; and an app settings
  file next to the exe remembers "do not show again". See "Blinkers",
  "Settings" and "Editing" below — and note that **four visual regressions**
  came out of this phase, which is what produced `drive_models_tab.py`.

Gate for "done": `python tools/verify_all.py --game <install> --mod <mod>`
passes. `python tools/check_app.py` covers the Qt layer, which cannot be
unit-tested. **519 unit tests**, all passing on Windows under both runners.

## The Windows run — and the bug only it could find

`python packaging/build.py` builds and self-verifies
`dist/DarkstarOneModdingSuite/` (254 MB). The app launches, autodetects the
Steam install, indexes it ("7 layers, 13948 assets"), loads a mod and shows the
right diff tree. All seven tabs open; it closes cleanly.

**Deploy was broken on Windows, and POSIX cannot show you this.** `Mod.files()`
holds `user_data.zip` open to serve lazy reads; `_write_zip` finishes with
`os.replace()` onto that same file. POSIX allows renaming over an open file.
Windows refuses — `PermissionError: [WinError 5]`, naming the *temp* file, not
the open handle. Seven Deploy tests failed the first time the suite ran here.

The fix is `Mod.close()`, called immediately before the replace — late enough
that the relocation payload and the old archive have both already been read,
early enough to release the handle. `Session.build_index` now also closes the
mod layers it mounts rather than leaving them to the collector; relying on
refcounting would have made Deploy fail only *sometimes*, which is worse than
failing always. `Layer.close()` is a no-op on the base class so a mixed stack
can be closed without type-testing.

Reproducing it needs an **existing** `user_data.zip` plus loose `3DView/` files;
with no zip there is no destination to collide with. Verified end-to-end on a
copy of the tutorial mod with a seeded zip.

Two more Windows-only defects, in the test tooling rather than the product —
both of which made a green suite dishonest here:

- `offline_test_runner` read test sources with `read_text()` and no encoding,
  i.e. the locale codepage. Python source is UTF-8 by definition; on this
  German Windows a test asserting `"höhere"` compared against `"hÃ¶here"`.
- `test_check_environment_notes_that_windows_builds_need_windows` fakes
  `sys.platform = "linux"` and then really imports PyInstaller, whose `compat`
  reads `sys.platform` at import and shells out to `ldd`. It passed only while
  PyInstaller was absent — installing the build toolchain broke it.
  `test_find_game_returns_none_rather_than_raising` was the mirror image: it
  asserted the game is *not* installed, so it failed on precisely the machines
  it exists to serve.

**Run the suite on Windows routinely, not once.** Everything before this ran on
Linux or in the cloud, and all three defects above were invisible there.

## The viewport spike — Qt Quick 3D is confirmed

`tools/spike_viewport.py --game <install> --seconds 8 --screenshot out.png`.
`PlayerShip.xml`: 40 submeshes, 31,913 triangles, 28 textures, **144 fps**
vsync-locked with no per-frame Python. `DockYard_A.xml` — a different shape of
scene, 3 shaders — rendered with **no new plumbing**. All three pass criteria
met, so the fallback renderers in `docs/ARCHITECTURE.md` §2 are not needed.

Two findings Phase 5 inherits:

- **The spike had never actually run before.** `@QmlElement` reads
  `QML_IMPORT_NAME` from the decorated class's *module* globals by walking the
  calling frame; the spike defined them inside `run_qt()`, so it raised before
  opening a window.
- **`_nrm` textures are DXT5nm, not RGB normal maps** — X in alpha, Y in green,
  Z reconstructed. Measured: as RGB the vectors have median length 0.345;
  unswizzled, reconstructed Z has median 0.994. Feed the raw RGB to a PBR
  material and the ship renders black and speckled. The *slot* convention
  (`SLOT_NORMAL = 2`) was right all along — it was the encoding that was wrong,
  and the spike's own docstring aimed the suspicion at the wrong one of the two.
  `--no-normal` is the first diagnostic to reach for.

## What Deploy does, and the three decisions inside it

`Mod.deploy_plan()` / `Mod.apply_deploy_plan()` in the library,
`Session.deploy_preview()` / `Session.deploy()` above it, `DeployDialog` and
`MainWindow.run_deploy` on top. Deploy makes a mod's *layout* match what the
engine reads; it never changes file contents.

- **Relocate vs. conflict.** A loose `3DView/` file with no counterpart in the
  zip is moved in. One that the zip *already has* is reported and left alone.
  This is not caution for its own sake: one large third-party mod has **424**
  such paths, and a Deploy that overwrote the zip from the loose tree would
  have rewritten 424 entries from copies that may be older. Measured, not
  guessed — the plan was run against every mod in `Customization/`.
- **The gate exempts errors it repairs.** `PRJ004` (no `items.ini`) is an
  error, and writing that file is Deploy's own first act. Counting it as a
  blocker makes the app refuse to apply a fix because the fix has not been
  applied. `DeployGate.SELF_HEALED` is that exemption; everything else blocks,
  and blocking is overridable only through a checkbox that names what is being
  overridden.
- **Zip first, delete second.** Both steps can fail. A failed delete leaves the
  file in both places and the engine reads the zip, so the mod is *correct*. A
  failed zip write after deleting would have destroyed the only copy. Failed
  deletes are collected in `DeployResult.not_removed`, never raised.

The official Ascaron tutorial mod ships two dead loose files
(`3DView/Container.xml`, `Container_low.xml`). Deploy fixes those.

## Packaging

`packaging/` — spec, `build.py`, runtime hook, licence notice, README.

**`excludes` does not exclude the DLLs.** Listing `PySide6.QtWebEngineCore` in
the spec's `excludes` stops the *Python module* being imported and does nothing
about the binary: PySide6's own PyInstaller hook collects Qt's DLLs wholesale,
so `Qt6WebEngineCore.dll` shipped anyway — **205 MB, nearly half the build**,
for a module the app never loads. The spec now filters `a.binaries` and
`a.datas` as well, and the folder went from 450 MB to **254 MB**.

Deliberately narrow: `opengl32sw.dll` (20 MB) is Qt's software OpenGL fallback
and stays, because the viewport is Qt Quick 3D and a machine with no usable GPU
driver is exactly the one that needs it. numpy's OpenBLAS stays for the same
reason. A smaller download is not worth a bundle that fails on somebody else's
machine.

The point of `build.py` is that it **verifies what it built** rather than
trusting the flags it passed: it reads the executable's own PE subsystem field
to confirm the binary is GUI, checks Qt's platform plugin is in the bundle, and
checks the licence notice shipped. `--check` runs the prerequisite half
anywhere, including a machine that cannot build.

`app/dso_app/frozen.py` holds the no-console rules, and `tests/test_frozen.py`
covers them **without building anything** — `sys.stdout = None` is exactly what
PyInstaller hands a windowed process. Three real defects were fixed there:

- the PySide6-failure branch printed to `sys.stderr`, which is `None` in a
  windowed build — so the one path whose whole job is explaining a failure
  would itself have raised, giving a silent exit;
- `argparse` writes usage to `stderr` and would do the same on a bad flag;
- crash reports were written next to the executable, i.e. into
  `C:\Program Files\...`, where the write fails and the report was dropped.
  They now fall back to `%LOCALAPPDATA%`.

**Done.** The build has been run and launched on Windows; `dist/` holds the
current one, rebuilt after the Deploy fix. Building needs PyInstaller, Pillow
and numpy installed alongside PySide6 — `build.py --check` names any that are
missing and refuses to build without them.

## The Textures tab — and the layer bug it exposed

`tabs/textures_tab.py` is widgets only. The work is in `Session`
(`texture_assets`, `decode_preview`, `open_atlas`, `atlas_problems`,
`commit_atlas`), which is why it has tests instead of a click-through.

`Session.open_vfs()` is a **context manager**, and that is deliberate: it mounts
the mod's `user_data.zip` as a `ZipLayer`, and a tab that held one open would
make the next save fail on Windows — the same defect that broke Deploy.
Everything reads eagerly, so the page outlives the block.

**The bug it found.** `iter_mod_layers` mounted a mod's *entire* loose tree as
`loaded=False`, below stock. The established finding was much narrower — loose
**`3DView/`** is never read — and generalising it contradicted `ZIP_ONLY_ROOTS`,
`ModFile.is_dead` and `Mod.deploy_target`, all of which scope it correctly. It
is also refuted by the project's own load-bearing fact: `inifiles/items.ini` is
read loose, which is why `PRJ004` exists at all.

Consequences while it was wrong, none of them visible from the outside:

- the asset index recorded **stock** where a mod shipped a loose override;
- `check_atlas` validated the **stock** page for a mod shipping its own, so
  TEX001–TEX004 were checking the wrong file;
- a saved atlas edit could not be read back by the tool that wrote it.

Now three layers: the zip (2000), the loose tree the engine really reads
(1900, above stock), and loose `3DView/` (50, `loaded=False`) kept visible so
`PRJ005`/`PRJ006` can still warn. `DirectoryLayer` grew an `only=` parameter to
express the split without re-rooting the paths.

## The .aim writer, and why a saved atlas edit did nothing in game

Found by a real edit that reached the game and had no effect.

`AtlasPage.save()` called bare `aim.from_image()`, which always writes
`IMTC32` with `footer_extra=(0,0,0)`. But `images/TexPage_0_4.aim` — the page
behind `TexPage5.tex` — ships as **BMPRES**, and *every* shipped page carries
`footer_extra=(0,0,1)`. So the tool wrote a 524,420-byte IMTC32 file where the
game expected a 393,432-byte BMPRES one, and the game ignored it.

This is exactly what `docs/ARCHITECTURE.md` §3.4 said the library must never do: a
replacement keeps the encoding, flags, footer, tiling and declared size of the
asset it replaces. `aim.from_image_like(source, img)` is now that call, and
`from_image` is the exceptional one. Result on stock data: **8 of the 10
referenced pages round-trip byte-identically** (they did not before — every one
lost `footer_extra`), and the two `IMJPG24A` pages are **refused with a message**
rather than written in a format the engine drops.

Two rules inside it:

- **Same size ⇒ keep the original grid** (that is what makes round-trip exact).
  **Different size ⇒ re-tile**, because `rescale()` changes the dimensions and
  reusing the old grid silently *crops* the new page to the old size. The first
  version of the fix did exactly that, and two tests caught it.
- A page whose declared size differs from its stored size is using the footer as
  an addressing space (`specs/aim.md` §9). How that maps is not established, so
  rebuilding one is **refused** rather than guessed.

`BMPRES` is written by hand rather than through Pillow: Pillow fills in
`biSizeImage` and 96 DPI, while shipped files carry 0 and 72 DPI. Ten bytes per
tile, and without them the round-trip is not byte-exact — which is what makes
diff-against-stock trustworthy.

**`IMJPG24A` pages are editable too, by re-encoding.** They are the remaining 2
of the 10, readable but not writable. Rather than leave them permanently
read-only, `save()` writes them as `IMTC32` — lossless, alpha intact, and what
six of the other pages already use. The engine reads the codec from the chunk
tag rather than the filename, so the result loads. It is opt-in at the library
level (`from_image_like(..., fallback=…)` still refuses without it) and
**reported** through `AtlasPage.recoded_to`, because silently changing an
asset's format is the bug this whole section is about.

## Closed: a mod's loose `images/` is never read

**Settled in game on 2026-08-15, and it was the real reason a texture edit did
nothing.** An edited atlas page deployed loose was invisible; the identical file
placed in `user_data.zip` appeared immediately.

So `ZIP_ONLY_ROOTS = ("3DView", "images")`. That one constant carries the whole
consequence: `deploy_target` routes pages into the zip, `ModFile.is_dead` counts
a loose one as dead, `iter_mod_layers` stops resolving it, `PRJ005` warns, and
Deploy relocates it. `PRJ005`'s message now names the offending folder rather
than hardcoding `3DView/` — being told about the wrong folder is worse than
being told nothing.

One edit can still land in two places, and that is correct: the page goes into
the zip, the `.tex` and `.anim` stay loose because `scripts/` genuinely is read
loose.

`staticImages/` remains **untested** and is deliberately in neither list.
"Probably loose because it is not `3DView/`" is exactly the reasoning that kept
`images/` wrong.

## The Models tab

`dsotools/edit/meshview.py` (no Qt) turns a scene into draw calls;
`app/dso_app/viewport.py` is the Qt Quick 3D widget; `tabs/models_tab.py` is
thin. Browsing is by **scene**, not by file — a `.3do` on its own raises "with
which textures?", and the honest answer is "it depends which scene you mean".

Four things worth knowing before touching it:

- **`@QmlElement` reads `QML_IMPORT_NAME` from the module globals**, by walking
  the calling frame. Moving those two constants inside a function breaks every
  registered type before a window opens. That is the bug that meant the Phase 2a
  spike had never actually run.
- **`QtQuick3D.Helpers` does not load** from the PySide6 wheel — its plugin DLL
  is missing dependencies — so `OrbitCameraController` is unavailable and the
  orbit rig is hand-written QML.
- **`QQuickWidget` has no `loadData`.** Build a `QQmlComponent`, `setData`, then
  `setContent`. Shipping a loose `.qml` beside a frozen one-folder build is one
  more thing that can go missing.
- **A QML object created with `createObject(null, …)` is owned by the
  JavaScript engine and will be garbage-collected.** The materials' `Texture`
  objects were created that way, so the first GC stripped every map and the
  model turned flat grey — and camera movement is what triggers a GC, which
  made a lifetime bug look like a rendering bug. Measured: 1207 coloured pixels
  before `engine.collectGarbage()`, 0 after. They are declared as children of
  the `Model` now, so they live and die with it.
- **Texture slots come from the shader, not the filename.** `.bsd9` names them;
  `meshview.pick_slots` asks it first and only falls back to `_col`/`_lgh`/`_nrm`
  where the shader names nothing useful. See "`.bsd9` — decoded".
- **A draw call is matched to its scene detail by `node_path`, never by name.**
  `session.mesh_for` is the only place that join happens. Names collide — 85
  meshes under 28 names in `PlayerShip.xml` — and matching on one silently
  reads another variant's effects. See "Preview was broken for `.dds`".
- **Slot meaning now comes from the shader**, and the UI distinguishes the two
  cases: rows are labelled with the `.bsd9`'s own slot name, and the amber
  "inferred" marker fires only where the shader named nothing usable. The
  filename convention is still there as the fallback — do not let *that* half
  silently become "the tool knows".

## Malformed XML, and the 55 files it was hiding

**Decided and closed 2026-08-16.** This had been "the next item" for several
rounds. Measuring it first shrank it, and then turned up something larger.

**The "16 malformed files" were two unrelated things.** Thirteen have root
`<MeteoridFieldDefinition>` — a schema the scene parser does not handle at all,
and `is_scene()` already filters them out by sniffing for `<WalhallaScene`, so
they **never reach the parser** in the app or in `verify_all`. They only ever
appeared in the count because expat fails on well-formedness *before* the
root-element check runs. They needed no tolerance, and the bare-`&` and
stray-tag cases live entirely among them — so those questions evaporated.

That leaves **three** genuinely malformed scenes — `AsteroidsVolume05`, `10`
and `20` — each with one defect: `</object>` closing `<Object>`.

**The decision: tolerate a case-mismatched close tag, and nothing else.** The
engine loads these, so its reader matches case-insensitively. The repair is
applied to a copy of the bytes purely so expat can read them, and because
changing a tag's *case* never changes its *length*, every byte offset survives
it — which is what lets the file be written back with its own `</object>`
intact. `Scene.repaired_tags` reports it, because a tolerated file is still not
well-formed and a modder shipping one should be told. Anything that is not a
case-only mismatch still raises `ParseError`.

**And behind those three, 55 more.** `_scene_rt` let the `ParseError` escape,
so the check died on the first bad file and never examined the other ~1,184
scenes. Hidden behind them were **55 scenes that parsed cleanly and serialised
back with different formatting**. Verified by comparing parsed trees: content
was identical in all 55 — no data was ever lost — but byte-exactness was gone,
and that is what diff-against-stock rests on. Two habits of Ascaron's
exporters did it:

- `<AABB ... />` versus `<AABB .../>` — a space before the self-closing slash;
- `CPostModVolume` nodes written one attribute per line with the `=` signs
  aligned, which a rebuilt tag collapses onto one line.

**The fix: the serialiser keeps each element's original tag spelling** and
re-emits it verbatim *while the element still says what it said when it was
read*. Edit an attribute and that one tag is rebuilt — a minimal diff on
exactly what changed. Measured on `PlayerShip.xml`: a material edit and a
shader edit each touch **2 lines of 8,135**, and an untouched scene is
byte-identical.

Result: **1,187 of 1,187 stock scenes round-trip byte-exactly, 0 parse errors**
(was 1,129 exact, 55 differing, 3 unreadable), and all **450** modder-authored
scenes in `Customization/` round-trip too. `SCN001` now reaches 9,806 pairs at
100%, having previously aborted before it could count them.

Three traps met along the way, all recorded in the code:

- expat's `ErrorByteIndex` points at the offending tag's **name**, not its `<`.
- expat's byte index for the end of an empty element is **not dependable** —
  it coincides with the start index for some and not others. Read
  self-closing off the tag text (`endswith("/>")`) instead.
- a captured tag must be stored with `\n` endings, because `serialise` applies
  the source's newline to the whole body at the end; a tag holding `\r\n`
  comes back out as `\r\r\n`. Start tags really do span lines here.

## Preview was broken for `.dds`, and the two bugs behind it

**Fixed 2026-08-16.** Reported as "preview does not work for dds files". It was
three separate defects that happened to land on the same feature, and the first
two are the interesting ones because both are *join* bugs — the right data
existed and the wrong key was used to reach it.

**1. The panel handed out references, not paths.** A scene names its textures
*relative to itself* — `textures/playership_body_00_col.dds` — and that string
is not a vpath. `Session.scene_detail` resolved each one only far enough to
produce a **boolean** and then threw the answer away, so `LinkedAssets` used
`DrawCall.textures` (the raw references) directly. Every action built on that
row read the VFS and failed with `VFS001`: Preview, Export, Open in its tab.
The mesh's `.3do` had carried a resolved `model_vpath` since Phase 5b; textures
simply never got the equivalent. They do now — `slots[i]["texture_vpaths"]`,
resolved once, where the VFS is open and the scene path is known, which is also
**the only place they can be resolved correctly**, because the reference is
relative to that scene and nothing else.

Why it looked intermittent: selecting a submesh went through
`Session.describe_assets`, which re-resolved the reference *without* the scene
and got away with it. Selecting nothing — the state every scene opens in —
went through the tree, which did not. So Preview worked or failed depending on
what was selected, which is the worst way to meet a path bug.

**2. Meshes were joined to draw calls by name, and names are not unique.**
`PlayerShip.xml` has **85 meshes under 28 distinct names**, because each of its
eleven body variants contains one called `main_`. `{m["name"]: m for m in …}`
keeps the *last*, so body_0's submesh was reading **body_10's** effects. Those
differ: the panel offered four texture slots for a submesh that has one, which
is how three textures survived the first fix.

The same wrong key was in `models_tab._effect_for`, and there it is worse than
cosmetic — the effect editor showed an arbitrary variant's shader, parameters
and material while `_node_path` still aimed the write at the submesh you
clicked, so **Apply copied another variant's material onto it**. The scene-graph
path is unique (85 of 85) and `DrawCall.node_path` has always carried it;
`session.mesh_for` is now the one place that join happens.

**3. Three stock textures are `A4R4G4B4` and were refused.**
`_decode_uncompressed` shifted each channel into place but never scaled it,
which is correct only while every mask is 8 bits wide — true of the 24- and
32-bpp formats it accepted, false of every packed 16-bpp one. So it refused all
16 bpp rather than decoding them wrong, which was the right call and left
`debris_a_nrm`, `hunter_g_0_nrm` and the `ObjectFieldScripts` debris normal map
unpreviewable. A channel is now as wide as its mask says (`v * 255 // max`,
which for a 4-bit channel is exactly the bit replication `_expand565` already
used, and the identity for an 8-bit one — so the files that worked before decode
byte-for-byte as they did).

**Result on real data: 3,052 of the 3,053 stock `.dds` preview.** The one
holdout is `noisel8_32x32x32.dds`, a 32×32×32 **volume** texture with
`DDPF_LUMINANCE` — a genuinely different thing, not a missing decoder, and it is
refused by name rather than guessed at. See the open findings.

Lesson worth keeping, since this project has now met it twice: **a raw reference
and a resolved path are different types, and storing them in the same field is
what lets one be used where the other is required.** The bool `resolved` was the
tell — something had already done the resolution and discarded the useful half.

## Variants and linked assets

**A scene is not one model.** `PlayerShip.xml` holds eleven bodies, eleven
wings and eleven boosters; `Container.xml` holds three containers. Drawing them
all is what made the viewport look broken.

`SceneGeometry.groups()` finds numbered sibling nodes and — this is the part
worth keeping — decides whether they are **alternatives or parts from the
geometry, not the names**. `body_0..body_10` occupy the same space, so they are
one-of. `CargoDock_0` and `CargoDock_1` sit side by side, so they are both-of.
`OVERLAP_THRESHOLD` is the dial. Default selection is the first alternative of
each group.

`linked_assets.LinkedAssets` is shared by both tabs on purpose: Models asks
"what does this submesh bind?" and Textures asks "what draws this page?", and
those are the same list with the same five actions. Written twice, the two
context menus would drift.

`effect_editor.EffectEditor` edits the shader path, the named parameters and
the 17-float material. It is editable because a corpus survey said so: the
parameters are a fixed named set across 1,006 scenes, and `<Material>` is
exactly 17 floats in all 16,036 effects (D3DMATERIAL9 shape). The row names are
labelled provisional on screen: the `<Material>` and `<Parameters>` semantics
live in the `.bsd9` blob, which is still undecoded even though the container
and its slot names are not.

**Queued, and thought through rather than forgotten:** glow objects, blinkers,
distortion objects and shield meshes (44 + 63 + 33 + 1 in PlayerShip alone)
should become toggleable viewport layers with their own editors — each carries
its own `EffectContainer`, and `scene.NON_MESH_EFFECT_OWNERS` already names the
types. Also queued: `.glb` import with `SCN001` checked before it lands,
`CLODSelector`, and a model-centric browse mode (which must show *bindings*,
not "a model's textures" — 28 of PlayerShip's 85 model references carry two
different texture sets).

## CI

`.github/workflows/ci.yml` runs on every pull request and on pushes to `main`:

- **lint** — `ruff check` (config in `pyproject.toml [tool.ruff]`) plus
  `tools/check_app.py`. The rule set is **bug-focused, not style-focused**
  (`F`, `E9`, `B`): this is format code whose layout is often load-bearing, and
  a reformatting linter would bury the findings that matter. Ruff is **pinned**,
  because an unpinned linter turns CI red on a PR that changed nothing.
- **test** — Ubuntu *and Windows*, Python 3.11 / 3.13. Installs the
  `image` extra deliberately: without Pillow and numpy the atlas and DDS tests
  skip themselves, and a rule that could not run must not look like a rule that
  passed. Runs pytest *and* `tools/offline_test_runner.py`, since `verify_all`
  falls back to the latter and it has its own bugs (it had one).
- **build (windows)** — a real PyInstaller build. `build.py` verifies what it
  built rather than the flags it passed, so a non-zero exit means the PE
  subsystem, the Qt platform plugin or the licence notice is wrong. The folder
  is uploaded as an artifact for 14 days.

Windows is in the matrix on purpose: every defect found on 2026-08-15 —
the Deploy handle bug and two test-tooling bugs — was invisible on Linux.

`.github/workflows/release.yml` builds and publishes on a `v*` tag. It
**refuses to publish if the tag disagrees with `dsotools.__version__`**, since
the executable's version resource is generated from that and a mismatch ships a
binary that misreports itself. `workflow_dispatch` runs the same build and
uploads the zip *without* creating a release, which is how to rehearse it.

`requires-python` said **3.8** for a long time, which is EOL and was not in the
CI matrix — so the oldest version the project claimed to support was the one
version nobody ever ran. It says **3.11** as of 2026-08-23.

It went to 3.9 first, and that was the wrong answer for the right reason. 3.9 is
the oldest version the code *works* on — the syntax is 3.8-clean, checked with
`ast.parse` at `feature_version=(3, 8)` across all 131 source files, and
`importlib.resources.files` (3.9) is the newest API used. But "it works" is not
the test. **3.9 went end-of-life in October 2025 and 3.10 goes in October
2026**, so both were already the same objection that had been raised against
3.8, only with a different number. 3.11 is the oldest that is still supported.

Not 3.13, though that is what the app ships and what everything is developed on.
The library is deliberately dependency-free so `import dsotools` works on a bare
interpreter, and distributions are still on 3.11 and 3.12; a floor of 3.13 would
exclude working setups to buy nothing. The frozen app is unaffected either way —
it embeds its own 3.13.

Ruff no longer carries its own `target-version`; it derives one from
`requires-python`, so the linter cannot drift from the packaging metadata the
way it had.

Bumping the floor surfaced **B905** — `zip()` without `strict=`, which bugbear
only raises at 3.10+ — in 27 places. It is ignored for now with the reasoning in
`pyproject.toml`: each site is a real choice between an assertion and a
restatement of current behaviour, and a version bump is the wrong commit to make
27 semantic decisions in.

## Open findings, not yet acted on

- ~~16 of Ascaron's own loose XML files are not well-formed.~~ **Closed
  2026-08-16.** Only 3 were malformed *scenes*; the other 13 are
  `MeteoridFieldDefinition` files the parser never sees. The parser now accepts
  a case-mismatched close tag and writes it back unchanged. See "Malformed XML,
  and the 55 files it was hiding" — which is also where the 55 formatting
  round-trip failures those three were concealing are written up.
- **One stock `.dds` is a volume texture and is not decoded.**
  `3DView/textures/noisel8_32x32x32.dds` — `DDPF_LUMINANCE`, 8 bpp, 32×32×32
  with `DDSD_DEPTH` set. It is the only one of 3,053 that does not preview.
  Luminance alone would be trivial; the *depth* is not, because the mip-chain
  sizes are computed as `w*h*bpp` and a volume's slices would be misread. A
  preview would also have to choose which slice it is showing. Refused by name
  rather than guessed at, which is the right default — but if the Models tab
  ever needs it (it is a noise texture, so probably a shader input), decide
  what "preview" means for a 3D texture first.
- ~~`.anim`: 57 of 1107 drawables disagree with their own second size copy.~~
  **Closed 2026-08-17, and the data was never wrong.** See "The `.anim` sizes".
- ~~`MDL001`–`MDL005` are specified and absent.~~ **Closed 2026-08-17**, and
  they became seven: `MDL006` (the model will not load at all) and `MDL007` (the
  root header disagrees with the LODs, which is the field `SCN001` trusts) both
  earned a place, while a candidate rule on the root table's *contents* was
  measured out of existence at four stock firings. See "The model rules".
- ~~Quitting during startup work crashes the worker.~~ **Fixed.** Closing the
  window during the initial `open_game` scan wrote a crash report:
  `RuntimeError: Signal source has been deleted`, cascading through
  `Worker.run`'s `except` *and* `finally` because every `emit` raised once Qt
  had destroyed the C++ side of `WorkerSignals`. Each emit is now guarded
  individually (`Worker._emit`); a destroyed source is a quiet no-op, and any
  other error still propagates.
- ~~`verify_all` reports PASS for skipped mod checks.~~ **Fixed.** `SKIPPED` is
  a third outcome, so a run with no `--mod` prints SKIP and summarises PARTIAL.

## The `.anim` sizes — the last red check, and it was ours

**Resolved 2026-08-17.** 57 of 1,107 drawables "disagreed with their own second
size copy" and had been carried as an open finding for weeks. Measuring it
first — as with the malformed XML — did not shrink the problem so much as
move it: **the data was never wrong. This project was.**

The two stored sizes are not one size written twice. Of the 441 drawables whose
source sprite is in a `.tex`, **404 have both pairs equal to the atlas
rectangle and 36 have only the second equal to it. Not one has only the
first.** So `0x010`/`0x014` is the size the interface **draws** it at, and
`0x1a0`/`0x1a4` is the size of the **source image** — the atlas rectangle.

They differ exactly where a drawable is a **stretched nine-slice frame**: 55 of
the 57 name a source ending in `TL`, the top-left corner tile, and the atlas
holds its `TC`/`TR`/`ML`/`MC`/… siblings alongside. `Background_blaugrau` draws
at 511×215 from a source that genuinely is 3×3.

**Two real bugs fell out of it**, both ours and both invisible until the fields
were told apart:

- `Anim.set_size` wrote **both** pairs. Rescaling a page containing a stretched
  frame would have replaced its drawn size with its corner tile's — a 511×215
  window becoming 3×3, silently, in the user's mod.
- `TEX004` compared the **drawn** size against the rectangle, so it reported 36
  of Ascaron's own drawables as errors that were never wrong.

`set_size` now writes only the drawn size, `set_source_size` writes the other,
`TEX004` compares the source size, and a rescale moves the drawn size *in
proportion* — which for an ordinary drawable is exactly the old behaviour and
for a frame keeps its geometry. `TEX007` is deleted: it asserted an invariant
that does not exist.

Third time this pattern has paid: **measure the finding before designing around
it.** "16 malformed files" was 3. "57 corrupt drawables" was 0.

## The model rules — `MDL001`–`MDL007`

**Closed 2026-08-17.** `docs/ARCHITECTURE.md` §6.1 had specified `MDL001`–`MDL005` since
rev 2 while `validate.py` shipped 16 codes and none of them. They exist for one
failure mode: a model round-tripped through a DCC tool, where the file still
parses, still round-trips byte-exactly, and the engine draws garbage. `SCN001`
catches the half of that visible from the scene; this is the other half.

**Every severity came out of a count, and the fourth time the method paid it
paid twice.** Before writing a rule, each candidate was run over the whole real
corpus:

| Candidate | Stock firings (3,110 models) | Result |
|---|---|---|
| vertex count > 65,535 | 0 | `MDL001`, error |
| submesh ranges vs. the buffers | 0, on every variant | `MDL002`, error / warning |
| index range, stride, index count | 0 | `MDL003`, error / warning |
| bounding box, exact comparison | **1,074** | rewritten as a relative tolerance |
| `.shd` LOD count (1,738 pairs) | 0 | `MDL005`, warning |
| root `submesh_total` vs. the LODs | 0 | `MDL007`, error |
| root table *contents* vs. the LODs | **4** | **not shipped** |

The bounding-box row is the one worth remembering. An exact comparison would
have flagged a third of the game; the worst disagreement across all 1,074 is
**1.19e-07 relative — one float32 ULP**, the original exporter's reduction
order (`threedo._bbox_f32` has always said so). The tolerance is relative to the
model's own size and set at 1e-3, four orders of magnitude clear of the noise
and far below anything a stale box would show. `MDL004` is a *warning*: the
engine culls against that box, so a stale one makes an object vanish at angles
where it should be visible.

**Two codes were added to the specified five.** `MDL006` — the model will not
load at all — because a check that could not run must never read as a check that
passed; `SCN003` and `TEX005` exist for the same reason, and without it a
truncated file would have produced *no findings*. `MDL007` — the root header's
submesh total disagreeing with the LODs — because **`SCN001` compares an
`EffectContainer` count against that field** and nothing verified it; a wrong
value there does not fail quietly, it makes the project's highest-value model
check validate against a lie.

**And one candidate was measured out of existence**, which is the same result
arriving the other way. The root `(submesh_index, lod_index)` table agrees with
the chunk structure in 3,107 of 3,110 models. The three that disagree ship in
the game: `baseshape`, `glow_hg_streak06_` and `polysurfaceshape72` each carry a
lone submesh numbered 1. A rule there would have flagged Ascaron's own files.

**Chasing that anomaly corrected the format.** The fourth odd file,
`turretrotxshapelod_6.3do`, has two LODs and a table entry naming LOD 2 — and
its ATTR trailer says the same, so the file is internally consistent and looks
like a three-LOD export with the middle level dropped. That is only visible if
the trailer's `+4` field is read as **two u16s**, `(index_within_lod,
lod_index)`, rather than the plain u32 the spec called it. It is: the high half
matches the record's own LOD position in **6,243 of 6,244** trailers.
`Submesh` still stores the raw value — `build()` writes those bytes back
verbatim, and the three stock oddities cannot be reproduced from the split
reading — with `index_in_lod` and `lod_index` as properties.

**`scan()`, not `parse()`.** `parse` refuses at the first defect and
materialises every vertex. A validator must do neither: it has to keep going
(a check that stops early reports the first cause as if it were the only one)
and it only needs the headers, the index buffer and LOD0's positions.
`threedo.scan` walks the same chunks and *returns* the disagreements, raising
only when the walk itself cannot continue — past that point every number it
would report is invented. The whole 3,110-model corpus scans in 8.8s.

**One implementation, three front ends.** `cli/dsvalidate.py` had its own copy
of most of these rules — its own bounding-box tolerance, its own gap check, no
stable codes. Two implementations of one rule is how a CLI and a GUI come to
disagree about whether a file is broken, so it calls `check_model` now and
keeps only what is genuinely its own (the round-trip check, NaN tangents,
`--compare`). The third front end is `Session.preflight_glb`: the rules run over
**the bytes an import would write**, beside the `SCN001` check that was already
there, because a DCC round-trip is the one place they realistically fire and
before the write is the only cheap moment.

**A real defect of ours fell out of it.** A truncated `.3do` made
`threedo.parse` raise `struct.error` — *not* a `DsoError`, and one exception
escaping that hierarchy is enough to throw away a whole validation report. Every
LOD chunk is bounds-checked before it is read now. It was found by a test
fixture, which is what fixtures assembled by hand rather than by the writer are
for.

## Look at the picture: `tools/drive_models_tab.py`

**The object model lies about the viewport, and only the viewport is the
product.** Four defects in a row proved it, and not one of them raised:

- appending a draw call to `calls` made `Repeater3D` rebuild every delegate,
  destroying the `QQuick3DGeometry` each `Model` held — the viewport went black
  while the model reported "4 of 7 shown";
- a `baseColor` binding silently did not apply, so the red marker rendered
  white among white markers, with no QML warning;
- the `<Material>` emissive row rendered `ComSat` as a white silhouette —
  detectable only as pixel statistics;
- matching draw calls by identity broke the instant one was rebuilt, and a
  whole layer stopped being drawn.

So there is a tool now. It opens a scene in the **real** `MainWindow` and the
real Models tab — not a mock — switches layers, lists what is drawn, and grabs a
frame with a brightness histogram.

```bash
python tools/drive_models_tab.py --game "<install>" --scene 3DView/ComSat.xml --layers geometry,blinker --shots out/
```

| Flag | Meaning |
|---|---|
| `--game` | the installation to open (required) |
| `--scene` | e.g. `3DView/ComSat.xml` (required) |
| `--layers` | comma-separated; `geometry,glow,distortion,shine,shield,blinker`. Default `geometry` |
| `--shots` | directory to write `frame.png` into. Omit for text only |
| `--wait` | seconds to allow the scene to load. Default 20; raise it for `PlayerShip` |
| `--isolate` | click that submesh's row in the parts table, then report and grab again. Added when isolate-by-row-index turned the viewport black |
| `--mod` | open a mod as well. Use a copy |
| `--reset` | "reset to stock" that vpath mid-run, the way the Project tab does, and report again. Added when a reset left the removed texture on screen |
| `--menu` | print the submesh row's context menu, built but not shown -- `exec` is modal, so this is the only way to see that it assembles |

What it prints, and why each line earns its place:

- **`N draw calls, M shown, layers present [...]`** — catches a layer that
  silently stopped being drawn.
- **one line per shown call** with layer, name, triangle count and **opacity** —
  catches the shield-too-translucent class of bug without a screenshot.
- **`QML errors:`** if `viewport.errors` is non-empty — QML binding failures do
  *not* raise into Python, so this is the only place they surface.
- **the readout line** — what the title bar claims about submesh and triangle
  counts, which is a *separate* claim from what is drawn, and was wrong on its
  own: it counted this tool's generated blinker markers as the scene's.
- **the brightness histogram** — `lit%`, `white%` and mean RGB. This is what
  caught the white `ComSat`: geometry present, texture invisible, everything
  reporting healthy. Black screen and white-out both look identical to the
  object model and completely different here.

**Three things to know before using it.**

1. **It needs a real platform plugin.** `QT_QPA_PLATFORM=offscreen` cannot render
   Qt Quick 3D at all — `grabFramebuffer()` returns a null image — so the tool
   deliberately `os.environ.pop`s it and opens a genuine window. It cannot run
   in a cloud session, and it cannot run headless on Windows either.
2. **The grab falls back three ways**: `quick.grabFramebuffer()` →
   `viewport.grab()` → nothing, reported honestly rather than as a blank pass.
   The first is the only one that sees the 3D content; if you only ever get the
   second, you are measuring the widget frame, not the scene.
3. **It reaches into private state** — `tab._geometry`, `tab._layers`,
   `tab._apply_visibility()`. That is intentional: it is a test driver, not a
   consumer, and keeping it honest to the real widget matters more than
   respecting the underscore. If you rename those, this breaks loudly, which is
   the correct outcome.

Reach for it **before** reasoning about anything visual. Every one of the four
defects above cost an hour of arguing with the object model first.

## Three from real use, 2026-08-17 — and two of them were one bug

Reported by the user after working in the app. All three were reproduced before
anything was theorised about them, which is why this section is short.

**1. A model could only ever be exported as `.3do`.** `Export…` opened a file
dialog with *no filters at all* and called `Session.export_image`, whose rule is
"images re-encode, everything else copies the original bytes". So the glTF
exporter — the thing `3do2gltf.py` has always had, and the reason to export a
model in the first place — was reachable from the command line and not from the
app. `Session.export_asset` now routes on the destination extension: `.glb`
converts, images re-encode, anything else copies bytes untouched. The dialog
offers glTF **first** for a model and unchanged-bytes second, and lives in one
module-level function that both the Project tab and the linked-assets panel
call, because two copies of a file dialog are two sets of filters that drift.
Verified as a round trip rather than as "a file appeared": `.3do` → `.glb` →
`.3do` is byte-identical. `.gltf` is refused by name — this writer produces the
single-file binary form, and a GLB under a `.gltf` name is a trap.

**2. Isolating a submesh made the viewport go black.** Reported as "selecting
many submeshes causes the preview to be completely black; nothing is shown for
e.g. wing_00". It is **the same wrong key as `session.mesh_for`**, one method
along: `models_tab` passed `indexOfTopLevelItem` — a row number in the *parts
table* — to `viewport.set_isolated`, which indexed its **own** list of calls.
The two are not the same sequence. On `PlayerShip` the table has 10 rows and the
viewport holds 254 calls, so:

| table row | what the user clicked | what got isolated |
|---|---|---|
| 0–2 | `main_[0]`, `main_[1]`, `wdw_[0]` | the same call, by luck |
| 3 | `backwing_l_[0]` | `main_[0]` |
| 7 | `wing_00_[0]` | `main_[1]` — of a body variant the selector has **hidden** |

A hidden call cannot be shown by isolating it, so everything ended up hidden and
the viewport went black with nothing reporting a problem. That the first three
rows worked is what made it look intermittent. `set_isolated` takes the draw
call now and matches on identity. The same reload path was restoring the
selection **by name**; measured over 200 stock scenes, 4 have two *visible* rows
sharing one name (`Cruiser_G_0` has eight), so that is keyed by
`(node_path, lod, index)` — unique in all 200.

**3. Blinker markers were listed as submeshes and counted as triangles.** The
parts table and the title were built from `visible_calls`, which is "what is on
screen" — and the markers *are* on screen. But a blinker group is a point-sprite
emitter with no model and no submesh; the spheres are this project's own
geometry, drawn so the lights can be placed. So the table had seven rows with no
shader, no textures and nothing to edit, and ticking the `blinker` layer added
**2,256 triangles to PlayerShip's 4,440** — a scene total that moved when you
ticked a checkbox. `meshview.MARKER_LAYERS` names the rule where the layers
live, `visible_calls(..., markers=False)` and `triangle_count()` apply it, and
the viewport keeps drawing them. The rule already existed in
`viewport.apply_visibility`, which exempts markers from the variant filter; it
was in one place and needed to be in two, which is exactly when it belongs in
the library.

`drive_models_tab.py` grew `--isolate` and a readout line for this round, so
both the second and third bugs are now things the driver can *see*.

## The stale picture after "Reset to stock"

Reported next, and it turned out to be **two tabs and three defects**.

**A refresh that cleared the state and left the picture.** Anything changing
what a path resolves to emits `"mod"`, and both tabs handle it. The Models tab
dropped `_geometry`, cleared the tables and reloaded the scene *list* -- and the
tree selection is restored with signals blocked, deliberately, so that
repopulating cannot jump to another scene. Nothing was left to reopen the scene.
The tab ended up holding **no geometry at all** while the viewport still
displayed the frame it had already uploaded, so a texture reset to stock stayed
on screen exactly as it was. Measured with `drive_models_tab --mod --reset`:
before, `the tab holds NO geometry (scene=3DView/ComSat.xml)` with the frame's
brightness unchanged to the digit; after, 6 draw calls and a visibly different
hull. `refresh()` now reopens the scene once the browser rows are back -- rows
first, because only then is it known whether the scene still exists -- keeping
the camera and the selected submesh, which is what the old comment was
protecting.

**The Textures tab had the same bug, and it matters more there** -- the whole
point of that tab is looking at an image. `refresh()` cleared `page`, left
`_preview` and the last pixmap alone, and `PageView` had no way to show
*nothing*: the picture stayed until something replaced it. It now clears and
reopens through `_pending_reveal`, the path the cross-tab jump already uses.

**And fixing that exposed a race the tab had all along.** The first version of
the fix reopened the wrong file entirely -- `04_dirtseg.dds` instead of the
`comsat_col.dds` it asked for. Two causes, both real:

- `_apply_filter` rebuilt the row list **without blocking signals**, so
  repopulating changed the selection and opened whatever landed in row 0. The
  Models tab had already learned this one; the Textures tab had not.
- decoding runs on a worker and nothing checked that a result was still wanted,
  so of two overlapping loads the *slower* one landed last and won. `_wanted`
  records what the user last asked for and a superseded result is dropped. It
  needed a reset-to-stock to surface, but two quick clicks on a 1024x1024 DDS
  would have done it.

Verified in the running app, because none of this raises: the Textures tab's
pixmap goes 1024x1024 (the mod's override) to 512x1024 (stock) with the title
naming the file it actually shows.

## Which DXT format to save a texture as

Asked after a real edit: DXT1 "looks really weird", DXT3 "adds some kind of
pattern", DXT5 looks best. All three observations are correct, and the corpus
says why. Counted over the 3,053 stock `.dds`:

| Format | Count | Share |
|---|---|---|
| DXT5 | 1,973 | 64.6% |
| DXT1 | 683 | 22.4% |
| uncompressed (RGB32/24/16) | 396 | 13.0% |
| **DXT3** | **0** | — |

By filename suffix: `_col` is 624 DXT5 / 11 DXT1, `_flat` is 134 DXT1 / 5 DXT5,
and **`_nrm` is 406 DXT5 and 21 uncompressed — not one DXT1 and not one DXT3.**
2,891 of 3,053 ship mipmaps.

**The reason is the DXT5nm swizzle**, which this project measured long ago: a
`_nrm` texture keeps **X in the alpha channel**, Y in green, Z reconstructed
(read as plain RGB the vectors have median length 0.345; unswizzled, 0.994). So
alpha is not decoration, it is half the normal:

- **DXT1 has no alpha channel at all**, so it discards X outright. The lighting
  is then computed from a vector missing its first component — which is exactly
  "really weird" rather than merely "worse".
- **DXT3's alpha is 4-bit and explicit**, not interpolated, so X survives in 16
  steps. Banding in a channel that should be a smooth gradient is the "pattern".
- **DXT5's alpha is interpolated**, which is why it is the only one that looks
  right, and why Ascaron used it for every compressed normal map they shipped.

The Replace… notice now says all of this **and leads with what the file being
replaced actually is** — `Session.texture_format` reads the DDS header (no
decode) so the dialog can say "This texture is 1024x1024 DXT5, 11 mip(s)".
A rule the reader cannot check is advice; the file's own format is an answer.
The `replace_asset` refusal carries the short version, so the two cannot drift.

Its "do not show this again" key changed (`replace_dds` -> `replace_dds_format`),
so anyone who silenced the old wording sees the new one once. Silencing a
sentence is not silencing the page that replaced it.

**And the format does not live in that dialog.** A fact this load-bearing must
not be reachable only through something the user can switch off for good, so it
is on screen in four places that cannot be dismissed:

- a **Format column** in the linked-assets panel, in both its views -- the
  selected submesh's bindings and the whole-scene tree;
- the **file picker's own title**: *"Replace playership_body_00_nrm.dds — the
  original is 1024x1024 DXT5, 11 mip(s)"*, on screen at the moment the
  replacement is chosen;
- the **texture submenu labels** on the parts table, and a tooltip on its Base
  colour and Normal columns;
- the Textures tab's preview title, which already said it.

The column tooltip carries the rule (DXT5 unless it says otherwise, never DXT3,
keep the mipmaps) so the advice travels with the fact.

Reading a DDS header is ~4 ms and a scene binds up to a hundred distinct
textures, so the formats are read **on the worker that loads the scene** and
cached in `Session` -- a fifth of a second where nobody is waiting, rather than
a stall on the GUI thread. The cache is cleared with the preview cache whenever
the bytes behind a path can have changed, because a stale format line is worse
than none: it is the one number a replacement is copied from.

## Stale state when switching screens

Reported after using the toggles: frames switched off came back on the next
screen, and a selection carried over. Both reproduced first, and the second was
worse than described -- after a switch the canvas outlined element 0 in orange
while the table showed **no selection at all**.

Three defects, all of them the same shape: state that belongs to the *canvas*
being rebuilt from the *screen*.

- **`show_screen` built every item with a solid pen** and nothing re-applied the
  toggles afterwards, so new items ignored a switch that was still off. It ends
  by applying the current visibility now.
- **`clear_screen` did not reset the selection.** An index means nothing across
  screens -- element 1 of one layout is not element 1 of the next -- so a
  surviving selection highlights an unrelated rectangle.
- **Rebuilding the table emitted a selection change**, and `_row_selected` had
  no `_loading` guard, so refilling the list selected element 0 of the new
  screen and outlined it. Its sibling `_cell_changed` already had that guard;
  this one was written without it.

The third is why the canvas and the table disagreed, and it is the kind of thing
only a driver catches: nothing raised, and both widgets were individually
consistent.

**`tools/drive_interface_tab.py`** is that driver, and it exists for the same
reason `drive_models_tab.py` does. `--then SCREEN` switches after loading the
first one, which is exactly the sequence that produced two of these three; the
report names how many elements are outlined, how many labels are visible, how
much artwork is loaded, and whether the canvas and the table agree about the
selection.

## Phase 6b — the Interface tab

**Built 2026-08-17**, straight after the `.screen` decode that unblocked it.

**The canvas is the tab.** A `.screen` is 1,381 rectangles across 83 files, and
a rectangle is not something anyone reads as four numbers — the useful view is
*where the button sits* on the 1024×768 the file was authored for. So the layout
is drawn to scale, click a rectangle to select its row, and type into the row to
move it by one pixel. Both directions are wired: canvas → table and table →
canvas, with the selected element in orange.

**The chain is resolved before anything is shown**: element → `scripts/X.anim` →
`images/Y.aim` → atlas page + rectangle, which reaches real pixels for **1,433
of 1,433** references. The selected element's artwork is decoded and shown
beside the table — the pixels the game actually draws, cropped out of the page,
not the `images/*.aim` of the same name, which is the packer's leftover source.

**A move rewrites four integers.** Measured through the app on the largest
layout: nudging a button ten pixels wrote `PLASMATREE_1024x768.screen` with
**1 byte of 220,648 different**. That is the whole point of the verbatim
element bytes in the parser.

**Dragging, and the bug under it.** Elements are moved by click-and-hold on the
canvas, rounded to whole layout pixels because the file stores integers and a
drag reporting 184.6 would be a number nobody could type back. Reported first
was that the *label* stayed behind when the coordinates changed: the rectangle
carried its position inside its own `rect` and the label was a child positioned
separately, so `setRect` moved one and not the other. The item's **position**
carries where it is now and the rect only its size, so everything parented to it
follows. A drag and a typed edit go through the same `_edits` dict and update
the same row, so Save, Revert and the blue "changed" marks cannot disagree with
the picture.

**The element table has the drawable's context menu.** The element itself is
geometry -- moving it is the canvas and the table -- but everything else an
author wants is about what it *draws*, and that is an asset like any other:
Preview what it draws, **Open the sprite in Textures** (which jumps to the page
*and selects the sprite*, rather than landing on 331 of them with no
selection), then Export / Replace / Reset to stock / What uses this on the
`.anim` itself. All the shared actions, aimed one link down the chain, and every
disabled entry says why.

**Three toggles under the canvas, and a real preview.** Reported as "dragging
works but you cannot see what you drag": 60 outlined boxes hide the thing being
moved. So **Frames** (off leaves only the *selected* element outlined -- hiding
that one too would answer "what am I dragging?" with "nothing"), **Labels**, and
**Artwork**.

Artwork draws what each element actually draws, and the measurement is what made
it worth building. Of the 694 elements that draw something across the 83 stock
screens:

| | count | how it is drawn |
|---|---|---|
| sprite at its own size | 399 | cropped and placed — exact |
| nine-slice frame | 135 | **composed from its nine tiles** |
| stretched, no tile family | 160 | scaled, and labelled approximate |

The middle row is the one that matters. A frame's drawable names only its
**top-left tile** (`ND_ListBoxWithBGTL`) while the atlas holds its `TC TR ML MC
MR BL BC BR` siblings beside it — all nine present for **135 of 135** — so
stretching that corner is what makes a preview a smear: `Background_blaugrau` is
a 3×3 tile drawn at 511×215. `dsotools.edit.nineslice` composes it properly:
corners at their own size, edges stretched along one axis, centre along both.

Two details from measuring rather than assuming:

- **The trailing digits are part of the convention.** `ND_ScreenFrameTL1` and
  `ND_ScreenFrame_02TL1` are real families; requiring the suffix to be last cost
  12 of the 135.
- **The status line says which of the three each element got** — "5 exact, 3
  composed from tiles, 2 stretched (approximate)". A stretched sprite is an
  approximation of what the game draws, and a preview that will not admit that
  is worse than a box.

Pages are opened **once each** rather than once per element: a screen binds
three or four 1024×1024 atlas pages, so the naive loop is sixty decodes. The
whole thing runs on a worker and is loaded only when the toggle is switched on.

Three decisions worth keeping:

- ~~It draws rectangles, not artwork.~~ **Superseded**: the nine-slice
  composition that made this hard turned out to be nine crops and eight
  resizes, with every tile present in 135 of 135 frames, so the artwork view is
  honest for 534 of the 694 drawn elements and says "approximate" for the
  other 160.
- **Labels only where the label fits** (`w ≥ 40 and h ≥ 11`, truncated to the
  box). PLASMATREE has 60 overlapping elements; 60 stacked labels is worse than
  none, and the tooltip carries the name everywhere.
- **The declared-vs-actual child count is shown, not hidden.** When a screen
  says 5 children and the file holds 9, the tab says so in amber. That
  difference *is* the nesting, and it is now worked out — see below.

`.screen` routes to this tab, so "Open in its tab" works from Problems and the
Project tree, and the file list carries the same in-mod marker and context menu
as everywhere else.

## The element tree, and three bugs that were all the same bug

Reported together: `STATUSLEISTE` had elements off the canvas, `MOD_MANAGER` was
"missing most of its elements", buttons all declared they drew a *disabled*
button, and `MAINMENU`'s `Static_Submenu` elements animate in the game.

The first two were one cause. **A child's rectangle is an offset from its
parent**, and the tab drew every rectangle flat, so `MOD_slider_Button Drag`
(stored at `(2, 48)`, belonging on a slider at `(444, 59)`) landed in the corner
and `MOD_slider_Background` (`y = -1`) fell off the top. `STATUSLEISTE` was the
mirror image: its screen box is `(300, 0, 800, 150)` and the canvas *subtracted*
that origin, though the elements are laid out inside the box — 1,349 elements
fit that way against 1,319 read as absolute.

**Finding the tree took ruling the field out first.** A child count must read 0
for every element of a flat screen; the 948 elements of the 64 flat screens
leave 551 candidate offsets in the 720-byte common part of an element, and
**none** reads 4 at either slider that owns four children. Nor does any offset
mark a *child*. So it is not stored, and `dsotools/edit/screentree.py` derives
it from what the engine's widgets build — a slider owns the four sub-controls
that follow it, a list box its row templates and its slider.

Two things keep that from being a guess:

| check | result |
|---|---|
| derived top-level count vs. the header's, which it is never given | **83/83** screens |
| derived children sitting on their parent, resolved relatively | **105/133**, mean overlap 77% |
| the same children read flat | **11/133**, mean overlap 7% |

Getting there needed three wrong rules first, and each failure named the next:
matching any shared name prefix gave 66/83 (`WH_Background2` is not part of
`WH_Background`); requiring a separator gave 73/83 (LOGBUCH cells are named
`...(Col:0 Row:0)`); a fixed vocabulary of suffixes gave 79/83 (one screen
abbreviates to `_vsl_bdr`, another's cell name is mangled). Only the class
pattern — *which* records follow *which* widget — covers all 83.

**In the tab:** children are nested under their parent in the table, italic and
grey, dashed on the canvas, and **not editable or draggable**;
`set_screen_rects` refuses them too, because the engine recomputes their
placement and a typed coordinate would not hold in the game.

**Buttons.** A `CButton` names one drawable per state in the order `disabled,
normal, pressed, highlight, [blink]` — slot 0 carries a `*_disabled`-style name
127 times and never a `*_normal` one. The tab read slot 0, which greyed out 125
of the 694 elements that draw. `screen.resting_index()` picks the resting state,
and the three-reference buttons (`_nr`, `_pr`, `_hl`) start at normal.

**The animated submenus.** There is nothing to find: all **1,107** `.anim` files
in the game report `frames = 1`. The growth is the engine resizing the element,
and all three `Static_Submenu` drawables are nine-slice frames precisely so
resizing looks right (`MainWindow_framed` draws a 59×5 source at 140×74). The
rectangle in the file is the authored size, not a bound the engine keeps to.

## `.screen` — decoded

**2026-08-17.** The last undecoded format in the interface chain, and the one
that gated the Interface tab. 83 files, 1,381 elements.

It is the same `A2DFILE` container as `.tex` and `.anim`: a 28-byte header, then
one element per control, **flat rather than nested**. Each element is
`SH_DWFAB` (416 bytes: class name at +0x10, element name at +0x50) then `SH_DWB`
(304: `x, y, w, h` as signed int32 at +0x0c), then zero or more length-prefixed
blocks of class-specific data. The screen itself is the first element and adds
an `SH_SCRN` record plus a child count.

**Validated by two methods that agree, not by "it parsed":**

- The **structural walk never searches for a tag**, and it finds exactly the
  same element offsets as scanning for `SH_DWFAB`, landing on the final byte, in
  **83 of 83** files.
- Every resource the class blocks name **resolves**: `scripts\*.anim`
  1,433/1,433, `fonts\*.res` 470/470, `sfx\*.res` 342/342, `staticImages\` 2/2
  — **2,247 of 2,247**. Four more strings look like references and are
  Ascaron's own typo (`\Pr2_Slider_DragBtn_nr.anim`, no folder, leading
  backslash), reported rather than repaired.
- **83 of 83 round-trip byte-identically**, and a file truncated at any of 60
  points is refused rather than read as a short one — which it was, until the
  test for it went in: the record sizes were trusted without checking they fit.

The rectangle reading is corroborated the same way: 1,260 of 1,381 elements fit
strictly inside the 1024×768 the filename declares, the exceptions being
deliberate (letterbox bars at `y = -1`, a background at `(-3,-3)`).

**Editing is in, and bounded.** Moving an element rewrites only its rectangle
(measured: one byte for a 15-pixel move); a drawable can be repointed within its
own fixed-width field, and a replacement too long for that field is refused
rather than allowed to overrun a block whose layout is not decoded.

**The nesting was not claimed here, and then it was worked out.** As decoded,
the parser exposed a flat list: the screen's declared count matches the element
count in only **64 of 83** files, and no field says who owns the rest. That is
still true of the *file* — see "The element tree" above for the search that
ruled it out — but the tree itself is now derived in
`dsotools/edit/screentree.py` and checked against the count the header does
give, on 83 of 83 files. `dsotools.formats.screen` still exposes the flat list
it can prove; the derivation lives next door, and says so.

**The Interface tab is no longer blocked.** The chain `.screen → .anim →
images\X.aim → page + rectangle` is now readable end to end, and every link in
it is editable.

## The selected row, and why it was unreadable

A long-standing complaint, finally given a number. Every list in this app codes
meaning into the **colour of the text** -- blue for "your mod supplies this",
amber for "inferred rather than read", red for "missing" -- and the platform's
selected-row style is the system accent with white text. So selecting a row
threw the coding away, and whatever survived sat on saturated blue.

Measured in WCAG contrast, on the Windows accent `#0078d4`:

| | on `#0078d4` (was) | on `#d7e9f7` (now) |
|---|---|---|
| normal text | 3.8 | **14.0** |
| "from the mod" blue | **1.1** | 3.5 |
| inferred amber | **1.4** | 3.6 |
| missing red | **1.2** | 4.4 |

1.1:1 is invisible, not merely poor. That is what "the blue makes the text hard
to read" was.

`theme.py` is the fix and the only place the app styles Qt:

- **A soft selection, and the item keeps its own colour.** Setting only
  `background-color` would leave Qt painting the text with `HighlightedText`
  (white), which is the discarding half of the bug; the rules set
  `color: palette(text)` as well, so an uncoloured row uses normal text while a
  row that set its own foreground keeps it -- the model's ForegroundRole wins.
- **Hover is a separate, lighter wash.** "Under the pointer" and "chosen" are
  different facts, and a list that paints them the same makes people click to
  find out which is which.
- **Light and dark are told apart by the palette's own lightness**, not by
  asking the OS, so a forced dark palette or a Qt style change is picked up the
  same way. Both were photographed.
- The **amber was darkened** from `#b8860b` to `#9a6f09`: it measured 3.3 even
  on plain white, which was already marginal, and 2.6 on the new selection.

`tests/test_theme.py` asserts the *rule*, not the strings: every coded colour
must clear 3.0 against every selection and hover background, normal text must
clear 7.0, hover and selection must differ, and the selected rules must keep
`palette(text)`. The contrast maths is reimplemented in the test rather than
imported, and checked against the published examples first. A future tidy-up
that reintroduces a pretty accent will fail it.

**And the Data tab's key and comment columns are properly read-only now.** They
were editable and silently reverted afterwards, which is a worse answer than
refusing: the user types, watches it undo itself, and cannot tell whether the
tool is broken or the edit is disallowed. A `QStyledItemDelegate` returns no
editor for those columns, so no cursor ever appears, and both carry a tooltip
saying why.

## Phase 6a — the Data tab

**Built 2026-08-17**, and it is the first piece of Phase 6.

`inifiles/` is the largest genuinely moddable surface in the game and the only
one that needed no reverse engineering: **68 files, 13,091 sections, 120,034
entries** the engine reads as plain text -- ship stats, weapon damage, planet
types, prices.

**The unit is the section, not a row in a grid, and that was a measurement.**
Only **27 of the 68** files are uniform enough to be a table: `Planets.ini` is
5,970 sections sharing one key set 99% of the time, while the cluster files mix
object types and share a key set in 40-55% of their sections. A grid would have
worked beautifully for a third of the data and misrepresented the rest, so the
editor is "pick a file, filter the sections, edit the keys" -- true of all 68.

- `Session.ini_files` / `open_ini` / `set_ini_values` hold the work, so it is
  tested rather than clicked: `Planets.ini`'s 5,970 sections parse in 0.11s.
- **It edits values and refuses to invent schema.** No adding or deleting keys
  and sections: nothing writes down which keys the engine reads, so a key this
  tool made up is one nobody can say has any effect, and the failure mode is
  silence. `set_ini_values` raises on an unknown section or key rather than
  creating it.
- **One changed value rewrites one line.** Measured end to end through the app:
  editing a ship's stat wrote a file where **1 line of 1,933 differs**, comment
  and spacing intact. That is what keeps diff-against-stock meaningful on a 3 MB
  table.
- Duplicate sections and duplicate keys are **reported, not resolved** -- the
  engine tolerates them and which one wins is not established, so nothing here
  quietly picks for the author.
- The file list carries the same in-mod marker and source column as everywhere
  else, and the same context menu: Export, Replace, Reset to stock, What uses
  this. `.ini` routes to this tab, so "Open in its tab" works from Problems and
  the Project tree.

**And it found a defect in the INI parser** -- one that affects **119,006 of the
120,034 entries**, because that is how many carry a trailing comment. The gap
before the `;` was being kept on the *value*: `Hitpoints = 1200 ; Trefferpunkte`
read back as `"1200 "`. Two costs, one of them silent: every reader had to
`strip()` (and `as_float` did while `value` did not, so the two disagreed), and
**setting a new value dropped the space**, rewriting the line as
`1200; Trefferpunkte` -- turning a one-number edit into a formatting change in
the diff. The gap belongs to the comment now. All 68 stock files still round-trip
byte-identically.

Still outstanding in Phase 6: **Scripting** (blocked on the CHM → JSON
extraction) and **Interface** (blocked on `.screen`).

## Opening a scene: what it costs, and what it looks like while it costs it

Reported together: a variant change had become "a few seconds" and opening
PlayerShip felt like 5-10, with the tab showing the previous scene throughout.

**The variant regression was mine, and it was 3.3 seconds.** The blinker picker
asks "is this group reachable?" of all 63 groups on every variant change, and
`reachable()` re-derived the scene's whole variant grouping *per call* --
`groups()` is 51 ms, which is nothing once and 3.3 seconds sixty-three times.
Two fixes, both worth having on their own: `groups()` is **computed once per
scene** (it reads only `calls`, which never changes after construction), and
`reachable_filter(selection)` returns a predicate with the rejected set resolved
once. **3,260 ms → 0.2 ms.**

**Decoded textures are now kept between scene builds.** They were cached per
build, and the tab rebuilds constantly: every LOD change, save, replace and
reset reopens the scene, and decoding PlayerShip's 69 distinct textures is most
of what that costs.

| | before | after |
|---|---|---|
| open PlayerShip | 1.58s | 1.58s (nothing is cached yet) |
| reopen it — what every save does | 1.58s | **0.41s** |
| change LOD | 1.58s | **0.39s** |

The cache lives on `Session`, is bounded at 192 MB with the oldest dropped
first, and is **cleared on the same signal as the preview cache** -- serving the
old pixels after a texture replace is exactly the "my edit did nothing" trap
this project keeps meeting.

**And the tab now says what it is doing.** Three changes, all of them the
difference between "working" and "hung":

- **Switching scenes unloads the old one first** -- viewport, tables, linked
  assets, blinker pane, variant combos. A half-cleared tab is worse than an
  empty one: the viewport kept the previous ship while the tables described it.
  Reloading the *same* scene deliberately does not unload, or every Apply would
  blank the viewport and read as the tab resetting itself.
- **The title animates and counts**: `building geometry 134/163`, then
  `reading materials and textures`, then `describing 189 assets`. The counter
  is `build_scene_geometry`'s own progress callback, which existed and was
  unused; the phases after it report by name, because a bar that reaches
  162/163 and then sits there says the opposite of what is happening.
- A **spinner** beside it, so a phase with no count still visibly moves.

Worth being honest about what was *not* fixed: the first open of a big scene is
still ~1.6s warm, and more on a cold file cache -- 254 draw calls, 69 textures
and 189 assets is real work. What changed is that every subsequent open is
quarter the price and the wait no longer looks like a freeze.

## Blinker groups now say which part they belong to

The last item the user had recorded as a TODO. `PlayerShip` has **63 blinker
groups under 5 distinct names** -- `blinks_0` alone 33 times, because every
body, wing and booster variant carries its own -- so the picker listed 63
entries nothing could tell apart.

**The label is built from the node path**, which is unique by construction:
`bodys|body_3|blinks_0` becomes `body_3 · blinks_0`, and
`wings|wing_0|backwing_l|blinks_3` becomes `wing_0 / backwing_l · blinks_3`.
The top container is dropped because the variant already names it and `body_3`
is not ambiguous with `booster_3` -- **unless dropping it would make two labels
identical**, in which case those keep their full path. A shortened label that no
longer identifies the thing is the same "names are not keys" trap one level up,
and this project has now met it four times.

**And the picker only offers what the variant selection can reach**: 63 entries
become **7**. The other 56 hang off a body, wing or booster that is not
selected, so their lights are not on screen and editing them shows nothing --
which is exactly the complaint that produced the variant-aware combo row in the
first place. `SceneGeometry.rejected_paths` / `reachable` are the same rule
`visible_calls` and `reachable_groups` were already applying separately; they
are one implementation now, in the library, with a test.

Two details worth keeping:

- **Re-filtered, not reloaded.** Changing a variant calls `set_reachable`,
  which rebuilds the combo from the groups already in memory. Reloading would
  re-parse an 8,000-line scene on the GUI thread to redraw a combo box.
- **The open group survives a re-filter** where it can. Changing the booster
  must not throw away the body blinker you were editing; when its own variant is
  switched away it is gone, and the first remaining group is selected, which is
  the only honest answer.

## verify_all was two minutes; it is 47 seconds

Reported as "the model checks made it slow". Half right, and measuring first
said which half: of the ~140s run, **86s was the `.3do` round-trip** -- which
predates this work -- and 6.6s was the new `MDL` check. The fixes are four, and
three of them are in the *library*, so the app got them too.

| | before | after |
|---|---|---|
| `.3do` parse → build | 86s | **11s** |
| reference resolution / index build | 18s | 14s |
| `SCN001` | 11.5s | 4.7s |
| scene XML round-trip | 9.7s | 3.8s |
| `MDL001`–`MDL007` | 6.6s | 2.0s |
| **whole run, with tests** | **~140s** | **47s** |

**1. The vertex loop was ten million struct calls.** `parse` unpacked every
element of every vertex separately -- 2.5 million vertices times four elements
-- and `build` packed them back the same way. But **3,106 of the 3,110 stock
models** have a vertex that is nothing but packed floats tiling the stride, so
the whole buffer is *one* `struct` call and a slice per vertex. The other four
carry a `D3DCOLOR`, which is not a float and is kept as a raw dword; they take
the general path, unchanged. Parse 47s → 27s, build 36s → 16s, and the corpus
still round-trips 3,110/3,110 byte-exactly -- which is the only test that
matters for a change like this.

**2. `scene._scan_tag_end` walked one byte at a time.** It was the hottest
function in the whole project: 592k calls while indexing the installation, 28.8
million `len()` evaluations, and a one-byte `bytes` object allocated per
character. It jumps between quotes and `>` with a compiled regex now. Same rule,
same result, 1,187/1,187 scenes still byte-exact -- and every scene the app
opens is quicker for it.

**3. `SCN001` read whole models to look at four bytes.** `e.read()[:0x1000]`
reads the *entire* `.3do` -- up to 4 MB -- and it ran once per **reference**:
9,806 references over 2,762 distinct models, so 1.7 GB decompressed to read a
u32 at 0x30. Memoised per model. The same pattern was in `validate.py`'s
`submesh_total`, where a mod with many scenes paid it too.

**4. The two per-model checks run across processes.** Independent work per file,
a tiny answer per file, and pure-Python parsing that holds the GIL -- so threads
buy nothing and processes buy 4x. `--serial` forces one process, because a pool
swallows tracebacks and that is the first thing to want when a corpus check
misbehaves. **The pool is an optimisation and never a weakening**: if it cannot
start, the check runs serially rather than skipping, and nothing in it decides
*whether* a file is examined.

**What was deliberately not done: gating the model checks behind a flag.** It
was the suggestion, and it is the one change here that would have traded honesty
for time -- a corpus check that usually does not run is a corpus check whose
green means less each time you see it, and `PARTIAL` stops meaning anything once
it is the normal outcome. At 47 seconds the full run is cheap enough not to
need the trade; `--only SUBSTR` already exists for a focused loop.

## The panel had the facts and did not show them

Reported after replacing a texture: no sign that the mod now supplied it, and
the Resolves and Source columns blank.

**The panel has two views and only one of them knew anything.** With a submesh
selected the rows come from `Session.describe_assets`, which resolves each
reference and marks what the mod owns. With *nothing* selected -- the state the
screenshot showed -- the tree is built from the parsed scene, and it knew only
whether a reference resolves. Everything else was an empty string. So a texture
the mod had just replaced looked exactly like one it had not.

`Session.asset_info(refs)` is now the one place those facts come from -- where
it resolves, whether the mod owns it, its format, and whether it can be reset --
and `describe_assets` is a thin mapping of roles onto it. A test asserts the two
cannot disagree, because they already did once.

Three smaller decisions inside it:

- **"from the mod" in words, not only in colour.** A blue path says nothing to
  a reader who does not know the convention, and nothing at all to a colour-blind
  one. The exact archive is on the tooltip; the cell says the thing that matters.
- **The "Resolves" column is gone.** It could only ever say MISSING, and a file
  that does not resolve has no source either -- so the two are one column. Five
  columns did not fit the pane, and the one that got clipped was Source.
- **`can_reset_to_stock` grew a `mod_files=` parameter.** It calls `Mod.files()`,
  which walks the folder *and opens the archive*; asking it about a scene's
  hundred bindings would have done that a hundred times per panel fill.

**Reset to stock is offered wherever an asset is**, not only in the Project
tab's file list: the linked-assets panel, and the Models tab's submesh menu for
the model and each texture. Disabled with the reason where it cannot apply --
"This mod does not contain that file", or the `items.ini` refusal. The
confirmation is `reset_asset_to_stock`, module-level, so the third caller could
not invent a third wording for a deletion.

## Blinkers are not submeshes, in the panel either

Same rule, third place. `show_all_meshes` listed blinker groups as branches with
a dash in every column, because it iterated `visible_calls` and the markers are
drawn. It passes `markers=False` now, like the parts table.

**And the blinker pane gained "Replace texture…"**, which is the other half of
the same point: a group's sheet is an asset like any other, but it is reached
from a pane that is not the panel, so removing it from the panel would have left
it named in one place and replaceable in none. `Session.blinker_groups` now
returns `texture_vpath` beside the scene's own `texture` reference -- **two
fields because they are two types**, and handing a raw scene-relative reference
to anything that reads the VFS is the defect this project has now met three
times. The pane shows the resolved path, the format and whether the mod supplies
it, and the button is disabled with a reason when the texture does not resolve
or no mod is open.

`replace_asset_dialog` joined `show_users`, `export_asset` and
`reset_asset_to_stock` as a module-level action for the same reason each of them
did: it now has three callers, and the DDS advice, the filters and the `.glb`
pre-import check must be identical in all three.

## Model and texture actions on the submesh table

Asked for alongside: Export and Replace on the parts table, and a texture
submenu with the full set. The row already names a model and its textures, so
"export that" was a scroll away in another panel for no reason.

Right-clicking a submesh now offers the model's actions directly and one
submenu per texture slot -- named by what the **shader** calls it (`t_Normal`,
not "texture 3") -- each with Preview, Export, Replace, Open in its tab, What
uses this and Copy path. Every one of them *calls the linked-assets panel's own
method*: this builds no dialogs. Two context menus offering "Replace…" with two
sets of filters and two refusal messages is the drift `show_users` and
`export_asset` were made module-level to prevent.

Two things worth keeping:

- **The menu is built by a method that does not show it.** `exec` opens a modal
  loop, so a menu that raised while being assembled would be invisible to every
  check here -- `check_app.py` is static and the widget layer has no unit tests.
  `drive_models_tab --menu NAME` prints what `build_parts_menu` returns.
- **Verified by triggering every action with the dialogs stubbed**, not by
  reading the code: 24 actions fired, each with the *resolved* vpath rather than
  the scene's raw `textures/x.dds` reference -- the join that has already been
  wrong twice in this project.

## Blinkers, and the variant bug found while adding them

Added 2026-08-16, from a round of real use.

**Eleven groups were called `boost`, and the selection was keyed by name.**
`PlayerShip.xml` has one `boost` group under *each* of `booster_0..booster_10`,
so all eleven collapsed into a single entry whose value pointed into
`booster_10`. Every other group then saw a chosen path it did not contain and
rejected **every member it had** — so choosing booster 5 drew the booster and
hid all eight of its nozzles. `NodeGroup.key` is `parent_path|name`, unique by
construction, and that is what the selection is keyed by now.

The visible half of the same bug: **ten of the eleven `boost` combos could not
do anything**, because they sat under a booster that was not the chosen one.
`SceneGeometry.reachable_groups(selection)` drops those, and the combo row is
rebuilt whenever a selection changes — so picking booster 5 swaps in booster
5's own `boost` combo.

**Blinkers are editable, and drawn as markers.** `scene.BlinkerGroup` /
`scene.Blinker` read and write the `Texture` attribute and the
`<Blinker displacement="x y z size" vrow="…" animtime="…"/>` list, in the
shipped number format, so an untouched group round-trips byte-for-byte and an
edited light touches one line. `meshview.blinker_markers` emits one draw call
per group — a cluster of small emissive spheres on a `blinker` layer — because
the real thing is a camera-facing sprite and there is no billboard renderer
here. Markers say *where* the lights are without pretending to show what they
look like.

`blinker_table.BlinkerTable` is the editor: a group picker, the texture, and a
row per light with x/y/z/size/vrow/animtime, plus add and delete. `vrow` picks
a row of the texture sheet — 0.111 in `blinks_0`, i.e. one ninth.

**The additive shells are drawn see-through.** Qt Quick 3D has no notion of the
engine's additive blend, so a shield bubble was an opaque shell with the ship
sealed inside it. `viewport.LAYER_OPACITY` gives shield 60%, glow and shine
55%, distortion 50%. The first attempt used 30%, which is what "70%
translucent" sounds like — but the shield texture is dark, so 30% of it against
a black background was invisible. Tune by looking, not by arithmetic.

**And translucency exposed an older bug: the `<Material>` "emissive" row is
not self-illumination.**

Qt Quick 3D's `emissiveFactor` is self-illumination, not an additive pass, so
driving it from that row rendered `ComSat`'s hull pure white — measured mean
(244,244,244), 92% of lit pixels near-white — with `comsat_col.dds` nowhere to
be seen. Forcing emissive to zero dropped it to a correctly textured
(129,130,133). Capping it at 0.35 was **not** enough; it still came out white.

The corpus says the same thing independently: **5,069 of 16,444** shipped
materials set that row to (1,1,1). If it meant self-illumination, a third of
everything in the game would be a featureless white blob.

So a submesh **with** a base colour map now gets no emissive at all — the
texture is the thing worth showing — and one **without** still uses the row,
because it is the only description of the surface there is, and the blinker
markers are exactly that case. This is a correction to the provisional
D3DMATERIAL9 row reading, not a rendering tweak.

**The selected blinker is drawn in red**, and getting it there cost three
bugs — all found by driving the running app and *looking at the frames*, which
is the only way any of them were going to show up.

1. **Appending the marker to `calls` blanked the viewport.** Replacing that
   list makes `Repeater3D` rebuild every delegate, and destroying a `Model`
   destroys the `QQuick3DGeometry` it was handed — so every other submesh lost
   its geometry and the scene went black except for the marker just created.
   The object model said 4 of 7 shown throughout, which is why this was
   invisible without a screenshot. The marker now has **its own property with
   its own notify signal**, so `calls` is never re-read.
2. **`baseColor: hl.diffuse` silently did not bind** — no QML warning, and the
   marker rendered pure white among white blinkers. It is a literal `#ff2020`
   now, which is also more honest: the marker's colour belongs to the app, not
   to the scene.
3. **Escape had no way to clear the selection**, so the marker could not be
   dismissed at all. An event filter on the table handles it.

The marker ignores the variant selector and the isolate button — it answers
"which row is this?" — but it **does** follow the `blinker` layer switch,
because unticking a box that leaves something on screen reads as broken.

**Only the group the table has open is drawn.** A scene can hold 63 blinker
groups — PlayerShip does — and all of them at once is a cloud of dots with
nothing tying any of it to the table below.

That rule also fixed a regression the live-update introduced. `apply_visibility`
matched draw calls by **object identity** against `geometry.visible_calls`, and
re-cutting a group's markers produces a *new* `DrawCall` — so the moment a group
was previewed it fell out of that set and stopped being drawn. Markers are
editor affordances, not scene content, so they no longer go through the variant
filter at all: their rule is "the layer is on, and this is the open group".

**Changing group resets the selection.** A row index means nothing across
groups — row 3 of `blinks_0` is a different light from row 3 of `blinks_1` —
and `currentRow()` survives a refill, so the marker used to stay on screen
pointing at a blinker from the group you had just left.

**And the white markers follow the table too.** Built once from the parsed
scene they stayed where the *file* put them while the red one moved with the
edit, so a blinker being dragged appeared to split in two.
`meshview.blinker_marker_call` takes plain `[(position, size)]` rather than a
parsed group, so the editor can re-cut a group's markers from values that have
not been saved yet; `CallItem.replace_geometry` swaps the geometry in place,
leaving `calls` alone so nothing rebuilds.

**The splitter moved.** The variant combos, the LOD picker and the layer
switches all change what is on screen, so they belong with the viewport, above
the handle — not swept into the scrolling pane with the tables that merely
describe it.

## The rest of Phase 5

**Closed 2026-08-16**, and measuring it first changed the shape of the job.

**Four new drawable layers, not five.** `CGlowObject` (1,460),
`CDistortionObject` (538), `CShineObject` (304) and `CShieldMesh` (254) all
reference a `.3do` **and** carry their own `EffectContainer` — every single one
of them — so they build exactly like a mesh and go through the same loop; only
the layer differs. That is 2,556 objects that were parsed and then never drawn.

**`CBlinkerGroup` is not one of them, and the plan was wrong to group it with
them.** All 621 have no model, no effect and *no children*: a blinker group is
a **point-sprite emitter** — a `Texture` attribute plus a list of
`<Blinker displacement=… vrow=… animtime=…/>`. Drawing one means billboards,
not triangles. Left out rather than faked.

**Framing had to become layer-aware or this would have been a regression.**
`SceneGeometry.bounds()` spanned every draw call, and glow and shield hulls
reach well past the ship — so simply making them drawable would have shrunk
every model on screen even with those layers switched *off*. That is the same
"the ship becomes a speck" failure the `radius` docstring already warns about,
arriving by a different route. `bounds`/`center`/`radius` now take the layers
to frame on, and fall back to all calls when the restriction matches nothing,
so a scene made entirely of glow objects is still framed. Measured on
PlayerShip: 20.6 with the hull alone, 26.6 with everything.

They are **off by default**. These are additive shells the engine blends over
the hull; drawn here as ordinary opaque geometry they *hide* the ship rather
than glow around it, and the layer tooltips say so before someone reports it as
a bug.

**`.glb` import is checked before it lands.** `Session.preflight_glb` imports
the model, counts submeshes **across all LODs**, and asks the index which
scenes bind that `.3do` — then reports every scene whose `EffectContainer`
count would no longer match. This is the one check worth running before the
write: a model round-tripped through a DCC tool routinely comes back with
materials merged or split, the scene still has the old count, and the engine
says nothing at all — it binds the wrong material to the wrong surface.

It **warns rather than refuses**, and names each offending scene and node.
Deliberately re-cutting a mesh *and* updating the scene is a legitimate
two-step edit, so this is Deploy's shape: say exactly what will not add up, and
let the author say yes. When the index is not built it says *that* instead of
reporting a clean bill of health nobody earned.

Verified on a copy of a real mod: a faithful `.3do` → `.glb` → import
round-trip reports no conflicts; removing one submesh reports
`statue.xml / AH_dienst_sphere_ has 2, needs 1`.

## Settings, and notices that can be told to stop

`settings.py` is a JSON file, **next to the executable** when that folder is
writable and in the per-user data folder when it is not — the same fallback
`frozen.report_dir` already uses, because `C:\Program Files\…` is not
writable and a preference that silently fails to save is worse than one kept
somewhere less obvious. It lives on `Session` since that is the object every
widget already holds, and it is Qt-free like the rest of that layer.

Nothing in it is load-bearing: every read takes a default and every failure is
swallowed. Losing a "do not show this again" tick is a nuisance; refusing to
launch over a corrupt settings file would be absurd.

`linked_assets.dismissible_notice` is the first user. Two deliberate
differences from `QMessageBox.information`: **no icon, and therefore no
sound** — on Windows the alert sound is tied to the icon, so an informational
dialog dinged every time — and a **"do not show this again"** box, remembered
per notice key so silencing one never silences another.

## Editing: reset, live preview, and reset-to-stock

Added 2026-08-16, and three of the four only became possible once `.bsd9` was
decoded.

- **"Reset to shader defaults"** in the Shader options dialog. Not the same as
  **Revert**, which restores what the *scene* says; this restores what the
  *shader* says, from the compiled-in defaults in its D3DX9 parameter table. A
  parameter the shader does not declare has no default to restore and is left
  alone rather than zeroed — so on `mat_biotechanim`, `Reflectivity` and
  `EmissiveFactor` move and `Bumpiness`/`Roughness` do not, because that shader
  does not have them. The button disables itself, with a reason, when the
  shader cannot be read.
- **The dialog is called "Shader options"** — title and group box — matching the
  button that opens it. It said "Effect" in two places and "Shader options" in
  the third.
- **Live preview.** The viewport shades from the draw call's own numbers and
  every shading property is `notify=changed`, so a preview is those numbers
  swapped and one signal: no rebuild, no re-uploading 70,000 triangles and their
  textures. `ModelViewport.preview_shading` matches on the draw call's
  **identity**, not a row index — the parts table lists only *visible* calls
  while the viewport holds all of them, so the two are not the same sequence
  once a variant is hidden. Cancel restores the snapshot taken before the dialog
  opened, or the viewport would keep showing values the scene does not contain.
- **"Reset to stock…"** in the Project tab's context menu. It **removes** the
  mod's copy rather than writing stock bytes back: an identical-to-stock file is
  dead weight the app then reports as having no effect (`PRJ002`), so restoring
  the content would just trade one complaint for another. The confirmation says
  "remove" for that reason. Disabled with an explanation for an addition (no
  stock version to fall back to) and for `items.ini`.
- **`.dsoproj` is no longer listed as mod content.** It is this tool's sidecar
  and the game never reads it; among the author's files it invited the question
  "what does this override?", whose answer is nothing. `Mod.files()` now skips
  it alongside the manifest and the archive.
- **`items.ini` identical to stock is labelled "identical to stock, and
  required"**, not dead weight. Its *presence* is load-bearing whatever its
  contents — this is the same trap `PRJ002` fell into when it advised deleting
  it, which makes the game silently refuse to list the mod. `Mod.remove` refuses
  it outright, so no path through the UI can get rid of it by accident.

## `.bsd9` — decoded, and what it corrected

**Decoded 2026-08-16.** Full write-up in `specs/bsd9.md`. It was the largest
open unknown and it settled the first of the three heuristics outright.

**The shader names its own texture slots.** `mat_main.bsd9` declares
`t_Color, t_Light, t_Normal, t_Reflection`; `mat_main_2` declares two;
`mat_biotechanim` one, called `tex0`; `phong1_1` none. The scene's `<Textures>`
list pairs with that list positionally, and **the two are the same length in
15,978 of 15,978 effects** — none binds more, none fewer. That total agreement
is what makes the pairing safe to rely on, and `verify_all` enforces it.

**It found a real rendering bug: 841 submeshes had the wrong normal map.** A
five-slot family — `t_Color, t_Light, t_SpecialMap, t_Normal, t_Reflection` —
feeds a `_nrm`-suffixed texture to **`t_SpecialMap` as well**. Scanning for the
first `_nrm` found the special map and stopped. `Cruiser_A_0.xml`'s `mainShape`
bound `testtechstuffdxt_nrm.dds` where the shader says `a_plates1_nrm.dds` —
which is the obvious pair of its `a_plates1_col.dds` albedo, so the names
corroborate the decode independently. Eight base colours were wrong too
(`planet.bsd9`, whose `t_Color` is `demo.dds` while a later slot is
`defaultplanet_cloud_col.dds` — the heuristic took the cloud layer).

**The suffix convention was not thrown away, it was demoted to the fallback.**
It is still right 12,720 of 12,728 times for base colour, and it is still the
only evidence for shaders whose slot names are generic (`tex0` receives `_flat`
473 times, `_lgh` 446, `_col` 62 — it means nothing) and for the **466** effects
whose shader is not in the installation at all. `pick_slots(refs, slot_names)`
asks the shader first and falls through unchanged otherwise.

The UI now labels linked-asset rows with the shader's own slot name — `t_Normal`
rather than `texture 3` — and the amber "this was inferred" marker fires only
where the shader really named nothing.

**The blob turned out to be a Microsoft format, not Ascaron's.** Its first
dword is `0xFEFF0901` — a **D3DX9 compiled effect**, i.e. the output of
`D3DXCreateEffect`, which Wine's `d3dx9_36` documents. So the parameter table is
readable, and it is now read: every parameter's name, **semantic**, type and
compiled-in default. All six universal `<Parameters>` semantics are there with
their defaults (`Bumpiness` 1, `EmissiveFactor` 2, `DetailRepeat` 12,
`DetailIntensity` 0.5, `Roughness` 1, `Reflectivity` 1).

One trap, and it is nasty: **an annotation is 2 dwords, not 4.** Getting it
wrong desyncs the walk a few parameters in and does *not* raise — it yields
plausible-looking records with garbage names. The gate therefore checks that all
8,714 parameter names are C identifiers, not merely that the walk finished. Also
note `VECTOR` stores columns-then-rows while `SCALAR` and the matrix classes
store rows-then-columns; a 1×4 read backwards becomes 4×1 and its default is
read as one float instead of four.

**A fifth of the parameters a scene writes are inert.** The exporter wrote the
same fixed block onto every effect regardless of shader, so **17,998 of 82,872**
parameter writes address a semantic the shader does not declare, and D3DX
ignores them. It is coherent rather than random: `mat_main_2` and
`mat_biotechanim` declare neither `Bumpiness` nor `Roughness`, and neither has a
normal-map slot. The effect editor now marks those rows — editing one changes
the file and nothing on screen, which is the `PRJ001`/`PRJ005` complaint again.

**`<Material>`: shape confirmed, order still inferred.** `mat_main` declares
`Diffuse`, `Specular`, `Ambient`, `Emissive` as 1×4 vectors plus a scalar
`SpecularPower` — 4×4+1 = 17, exactly the block's size. But **D3DX binds by
semantic, not position**, so the shader's declaration order is *not* evidence
for the XML order — and it differs from the D3DMATERIAL9 struct order anyway.
The corpus supports the existing reading: over 16,444 materials row 3 sits at
`0,0,0,0` (emissive), the scalar takes 200/20/100/150/300 (specular power), and
row 2 takes values **above 1** (`4,4,4,0`, `3,3,3,0`) — defensible for specular
intensity, hard to justify for an ambient colour. Evidence, not proof; the row
labels stay marked provisional.

Still undecoded: the technique/pass and object tables (shader bytecode, sampler
state), the five chunk payloads, and therefore what the engine actually *does*
with a parameter.

## Phase 6c — Scripting, and the files that go into the game folder

**Built 2026-08-18**, once the user supplied the *Darkstar One Modding Tools*.

**The CHM really was just a parsing job.** `ds1doc_eng.chm` decompiles with
Windows' own `hh.exe` into 325 pages, and `dsotools/scriptdoc.py` turns them
into **318 symbols** (254 commands, 39 events, 25 camera helpers) plus 223
constants — committed as `src/dsotools/data/lua_api.json` so nobody else needs
the modding tools installed. Three page shapes, and the shape is the meaning;
an optional parameter is marked by *italics and nothing else*. Full account in
`specs/lua_api.md`.

Two of Ascaron's pages carry a copied `<title>` — `ActionCamStart.htm` is
titled *ActionCamEnd* — so the symbol is named by its **signature**. Keyed on
the title, two events collide and one disappears.

**The undocumented-call check had to be measured before it was worth having.**
Naively it reports 153 hits on the game's own scripts. Resolving the 1,018
functions the libraries define for themselves (`MissionLib.Rnd = function(...)`,
not `function MissionLib.Rnd(...)`) takes it to 20; stripping comments takes it
to **15**, and those 15 are real gaps in the 2006 reference — the shipped
libraries call `NObject.SetScanningFlag` and friends. So the tab says **"not in
the reference"**, not "unknown symbol". `verify_all` pins the number, because
at 153 nobody leaves the check switched on.

## Some mod content cannot live in a mod folder

The bigger finding, from a large third-party mod's archives:
**no `.cpr` archive holds a single `lua/` entry.** The shared mission libraries
exist only as loose files in the game installation, and a mission script
imports them by a path resolved against the game root. Seven content roots are
loose-only that way (`particlescripts` 1,501 files, `interface3d` 611,
`objectfieldscripts` 342, `lua` 13, `effects` 23, `frontend` and `strings` 3
each), plus `video/subtitles`, which the VFS does not even index.

That is why the mod ships **two** archives, the second with a readme saying
*"copy into game root"*. Copied by hand it is a one-way door: on this machine
all twelve of its root files were already in place, byte-identical — including
`lua/mission/sync.ffs_lock`, a FreeFileSync lock file shipped by accident — and
the stock `MissionLib.lua` they replaced exists nowhere any more.

**`dsotools/rootfiles.py` makes it reversible.** A mod's payload lives in
`<mod>/root/` mirroring the game root; the manifest is recorded in `.dsoproj`;
displaced files are copied into `<game>/.dso_backup/`; and
`<game>/.dso_installed.json` records who owns what — kept beside the files
rather than in the app's settings, because the files outlive any one machine's
copy of this tool.

What it refuses is the point:

- installing over a file another mod owns (offer Swap instead — which
  uninstalls first, so the second install cannot back up the *first mod's*
  files and call them the originals);
- removing or restoring a file whose bytes changed since it was installed;
- claiming a stock file can be restored when no copy of it was ever taken.
  Files **adopted** from an installation that already had them are marked as
  such, and Uninstall says so rather than deleting a library the game needs.

**Editing a library saves into the payload, never into the game.** That is what
ties the Scripting tab to this: `MissionLib.lua` opens from the game folder and
saves to `<mod>/root/lua/mission/MissionLib.lua`, and the tab says so on screen
every time.

## Validating a script against the build, not the manual

**Done 2026-08-22**, after four in-game experiments settled how mods deliver
scripts (`specs/mod_packaging.md`).

The reference and the executable disagree in both directions, so validating
against the documentation alone is wrong twice over.
`tools/exe_api_scan.py` recovers the engine's registration tables straight out
of the exe -- pure Python, no disassembler: namespace tables are pointer pairs
at a 12-byte stride, so they can be found by scanning the data sections. It
writes `src/dsotools/data/lua_engine.json`: **219 functions in 21 namespaces**,
the **5** documented-but-absent, the **30** undocumented, and the **2** that are
registered and do nothing. It refuses a DRM-wrapped executable rather than
returning half an answer.

The Scripting tab now reports four kinds:

| kind | meaning |
|---|---|
| `absent` | documented, and this build does not register it |
| `stub` | registered, and it does nothing (`NDebug.Message`) |
| `literal` | prose where the reference says the parameter is a StringId |
| `unknown` | not documented, not registered, not defined by the Lua in play |

On a stock installation the shipped scripts now raise **no** `absent`,
`unknown` or `literal` findings and 4 `stub` ones. Judged against the reference
alone, two real functions look like typos.

`literal` is the one that matters most in practice: `ShowInfoText`,
`ShowSubtitle` and `AddMessage` take a StringId from `user_strings.res`, and
passing text makes the call succeed and display nothing. That cost a full
experiment cycle here before it was understood.

**`dsotools.luac`** wraps the modding tools' `ScriptCompiler.exe`, which is
`luac` for this engine's modified Lua 4.1 -- the only parser that agrees with
the game. `-p` syntax-checks, `-o` builds `user_scripts.bin`, `-l`
disassembles. `chunk_names()` reads a bundle's contents with no compiler at
all, which is how `missions.bin` was shown to hold 154 chunks including the
libraries.

**`PRJ007`** is the mirror of `PRJ005`: `3DView/` and `images/` must be inside
`user_data.zip`, and `scripts/` must not be. Measured with two identical mods,
one loose and one zipped -- the loose scripts ran, the zipped ones did nothing.

## Stock state is recorded; everything else is a delta

**Done 2026-08-22.** The suite had no way to tell a stock file from a modded
one, and it had already cost accuracy: numbers measured on an installation
carrying that mod's libraries were written into the specs as facts
about the game.

`tools/stock_baseline.py` records a known-clean installation — 2,687 loose
content files with size and SHA-256, the six archives, and both executables —
into `src/dsotools/data/stock_baseline.json` (375 KB). `dsotools.baseline`
then classifies any installation: **unchanged / modified / added / missing**.
`Session.non_stock_files()` adds who owns each difference, from the rootfiles
ledger, so "a mod put this here through the tool" is distinguishable from
"someone copied this in by hand".

**One baseline serves both editions.** GOG and Steam are the same build: only
`.text` differs (the Steam copy is DRM-wrapped), `.rdata`/`.data` are
byte-identical, none of the 2,687 shared files differ, and Steam merely adds 37
German localisation files. That also fixed `exe_api_scan.py`, which had been
refusing wrapped executables for no good reason — the API tables are in
`.data`, so it now scans either edition and produces byte-identical output,
fingerprint `532b21cc8f37`.

**Size alone is not enough**, which the real mod proves: two of the twelve
files it installs are byte-different at exactly the stock size — `missions.bin`
with its 39 identifier edits, and one subtitle XML. So everything up to 4 MB is
hashed (2,653 files, 197 MB, about a second) and only video is compared by
size.

Checked end to end against the mod's own payload: 8 modified, 4 added, and both
installations here report clean.

## The `.res` hash, and custom text

**Done 2026-08-22.** This was the last thing between a modder and words on
screen. Every text-showing API takes a StringId, so without the ability to write
a string table a mod could change numbers and models but never say anything.

The format is a count, a run of 16-byte records (hash, offset, reserved, byte
length) and a UTF-16LE blob. Two things the earlier partial reading got wrong,
both now corrected in the specs: there is no `size` field, and the entries are
**not** sorted by hash — the writer emitted them in .NET hashtable order, so a
reader must not binary-search.

The hash itself:

```python
h = 0                      # 32-bit SIGNED
for c in identifier:
    h = (h * 113 + c) % 999999991
```

in C# arithmetic — the multiply wraps at 32 bits, `%` keeps the sign of the
dividend, and only the final value is read as unsigned. **That last clause is
the entire difficulty.** Read as unsigned, the function cannot produce a value
above 999,999,991; real tables are full of keys above 4,000,000,000. Every
reconstruction failed against the data for that reason, including one that had
the multiplier and the modulus exactly right.

How it was finally settled is the transferable part. Guessing was exhausted —
standard hashes, .NET `GetHashCode` at three framework versions, and a
Hensel lift over every `h = h*K + c (mod 2^32)` all came up empty. What worked
was reading `Xml2ResConverter.Converter.Hash` out of the shipped .NET assembly
(44 bytes of IL) and then **invoking that method by reflection** rather than
trusting the hand decode. The two disagreed: I had read opcode `0x5D` as
`rem.un` when it is signed `rem`. 61/61 vectors agreed once the tool itself was
asked.

Verification: all 12 `.res` files across both installations and the tutorial —
76,000+ entries — parse with zero malformed records and rebuild byte for byte.
That is a `verify_all` check, not just a unit test.

**A table stores hashes, so ids are not recoverable from one.** Two consequences
shaped the design. The authored `(id, text)` pairs live in the `.dsoproject` and
`user_strings.res` is a build product — lose the project and the mod's text is
uneditable. And there is no catalogue of stock ids: hashing every
identifier-like token in the executable, the DLLs, the loose Lua and all six
archives resolves 934 of 9,378 stock keys, 10%. Looking up an id you already
know always works, which is what `Session.stock_string()` is for — it warns
before a mod shadows stock text by accident.

Two rules came with it. **`PRJ008`**: the mod's table is unreadable, or two ids
hash alike so one text is permanently unreachable. **`PRJ009`**: a script names
a StringId that neither the mod's table nor `global.res` defines — a missed
lookup draws nothing at all, with no placeholder and no log line. The editor is
`Mod text…` in the Scripting tab; it refuses a colliding save the same way the
shipped converter did.

## Overriding a stock mission

**Done 2026-08-22.** The engine has no "replace a mission" call.
`NScript.Register` stores `SCRIPT_TABLE[Name] = record`, and a mod's
`scripts\` is read after `lua/mission/missions.bin`, so registering an existing
name overwrites that mission and registering a new one adds it. **The same call
does both**, and the difference is one string — which is precisely why it needed
a UI rather than a documentation paragraph.

To offer it, the suite had to know what the stock missions are, and they exist
only as Lua 4 bytecode. `ScriptCompiler -l` disassembles `missions.bin` with
string constants intact, and each chunk's registration record has a fixed shape,
so name, type, group and the ordered state list all read back.

**154 chunks = 150 missions + exactly 4 libraries.** Nothing unaccounted for —
that is the check worth having, not "it produced 150 records", because a parser
that quietly drops one would still look healthy. GOG and Steam produce identical
tables, so one ships (`tools/stock_missions.py` →
`src/dsotools/data/stock_missions.json`, 35 KB).

Three facts fell out that a modder will otherwise learn the hard way:

* **The file name is not the mission name.** `BAR_006_02.lua` registers
  `BAR_006`; `STORY_011_01.lua` registers `STORY_011`. Only the `Name` field
  decides what a script replaces, so the generated override is named after the
  mission, not after the stock chunk.
* **Names are case-sensitive** — stock ships both `ALWAYS_000` and
  `Always_001`, so `ALWAYS_001` would *add* a mission, not replace one.
* **Two mission types are undocumented.** The reference lists eight; the bundle
  also uses `MTYPE_STORY_CHAPTER` (8 missions) and `MTYPE_MENU` (1).

The Scripting tab's **New mission…** dialog has the two intentions as separate
tabs. Overriding lists all 150 with type and states and fills the template from
the real record — a state left out of an override stops existing for that
mission, so the states are shown rather than assumed. Creating a new mission
refuses a stock name outright.

`PRJ010` covers the aftermath: two scripts registering one name is an **error**
(only one registration survives and which one is undefined), and replacing a
stock mission is reported as **information** — legitimate, but easy to do by
accident and invisible in game.

Both generated templates parse under `ScriptCompiler -p` and raise no findings
from the Scripting tab's own API check.

**One defect was caught on the way, by reading the stock bytecode rather than
the template.** `Init` decides whether a mission is created at all: stock
`ALWAYS_000` ends its `Init` in `{ Ready = true }` on one branch and
`{ Ready = false }` on the other. The first version of the generator emitted an
empty `Init`, which returns nil — a script that parses, registers, looks
finished and never runs, with no error anywhere. `STATE_BODIES` in
`dsotools/missions.py` now always emits the return, and a test pins it.

**`PRJ011` turns that defect into a rule.** The suite's own generator got `Init`
wrong, which is the best evidence there is that a hand-written script gets it
wrong too — so `missions.init_states` reads the `Init` transition's body and
the validator reports, as an **error**, a mission that can never be created.
Only the certain case is reported: a body with no `return` in it at all. A body
ending in `return MissionLib.Decide( V )` is legitimate and unreadable to a text
scan, so it comes back as *unclear* and nothing is said. A validator that cries
wolf gets switched off, and the real findings go with it.

Finding the body at all needs a small block scanner, because the naive one is
wrong in a way that looks right: `Transitions` bodies contain `if`, `for` and
inner `function`s, and stopping at the first `end` reads a fragment. It counts
`function`/`if`/`do` against `end` — `for` and `while` are deliberately *not*
counted, both being followed by the `do` that is — over text with comments and
string contents blanked in place, so `NGUI.ShowInfoText( { Text = "the end" } )`
does not close the function.

### Adding an asset, binding it, and taking it out again

The first cut of this was a generic **Add file…** in the Project tab — type a
path, pick a file — and it was the wrong feature. Not because it did not work,
but because nobody wants to add *a file*: they want to add a **texture**, from
the tab that shows textures.

**Add model… was built and then removed too**, for a reason that took an
investigation to establish: a new model needs a scene to name it, and nothing a
mod can write reaches a new scene name. `NWing.Create` takes fixed
`WINGTYPE_*`/`RACE_*` constants, no shipped ini maps a wing type to a ship
family, and the class-to-family mapping lives inside the executable. Replacing
the `.3do` an existing scene already names is the route that works, and the
linked-assets panel had always done it. See `specs/scene.md` §4.3.4. The typed version knows three things the generic
one could not, and the third is the one that matters.

* **Where it goes.** `3DView/textures/`, `3DView/objects/`, `scripts/`.
* **What it accepts.** A `.glb` is a model and a `.png` is not a texture, and
  the refusal names the formats the kind takes rather than leaving them to be
  inferred.
* **Whether it does anything at all on its own.** Three of the four kinds are
  **inert until something names them**, which is exactly the silent failure
  this project exists to surface. An app that writes the file and says nothing
  has built a very tidy no-op.

That table is `Session.ADD_KINDS`, read by the tabs rather than restated in
each — the same reason the delivery rules live in one function. A script is the
one kind marked *not* inert, and that is measured rather than assumed: the
loader globs `scripts\*.lua` and runs each one, so a script takes effect by
existing.

| Tab | Adds | Then | Inert until |
|---|---|---|---|
| Textures | **Add texture…** (`.dds`) | Shader options ▸ a texture slot | a scene binds it |
| Models | *(nothing — see below)* | **Change model…**, in the submesh context menu | — |
| Scripting | **New script…** (`.lua`) | — | nothing; the loader runs it |
| Audio | **Add sound…** (already there) | — | it declares as it copies |

**The library could always write a file at a new path.** `Mod.deploy` routes by
the delivery rules and never cared whether the path existed; `replace_asset`
refuses a PNG offered as a `.dds` because there is no DDS writer, not because
the path is new. What was missing was an entry point that *asks*. The path
rules are `project.check_mod_path` — the delivery matrix as a function, which
finally gives the long-unused `LOOSE_ROOTS` constant a caller.

**Binding is the half that makes adding worth doing.** Both setters already
existed in the library with **no caller anywhere in the app**:
`EffectContainer.set_texture` and `SceneObject.model`. What was genuinely
missing is a way to spell a virtual path the way a scene has to.

`Vfs.reference_for` is that piece, and it is the interesting part of this work.
A scene names its assets *relative to itself*; writing a vpath straight in
produces a reference that resolves to nothing, or to a different file of that
name. It checks itself by resolving its answer back, which is load-bearing
rather than defensive: a texture in a scene's private folder can be shadowed by
one of the same name under `3DView/`. All 35,763 texture references across the
1,006 stock scenes round-trip through it.

It is also **stricter when writing than the reader is**, and that came out of
measuring rather than taste. The reader tries a bare-vpath candidate last;
`0 of 45,322` resolving references in stock need it, so it is this project's
convenience and not an engine rule. Writing one would work in the app and rest
on something the engine has never been observed to do — so the texture picker
offers only the textures under `3DView/` and says why the rest are missing,
`bindable_models` filters the same way, and `set_effect`/`set_mesh_model`
refuse outright. See `specs/scene.md` §4.0.1.

Binding a model asks **`SCN001` before the write**, not after: one
`EffectContainer` per submesh is what the engine assumes, and pointing a mesh
at a model with a different count binds the wrong material to the wrong
surface. `mesh_model_fit` reads the count from the root header at `0x30` — the
same field `validate_mod` reads — rather than parsing every LOD, so a model
whose geometry this build cannot decode still gets an answer. It warns and
proceeds rather than refusing: re-cutting a mesh and fixing the scene up is a
legitimate two-step edit.

**And a file that can be added and not removed leaves the tool apologising.**
`can_reset_to_stock` said so in as many words — *"Delete it outside the app if
you want it gone"* — because reset only ever meant "drop an override so stock
shows through", which an added file has no version of. `remove_from_mod` is the
general case, and the Project tab's menu names which of the two this file gets:
**Reset to stock…** when the game has its own copy, **Remove from the mod…**
when it does not. One word for both is what made reset look broken on every
file a mod had added.

Two things are refused and the rest are only reported, which is the distinction
worth keeping: a refusal is for damage the author cannot see, a note is for a
consequence they may well intend. `inifiles/items.ini` stays, and a file a
sound declaration points at is redirected to the Audio tab, which removes the
declaration and the file together rather than leaving one naming the other.
Everything else — the string table whose texts survive in the project file, any
of the mod's own scenes that bind the file as a texture *or* as a model — is
said out loud and then done.

`tools/drive_add_new.py` runs the whole chain against a real installation on a
mod it builds itself: override a stock scene, add a texture and a model the
game has never had, bind both, prove the scene resolves to what was added,
prove an asset outside `3DView/` is refused, write a script, and take the
texture out again. It also drives the widgets, because every session answer
above can be correct with a UI that never shows them — which is how it caught
that a pending rebind survived *Undo my changes*.

### 1.0

Marked **1.0.0** on 2026-08-23 — `pyproject.toml`, `dsotools.__version__`, and
the *Development Status* classifier promoted from Alpha. The Windows version
resource is generated from `__version__` at build time, and the release
workflow refuses a tag that disagrees with it, so those three cannot drift.

Checking the release path turned up four things it would otherwise have
shipped:

* **`pyproject.toml`'s `Homepage` pointed at a repository that does not
  exist** — an old name, never corrected. It is now the real URL, alongside
  `Issues` and `Source`.
* **`build.py` required Python 3.9** while the project requires 3.11. The floor
  had moved in `pyproject.toml` and the build's own check stayed behind, so a
  build would have accepted an interpreter the project does not support. Both
  now carry a comment saying they must match.
* **There was no `LICENSE` file**, only a README sentence. A sentence is a
  claim; the file is the grant, and without it GitHub and most tooling treat a
  repository as all-rights-reserved whatever the README says. `CONTRIBUTING.md`
  also promises contributions are accepted under it, which needs it to exist.
  The copyright holder was never a blocker — `pyproject.toml` had already
  declared the project's author.
* **Adding it broke the build**, which is exactly why it was worth doing before
  a tag rather than after. `license-files` puts setuptools into PEP 639 mode,
  where the old `license = { text = "MIT" }` table and the
  `License :: OSI Approved` classifier are both rejected. The fix is the modern
  spelling — `license = "MIT"` plus `license-files` — and a `setuptools>=77`
  build requirement, because 68 predates PEP 639 and a fresh CI runner resolves
  what it is allowed rather than what a developer happens to have. Verified by
  building a wheel: `License-Expression: MIT` and the text at
  `dsotools-1.0.0.dist-info/licenses/LICENSE`.

Both workflows were read end to end and are otherwise sound: CI lints, runs the
suite on two platforms and two interpreters, runs the pytest-free fallback, and
does a real PyInstaller build; the release workflow checks the tag against
`__version__` before building and has a `workflow_dispatch` rehearsal that
produces the identical artifact without publishing.

### Reporting a bug

**Help ▸ Report a bug…** opens GitHub's new-issue form prefilled with the
build, the platform, and whether a game and mod are open. The interesting part
is what it leaves out.

**It carries nothing that identifies the machine.** No install path, no mod
path, no user name, no folder layout — those are exactly what a bug report does
not need and what nobody should paste into a public tracker by accident. The
fact set is fixed and a test asserts its exact membership, because a field
added without thought is precisely how a path ends up in an issue. Two more
tests check that no path from the running machine and no user name reach either
the body or the URL.

**Nothing is sent.** The browser lands on a form the reporter reads, edits and
submits. A tool that filed issues on someone's behalf would be filing them
without their having read what it wrote — so the confirmation dialog lists the
facts verbatim before opening anything, and the body is composed in a Qt-free
module so it can be tested without a window.

The title is deliberately left empty: one the reporter wrote is better than one
a tool guessed.

`CONTRIBUTING.md` and three issue templates landed with it. The templates are
shaped around the project's actual bar — the *Format finding* one asks how a
claim was established, with the five evidence markers as checkboxes, and asks
for the exceptions too, because "3 of 1,040 files disagree" is the interesting
half. A pull-request template was written and then dropped as premature; what it
would have asked is in `CONTRIBUTING.md` instead.

### The documentation window

**Help ▸ Documentation** (F1) opens the specs inside the app. Non-modal, and a
`QWidget` window rather than a `QDialog.exec()` for a reason that is the whole
point: documentation is what you read *while* doing the thing it describes, and
a help window you have to close before touching the mod again is a worse place
for the rules than the repository they came from.

**It reads the repository's own markdown.** Nothing is generated, converted or
copied at runtime — `specs/` was already bundled into the frozen build, and the
two CLI manuals joined it. So what ships and what is written cannot drift, and
correcting a spec corrects the help.

Rendering is `QTextBrowser.setMarkdown` — Qt's own, no third dependency. It
handles headings, tables, code and links, which is what these files are made
of.

The catalogue is an **explicit list, not a glob**, for two reasons. `docs/`
also holds this project's working records — `STATE.md`, `HANDOVER.md`,
`TODOS.md` — which are about building the suite and would be noise or worse to
someone modding the game. And order carries meaning: the guide is first because
it is the only document that answers *what do I do*; the format specs answer
*how does this file work*, which is the second question.

`docs_library` is Qt-free and tested, `docs_window` lays it out. The
load-bearing test is that **every document the catalogue promises actually
exists** — a help menu that opens a blank page is worse than no help menu, and
the failure mode is a renamed spec, which no amount of care in the window would
catch. `tools/drive_docs_window.py` renders all thirteen and reports what came
through, because Qt's markdown parser is not the one that wrote these files.

### The modding guide

The piece that did not exist. Everything this project established about *what
fails quietly* was recorded in the specs as evidence for a format, which is the
right place for it and the wrong shape for someone asking "why did my mod do
nothing". `specs/modding_guide.md` is that answer, organised by what a modder
actually hits, in order:

get the mod listed at all (`items.ini`, and the game says nothing when it skips
you) → put files where the engine reads them (loose versus zip, in both
directions) → override rather than add, and what genuinely *can* be added →
scripting, where the silent failures are worst (`Init` must return `Ready`, the
mission's name is its identity, text must be a StringId, five documented
functions do not exist and others do nothing) → sound, where an undeclared file
is inaudible → validate and deploy before shipping.

Every claim in it is already carried in a spec with its evidence marker, and
the last section says how to read those markers — including `[open]`, which is
where this project already knows it has no answer.

### Editing the submesh list

`SCN001` — one `EffectContainer` per submesh across all LODs — was reportable
and **not fixable**. Bind a mesh to a model with three submeshes when the scene
carries two and the engine binds the wrong material to the wrong surface, or
none at all; the only remedy was to edit the XML by hand, which is the workflow
this app exists to replace. The Submeshes table now has **Add submesh** and
**Remove submesh**, and *Change model…* moved into that table's context menu,
where the thing it acts on already lives.

The added container is a **copy of the last one**, not a blank: a container
needs a `<Material>` to draw at all, and the shader, parameter block and
texture-slot count that make sense are the neighbouring submesh's.

The interesting part is whitespace. Layout lives in element tails, so appending
naively puts the new element where the *closing* tag used to sit — one indent
level out. The fix is a swap: the old last child takes the sibling indent, the
new last child inherits the closing one. What proves it is not "it parses
afterwards" but **add-then-remove returning the original bytes**: 754 of the
stock scenes that carry a mesh survive that round trip byte for byte, 0 failures.

### Fixing what the report found

Four diagnostics can now be repaired from the Problems tab: `PRJ004` (no
`inifiles/items.ini`), `PRJ005` (a loose file in a zip-only root), `PRJ007`
(scripts inside `user_data.zip`) and `SND004` (a declaration that disagrees
with its file). Three already had working machinery behind them — `PRJ004` and
`PRJ005` are what Deploy does, and `SND004` is what replacing a sound file does
— so the work was mostly deciding *which* to offer, and `Mod.unzip_scripts`
for the one that was missing.

**Which four, and why not the rest, is the whole design.** A fix is offered
only when the repair is exactly mechanical: one correct outcome, nothing needed
from the author. `PRJ006` (the same path loose and zipped) cannot know which
copy is the newer edit. `SND002` (a shipped file nothing declares) cannot know
whether the answer is to declare it or delete it. A menu entry that quietly
picks one is worse than no menu entry, because it looks like the tool knew.
The list lives in `Session.FIXES` with that reasoning next to it, and the tab
only renders the offer — it has no idea which rules are fixable.

Three properties are pinned by tests, and the middle one is the one that
matters:

* the repair happens;
* **the diagnostic is gone from a fresh validation afterwards.** A fix the
  validator still complains about is a bug that looks like a fix, and the
  `SND004` tolerance is now stated once, in `validate.sound_metadata_drift`,
  precisely so the rule and the repair cannot disagree about what "wrong"
  means;
* the report is **dropped**, not adjusted. A repair invalidates the other
  findings too — moving files into the zip changes what `PRJ006` would say —
  and a problem list that is right about the row just fixed and stale about
  the rest is worse than one that admits it needs re-running.

`Mod.unzip_scripts` takes the same ordering `apply_deploy_plan` takes, for the
same reason: the loose copies are written **first** and the archive rewritten
without them only afterwards. A failure between the two leaves the script in
both places, which the engine reads correctly and `PRJ006` reports; the other
order would lose the file outright. A loose file that already exists and
*differs* is left alone and reported, rather than overwritten — the same call
`deploy_plan` makes about a duplicated `3DView/` file.

`tools/drive_problems_tab.py` builds its own deliberately broken mod — one
instance of each repairable mistake, plus an `SND002` that must **not** be
offered a fix — opens the real window and applies all four. It checks the
button appears with the right label, that each finding is gone from a fresh
validation, and that `SND002` is still there at the end.

### `PRJ012` — the script API, for the whole mod

The Scripting tab has judged the file on screen against the real build since the
API table was extracted: `absent` (documented, not registered), `stub`
(registered, does nothing), `literal` (prose where a StringId belongs) and
`unknown` (a typo). It ran on **one file**, the one being edited. A mod with
twenty scripts had no way to see all of it at once, and `literal` is the failure
that cost this project a whole experiment cycle — text passed where a StringId
belongs displays nothing and reports nothing.

The rule is the same scan over every `scripts\*.lua` a mod ships. Making it one
scan rather than two implementations moved the machinery down into the library
as `dsotools/luascan.py`; `Session.check_script` is now four lines that gather
what to judge against — the reference, the executable's registration table, and
every function the Lua in play defines — and call it. Two implementations of
"what does this script call" would have drifted, and the editor's answer
disagreeing with the problem list is worse than either alone.

**It needs the game folder and says so when it does not have it.** Judging a
call *unknown* means "nothing in play defines it", and without the game's own
96 Lua libraries that is every library call in the mod. With no baseline the
rule is skipped and listed as skipped rather than run blind — a check that could
not run must never report as passing.

`absent` is the one error of the four: it is measured against what the
executable really registers, and the call fails at runtime with nothing to
explain it. The rest are silent rather than fatal, and each has a
false-positive story, so they are warnings.

`tools/make_probe_mod.py` builds the in-game probe **through the suite's own
API**, so it tests what ships rather than a hand-written copy. It overrides
`ALWAYS_000` and adds a control mission that replaces nothing, instruments both
with `NPlayer.AddCredits` on a decimal digit code, and shows text from the mod's
own `.res` table:

```
    1  override  Init          1 000  new mission  Init
   10  override  Create       10 000  new mission  Create
  100  override  Destroy     100 000  new mission  Destroy
```

Credits rather than log lines because `NDebug.Message` is a registered stub that
does nothing — that cost two experiment cycles here.

**It ran on 2026-08-22: 1,000 credits at game start, 13,012 after one hypergate
jump, and both custom messages appeared.** `+12 012` decodes as override `Init`
×2 / `Create` ×1 and control `Init` ×2 / `Create` ×1 — the override behaved
*identically* to a mission that displaces nothing, which is what makes it a
replacement rather than a name the engine merely tolerates. Two things closed at
once:

* **A generated mission override takes effect.** The last unverified link in the
  override feature.
* **A suite-written `.res` table reaches the screen** — the first custom text
  this project has ever shown the player, and the in-game half of the string
  work.

And one thing opened, then closed the same day. **`Init` is not called once**:
two calls per `Create`, on both missions. The documented lifecycle reads as
though `Init` runs once before `Create`; it is a readiness question the engine
re-asks, so a body with side effects pays for them twice. The generated template
now says so, because it invites the author to put code exactly there.

A second reading of the same probe — credits checked in the start system
*before* jumping — came back **unchanged**, so **both `Init` calls belong to the
arrival**. For an `MTYPE_ALWAYS` mission the start system produces nothing at
all: not `Create`, not even the readiness query.

That settles a question that had been open since the first scripting
experiments, and it *restores* a claim I had just weakened. The older note said
"nothing at all happened in the start system"; when `Init` turned out to be
polled I softened it, reasoning that the earlier mission had not instrumented
`Init` and might have been asked there unnoticed. The follow-up shows the
original wording was right — and now rests on a mission that does instrument
`Init`, which is the evidence it previously lacked. Weakening it was still the
correct call at the time: the run genuinely did not separate the two calls.

The consequence for a modder is firm: **no mission callback can run in the start
system**, so anything that must happen there belongs in the script's own body,
which executes when the loader reads the file.

## The Audio tab, and the parser that was seeing 0.7% of the data

**Done 2026-08-23.** The last stub is gone. What made this bigger than "wire up
a list" is that the foundation under it was quietly broken.

**`sounddb` read 3 of 442 sounds.** Groups nest — 60 of the stock database's 285
do — and the parser looked exactly one level down. It had been checked against a
flat mod database, where it was right, so nothing ever failed; `check_sounds`
was simply near-blind on anything shaped like the real thing. Rewritten
recursively: 285 groups, 442 sounds, and the 102 KB stock file round-trips byte
for byte.

**A sound's identity is its group path, not its name.** Stock reuses 38 names
across different groups, each pointing at a different file —
`FX_FighterExplosionDistant-03` lives under three explosion groups. The first
rewrite still had a flat `by_name()` index and a duplicate check that called all
38 a fault; the data said otherwise, so `by_name()` returns lists,
`by_qualified()` is the unique index, and only a repeat *inside one group* is an
error. That is the sort of thing that only shows up by looking at what the game
actually ships.

**`Duration` is in samples.** Written `:2048256`, and it had been sitting in the
docstring as an open question with a note that the values were "far too large
for milliseconds". Dividing by `Freq` predicts 46.45 s where the decoder plays
46.405 s. It matters because the engine reads those numbers from the database
rather than from the file: a wrong `Duration` truncates playback and looks like
a corrupt file.

### Byte-exact XML, extracted

Editing `user_sounds.xml` needed the same guarantee scenes have, so the layout
machinery moved out of `scene.py` into **`dsotools/formats/xmldoc.py`** and both
now share it. Scene's own corpus check — 1,187 files round-tripping
byte-identically — is what made that refactor safe to do, and it still passes.
Adding one sound to a 554-entry mod database changes exactly one
line of the file.

### Reading a file's own metadata

Adding a sound has to fill in `Channels`, `Freq` and `Duration`, and the library
cannot ask a decoder — nothing in `dsotools` imports Qt. **`formats/audio.py`**
reads them from the headers, and the check that it is right is that it
reproduces **442 of 442** entries Ascaron's tool wrote. Getting there needed
three corrections, each found by disagreeing with the data rather than by
reasoning:

* most effects are **IMA ADPCM**, where the byte count says nothing about the
  sample count — the `fact` chunk is the authority;
* for the 14 **stereo** ADPCM files the encoder wrote `fact` as the total across
  channels and Ascaron divided by them, so `fact // channels` is what matches;
* several MP3s **do not start with a frame** — `66_gameover_final.mp3` opens
  with a long run of zero bytes, so the sync word has to be searched for rather
  than expected at byte zero.

### What the tab does

Both databases in one tree, because "what exists and which are mine" is one
question: the game's 442 and the mod's own, grouped by path, with length, rate
and origin. Qt plays the selected file — MP3 included, confirmed through
`QMediaPlayer`. Adding a sound copies it into the mod under the folder its kind
belongs in and writes the entry with metadata read from the file. Replacing a
file re-probes it, because a stale `Duration` left behind is the failure above.

Two refusals worth naming. Adding a second file with the same basename would
land on the same path and silently change what the *first* entry plays, so it is
refused unless the bytes are identical. And `SND004` reports a declaration that
disagrees with its file at all — the class of bug that is invisible until
something plays for two seconds and stops.

## Overriding a stock sound works

**Measured 2026-08-23.** `DSO Menu Music Swap` carries exactly one entry — the
stock `Mainmenu/MUSIC_Mainmenu`, pointed at one of the game's own combat loops
— and the main menu played the combat track. So:

* a mod's `user_sounds.xml` entry **beats** the game's on a shared group and
  name, and
* replacing a stock sound therefore needs **nothing in the game folder**: no
  payload, no backup, no overwriting the installation. That is a much better
  answer than the alternative, because it is reversible by deleting a folder.

The Audio tab's *Override…* button is built on this, and its wording was
corrected afterwards — while the question was open it had claimed the mod's
declaration wins, which was the very thing being tested.

**Settled the same way a day later**: with the mod still selected, everything
else in the game still made its usual noise. So the databases are **additive**
and the mod's entry wins on a shared address — replacing a stock sound costs
nothing but a same-named declaration, and puts none of the other 441 at risk.

Worth keeping the shape of that: the swap alone could not distinguish additive
from wholesale replacement, because a mod whose database had replaced the
game's would have played its one track too. The discriminator was the sounds
that *did not* change, which is the sort of evidence it is easy to forget to
ask for.

## `staticImages/` is zip-only, and the suite had it wrong

**Measured 2026-08-23.** The delivery table in `specs/README.md` §5 says which
mod folders the engine reads loose. `3DView/` and `images/` were each
established by an in-game probe; `staticImages/` had never been tested, and was
carried as **untested** rather than assumed.

That caution paid: the assumption actually in force was the wrong one.
`ZIP_ONLY_ROOTS` held `("3DView", "images")`, so `Mod.deploy_target` — the
function whose own docstring calls it "what stops the app from writing files
that silently do nothing" — was sending `staticImages/` loose. Any mod that put
content there through the suite had it quietly ignored by the game.

`tools/make_staticimages_mod.py` builds the answer as a **pair**: the same
payload delivered loose in one mod and inside `user_data.zip` in the other, so
one sitting answers the question in both directions rather than leaving "it did
nothing" ambiguous. It swaps in the bytes of *another stock image of matching
geometry* rather than editing one, because most of these are `IMSLD32` — which
this project reads and cannot write — and a re-encoded file the game rejected
would look exactly like a folder the game does not read.

The run used an edited `staticImages\Starmap.dds` added to both: nothing loose,
visible from the zip.

The fix is one line, because every consumer reads that one constant —
`deploy_target`, `ModFile.is_dead` and `iter_mod_layers` all corrected
themselves. **Except one.** `vfs.add_mod` carried its own copy frozen at
`("3DView",)`, so it had been wrong since `images/` was established and would
have gone wrong again here. It was never called from anywhere and is now
deleted, with a note where it used to be. The lesson is the one `project.py`
already had a comment about: this rule may have exactly one home.

## `.bsd9`: the whole effect blob now walks

**Done 2026-08-23.** The parameter table had been read since August; the
technique table, the pass table and the object table had not, and were the
standing item on this list.

They follow the parameter records directly. A technique is a name, an
annotation count and a pass count; a pass the same shape with a state count;
the object table is two counts and then inline objects by id followed by
resources. **230 of 230 shipped shaders now walk to their last byte.**

The one number that had to be measured rather than reasoned out is the resource
header: five dwords. Four leaves 193 files unaccounted for and six leaves 207,
which is exactly why the "every byte accounted for" rule earns its keep here —
a D3DX walk that has drifted does not raise, it keeps producing records with
plausible structure and garbage strings. Landing on the end is the only check
that can tell the two apart, and it is now enforced by `verify_all`.

What it buys: **500 techniques, 609 passes, 3,514 objects**, of which 1,387 are
compiled shaders in four models (`vs_1_1`, `vs_2_0`, `ps_1_1`, `ps_2_0`). The
object header names the technique, pass and state it belongs to, so a shader can
be attributed to the pass that runs it — `Shader.techniques` hands each pass its
own objects.

It also **corrected §2.2 of the spec**, which had guessed that the leftover
header strings were "technique and parameter names". `mat_main`'s header carries
`DoIt`, `V20P20`, `FFPHigh`, `FFPLow`; the effect actually declares `V20P20`,
`ShadowMapV20P20` and `Occluded`. One name in common. Whatever the other three
are, they are Ascaron's own and most likely belong to the undecoded chunks.

Still not decoded, and now the whole of it: what each pass **state** means
(needs the Direct3D state table), what a shader does with a parameter (needs the
bytecode disassembled), and the five chunk payloads. The effect editor still
cannot say what a parameter *does* — that was the original motivation and it
remains out of reach — but it can now say which techniques and passes exist and
which shaders they run.

## Five consistency fixes, and the colour table behind one of them

**Done 2026-08-23.** All reported from using the app rather than found by
testing it, which is the pattern worth noticing: none of these was a defect any
check could have caught.

* **Pane labels in the Models tab.** The submesh and linked-asset tables sat
  above a labelled *Blinkers* group box with no titles of their own, so three
  panes read as one list with a heading two thirds of the way down.
* **The Textures tab could not replace a texture.** It could replace a *sprite
  inside an atlas page*, which is a different operation, and had no reverse
  lookup at all. It now has **Replace file…** and **What uses this…**, both
  through session methods that already existed (`replace_asset`, `used_by`) and
  a context menu matching the linked-assets panel elsewhere. The reverse lookup
  needs the asset index, and says so rather than reporting "nothing uses it" --
  those are different answers and only one is safe to act on.
* **Audio tab buttons.** *Replace file…* no longer appears on a stock row and
  *Override…* no longer appears on a mod row. *Override…* stays **visible but
  disabled** on a stock row the mod already overrides: an action that can never
  apply to this row is absent, one that could but currently cannot is greyed,
  because "already overridden" is worth saying.
* **The mission dialog opened on the destructive tab.** *Override a stock
  mission* was first and selected; writing a new mission is the common case and
  replacing one is the deliberate one. Order swapped, and the tab indices are
  named constants now rather than literals in three places.
* **Severity colours disagreed between tabs**, which turned out to be the
  interesting one.

### One severity, one colour

A dead file was **red** in the Project tab and **amber** in the Problems tab —
the same finding, `PRJ005`, painted two ways. Chasing it found *four*
independent colour tables: `theme.py` (with contrast tests), `problems_tab`
(using `#d68910`, an amber that appears nowhere else), `project_tab`, and per-tab
constants in Audio, Data and Interface. `theme.py`'s own comment says these are
named there "rather than repeated as a literal in four files -- it is a
legibility decision, and one place is where it can stay one", which the rest of
the app had quietly stopped honouring.

`theme.SEVERITY` is now that one place, keyed by `Severity`'s own string values
so the module stays importable without Qt, a game or a session. Both tabs read
it, and the Project tab's file states are declared *as* severities rather than
as colours, so a state and the rule that reports it cannot drift apart again.

The change overruled a deliberate older choice — "colour by consequence, not by
category; dead files cost people hours, so they must catch the eye" — and that
argument was not wrong, it was aimed at the wrong control. If a dead file
deserves the strongest colour it deserves `ERROR`, which `validate.py` defines
as "a change will not take effect" — a fair description of a file the engine
never reads. That is a severity question rather than a palette one, and it is
noted in the code where someone will meet it.

## Next, in order

See **`docs/TODOS.md`**, which is now the queue: what is planned, in the order
it should happen, with the parked items and the reason each is parked. This
document stays the record of what was done and why.

(Done: the Windows build, the viewport spike, Textures, Models, cross-tab
editing, CI, the `.dds` preview defect, the malformed-XML decision, the
`.anim` sizes, blinkers — parsed, drawn as markers, and editable through a
table — app settings, the `.bsd9` decode (container, texture slots and the
D3DX9 parameter table), the rest of Phase 5, the model rules
`MDL001`-`MDL007`, the blinker group labels, the Data tab, the `.screen` decode,
the Interface tab and its element tree, the CHM → JSON extraction, the
Scripting tab, the install-folder payload system, the recorded stock
baseline, the `.res` string tables, the stock mission table with override generation, and the Audio tab.)

## Things that cost time before

Each of these was a real bug, found the hard way. They are documented where the
code is, but they are the ones worth knowing up front.

- **A file that stops being referenced does not stop existing.**
  *Replace file…* repointed a declaration and left the old audio in the mod —
  a 2.8 MB track shipping as dead weight, visible only to `SND002`. Reported
  from the app. Replacing now deletes what it displaced, with three refusals
  that each prevent a worse bug: never a path outside `%MOD%` (the game's own
  files), never one outside the mod root, and never one **another declaration
  still names** — two entries sharing a file is legitimate, and deleting it
  would silence the other while leaving it looking healthy. `remove_sound` got
  the same guard, which it had been missing.
- **This suite does not build a `QApplication`, and that is load-bearing.**
  A widget test for the audio scrubber added one, imported `QtMultimedia` with
  it, and turned an eleven-second run into something the user cancelled after
  ten minutes -- the media backend leaves threads that never settle. Every
  other tab is verified by a `tools/drive_*_tab.py` script for exactly this
  reason, with the testable arithmetic pulled into a Qt-free module
  (`api_view.py`, `theme.py`, now `transport_view.py`). Follow that pattern
  rather than reaching for pytest-qt.
- **An exclude beats a static import, and "verified" has to mean "it ran".**
  Two defects in one build, both from adding the Audio tab, and the second is
  the one that matters. `PySide6.QtMultimedia` was in the spec's `QT_UNUSED`
  list from years back; the tab imported it, PyInstaller honoured the exclude,
  and the packaged app died on launch with `ModuleNotFoundError` while running
  perfectly from source. Separately, `QMediaPlayer` resolves its backend at
  runtime out of `plugins/multimedia`, which no import graph can see, so even
  with the binding present the app would have been silent -- and
  `Qt6Multimedia.dll` was already in `dist/` (Qt Quick 3D pulls it in), which
  made it look packaged.

  What let the first one reach the user is that `build.py` printed "built and
  verified" after reading the PE header and listing plugin files. It never
  started the app. It does now: `--selftest` builds the main window offscreen
  and exits, and the build fails if that does not come up. A check that cannot
  fail the way the thing actually fails is not a check.
- **Invalidating a cache is not the same as announcing a change.**
  `save_strings` dropped the mod's cached file index but never called
  `_emit("mod")`, so nothing rebuilt from it: the Project tab went on showing a
  mod without the `strings/user_strings.res` that had just been written, until
  pressing Validate refreshed the tab for unrelated reasons. Reported from the
  running app. Every session method that writes into the mod now emits, and a
  test asserts it for each — the failure mode is indistinguishable from "the
  save button did nothing", which is the worst thing a save button can look
  like.
- **A fallback that disagrees with the thing it replaces is worse than none.**
  `offline_test_runner` handed out a fresh `tmp_path` on every request, while
  pytest caches one per test. Four baseline tests that take both a fixture and
  `tmp_path` therefore passed under pytest and failed here — and the runner
  exists precisely so a machine without pytest gets the same answer. Both
  runners now report 682 passed, 14 skipped. Function-scoped fixtures are
  cached per test for the same reason.
- **Read a tool by running it, not by reading it.** The `.res` hash was decoded
  by hand out of 44 bytes of IL and looked obviously right; it was wrong,
  because opcode `0x5D` is signed `rem`, not `rem.un`. Loading the assembly and
  calling the method by reflection settled it in one command after days of
  guessing. Where a shipped tool implements the thing you are reverse
  engineering, invoke it.
- **A replacement keeps everything about the original it is not editing.**
  Encoding, flags, footer, tile grid, declared size. Re-encoding a BMPRES atlas
  page as IMTC32 produced a file the game silently ignored — the failure has no
  error anywhere, it just does not appear. `aim.from_image_like` exists for
  this; plain `from_image` is for genuinely new assets only.
- **Advice attached to a diagnostic is part of the diagnostic.** `PRJ002` told
  authors to delete `inifiles/items.ini` because it is identical to stock — and
  deleting it makes the game refuse to list the mod, which is what `PRJ004`
  exists to prevent. The two rules contradicted each other for a while.
- **A raw reference and a resolved path are different types.** A scene's
  `textures/x.dds` is relative to that scene; a vpath is not. Kept in one field
  they get used interchangeably, and the half that reads the VFS fails. The
  tell was a stored boolean `resolved` — something had done the resolution and
  discarded the answer.
- **Do not join on a name that is not a key -- and a row index is not a key
  either.** Mesh names repeat once per variant, so a name-keyed lookup returns
  an arbitrary sibling: it reads correctly, it type-checks, and it silently
  showed one variant's material while writing to another. The row-index form of
  the same mistake turned the viewport black, because the parts table lists the
  *visible* calls and the viewport holds *all* of them -- 10 against 254 on
  PlayerShip. Three times now: `mesh_for`, `preview_shading`, `set_isolated`.
- **A finding about one subtree is not a finding about the tree.** "Loose
  `3DView/` is never read" got generalised to the whole loose tree and quietly
  corrupted the index, atlas validation and the Textures tab. Same shape as the
  `SCN001` mistake below: the evidence was real, the scope was invented.
- **Windows will not let you replace a file you still have open.** POSIX will,
  so this class of bug is invisible until the suite runs on Windows — and it
  has now cost twice. Anything that ends in `os.replace()` onto a path the
  process might also have open needs the handle released first,
  deterministically, not left to the garbage collector.
- **"Its own handle" is not the same as "every handle".** The second time was
  a *different* `Mod` object on the same folder: the mod picker builds one per
  discovered mod and keeps them for its labels, and `is_listable()` opened the
  zip to answer one question about one path. Closing `self` was not enough.
  `_OPEN_ZIP_MODS` (a `WeakSet`) now tracks them and `_write_zip` closes every
  handle on the file it is replacing. Symptom to recognise: saving works on a
  mod with no `user_data.zip` and fails on one that has it.
- **Do not build a whole inventory to answer one question.** `is_listable()`
  called `files()` — walking the mod and opening its archive — to check whether
  one path exists. That is where the handle came from.
- **Python source is UTF-8; `read_text()` is not.** Without an explicit
  `encoding=`, it uses the locale codepage, which on a German Windows silently
  mangles every non-ASCII literal in a file you `exec`.
- **A test that fakes `sys.platform` is faking it for third-party imports too.**
  PyInstaller's `compat` reads it at import time and acts on it. Prime
  `sys.modules` before faking, or the fake escapes the test.
- **A test whose premise is "this machine lacks X" fails on the machines that
  have X** — which for a game-modding tool is every machine that matters. Stub
  the lookup instead of asserting on the developer's environment.
- **Qt worker callbacks.** Two separate failures: a callback connected to a
  plain callable runs on the *worker* thread, and a worker with no strong
  reference is garbage-collected before its queued callbacks are delivered — Qt
  then discards them silently, leaving the UI looking stuck. Both are fixed and
  explained at length in `app/dso_app/workers.py`. Do not simplify that module
  without reading it.
- **Byte-exact round-trip is a hard requirement**, not a nicety. Diff-against-
  stock depends on it, and it is the only honest test that a format is
  understood. Three bugs were found by round-trip failures and nothing else.
- **Preserve what you did not touch** — including bytes Ascaron's own packer
  left uninitialised. Zero-filling them produced 518 spurious differences.
- **Every library exception must derive from `DsoError`.** Four did not, and
  one malformed file could throw away an entire validation report. There is now
  a test enforcing it across the whole package.
- **A rule that could not run must not look like a rule that passed.**
  `Report.skipped` exists for this. `verify_all --only` reports `PARTIAL` for
  the same reason, and never "all checks passed".
- **A check that stops early reports the first cause as if it were the only
  one.** `_scene_rt` let an exception escape, so three malformed files hid 55
  unrelated round-trip failures for as long as the check aborted ahead of them.
  A per-file failure belongs in a counter, not in a raise — the whole point of
  a corpus check is the *whole* corpus.
- **Measure the finding before designing around it.** "16 malformed files, three
  defect classes" was really 3 files with one defect; the other 13 were a schema
  the parser already skips. Several rounds were spent deferring a decision that
  the measurement made small.
- **`SCN001` was once stated wrong** (LOD0 submesh count instead of submesh
  total) and produced 623 false positives on a real mod. The wrong version is
  preserved in `specs/scene.md` §6 next to the right one. Measure over the full
  corpus, not a truncated sample.
- **`tools/offline_test_runner.py` implements only part of pytest** on purpose,
  and `verify_all` falls back to it where pytest is absent. New tests must stay
  inside that surface: no `capsys`, no `monkeypatch.setattr(..., raising=False)`.
  Grow the test, not the runner.

## Two engine rules that fail silently

Both are in `specs/README.md` §5 with the evidence. They drive real code:

- A mod without `inifiles/items.ini` **is never listed by the game**, with no
  message. `Mod.create` writes it; `PRJ004` checks it; Deploy repairs it.
- A mod's loose `3DView/` **and `images/`** are **never read**. Only
  `user_data.zip` is. `ZIP_ONLY_ROOTS`, `PRJ005`, `PRJ006`, and Deploy.
  `images/` was added on 2026-08-15 after an in-game test, and it was the
  real reason the first texture edit appeared to do nothing.

## Layout

```
.github/   CI: ci.yml (PR gate: lint, tests on Linux+Windows, a real build)
           release.yml (tagged Windows release, refuses a mismatched tag)
specs/     file formats and findings -- specs/README.md is the overview
           bsd9.md is the newest (texture-slot semantics)
src/dsotools/
  errors.py      DsoError root
  vfs.py         layered overlay, measured archive precedence
  formats/       threedo shd aim sld a2d anim dds scene ini sounddb bsd9
  convert/       gltf (in + out), obj
  edit/atlas     coordinated .aim/.tex/.anim edits (replace, rescale)
  edit/meshview  scene -> draw calls, variant groups, layers, normal unswizzle
  validate.py    the diagnostic engine, stable codes + fixes
  project.py     Mod, deploy, .dsoproj, ZIP_ONLY_ROOTS
  index.py       SQLite index + reference graph
app/dso_app/
  session.py     ALL logic, no Qt -- start here
  main_window.py shell, menus, open_asset() routing
  workers.py     QThreadPool plumbing; read the docstring before editing
  viewport.py    Qt Quick 3D component
  settings.py    JSON config next to the exe; "do not show again" lives here
  frozen.py      PyInstaller-aware paths
  blinker_table.py / linked_assets.py / effect_editor.py / asset_preview.py
                 shared widgets
  resources/     icon.png (source) and icon.ico (built by tools/make_icon.py)
tabs/          project, models, textures, data, interface,
               scripting, audio, problems
cli/       the converters, importing the library
packaging/ PyInstaller spec, build driver, licence notice
tools/     verify_all check_app offline_test_runner validate_mod asset_index
           pyside_doctor spike_viewport drive_models_tab drive_scripting_tab
           chm_to_json exe_api_scan     -> src/dsotools/data/lua_api|lua_engine
           stock_baseline stock_missions -> src/dsotools/data/*.json
           make_icon                    rebuilds the app icon
           make_probe_mod make_music_mod make_staticimages_mod
                                        build the in-game test mods
           drive_audio_tab              plays a sound in the real tab
tests/     pytest; corpus tests skip without game data
docs/      ARCHITECTURE.md (the layering rules), this file (the record),
           TODOS.md (the queue), the two CLI manuals
specs/     the formats as evidence; modding_guide.md ships inside the app
```

Roughly, as of 2026-08-23: `src/dsotools` 15,700 lines, `app/dso_app` 15,900
(of which `session.py` 3,900 and `tabs/` 6,000), `cli/` 1,300, `tools/` 5,600,
`tests/` 13,800.
Tests outweigh the app, which is the intended ratio given that the widget layer
cannot be unit-tested at all.

**Where to start reading**, by task:

| You want to | Read |
|---|---|
| add a tab | `session.py` first, then `tabs/textures_tab.py` as the model |
| touch a format | the module docstring; they carry the measurements |
| understand deploy | `project.py` header, then STATE's Deploy section |
| change the viewport | `viewport.py` docstring — three Qt traps are named there |
| add a check | `validate.py`; give it a code, a severity and a fix |

## If you are running in a cloud session

PyPI and the Ubuntu archive are blocked by egress rules, so **PySide6 and
PyInstaller cannot be installed there**. GitHub over HTTPS *is* reachable. The
library, the whole test suite, `check_app.py` and the packaging *checks* all run
headlessly; the Qt app and the actual build do not. Game archives are reachable
through the device bridge, but `ds_3dtex.cpr` (1.5 GB) and `ds_3dobj.cpr`
(460 MB) exceed the staging caps — the other four stage fine, which is enough
for everything except the model and texture corpus checks.
