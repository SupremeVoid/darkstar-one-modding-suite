"""
The diagnostics engine: a compiler's problem list for a mod.

Every finding is a :class:`Diagnostic` with a stable ``code``, a severity, a
file, and -- where one exists -- a described fix.  That shape is deliberate:
the GUI renders it as a clickable problem list, the CLI prints it, and CI can
fail a build on it, all without any of them knowing what the rules are.

WHAT EARNS A RULE
-----------------
Every rule here corresponds to a failure that is **silent in game**.  The engine
either ignores the mistake, or crashes with no useful message, or renders
something subtly wrong.  Rules that merely encode taste do not belong; a
validator that cries wolf gets switched off, and then the real findings go with
it.  Each rule's docstring says how the failure was established.

Severity means something specific:

    ERROR    the mod is broken or a change will not take effect
    WARNING  probably not what the author intended
    INFO     harmless, worth knowing
    HINT     might matter; the underlying rule is not fully established

Nothing here imports Qt, and no rule prints.
"""

from __future__ import annotations

import os
import re
import struct
import zipfile
from typing import Callable, Dict, Iterable, List, Optional, Tuple

from . import luascan
from . import missions as missionsmod
from . import scriptdoc
from . import vfs as vfsmod
from .errors import DsoError
from .formats import audio as audiofmt
from .formats import res as resfmt
from .formats import scene as scenefmt
from .formats import sounddb as sounddbfmt
from .project import Mod, FileState, VALIDITY_TOKEN

VERSION = "1.0"


class Severity:
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"
    HINT = "hint"

    _ORDER = {ERROR: 0, WARNING: 1, INFO: 2, HINT: 3}

    @classmethod
    def rank(cls, sev: str) -> int:
        return cls._ORDER.get(sev, 9)


class Diagnostic:
    """One finding."""

    __slots__ = ("code", "severity", "message", "path", "detail", "fix", "location")

    def __init__(self, code, severity, message, *, path=None, detail=None, fix=None, location=None):
        self.code = code
        self.severity = severity
        self.message = message
        self.path = path
        self.detail = detail
        #: Plain-language description of the automatic fix, or ``None`` if the
        #: user has to decide.  The GUI turns this into a button.
        self.fix = fix
        #: Line number, byte offset, or sub-image name -- whatever locates it.
        self.location = location

    def as_dict(self) -> dict:
        return {
            "code": self.code,
            "severity": self.severity,
            "message": self.message,
            "path": self.path,
            "detail": self.detail,
            "fix": self.fix,
            "location": self.location,
        }

    def __repr__(self) -> str:  # pragma: no cover
        where = f" {self.path}" if self.path else ""
        return f"[{self.severity.upper()} {self.code}]{where}: {self.message}"


