# `.bsd9` — Ascaron shader/effect container

*Decoded 2026-08-16 against the retail installation (232 files);
techniques, passes and the object table 2026-08-23.*

Referenced from a scene's `<EffectContainer Path="blender/mat_main.bsd9">`
(see `scene.md` §2.2). 173 distinct shaders are referenced across the corpus.

## 1. Why it mattered

The scene format binds textures **positionally** — `<Textures Number="4">` is
four paths in a row — and says nothing about what each position means. Three
shipped features rested on inferring that from the `_col` / `_lgh` / `_nrm`
filename suffixes.

The shader names its own slots, so that is no longer an inference.

| Shader | Declared slots |
|---|---|
| `mat_main.bsd9` | `t_Color`, `t_Light`, `t_Normal`, `t_Reflection` |
| `mat_main_2.bsd9` | `t_Color`, `t_Light` |
| `mat_biotechanim.bsd9` | `tex0` |
| `phong1_1.bsd9` | *(none)* |

**The slot list and the scene's texture list are the same length in 15,978 of
15,978 effects.** None binds more than its shader declares, none fewer. That
total agreement is what makes the pairing positional and complete, and it is
enforced by `verify_all` rather than merely recorded here.

## 2. Layout

Little-endian throughout. Four-character tags are stored **reversed**: the
file's `LRAV` is `VARL`. The magic is the same trick — `XF  90.1` read as two
reversed four-byte groups is `  FX` `1.09`.

```
0x00  char[8]    "XF  90.1"
0x08  uint32     version          1000 (157 files), 2000 (41), 500, 250, … 120000
0x0c  uint32     n_tex            texture slots this shader takes
0x10  uint32[]   n_tex indices    into the string table, one per slot, in slot order
      …          zero padding
0x30  uint32     n_str
      …          n_str strings
      uint32     blob_len
      byte[]     blob             a D3DX9 compiled effect — see §4
      …          chunk list
      uint32     terminator
```

### 2.1 Strings

`uint32 length`, the bytes, a NUL, then padding to a four-byte boundary. The
stored field is always `align(length + 1, 4)` bytes.

The header's `length` **excludes** the NUL; the variable table inside the blob
writes the same shape with a `length` that **includes** it. Both are "round the
NUL-terminated byte count up to four" — only the number written differs. Getting
this wrong by one desynchronises every following name.

### 2.2 The slot index array

`0, 1, 2, …` in all 230 readable files, so in shipped data the slots are simply
"the first `n_tex` strings". It is read as indices anyway: that is what the
layout expresses, and the two readings cannot be told apart from shipped data.

The remaining strings are technique and parameter names — `DoIt`, `V20P20`,
`FFPHigh`, `FFPLow` for `mat_main`; `fDeltaTime`, `mPWR`, `conf` for
`mat_biotechanim`. They are **not** separable from one another using the header
alone, so only the first `n_tex` are given a meaning.

### 2.3 Chunks

After the blob, each chunk is a reversed four-character tag and a `uint32` size
**including its own eight-byte header**. Five tags are seen: `VARL`, `ANI `,
`TRIG`, `INIT`, `MAIN`. Payloads are not decoded.

The walk must land exactly on the four-byte terminator at `len - 4`. The parser
raises if it does not, because a file whose bytes are not all accounted for is a
file the layout above does not describe.

## 3. Slot names observed

| Name | Meaning |
|---|---|
| `t_Color`, `t_color`, `t_Albedo` | albedo |
| `t_Light` | light map |
| `t_Normal` | normal map (DXT5nm-swizzled — see `scene.md` §3) |
| `t_Reflection`, `t_HardReflection` | environment map, usually `defaultspace.dds` |
| `t_SpecialMap` | a second `_nrm`-suffixed map; **not** the normal map |
| `t_Cloud`, `t_RayleighMap` | planet shaders |
| `t_Emissive`, `t_Shade`, `t_Detail`, `t_dist` | as named |
| `tex0`…`tex3`, `Texture0`…`3`, `t_Texture0`…`2` | **generic — no meaning** |
| `blinkTexture`, `flareTexture`, `ScanTexture`, … | effect-specific |

Slot name against texture filename suffix, over the corpus:

| Slot | `_col` | `_lgh` | `_nrm` | none/other |
|---|---|---|---|---|
| `t_Color` | 11,112 | 1 | — | 59 |
| `t_Light` | — | 10,995 | — | 176 |
| `t_Normal` | 44 | — | 5,408 | — |
| `t_Reflection` | — | — | — | 5,511 |
| `t_SpecialMap` | — | — | 841 | 870 |
| `tex0` | 62 | 446 | — | 928 |

Two rows carry the whole point:

