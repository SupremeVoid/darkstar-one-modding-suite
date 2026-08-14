# Ascaron `WalhallaScene` — scene graph, materials and texture binding

**Status: measured, and the most complete spec here.** The scene graph, material and texture binding are closed; §4.3 adds what makes a scene a *ship*. What is still open is marked `[open]` where it appears.

This document closes the biggest open question in `specs/3do_shd.md`: where
the engine gets a model's textures, given that `.3do` contains no material or
texture reference of any kind.

**Answer: it does not come from the model. It comes from the scene.**

A `.3do` is a *resource* — geometry and nothing else. The thing that places that
geometry in the world, assigns it a shader, sets material constants and binds
textures to it is a separate **scene file**, and the game ships those as **plain,
uncompiled XML**.

---

## 1. Where the scenes are

`WalhallaScene` XML files, extracted from the `.cpr` archives:

| Archive | Scene files |
|---|---|
| `ds_3dgen` | 982 |
| `ds_3dadd` | 42 |
| `ds_add` | 16 |
| **Total** | **1,040** |

They sit at `3DView/<Name>.xml`, alongside per-scene subfolders
(`3DView/Generator/objects/`, `.../textures/`, `.../animations/`) for scenes that
carry private assets.

The official modding tools ship a matching sample and its compiler:
`Modding/Tutorial/3DView/Container.xml` plus `Modding/Tools/converter/3doConv.exe`.
So this is a supported, documented-by-example authoring path, not an internal
format.

---

## 2. Structure

```xml
<WalhallaScene Date="04/07/06" Time="16:50:26" Version="2.00">
  <Object Type=".?AVCWorldRoot@@" Flags="98304">
    <AttachedObjects>
      <Object Type=".?AVCTransformationNode@@" Flags="98304" Name="bodys">
        <AttachedObjects>
          <Object Type=".?AVCTransformationNode@@" Name="body_0">
            <AttachedObjects>
              <Object Type=".?AVCTransformationNode@@" Name="main">
                <AttachedObjects>
                  <Object Type=".?AVCMesh@@" Name="main_" Resrc3DO="objects/main_.3do">
                    <AABB Center="…" SpcExt="…" />
                    <EffectContainer Path="blender/mat_biotechanim.bsd9">
                      <Material> … 5 rows of floats … </Material>
                      <Parameters>
                        <Float semantic="Bumpiness" value="+1.000000" />
                        …
                      </Parameters>
                      <Textures Number="1">
                        textures/playership_biotechanim_00_lgh.dds
                      </Textures>
                    </EffectContainer>
                    <EffectContainer Path="blender/mat_main.bsd9">…</EffectContainer>
                  </Object>
```

The `Type` attribute is a raw MSVC RTTI decorated name — `.?AVCMesh@@` is
`class CMesh`. That is a C++ ABI artefact, which means the exporter serialised
`typeid(*obj).raw_name()` directly.

### 2.1 Object types, counted across all 1,040 scenes

| Type | Count | Role |
|---|---|---|
| `CTransformationNode` | 28,389 | scene-graph transform |
| `CMesh` | 13,382 | **binds a `.3do` to materials/textures** |
| `CKeyframeAnimator2` | 2,273 | keyframe animation |
| `CGlowObject` | 1,458 | additive glow sprites |
| `CWorldRoot` | 1,040 | one per scene |
| `CBlinkerGroup` | 621 | navigation/hull blinker lights |
| `CDistortionObject` | 538 | heat-haze / distortion |
| `CLODSelector` | 444 | distance-based LOD switching |
| `CLuaScriptAnimator` | 356 | Lua-driven animation |
| `CShineObject` | 298 | specular flare |
| `CShieldMesh` | 254 | shield hull |
| `CHdrLighting` | 254 | HDR light source |
| `CCamera` | 166 | scene camera |
| `CWalGrannyModel` / `CGrannyCharacterInstance` | 80 each | Granny character models |
| `CPointLight` | 77 | point light |
| `CZPassModifier`, `CDebrisRenderer`, `CGroundFogModifier`, `CKeyframeAnimator` | 1 each | one-off |

`CGlowObject`, `CShineObject`, `CShieldMesh` and `CDistortionObject` also carry
their own `EffectContainer` children. Any parser that scans for
`<EffectContainer>` textually rather than as a *direct child* of a `CMesh` will
attribute them to the wrong object — this was measured and confirmed as a real
failure mode (see §6).

