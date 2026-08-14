# Darkstar One — modding specifications

Everything this project has established about how *Darkstar One* (Ascaron, 2006)
stores and loads its data. Each document below is a format reference; this page
is the map that ties them together — where assets live, how the engine finds
them, and what a mod may and may not do.

Findings here are separated into **measured** and **inferred**, and corrections
are kept visible rather than edited away. A spec that quietly rewrites a wrong
number teaches nobody why it was wrong.

## The documents

| Document | Covers |
|---|---|
| [`scene.md`](scene.md) | **`WalhallaScene` XML — the model↔material↔texture binding.** Also: archive precedence, path resolution, audio, the mod folder format, and the Lua API surface |
| [`3do_shd.md`](3do_shd.md) | `.3do` render meshes, `.shd` stencil shadow volumes, glTF interchange |
| [`aim.md`](aim.md) | `.aim` images — every encoding, plus the `IMSL` compressed container and the SLD codec |
| [`interface_formats.md`](interface_formats.md) | **The user interface.** `.screen` layouts and the controls on them, `.anim` drawables, `.tex` atlas indexes — the chain from a screen to the pixels it draws (Ascaron's A2dLib resources) |
| [`mod_packaging.md`](mod_packaging.md) | **What a mod may contain and where each file must live** — the script loading order, mission types and lifecycle, compiled bundles, string tables, and what still has to go into the game installation |
| [`lua_api.md`](lua_api.md) | **The Lua scripting API** — how the official reference is turned into a database the editor can use, and what the reference does not cover |
| [`string_tables.md`](string_tables.md) | **`.res` string tables — the only way a mod can put its own text on screen.** The layout, the hash, and why the ids are not recoverable |
| [`sound.md`](sound.md) | **The sound database.** Nested groups, why a sound's identity is its group path, and how to read a WAV or MP3's own rate and length back out |
| [`asset_paths.txt`](asset_paths.txt) | `.aim` paths extracted from `DarkStarOne.exe` — the paths as the engine requests them |

`scene.md` is the one to read first if you only read one. It answers the question
the other specs left open: a `.3do` carries no material or texture reference at
all, and the binding lives in 1,040 plain-XML scene files the game ships
uncompiled.

---

## 1. The archives

The six `.cpr` files in the game root are **plain ZIP containers** — `PK\x03\x04`,
deflate, standard central directory, even UNIX timestamp extra fields. Any zip
tool opens them; nothing needs extracting. All six open in about 20 ms and hold
12,767 files between them.

| Archive | Contributes |
|---|---|
| `ds_3dgen` | `3DView/*.xml` scenes, `blender/*.bsd9` shaders, `lua/`, `animations/`, `ActionCams/` |
| `ds_3dobj` | `3DView/objects/*.3do`, `*.shd` |
| `ds_3dtex` | `3DView/textures/`, `ClusterTextures/`, `Textures_Planets_{Hi,Low}/` |
| `ds_3dadd` | per-scene overlays and additions |
| `ds_add` | `inifiles/`, further overlays |
| `ds_interface` | UI: `scripts/`, `images/`, `sfx/Samples/` |

### They are overlays of one namespace, not six namespaces

This matters more than it sounds. `3DView/` is assembled from several archives at
once: **11,386 distinct virtual paths, of which 1,368 exist in more than one
archive.** `3DView/BlackHole.xml` is present in three.

Precedence, **measured** with Process Monitor on the running executable, highest
first:

1. Loose files in the game install directory
2. `ds_add`
3. `ds_3dadd`
4. `ds_3dtex`
5. `ds_3dobj`
6. `ds_3dgen`
7. `ds_interface`

At startup the archives are mounted in the *opposite* order, so the registry is
LIFO: an archive mounted later overrides one mounted earlier. `ds_patch`,
`ds_loca` and `ds_main` are absent from the captured Steam build and are placed
by **inference** from the same rule.

Of the 1,368 shadowed paths, 1,352 (98.8%) are byte-identical across every copy;
only 16 genuinely differ. Precedence is therefore mostly academic — but those 16
are exactly the files where guessing wrong gives you the wrong asset.

### Case

Windows is case-insensitive and the archives are not internally consistent
(`3DView` vs `3dview`, `TexPage_8_2.aim` vs lowercase references). Fold case on
lookup, but preserve the original spelling — that is what has to be written back.

---

## 2. How a reference resolves

References inside scene XML are relative. They resolve in this order:

1. **Scene-relative** — `<directory of the scene file>/<ref>`
2. **`3DView/`-relative** — `3DView/<ref>`

Rule 1 before rule 2 raises model resolution from 90.6% to 97.8% across the
corpus, so both are required. Scenes with private asset folders
(`3DView/Generator/objects/…`) depend entirely on rule 1.

Sound XML uses **backslashes** where scene XML uses forward slashes, and a
`%MOD%` prefix meaning "the mod root". Normalise both.

---

## 3. The asset chain

```
3DView/<Scene>.xml            WalhallaScene — the binding
  └─ Object .?AVCMesh@@
       ├─ model  →  objects/<name>.3do        geometry, LODs, submeshes
       │            objects/<name>.shd        stencil shadow volume (optional)
       └─ EffectContainer (one per submesh)
            ├─ shader →  blender/<name>.bsd9  D3D9 effect; names its texture slots
            └─ textures → textures/<name>_col.dds   colour
                          textures/<name>_nrm.dds   normal
                          textures/<name>_lgh.dds   light/gloss
```

The `_col` / `_nrm` / `_lgh` suffix convention is a naming habit, not something
the engine enforces — the actual slot assignment is positional, per shader.

**The invariant worth knowing:** a mesh's `EffectContainer` count equals the
mesh's **total submesh count across all LODs** — every one of 9,806 mesh
references on a full installation, bar two that are a defect in the shipped data
(`specs/scene.md` §6).
The two exceptions are a defect in Ascaron's shipped data. This is the check that
catches the most common export failure: a DCC tool that merges submeshes produces
a `.3do` which passes every self-consistency check and renders wrong in game,
because the submesh split is how the engine assigns materials within one mesh.

> **Correction, kept on purpose.** This invariant was first stated as
> "EffectContainer count == LOD0 submesh count", measured at 100% over a
> truncated sample. It was wrong, and produced 623 false positives on a real
> mod. See `scene.md` §6 — the wrong figure is preserved there alongside the
> right one.

### UI assets are a separate chain

```
scripts/*.tex             atlas index: name → page + rectangle
  └─ images/TexPage_*.aim the atlas page the game actually draws from
scripts/*.anim            drawables, referencing names from the .tex
images/<other>.aim        the packer's *sources* — not read at runtime
```

Everything lives in `images/`, so the folder does not tell them apart. **The
`.tex` does**: whichever `.aim` an index names is a page, and the other 27 are
leftovers. Sprite records are *named* `images\Foo.aim` because that was the
packer's input filename; for a handful of them (`Auftraege.aim`,
`All_out_icon.aim`, the `Background_Blaugrau*` set) that input also shipped.
Editing one of those changes nothing on screen — the pixels the game draws are
the copy composited into the page.

