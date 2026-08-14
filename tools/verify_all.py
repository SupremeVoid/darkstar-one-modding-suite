#!/usr/bin/env python3
"""
Whole-project verification: the test suite, plus every claim checked against
real game data.

    python tools/verify_all.py --data "<...>/extracted game data" [--mod "<...>/Mod"]

The unit tests prove the code does what it was written to do.  This proves the
*claims in the specs* still hold against the actual files -- which is a
different question, and the one that has caught every real bug in this project
so far:

    the DXT5 alpha weights          (both implementations wrong the same way)
    the missing trailing newline    (only a modder-authored file had none)
    the .tex uninitialised tail     (a byte-exact writer had to preserve it)

Each check prints PASS/FAIL and the measurement behind it, so a regression shows
up as a number moving rather than as a vague failure weeks later.

Exit code is 1 if anything failed.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

from dsotools import index as idxmod, validate, vfs as vfsmod  # noqa: E402
from dsotools.formats import (  # noqa: E402
    a2d, anim, bsd9, dds, ini, scene, screen, threedo,
)
from dsotools.errors import DsoError  # noqa: E402
from dsotools.project import Mod  # noqa: E402

RESULTS = []

#: Checks that need no game data.  They must still run on a bare checkout --
#: the Qt lint especially, since it is the only thing covering the widget code
#: and the machine building a release may well not have the game on it.
NO_DATA_NEEDED = {
    "unit tests",
    "core imports without third-party packages",
    "Qt layer passes the static check",
    "DXT5 alpha table matches the S3TC spec",
}


#: Returned as the ``ok`` value by a check that could not run.
#:
#: The two mod checks used to return ``True`` with the detail "no --mod;
#: skipped", so a run with no mod printed two PASS lines for work nobody did --
#: the exact anti-pattern ``Report.skipped`` exists to prevent, in the tool
#: whose whole job is enforcing it.  ``None`` makes "did not run" a third
#: outcome rather than a flavour of success.
SKIPPED = None


def check(name):
    def deco(fn):
        RESULTS.append((name, fn))
        return fn

    return deco


# --------------------------------------------------------------------------
# running one check over the corpus in parallel
#
# The two per-model checks were 52 of the run's 81 seconds, and both are the
# same shape: independent work per file, a tiny answer per file.  Parsing is
# pure Python and holds the GIL, so threads buy nothing -- processes do.
# Measured on the .3do round-trip: 45.2s serial, 11.2s on 8 workers, with the
# same 3,110/3,110 result.
#
# The pool is an optimisation and never a weakening: if it cannot start, the
# check runs serially rather than skipping.  Nothing here decides *whether* a
# file is examined, only which process examines it.
# --------------------------------------------------------------------------

_WORKER_VFS = None


def _worker_vfs(game, data):
    """One VFS per worker process, built once and reused across its chunk."""
    global _WORKER_VFS
    if _WORKER_VFS is None:
        _WORKER_VFS = (
            vfsmod.from_install(game) if game else vfsmod.from_extracted(data)
        )
    return _WORKER_VFS


def _threedo_rt_chunk(payload):
    game, data, paths = payload
    vfs = _worker_vfs(game, data)
    ok, bad = 0, []
    for vpath in paths:
        raw = vfs.read(vpath)
        try:
            if threedo.build(threedo.parse(raw)) == raw:
                ok += 1
            else:
                bad.append(vpath)
        except Exception as exc:  # noqa: BLE001
            bad.append(f"{vpath}: {exc}")
    return ok, bad


def _model_rules_chunk(payload):
    game, data, paths = payload
    vfs = _worker_vfs(game, data)
    checked = pairs = 0
    found = []
    for vpath in paths:
        spath = vpath[:-4] + ".shd"
        shadow = None
        if vfs.find(spath) is not None:
            shadow = vfs.read(spath)
            pairs += 1
        # Diagnostics are reported as text: only their *description* crosses
        # the process boundary, which is also all the summary line prints.
        found += [repr(d) for d in validate.check_model(vfs.read(vpath), vpath,
                                                        shadow, spath)]
        checked += 1
    return checked, pairs, found


class Ctx:
    def __init__(self, data, mod, game=None, serial=False):
        self.game = game
        self.data = data
        self.mod = mod
        #: Set by ``--serial``.  Kept because a pool hides tracebacks, and the
        #: first thing to try when a corpus check misbehaves is one process.
        self.serial = serial
        self._vfs = None
        self._full = None

    def map_chunks(self, worker, paths, workers=None):
        """Run ``worker`` over ``paths`` in parallel; yield each chunk's result."""
        n = workers or min(8, os.cpu_count() or 1)
        paths = list(paths)
        if self.serial or n < 2 or len(paths) < 64:
            return [worker((self.game, self.data, paths))]
        chunks = [(self.game, self.data, paths[i::n]) for i in range(n)]
        try:
            from concurrent.futures import ProcessPoolExecutor

            with ProcessPoolExecutor(max_workers=n) as pool:
                return list(pool.map(worker, chunks))
        except Exception:  # noqa: BLE001
            # A pool that will not start is a reason to be slow, not a reason
            # to report on files nobody looked at.
            return [worker((self.game, self.data, paths))]

    @property
    def vfs(self):
        if self._vfs is None:
            if self.game:
                self._vfs = vfsmod.from_install(self.game)
            else:
                self._vfs = vfsmod.from_extracted(self.data)
        return self._vfs

    @property
    def is_full_corpus(self):
        """Rate-based checks are meaningless on a handful of staged samples.

        With a partial extraction almost nothing resolves, and the failure says
        nothing about the code -- so those checks skip rather than cry wolf.
        """
        if self._full is None:
            n = sum(1 for _ in self.walk(".3do"))
            self._full = n >= 500
        return self._full

    def walk(self, suffix):
        for vpath in self.vfs.iter_paths():
            if vpath.lower().endswith(suffix):
                yield vpath