### 2.2 `EffectContainer`

One per submesh, **in submesh order**. Contains:

- `Path` — a `.bsd9` shader/effect file, e.g. `blender/mat_main.bsd9`. 173 distinct
  shaders are referenced across the corpus.
- `<Material>` — 17 floats laid out as 4 RGBA-ish rows plus a scalar. Consistent
  with D3D9 `D3DMATERIAL9`: diffuse, ambient, specular, emissive, then specular
  power (`+200.0` for `mat_main`, `+20.0` for `mat_biotechanim`).
- `<Parameters>` — named float semantics passed to the shader.
- `<Textures Number="N">` — whitespace-separated texture paths, positional. Slot
  meaning is determined by the shader, not by the file.

### 2.3 Shader parameter semantics observed

| Semantic | Occurrences |
|---|---|
| `Bumpiness` | 23,950 |
| `Reflectivity` | 23,950 |
| `EmissiveFactor` | 23,950 |
| `DetailRepeat` | 23,950 |
| `DetailIntensity` | 23,950 |
| `Roughness` | 23,895 |
| `ColoredReflection` | 6,765 |
| `FresnelPower` | 2,289 |
| `FresnelBias` | 2,289 |
| `FogBlend` | 1,946 |

The first six appear on essentially every material; the rest are shader-specific.
A `<Float semantic="X" />` with no `value` attribute means "default", and occurs
regularly — a parser must treat a missing `value` as valid.

---

## 3. Textures

Standard **Microsoft DDS**. No Ascaron container, no `.aim` wrapper.

Verified on `ds_3dtex/3DView/textures/`: magic `DDS `, FourCC `DXT5`,
1024 × 1024, 11 mipmap levels. Both DXT1 and DXT5 appear.

### 3.1 Suffix convention

| Suffix | Count | Meaning |
|---|---|---|
| `_col` | 446 | albedo / colour |
| `_nrm` | 395 | normal map |
| `_lgh` | 368 | light / gloss / specular mask |
| `_flat` | 139 | flat-shaded variant |
| `_dirtseg` | 62 | dirt / decal segment |
| `_alph` | 4 | alpha-only |
| `_lght` | 9 | misspelling of `_lgh`, present in shipped data |

**The shader names its own slots** — see `bsd9.md`. `mat_main.bsd9` declares
`t_Color`, `t_Light`, `t_Normal`, `t_Reflection`, which is the order this
section used to state as a convention. The suffixes above remain the fallback
for shaders whose slot names are generic (`tex0`) and for the 466 effects
whose shader is not in the installation.

Do not infer the normal map from the `_nrm` suffix where the shader is
readable: a five-slot family feeds a `_nrm` texture to `t_SpecialMap` as well,
and 841 submeshes were shaded with the wrong one before this was decoded.

A handful of `<Textures>` entries reference `.aim` rather than `.dds`
(e.g. `color.aim`), so a texture loader must accept both.

---

## 4. Path resolution

Texture and model references inside a scene are relative, and resolve in this
order:

1. **Scene-relative** — `<directory of the scene file>/<ref>`
2. **`3DView/`-relative** — `3DView/<ref>`

Applying rule 1 before rule 2 raised model resolution from 90.6% to 97.2% on the
corpus, so both are required. Scenes with private asset folders
(`3DView/Generator/objects/…`) depend entirely on rule 1.

### 4.0.1 Writing a reference is not the same as reading one

A reader may be generous; a writer must not. `dsotools.vfs` tries a third
candidate after the two above — the reference as a bare virtual path — because
it costs nothing when reading. **That candidate is this project's convenience,
not a measured engine rule**, and the corpus says so: across all 1,006 stock
scenes, **0 of 45,322 resolving references need it** [data]. Every one resolves
under `3DView/` or the scene's own folder.

So anything that *writes* a reference into a scene has to be stricter than the
reader — `Vfs.reference_for(..., strict=True)` drops that candidate. A texture
named as `staticImages/Starmap.dds` resolves perfectly in the suite's own
viewer while resting on a rule the engine has never been *observed* to follow.
Whether it also fails in game is untested [open] — the point is that the suite
must not be the only thing that makes it work, and refusing costs nothing since
the texture can be added under `3DView/` instead.