class Report:
    """A collection of diagnostics with the summaries a UI actually needs.

    Findings are **capped per rule**.  One broken scene reference can produce
    thousands of identical rows, and a list that long is neither readable nor
    renderable -- the first version of this produced 24,000 items and took the
    window down with it.  The cap keeps the full *count* while holding only the
    first ``limit_per_rule`` examples, which is what a user acts on anyway.
    """

    #: Examples kept per rule.  The count is always exact; only the detail is cut.
    DEFAULT_LIMIT = 200

    def __init__(
        self,
        diagnostics: Optional[List[Diagnostic]] = None,
        *,
        limit_per_rule: int = DEFAULT_LIMIT,
    ) -> None:
        self.diagnostics: List[Diagnostic] = []
        self.limit_per_rule = limit_per_rule
        #: Total occurrences per code, including ones not kept.
        self.totals: Dict[str, int] = {}
        #: ``{rule: why it did not run}``.  A rule that could not run must never
        #: look like a rule that ran and found nothing -- that is the difference
        #: between "checked" and "clean", and conflating them is how a tool
        #: tells someone their mod is fine when it never looked.
        self.skipped: Dict[str, str] = {}
        self.extend(diagnostics or ())

    def skip(self, rule: str, reason: str) -> None:
        self.skipped[rule] = reason

    def add(self, diag: Diagnostic) -> None:
        n = self.totals.get(diag.code, 0) + 1
        self.totals[diag.code] = n
        if self.limit_per_rule <= 0 or n <= self.limit_per_rule:
            self.diagnostics.append(diag)

    def extend(self, diags: Iterable[Diagnostic]) -> None:
        for d in diags:
            self.add(d)

    def truncated(self) -> Dict[str, int]:
        """``{code: how many were dropped}`` -- never silently, always reported."""
        kept: Dict[str, int] = {}
        for d in self.diagnostics:
            kept[d.code] = kept.get(d.code, 0) + 1
        return {
            code: total - kept.get(code, 0)
            for code, total in self.totals.items()
            if total > kept.get(code, 0)
        }

    def sorted(self) -> List[Diagnostic]:
        return sorted(self.diagnostics, key=lambda d: (Severity.rank(d.severity), d.code, d.path or ""))

    def counts(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for d in self.diagnostics:
            out[d.severity] = out.get(d.severity, 0) + 1
        return out

    def by_code(self) -> Dict[str, List[Diagnostic]]:
        out: Dict[str, List[Diagnostic]] = {}
        for d in self.diagnostics:
            out.setdefault(d.code, []).append(d)
        return out

    @property
    def ok(self) -> bool:
        """True if nothing would stop this mod working."""
        return not any(d.severity == Severity.ERROR for d in self.diagnostics)

    def __len__(self) -> int:
        return len(self.diagnostics)

    def __iter__(self):
        return iter(self.sorted())

    def __repr__(self) -> str:  # pragma: no cover
        c = self.counts()
        return f"<Report {len(self)} findings {c}>"


# --------------------------------------------------------------------------
# project-level rules
# --------------------------------------------------------------------------


def check_manifest(mod: Mod) -> List[Diagnostic]:
    """PRJ001/PRJ004: can the game see this mod at all?"""
    out = []
    try:
        name = mod.display_name
    except DsoError as exc:
        return [Diagnostic("PRJ001", Severity.ERROR, str(exc), path=mod.root)]

    if not name:
        out.append(
            Diagnostic(
                "PRJ001",
                Severity.ERROR,
                "manifest has no mod_name; the game will show a blank entry",
                path=os.path.join(mod.root, "darkstarmod.ini"),
            )
        )
    if not mod.is_listable():
        out.append(
            Diagnostic(
                "PRJ004",
                Severity.ERROR,
                "no inifiles/items.ini -- the game will not list this mod, and "
                "reports nothing when it skips it",
                path=mod.root,
                detail="Established by experiment; see specs/scene.md 6b.",
                fix="Copy the stock inifiles/items.ini into the mod.",
            )
        )
    return out


def check_dead_files(mod: Mod) -> List[Diagnostic]:
    """PRJ005: files in a location the engine never reads.

    A mod's loose ``3DView/`` is not read -- confirmed in game by shipping the
    same deliberately-broken scene both ways: crash from the zip, nothing at all
    when loose.  ``images/`` was added the same way: an edited atlas page did
    nothing loose and appeared immediately from the zip.  Editing these files
    appears to do nothing, which is exactly the trap this project set out to
    remove.

    The message names the offending folder rather than hardcoding ``3DView/``,
    because being told about the wrong folder is worse than being told nothing.
    """
    out = []
    for f in mod.dead_files():
        root = vfsmod.normalise(f.vpath).split("/", 1)[0]
        out.append(
            Diagnostic(
                "PRJ005",
                Severity.WARNING,
                f"loose {root}/ file is never read by the engine",
                path=f.vpath,
                detail=f"Only user_data.zip supplies {root}/ content. "
                       "See specs/README.md 5.",
                fix="Move it into user_data.zip.",
            )
        )
    return out


def check_scripts_in_zip(mod: Mod) -> List[Diagnostic]:
    """PRJ007: scripts inside ``user_data.zip`` are never loaded.

    The mirror image of ``PRJ005``, and just as silent.  ``3DView/`` and
    ``images/`` must be in the zip; ``scripts/`` must **not** be.  The loader
    reads ``<mod>\\scripts\\user_scripts.bin`` and then globs
    ``<mod>\\scripts\\*.lua`` as real filesystem paths -- measured in game on
    2026-08-22 with two identical mods, one loose and one zipped: the loose
    scripts ran, the zipped ones did nothing at all.

    Worth its own code because the mistake is natural: script bundles *are*
    opened through the archive layer (``arc_CreateFile``), so putting them in
    the archive looks reasonable and fails without a word.
    """
    zip_path = mod.zip_path
    if not zip_path or not os.path.exists(zip_path):
        return []
    try:
        with zipfile.ZipFile(zip_path) as archive:
            names = [n for n in archive.namelist()
                     if vfsmod.normalise(n).lower().startswith("scripts/")
                     and not n.endswith("/")]
    except (OSError, zipfile.BadZipFile):
        return []          # PRJ003 reports an unreadable archive
    if not names:
        return []
    return [
        Diagnostic(
            "PRJ007",
            Severity.WARNING,
            f"{len(names)} script(s) inside user_data.zip are never loaded",
            path=zip_path,
            detail="; ".join(names[:6]) + (" ..." if len(names) > 6 else "")
                   + ". The engine reads scripts/ from the mod folder as real "
                     "files. See specs/mod_packaging.md 1.",
            fix="Move them loose into the mod's scripts/ folder.",
        )
    ]


#: The stock table, relative to a game install.  ENG is the only language every
#: edition ships; the others are looked at only if present.
STOCK_TABLES = ("strings/ENG/global.res", "strings/DEU/global.res",
                "strings/FRA/global.res")

#: What a StringId literal looks like.  Deliberately narrow: the engine accepts
#: any string, so a loose pattern would flag ordinary text as a broken id.
_STRING_ID_LITERAL = re.compile(r"""["']((?:ID|IDC|IDD|IDL|IDM)_[A-Z0-9_]{2,60})["']""")


def _mod_string_table(mod: Mod):
    """The mod's own table, or ``None``.  Returns ``(path, table_or_error)``."""
    path = os.path.join(mod.root, "strings", "user_strings.res")
    if not os.path.exists(path):
        return None, None
    try:
        return path, resfmt.parse(open(path, "rb").read(), path=path)
    except (DsoError, OSError, UnicodeDecodeError) as exc:
        return path, exc


def check_string_table(mod: Mod) -> List[Diagnostic]:
    """PRJ008: the mod's ``strings\\user_strings.res`` is unusable.

    The engine loads the table once at startup and every later lookup is a hash
    probe, so a malformed table does not fail loudly -- text simply never
    appears.  Two ids that hash alike are the same failure with a smaller blast
    radius: one of the two texts is unreachable, and which one depends on write
    order.  The shipped converter refused collisions outright, which is the
    behaviour this rule restores.
    """
    path, table = _mod_string_table(mod)
    if path is None:
        return []
    if isinstance(table, Exception):
        return [Diagnostic(
            "PRJ008", Severity.ERROR,
            "the mod's string table cannot be read",
            path=path,
            detail=str(getattr(table, "message", table))
                   + ". No text from this mod will appear in game.",
            fix="Rebuild it from the suite, or restore a known-good copy.",
        )]
    out = []
    collisions = table.collisions()
    if collisions:
        out.append(Diagnostic(
            "PRJ008", Severity.WARNING,
            f"{len(collisions)} hash collision(s) in the string table",
            path=path,
            detail="; ".join(f"0x{h:08x} used by {n} entries" for h, n in collisions[:4])
                   + ". Only the last entry on each key is reachable.",
            fix="Rename one id in each colliding pair.",
        ))
    return out


def check_string_ids(mod: Mod, stock: Optional["vfsmod.Vfs"]) -> List[Diagnostic]:
    """PRJ009: a script names a StringId that no loaded table defines.

    Every engine call that shows words takes a StringId, never a literal, so a
    typo here is invisible: the lookup misses and the game draws nothing at all
    -- no placeholder, no log line.  Ids are checked against the mod's own
    ``user_strings.res`` first and then the game's ``global.res``, because a
    mod may legitimately reuse stock text.

    Needs a game folder for the second half, so it is skipped rather than
    guessed at when none is open.
    """
    if stock is None:
        return []
    known = set()
    _path, table = _mod_string_table(mod)
    if table is not None and not isinstance(table, Exception):
        known |= set(table.by_hash())
    for candidate in STOCK_TABLES:
        try:
            known |= set(resfmt.parse(stock.read(candidate)).by_hash())
        except (DsoError, KeyError, OSError, UnicodeDecodeError):
            continue
    if not known:
        return []

    missing: Dict[str, List[str]] = {}
    scripts = os.path.join(mod.root, "scripts")
    for name in sorted(os.listdir(scripts)) if os.path.isdir(scripts) else []:
        if not name.lower().endswith(".lua"):
            continue
        try:
            text = open(os.path.join(scripts, name), "rb").read().decode("latin-1")
        except OSError:
            continue
        for identifier in dict.fromkeys(_STRING_ID_LITERAL.findall(text)):
            if resfmt.string_hash(identifier) not in known:
                missing.setdefault(identifier, []).append(name)
    if not missing:
        return []
    shown = sorted(missing)[:6]
    return [Diagnostic(
        "PRJ009", Severity.WARNING,
        f"{len(missing)} StringId(s) used by scripts are defined nowhere",
        path=scripts,
        detail="; ".join(f"{i} ({', '.join(missing[i])})" for i in shown)
               + (" ..." if len(missing) > len(shown) else "")
               + ". A missed lookup draws nothing at all.",
        fix="Add them to strings/user_strings.res, or correct the spelling.",
    )]


def check_missions(mod: Mod) -> List[Diagnostic]:
    """PRJ010: what the mod's scripts register, and what that displaces.

    ``NScript.Register`` keys the mission table by ``Name``, so the same name
    twice means one registration wins and the other is dead code -- and which
    one wins depends on the order the loader happens to read the folder in.
    That is an error.

    Registering a *stock* name is not a mistake; it is the only way to replace
    a stock mission. It is reported as information because it is easy to do by
    accident -- the name is the only thing that decides it, and nothing in game
    says the original stopped existing.
    """
    scripts = os.path.join(mod.root, "scripts")
    found = missionsmod.registrations(scripts)
    if not found:
        return []

    out = []
    doubled = {name: files for name, files in found.items() if len(files) > 1}
    if doubled:
        out.append(Diagnostic(
            "PRJ010", Severity.ERROR,
            f"{len(doubled)} mission name(s) registered by more than one script",
            path=scripts,
            detail="; ".join(f"{name} in {', '.join(files)}"
                             for name, files in sorted(doubled.items())[:4])
                   + ". Only one registration survives, and which one is not "
                     "defined.",
            fix="Give each mission its own Name, or delete the duplicate.",
        ))

    stock_names = {m.name: m for m in missionsmod.stock()}
    replacing = {name: files[0] for name, files in sorted(found.items())
                 if name in stock_names}
    if replacing:
        shown = list(replacing)[:5]
        out.append(Diagnostic(
            "PRJ010", Severity.INFO,
            f"{len(replacing)} stock mission(s) replaced by this mod",
            path=scripts,
            detail="; ".join(f"{name} ({stock_names[name].type}) by "
                             f"{replacing[name]}" for name in shown)
                   + (" ..." if len(replacing) > len(shown) else "")
                   + ". The stock version no longer runs.",
            fix="Rename the mission if replacing the original was not intended.",
        ))
    return out


def check_mission_init(mod: Mod) -> List[Diagnostic]:
    """PRJ011: a mission whose ``Init`` never returns ``Ready``.

    ``Init`` is a readiness question, not a constructor: the engine creates
    the mission only when the call returns a table whose ``Ready`` is true.
    Returning nothing is not an error and writes no log line -- the mission
    simply never runs, which from the outside is indistinguishable from a mod
    that failed to load at all.  Established from the documented lifecycle
    [doc] and from stock bytecode [data]: ``ALWAYS_000``'s ``Init`` ends in
    ``{ Ready = true }`` on one branch and ``{ Ready = false }`` on the other,
    and returns nothing anywhere else.

    This project met the failure itself -- its own generated template left the
    return out until stock bytecode was read -- which is the best evidence
    there is that a hand-written script gets it wrong too.

    Only the certain case is reported: an ``Init`` body with no ``return`` in
    it at all.  A body ending in ``return SomeHelper( V )`` is legitimate and
    unreadable to a text scan, and is passed over rather than guessed at.
    """
    scripts = os.path.join(mod.root, "scripts")
    bad = [(f, name) for f, name, verdict
           in missionsmod.init_states_by_file(scripts)
           if verdict == missionsmod.INIT_NO_RETURN]
    if not bad:
        return []
    shown = bad[:6]
    return [Diagnostic(
        "PRJ011", Severity.ERROR,
        f"{len(bad)} mission(s) whose Init never returns Ready, so they are "
        "never created",
        path=scripts,
        detail="; ".join(f"{name} in {f}" for f, name in shown)
               + (" ..." if len(bad) > len(shown) else "")
               + ". The engine asks Init whether the mission is ready and "
                 "takes no answer as no.",
        fix="End Init with return { Ready = true }, or { Ready = false } to "
            "decline the mission deliberately.",
    )]


#: What each ``check_script`` finding means for a mod, and how loudly to say
#: it.  ``absent`` is measured against the executable's own registration
#: table, so it is an error; the rest are silent failures rather than
#: crashes, and each has a false-positive story that argues against error.
_SCRIPT_KINDS = (
    ("absent", Severity.ERROR,
     "call(s) to functions this build does not register",
     "The reference documents them and the executable does not register "
     "them; the call fails at runtime with nothing to explain it.",
     "Use a function the build provides, or delete the call."),
    ("stub", Severity.WARNING,
     "call(s) to functions that are registered but do nothing",
     "The call succeeds and is silently lost.",
     "Do not rely on the effect; NDebug.Message in particular never reads "
     "its argument."),
    ("literal", Severity.WARNING,
     "literal string(s) passed where a StringId belongs",
     "Nothing is displayed and nothing is reported -- the single most "
     "confusing failure in mod scripting.",
     "Add the text to strings/user_strings.res and pass its identifier."),
    ("unknown", Severity.WARNING,
     "call(s) to functions that are nowhere to be found",
     "Not in the reference, not in the build, and not defined by the Lua in "
     "play. Usually a typo.",
     "Check the spelling against the scripting reference."),
)


def check_script_api(mod: Mod, stock: Optional[vfsmod.Vfs]) -> List[Diagnostic]:
    """PRJ012: what a mod's scripts call, across the whole mod.

    The same scan the script editor runs, applied to every mission script the
    mod ships rather than to the one file on screen.  A mod with twenty
    scripts otherwise has no way to see all of it at once, and the ``literal``
    finding in particular is worth a sweep: text passed where a StringId
    belongs displays nothing at all, and cost this project a whole experiment
    cycle before the check existed.

    Needs the game folder.  Judging a call as *unknown* means "nothing in play
    defines it", and without the game's own Lua libraries that is every
    library call in the mod -- so with no baseline the rule is skipped rather
    than run blind.
    """
    if stock is None:
        return []
    database = scriptdoc.bundled()
    symbols = scriptdoc.index(database) if database else {}
    if not symbols:
        return []

    scripts = os.path.join(mod.root, "scripts")
    sources: List[Tuple[str, str]] = []
    for name in sorted(os.listdir(scripts)) if os.path.isdir(scripts) else []:
        if not name.lower().endswith(".lua"):
            continue
        try:
            with open(os.path.join(scripts, name), "rb") as handle:
                sources.append((name, handle.read().decode("cp1252", "replace")))
        except OSError:
            continue
    if not sources:
        return []

    defined = luascan.defined_in(_stock_lua(stock))
    defined |= luascan.defined_in(text for _name, text in sources)

    try:
        engine = scriptdoc.engine()
    except DsoError:
        engine = None
    string_ids = scriptdoc.string_id_parameters(database)

    found: Dict[str, List[str]] = {}
    for name, text in sources:
        for finding in luascan.check(text, symbols=symbols, engine=engine,
                                     string_ids=string_ids, defined=defined):
            found.setdefault(finding["kind"], []).append(
                f"{finding['symbol']} at {name}:{finding['line']}")

    out = []
    for kind, severity, summary, detail, fix in _SCRIPT_KINDS:
        hits = found.get(kind)
        if not hits:
            continue
        out.append(Diagnostic(
            "PRJ012", severity, f"{len(hits)} {summary}",
            path=scripts,
            detail="; ".join(hits[:6]) + (" ..." if len(hits) > 6 else "")
                   + ". " + detail,
            fix=fix,
        ))
    return out


def _stock_lua(stock: vfsmod.Vfs) -> Iterable[str]:
    """The game's own Lua sources, which define most of what a mod calls."""
    for path in stock.iter_paths():
        if not path.lower().endswith(".lua"):
            continue
        try:
            yield stock.read(path).decode("cp1252", "replace")
        except (DsoError, KeyError, OSError):
            continue

def check_duplicates(mod: Mod) -> List[Diagnostic]:
    """PRJ006: the same path shipped in the zip and loose."""
    dupes = mod.duplicated_files()
    if not dupes:
        return []
    return [
        Diagnostic(
            "PRJ006",
            Severity.WARNING,
            f"{len(dupes)} file(s) exist both in user_data.zip and loose; "
            "the loose copies are dead weight and editing them has no effect",
            path=mod.root,
            detail="; ".join(dupes[:6]) + (" ..." if len(dupes) > 6 else ""),
            fix="Delete the loose duplicates.",
        )
    ]


def check_redundant(mod: Mod, stock: vfsmod.Vfs) -> List[Diagnostic]:
    """PRJ002: files byte-identical to stock.

    Harmless today, but they silently revert those assets if a patch ever
    changes them upstream -- the one way a dead override can actually bite.

    **Except when the file is required to exist.**  ``inifiles/items.ini`` is
    byte-identical to stock in every well-formed mod, and it has to be: without
    it the game treats the folder as malformed and never lists the mod, saying
    nothing (see ``PRJ004`` and ``specs/README.md`` 5).  Telling the author to
    delete it is not a harmless suggestion, it is instructions to break the mod
    -- and ``Mod.create`` writes that very file deliberately.  So the finding is
    reported with the opposite advice rather than suppressed: the file *is*
    identical, and that is exactly right.
    """
    out = []
    for f in mod.classify(stock).values():
        if f.state != FileState.IDENTICAL:
            continue
        if vfsmod.normalise(f.vpath).lower() == VALIDITY_TOKEN.lower():
            out.append(
                Diagnostic(
                    "PRJ002",
                    Severity.INFO,
                    "identical to stock, and required: the game will not list "
                    "a mod without this file",
                    path=f.vpath,
                    fix="Keep it. It is what makes the mod appear in the game's "
                        "mod list at all.",
                )
            )
            continue
        out.append(
            Diagnostic(
                "PRJ002",
                Severity.INFO,
                "identical to the stock file; the override has no effect",
                path=f.vpath,
                fix="Remove it from the mod.",
            )
        )
    return out


# --------------------------------------------------------------------------
# scene rules
# --------------------------------------------------------------------------


def check_scene(
    data: bytes,
    vpath: str,
    resolver: Callable[[str, str], bool],
    submesh_total: Optional[Callable[[str, str], Optional[int]]] = None,
) -> List[Diagnostic]:
    """SCN001/SCN002/SCN003 for one scene.

    ``resolver(reference, scene_path) -> bool`` answers "does this resolve";
    ``submesh_total(reference, scene_path) -> int | None`` returns the referenced
    model's total submesh count.  Both are injected so this function stays
    independent of how assets are stored.
    """
    out: List[Diagnostic] = []
    try:
        sc = scenefmt.parse(data, path=vpath)
    except DsoError as exc:
        return [
            Diagnostic(
                "SCN003",
                Severity.ERROR,
                f"scene XML does not parse: {exc.message}",
                path=vpath,
                detail="Malformed scene XML crashes the engine on load; it is not skipped.",
            )
        ]

    for obj in sc.walk():
        ref = obj.model
        if ref and not resolver(ref, vpath):
            out.append(
                Diagnostic(
                    "SCN002",
                    Severity.ERROR,
                    f"model reference does not resolve: {ref}",
                    path=vpath,
                    location=obj.name,
                )
            )
        for eff in obj.effects:
            for slot, tex in enumerate(eff.textures):
                if not resolver(tex, vpath):
                    out.append(
                        Diagnostic(
                            "SCN002",
                            Severity.ERROR,
                            f"texture does not resolve: {tex}",
                            path=vpath,
                            location=f"{obj.name} slot {slot}",
                        )
                    )

    if submesh_total is not None:
        for mesh in sc.meshes():
            if not mesh.model:
                continue
            total = submesh_total(mesh.model, vpath)
            if total is None:
                continue
            got = len(mesh.effects)
            if got != total:
                out.append(
                    Diagnostic(
                        "SCN001",
                        Severity.ERROR,
                        f"{got} EffectContainer(s) for a model with {total} submesh(es)",
                        path=vpath,
                        location=mesh.name,
                        detail=(
                            "One EffectContainer per submesh across all LODs "
                            "(9,557/9,559 on stock data). A DCC tool that merges or "
                            "splits a submesh on export breaks this silently: the file "
                            "still parses, still validates, and still round-trips."
                        ),
                    )
                )
    return out


def check_low_twin(mod: Mod) -> List[Diagnostic]:
    """SCN004: a scene edited without its ``_low`` counterpart.

    A hint, not a warning.  The twins are genuinely separate documents with
    their own bindings, so an edit does not propagate -- that much is measured.
    When the engine actually loads a twin is *not* established, so this cannot
    yet be stated as a defect.
    """
    out = []
    scenes = {
        k: f
        for k, f in mod.files().items()
        if k.endswith(".xml") and k.startswith("3dview/") and not k.startswith("loose:")
    }
    for key, f in sorted(scenes.items()):
        if key.endswith("_low.xml"):
            base = key.replace("_low.xml", ".xml")
            if base not in scenes:
                out.append(
                    Diagnostic(
                        "SCN004",
                        Severity.HINT,
                        "_low variant shipped without its base scene",
                        path=f.vpath,
                        detail="Twins carry independent bindings; see specs/scene.md 6c.",
                    )
                )
    return out


# --------------------------------------------------------------------------
# model rules
# --------------------------------------------------------------------------

#: How far the stored bounding box may sit from the recomputed one before
#: MDL004 fires, relative to the model's own size.
#:
#: Measured, not chosen: over all 3,110 stock models the stored box and the
#: recomputed one differ at all in 1,074 cases and the **worst** relative
#: disagreement is 1.19e-07 -- one float32 ULP, an artefact of the original
#: exporter's reduction order (see ``threedo._bbox_f32``).  1e-3 leaves four
#: orders of magnitude of headroom above that noise while still catching a box
#: that no longer describes the geometry, which is off by percent, not by ULPs.
MDL_BBOX_TOLERANCE = 1e-3


def check_model(
    data: bytes,
    vpath: str,
    shadow: Optional[bytes] = None,
    shadow_path: Optional[str] = None,
) -> List[Diagnostic]:
    """MDL001-MDL007: a ``.3do``'s structure, and its ``.shd`` twin's LOD count.

    Every rule here was counted over the real corpus before it was given a
    severity, because a check that flags 3,000 of Ascaron's own models is a
    check that gets switched off.  **All seven fire zero times on the 3,110
    stock models and the 150 models in the user's Customization/ folder**, so
    anything they report is a genuine departure from what the game ships.

    They exist for one failure: a model round-tripped through a DCC tool.  The
    exporter renumbers, merges or splits things, the file still parses, still
    round-trips, and the engine draws garbage or nothing at all.  ``SCN001``
    catches the half of that visible from the scene; this is the other half.

    ``shadow`` is the companion ``.shd``'s bytes, if the caller has them.
    Without it MDL005 simply does not run -- a model with no shadow volume is
    ordinary (1,738 of 3,110 stock models have one), not a finding.
    """
    from .formats import shd as shdfmt
    from .formats import threedo

    try:
        sc = threedo.scan(data)
    except DsoError as exc:
        return [
            Diagnostic(
                "MDL006",
                Severity.ERROR,
                f"model will not load: {exc.message}",
                path=vpath,
                detail="Nothing else could be checked in this file.",
            )
        ]

    out: List[Diagnostic] = []

    if sc.stopped:
        out.append(
            Diagnostic(
                "MDL006",
                Severity.ERROR,
                f"LOD walk stopped after {len(sc.lods)} of {sc.lod_count} chunk(s): {sc.stopped}",
                path=vpath,
                detail="Everything past that point is unreadable, so the checks "
                       "below cover only the LODs that were walked.",
            )
        )
    elif sc.trailing:
        out.append(
            Diagnostic(
                "MDL006",
                Severity.ERROR,
                f"{sc.trailing} unexpected byte(s) after the last LOD chunk",
                path=vpath,
                detail="Stock models end exactly on the final LOD (3,110 of 3,110).",
            )
        )
    else:
        total = sum(l.submesh_count for l in sc.lods)
        if total != sc.submesh_total:
            out.append(
                Diagnostic(
                    "MDL007",
                    Severity.ERROR,
                    f"root header declares {sc.submesh_total} submesh(es) but the "
                    f"{len(sc.lods)} LOD chunk(s) contain {total}",
                    path=vpath,
                    location=f"0x{0x30:02x}",
                    detail=(
                        "This is the field SCN001 compares a scene's EffectContainer "
                        "count against, so a wrong value here does not fail quietly: "
                        "it makes that check validate against a lie."
                    ),
                    fix="Rebuild the model so the header matches its LODs.",
                )
            )

    for lod in sc.lods:
        where = f"LOD {lod.index}"

        if lod.vertex_count > 0xFFFF:
            out.append(
                Diagnostic(
                    "MDL001",
                    Severity.ERROR,
                    f"{where} has {lod.vertex_count} vertices; .3do indices are "
                    "uint16, so everything above 65,535 is unreachable",
                    path=vpath,
                    location=where,
                    detail=(
                        "The budget is per LOD, not per file -- each LOD owns its own "
                        "vertex buffer. Ascaron's own exporter splits across LODs to "
                        "stay under it (largest stock LOD: 57,986 vertices)."
                    ),
                    fix="Split the mesh, or reduce it below 65,535 vertices per LOD.",
                )
            )

        if lod.declared_stride != lod.computed_stride:
            out.append(
                Diagnostic(
                    "MDL003",
                    Severity.ERROR,
                    f"{where} declares a {lod.declared_stride}-byte vertex but its "
                    f"{'FVF code' if lod.fvf is not None else 'vertex declaration'} "
                    f"implies {lod.computed_stride}",
                    path=vpath,
                    location=where,
                    detail="Every vertex after the first is then read from the wrong "
                           "offset, so the whole buffer decodes as noise.",
                )
            )

        if lod.max_index is not None and lod.max_index >= lod.vertex_count:
            out.append(
                Diagnostic(
                    "MDL003",
                    Severity.ERROR,
                    f"{where} index buffer references vertex {lod.max_index} of "
                    f"{lod.vertex_count}",
                    path=vpath,
                    location=where,
                )
            )

        if lod.index_count % 3:
            out.append(
                Diagnostic(
                    "MDL003",
                    Severity.WARNING,
                    f"{where} has {lod.index_count} indices, which is not a whole "
                    "number of triangles",
                    path=vpath,
                    location=where,
                    detail="The buffer is a flat triangle list; the trailing "
                           f"{lod.index_count % 3} index(es) are never drawn.",
                )
            )

        out.extend(_check_submesh_partition(lod, vpath, where))

    if sc.computed_bbox is not None:
        stored = sc.stored_bbox[0] + sc.stored_bbox[1]
        computed = sc.computed_bbox[0] + sc.computed_bbox[1]
        scale = max(abs(x) for x in computed) or 1.0
        rel = max(abs(a - b) for a, b in zip(stored, computed)) / scale
        if rel > MDL_BBOX_TOLERANCE:
            out.append(
                Diagnostic(
                    "MDL004",
                    Severity.WARNING,
                    "stored bounding box does not describe the geometry "
                    f"(off by {rel:.1%} of the model's size)",
                    path=vpath,
                    location="0x10",
                    detail=(
                        f"stored centre {_fmt3(sc.stored_bbox[0])} half-extent "
                        f"{_fmt3(sc.stored_bbox[1])}; geometry gives "
                        f"{_fmt3(sc.computed_bbox[0])} and {_fmt3(sc.computed_bbox[1])}. "
                        "The box covers LOD0 only. It is what the engine culls "
                        "against, so a stale one makes the object vanish at angles "
                        "where it should be visible -- or hang about after it leaves."
                    ),
                    fix="Recompute the bounding box from the geometry.",
                )
            )

    if shadow is not None:
        spath = shadow_path or (vpath[:-4] + ".shd" if vpath.lower().endswith(".3do") else vpath)
        try:
            n = shdfmt.lod_count(shadow)
        except DsoError as exc:
            out.append(
                Diagnostic(
                    "MDL006",
                    Severity.ERROR,
                    f"shadow volume will not load: {exc.message}",
                    path=spath,
                )
            )
        else:
            if not sc.stopped and n != len(sc.lods):
                out.append(
                    Diagnostic(
                        "MDL005",
                        Severity.WARNING,
                        f"shadow volume has {n} LOD(s) but the model has {len(sc.lods)}",
                        path=spath,
                        detail=(
                            "The two counts agree in all 1,738 stock pairs. A .shd is "
                            "a separate mesh with its own topology, so editing a .3do "
                            "does not update it -- which is exactly how they drift "
                            "apart. What the engine does with the surplus or missing "
                            "level is not established, so this is a warning."
                        ),
                        fix="Re-export the shadow volume alongside the model.",
                    )
                )

    return out


def _fmt3(v) -> str:
    return "(" + ", ".join(f"{x:.4g}" for x in v) + ")"


def _check_submesh_partition(lod, vpath: str, where: str) -> List[Diagnostic]:
    """MDL002: the ATTR records must partition the LOD's shared buffers.

    Stock data does this exactly -- no gap, no overlap, full coverage, in all
    3,110 models -- so every departure below is a real one.  Reported at most
    once per buffer per LOD: once the ranges are misaligned, *every* subsequent
    submesh disagrees, and 40 rows saying so is 39 rows of noise.
    """
    out: List[Diagnostic] = []
    faces = lod.index_count // 3

    for sm in lod.submeshes:
        if sm.face_start + sm.face_count > faces:
            out.append(
                Diagnostic(
                    "MDL002",
                    Severity.ERROR,
                    f"{where} submesh {sm.index_in_lod} draws triangles "
                    f"{sm.face_start}..{sm.face_start + sm.face_count - 1} but the "
                    f"LOD has {faces}",
                    path=vpath,
                    location=f"{where} submesh {sm.index_in_lod}",
                    detail="The engine reads past the end of the index buffer.",
                )
            )
        if sm.vert_start + sm.vert_count > lod.vertex_count:
            out.append(
                Diagnostic(
                    "MDL002",
                    Severity.ERROR,
                    f"{where} submesh {sm.index_in_lod} claims vertices "
                    f"{sm.vert_start}..{sm.vert_start + sm.vert_count - 1} but the "
                    f"LOD has {lod.vertex_count}",
                    path=vpath,
                    location=f"{where} submesh {sm.index_in_lod}",
                )
            )

    for label, total, start, count in (
        ("triangle", faces, lambda s: s.face_start, lambda s: s.face_count),
        ("vertex", lod.vertex_count, lambda s: s.vert_start, lambda s: s.vert_count),
    ):
        cursor = 0
        reported = False
        for sm in sorted(lod.submeshes, key=start):
            if start(sm) != cursor and not reported:
                reported = True
                gap = start(sm) > cursor
                out.append(
                    Diagnostic(
                        "MDL002",
                        Severity.WARNING if gap else Severity.ERROR,
                        f"{where} submesh {sm.index_in_lod} starts at {label} "
                        f"{start(sm)}, but the submesh before it ends at {cursor} "
                        + ("(a gap)" if gap else "(an overlap)"),
                        path=vpath,
                        location=f"{where} submesh {sm.index_in_lod}",
                        detail=(
                            f"{label.capitalize()}s in no submesh are never drawn."
                            if gap else
                            f"{label.capitalize()}s in two submeshes are drawn twice, "
                            "each time with the other's material."
                        ),
                    )
                )
            cursor = max(cursor, start(sm) + count(sm))
        # `cursor > total` is a range running off the end, which the per-submesh
        # loop above has already reported as an error; saying "covers 4 of 1"
        # after that is noise about the same defect.
        if lod.submeshes and cursor < total and not reported:
            out.append(
                Diagnostic(
                    "MDL002",
                    Severity.WARNING,
                    f"{where} submeshes cover {cursor} of {total} {label}(s)",
                    path=vpath,
                    location=where,
                    detail=f"The remaining {total - cursor} {label}(s) belong to no "
                           "submesh, so nothing draws them.",
                )
            )
    return out


# --------------------------------------------------------------------------
# atlas rules
# --------------------------------------------------------------------------


def check_atlas(vfs: vfsmod.Vfs, tex_path: str) -> List[Diagnostic]:
    """TEX001-TEX004: an atlas page against its index and its drawables.

    Three files hold one truth -- the ``.aim`` pixels, the ``.tex`` rectangles
    and each ``.anim``'s declared size -- and nothing in the game keeps them in
    step.  Every rule here is a way for those three to disagree, and every one
    of them is invisible until the interface draws the wrong region.

    Requires Pillow; without it the page cannot be opened and the check is
    skipped rather than guessed at.  ``skipped_rules`` reports that, so the
    absence shows up as "not checked" instead of as a clean bill of health.
    """
    try:
        from .edit.atlas import AtlasPage, have_pillow
    except Exception:  # pragma: no cover
        return []
    if not have_pillow():
        return []

    try:
        page = AtlasPage.open(vfs, tex_path)
    except DsoError as exc:
        return [
            Diagnostic(
                getattr(exc, "code", None) or "TEX005",
                Severity.ERROR,
                str(exc.message if hasattr(exc, "message") else exc),
                path=tex_path,
            )
        ]

    out: List[Diagnostic] = []
    w, h = page.size

    for sp in page.out_of_bounds():
        out.append(
            Diagnostic(
                "TEX002",
                Severity.ERROR,
                f"{sp.stem} at ({sp.x},{sp.y}) {sp.w}x{sp.h} falls outside the "
                f"{w}x{h} page",
                path=tex_path,
                location=sp.stem,
                detail=(
                    "Usually means the page was resized without rewriting the "
                    "index. AtlasPage.rescale() does both."
                ),
                fix="Rescale the index to match the page.",
            )
        )

    for a, b in page.overlaps():
        out.append(
            Diagnostic(
                "TEX003",
                Severity.ERROR,
                f"{a.stem} and {b.stem} overlap on the page",
                path=tex_path,
                location=f"{a.stem}/{b.stem}",
            )
        )

    for vpath, declared, actual in page.anim_mismatches():
        out.append(
            Diagnostic(
                "TEX004",
                Severity.ERROR,
                f"drawable declares {declared[0]}x{declared[1]} but the atlas "
                f"rectangle is {actual[0]}x{actual[1]}",
                path=vpath,
                fix="Set the drawable size to match the rectangle.",
            )
        )

    # TEX007 used to fire when a drawable's two stored sizes differed.  It was
    # wrong: they are the *drawn* size and the *source image* size, and 57
    # shipped drawables differ on purpose because they are stretched nine-slice
    # frames.  The rule reported Ascaron's own files as damaged.  See
    # `formats/anim.py`.
    return out


# --------------------------------------------------------------------------
# audio rules
# --------------------------------------------------------------------------


def check_sounds(mod: Mod, stock: Optional[vfsmod.Vfs] = None) -> List[Diagnostic]:
    """SND001/SND002: the sound database against the files on disk.

    Checked in both directions on purpose.  A path typo puts the same sound in
    both lists -- declared-but-missing *and* present-but-unreferenced -- and
    that pairing is what distinguishes a typo from a deliberate omission.
    """
    path = os.path.join(mod.root, "user_sounds.xml")
    if not os.path.exists(path):
        return []
    with open(path, "rb") as fh:
        raw = fh.read()
    try:
        db = sounddbfmt.parse(raw, path=path)
    except DsoError as exc:
        return [Diagnostic("SND003", Severity.ERROR, str(exc), path=path)]

    files = mod.files()
    # Lookup folds case (Windows), but a diagnostic must quote the path as the
    # user will see it on disk -- "TRADER" and "trader" look like different bugs.
    have = {}
    for k, f in files.items():
        have.setdefault(k[len("loose:") :] if k.startswith("loose:") else k, f.vpath)

    def exists(p, mod_relative):
        if p.lower() in have:
            return True
        if not mod_relative and stock is not None:
            return stock.exists(p)
        return False

    out = []
    for e in db.missing(exists):
        out.append(
            Diagnostic(
                "SND001",
                Severity.ERROR,
                f"sound {e.name!r} points at a file that does not exist",
                path=path,
                location=e.resource,
                detail="Declared in user_sounds.xml but not shipped and not in the game.",
            )
        )

    shipped = sorted(p for p in have if p.rsplit(".", 1)[-1] in ("wav", "mp3") and p.startswith("sound/"))
    for p in db.unreferenced(shipped):
        out.append(
            Diagnostic(
                "SND002",
                Severity.INFO,
                "audio file is shipped but nothing in user_sounds.xml names it",
                path=have.get(p, p),
                detail="If a declared sound is also missing, the two are probably the same typo.",
            )
        )
    out.extend(_check_sound_metadata(mod, db))
    return out


#: How far a declared length may sit from the measured one before it counts
#: as wrong.  MP3 length is derived from frame counts rather than read, so
#: demanding an exact match would report every MP3 in every mod.
SOUND_LENGTH_TOLERANCE = 0.05


def sound_metadata_drift(entry, info) -> List[str]:
    """Where a declaration disagrees with the file, in words.

    Shared by the ``SND004`` rule and by the fix that corrects it: two
    statements of the tolerance would let the app offer a repair that the next
    validation still complains about.
    """
    wrong = []
    if entry.frequency and info.frequency and entry.frequency != info.frequency:
        wrong.append(f"rate {entry.frequency} declared, {info.frequency} in the file")
    if entry.channels and info.channels and entry.channels != info.channels:
        wrong.append(f"{entry.channels} channel(s) declared, {info.channels} in the file")
    if entry.duration and info.samples:
        drift = abs(entry.duration - info.samples) / max(1, info.samples)
        if drift > SOUND_LENGTH_TOLERANCE:
            wrong.append(f"length {entry.duration} samples declared, "
                         f"{info.samples} in the file")
    return wrong


def _check_sound_metadata(mod: Mod, db) -> List[Diagnostic]:
    """SND004: the declared rate, channels or length is not what the file says.

    The engine reads these three from the database rather than from the file,
    so they are not documentation -- a wrong ``Duration`` cuts playback off
    where the database says the sound ends, which in game is indistinguishable
    from a corrupt file. They are easy to get wrong by hand and easy to leave
    stale after swapping a file, which is exactly why this is checked.

    Only the mod's own files are looked at. Reading them is header-only, so the
    cost is a stat and a short read each.
    """
    out = []
    for entry in db.entries():
        if not entry.is_mod_relative:
            continue                     # the game's own, and not ours to judge
        full = os.path.join(mod.root, entry.path().replace("/", os.sep))
        if not os.path.exists(full):
            continue                     # SND001 already says so
        try:
            info = audiofmt.probe(full)
        except DsoError:
            continue                     # not decodable here; the engine may differ
        wrong = sound_metadata_drift(entry, info)
        if wrong:
            out.append(Diagnostic(
                "SND004",
                Severity.WARNING,
                f"{entry.name!r} is declared differently from the file it names",
                path=full,
                location=sounddbfmt.qualified(entry),
                detail="; ".join(wrong)
                       + ". The engine believes the database, not the file.",
                fix="Re-add the sound, or correct the attributes in user_sounds.xml.",
            ))
    return out


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------


def validate_mod(
    mod: Mod,
    stock: Optional[vfsmod.Vfs] = None,
    *,
    scenes: bool = True,
    progress: Optional[Callable[[int, int, str], None]] = None,
    limit_per_rule: int = Report.DEFAULT_LIMIT,
) -> Report:
    """Run every applicable rule over a mod.

    ``stock`` enables the rules that need a baseline.  Without it the structural
    rules still run, so a mod can be checked before the game is located.

    ``progress(done, total, label)`` reports across the **whole** run, not just
    the file loops.  Two properties are load-bearing:

    * ``total`` is never zero.  A mod with no scenes and no atlases -- a
      texture-only mod, which is the common case -- used to end its only
      progress call at ``0/0``, and the bar sat at 0% for the entire run.  A
      progress indicator that reads 0% while working is worse than none: it
      says "stuck", and the user has no way to tell that apart from a hang.
    * ``done`` is reported *after* each unit completes, so the last call is
      ``total/total``.  Reporting before the work meant the bar could never
      reach 100%, and on a one-item mod it never left 0%.

    The structural rules count as units of their own.  ``check_redundant``
    hashes every mod file against stock and ``check_sounds`` walks the sound
    database; on a mod with no scenes those *are* the run, and leaving them
    unreported is what made the whole thing look dead.
    """
    report = Report(limit_per_rule=limit_per_rule)

    # Work out the full unit list before starting, so `total` is honest from
    # the first call rather than growing as phases are discovered.
    mod_files = sorted(mod.files().items())

    def _live(suffix):
        return [(key, f) for key, f in mod_files
                if not key.startswith("loose:") and key.endswith(suffix)]

    def _by_suffix(suffix):
        if stock is None:
            return []
        return [f for _key, f in _live(suffix)]

    atlases = _by_suffix(".tex")
    candidates = _by_suffix(".xml") if scenes else []
    # The model rules are structural, so unlike the atlas and scene rules they
    # need no baseline to compare against and run with no game folder open.
    #
    # A mod's loose 3DView/ is never read, so a loose .3do is PRJ005's finding
    # rather than MDL's: checking the internals of a file the engine ignores
    # would bury the one thing wrong with it, which is where it lives.
    models = _live(".3do")
    shadows = dict(_live(".shd"))

    steps: List[Tuple[str, Callable[[], Iterable[Diagnostic]]]] = [
        ("manifest", lambda: check_manifest(mod)),
        ("dead files", lambda: check_dead_files(mod)),
        ("duplicates", lambda: check_duplicates(mod)),
        ("scripts in the zip", lambda: check_scripts_in_zip(mod)),
        ("string table", lambda: check_string_table(mod)),
        ("string ids", lambda: check_string_ids(mod, stock)),
        ("missions", lambda: check_missions(mod)),
        ("mission readiness", lambda: check_mission_init(mod)),
        ("script api", lambda: check_script_api(mod, stock)),
        ("low twins", lambda: check_low_twin(mod)),
        ("sounds", lambda: check_sounds(mod, stock)),
    ]
    if stock is not None:
        steps.append(("comparing against stock", lambda: check_redundant(mod, stock)))

    total = len(steps) + len(atlases) + len(candidates) + len(models)
    done = 0

    def tick(label: str) -> None:
        if progress:
            progress(done, total, label)

    tick("starting")
    for label, rule in steps:
        report.extend(rule())
        done += 1
        tick(label)

    if stock is None:
        report.skip("PRJ002/PRJ009/PRJ012/SCN001/SCN002",
                    "no game folder open; nothing to compare against")

    for key, f in models:
        try:
            data = f.read()
        except DsoError as exc:
            report.add(Diagnostic("MDL006", Severity.ERROR, str(exc.message), path=f.vpath))
            done += 1
            tick(f.vpath)
            continue

        # The shadow volume the *engine* would pair with this model: the mod's
        # own if it ships one, otherwise the stock file it is overriding. That
        # second case is the one worth checking -- a re-exported model dropping
        # a LOD while the stock .shd still has three is exactly the drift
        # MDL005 is for, and it is invisible if the pair has to be complete.
        shadow = None
        skey = key[:-4] + ".shd"
        spath = f.vpath[:-4] + ".shd"
        try:
            if skey in shadows:
                shadow = shadows[skey].read()
                spath = shadows[skey].vpath
            elif stock is not None and stock.exists(spath):
                shadow = stock.read(spath)
        except DsoError:
            shadow = None   # MDL005 does not run; MDL006 is not this file's fault

        report.extend(check_model(data, f.vpath, shadow, spath))
        done += 1
        tick(f.vpath)

    if atlases:
        from .edit.atlas import have_pillow

        if not have_pillow():
            report.skip(
                "TEX001-TEX004",
                f"{len(atlases)} atlas page(s) not checked: Pillow is not "
                "installed (pip install 'dsotools[image]')",
            )
        merged_for_atlas = vfsmod.Vfs(stock.layers)
        for layer in _mod_layers(mod):
            merged_for_atlas.add(layer)
        for f in atlases:
            report.extend(check_atlas(merged_for_atlas, f.vpath))
            done += 1
            tick(f.vpath)

    if candidates:
        merged = vfsmod.Vfs(stock.layers)
        for layer in _mod_layers(mod):
            merged.add(layer)

        def resolver(ref, scene_path):
            return merged.resolve_reference(ref, scene_path=scene_path) is not None

        # One model is bound by many meshes -- 9,806 references over 2,762
        # models on stock data -- and the number this reads sits 0x30 bytes in.
        # Uncached, a mod with a lot of scenes decompresses the same multi-MB
        # model once per reference to look at four bytes.
        totals: Dict[str, Optional[int]] = {}

        def submesh_total(ref, scene_path):
            entry = merged.resolve_reference(ref, scene_path=scene_path)
            if entry is None:
                return None
            if entry.vpath not in totals:
                try:
                    head = entry.read()[:0x1000]
                    totals[entry.vpath] = (
                        struct.unpack_from("<I", head, 0x30)[0]
                        if head[:4] == b"OD3 " else None
                    )
                except Exception:  # noqa: BLE001
                    totals[entry.vpath] = None
            return totals[entry.vpath]

        for f in candidates:
            data = f.read()
            if scenefmt.is_scene(data):
                report.extend(check_scene(data, f.vpath, resolver, submesh_total))
            done += 1
            tick(f.vpath)

    return report


def _mod_layers(mod: Mod):
    from .project import iter_mod_layers

    return list(iter_mod_layers(mod))


__all__ = [
    "VERSION",
    "Severity",
    "Diagnostic",
    "Report",
    "validate_mod",
    "check_manifest",
    "check_dead_files",
    "check_scripts_in_zip",
    "check_string_table",
    "check_string_ids",
    "check_missions",
    "sound_metadata_drift",
    "SOUND_LENGTH_TOLERANCE",
    "check_mission_init",
    "check_script_api",
    "check_duplicates",
    "check_redundant",
    "check_scene",
    "check_sounds",
    "check_low_twin",
    "check_atlas",
    "check_model",
    "MDL_BBOX_TOLERANCE",
]