Replacing a standalone `images/*.aim` changes nothing on screen. The edit has to
go into the atlas page at the rectangle the `.tex` names. Rescaling a page
without updating the rectangles is the classic silent breakage.

### `TexPage_<format>_<index>` — the first number is the codec

The 37 files in `images/` are not 37 pages. The name is
`TexPage_<format>_<index>`, and only ten are referenced by an index:

| Prefix | Encoding | Example |
|---|---|---|
| `_0_` | `BMPRES` — an embedded 24-bit BMP | `TexPage_0_4.aim`, 512×256 |
| `_1_` | `IMJPG24A` — JPEG plus a 1-bit alpha mask | `TexPage_1_3.aim`, 256×256 |
| `_8_` | `IMTC32` — uncompressed BGRA | `TexPage_8_2.aim`, 1024×1024 |

The same index exists in several formats at different sizes (`TexPage_0_4`,
`TexPage_1_4`, `TexPage_8_4`), but a `.tex` names exactly one of them and the
rest are unreferenced. So an `images/TexPage_*.aim` that *looks* like a copy of
the page you edited, minus your edit, is a leftover variant nothing reads.

**This matters when writing.** `aim.from_image_like()` preserves the page's own
codec, its `flags`, and the `footer_extra` `(0,0,1)` that every shipped page
carries; eight of the ten referenced pages then round-trip byte-identically.