`reference_for` is the inverse of resolution and **checks itself** rather than
computing a path and hoping: it produces a candidate spelling, resolves it back,
and only returns it if the result is the file that was asked for. That check is
load-bearing rather than defensive — a texture in a scene's private folder can
be shadowed by one of the same name under `3DView/`, and the short spelling then
silently means the other file. All 35,763 texture references in stock round-trip
through it [data].

### 4.1 The archives are overlays of one namespace, not separate namespaces

This matters more than it sounds.

`3DView/` is assembled from several archives at once:

| Archive | Contributes |
|---|---|
| `ds_3dgen` | `3DView/*.xml`, `blender/*.bsd9`, `lua/`, `animations/`, `ActionCams/` |
| `ds_3dobj` | `3DView/objects/*.3do`, `*.shd` |
| `ds_3dtex` | `3DView/textures/`, `ClusterTextures/`, `Textures_Planets_{Hi,Low}/` |
| `ds_3dadd`, `ds_add` | per-scene overlays and additions |

Measured across the six extracted archives: **11,386 distinct virtual paths, of
which 1,368 exist in more than one archive.** `3DView/BlackHole.xml` is present
in `ds_3dadd`, `ds_3dgen` *and* `ds_add`.

**The precedence is now measured.** Process Monitor on `DarkStarOne.exe` shows
the resolver probing the same fixed sequence for every single resource:

```
<game>\3DView\<path>          NAME NOT FOUND / PATH NOT FOUND   (loose override)
<game>\3DView\objects         SUCCESS                           (directory probe)
ds_add.cpr                    SUCCESS
ds_3dadd.cpr                  SUCCESS
ds_3dtex.cpr                  SUCCESS
ds_3dobj.cpr                  SUCCESS
ds_3dgen.cpr                  SUCCESS
ds_interface.cpr              SUCCESS
```

At startup the archives are *mounted* in the opposite order — `ds_interface`,
`ds_3dgen`, `ds_3dobj`, `ds_3dtex`, `ds_3dadd`, `ds_add` — so the registry is
LIFO: **an archive mounted later overrides one mounted earlier.**

Final precedence, highest first:

1. Loose files in the game install directory
2. `ds_add`
3. `ds_3dadd`
4. `ds_3dtex`
5. `ds_3dobj`
6. `ds_3dgen`
7. `ds_interface`

This confirms the add-on archives outrank `ds_3dgen`, which is exactly what the
contested files needed. `ds_patch`, `ds_loca` and `ds_main` are absent from the
captured Steam build and are placed by inference from the same LIFO rule.

### 4.1.1 How much it mattered

Hashing every shadowed path first:

| | Count |
|---|---|
| Shadowed paths | 1,368 |
| **Byte-identical across all copies** | **1,352 (98.8%)** |
| Content genuinely differs | **16** |

The 16 are `3DView/BlackHole.xml`, seven `BuzzleFlash_*_0.xml`,
`Tunnel2{,_low}.xml`, `Turret_{Cruiser,Freighter}_D{,_low}.xml`,
`3DView/lua/bossattack_a_0.lua` and `inifiles/ini_file.bin`. In every one,
`ds_3dadd` and `ds_add` agree with each other and differ from `ds_3dgen` — and
carry a `Date` three months later. So the measured order matches what the data
already implied.

`dsotools.vfs.Vfs.contested()` returns exactly this set, so the app can still
show which archive a file came from even though the winner is no longer in
doubt.

### 4.1.2 Other resolver behaviour visible in the same capture

Incidental, but each one is a fact the app would otherwise have had to guess:

* **Every lookup is issued twice**, back to back, identical path and result.
  Two code paths, or an exists-then-open pattern. Harmless, but it means a
  ProcMon trace has double the entries you expect.
* **Shadow meshes have their own search path.** A `.shd` is probed at
  `3DView\objects\X.shd`, then `Effects\FX_StencilShadow\3DView\objects\X.shd`.
  So the stencil-shadow effect can supply its own shadow geometry.
* **A doubled path appears**: `<game>\3DView\3DView\objects\X.shd`. An
  engine path-composition quirk — a `3DView` prefix applied to an already
  prefixed path. It always fails and the resolver moves on.
* **`.dssc` is a real extension.** Alongside each `.shd` probe the engine tries
  `X.dssc` (`energyshieldshapelod_77.dssc`, `glow_boostlod_48.dssc`). Not
  present in any shipped archive and not documented anywhere here — an
  unanalysed shadow-related format, possibly a compiled or cached variant.
  Added to §9.
