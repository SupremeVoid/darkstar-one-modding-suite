# Modding Darkstar One — what works, and what fails quietly

Darkstar One was made by **Ascaron Entertainment** in 2006. It shipped a
modding kit, and the kit works — but the engine almost never tells you when a
mod is wrong. It loads what it understands, ignores what it does not, and says
nothing either way.

That is the single fact worth carrying into everything below. **Nearly every
mistake in this game is silent.** A file in the wrong folder, a script that
returns nothing, a sound nobody declared, a texture no scene points at — all of
them produce a game that starts normally and simply does not contain your
change. There is no error, no log line, and nothing in the interface that says
a mod was skipped.

So the useful question is not "how do I edit this file" but "what makes the
engine actually read it". This guide is the answer, drawn from what has been
measured in the game rather than from what seems reasonable.

---

## 1. Get the mod listed at all

A mod lives in
`Documents\Ascaron Entertainment\Darkstar One\Customization\<Name>\` and needs
two things before anything else matters:

| File | Why |
|---|---|
| `darkstarmod.ini` | the manifest — `mod_name` is what the game's mod list shows |
| `inifiles\items.ini` | **without it the game does not list the mod at all** |

The second one is the classic. A mod with no `inifiles\items.ini` is skipped
entirely: it does not appear in the list, and nothing anywhere says why. Copy
the stock file in; it is meant to be identical.

> The suite reports this as **`PRJ004`** and will write the file for you from
> the Problems tab.

---

## 2. Put files where the engine reads them

This is the second-largest source of silent no-ops, and it is not symmetrical —
some folders are read **only** loose, others **only** from inside the mod's
`user_data.zip`.

| What | Where it must be |
|---|---|
| `inifiles\`, `sound\`, `strings\`, `scripts\` | loose in the mod folder |
| `3DView\`, `images\`, `staticImages\` | **only inside `user_data.zip`** |

Both directions were established by shipping the same file both ways and
looking at the game. An edited atlas page did nothing loose and appeared
immediately from the zip. A deliberately broken scene crashed the game from the
zip and did nothing at all when loose — which is the clearest possible proof
that the loose copy is never opened.

Scripts are the mirror image: `scripts\*.lua` **must** be loose. Zipped scripts
are never loaded, even though script bundles are otherwise read through the
archive layer, which is exactly why the mistake is natural.

> **`PRJ005`** and **`PRJ007`** report each direction, and *Deploy* moves files
> into the shape the engine reads.

---

## 3. Overriding beats adding

A mod overrides a stock file by shipping a file at the same virtual path. That
is the mechanism for essentially everything — textures, models, scenes, ini
files, interface screens — and it always works.

**Adding something the game has never had is a different question**, and the
answer is usually "nothing will ask for it". The engine loads assets by name
from its own code: a new texture, model or scene sits in the mod doing nothing
until something the mod also controls names it.

What *can* introduce genuinely new content:

* **Sounds** — a mod's `user_sounds.xml` is read in addition to the game's, so
  a declaration you add is a sound the game now has.
* **Scripts** — the loader globs `scripts\*.lua` and runs every one, so a new
  file takes effect by existing.
* **Text** — `strings\user_strings.res` adds your own StringIds.
* **Textures**, once a scene you also ship binds them.

What cannot, and why it is worth knowing before you spend a weekend on it:

* **New ships.** `NWing.Create` takes fixed `WINGTYPE_*` and `RACE_*`
  constants; no ini maps a wing type to a ship, and the mapping from a ship
  class to its scene name lives inside the executable. Override an existing
  ship instead — every Menschen cruiser of that id changes with it.
* **New interface screens.** The game's 83 `.screen` files are named by the
  executable and none references another. Editing one works; adding an
  eighty-fourth is inert.

---

## 4. Scripting

This is where the silent failures are worst, because a Lua script that is wrong
still parses, still loads, and still registers.

### `Init` must return `Ready`

A mission's `Init` is a **readiness question**, not a constructor. The engine
creates the mission only if the call returns a table whose `Ready` is true:

```lua
{ nil, nil, "Init",
    function( V, Data )
        return { Ready = true }     -- or { Ready = false } to decline
    end
},
```

Return nothing and the mission is never created — no error, no log line. This
project's own generated template got it wrong until the stock bytecode was read,
which is the best evidence there is that a hand-written one will too.

`Init` is also **not called once**. Measured in game: two `Init` calls for one
`Create`, both at the arrival that creates the mission, and never in the
starting system. Keep it free of side effects or you pay for them twice.

> **`PRJ011`** reports an `Init` with no `return` in it at all.

### The mission's name is its identity

`NScript.Register` keys the mission table by `Name`, and a mod's `scripts\` is
read *after* the stock bundle. So registering an existing name **replaces that
stock mission**, and the file name is irrelevant — stock's `BAR_006_02.lua`
registers `BAR_006`. Names are case-sensitive: `ALWAYS_001` and `Always_001`
are different missions.

> **`PRJ010`** reports both halves: two scripts registering one name is an
> error, and replacing a stock mission is reported so it is never an accident.

### Text must be a StringId, never a literal

Anywhere the reference says a parameter is a *StringId*, it means an identifier
from a string table:

```lua
NGUI.ShowInfoText( { Text = "IDM_MY_MESSAGE" } )   -- correct
NGUI.ShowInfoText( { Text = "Hello there" } )      -- displays nothing at all
```

The call succeeds either way. Nothing is drawn and nothing is reported. This
one cost this project a full experiment cycle before the check existed.

> **`PRJ012`** sweeps every script in the mod for it, along with three other
> findings: calls the build does not register, calls into functions that are
> registered but do nothing, and calls to nothing at all.

### Some documented functions do not exist, and some do nothing

The 2006 reference documents 318 symbols. The executable registers **219**.
Five documented functions are absent outright — the whole `NTutorial`
namespace among them — and a call to one fails at runtime with nothing to
explain it. Others are registered and inert: `NDebug.Message` is 39 bytes that
never read their argument, so debugging through it produces silence.

There are also **30 functions the executable registers that the reference never
documents**, including `NShip.CreateWreck` and `NStarSystem.List`.

### A script that is not a mission still runs

The loader runs every `scripts\*.lua`. Top-level code executes at load, which
is the reliable way to do something in the starting system — an `MTYPE_ALWAYS`
mission gets no callback at all there, not even `Init`, until the player
arrives somewhere new.

---

## 5. Sound

The engine does not scan folders. It reads a database, and **a file nobody
declared is not merely unreferenced, it is inaudible**.

A mod's `user_sounds.xml` is additive: the game's 442 sounds stay, and a mod
entry wins over the game's on a shared group and name. Overriding a stock sound
therefore needs nothing but a same-named declaration — no game-folder payload,
no risk to the rest of the sound bank. Measured by pointing the stock
`Mainmenu/MUSIC_Mainmenu` at a combat loop: the combat track played at the menu
and all 441 other sounds still worked.

Three declared numbers matter more than they look: `Channels`, `Freq` and
`Duration`. **The engine believes the database, not the file**, so a stale
`Duration` cuts playback off where the database says the sound ends — which in
game is indistinguishable from a corrupt file. `Duration` is in **samples**, not
milliseconds.

> **`SND001`**–**`SND004`** cover the four failures, and the suite fills the
> numbers in from the file whenever it writes a declaration.

---

## 6. Before you ship

1. **Validate.** The Problems tab is a compiler's problem list for a mod: every
   rule in it corresponds to a failure that is silent in game, and each says
   how it was established.
2. **Deploy.** This moves files into the layout the engine actually reads and
   writes the `items.ini` if it is missing. A mod that validates clean can still
   be laid out wrong.
3. **Check what is unused.** A file nothing references is usually a leftover —
   an old track that was replaced, a texture from an abandoned idea. It costs
   download size and confuses the next person, including you.

---

## 7. Where the evidence is

Every claim above is recorded, with how it was established, in the format
references that ship alongside this guide. They use four markers:

| Marker | Means |
|---|---|
| `[code]` | read out of the executable |
| `[data]` | measured across the game's own files |
| `[doc]` | from Ascaron's documentation |
| `[game]` | confirmed by running the game |
| `[open]` | not established — stated as unknown rather than guessed |

If something here disagrees with what you see in game, the specs are where to
look first, and the `[open]` markers are where this project already knows it
does not have an answer.