# --------------------------------------------------------------------------
# structural: the library itself
# --------------------------------------------------------------------------


@check("unit tests")
def _tests(ctx):
    exe = os.path.join(HERE, "offline_test_runner.py")
    try:
        import pytest  # noqa: F401

        cmd = [sys.executable, "-m", "pytest", "-q"]
    except ImportError:
        cmd = [sys.executable, exe]
    r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    tail = (r.stdout or r.stderr).strip().splitlines()
    return r.returncode == 0, tail[-1] if tail else "no output"


@check("Qt layer passes the static check")
def _app_lint(ctx):
    """The widget code cannot be unit-tested; this is what covers it instead.

    It exists because a bad edit moved two methods out of their class: the file
    compiled, imports succeeded, and the app died on launch.
    """
    r = subprocess.run(
        [sys.executable, os.path.join(HERE, "check_app.py")],
        cwd=ROOT, capture_output=True, text=True,
    )
    lines = [l for l in (r.stdout or "").strip().splitlines() if l.strip()]
    return r.returncode == 0, lines[-1] if lines else "no output"


@check("core imports without third-party packages")
def _core_deps(ctx):
    """`import dsotools` must work on a bare interpreter.

    The 3do converter has always had "no third-party packages" as a property,
    and the library inherits it: pixel work lives behind the `image` extra.
    """
    code = (
        "import sys;"
        "sys.modules['PIL']=None; sys.modules['numpy']=None;"
        "sys.path.insert(0,'src');"
        "import dsotools, dsotools.vfs, dsotools.project, dsotools.validate, dsotools.index;"
        "from dsotools.formats import threedo, shd, scene, a2d, anim, ini, sounddb;"
        "print('ok')"
    )
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
    return r.returncode == 0, (r.stdout.strip() or r.stderr.strip().splitlines()[-1:])


# --------------------------------------------------------------------------
# format claims, against real files
# --------------------------------------------------------------------------


@check("scene XML round-trips byte-identically")
def _scene_rt(ctx):
    """Every scene, and a failure never stops the count.

    This used to let a ``ParseError`` escape, so the whole check died on the
    first unreadable file and never looked at the other ~1,184 scenes.  Three
    malformed files therefore hid **55 more** that parsed cleanly and
    serialised back with different formatting -- a byte-exactness failure, and
    invisible for as long as the check aborted ahead of it.  A check that stops
    early reports the first cause as if it were the only one.
    """
    ok = bad = 0
    worst = []
    repaired = []
    for vpath in ctx.walk(".xml"):
        raw = ctx.vfs.read(vpath)
        if not scene.is_scene(raw):
            continue
        try:
            sc = scene.parse(raw, path=vpath)
        except Exception as exc:  # noqa: BLE001 - count it, do not stop
            bad += 1
            worst.append(f"{vpath}: {exc}")
            continue
        if sc.repaired_tags:
            repaired.append(vpath)
        if sc.to_bytes() == raw:
            ok += 1
        else:
            bad += 1
            worst.append(vpath)
    note = f"{ok} scenes, {bad} differ {worst[:3]}"
    if repaired:
        # Not a failure: these round-trip byte-exactly.  Said out loud anyway,
        # because they are not well-formed XML and that is worth knowing.
        note += f"; {len(repaired)} needed a close-tag case repair"
    return bad == 0, note


@check(".bsd9 parses and accounts for every byte")
def _bsd9_parse(ctx):
    """The two known-different files are named, not silently tolerated."""
    OTHER_CONTAINER = {"mat_dist_2.bsd9", "mat_dist_3.bsd9"}
    ok = 0
    refused = []
    broken = []
    for vpath in ctx.walk(".bsd9"):
        raw = ctx.vfs.read(vpath)
        try:
            bsd9.parse(raw, path=vpath)
            ok += 1
        except Exception as exc:  # noqa: BLE001
            name = os.path.basename(vpath).lower()
            if name in OTHER_CONTAINER:
                refused.append(name)
            else:
                broken.append(f"{vpath}: {exc}")
    note = f"{ok} shaders, {len(refused)} in the other container"
    if broken:
        note += f", {len(broken)} FAILED {broken[:2]}"
    return not broken, note


@check(".bsd9 D3DX9 parameter tables walk cleanly")
def _bsd9_params(ctx):
    """The integrity check for the blob walk, and it has to be this one.

    A desynchronised walk does not raise -- it produces plausible-looking
    records with garbage names.  Requiring every parameter name to be a C
    identifier is what actually catches it, and these values reach an editor.
    """
    import re

    ident = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
    shaders = params = 0
    bad = []
    for vpath in ctx.walk(".bsd9"):
        raw = ctx.vfs.read(vpath)
        try:
            sh = bsd9.parse(raw, path=vpath)
        except Exception:  # noqa: BLE001 - the parse check reports those
            continue
        try:
            plist = sh.parameters
        except Exception as exc:  # noqa: BLE001
            bad.append(f"{vpath}: {exc}")
            continue
        shaders += 1
        for p in plist:
            params += 1
            if not p.name or not ident.match(p.name):
                bad.append(f"{vpath}: name {p.name!r}")
            elif p.semantic and not ident.match(p.semantic):
                bad.append(f"{vpath}: semantic {p.semantic!r}")
    return not bad, f"{shaders} shaders, {params} parameters, {len(bad)} bad {bad[:2]}"