* `NAME NOT FOUND` vs `PATH NOT FOUND` distinguishes "directory exists, file
  does not" from "directory does not exist" — useful when reading a trace, and
  it confirms the install ships a loose `3DView\objects\` directory but no
  loose `3DView\textures\`.

### 4.2 Unresolved references in stock data

Measured over a **full installation** (all six archives plus the loose tree),
which is what `tools/verify_all.py` re-measures on every run:

| | Count | Rate |
|---|---|---|
| Mesh references | 12,635 | 97.8% resolve |
| Texture references | 49,012 | 98.3% resolve |

An earlier figure of 97.2% / 98.1% appears in older notes; that was the
extracted-archive corpus, before the suite could read an installation directly.

Every failure follows one pattern: a scene named `Generator_0.xml` referencing
`Generator_0/objects/…` where the shipped folder is `Generator/`. A
numeric-suffix-stripping fallback is the obvious candidate rule but has **not**
been confirmed against the engine — it is a hypothesis, recorded as such.

---

## 4.3 What makes a scene a *ship*

A `WalhallaScene` is geometry and nothing else: no hull value, no speed, no
weapon definition. Everything that makes `Cruiser_A_0.xml` a ship the game can
fly against lives outside it, and the pieces are joined by **naming convention**
rather than by any reference either file contains.

### 4.3.1 The name is the join

Ship scenes are named `<Family>_<RaceLetter>_<NationObjectId>`, where the letter
is the race's position in the documented `Races` enumeration [code][data]:

| Letter | `RACE_*` | `Aliens` in `StarShip.ini` |
|---|---|---|
| `A` | `RACE_HUMAN` | Menschen |
| `B` | `RACE_MORLOK` | Morlok |
| `C` | `RACE_RAPTOR` | Raptor |
| `D` | `RACE_OCTO` | Octo |
| `E` | `RACE_WASP` | Wasp |
| `F` | `RACE_THUL` | Thul |
| `G` | `RACE_SKAA` | Säkaa |

`inifiles/StarShip.ini` holds **138 sections** named `StarShip<Class>_<Row>`,
each carrying `Aliens` and a `NationObjectId` documented as *"unique per nation
and ship"*. Composing the race letter with that id names a scene that exists for
**127 of the 138 rows** [data]. `Hunter` is the clean demonstration: class 016
has eight rows whose races and ids reproduce `Hunter_A_0`, `Hunter_A_1`,
`Hunter_B_0` … `Hunter_G_0` exactly.

The `<Family>` half comes from the class number by a mapping that is **in no
shipped file** — searched: no ini mentions `WINGTYPE`, a family name, or a scene
name. The executable holds it.

### 4.3.2 Hardpoints are nodes; the ini count is related but not identical

Weapon mounts are `CTransformationNode`s named by convention [data]:

* `Turret_0` … `Turret_N` — turret mounts
* `Gun_0` … `Gun_N` — fixed weapon slots

and `StarShip.ini` declares `TurretNum` and `SlotNum`. The two agree on **86 of
the 127** rows that resolve to a scene [data] — including every row of classes
003, 004, 033 and 040, and both `Hunter_A_*`. They **disagree** often enough
that the ini count must not be read as "the number of nodes": class 016's Morlok
row declares four turrets and no slots while `Hunter_B_0` carries four `Gun_`
nodes and no `Turret_` ones. So the ini figure is a *gameplay* count and the
nodes are placement; where they differ, the engine evidently reconciles them,
and how is **[open]**.

Other conventional node names in the same scenes: `boost_N` (engine glow),
`blinks_N` (blinker groups), `energyShield` / `energyShieldShape` (shield mesh),
`dist_boost` (distortion).

### 4.3.3 Where the rest of the statistics live

| Property | Where | Note |
|---|---|---|
| Hull, shields | `inifiles/HitPoint.ini` | **per race, not per ship** — `HullFactor<Race>`, `ShieldFactor<Race>` |
| Per-ship hull deviation | `StarShip.ini` `HitpointFactor` | scales the racial value |
| Speed, acceleration | `inifiles/Engines.ini` + `StarFlight.ini` | engines carry a `Name` alias other files reference |
| Cargo | `StarShip.ini` `BayNum`, `ContainerSize` | |
| Collision, camera | `StarShip.ini` `Radius`, `CollScale`, `CameraDist` | |
| Destruction | `StarShip.ini` `Explosion` | names an `Explosion.ini` entry |
| Which races populate a cluster | `inifiles/StarCluster.ini` | `Wkeit_<Race>` percentages, `Rasse` |
| Named roaming capital ships | `StarCluster.ini` `CruiserIndex0..3` | a *name* index (0–62, 127 = none) plus size and start system — not a model selector |

All 98 ini files live in `ds_add.cpr`; none is in `ds_3dadd`, which carries
`3DView/` and `CollisionObjects/` only [data].

### 4.3.4 A script cannot spawn a new one [code][data]

`NWing.Create` — the call the tutorial mod uses to put ships in space — takes
`WingType` and `Race`, both **constants from fixed enumerations** (11 wing
types, 7 races). No file name, model name or scene name enters from Lua. Every
other spawn in the reference is the same shape: `NCanyon.CreateShip` takes a
`Look` constant, `NObject.CreateSatellite` a race.

**Nor is the wing table data-driven.** `WingInfo.ini` defines formation
*offsets* and an `AiState`, not which ship a wing type flies; no shipped file
maps `WINGTYPE_*` to anything. Adding a twelfth wing type is not possible from
outside the executable.

**Nor is there an undocumented way in.** The executable registers 219 functions;
30 of them are absent from the 2006 reference — `NShip.CreateWreck`,
`NStarSystem.List`, `NCanyon.CreateSpeedBoost` and 27 others [data] — and not
one takes an asset name. Searching every documented parameter, exactly **one**
does: `NNPC.Create`'s `Model`. Its example passes `"Ratsmitglied2"` and stock
ships `MortokRatsmitglied2.xml` — the same `<Race><Name>` composition, with
`Ratsmitglied1` and `Sicherheitschef` each appearing under several races, which
is what composition predicts and a flat name list does not [data]. Whether a
mod's own `<Race><Name>.xml` would load that way is **[open]**; it needs one
probe mod.

So a new ship *type* is not reachable. Two things are:

* **Overriding an existing one** — replace `Cruiser_A_0.xml` or the `.3do` it
  names, and every Menschen cruiser of that id changes. Fully supported.
* **A new row in an existing class**, in principle: `StarShip016_008` with
  `NationObjectId = 2` plus a `Hunter_A_2.xml`, spawning as an ordinary
  `WINGTYPE_HUNTER`. This rests on the engine enumerating the ini rows rather
  than holding a compiled count, which is the same **[open]** question as any
  other added ini section — see `TODOS.md`. It is the most valuable version of
  that probe, because a positive answer means new ships without touching the
  executable.

## 5. Other formats encountered

| Ext | Count | Magic | What it is | Status |
|---|---|---|---|---|
| `.xml` | 1,040 | text | WalhallaScene | **documented here** |
| `.dds` | ~2,400 | `DDS ` | DXT1/DXT5 textures, mipped | public format |
| `.bsd9` | 232 | `XF  90.1` | Ascaron D3D9 shader/effect | **documented** — `bsd9.md` |
| `.cat` | 698 | `tAkc00.1` | camera/spline tracks (`ActionCams/`) | not analysed |
| `.gr2` | 80 | Granny | Granny 3D character mesh + animation | **proprietary** |
| `.curves` | 2 | `//<CurveExportScene` | animation curves, text | trivially readable |
| `.lua` | 64+ | text | effect and mission scripts | plain source |
| `.res` | 76 | — | string tables; `Xml2ResConverter.exe` round-trips them | not analysed |
| `.ini` | 68 | text | game data tables (`ds_add/inifiles/`) | plain text |