- **`t_SpecialMap` receives a `_nrm` texture 841 times.** A scan for the first
  `_nrm` finds it and stops, so 841 submeshes were shaded with the wrong
  normals. `Cruiser_A_0.xml`'s `mainShape` binds
  `testtechstuffdxt_nrm.dds` to `t_SpecialMap` and `a_plates1_nrm.dds` to
  `t_Normal`; the second is the pair of its `a_plates1_col.dds` albedo.
- **`t_Normal` receives a `_col` texture 44 times** — every one of them the same
  8×8 flat-white `dummyga_col.dds`, i.e. a placeholder for "no normal map", not
  a colour map being misfiled.

`tex0` is genuinely meaningless: it receives `_flat` 473 times, `_lgh` 446, no
recognised suffix 431 and `_col` 62. For those shaders the filename remains the
only evidence, so the suffix convention is kept as a documented fallback.

## 4. The blob is a D3DX9 compiled effect

Its first dword is `0xFEFF0901` — the Microsoft **D3DX9 effect** tag. The inner
format is not Ascaron's at all; it is what `D3DXCreateEffect` produced. The
layout below follows Wine's `d3dx9_36` implementation and was checked against
all 230 files.

```
+0   uint32   0xFEFF0901
+4   uint32   offset to the effect header, relative to +8
```

At that header: `parameter_count`, `technique_count`, one unknown dword,
`object_count`. Then one 16-byte record per parameter — typedef offset, value
offset, flags, annotation count — each followed by its annotations.

**An annotation is 2 dwords, not 4.** This is the one trap. Assuming 4
desynchronises the walk a few parameters in, and it does *not* raise: it yields
plausible-looking records with garbage names. `verify_all` therefore checks that
all 8,714 parameter names are C identifiers rather than merely that the walk
completed.

The typedef is `type`, `class`, name offset, semantic offset, `element_count`,
then the dimensions — and **the order of those two depends on the class**:

| Class | Order |
|---|---|
| `VECTOR` | columns, then rows |
| `SCALAR`, `MATRIX_ROWS`, `MATRIX_COLUMNS` | rows, then columns |

An asymmetry in the format, not a typo. A 1×4 vector read the wrong way round
becomes 4×1 and its default is read as one float instead of four.

Strings here are `uint32 size` then the bytes, with the size **including** the
NUL — the opposite convention to the Ascaron header in §2.1. Same storage rule,
different number written, because the two sections have different authors.

### 4.1 What this gives

Every parameter has a name, a semantic, a type and a compiled-in default:

| Name | Semantic | Type | Default |
|---|---|---|---|
| `g_Bumpiness` | `Bumpiness` | float | 1 |
| `g_Reflectivity` | `Reflectivity` | float | 1 |
| `g_EmissiveFactor` | `EmissiveFactor` | float | 2 |
| `g_DetailRepeat` | `DetailRepeat` | float | 12 |
| `g_DetailIntensity` | `DetailIntensity` | float | 0.5 |
| `g_Roughness` | `Roughness` | float | 1 |
| `g_MaterialDiffuse` | `Diffuse` | float4 | 1,1,1,1 |
| `g_MaterialSpecular` | `Specular` | float4 | 1,1,1,1 |
| `g_MaterialAmbient` | `Ambient` | float4 | 1,1,1,1 |
| `g_MaterialEmissive` | `Emissive` | float4 | 0,0,0,1 |
| `g_SpecularPower` | `SpecularPower` | float | 20 |
| `t_Reflection` | `ENVIRONMENT` | texture | — |

`t_Reflection` carrying the semantic **`ENVIRONMENT`**, with `s_Reflection`
declared a `SAMPLERCUBE`, independently confirms what §3 calls the environment
slot.

### 4.2 A fifth of the parameters a scene writes are inert

The exporter writes the same fixed block — the six universal semantics — onto
every effect regardless of which shader it names. **17,998 of 82,872 parameter
writes in stock data address a semantic the shader does not declare**, and D3DX
silently ignores those.

It is coherent rather than random: `mat_main_2` and `mat_biotechanim` declare
neither `Bumpiness` nor `Roughness`, and neither of them has a normal-map slot.

This matters to a modder, so the effect editor marks such rows: editing one
changes the scene file and nothing on screen. Same class of problem as `PRJ001`
and `PRJ005`.

## 5. `<Material>`: shape confirmed, order still inferred

`mat_main` declares `Diffuse`, `Specular`, `Ambient` and `Emissive` as 1×4
vectors plus a scalar `SpecularPower` — 4×4 + 1 = **17**, exactly the count in
every `<Material>` block. So the D3DMATERIAL9 *shape* is now confirmed from the
shader rather than inferred from the float count.

**It does not confirm the order of those 17 floats in the scene XML**, and this
is worth being careful about: D3DX binds parameters **by semantic, not by
position**, so the order in which the shader happens to declare them is not
evidence. The shader's declaration order (Diffuse, Specular, Ambient, Emissive)
is in fact *different* from the D3DMATERIAL9 struct order (Diffuse, Ambient,
Specular, Emissive).