@check(".bsd9 effect walk accounts for every byte of every blob")
def _bsd9_effect(ctx):
    """The whole D3DX9 blob, not just its parameters.

    This is the check that decided the layout. A D3DX walk that has drifted
    does not raise -- it keeps producing records -- so "it parsed" proves
    nothing. What proves it is landing **exactly** on the end of the blob, and
    that is what settled the resource header at five dwords: four and six leave
    most files unaccounted for, five lands all 230.

    Names are checked too, for the same reason the parameter check does it: a
    drifted walk yields plausible structure with garbage in the strings.
    """
    import re

    ident = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
    shaders = techniques = passes = objects = shaders_found = 0
    bad = []
    for vpath in ctx.walk(".bsd9"):
        try:
            sh = bsd9.parse(ctx.vfs.read(vpath), path=vpath)
        except Exception:  # noqa: BLE001 - the parse check reports those
            continue
        try:
            found = sh.techniques
        except Exception as exc:  # noqa: BLE001
            bad.append(f"{vpath}: {exc}")
            continue
        shaders += 1
        techniques += len(found)
        for technique in found:
            if not technique.name or not ident.match(technique.name):
                bad.append(f"{vpath}: technique {technique.name!r}")
            passes += len(technique.passes)
            for one in technique.passes:
                if not one.name or not ident.match(one.name):
                    bad.append(f"{vpath}: pass {one.name!r}")
        objects += len(sh.objects)
        shaders_found += sum(1 for o in sh.objects if o.shader_model)

    return not bad, (f"{shaders} shaders, {techniques} techniques, {passes} passes, "
                     f"{objects} objects ({shaders_found} compiled shaders), "
                     f"{len(bad)} bad {bad[:2]}")


@check("shader slot count == textures the scene binds")
def _bsd9_slots(ctx):
    """The measurement that turned the slot convention into a fact.

    Every effect binds exactly as many textures as its shader declares slots --
    which is what makes the pairing positional and total, and therefore what
    lets the viewport stop guessing from filenames.  An effect whose shader is
    not installed is counted separately; it is not evidence either way.
    """
    cache = {}
    agree = fewer = more = missing = 0
    examples = []
    for vpath in ctx.walk(".xml"):
        raw = ctx.vfs.read(vpath)
        if not scene.is_scene(raw):
            continue
        try:
            sc = scene.parse(raw, path=vpath)
        except Exception:  # noqa: BLE001 - the scene checks report that
            continue
        for mesh in sc.meshes():
            for eff in mesh.effects:
                if not eff.shader:
                    continue
                key = (eff.shader.lower(), vpath.rsplit("/", 1)[0].lower())
                if key not in cache:
                    e = ctx.vfs.resolve_reference(eff.shader, scene_path=vpath)
                    try:
                        cache[key] = (
                            bsd9.parse(e.read()).texture_slots if e is not None else None
                        )
                    except Exception:  # noqa: BLE001
                        cache[key] = None
                slots = cache[key]
                if slots is None:
                    missing += 1
                    continue
                n = len(eff.textures)
                if n == len(slots):
                    agree += 1
                elif n < len(slots):
                    fewer += 1
                    examples.append((vpath, eff.shader, len(slots), n))
                else:
                    more += 1
                    examples.append((vpath, eff.shader, len(slots), n))
    return (
        fewer == 0 and more == 0,
        f"{agree} effects match, {fewer} bind fewer, {more} bind more, "
        f"{missing} shader not installed {examples[:2]}",
    )


@check(".3do parse -> build is byte-identical")
def _threedo_rt(ctx):
    ok = 0
    worst = []
    for chunk_ok, chunk_bad in ctx.map_chunks(_threedo_rt_chunk, ctx.walk(".3do")):
        ok += chunk_ok
        worst += chunk_bad
    return not worst, f"{ok} models, {len(worst)} differ {worst[:3]}"


@check("MDL001-MDL007 fire on no stock model")
def _mdl_rules(ctx):
    """The measurement the severities were chosen from, kept as a check.

    Every one of these rules was counted over the whole corpus *before* it was
    given a severity, because a rule that flags 3,000 of Ascaron's own models
    is a rule that gets switched off -- and then the real findings go with it.
    The answer was zero, for all seven, over 3,110 models and 1,738 .3do/.shd
    pairs.  If this ever goes red, either the data changed or a rule started
    crying wolf.
    """
    checked = pairs = 0
    found = []
    for n, p, chunk in ctx.map_chunks(_model_rules_chunk, ctx.walk(".3do")):
        checked += n
        pairs += p
        found += chunk
    return (
        not found,
        f"{checked} models, {pairs} with a shadow volume, {len(found)} finding(s) "
        f"{found[:2]}",
    )