`.gr2` is the only genuinely closed format in the set. It is confined to 80
character models, loaded through `granny2.dll`, and there is no open writer.
Recommend documenting it as out of scope rather than attempting it.

---

## 6. Verified invariant: EffectContainer count == total submesh count

The strongest cross-file rule found, and directly useful as a validation check.

**Claim.** For every `CMesh` object, the number of **direct-child**
`<EffectContainer>` elements equals `submesh_total` of the referenced `.3do` —
the number of entries in the root header's `(submesh_index, lod_index)` table at
0x48, i.e. every submesh of **every LOD**, not just LOD 0.

So a model with 2 submeshes across 3 LODs carries 6 `EffectContainer`s, and the
material assignment is per-LOD-per-submesh rather than shared across LODs.

**Method.** `submesh_total` was read from the `.3do` root header at 0x30, so the
check is independent of the mesh parser. Scenes were parsed with a real XML
parser so that `EffectContainer`s belonging to `CGlowObject` / `CShineObject` /
`CShieldMesh` / `CDistortionObject` siblings are correctly excluded.

**Result over 9,806 mesh references — a full installation:**

| | Count |
|---|---|
| EC count == `submesh_total` | 9,804 |
| **Mismatches** | **2** |

Two exceptions, and they are the same model in two scenes:
`objects/mainshape_20.3do` in `TunnelVersion1.xml` and `TunnelVersion1_low.xml`,
4 submeshes against 3 `EffectContainer`s. A defect in Ascaron's shipped data,
so `verify_all` tolerates it **by name** (`KNOWN_BAD`) and reports 9,806/9,806
rather than weakening the rule to a percentage that would hide a third.

