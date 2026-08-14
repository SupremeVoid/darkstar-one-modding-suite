# Command-line tools

Nine converters and checkers over the *Darkstar One* file formats. They are
thin wrappers around `dsotools` — the same library the desktop application
uses — so the two cannot drift, and anything a tool can do is also two lines of
Python.

**Core needs Python 3.11+ and nothing else.** The image tools need Pillow;
`aimfind.py` also needs numpy.

```bash
pip install -e '.[image]'     # from the repository root
```

Two manuals go deeper than this page:

* **[`cli_3do.md`](cli_3do.md)** — the model round trip, in detail: what
  survives a pass through Blender, what does not, and why.
* **[`cli_aim.md`](cli_aim.md)** — images and UI atlases, including finding a
  sprite that has no file of its own.

---

## The tools

### Models — `.3do`

| Tool | Does |
|---|---|
| `3do2gltf.py` | `.3do` → glTF 2.0 (`.glb`), for Blender and everything else |
| `gltf23do.py` | `.glb` → `.3do`, back into the game |
| `3do2obj.py` | `.3do` → Wavefront OBJ, when you only need the geometry |
| `dsvalidate.py` | check `.3do` / `.shd` structure, and a whole mod |

### Images — `.aim`, `.tex`

| Tool | Does |
|---|---|
| `aim2png.py` | `.aim` → PNG, every encoding including the SLD codec |
| `png2aim.py` | PNG → `.aim`, copying the original's metadata |
| `aimatlas.py` | work with UI atlases through their `.tex` indexes |
| `aimfind.py` | locate a named sprite inside the atlas pages |
| `aimvalidate.py` | check `.aim` images and `.tex` indexes |

---

## Usage

Every tool takes **a file or a folder**, and `-h` prints its own options.

Folders are scanned **non-recursively on purpose**. Asset folders nest deeply,
and a recursive default makes it far too easy to rewrite thousands of files by
accident.

### Models, end to end

```bash
# 1. Export the game's meshes
python3 cli/3do2gltf.py "<game>/3DView/objects" -o work/

# 2. Edit work/whatever.glb in Blender and export as .glb.
#    Keep "Custom Properties" on, and do not merge material slots.

# 3. Convert back
python3 cli/gltf23do.py work/whatever.glb -o mod/3DView/objects --force

# 4. Check it before launching the game
python3 cli/dsvalidate.py mod/3DView/objects
python3 cli/dsvalidate.py work/whatever.3do --compare "<stock>/whatever.3do"
```

The `--compare` form is the one worth remembering: it catches **structural
drift** — a changed submesh count, a lost LOD — that a standalone check cannot
see, because the file is perfectly valid and simply no longer matches the scene
that binds it.

### Images, end to end

```bash
# 1. Convert to PNG
python3 cli/aim2png.py "<game>/staticImages" -o work/

# 2. Edit work/whatever.png, keeping its exact pixel dimensions

# 3. Convert back, copying the original's metadata
python3 cli/png2aim.py work/ --like-dir "<game>/staticImages" -o mod/

# 4. Check it
python3 cli/aimvalidate.py mod/
```

**Most UI art is not a standalone file.** It lives inside an atlas page, and
the name you are looking for is in a `.tex` index rather than on disk:

```bash
python3 cli/aimfind.py Auftraege --scripts "<game>/scripts"
#   Auftraege   27 x 38  at ( 950,  521)  in  images\TexPage_8_2.aim
```

### Checking a whole mod

```bash
python3 cli/dsvalidate.py --mod "<Customization>/<Mod Name>" --game "<game>"
```

Runs the same diagnostics engine the application's Problems tab uses. Every
rule corresponds to a failure that is **silent in game**, and each says how that
was established. Exit status is non-zero when a mod has errors, so this drops
straight into CI.

---

## What they will not do

These are the library's rules, and the tools inherit them:

* **Nothing prints to stdout that is not the answer**, and nothing calls
  `exit()` from library code — errors are typed exceptions carrying the path
  and, where meaningful, the byte offset.
* **Nothing is guessed.** A field that has not been established is preserved
  verbatim rather than defaulted. Where a value cannot be derived, the tool
  refuses and says why instead of writing something plausible.
* **Edits are operations on an existing file**, never a fresh encode. That is
  what keeps the fields nobody has decoded intact — see
  [`../docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md) §2.