@check(".tex parse -> build is byte-identical")
def _tex_rt(ctx):
    ok = bad = 0
    for vpath in ctx.walk(".tex"):
        raw = ctx.vfs.read(vpath)
        if a2d.build(a2d.parse(raw)) == raw:
            ok += 1
        else:
            bad += 1
    return bad == 0, f"{ok} indexes, {bad} differ"


@check(".screen round-trips, and every reference it names resolves")
def _screen_rt(ctx):
    """Two independent claims, because "it parsed" proves neither.

    The walk never searches for a tag, so agreeing with the file's own length
    fields to the last byte is one check; the other is that the resources the
    class blocks name are really there -- 2,247 of 2,251 on stock data, the
    four exceptions being one malformed reference in Ascaron's own files.
    """
    have = {p.lower() for p in ctx.vfs.iter_paths()}
    ok = bad = 0
    refs = resolved = elements = 0
    worst = []
    for vpath in ctx.walk(".screen"):
        raw = ctx.vfs.read(vpath)
        try:
            parsed = screen.parse(raw, path=vpath)
        except Exception as exc:  # noqa: BLE001
            bad += 1
            worst.append(f"{vpath}: {exc}")
            continue
        if parsed.to_bytes() == raw:
            ok += 1
        else:
            bad += 1
            worst.append(vpath)
        elements += len(parsed)
        for element in parsed:
            for reference in element.references():
                refs += 1
                resolved += reference.replace("\\", "/").lower() in have
    rate = resolved / refs if refs else 1.0
    return (
        bad == 0 and rate == 1.0,
        f"{ok} screens, {elements} elements, {bad} differ; "
        f"{resolved}/{refs} references resolve {worst[:2]}",
    )


@check("the element tree accounts for every screen's declared top-level count")
def _screen_tree(ctx):
    """The parentage is derived, so it has to answer to something.

    A ``.screen`` says how many of its elements are top level and never says
    who owns the rest.  The tree here is worked out from what the engine's
    widgets build -- a slider owns the four sub-controls that follow it -- and
    the count in the header is the independent answer key it is never given.

    The second number is the reason any of it matters: a child's rectangle is
    an offset from its parent, so resolved through the tree it lands *on* its
    parent, and read flat it does not.
    """
    from dsotools.edit import screentree

    agree = total = 0
    kids = on_parent = 0
    worst = []
    for vpath in ctx.walk(".screen"):
        parsed = screen.parse(ctx.vfs.read(vpath), path=vpath)
        total += 1
        if screentree.consistent(parsed):
            agree += 1
        else:
            derived = sum(1 for x in screentree.parents(parsed.elements) if x < 0)
            worst.append(f"{vpath}: declares {parsed.declared_children}, "
                         f"derived {derived}")
        parent_of = screentree.parents(parsed.elements)
        where = screentree.origins(parsed.elements, parent_of)
        for i, element in enumerate(parsed.elements):
            if parent_of[i] < 0:
                continue
            kids += 1
            px, py = where[parent_of[i]]
            pw, ph = parsed.elements[parent_of[i]].rect[2:]
            x, y = where[i]
            w, h = element.rect[2:]
            ox = max(0, min(x + w, px + pw) - max(x, px))
            oy = max(0, min(y + h, py + ph) - max(y, py))
            on_parent += (ox * oy) / max(w * h, 1) > 0.5
    return (
        agree == total and total > 0,
        f"{agree}/{total} screens agree with their header; {kids} children, "
        f"{on_parent} sit on their parent {worst[:2]}",
    )


@check("the shipped Lua API database is complete and addressable")
def _lua_api(ctx):
    """The reference is only useful if every symbol can be looked up.

    318 symbols extracted from 325 documentation pages; the seven that are not
    symbols are the index, five overview pages and the modding guide.  Two of
    the pages carry a copied ``<title>``, so the count also proves the symbol
    is named by its **signature** -- keyed on the title, two events collide and
    one is lost.
    """
    from dsotools import scriptdoc

    database = scriptdoc.bundled()
    if database is None:
        return False, "this build ships no lua_api.json (run tools/chm_to_json.py)"
    index = scriptdoc.index(database)
    symbols = database["symbols"]
    constants = sum(len(v) for v in database["constants"].values())
    namespaces = [n for n in database["namespaces"] if n.startswith("N")]
    ok = (len(index) == len(symbols) == 318 and len(namespaces) == 22
          and constants == 223)
    return ok, (f"{len(symbols)} symbols, {len(index)} addressable, "
                f"{len(namespaces)} N* namespaces, {constants} constants")