**Correction.** An earlier draft of this section stated the rule as "== LOD 0
submesh count", measured over a truncated 2,762-mesh sample where it appeared to
hold at 100%. It does not hold: single-LOD files have `lod0 == total`, which
masked the difference. Applied to a large third-party mod the wrong rule
produced **623 false positives**. The rule above is the corrected one, measured
over the whole corpus. A validator that cries wolf is worse than none.

**Why it matters for modding.** Adding or removing a submesh in a `.3do` — which
a DCC tool will do silently on export — desynchronises the model from every scene
that references it. The mesh will still parse, still validate structurally, and
still round-trip byte-exactly. It will render wrong, or not at all. This is
precisely the class of failure `specs/3do_shd.md` warns about under
"structural validity is not visual correctness", and it is now mechanically
checkable.

**Caveat.** An earlier attempt at this measurement using regex chunking reported
84.8% agreement. That number was wrong: splitting the file textually at each
`CMesh` swept sibling objects' `EffectContainer`s into the preceding mesh. The
100% figure comes from proper tree parsing. Recorded because the failure mode is
easy to repeat.

---

## 6a. Audio

No reverse engineering required. Everything is a standard format.

| Location | Contents |
|---|---|
| `sound/sfx(2d)/`, `sfx(3d)/` | 348 WAV — RIFF/WAVE, PCM 16-bit, 44.1 kHz, stereo |
| `sound/music(stream)/`, `radio(stream)/` | 94 MP3 |
| `voice/DEU/`, `voice/ENG/` | 10,689 MP3 |
| `ds_interface/sfx/Samples/` | 59 WAV, plus 53 `.res` sound descriptors |
| `video/` | 165 `.bik` (Bink, `binkw32.dll`) |

The engine is Miles Sound System (`mss32.dll`, `.emp`/`.asi`/`.flt` plugins).

### The sound database is XML

Sound definitions live in an `ASE_Database` XML document — the game's own, and
`user_sounds.xml` at a mod's root for mod-added audio:

```xml
<ASE_Database>
  <DocumentProperties><Author/><Created/><LastSaved/><Version/></DocumentProperties>
  <Group Name="USER" Volume="2.0" Priority="10">
    <Stream  Name="Lopster" Resrc="%MOD%sound\music(stream)\grp_USER\Lopster.mp3"
             Channels="2" Duration=":11029410" Freq="44100" />
    <Sound2D Name="..." Resrc="..." ... />
  </Group>
</ASE_Database>
```

- `%MOD%` resolves to the mod root; an unprefixed path resolves against the game.
- Path separators are backslashes here, unlike scene XML.
- `Group` carries `Volume` and `Priority`; `Stream` is streamed, `Sound2D` is a
  loaded sample. A `Sound3D` variant is expected but was not observed.
- `Duration` is a string with a leading colon — format not established.

Measured on one large mod: 554 definitions, of which 511 resolve into the
mod, 28 into the game, and 15 do not resolve (see the mod audit).

**Consequence for the app.** An Audio tab can preview, replace and validate
without any format work — the only real logic is keeping `user_sounds.xml` and
the files on disk in agreement, in both directions: declared-but-missing, and
present-but-unreferenced. Both directions found real bugs on the first mod tried.

---

## 6b. Mod folder format

