# The Lua scripting API

**Status: extracted 2026-08-18, from documentation rather than from the
engine.** This is the one part of the game that needed no reverse engineering:
Ascaron shipped a complete reference with the *Darkstar One Modding Tools*, and
the work was parsing it.

Companion to [`mod_packaging.md`](mod_packaging.md), which covers *delivery* —
where each kind of file must live, how scripts are loaded, and the mission
types and lifecycle. See also [`README.md`](README.md) §5, which has the
archive rules — including the finding that mission libraries exist only in the game
installation folder and cannot be shipped from a mod folder at all.

---

## 1. The source

`Modding\Documentation\ds1doc_eng.chm`, 745 KB, dated 27 November 2006. A
compiled HTML help file; `hh.exe -decompile` (part of Windows) unpacks it. A
German twin, `ds1doc_de.chm`, documents the same API.

`tools/chm_to_json.py` does the whole conversion — copy, decompile, parse,
write `src/dsotools/data/lua_api.json` — and never touches the original. It has
to run on Windows, because `hh.exe` is the only thing that reads the format;
the generated JSON is committed so nobody else needs the modding tools
installed.

## 2. What is in it

325 pages, of which 6 are the index, the section overviews and the modding
guide. The remaining **319 document one symbol each**, and
`MissionLib.SetWingName` is filed under two categories, so **318 distinct
symbols**:

| Kind | Count | Where |
|---|---|---|
| command | 254 | `Commands/<namespace>/` and `MissionLib/` |
| event | 39 | `Events/<category>/` |
| camera helper | 25 | `Camera/` (`CameraLib`) |

Twenty-two engine namespaces — `NCamera`, `NCanyon`, `NComm`, `NContainer`,
`NDebug`, `NGame`, `NGroupAi`, `NGUI`, `NMission`, `NNPC`, `NObject`,
`NPlayer`, `NScript`, `NShip`, `NSound`, `NStarSystem`, `NStory`, `NTerminal`,
`NTutorial`, `NVector`, `NWaypoint`, `NWing` — plus `MissionLib` (60) and
`CameraLib` (25). Also **223 constants in 25 groups**, from `contants.htm`
[sic].

313 symbols carry a description and 209 a worked example. The 109 without an
example are not a parsing failure: those pages ship `<pre></pre>` with nothing
in it.

## 3. Three page shapes, and the shape is the meaning

* **Command pages** carry a signature, a description, a *Parameter table*, a
  *Return table* and usually an *Example*. Almost everything takes one Lua
  table and returns one:
  `NComm.AddMessage( { Text, Voice, ... } ) : { Message }`.
* **Event pages** add a *Trigger* section saying what fires them, and have no
  callable signature — an event is named by a string in a script's event
  table, so it is addressed by bare name, never `Category.Name`.
* **Camera pages** document `CameraLib` helpers, which are plain positional
  Lua functions, as *Syntax* / *Description* / *Example* instead of tables.

**An optional parameter is marked by italics and by nothing else** —
`<i>Video</i>`, in the signature and again in the table. 124 parameters are
optional. Losing that distinction turns "you may pass a video" into "you must".

## 4. Where the reference is wrong, and where it is short

Recorded because a database that silently smooths these over is a database
nobody can check:

* **Two event pages carry a copied `<title>`.** `ActionCamStart.htm` is titled
  *ActionCamEnd*, and `canyon.htm` is titled *Create*. Keying on the title
  lists one event twice and loses the other, so the **signature** names the
  symbol, and the title is only a fallback.
* **`MissionLib.SetWingName` is documented twice**, under `Other` and `Wing`,
  with identical signatures. Kept once.
* **`sync.ffs_lock`-grade sloppiness exists in the docs too**: five MissionLib
  pages have an empty description block, and one cell name is mangled
  (`STT_NEWS_ListBox_NewsBox_Story Row:0)`).

**The reference is incomplete, and this is measurable.** The strongest
evidence is structural rather than statistical: cross-referencing the reference
against the API the executable really registers (`lua_engine.json`, scanned by
`tools/exe_api_scan.py`) shows **30 functions that exist and are undocumented**,
and **5 that are documented and do not exist**.

Scanning the scripts agrees. On a **stock** installation there are 8 Lua
sources defining 179 functions of their own; 18 calls go to a documented
namespace without being in the reference, and after resolving those local
definitions **2 are left** — `NObject.GetActionTurret` and `NWing.IsWing`, both
of which the executable does register. Stripping comments avoids 4 further
false hits, from calls commented out in `MAIN_MENU.lua`.

Among the 30 undocumented functions:

```
NContainer.IsInSpace      NObject.GetHangarPos      NShip.SetHidden
NMission.GetMissionHandle NObject.SetScanningFlag   NShip.SetOrbiterEvasion
NObject.GetActionTurret   NShip.CreateWreck         NSound.PlayUserSound
NObject.GetAnomalie       NShip.IsInSpace           NStarSystem.GetOrbiter
NWing.IsWing              NStarSystem.GetTradeStation  NStarSystem.List
```

The executable registers every one of them, so they are real; the
documentation is simply silent. This is why validation checks the **build**
and not the reference — judged against the reference alone, real functions look
like typos.

(A heavily modded installation raises every count: with one large mod's
libraries in the game folder the same scan sees 11 sources, 1,018 locally
defined functions and 15 unexplained calls. Those are the mod's numbers, not
the game's.)

## 5. The database

`src/dsotools/data/lua_api.json`, schema 1, ~314 KB:

```json
{ "schema": 1, "source": "ds1doc_eng.chm",
  "symbols":   [ { "name", "namespace", "category", "kind", "qualified",
                   "signature", "summary", "parameters": [...], "returns": [...],
                   "example", "trigger", "page" } ],
  "constants": { "<group>": ["NAME", ...] },
  "namespaces": { "<namespace>": ["Name", ...] } }
```

`qualified` is how a script writes the symbol: `NComm.AddMessage` for a
function, the bare name for an event. `dsotools.scriptdoc.index()` keys on it,
and all 318 are distinct.

## 5a. The API the build actually has

`lua_api.json` is the 2006 documentation. `src/dsotools/data/lua_engine.json`
is what the executable registers, scanned by `tools/exe_api_scan.py`: **219
functions in 21 namespaces**, with the five documented-but-absent entries and
the thirty undocumented ones listed explicitly, plus the two that are
registered and do nothing. Validation uses both — see
[`mod_packaging.md`](mod_packaging.md) §8.

## 6. What this does not cover

* **The engine's own logic**, which is not extractable and does not need to be.
* **`missions.bin` / `user_scripts.bin`** — compiled Lua (`\x1bLuaA`), produced
  by `ScriptCompiler.exe` in the modding tools. Not decoded; a mod ships its
  own rather than editing one.
* **The libraries' own functions.** `MissionLib` and friends are Lua source in
  the game folder, and a mod may extend them — the mod examined adds
  `MissionLibEx.lua` (404 KB) and `BattleLibEx.lua`. They are read for the
  undocumented-call check, but they are not documentation and are not in the
  database.