`IMJPG24A` (2 of the 10) can be read but not written. Those pages are saved as
`IMTC32` instead — lossless, alpha intact, and the codec six of the other pages
already use. **The engine reads the codec from the chunk tag, not the
filename**, so `TexPage_1_3.aim` holding `IMTC32` loads normally. The
substitution is deliberate and reported (`AtlasPage.recoded_to`), never silent:
the file stops matching stock byte-for-byte, and that is worth knowing.

---

## 4. Format inventory

| Ext | Count | Magic | What it is | Status |
|---|---|---|---|---|
| `.cpr` | 6 | `PK\x03\x04` | archive | plain ZIP |
| `.xml` | 1,040 | text | WalhallaScene | [documented](scene.md) |
| `.3do` | ~2,900 | — | render mesh | [documented](3do_shd.md) |
| `.shd` | — | — | stencil shadow volume | [documented](3do_shd.md) |
| `.aim` | — | `IMxx` | UI image | [documented](aim.md) |
| `.tex` `.anim` | — | `SH_TEXPG` / `SH_ANIM` | atlas index, drawable | [documented](interface_formats.md) |
| `.dds` | ~2,400 | `DDS ` | DXT1/DXT5, mipped | public format |
| `.ini` | 68 | text | game data tables | plain text, Ascaron dialect (see below) |
| `.wav` `.mp3` `.bik` | — | RIFF / MPEG / `BIK` | audio, video | standard |
| `.lua` | 64+ | text | effect and mission scripts | plain source |
| `.bsd9` | 232 | `XF  90.1` | D3D9 shader/effect | **documented** — `bsd9.md` |
| `.cat` | 698 | `tAkc00.1` | camera/spline tracks | **not analysed** |
| `.res` | 76 | — | string tables | `Xml2ResConverter.exe` round-trips them |
| `.screen` | 83 | `SH_SCRN` | UI layout | **documented** — `interface_formats.md` 4; element tree derived in 4.3, class-block internals open |
| `.gr2` | 80 | Granny | character mesh + animation | **proprietary — out of scope** |

`.gr2` is the only genuinely closed format in the set. It is confined to 80
character models, loaded through `granny2.dll`, and there is no open writer.

### The INI dialect is not `configparser`'s

Real files contain trailing `;` comments on value lines, duplicate keys,
duplicate sections, and cp1252 umlauts. `configparser` raises on some of these
and silently mangles others (`Radius = 15.403557 ; Radius der Huellkugel` comes
back with the comment attached to the value). Parse it as its own dialect and
round-trip it byte-for-byte.

---

## 5. Mods