@check("the sound database round-trips, and the prober agrees with all of it")
def _sound_database(ctx):
    """Two claims at once, because the second is what makes the first useful.

    The database has to survive a read/write cycle byte for byte -- it is
    hand-edited, and a reflowed save destroys diff-against-stock. And the
    metadata prober has to reproduce every ``Channels``, ``Freq`` and
    ``Duration`` the file declares, because those are what the suite will write
    when a mod adds a sound, and the engine reads them instead of the file.

    Agreeing with 442 entries written by Ascaron's own tool is the strongest
    statement available that a sound added here will look like one of theirs.
    """
    from dsotools.formats import audio, sounddb

    if not ctx.game:
        return SKIPPED, "no --game given"
    full = os.path.join(ctx.game, "KlangErzeugerDefault.xml")
    if not os.path.isfile(full):
        return SKIPPED, "this installation has no KlangErzeugerDefault.xml"

    with open(full, "rb") as handle:
        raw = handle.read()
    db = sounddb.parse(raw, path=full)
    groups = sum(1 for _ in db.all_groups())
    entries = list(db.entries())
    exact = db.to_bytes() == raw

    probed = 0
    absent = 0
    wrong = []
    for entry in entries:
        target = os.path.join(ctx.game, entry.path().replace("/", os.sep))
        if not os.path.isfile(target):
            absent += 1
            continue
        try:
            info = audio.probe(target)
        except DsoError as exc:
            wrong.append(f"{entry.name}: {exc.message}")
            continue
        probed += 1
        if entry.frequency and info.frequency != entry.frequency:
            wrong.append(f"{entry.name}: {info.frequency} vs {entry.frequency} Hz")
        elif entry.channels and info.channels != entry.channels:
            wrong.append(f"{entry.name}: {info.channels} vs {entry.channels} ch")
        elif entry.duration and info.samples:
            drift = abs(info.samples - entry.duration) / entry.duration
            # WAV is read exactly; MP3 length is derived and lands within a
            # fraction of a percent. See tests/test_audio.py.
            if drift > (0.0 if info.kind == "wav" else 0.005):
                wrong.append(f"{entry.name}: {info.samples} vs {entry.duration}")

    ok = exact and not wrong and groups > 0 and probed > 400
    return ok, (f"{groups} groups, {len(entries)} sounds, "
                f"round-trip {'exact' if exact else 'DIFFERS'}, "
                f"{probed} probed and agreeing, {absent} absent"
                + (f", {len(wrong)} disagree {wrong[:2]}" if wrong else ""))


@check("the shipped mission table accounts for every chunk in missions.bin")
def _stock_missions(ctx):
    """154 chunks = 150 missions + exactly 4 libraries.

    The extraction reads a disassembly, so "it produced 150 records" proves
    nothing on its own -- a parser that quietly drops a record would still look
    healthy. What makes it falsifiable is that every chunk is *accounted for*:
    each one either registers a mission or is one of the four named libraries.

    Also re-checks the shipped JSON against the bundle rather than trusting it,
    because the table is generated once and then rides along in the package.
    """
    from dsotools import luac, missions

    if not ctx.game:
        return SKIPPED, "no --game given"
    bundle = os.path.join(ctx.game, *missions.STOCK_BUNDLE.split("/"))
    if not os.path.isfile(bundle):
        return SKIPPED, "this installation has no lua/mission/missions.bin"
    if not luac.find_compiler():
        return SKIPPED, "ScriptCompiler.exe is not installed"

    with open(bundle, "rb") as handle:
        chunks = luac.chunk_names(handle.read())
    found = missions.index(bundle)
    silent = [c for c in chunks if c not in {m.source for m in found}]
    libraries = {"battlelib", "battlelibex", "cameralib", "missionlib"}
    unexplained = [c for c in silent
                   if os.path.basename(c)[:-4].lower() not in libraries]

    shipped = missions.stock()
    same = ([m.to_dict() for m in sorted(found, key=lambda m: m.name)]
            == [m.to_dict() for m in shipped])
    ok = not unexplained and len(silent) == 4 and same and len(found) == 150
    return ok, (f"{len(chunks)} chunks, {len(found)} missions, "
                f"{len(silent)} libraries, "
                f"shipped table {'matches' if same else 'DIFFERS'} "
                f"({len(shipped)} records)"
                + (f", {len(unexplained)} unexplained {unexplained[:3]}"
                   if unexplained else ""))


@check("every shipped .res string table round-trips byte-identically")
def _string_tables(ctx):
    """The format claim in specs/string_tables.md, re-checked on real files.

    Worth a corpus check rather than a unit test because the layout was decoded
    from three files and then asserted of all of them: a table is a count, a run
    of 16-byte records and a blob, with nothing in the header to catch a wrong
    reading.  Byte-exact rebuild plus "text ends exactly at EOF" is what makes
    the reading falsifiable.
    """
    from dsotools.formats import res as resfmt

    if not ctx.game:
        return SKIPPED, "no --game given"
    found = []
    for root, _dirs, files in os.walk(ctx.game):
        for name in files:
            if name.lower().endswith(".res"):
                found.append(os.path.join(root, name))
    if not found:
        return SKIPPED, "no .res files in this installation"

    entries = 0
    bad = []
    for path in sorted(found):
        with open(path, "rb") as handle:
            blob = handle.read()
        try:
            table = resfmt.parse(blob, path=path)
        except Exception as exc:            # noqa: BLE001 - report, do not stop
            bad.append(f"{os.path.basename(path)}: {exc}")
            continue
        entries += len(table)
        if resfmt.build(table) != blob:
            bad.append(f"{os.path.basename(path)}: does not rebuild")
    return not bad, (f"{len(found)} table(s), {entries} entries" if not bad
                     else "; ".join(bad[:3]))