Mods live in
`Documents\Ascaron Entertainment\Darkstar One\Customization\<ModName>\`.
The selected mod is recorded one level up in `mod.ini`:

```ini
[DarkstarOne] ;
load_mod = original ;
```

`original` means no mod. The manifest inside each mod folder is
`darkstarmod.ini`:

```ini
[darkstarmod]
mod_name = Example Mod v2.8.4
mod_desc = 5 neue Sidequests. ...
```

### A mod without `inifiles\items.ini` is silently invisible

**This is the important one, and it fails silently.** A mod folder with a
perfectly valid `darkstarmod.ini` does **not** appear in the game's mod list
unless it also contains `inifiles\items.ini`.

Established by construction: four test mods with valid manifests and no
`inifiles/` did not appear; adding an unmodified stock `items.ini` was the only
change made. Corroborated by the executable's string table, where the mod-loader
strings sit in one contiguous group:

```
original
\inifiles\items.ini
mod_desc
darkstarmod
mod_name
bad_data            <- rejection marker
\darkstarmod.ini
DarkstarOne
load_mod
mod.ini
```

`\inifiles\items.ini` and `bad_data` are interleaved with the manifest keys, so
the loader appears to treat a missing `items.ini` as malformed mod data and skip
the folder without reporting anything.

The practical consequence for modders: a texture-only or model-only mod is
impossible to distribute unless it also ships a copy of `items.ini`. That is a
non-obvious requirement with a silent failure mode, so the app should check it
(`PRJ004`) and offer to add the stock file.

### Only `user_data.zip` is read — a mod's loose tree is not

Established in game with four probe mods carrying the same file at the same
virtual path, differing only in how it was delivered:

| Probe | In `user_data.zip` | Loose in the mod root |
|---|---|---|
| Hull texture replaced with magenta | hull renders magenta | **no change** |
| Scene XML deliberately broken | **game crashes on load** | loads fine, no effect |

The second row settles it. A scene broken on purpose crashes the game when
delivered in the zip and does nothing at all when delivered loose — and a crash
cannot be mistaken for "loaded but visually identical". So the loose copy is not
read, rather than read-and-ineffective.

This confirms the note in `specs/3do_shd.md` and **widens it**: the original
finding concerned `3DView\objects\`, but the whole `3DView/` subtree is ignored,
textures included.

It also resolves the apparent contradiction with Ascaron's tutorial mod, which
ships a loose `3DView/` and no `user_data.zip`: that folder never loads either.
It is the authoring workspace for `3doConv.exe`, not runtime content.

Consequence for the mod measured: its 425 loose duplicates of the zip
contents are confirmed dead — see the mod audit, `MOD-PKG-001`.

**A second consequence worth stating plainly:** malformed scene XML crashes the
engine on load. There is no graceful skip. Validation before deploy is therefore
not a convenience feature.

### Mod-relative paths known to the executable

```
\darkstarmod.ini
\user_data.zip
\inifiles\items.ini
\inifiles\ini_file.bin
\scripts\
\scripts\*.lua
\scripts\user_scripts.bin
\strings\user_strings.res
```

Two observations, the second weaker than the first:

* `\scripts\*.lua` is a wildcard, so a mod's `scripts/` folder is enumerated
  rather than probed for fixed names.
* **No mod-relative `\3DView\` string appears in this group.** That is
  suggestive for the open question in §9 about whether a mod's loose `3DView/`
  is read, but it is not proof — a 3DView path could be composed at runtime from
  fragments rather than stored whole. Treat it as a hint that agrees with the
  existing "never loaded" note, not as a second confirmation.

---

## 6c. `_low` scene twins

Many scenes ship as a pair: `PlayerShip.xml` and `PlayerShip_low.xml`,
`Container.xml` and `Container_low.xml`. Measured on `ds_3dgen`:

| | Count |
|---|---|
| Scene files | 1,006 |
| With a `_low` twin | 394 (39%) |
| Orphan `_low` with no base | 0 |
| Pairs sharing ≥1 **model** reference | 345 / 391 (88%) |
| Pairs sharing ≥1 **texture** reference | 153 / 391 (39%) |

The twins are independent documents with their own `EffectContainer` lists, so a
material or texture change in one does not propagate to the other. That much is
structural fact.

**When the engine chooses a twin is not established.** A Process Monitor capture
taken with an edited `PlayerShip.xml` active shows the *edited* binding being
requested and the stock one never requested at all — evidence that
`PlayerShip_low.xml` was **not** loaded in that session. Detail settings,
camera distance and object class are all plausible selectors; none is confirmed.

**Retracted.** An earlier draft of this section claimed the `_low` twin
explained an observed "my edit did nothing" result. It did not. The edit had
applied; the substituted texture simply looked almost identical to the original
(see the correction note below). The measurements above stand; the causal story
built on them was wrong and is withdrawn.

**Design consequence, downgraded to a precaution.** The app should surface the
twin relationship and offer to apply a binding edit to both, because they are
genuinely separate files and a modder will not expect that. But `SCN004` is a
*hint*, not a known-necessary rule, until twin selection is understood.

Note this is orthogonal to LOD selection *within* a scene (`CLODSelector` /
`LODDesc`). A scene can contain several LODs and still have a `_low` twin.

---

## 6d. Correction: the texture-substitution probe that proved nothing

Recorded because the mistake is easy to repeat and cost two rounds of testing.

A probe repointed `PlayerShip.xml`'s hull albedo slot from
`playership_body_00_col.dds` to `playership_body_00_lgh.dds`, chosen only
because that file was certain to exist. In game the hull looked normal, which
was read as "the edit did not apply".

It had applied. The two textures are **the same artwork**: identical UV layout,
identical shapes, the same gold-and-green palette — `_lgh` is a darker,
half-resolution (512² vs 1024²) lighting pass over the same atlas. Mean RGB
`(27, 29, 19)` against `(11, 14, 10)`. Substituting one for the other yields a
slightly dimmer version of the same ship, not an obviously wrong one.

Two lessons for any future in-game probe:

* **Pick a probe whose failure state is unmistakable**, not merely different.
  The magenta-texture probe worked precisely because no ship is magenta. A
  binary outcome (crash / no crash, present / absent) beats any judgement of
  degree.
* **Verify the probe's premise before running it.** Thirty seconds decoding both
  textures side by side would have ruled `_lgh` out before the game was launched.

The `_col`/`_lgh` naming convention in §3.1 is therefore stronger than "colour"
and "light": they are matched pairs over one UV layout, which is useful for the
viewer and worth knowing before designing any test that swaps one for the other.

---

## 7. Lua API surface

`Modding/Documentation/ds1doc_eng.chm` (ITSF container, 745 KB) is a complete
scripting API reference: **324 pages**.

22 command namespaces: `NCamera`, `NCanyon`, `NComm`, `NContainer`, `NDebug`,
`NGame`, `NGroupAi`, `NGUI`, `NMission`, `NNPC`, `NObject`, `NPlayer`, `NScript`,
`NShip`, `NSound`, `NStarSystem`, `NStory`, `NTerminal`, `NTutorial`, `NVector`,
`NWaypoint`, `NWing`.

Plus `MissionLib` (58 pages), `Events` (39), `Camera` (25) and a `ModdingGuide`.

Largest namespaces: `NObject` (35), `NShip` (29), `NPlayer` (24), `NCanyon` (14),
`NWing` (14), `NCamera` (13), `NContainer` (12), `NStarSystem` (11).

The engine's own logic is not extractable, but it does not need to be — the
scripting surface a modder actually touches is fully documented, and the game's
shipped mission scripts are readable Lua source.

---

## 8. What this changed in the existing specs

All three were applied; kept here so the trail from finding to correction is
readable.

- `specs/3do_shd.md` dropped the material/texture question from its open list
  and now points here from its opening section — a `.3do` has no material
  reference to find.
- The load-order section took the archive-precedence refinement from §4.1;
  `README.md` §1 carries the measured mount order, and `verify_all` checks it
  against the installation on every run.
- The claim that a loose `3DView\` folder in a mod directory is never loaded
  was **widened to the whole `3DView/` subtree** (§6b, and `README.md` §5).
  The tutorial mod's loose `3DView/` is an authoring workspace, not a
  counter-example.

---

## 9. Not established

- `.dssc` — probed alongside every `.shd`, absent from every shipped archive (§4.1.2)
- The `Generator_0` → `Generator` fallback rule (§4.2)
- `.bsd9` **blob** internals — the container and per-shader texture-slot names
  are decoded (`bsd9.md`); the compiled effect inside, which carries the
  `<Parameters>` semantics of §2.3, is not
- `.cat` camera-track format
- `Flags="98304"` on scene objects
- `PivotTransform` / `Evaluator` / `MatrixProperty` payload semantics
- Whether `Version="2.00"` scenes are the only variant
