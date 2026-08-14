# Sound

The audio itself needed no reverse engineering — it is ordinary WAV and MP3,
loose in the installation, in no archive. What needed working out is the
**index**: the engine does not scan folders, it reads a database, and a file
nobody declared is not merely unreferenced but inaudible.

Findings are marked **[data]** (measured on shipped files), **[doc]**, and
**[open]**.

**Status: the format is closed.** The stock database round-trips byte for byte,
and a pure-Python reader reproduces all three declared numbers for every one of
its 442 sounds. Implemented in
[`dsotools.formats.sounddb`](../src/dsotools/formats/sounddb.py) and
[`dsotools.formats.audio`](../src/dsotools/formats/audio.py).

---

## 1. Where the databases are

| Database | Path | Holds |
|---|---|---|
| The game's | `KlangErzeugerDefault.xml` at the install root | 285 groups, 442 sounds [data] |
| A mod's | `<mod>\user_sounds.xml` | **additive**: the game's 442 stay, and a mod entry beats the game's on a shared group and name [game] |

Both are `ASE_Database` XML. The audio files sit under `sound\`, split by how
they are played: `music(stream)\`, `radio(stream)\`, `sfx(2d)\`, `sfx(3d)\`, each
subdivided into `grp_*` folders that mirror the group names. **5,769 files —
5,421 MP3 and 348 WAV** [data]; not one is inside a `.cpr`.

## 2. The format

```xml
<ASE_Database>
  <Group Name="MUSIC" Volume="0.88" Wet="0.0">
    <Group Name="Death" Select="Random2">
      <Stream Name="66_gameover_final"
              Resrc="sound\music(stream)\grp_MUSIC\grp_Death\66_gameover_final.mp3"
              Channels="2" Duration=":2048256" Freq="44100" />
    </Group>
  </Group>
</ASE_Database>
```

Three element types declare a playable resource, and the choice decides how the
engine loads it:

| Element | Loading |
|---|---|
| `Stream` | read from disk while playing — music, radio, long atmospheres (94) |
| `Sound2D` | loaded whole, no position — interface, notifications (51) |
| `Sound3D` | loaded whole, positioned in the world — effects (297) |

### 2.1 Groups nest, and the path is the identity

**60 of the 285 groups contain other groups** [data]. This is the detail that
matters most for anyone reading the file, because a reader that looks only one
level down finds **3 of 442 sounds** — which is exactly what this project's
first parser did, leaving the sound checks near-blind for months without
failing.

A sound is addressed by **group path plus name**, not by name. Stock reuses
**38 names** across different groups, every one pointing at a different file:
`FX_FighterExplosionDistant-03` exists under three separate explosion groups
[data]. A flat name index looks tidier and silently drops one of each pair.

Only a repeat *within one group* is a fault, and stock has none.

### 2.2 Group attributes

`Volume`, `Priority` and `Wet` (reverb send) tune a group. `Select="Random2"`
makes the group a **random pool** — the engine picks among the children rather
than playing them all, which is how the game gets variety out of four near-
identical explosion samples. Dropping that attribute while editing would turn a
pool into a chorus, so the suite carries every attribute through untouched.

### 2.3 Paths

`%MOD%` at the front of `Resrc` expands to the mod root; without it the path
resolves against the game installation. Separators are **backslashes**, unlike
scene XML which uses forward slashes — both are read by the same engine, only
the authoring tools differed.

### 2.4 `Duration` is in samples

Written as `:2048256`: a leading colon, then a number. The colon is decoration,
separating nothing.

The unit is **samples**, so playing time is `Duration / Freq`. Verified against
a decoder on the game's own files — 2,048,256 at 44,100 Hz predicts 46.45 s and
the file plays for 46.405 s [data]. Reading it as milliseconds would call that
same file 34 minutes long.

This matters because **the engine believes the database, not the file**: the
three declared numbers are what it uses, so a mod that gets `Duration` wrong
gets playback cut off where the database says the sound ends, which in game is
indistinguishable from a corrupt file.

## 3. Reading the numbers back out of a file

A mod adding a sound has to supply `Channels`, `Freq` and `Duration`, and the
suite cannot ask a decoder — `dsotools` never imports Qt. All three come out of
the file's own headers, and the check that this is right is that the result
reproduces **442 of 442** entries Ascaron's own tool wrote [data].

Three shapes, only the first obvious:

* **PCM WAV** — the data chunk's size divides into frames.
* **IMA ADPCM WAV** (`wFormatTag` 17, four bits a sample in 256-byte blocks) —
  most of the game's effects. The byte count says nothing; the `fact` chunk
  carries the true sample count, which is what that chunk is for. For the 14
  **stereo** ADPCM effects the encoder wrote `fact` as the total across both
  channels and Ascaron's tool divided by them, so `fact // channels` is what
  reproduces the declared value [data].
* **MP3** — no file header at all, only frame headers, and *the first frame is
  not always at byte zero*: several music tracks open with a long run of zero
  bytes. Length comes from a Xing/VBRI frame count where one exists and from
  payload-over-bitrate otherwise, which is exact for CBR. Every stock file lands
  within 0.2% of a decoder [data].

## 4. Validation the suite performs

| Code | Finding |
|---|---|
| `SND001` | A declared sound's file is not there |
| `SND002` | A shipped audio file that nothing declares — inaudible |
| `SND003` | The database will not parse |
| `SND004` | Declared rate, channels or length disagrees with the file |

`SND001` and `SND002` are deliberately checked in both directions: a path typo
puts the same sound in *both* lists, and that pairing is what distinguishes a
typo from a deliberate omission. It found two genuinely broken voice lines in
the first mod it was pointed at.

`SND004` tolerates a small length difference, because MP3 length is derived
rather than read.

`SND002` is also where a *leftover* file surfaces. The suite deletes the old
file when a declaration is repointed, so anything it reports arrived some other
way — dropped in by hand, or left behind by a removal that was told to keep it.
It is reported there and listed in the Project tab rather than repeated in the
Audio tab: a file nothing declares is an unreferenced file like any other.

## 5. Overriding a stock sound

A mod cannot delete or edit an entry in the game's database; it can only
declare the same **group and name** against a file of its own, and leave the
engine to decide between the two. The suite offers that as *Override* in the
Audio tab, separately from *Add*, because the difference between extending the
game and displacing part of it should not be a typo.

**The mod's declaration wins, and nothing else is lost** [game]. Measured with a
mod carrying exactly one entry — the stock `Mainmenu/MUSIC_Mainmenu` pointed at
one of the game's own combat loops. The combat track played at the main menu
instead of the menu theme, *and every other sound in the game still worked*.

Both halves were needed. A mod whose database had **replaced** the game's would
have played its one track too, so the swap alone could not tell "additive with
override" apart from "wholesale replacement" — the discriminator was whether the
other 441 sounds survived, and they did.

So overriding a stock sound needs nothing but a same-named declaration: no
game-folder payload, no overwriting the installation, and no risk to the rest of
the sound bank.

## 6. What is still open

| Question | Why it matters |
|---|---|
| What do `Wet` and `Priority` actually do? | Carried through untouched; a modder cannot yet be told what changing them does |
| What `Select` values exist besides `Random2`? | Only `Random2` appears in stock, so any other is guesswork |

---

## Cross-references

* [`mod_packaging.md`](mod_packaging.md) — where `user_sounds.xml` sits among
  everything else a mod may ship
* [`scene.md`](scene.md) — the other XML format, and the byte-exact machinery
  both now share