@check("the installation matches the recorded stock baseline")
def _stock_baseline(ctx):
    """Stock state is recorded data; anything else is reported as a delta.

    Works on either edition -- the GOG and Steam builds differ only in the DRM
    wrapper over ``.text``, and of the 2,687 loose content files none differ.

    A modded installation is not a failure of this check; it is the answer.
    What would be a failure is an installation the baseline cannot recognise at
    all, which means the build is not the one the suite was measured against.
    """
    from dsotools import baseline

    table = baseline.bundled()
    if table is None:
        return False, "this build ships no stock_baseline.json"

    edition = baseline.detect_edition(ctx.game, table)
    if edition is None:
        return False, ("this executable is neither recorded edition; the "
                       "baseline does not describe it")
    found = baseline.classify(ctx.game, baseline=table, edition=edition)
    changed = found[baseline.MODIFIED] + found[baseline.ADDED]
    return True, (
        f"{edition}: {len(found[baseline.UNCHANGED])} unchanged, "
        f"{len(found[baseline.MODIFIED])} modified, {len(found[baseline.ADDED])} added, "
        f"{len(found[baseline.MISSING])} missing"
        + (f" {sorted(changed)[:3]}" if changed else ""))


@check("the shipped engine API table matches the executable")
def _engine_table(ctx):
    """``lua_engine.json`` is scanned out of the game; it must still agree.

    Skipped against a DRM-wrapped build, whose ``.text`` is encrypted and whose
    registration tables cannot be read at all -- reporting a mismatch there
    would be reporting the wrapper, not a regression.
    """
    import sys as _sys

    from dsotools import scriptdoc

    tools = os.path.join(ROOT, "tools")
    if tools not in _sys.path:
        _sys.path.insert(0, tools)
    import exe_api_scan

    shipped = scriptdoc.engine()
    if shipped is None:
        return False, "this build ships no lua_engine.json"

    exe = os.path.join(ctx.game, "DarkStarOne.exe")
    if not os.path.isfile(exe):
        return True, f"{shipped['count']} functions shipped; no executable here"

    # A DRM wrapper encrypts .text and leaves .data alone, and the tables are
    # in .data -- so a wrapped installation re-scans exactly like a plain one.
    image = exe_api_scan.Image(exe)
    tables = exe_api_scan.scan(exe)
    live = {f"{ns}.{n}" for ns, fns in tables.items() for n, _f in fns
            if ns.startswith("N") or ns in ("MissionLib", "CameraLib")}
    stored = scriptdoc.implemented(shipped)
    same = live == stored
    missing = sorted(live - stored)[:3]
    return same, (f"{len(stored)} shipped, {len(live)} in the executable"
                  + (" (DRM-wrapped, scanned anyway)" if image.is_wrapped() else "")
                  + (f"; differs: {missing}" if not same else ""))


@check("the shipped scripts raise nothing but known stubs")
def _lua_calls(ctx):
    """A guard on the checker, not on the scripts.

    Everything the game ships must come out clean once a script is judged
    against the API the executable really registers: no `absent`, no
    `unknown`, no `literal`.  What remains is `stub` -- the game's own calls to
    `NDebug.Message`, which really does nothing.

    Any other kind appearing here means the checker has regressed: comment
    stripping, the resolution of functions the libraries define for
    themselves, or the engine table itself.  Judged against the documentation
    alone this same scan reports two real functions as unknown, which is the
    state this check exists to prevent returning to.
    """
    import sys as _sys

    app = os.path.join(ROOT, "app")
    if app not in _sys.path:
        _sys.path.insert(0, app)
    from dso_app.session import Session

    session = Session()
    session.open_game(ctx.game)
    if not session.lua_api():
        return False, "no API database to check against"

    kinds = {}
    scripts = session.scripts()
    for row in scripts:
        for hit in session.check_script(session.read_script(row["key"])):
            kinds.setdefault(hit["kind"], set()).add(hit["symbol"])
    defined = session._lua_defined()
    unexpected = {k: sorted(v) for k, v in kinds.items() if k != "stub"}
    return (
        not unexpected,
        f"{len(scripts)} scripts, {len(defined)} functions they define "
        f"themselves, {len(kinds.get('stub', ())) } stub call(s)"
        + (f"; UNEXPECTED {unexpected}" if unexpected else ""),
    )


@check(".anim round-trips byte-identically")
def _anim_rt(ctx):
    """Round-trip, and count the stretched drawables rather than failing them.

    The two stored sizes are the *drawn* size and the *source image* size, not
    one size written twice, so 57 shipped drawables differ on purpose -- every
    one a nine-slice frame.  This check used to call that a failure.
    """
    ok = bad = stretched = 0
    for vpath in ctx.walk(".anim"):
        raw = ctx.vfs.read(vpath)
        a = anim.parse(raw, path=vpath)
        ok += a.to_bytes() == raw
        bad += a.to_bytes() != raw
        stretched += a.stretched
    return bad == 0, f"{ok} drawables, {bad} differ, {stretched} stretched (nine-slice)"


@check("INI round-trips byte-identically")
def _ini_rt(ctx):
    ok = bad = 0
    worst = []
    for vpath in ctx.walk(".ini"):
        raw = ctx.vfs.read(vpath)
        try:
            if ini.parse(raw, path=vpath).to_bytes() == raw:
                ok += 1
            else:
                bad += 1
                worst.append(vpath)
        except Exception as exc:  # noqa: BLE001
            bad += 1
            worst.append(f"{vpath}: {exc}")
    return bad == 0, f"{ok} files, {bad} differ {worst[:3]}"


