# Interface formats — `.screen` layouts, `.anim` drawables, `.tex` atlases

Everything the game's **user interface** is built from: which image is drawn,
where that image actually lives, and how the screens place them.

| section | format | what it is |
|---|---|---|
| [§2](#2-tex--texture-page-index-sh_texpg) | `.tex` | the atlas index — which page a graphic is on, and the rectangle it occupies |
| [§3](#3-anim--drawable-sh_anim) | `.anim` | a drawable — names a source image and the size it is drawn at |
| [§4](#4-screen--screen-layout-sh_scrn) | `.screen` | a screen layout — every control, its rectangle, and what it draws |
| [§5](#5-partmap007) | `Partmap.007` | the resource hash index (not analysed) |
| [§6](#6-editing) | — | how to change a UI graphic end to end |

Companion to [`aim.md`](aim.md), which specifies the image encodings
themselves. This document covers the resource files that tell the engine
*which* image to draw and *where it lives*, without which `.aim` editing is
guesswork.

These are Ascaron's **A2dLib** resources (the name the engine's own log
strings and tags use — `SH_TEXPG`, `SH_ANIM`, `SH_SCRN`), and they live in a
`scripts/` folder inside the interface archives —
`ds_interface/scripts/` and `ds_add/scripts/`, which ship identical copies.
A2dLib loads them through a resource directory (`AddScriptDir`) indexed by
`Partmap.007`, registering resources by hash.

Contents of `ds_interface/scripts/` in Darkstar One:

| files | extension | resource | tag |
|---|---|---|---|
| 1107 | `.anim` | drawable / animation | `SH_ANIM` |
| 83 | `.screen` | screen layout | `SH_SCRN` |
| 10 | `.tex` | texture page index | `SH_TEXPG` |
| 1 | `Partmap.007` | resource hash index | — |

Other resource tags exist in the library — `SH_DWB`, `SH_DWFAB`, `SH_FONT`,
`SH_CURSR`, `SH_SND` — but no files of those types ship here.

All integers are little-endian.

---

## 1. Why this matters

**The standalone `images\*.aim` files are the packer's sources, not what the
game draws.** At runtime a UI graphic comes out of an atlas page, at a
rectangle recorded in a `.tex` file. Replacing `images\Auftraege.aim` on disk
therefore changes nothing on screen; the change has to go into
`TexPage_8_2.aim` at (950, 521).

This also explains why so few standalone sources ship at all — the
`ds_interface/images/` folder holds 37 files while the `.tex` indexes name 729
sub-images across 10 pages.

## 2. `.tex` — texture page index (`SH_TEXPG`)

A fixed-record file: a 28-byte header, then `(filesize - 28) / 284` records of
284 bytes each. Record 0 describes the page; the rest describe its contents.

```
Header, 28 bytes
  char[8]  "A2DFILE\0"
  u32      28              header size
  u32      17              constant in every sample; meaning unknown
  u32      0, 0, 0

Record 0 — the page                       (284 bytes)
  char[8]  "SH_TEXPG"
  u32      284             record size
  u32      count           number of sub-image records that follow
  char[]   page filename, NUL-terminated, zero-padded

Records 1..count — one per packed sub-image   (284 bytes each)
  u32      284             record size
  u32      x, y, w, h      rectangle within the page
  char[]   source filename, NUL-terminated, zero-padded
```

`count` always equals the record count minus one, and every rectangle lies
inside its page's bounds.

The ten pages in Darkstar One:

| file | page | sub-images |
|---|---|---|
| `TexPage1.tex` | `TexPage_8_0.aim` | 102 |
| `TexPage2.tex` | `TexPage_8_1.aim` | 177 |
| `TexPage3.tex` | `TexPage_8_2.aim` | 331 |
| `TexPage4.tex` | `TexPage_1_3.aim` | 45 |
| `TexPage5.tex` | `TexPage_0_4.aim` | 58 |
| `TexPage6.tex` | `Samplegroup_8_5.aim` | 12 |
| `TexPage7.tex` | `Samplegroup_8_6.aim` | 12 |
| `TexPage8.tex` | `TexPage_1_7.aim` | 7 |
| `TexPage9.tex` | `TexPage_0_8.aim` | 25 |
| `TexPage10.tex` | `Samplegroup_8_9.aim` | 12 |

Note the page naming: the **trailing** number is the page index and matches the
`.tex` number minus one. The leading number tracks the `.aim` encoding —
`0_*` pages are `BMPRES`, `1_*` are `IMJPG24A`, `8_*` are `IMTC32`. Pages
outside this list exist on disk (`TexPage_0_1`, `TexPage_1_1`, `TexPage_1_2`,
`TexPage_1_4`, `TexPage_1_6`) and are referenced by no `.tex`; they appear to
be alternate-encoding builds of the same indices that ship unused.

Each named graphic occurs in exactly one page across the whole index. Visual
duplication between pages is either genuinely different artwork or the same art
under a different name (`Auftraege` and `ND_Auftraege` are separate 27×38
entries in the same page).

## 3. `.anim` — drawable (`SH_ANIM`)

Every one of the 1107 files is exactly **3220 bytes**, a single fixed record.

```
0x000  char[8]  "SH_ANIM\0"
0x008  u32      32            section size
0x00c  u32      1             frame count — 1 in every shipped file
0x010  u32      width         the size it is DRAWN at
0x014  u32      height
0x020  u32      24
0x024  u32      1, 1, 1
0x038  u32      32
0x03c  u32      0xffffffff
0x04c  u32      0xffffffff
0x058  u32      12
0x068  char[]   source image, e.g. "images\Auftraege.aim"
0x1a0  u32      width         the size of the SOURCE IMAGE
0x1a4  u32      height
0x1b0  u32      1
0x0c7c u32      12
0x0c88 u32      12
```

Comparing any two `.anim` files, the **only** bytes that differ are the two
sizes and the source filename. Everything else is identical boilerplate across
all 1107 files.

The animation machinery is present but unused — no shipped file has more than
one frame.

### 3.1 The two sizes are not one size twice

They were read that way for a long time, and 1050 of the 1107 files have them
equal, which made the mistake comfortable. The remaining **57 are not corrupt**.

Measured over the corpus: of the 441 drawables whose source sprite can be found
in a `.tex`, **404 have both pairs equal to the atlas rectangle and 36 have
only the second equal to it. Not one has only the first.** So:

| Offset | Meaning |
|---|---|
| `0x010` / `0x014` | the size the interface **draws** it at |
| `0x1a0` / `0x1a4` | the size of the **source image**, i.e. the `.tex` rectangle |

They differ exactly where a drawable is a **stretched nine-slice frame**. 55 of
the 57 name a source ending in `TL` — the top-left corner tile — and the atlas
holds its `TC`, `TR`, `ML`, `MC`, `MR`, `BL`, `BC`, `BR` siblings beside it.
`Background_blaugrau.anim` draws at 511 × 215 from a source that really is
3 × 3: `images/Background_BlaugrauTL.aim` is a 3 × 3 image.

For an ordinary drawable the source *is* the whole graphic, so both agree —
`Auftraege.anim` says 27 × 38 and `TexPage3.tex` places `Auftraege.aim` at
27 × 38.

**Which pair a tool uses matters.** Writing the rectangle into both — which is
what this project did until 2026-08-17 — turns a 511 × 215 window into a 3 × 3
one the first time its page is rescaled. And `TEX004` must compare the
*source* size against the rectangle; comparing the drawn size reports 36 of
Ascaron's own drawables as broken.

The chain is therefore:

```
.screen  →  drawables  →  .anim  →  source images\X.aim
                                          ↓  (resolved through the .tex indexes)
                                    TexPage page + (x, y, w, h)
```

## 4. `.screen` — screen layout (`SH_SCRN`)

**Decoded 2026-08-17.** 83 files, 1.3 KB – 216 KB, named per screen and per
resolution — `LOGBUCH_MISSIONEN_1024x768.screen`, `BORDCOMPUTER_1024x768.screen`
— and every shipped one is `1024x768`, so layouts are authored per resolution
and only one was ever shipped.

A screen is the `A2DFILE` container again: the 28-byte header, then **one
element per control**, laid out flat. The first element is the screen itself.

```
28 bytes   A2DFILE header (28, the constant 17, three zeros)

element    the CScreen: SH_DWFAB + SH_DWB + SH_SCRN + u32 top-level count
element*   the controls
```

Every element starts with the same two records:

```
SH_DWFAB, 416 bytes
  +0x000  "SH_DWFAB", u32 416, 0xffffffff
  +0x010  class name, NUL-padded      CButton, CStatic, CTextBox, CDrawable, …
  +0x050  element name, NUL-padded    longest shipped is 54 characters
  +0x150  two dwords, 0xffffffff in every element

SH_DWB, 304 bytes
  +0x00c  x, y, w, h                  signed int32
```

and the `CScreen` — and only the `CScreen` — carries a third:

```
SH_SCRN, 568 bytes
  +0x000  `"SH_SCRN"` + NUL, u32 568
u32        immediately after the record: the number of TOP-LEVEL elements
```

then zero or more **length-prefixed blocks** — a u32 size followed by that many
bytes — holding the class-specific data, and optionally an 8-byte trailer.

**Field widths, exactly** (all offsets within the record named):

| field | record | offset | width | notes |
|---|---|---|---|---|
| class name | `SH_DWFAB` | `0x10` | `0x40` | NUL-terminated inside the field |
| element name | `SH_DWFAB` | `0x50` | `0x100` | up to the two dwords at `0x150`; longest shipped is 54 bytes |
| `x, y, w, h` | `SH_DWB` | `0x0c` | 4 × int32 | signed; see §4.4 for what they are relative to |
| top-level count | after `SH_SCRN` | — | u32 | see §4.3 |

Strings are **cp1252**, NUL-terminated inside a fixed field. A name is
therefore bounded: writing one that does not fit would overrun into whatever
follows it, so `screen.Element.name` refuses at `0x100 - 1` bytes rather than
truncate.

**Blocks end an element implicitly.** They run until the next `SH_DWFAB` or the
end of the file — no count says how many there are. **14 of 1,381** elements
carry 8 further bytes after their last block; only `(0, 0)`, `(1, 0)` and
`(2, 0)` ship, they are not the nesting (they are absent from every element
that owns children), and they are kept verbatim rather than interpreted. A
trailer of any other length is refused rather than skipped, because that would
mean the walk has lost the thread.

**References are found by scanning, not by offset.** Inside a class block, a
resource path is a run of ≥ 4 printable bytes containing a backslash whose
first component is one of `scripts`, `fonts`, `sfx`, `staticImages`, `images`.
That is what makes the resolve rate in §4.1 meaningful: the scan is
independent of any assumed layout, and it still reaches 2,247 of 2,247.

### 4.1 Why the walk is trusted

Two independent methods agree on every file:

- The **structural walk** above never searches for a tag, and it finds exactly
  the same element offsets as scanning the file for `SH_DWFAB`, ending on the
  final byte, in **83 of 83** files.
- The **references** embedded in the class blocks resolve in the VFS:

| folder | references | resolve |
|---|---|---|
| `scripts\*.anim` | 1,433 | 1,433 |
| `fonts\*.res` | 470 | 470 |
| `sfx\*.res` | 342 | 342 |
| `staticImages\` | 2 | 2 |
| **total** | **2,247** | **2,247 (100%)** |

Four more strings look like references and are not: `\Pr2_Slider_DragBtn_nr.anim`
and its `_hl` twin carry a leading backslash and no folder, twice each. They are
Ascaron's own typo, reported rather than repaired.

The rectangle reading is corroborated the same way: **1,260 of 1,381** elements
fit strictly inside the 1024×768 the filename declares, and the exceptions are
deliberate — letterbox bars at `y = -1`, a 1024×768 background at `(-3, -3)`.
Only 10 have a non-positive width or height.

### 4.2 Element classes

| class | count | block size | class | count | block size |
|---|---|---|---|---|---|
| `CStatic` | 485 | 988 | `CStaticImg` | 42 | 1,004 |
| `CButton` | 311 | 3,888–5,496 | `CSlider` | 27 | 1,264 |
| `CTextBox` | 302 | 1,320 | `CListBox` | 12 | 792 |
| `CDrawable` | 120 | 720 | `CVideoSKS` | 4 | 992 |
| `CScreen` | 83 | 1,292 | `CEditBox` | 1 | 1,592 |
| `CTextBoxEx` | 77 | 1,316 | | | |

Every class has one fixed size except `CButton`, whose five sizes differ by
exact multiples of **536** — it carries one 536-byte block per extra state
(3,888 = one 3,168 block; 5,496 = three 536s and a 3,168).

### 4.3 The parent/child structure — derived, not stored

A `CScreen` declares how many elements are **top level**. In **64 of 83** files
that equals the element count and the layout is flat; the other 19 hold more
elements than the screen claims, and the difference — **133 elements** — are
parts of *another element* rather than of the screen.

That distinction is not cosmetic: **a child's rectangle is an offset from its
parent.** `MOD_slider_Button Drag` is stored at `(2, 48)` and belongs on a
slider at `(444, 59)`; `MOD_slider_Background` starts at `y = -1`. Read flat,
those pile into the top-left corner or fall off the top edge.

**No field holds the relationship.** Searched exhaustively rather than
sampled: every one of the 948 elements in the 64 flat screens must read 0 for
any candidate child-count field, which leaves 551 byte offsets in the 720-byte
common part of an element — and **none** of them reads 4 at either of the two
sliders known to own four children. The same filter finds no offset that marks
a *child* either. `SH_DWB+0x12c` is not it (a screen owning 5 reads 122, a
slider owning 4 reads 102, a leaf reads 115). Nor is any offset inside the
class blocks: no single position works across classes, and the counts are not
in the 8-byte trailer.

So the tree is **derived**, in `dsotools.edit.screentree`, from what the
engine's widgets are:

* a `CSlider` is followed by exactly four sub-controls — a background and the
  two step buttons and the drag handle — whose names extend its own;
* a `CListBox` (or a `CDrawable` acting as one) is followed by its row
  templates and cells, and by its slider, which then takes its own four.

Two independent facts say the derivation is right rather than plausible:

| check | result |
|---|---|
| derived top-level count vs. the count in the header | **83 of 83** screens |
| children that sit on their parent, resolved relatively | **105 of 133** (mean overlap 77%) |
| the same children read flat | **11 of 133** (mean overlap 7%) |

The header count is never given to the derivation, and the geometry is not
used by it at all. Where the names are abbreviated (`..._Goods_vsl_bdr`) or
mangled (`STT_NEWS_ListBox_NewsBox_Story Row:0)`) the class pattern still
carries it; where names merely share a prefix (`WH_Background2` is not part of
`WH_Background`, `..._Radar_Freund_selektiert` is not part of `..._Freund`) no
child is invented. `screentree.consistent()` reports the header agreement per
file, so a caller can fall back to a flat reading rather than act on a guess.

Children are shown but never editable: the engine recomputes their placement,
so a coordinate typed against one would not hold in the game.

### 4.4 Screen origin

Two of the 83 screens have a non-zero origin — `STATUSLEISTE` is at
`(300, 0, 800, 150)`. Their elements are laid out **inside** that box, not on
the desktop: taking the rectangles as relative to the screen box puts 1,349
elements inside it against 1,319 read as absolute. Subtracting the origin is
what pushed half of that layout off the canvas.

### 4.5 Button states

A `CButton` names one drawable per state, in this order:

    disabled, normal, pressed, highlight, [blink]

Read off the shipped data: of the 237 buttons carrying four or more `.anim`
references, slot 0 has a `*_disabled`/`_gr`/`_off` name **127** times and a
`*_normal` name **never**, while slot 1 is plain or `*_normal` **209** times.
The 26 buttons with exactly three skip the disabled state and start at normal
(`_nr`, `_pr`, `_hl`). This is also the 536-byte block per extra state noted in
§4.2.

Anything drawing a layout must therefore pick slot 1, not slot 0 — the resting
state is *enabled and untouched*. Drawing the first reference greys out **125
of the 694** elements that draw anything. `screen.resting_index()` does the
picking.

### 4.6 Nothing is frame-animated

Every one of the **1,107** `.anim` files in the game reports `frames = 1`, so
no element animates from its own data. Elements that visibly grow in the game —
`MAINMENU`'s three `Static_Submenu` elements — are resized by the engine at
runtime, and all three drawables are nine-slice frames precisely so that
resizing looks right: `MainWindow_framed` draws a 59×5 source at 140×74,
`MainMenu_Frame_HG` a 5×5 source at 140×20. The rectangle in the `.screen` is
the authored size, not a bound the engine keeps to.

### 4.7 What a `.screen` still does not tell you

Everything above is enough to read, draw and edit a layout, and to round-trip
it byte-for-byte. What is *not* decoded, in order of how much it would cost to
find out:

- **The class blocks' internals.** Their sizes are fixed per class (§4.2) and
  their resource references are located by scan, but the fields around those
  references are not mapped — colours, alignment, font size, tab order,
  whatever the engine reads to lay text out inside a `CTextBox`. Editing a
  layout does not need them; changing what a control *looks like* beyond its
  artwork would.
- **`CButton`'s 536-byte per-state block.** The count of them matches the
  number of states (§4.5), but the block's own contents are unread.
- **The 8-byte trailer** on 14 elements: `(0, 0)`, `(1, 0)`, `(2, 0)`.
- **`Partmap.007`** (§5), which is how the engine resolves a resource *name*
  to one of these files in the first place. Not needed to edit a shipped
  screen; needed to add a new one under a new name.

Nothing here blocks the round-trip: the parser keeps every byte it does not
understand verbatim, so an edit rewrites only the integers it was asked to.

## 5. `Partmap.007`

Not analysed. 23232 bytes, one per `scripts/` folder. A2dLib logs
*"AddScriptDir: Failed to load Partmap in %s"*, *"Partmap already loaded"* and
*"Hash Collision while registering %s"*, so it is the hash index the resource
manager uses to resolve a resource name to a file in the directory.

## 6. Editing

To change a UI graphic:

1. Find it — `aimatlas.py find NAME` — which reports the page and rectangle.
2. Extract it — `aimatlas.py extract NAME` — which crops it out of the page.
3. Edit the PNG, keeping its exact pixel dimensions. The rectangle is fixed by
   the `.tex`; anything else would overlap a neighbour.
4. Patch it back — `aimatlas.py patch NAME edited.png` — which composites it
   into the page and writes a new `.aim`.
5. Install the page (not the source image) at the path the `.tex` names,
   relative to the game root: `images\TexPage_8_2.aim` → `<game>/images/`.

Changing a rectangle's size means editing the `.tex` record, and because
`x, y, w, h` is both the source region and the drawn size, that scales the
element on screen rather than sharpening it.