The corpus favours the D3DMATERIAL9 reading. Over 16,444 materials:

| Row | Most common values |
|---|---|
| 0 | `1,1,1,0` — diffuse |
| 1 | `1,1,1,0`, then `0,0,0,0` |
| 2 | `1,1,1,0`, then **`4,4,4,0`** and **`3,3,3,0`** |
| 3 | `0,0,0,0` — emissive |
| scalar | 200, 20, 100, 150, 300 — specular power |

Row 2 takes values **above 1**, which is defensible for a specular intensity and
hard to justify for an ambient colour. Together with row 3 sitting at zero and
the scalar taking classic specular-power values, that supports
diffuse / ambient / specular / emissive / power — the existing reading. It is
evidence, not proof, and the row labels stay marked provisional in the UI.

(The fourth component is `0.0` in every common case, which is itself unexplained
for a D3DMATERIAL9 alpha.)

## 6. Techniques, passes and the object table

*Decoded 2026-08-23.* The blob is now walked end to end — **230 of 230 files
land exactly on their last byte**, which is the only honest check available:
a D3DX walk that has drifted does not raise, it keeps producing records.

After the parameter table:

```
per technique:   uint32 name offset, annotation count, pass count
                 annotations       (2 dwords each)
                 passes
per pass:        uint32 name offset, annotation count, state count
                 annotations       (2 dwords each)
                 states            (4 dwords each: operation, index,
                                    typedef offset, value offset)

uint32  inline_count               objects stored by id
uint32  resource_count
inline_count   * { uint32 id, uint32 size, bytes padded to 4 }
resource_count * { uint32 technique, pass, element, state, usage,
                   uint32 size, bytes padded to 4 }
```

**The resource header is five dwords.** That was settled by trying four, five
and six across the corpus: four leaves 193 files unaccounted for, six leaves
207, five lands all 230 exactly. It is the same discipline as §2.3 — a file
whose bytes are not all accounted for is a file the layout does not describe.

Across the corpus: **500 techniques, 609 passes, 3,514 objects**, of which
1,387 are compiled shaders.

### 6.1 What the object header locates

`technique`, `pass` and `state` say where an object is used, so a shader can be
attributed to the pass that runs it:

```
head=(2, 0, -1, 0, 0)   360 bytes  vs_1_1   technique 2 "Occluded", pass 0
head=(2, 1, -1, 1, 0)  2120 bytes  ps_2_0   technique 2, pass 1
head=(-1, 60, 0, 0, 1)   13 bytes           not a pass: parameter 60
```

An object written for a **parameter** rather than a pass stores `0xFFFFFFFF`
as the technique and the parameter's index in the pass field, which is why
`EffectObject` keeps both raw rather than resolving them to names.

Shader bytecode is recognised by its Direct3D 9 version token — high word
`0xFFFE` for a vertex shader, `0xFFFF` for a pixel one. Four models appear:
`vs_1_1` (178 shaders), `ps_2_0` (83), `vs_2_0` (81), `ps_1_1` (74).

### 6.2 The header strings are not the technique list

§2.2 guessed that the leftover header strings were "technique and parameter
names". They are not the technique names: `mat_main`'s header carries `DoIt`,
`V20P20`, `FFPHigh`, `FFPLow`, while the effect declares `V20P20`,
`ShadowMapV20P20` and `Occluded`. Only one name is common to both. Whatever
`DoIt`, `FFPHigh` and `FFPLow` are, they are Ascaron's own — most likely read by
the undecoded chunks below — and `Shader.techniques` is the list to trust.

## 7. Still not decoded

- **state values.** Each pass's states are read as four dwords so the walk
  lands, but what operation each index means needs the Direct3D state table,
  and what a shader does with a parameter needs the bytecode disassembled.
- the payloads of the five trailing chunks — `VARL`, `ANI `, `TRIG`, `INIT`,
  `MAIN`.

The blob is kept verbatim, so nothing is lost.

## 8. Two files are a different container

`ObjectFieldScripts/Meshes/Blender/mat_dist_2.bsd9` and `mat_dist_3.bsd9` have
no `XF` magic — two `uint32` sizes (compressed, then uncompressed) followed by
high-entropy data. **No scene references either of them.** They are refused with
`UnsupportedFormat` rather than guessed at.

## 9. Implementation

`dsotools/formats/bsd9.py` — read-only; the app never writes shaders.
`dsotools/edit/meshview.py` asks the shader first and falls back to the suffix
convention when it names nothing useful, or when the shader is not installed
(466 effects reference one that is not present).

`verify_all` enforces four claims: every file parses with all bytes accounted
for, every parameter name in every blob is a C identifier (the integrity check
for the walk), **every effect walk lands exactly on the end of its blob**, and
every effect's texture count equals its shader's slot count.
