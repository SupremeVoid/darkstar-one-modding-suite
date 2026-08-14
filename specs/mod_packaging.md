# Mod packaging: what a mod may contain, and where each thing must live

**Status: established 2026-08-22**, from the DRM-free GOG build (unpacked, so
the game's own code is readable), the modding tools, Ascaron's modding guide,
a large third-party mod, and in-game experiments.

This is the document to read before writing anything that *delivers* files.
[`lua_api.md`](lua_api.md) covers the API surface; [`README.md`](README.md) §5
covers the archive/VFS rules. This one answers: **can this go in the mod, or
does it have to go in the game installation?**

Every claim is marked with how it is known:

| mark | meaning |
|---|---|
| **[code]** | read out of the disassembled executable or a DLL |
| **[game]** | observed in a running game |
| **[data]** | measured over the shipped files |
| **[doc]** | stated by Ascaron's modding guide or the API reference |
| **[open]** | not established — do not rely on it |

---

## 0. The two editions, and the recorded stock state

**GOG and Steam are the same build.** Measured [data]:

| | GOG | Steam |
|---|---|---|
| `.text` | plain | DRM-wrapped (encrypted, entry point in `.bind`) |
| `.rdata`, `.data`, `.rsrc`, `.reloc` | — | **byte-identical to GOG** |
| loose content files | 2,687 | the same 2,687, plus 37 German localisation files |
| files differing between them | — | **none** |

So anything read out of the executable's *data* works on both: the Lua API
tables live in `.data`, which is why `tools/exe_api_scan.py` scans either
edition and produces the same 219 functions. The build is identified by a
fingerprint of `.rdata` + `.data` (`532b21cc8f37`) rather than of the whole
file, because a whole-file hash would call the two editions different games.

Only reading *code* needs the GOG copy — which is why the reverse engineering
was done there, and why none of its results are GOG-specific.

**Stock state is recorded, not measured on the spot.** `tools/stock_baseline.py`
writes `src/dsotools/data/stock_baseline.json` from known-clean installations,
and `dsotools.baseline` classifies any installation against it as *unchanged*,
*modified*, *added* or *missing*. This matters more than it sounds: several
"facts about the game" in earlier drafts of these specs were measured on an
installation carrying a mod's own libraries — "13 files under
`lua/`" where a stock install has 9, "1,018 functions the libraries define"
where a stock install defines 179. Recorded stock, measured delta, in that
order.

Comparing sizes alone is not enough: of the twelve files that mod puts in the
game folder, two are byte-different at exactly the stock size, so the default
hashes everything up to 4 MB and compares only size above it (video).

---

## 1. The delivery matrix

`<mod>` is `Documents\Ascaron Entertainment\Darkstar One\Customization\<Name>\`.

| What | Where it must live | How known |
|---|---|---|
| `darkstarmod.ini` | `<mod>\` | [code] mod loader reads `\darkstarmod.ini` |
| `inifiles\items.ini` | `<mod>\` — **required or the mod is not listed at all** | [code][game] |
| `inifiles\`, `sound\`, `strings\`, `scripts\` | `<mod>\`, read loose | [code][game] |
| `3DView\`, `images\` | **only** inside `<mod>\user_data.zip`; loose copies are never read | [game] |
| mission scripts | `<mod>\scripts\*.lua` **loose**, or `<mod>\scripts\user_scripts.bin`. Each works on its own [game]. The loader reads both, so both *should* take effect in one run — but that has never been measured, and this table said it had. See §7 | [code] |
| mission scripts inside `user_data.zip` | **never read** — measured | [game] |
| own text | `<mod>\strings\user_strings.res` | [code] |
| own sounds | `<mod>\sound\` + `user_sounds.xml` | [data] |
| **shared Lua libraries** (`MissionLib.lua`, `BattleLib.lua`, `CameraLib.lua`) | **game installation only** — `<game>\lua\mission\` | [code][data] |
| **the stock mission bundle** `missions.bin` | **game installation only** — `<game>\lua\mission\` | [code][data] |
| `video\subtitles\*.xml` | game installation only | [data] |

**No `.cpr` archive contains a single `lua/` entry** [data]. Seven content
roots exist only as loose files in the installation: `particlescripts` (1,501
files), `interface3d` (611), `objectfieldscripts` (342), `effects` (23), `lua`
(9 in a stock install — six of them in `lua\mission\`), `frontend` and
`strings` (3 each). Anything a mod needs to change in
those has to be installed into the game folder, which is the one irreversible
thing a modding tool can do — see `dsotools.rootfiles`.

---

## 2. How scripts are loaded

One function does all of it (`DarkStarOne.exe` GOG build, `0x0061E1A0`) [code]:

```
base = GetCurrentDirectory()                    -- the game root
load( base + "missions.bin" )                   -- the stock bundle
if (a mod is active):
    load( <mod> + "\scripts\user_scripts.bin" ) -- the mod's bundle
    for each file matching <mod> + "\scripts\*.lua":
        load( it )                              -- FindFirstFile/FindNextFile
config  = <argument> or "lua/mission/config/default/"
include = config + "include.ini"
```

Consequences:

* the mod's scripts are loaded **in addition to** the stock bundle, never
  instead of it;
* both mod routes are live in the same run — bundle first, then loose files.
  Measured: a mod carrying one mission in `user_scripts.bin` and another loose
  paid out for both in a single game [game]. A mod that ships the same mission
  both ways registers it twice, and the second registration wins;
* `include.ini` is composed against a mission *config* directory under the
  game root (`lua/mission/config/default/` by default, `.../scenario/` for the
  other case). No stock install ships that directory. Its role is **[open]**.

**Bundles are opened through the archive layer**, not with plain file I/O
[code]: `g_pArchiveMgr->ArchiveHandle()` → `arc_CreateFile` → `arc_FileLength`
→ `arc_Read` → `lua_dobuffer`. That is the same layer that mounts the `.cpr`
files and a mod's `user_data.zip` — but **that does not make the zip a delivery
route for scripts**. A mod carrying nothing but `scripts/dsotest_probe.lua`
inside its `user_data.zip` produced no effect whatsoever, while the identical
script loose in `<mod>\scripts\` ran [game]. The loader passes an absolute
filesystem path, and `arc_CreateFile` resolves that on disk rather than in a
mounted archive. **Scripts must be loose.**

**`source` does not go through that layer.** It is `lua_dofile` on the literal
string, which reaches `CreateFileA` in `lua.dll`, resolved by Windows against
the process working directory (the game root) [code]. There is no search path
and no archive lookup, which is why `source "lua/mission/MissionLib.lua"`
only ever finds the installation's copy.

---

## 3. The Lua environment

* **Lua 4.1.0 (ASCARON_LUA, modified 4.0.1 source)** [code] — bytecode header
  `\x1bLuaA`.
* **No standard library at all.** `lua.dll` contains none of the registration
  names for base, io, os, string, math or debug: no `dofile`, `print`,
  `type`, `tostring`, `strfind`, `abs`, `readfrom` [code]. This is why the
  shipped `MissionLib` implements its own `Rnd`. A script therefore **cannot**
  open a file, so a mod cannot write its own loader in Lua.
* `true`, `false` and `nil` exist and the stock scripts use `== false` [data].
* Globals registered by `Ascaron.Scripting-4.0.dll` [code]: `require`,
  `import`, `print`, `error`, `source`, `exit`.
  * `source "path"` → `lua_dofile(path)` verbatim, as above.
  * `import "X"` is **not** a file loader: it splits on `.` and binds an engine
    namespace table.
* Engine API: **219 functions in 21 namespaces**, recovered from the
  registration tables [code]. Against the 194 documented engine functions:
  **5 are documented but not registered** (`NGUI.Enable`, `NGUI.Disable`,
  `NContainer.SetArtefact`, `NTutorial.Blink`, `NTutorial.ShowPopup` — the
  whole `NTutorial` namespace is absent), and **30 are registered but
  undocumented**. Full lists in [`lua_api.md`](lua_api.md) §4.
* **Some documented functions do nothing.** `NDebug.Message` is 39 bytes that
  build an empty return table and never read the argument; `NDebug.SetPlasmaEffect`
  is a bare `retn` [code]. Anything relying on `NDebug.Message` for output is
  silently dead — it cost this project two inconclusive experiments.

---

## 4. Mission scripts

### 4.1 Registration

A script registers with `NScript.Register` — returning a table of transitions
does **not** work [doc][data]:

```lua
NScript.Register( {
    Name        = "MY_MISSION_001",
    Group       = 0,
    Type        = MTYPE_ALWAYS,
    Transitions = {
        { nil, nil, "Init", function( V, Data ) return { Ready = true } end },
        ...
    }
} )
```

The transition form is `{ step, unused, "Event", function }`, with `nil` for
"any step" — confirmed against the stock `ALWAYS_000` bytecode [data].

### 4.2 Mission types

The engine registers thirteen, with these values [code]:

| | | | |
|---|---|---|---|
| `MTYPE_ALWAYS` 1 | `MTYPE_SPACE` 2 | `MTYPE_TERMINAL` 3 | `MTYPE_BAR` 4 |
| `MTYPE_CHAPTER` 5 | `MTYPE_TUNNEL` 6 | `MTYPE_STORY` 7 | `MTYPE_STORY_TERMINAL` 8 |
| `MTYPE_STORY_CHAPTER` 9 | `MTYPE_STORY_BAR` 10 | `MTYPE_MENU` 11 | `MTYPE_USER_TERMINAL` 12 |
| `MTYPE_USER_SPACE` 13 | | | |

The modding guide says only `MTYPE_USER_TERMINAL` and `MTYPE_USER_SPACE` are
"available for self-made scripts" [doc]. **That is not the whole story**:

* The mod examined delivers its own `ALWAYS_01`..`ALWAYS_05` and
  `GLOBALSPACE_*` missions as `MTYPE_ALWAYS` inside its `user_scripts.bin`,
  and those names are not in stock `missions.bin` [data];
* a mod-delivered `MTYPE_ALWAYS` mission started by itself in a running game
  and received events [game].

`MTYPE_USER_SPACE` was registered and never created, even after arriving in a
new system by hypergate [game] — so whatever creates that type needs more than
arrival. **[open]**

### 4.3 A mod can register under a stock mission's name

A mod script registered as `Name = "ALWAYS_000"` — a name compiled into the
game's own `missions.bin` — and **its `MissionStart` ran** [game]. Mod scripts
load after the stock bundle, so the name is at least accepted and used.

**Confirmed against a control** [game]. A probe mod carried two missions: an
override of `ALWAYS_000` generated by the suite, and `PROBE_NEW`, a fresh
`MTYPE_ALWAYS` mission replacing nothing. Both were instrumented with
`NPlayer.AddCredits` on a decimal digit code. After one hypergate jump the
balance had risen by **12 012** — override `Init` ×2 and `Create` ×1, control
`Init` ×2 and `Create` ×1. The override behaved identically to a mission that
displaces nothing, which is what makes it a replacement rather than a name the
engine merely tolerates.

**It replaces rather than joins** [code]. `NScript.Register` pushes the
mission's name, then builds the record table, then calls `lua_settable` — i.e.
`SCRIPT_TABLE[name] = record`, a Lua table keyed by name, whose record carries
`FORMAT`, `STATES`, `TYPE`, `T_COUNT`, `GROUP`, `M_COUNT`, `I_COUNT` and
`NAME`. A second registration under the same key overwrites the first, and mod
scripts are loaded after the stock bundle, so **the mod's version is the one
that survives**.

This matters because it is the alternative to patching `missions.bin` — see
§5.

### 4.4 The stock mission table

`missions.bin` was disassembled with `ScriptCompiler -l` and every chunk's
registration record read back out: **154 chunks = 150 missions + exactly 4
libraries** (`MissionLib`, `BattleLib`, `BattleLibEx`, `CameraLib`) [data].
Nothing is unaccounted for, which is what makes the reading falsifiable rather
than merely plausible; `verify_all` re-checks it.

The record sits in each chunk's main body in a fixed shape, so name, type,
group and the ordered state list all come back:

```
GETGLOBAL   ; NScript
GETDOTTED   ; Register
PUSHSTRING  ; "Name"          PUSHSTRING ; "<name>"
PUSHSTRING  ; "Group"         PUSHINT    <n>
PUSHSTRING  ; "Type"          GETGLOBAL  ; MTYPE_*
PUSHSTRING  ; "Transitions"
  per state: CREATETABLE 4, PUSHNIL 2, PUSHSTRING ; "<state>", CLOSURE
```

which matches the `table[n]:{float,float,string,function}` the reference
documents for `Transitions` [doc].

What the 150 records say:

| Type | Missions | |
|---|---|---|
| `MTYPE_STORY` | 62 | |
| `MTYPE_SPACE` | 27 | |
| `MTYPE_TERMINAL` | 27 | |
| `MTYPE_BAR` | 22 | |
| `MTYPE_STORY_CHAPTER` | 8 | **not in the reference's list of eight** |
| `MTYPE_ALWAYS` | 3 | |
| `MTYPE_MENU` | 1 | **not in the reference's list of eight** |

Two things that will catch a modder out:

* **The file name is not the mission name.** `BAR_006_02.lua` registers
  `BAR_006`, and `STORY_011_01.lua` registers `STORY_011`. Only the `Name`
  field decides which mission a script *is*, so an override must match that,
  not the chunk it came from.
* **Names are case-sensitive**, because the table is an ordinary Lua table.
  Stock ships both `ALWAYS_000` and `Always_001`; registering `ALWAYS_001`
  would add a mission rather than replace one.

Across 823 states the whole vocabulary is 20 names: `Achieved`, `ActionCamEnd`,
`Container`, `Create`, `Destroy`, `Failed`, `Hostile`, `Init`, `Item`,
`MissionStart`, `Shield`, `StarshipDeath`, `StarshipDestroyed`, `Station`,
`Subtype`, `System`, `Text`, `Timer`, `Waypoint`, `Wing`.

The GOG and Steam bundles produce **identical** tables [data], so the suite
ships one, generated by `tools/stock_missions.py` into
`src/dsotools/data/stock_missions.json`.

### 4.5 Lifecycle, as observed

Documented order [doc]: load (the file runs, `Register` executes) → `Init`
(must return `{Ready=true}`) → `Create` → `MissionStart` → events → `Achieved`
/ `Failed`.

The `Init` contract is also visible in the stock bytecode [data]: `ALWAYS_000`'s
`Init` ends in `{ Ready = true }` on one branch and `{ Ready = false }` on the
other, and nothing else is returned. **Returning nothing means the mission is
never created** — no error, no log line. A generated mission stub that leaves
`Init` empty therefore looks finished and is inert, which is why the suite's
templates always emit the return.

`PRJ011` reports it in a hand-written script too: an `Init` body with no
`return` in it at all is an **error**, because the mission can never be
created. Only that certain case is reported — a body ending in
`return MissionLib.Decide( V )` is legitimate and unreadable to a text scan,
so it is passed over rather than guessed at.

`PRJ012` runs the Scripting tab's API check over **every** script a mod ships
rather than the one file on screen: calls the build does not register (an
error, since the call fails at runtime), calls into registered functions that
do nothing, calls to nothing at all, and prose passed where a StringId belongs.
It needs the game folder, because judging a call *unknown* means "nothing in
play defines it" and without the game's own Lua libraries that is every library
call in the mod; with no baseline it is skipped and listed as skipped.

**`Init` is not called once** [game]. The probe counted **two `Init` calls for
one `Create`**, on the overriding mission and on the fresh one alike — so it is
a readiness question the engine re-asks, not a one-shot constructor. A body
with side effects pays for them twice.

**Both calls belong to the arrival.** A second reading of the same probe, taken
in the start system *before* jumping, showed the credit balance unchanged
[game]: no `Init`, no `Create`. So the sequence for an `MTYPE_ALWAYS` mission is
nothing at all until the player arrives somewhere new, then `Init`, `Init`,
`Create`. Why the engine asks twice at that moment is not established; that it
asks twice, and only there, is.

Measured in game [game], with a mod's loose scripts:

* **A mission receives events as soon as it is created** — before
  `MissionStart`. A `USER_TERMINAL` mission whose `Init` returned `Ready=true`
  handled a `Station` event while it was still unaccepted. Acceptance is what
  triggers `MissionStart`, not what makes the mission live.
* **An `MTYPE_ALWAYS` mission gets nothing at all in the start system** — seen
  three times, and the third time conclusively [game]. The probe instrumented
  `Init` as well as `Create`, and the balance was still untouched in the start
  system, so the engine does not even ask whether the mission is ready there.
  Undocking and re-docking produced no callback either. Everything happens on
  arriving somewhere new.

  The consequence for a mod is worth stating plainly: **no mission callback can
  run in the start system**, so anything that must happen there has to go in the
  script's own body, which executes when the loader reads the file rather than
  when a mission is created.
  A mod that needs to act in the first system cannot rely on this type.
* The `Station` event fires in both directions (`Data.Enter` true/false),
  survives a hyperjump, and reaches **several missions at once** — two active
  missions both saw the same landing.

---

## 5. Compiled bundles

`ScriptCompiler.exe` in the modding tools **is `luac`** — it prints
`usage: luac [options] [filenames]` and identifies itself as
`Lua 4.1.0 (ASCARON_LUA,modified 4.0.1 source)` [data]. Options: `-o file`,
`-l` (list/disassemble), `-p` (parse only), `-s` (strip debug info), `-v`.

```
ScriptCompiler.exe -o user_scripts.bin AAA_Lib.lua ZZZ_Mission.lua
```

Many inputs produce **one** bundle whose main chunk calls each file's chunk in
order — verified by listing both the game's `missions.bin` (154 chunks, one
`CLOSURE`/`CALL` pair each) and a bundle built here [data]. Order is simply the
command-line order; the stock bundle is alphabetical, so libraries are
interleaved among missions and nothing depends on load order (scripts only
*register* at load time).

Useful side effects:

* `-p` is a **syntax check** for mod scripts, using the engine's own parser;
* `-l` **disassembles** an existing `.bin`, including the mod's and the game's,
  which is how the stock mission structure above was read.

`missions.bin` contains the shared libraries too (`MissionLib`, `BattleLib`,
`BattleLibEx`, `CameraLib`) [data], so those exist both as `.lua` source and as
compiled chunks.

**Patching a bundle without recompiling is a real technique** [data]:
the `missions.bin` in that mod's game-root archive differs from the
stock one in **39 single bytes**,
every one an equal-length identifier edit — `NWing`→`XWing`,
`WINGTYPE_FREIGHTER200T`→`...200C`, `Actions`→`AcTiOnS`. Breaking a name makes
the lookup fail, which disables that piece of stock behaviour.

---

## 6. Text and strings

Every text-showing API takes a **StringId, not a literal** [doc]:
`NGUI.ShowInfoText`, `NGUI.ShowSubtitle` and `NComm.AddMessage` all document
`Text` as *"StringId from user_strings.res"*. Passing a literal silently shows
nothing — confirmed in game, where the surrounding credit calls worked and the
text never appeared [game].

A mod ships its own table at **`<mod>\strings\user_strings.res`**: the engine
composes that path from the same mod-base accessor the script loader uses
(`0x00626700`) [code].

Ascaron's source form was an **Excel-2003 XML** with two columns (id, text),
converted by `Xml2ResConverter.exe`. The Tutorial mod ships a matched pair.

**The format is fully decoded** — see [`string_tables.md`](string_tables.md) for
the layout and the hash, and note that the earlier reading of it here was wrong
in two places: there is no `size` field (the four words are hash, offset,
reserved, byte length) and the entries are **not** sorted. The suite reads and
writes tables itself; `Xml2ResConverter.exe` is not needed and, being .NET 1.1
WinForms, no longer runs conveniently anyway.

Because a table stores only hashes, an id cannot be read back out of one. The
suite therefore keeps the authored `(id, text)` pairs in the `.dsoproject` and
treats `user_strings.res` as a build product.

---

## 7. What is still open

| Question | Why it matters |
|---|---|
| What creates an `MTYPE_USER_SPACE` mission? | Registered and never created, even on hypergate arrival |
| Can any mission type act in the *start* system? | Settled for `MTYPE_ALWAYS`: **no** — not even `Init` is asked there (§4.5). Whether another type is offered one is untested; the fallback is top-level code in the script body |
| Why is `Init` asked twice on arrival? | Two calls, one `Create`, both at the same moment. Harmless if `Init` is pure, which the suite's template now says it must be |
| What reads `include.ini`, and from where? | Path is composed under the game root; no stock install has the folder |
| Which StringIds the stock game defines | A table stores hashes, so ids are not recoverable from it. Scanning the executable, the DLLs, the loose Lua and all six archives resolves 934 of 9,378 stock keys (10%); the rest are named only inside compiled Lua and A2dLib resources. Looking up an id you already know always works — see [`string_tables.md`](string_tables.md) §4 |
| Do loose `scripts\*.lua` and `user_scripts.bin` both load in one run? | The loader reads both; not yet confirmed together in game |

---

## 8. What this means for the suite

1. **Script editing is safe to offer for `<mod>\scripts\`** — that route is
   proven end to end. Editing a game-root library must keep going through the
   `root/` payload and `dsotools.rootfiles`, because `source` can only ever
   read the installation.
2. **Validate against the implemented API, not the documentation** — done.
   `tools/exe_api_scan.py` recovers the registration tables straight from the
   executable into `src/dsotools/data/lua_engine.json`, and the Scripting tab
   reports four kinds of finding: `absent` (documented, not in the build),
   `stub` (registered, does nothing), `literal` (prose where a StringId
   belongs) and `unknown`. On a stock installation this leaves the shipped
   scripts with **no** `absent`, `unknown` or `literal` findings at all and
   **4** `stub` ones — the game's own calls to `NDebug.Message`. Judged against
   the reference alone, two real functions (`NObject.GetActionTurret`,
   `NWing.IsWing`) would have been reported as unknown.
3. **Warn when a literal is passed where a StringId is expected** —
   `ShowInfoText`, `ShowSubtitle`, `AddMessage`. This is the single most
   confusing failure in mod scripting: the call succeeds and nothing happens.
4. **Use `ScriptCompiler.exe`** — done, as `dsotools.luac`: `-p` for a syntax
   check by the engine's own parser, `-o` to build a bundle, `-l` to
   disassemble one. `luac.chunk_names()` reads a bundle's contents with no
   compiler at all, so inspection works on any machine.
5. **Refuse scripts in `user_data.zip`** — done, as `PRJ007`. The zip is
   mandatory for `3DView/` and `images/` and fatal for `scripts/`, and the
   failure is silent in both directions.
6. **Prefer a same-named mission over patching `missions.bin`** — done. The
   Scripting tab's *New mission* dialog lists all 150 stock missions with their
   real type, group and states, and writes an override script that starts from
   that record; the *New mission* half refuses a name the stock game already
   uses, because doing that by accident disables a stock mission silently.
   `PRJ010` reports both halves afterwards: two scripts registering one name is
   an error (only one survives, and which is undefined), and replacing a stock
   mission is reported as information. If a byte patch is ever unavoidable, it
   must be an equal-length identifier rename recorded in the project manifest
   and installed through `rootfiles` with a backup — never a raw hex editor,
   and never an unrecorded edit.
7. **Write text from the suite** — done. `.res` string tables are read and
   written directly (see [`string_tables.md`](string_tables.md)); the authored
   ids live in the `.dsoproject` because a table stores only hashes.
8. A mod that only adds missions, ini tweaks, sounds and text is **fully
   self-contained**. The moment it touches `lua/`, `particlescripts/`,
   `interface3d/`, `objectfieldscripts/`, `effects/`, `frontend/` or
   `video/subtitles/`, it needs an install-folder payload — and that is the
   point at which the suite must take a backup.
