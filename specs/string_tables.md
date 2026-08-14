# `.res` string tables

Every engine call that shows words takes a **StringId**, never a literal, and
resolves it against the string tables loaded at startup. A mod that wants to say
anything at all therefore has to ship its own table. This document is the whole
format, including the hash, so the suite can read and write one.

Findings are marked **[code]** (read out of a shipped binary), **[data]**
(measured on shipped files), **[tool]** (obtained by running a shipped tool) and
**[open]**.

**Status: closed, and confirmed in game.** The format round-trips byte for byte
on every `.res` on this machine — 12 files, 76,000+ entries, 0 malformed
records [data] — and a table written by the suite put **two custom messages on
screen** in a running game [game], which is the first time this project has
shown the player its own text. Implemented in
[`dsotools.formats.res`](../src/dsotools/formats/res.py).

---

## 1. Where tables live

| Table | Path | Loaded |
|---|---|---|
| The game's | `strings\<LANG>\global.res` | always; `ENG` ships in every edition, `DEU`/`FRA` depending |
| The crash reporter's | `Ascaron.Exception.res` | by `Ascaron.Exception.exe`, not the game |
| A mod's | `<mod>\strings\user_strings.res` | composed from the same mod-base accessor the script loader uses (`0x00626700` in the GOG build) [code]; a suite-written table displayed its text in game [game] |

A mod's table is additive: an id the mod defines that the stock game also
defines shadows the stock text. The suite says so before you save, because it is
easy to do by accident with a plausible-looking id.

## 2. File layout

```
u32          count

count x 16 bytes, in no particular order:
    u32      hash        the StringId, hashed (section 3)
    u32      offset      of the text, relative to byte 4
    u32      reserved    0 in every file examined
    u32      length      of the text in BYTES -- two per character

...          the text, UTF-16LE, packed back to back, no terminators
```

Because `offset` is relative to byte 4, the first entry's offset equals
`count * 16`, and text ends exactly at EOF.

Three details worth stating plainly, because each one cost time:

* **The table is not sorted.** The writer emitted entries in .NET hashtable
  order. A reader must not binary-search. (The tutorial table looks sorted at a
  glance because its ids were authored in blocks.)
* **`length` is bytes, not characters.**
* **There is no magic number and no version field.** The first four bytes are
  the count. Sniffing a `.res` means parsing it and seeing whether it is
  self-consistent, which is what `res.is_string_table` does.

## 3. The hash

```python
h = 0                      # 32-bit SIGNED
for c in identifier:       # one byte per character
    h = (h * 113 + c) % 999999991
```

with **C# arithmetic throughout**: the multiply and the add wrap at 32 bits, and
`%` truncates toward zero, so it keeps the sign of the dividend. The final value
is reinterpreted as unsigned.

That last clause is the whole difficulty. Read as unsigned arithmetic the
function cannot produce a value above 999,999,991 — yet real tables are full of
keys above 4,000,000,000, which is why every earlier reconstruction failed
against the data. The accumulator goes *negative*, and the remainder stays
negative.

Recovered from `Xml2ResConverter.exe`, `Xml2ResConverter.Converter.Hash`, 44
bytes of IL [code]. The decisive byte is the opcode at the end of the loop:
`0x5D` is `rem`, not `rem.un`. Ground-truthed by invoking that method directly
on all 53 tutorial ids plus 8 probes: **61/61** [tool].

Worked example, `"AB"`: `0`, then `0*113 + 65 = 65`, then `65*113 + 66 = 7411`.

### Collisions

Short ids never collide — the function is injective while it stays below the
modulus, verified over all 1,926,220 strings of up to 4 characters from
`[A-Z0-9_]` [data]. Collisions appear at realistic id lengths:
`ID_BRCBHZSVVRVG` and `ID_CICQUQNQSTPJ` both hash to `0xF6CF0D05`.

A collision is not a corruption — the file writes both entries — but only the
last one on a key is reachable, and which one that is depends on write order.
The shipped converter refused collisions outright and so does the suite
(`PRJ008`, and the editor blocks the save).

## 4. Ids are not recoverable

A table stores hashes. Nothing in the file names an id, and the hash is not
invertible in practice. Two consequences:

* **Authoring must keep the ids somewhere else.** The suite records the
  `(id, text)` pairs in the `.dsoproject` and treats `user_strings.res` as a
  build product. A table without its project record is uneditable.
* **The stock ids are largely unknown.** Scanning every byte of the executable,
  every DLL, every loose `.lua` and all six `.cpr` archives for identifier-like
  tokens and hashing each one resolves **934 of 9,378** stock keys — 10% [data].
  The rest are named only inside compiled Lua and A2dLib resources in forms this
  scan does not reach. Looking up an id you already know always works; there is
  no catalogue of the ones you do not. **[open]**

## 5. Authoring

Ascaron's route was an Excel-2003 XML with two columns (id, text) converted by
`Xml2ResConverter.exe`. That tool is a .NET 1.1 WinForms application: it needs a
runtime Windows no longer installs by default, and it is interactive, so it
cannot be scripted. The Tutorial mod ships a matched `.xml`/`.res` pair, which is
what the format was verified against.

The suite writes tables directly. Nothing about the format requires the original
tool.

## 6. Validation the suite performs

| Code | Finding |
|---|---|
| `PRJ008` | The mod's `user_strings.res` cannot be read, or carries colliding keys |
| `PRJ009` | A script names a StringId that neither the mod's table nor `global.res` defines — the lookup misses and the game draws nothing at all |

`PRJ009` needs a game folder for the stock half and is skipped, not guessed at,
when none is open.

---

## Cross-references

* [`mod_packaging.md`](mod_packaging.md) §6 — where a mod's table sits among
  everything else it may ship
* [`lua_api.md`](lua_api.md) — which API parameters are documented as StringIds
* [`interface_formats.md`](interface_formats.md) — `.screen` controls also name
  their captions by StringId