@check("DDS: numpy and pure-python decoders agree")
def _dds_agree(ctx):
    if dds._np is None:
        return True, "numpy absent; skipped"
    checked = 0
    for vpath in ctx.walk(".dds"):
        img = dds.parse(ctx.vfs.read(vpath), path=vpath)
        if not img.fourcc:
            continue
        lvl = min(6, len(img.levels) - 1)
        w, h = img.level_size(lvl)
        if dds._decode_dxt_numpy(img.levels[lvl], w, h, img.fourcc) != dds._decode_dxt_python(
            img.levels[lvl], w, h, img.fourcc
        ):
            return False, f"decoders disagree on {vpath}"
        checked += 1
        if checked >= 60:
            break
    return True, f"{checked} textures, both paths identical"


@check("DXT5 alpha table matches the S3TC spec")
def _dxt5_table(ctx):
    """Values computed by hand, outside this codebase.

    The cross-check above cannot catch an error here: both decoders were written
    from the same formula and were wrong the same way.
    """
    cases = {
        (255, 0): [255, 0, 218, 182, 145, 109, 72, 36],
        (0, 255): [0, 255, 51, 102, 153, 204, 0, 255],
    }
    for (a0, a1), expect in cases.items():
        got = dds.dxt5_alpha_table(a0, a1)
        if got != expect:
            return False, f"a0={a0} a1={a1}: {got} != {expect}"
    return True, f"{len(cases)} reference tables match"


# --------------------------------------------------------------------------
# spec claims
# --------------------------------------------------------------------------


@check("SCN001: EffectContainer count == submesh_total")
def _scn001(ctx):
    """specs/scene.md §6: measured at 9,557/9,559 on stock data.

    Runs on whatever resolves, so it is meaningful on a partial corpus too --
    unlike a *rate*, an invariant either holds for a pair or it does not.
    """
    import struct

    KNOWN_BAD = {"mainshape_20.3do"}
    agree = disagree = 0
    examples = []
    unreadable = 0
    # 9,806 references point at 2,762 distinct models, and the number wanted
    # sits 0x30 bytes into each.  Reading the file per *reference* meant
    # decompressing 1.7 GB to look at four bytes 9,806 times.
    totals: dict = {}

    def submesh_total(entry):
        if entry.vpath not in totals:
            head = entry.read()[:0x1000]
            totals[entry.vpath] = (
                struct.unpack_from("<I", head, 0x30)[0]
                if head[:4] == b"OD3 " else None
            )
        return totals[entry.vpath]
    for vpath in ctx.walk(".xml"):
        raw = ctx.vfs.read(vpath)
        if not scene.is_scene(raw):
            continue
        try:
            sc = scene.parse(raw, path=vpath)
        except Exception:  # noqa: BLE001 - an unreadable scene is not a SCN001 result
            # Counted and reported, never silently dropped: an invariant that
            # could not be evaluated must not read as an invariant that held.
            unreadable += 1
            continue
        for mesh in sc.meshes():
            if not mesh.model:
                continue
            e = ctx.vfs.resolve_reference(mesh.model, scene_path=vpath)
            if e is None:
                continue
            total = submesh_total(e)
            if total is None:
                continue
            if len(mesh.effects) == total:
                agree += 1
            elif os.path.basename(mesh.model).lower() in KNOWN_BAD:
                agree += 1
            else:
                disagree += 1
                examples.append((vpath, mesh.model, total, len(mesh.effects)))
    n = agree + disagree
    rate = agree / n if n else 1.0
    note = f"{agree}/{n} ({rate:.2%}) {examples[:2]}"
    if unreadable:
        note += f"; {unreadable} scene(s) could not be read"
    # An unreadable scene is not a passing scene.
    return disagree == 0 and unreadable == 0, note


@check("reference resolution rates match specs/scene.md §4.2")
def _resolution(ctx):
    if not ctx.is_full_corpus:
        return True, "partial corpus; rate check skipped"
    i = idxmod.build_index(ctx.vfs)
    s = i.stats()
    # Through AssetIndex.execute(), not i.db -- every query in the project goes
    # under the index's lock, including this one.  See index.py "THREADS".
    models = i.execute(
        "SELECT COUNT(*), SUM(dst IS NOT NULL) FROM refs WHERE kind='model'"
    )[0]
    tex = i.execute(
        "SELECT COUNT(*), SUM(dst IS NOT NULL) FROM refs WHERE kind='texture'"
    )[0]
    mrate = (models[1] or 0) / models[0] if models[0] else 1.0
    trate = (tex[1] or 0) / tex[0] if tex[0] else 1.0
    ok = mrate > 0.93 and trate > 0.95
    return ok, (
        f"models {models[1]}/{models[0]} ({mrate:.1%}), "
        f"textures {tex[1]}/{tex[0]} ({trate:.1%}), {s['assets']} assets"
    )


@check("archive precedence matches the measured mount order")
def _precedence(ctx):
    order = [a for a in vfsmod.DEFAULT_ARCHIVE_ORDER if a in vfsmod.OBSERVED_MOUNT_ORDER]
    expected = list(reversed(vfsmod.OBSERVED_MOUNT_ORDER))
    return order == expected, f"{order}"


@check("contested paths are few and all add-on vs base")
def _contested(ctx):
    c = ctx.vfs.contested()
    amb = ctx.vfs.ambiguous()
    odd = [k for k, v in c.items() if not any("add" in e.origin for e in v)]
    return len(odd) == 0, f"{len(c)} contested of {len(amb)} shadowed; {len(odd)} unexpected"