Mods live in
`Documents\Ascaron Entertainment\Darkstar One\Customization\<ModName>\`. The
selected mod is recorded one level up in `mod.ini` (`load_mod = original` means
none). The manifest inside each folder is `darkstarmod.ini`:

```ini
[darkstarmod]
mod_name = Example Mod v2.8.4
mod_desc = 5 neue Sidequests. ...
```

### Two rules that fail silently

**A mod without `inifiles\items.ini` is invisible.** A folder with a perfectly
valid `darkstarmod.ini` does not appear in the game's mod list unless it also
contains `inifiles\items.ini`. Established by construction with four test mods;
corroborated by the executable's string table, where `\inifiles\items.ini` sits
interleaved with the manifest keys and a `bad_data` rejection marker. The loader
appears to treat a missing `items.ini` as malformed mod data and skip the folder
without reporting anything. Consequence: a texture-only or model-only mod is
undistributable unless it also ships a copy of `items.ini`.

**Only `user_data.zip` is read; a mod's loose `3DView/` tree is not.** Tested in
game with probe mods carrying the same file at the same virtual path, differing
only in delivery:

| Probe | In `user_data.zip` | Loose in the mod root |
|---|---|---|
| Hull texture replaced with magenta | hull renders magenta | **no change** |
| Scene XML deliberately broken | **game crashes on load** | loads fine, no effect |

The second row settles it: a crash cannot be mistaken for "loaded but visually
identical", so the loose copy is *not read*, rather than read-and-ineffective.
Ascaron's own tutorial mod ships a loose `3DView/` and no `user_data.zip` — that
folder is the authoring workspace for `3doConv.exe`, not runtime content.

**`images/` behaves the same way**, established the same way on 2026-08-15: an
edited atlas page deployed loose did nothing in game, and the identical file
placed in `user_data.zip` appeared immediately. This is the whole reason a
first real texture edit looked like a no-op.

Load order for a running game, highest priority first:

1. Loose files in the game install directory — and for `lua/` and the other
   roots below, the *only* place they exist
2. `user_data.zip` in the active mod's root
3. The `.cpr` archives (in the order given in §1)
4. A loose folder in the mod directory — **never loaded**

`inifiles/`, `sound/`, `scripts/`, `strings/` and savegames *are* read loose
from the mod folder. `3DView/`, `images/` and `staticImages/` have to go into
the zip — every one of those three established by its own in-game probe, and
none of them by inference from the others.

| Root | Loose | Established by |
|---|---|---|
| `inifiles/` `sound/` `scripts/` `strings/` | **read** | the tutorial mod and `items.ini` |
| `3DView/` | **never read** | probe mods; a broken scene crashes from the zip, does nothing loose |
| `images/` | **never read** | an edited atlas page: invisible loose, visible from the zip |
| `staticImages/` | **never read** | a probe mod pair; an edited `Starmap.dds` did nothing loose and appeared from the zip |

`staticImages/` was carried as **untested** for as long as it was unmeasured,
and that was the right call: the assumption actually in force — that anything
which is not `3DView/` is read loose — turned out to be **wrong**. The suite had
been deploying `staticImages/` content loose, which is to say writing files the
engine never opens.

The pair was built by `tools/make_staticimages_mod.py`, which ships the same
payload two ways so a single run answers the question in both directions.

### Some content cannot be delivered from a mod folder at all

A mod folder is reversible: delete it and the game is stock again. Part of what
a mod may need to change is not reachable from there.

**No `.cpr` archive holds a single `lua/` entry.** The shared mission libraries
— `MissionLib.lua`, `BattleLib.lua`, `CameraLib.lua` and the compiled
`missions.bin` — exist **only as loose files in the game installation root**,
and a mission script imports them by a path resolved against that root:

```lua
source "lua/mission/MissionLib.lua"
```

Seven content roots are loose-only in the same way — present in the install
directory, absent from every archive:

| Root | Loose files | What it is |
|---|---|---|
| `particlescripts/` | 1,501 | particle definitions |
| `interface3d/` | 611 | 3D interface scenes |
| `objectfieldscripts/` | 342 | object field definitions |
| `lua/` | 13 | mission libraries and `missions.bin` |
| `effects/` | 23 | effect definitions |
| `frontend/`, `strings/` | 3 each | front end, string tables |

`video/subtitles/` is loose-only too, and is not even indexed by the asset VFS.

**So a mod that changes them ships two parts.** The worked example seen in
the wild: one archive goes into `Customization\`, and a
second archive of libraries has a readme saying *"copy into
game root"*. It carries nine `lua/mission` files and three
`video/subtitles/*.xml`.

**A mission script is not a library, and goes the normal way.** Ascaron's
tutorial mod puts its mission scripts in the mod folder's `scripts/`, read
loose like `inifiles/`. Note the collision: `scripts/` inside an *archive* is
A2dLib interface resources (`.screen`, `.anim`, `.tex` — see
[`interface_formats.md`](interface_formats.md)), while `scripts/` inside a
*mod folder* is mission Lua. Same name, unrelated contents.

**This is the one irreversible thing a modding tool can do.** Copied by hand,
the displaced stock `MissionLib.lua` is gone — no archive copy, no backup,
nothing to restore from short of verifying the game's files through Steam. On
the machine this was written on, all twelve of that mod's root files were
already in place and byte-identical, including `lua/mission/sync.ffs_lock`, a
FreeFileSync lock file the modder shipped by accident.

`dsotools.rootfiles` therefore treats the installation as something to be
written to *with a ledger*: a mod's payload lives in `<mod>/root/` mirroring
the game root, the manifest is recorded in the project's `.dsoproj`, whatever
is displaced is copied into `<game>/.dso_backup/`, and `<game>/.dso_installed.json`
records who owns what — beside the files it describes, so an uninstall still
works from another machine or a fresh copy of the tool.

### Audio

No reverse engineering required — everything is a standard format. Sound
definitions live in an `ASE_Database` XML document; a mod adds its own as
`user_sounds.xml` at the mod root:

```xml
<Group Name="USER" Volume="2.0" Priority="10">
  <Stream  Name="Lopster" Resrc="%MOD%sound\music(stream)\grp_USER\Lopster.mp3"
           Channels="2" Duration=":11029410" Freq="44100" />
  <Sound2D Name="..." Resrc="..." ... />
</Group>
```

The only real work is keeping the XML and the files on disk in agreement in
**both** directions — declared-but-missing, and present-but-unreferenced. A path
typo puts the same sound in both lists at once, which is what turns it from a
guess into a diagnosis. Both directions found real bugs on the first mod tried.

---

## 6. Editing rules

Three constraints govern every tool in this repository, and they are worth
adopting in any other tool too.

**Round-trip byte-for-byte.** `parse(x).to_bytes() == x` for every format that
has a writer. Not for elegance: diff-against-stock is the feature modders
actually want, and a serialiser that reformats turns every one-line edit into a
whole-file diff. It is also the only honest test that a format is understood —
three separate bugs here were found by round-trip failures and by nothing else
(`.tex` uninitialised tail bytes, a scene file with no trailing newline, DXT5
alpha weights wrong in two implementations the same way).

**Preserve what you did not touch.** Changing one texture reference changes one
token. Uninitialised bytes left by Ascaron's own packer get copied through, not
zero-filled — that alone accounted for 518 spurious differences before it was
fixed.

**Validate against silent failures, not taste.** Every rule worth having
corresponds to something the engine ignores, crashes on with no message, or
renders subtly wrong. A validator that also reports style gets switched off, and
the real findings go with it.

---

## 7. Not established

Open, and honestly so:

- `.bsd9` **blob** internals — the container and its texture-slot names are
  decoded (`bsd9.md`); the compiled effect inside it, which holds the
  `<Parameters>` semantics, is not
- `.cat` camera/spline tracks
- `.dssc` savegame containers
- `.screen` **class-block internals**: the container, the element records,
  the rectangles and the resource references are decoded and round-trip
  byte-exactly (`interface_formats.md` 4), but the fields *inside* a class block --
  colours, alignment, font metrics, `CButton`'s 536-byte per-state block --
  are not mapped (`interface_formats.md` 4.7)

  *Resolved 2026-08-18, and the correction is worth keeping visible:* this
  entry used to say the element **nesting** was not established. It is still
  true that no field holds it -- 551 candidate offsets survive the
  flat-screen filter and none fits -- but the tree itself is now derived from
  the widget structure and checked against the top-level count the header
  does give, on 83 of 83 files (`interface_formats.md` 4.3)
- `.gr2` — proprietary Granny; recommended out of scope
- The `Generator_0` → `Generator` scene-folder fallback rule: observed, not
  explained
- `Duration` in the sound XML: a string with a leading colon, format unknown
- `Sound3D` is expected by symmetry with `Sound2D` but was never observed