@check("atlas rescale keeps page, index and drawables consistent")
def _rescale(ctx):
    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        return True, "Pillow absent; skipped"
    from dsotools.edit.atlas import AtlasPage

    tex = next(iter(ctx.walk(".tex")), None)
    if tex is None:
        return True, "no .tex in corpus; skipped"
    p = AtlasPage.open(ctx.vfs, tex)
    before = len(p.sprites)
    p.rescale(2)
    problems = len(p.out_of_bounds()) + len(p.overlaps()) + len(p.anim_mismatches())
    out = p.save()
    reparsed = a2d.parse(out[p.tex_path])
    return problems == 0 and len(reparsed.subimages) == before, (
        f"{tex}: {before} sprites, {problems} problems, {len(out)} files written"
    )


# --------------------------------------------------------------------------
# mod, if given
# --------------------------------------------------------------------------


@check("mod scenes round-trip (modder-authored, not Ascaron's)")
def _mod_scenes(ctx):
    if not ctx.mod:
        return SKIPPED, "no --mod given"
    m = Mod(ctx.mod)
    ok = bad = 0
    worst = []
    for key, f in m.files().items():
        if key.startswith("loose:") or not key.endswith(".xml"):
            continue
        raw = f.read()
        if not scene.is_scene(raw):
            continue
        if scene.parse(raw, path=f.vpath).to_bytes() == raw:
            ok += 1
        else:
            bad += 1
            worst.append(f.vpath)
    return bad == 0, f"{ok} scenes, {bad} differ {worst[:3]}"


@check("mod validates without crashing the engine")
def _mod_validate(ctx):
    if not ctx.mod:
        return SKIPPED, "no --mod given"
    report = validate.validate_mod(Mod(ctx.mod), ctx.vfs)
    counts = report.counts()
    # Not asserting the mod is clean -- it is a third-party mod. Asserting the
    # validator runs to completion and produces a well-formed report.
    bad = [d for d in report if not d.code or not d.severity]
    return not bad, f"{len(report)} findings {counts}"


@check("a game installation is readable without extraction")
def _install_direct(ctx):
    """The .cpr archives are plain ZIP; no extraction step should be required."""
    if not ctx.game:
        return True, "no --game; skipped"
    v = vfsmod.from_install(ctx.game)
    archives = [ly.name for ly in v.layers if ly.name.startswith("cpr:")]
    n = len(v)
    sample = next((p for p in v.iter_paths() if p.lower().endswith(".xml")), None)
    if sample:
        v.read(sample)
    return bool(archives) and n > 0, f"{len(archives)} archives, {n} assets, read {sample}"


# --------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.strip().split("\n")[0])
    ap.add_argument("--game", help="Darkstar One installation folder (reads .cpr directly)")
    ap.add_argument("--data", help="folder of already-extracted archives")
    ap.add_argument("--mod", help="a mod folder to check as well")
    ap.add_argument("--skip-tests", action="store_true")
    ap.add_argument(
        "--serial", action="store_true",
        help="run the corpus checks in one process. Slower (the two per-model "
             "checks are 4x faster across 8), but a pool swallows tracebacks, "
             "so this is the first thing to try when one misbehaves.",
    )
    ap.add_argument(
        "--list", action="store_true", help="print the check names and exit",
    )
    ap.add_argument(
        "--only",
        metavar="SUBSTR",
        help="run only checks whose name contains SUBSTR (case-insensitive; "
             "comma-separates several).  This exists because the full run can "
             "outlive a short-lived shell, not because a partial run is ever "
             "an acceptable answer -- a filtered run reports itself as PARTIAL "
             "and never as 'all checks passed'.",
    )
    args = ap.parse_args()

    if args.list:
        for name, _ in RESULTS:
            print(name)
        return 0

    wanted = [s.strip().lower() for s in (args.only or "").split(",") if s.strip()]

    for flag, value in (("--data", args.data), ("--game", args.game)):
        if value and not os.path.isdir(value):
            raise SystemExit(f"{flag} is not a directory: {value}")

    ctx = Ctx(args.data, args.mod, game=args.game, serial=args.serial)
    have_source = bool(args.data or args.game)
    failures = 0
    print("=" * 74)
    print("dsotools verification")
    print("=" * 74)

    skipped = 0
    for name, fn in RESULTS:
        if wanted and not any(w in name.lower() for w in wanted):
            skipped += 1
            continue
        if args.skip_tests and name == "unit tests":
            skipped += 1
            continue
        if not have_source and name not in NO_DATA_NEEDED:
            print(f"  SKIP  {name:<52} (no --game or --data)")
            skipped += 1
            continue
        t0 = time.time()
        try:
            ok, detail = fn(ctx)
        except Exception as exc:  # noqa: BLE001
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        dt = time.time() - t0
        if ok is SKIPPED:
            skipped += 1
            print(f"  SKIP  {name:<52} {dt:5.1f}s  {detail}")
            continue
        mark = "PASS" if ok else "FAIL"
        failures += not ok
        print(f"  {mark}  {name:<52} {dt:5.1f}s  {detail}")

    print("=" * 74)
    if failures:
        print(f"{failures} check(s) FAILED" + (f", {skipped} not run" if skipped else ""))
    elif skipped:
        # Never say "all checks passed" for a run that did not run them all.
        print(f"PARTIAL: {len(RESULTS) - skipped} check(s) passed, {skipped} not run")
    else:
        print("all checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
