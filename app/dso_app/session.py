"""
Application state, with no Qt in it.

The plan's rule is that the GUI never touches bytes and the model never imports
Qt.  This module is that boundary: everything the app *knows* -- which game,
which mod, the index, the last report -- lives here as plain Python, so it can
be tested headlessly and driven from a script.

Qt widgets observe it through callbacks rather than the other way round.  That
keeps the interesting logic out of the part that cannot be unit-tested in CI,
and it is why the app shell can be reviewed by reading it rather than by
clicking through it.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import posixpath
import shutil
import struct
from typing import Callable, Dict, Iterator, List, Optional, Sequence, Tuple

from dsotools import index as idxmod
from dsotools import locate
from dsotools import baseline
from dsotools import luac
from dsotools import luascan
from dsotools import missions as missionsmod
from dsotools import rootfiles
from dsotools import scriptdoc
from dsotools import validate as validatemod
from dsotools import vfs as vfsmod
from dsotools.edit import atlas as atlasmod
from dsotools.errors import DsoError, ParseError
from dsotools.formats import audio as audiofmt
from dsotools.formats import res as resfmt
from dsotools.formats import sounddb as sounddbfmt
from dsotools.formats import threedo as threedofmt
from . import settings as settings_mod
from dsotools.project import (
    VALIDITY_TOKEN,
    DeployPlan,
    check_mod_path,
    DeployResult,
    FileState,
    Mod,
    ProjectFile,
    iter_mod_layers,
)

APP_NAME = "Darkstar One Modding Suite"


#: The `<Material>` block's four RGBA rows, by the semantic each maps to.
#:
#: This is the D3DMATERIAL9 reading, and it is still an *inference* about the
#: order of the 17 floats: D3DX binds parameters by semantic rather than by
#: position, so the shader declaring all five quantities (which it does)
#: confirms the shape and not the order.  `specs/bsd9.md` §5 has the corpus
#: evidence that supports this order.
MATERIAL_SEMANTICS = ("Diffuse", "Ambient", "Specular", "Emissive")
MATERIAL_POWER_SEMANTIC = "SpecularPower"


def _material_default(semantics: dict) -> Optional[List[float]]:
    """The 17 floats a shader's own defaults imply, or ``None``.

    ``None`` unless every one of the five is declared *and* carries a default:
    a partly-filled material would put invented numbers in four of the boxes
    and real ones in the rest, which is worse than offering nothing.
    """
    out: List[float] = []
    for name in MATERIAL_SEMANTICS:
        param = semantics.get(name)
        if param is None or not param.default or len(param.default) != 4:
            return None
        out.extend(float(v) for v in param.default)
    power = semantics.get(MATERIAL_POWER_SEMANTIC)
    if power is None or not power.default or len(power.default) != 1:
        return None
    out.append(float(power.default[0]))
    return out


def effect_parameter_edits(
    original: Dict[str, Optional[float]], current: Dict[str, Optional[float]]
) -> Dict[str, float]:
    """Which parameters an edit should actually write.

    ``None`` means the scene writes ``<Float semantic="X" />`` with no value,
    which *is* the shader default.  Two rules follow, and both were bugs:

    * A parameter left unset is **never written**.  ``set_parameter`` writes
      values and cannot remove an attribute, so "unset it" is not an edit that
      can be expressed -- and the widget's sentinel for unset is the spin box's
      minimum, which once leaked into a scene file as ``-10000``.
    * Only genuine changes are written, so applying an untouched dialog is a
      no-op rather than a rewrite of every value with what it already had.
    """
    out: Dict[str, float] = {}
    for name, value in current.items():
        if value is None:
            continue
        if original.get(name) != value:
            out[name] = value
    return out


def effect_default_values(
    original: Dict[str, Optional[float]], defaults: Optional[Dict[str, float]]
) -> Dict[str, Optional[float]]:
    """What each parameter becomes on "reset to shader defaults".

    Three cases, and the first is the one that surprised somebody:

    * **The scene leaves it unset** -- it stays unset.  Unset already means the
      shader default, so filling in the number would add an attribute the file
      does not have and change nothing the engine does.
    * **The shader declares a default** -- use it.
    * **It does not** (the shader has no such semantic, so the value is inert)
      -- leave it alone rather than invent a number for it.
    """
    known = defaults or {}
    out: Dict[str, Optional[float]] = {}
    for name, value in original.items():
        if value is None:
            out[name] = None
        elif name in known:
            out[name] = float(known[name])
        else:
            out[name] = value
    return out


def mesh_for(detail: Optional[dict], call) -> Optional[dict]:
    """The :meth:`Session.scene_detail` entry a draw call came from.

    **Matched on the scene-graph path, not the node name**, and that is the
    whole point of the function.  Mesh names are not unique: ``PlayerShip.xml``
    has 85 meshes under 28 distinct names, because each of its eleven body
    variants contains one called ``main_``.  Keyed by name, body_0's submesh
    reads body_10's effects -- and those differ, so the panel showed four
    texture slots where the drawn submesh has one, and the effect editor
    offered the wrong variant's shader and material to Apply.

    Falls back to the first mesh of that name when the path does not match, so
    a detail dict without paths degrades to the old behaviour rather than to
    nothing.
    """
    if not detail:
        return None
    meshes = detail.get("meshes") or []
    node_path = getattr(call, "node_path", "")
    if node_path:
        for mesh in meshes:
            if mesh.get("path") == node_path:
                return mesh
    for mesh in meshes:
        if mesh.get("name") == call.node:
            return mesh
    return None


def texture_refs(mesh: Optional[dict], call) -> List[tuple]:
    """``[(role, vpath, resolves)]`` for one draw call's texture slots.

    The bridge between a :class:`~dsotools.edit.meshview.DrawCall`, which
    carries the scene's **raw** texture references, and
    :meth:`Session.scene_detail`, which carries the **resolved** vpath for each
    of them.  A widget that takes ``call.textures`` straight from the draw call
    gets ``textures/x.dds`` -- a scene-relative reference, not a path anything
    can read -- so every action built on it fails with ``VFS001``.

    Lives here rather than in the widget that needs it because choosing between
    a raw reference and a resolved one is application logic, and because this is
    the side of the boundary with tests.

    Falls back to the raw reference when there is no slot to match, and marks
    that row unresolved: an unusable path presented as fine is exactly the
    failure this function exists to remove.
    """
    slots = (mesh or {}).get("slots") or []
    # meshview's own rule for a submesh with no effect of its own: effect *i*
    # if there is one, else the first.  Matching it keeps this panel describing
    # the submesh the viewport actually drew.
    slot = None
    if slots:
        slot = slots[call.index] if call.index < len(slots) else slots[0]
    vpaths = (slot or {}).get("texture_vpaths") or []
    marks = (slot or {}).get("resolved") or []

    # The shader's own name for the slot, where it has one.  "t_Normal" tells
    # the author what the binding is *for*; "texture 3" tells them only where
    # it sits.  Falls back to the index when the shader could not be read.
    slot_names = list(getattr(call, "slot_names", ()) or ())

    rows: List[tuple] = []
    for i, raw in enumerate(call.textures):
        vpath = vpaths[i] if i < len(vpaths) else None
        ok = marks[i] if i < len(marks) else False
        label = slot_names[i] if i < len(slot_names) else f"texture {i}"
        rows.append((label, vpath or raw, bool(ok)))
    return rows


class DeployGate:
    """May this mod be deployed, and what will deploying do?

    Deploy is the one action in the app that writes to a folder the user cares
    about, so the decision is modelled explicitly rather than left as a pair of
    booleans in the widget layer -- and it is modelled *here*, with no Qt, so
    the interesting part is the part under test.

    The rule is: **errors block, warnings do not, and an error the deploy will
    itself repair does not count.**  That last clause is not a nicety.  A mod
    with no ``inifiles/items.ini`` raises ``PRJ004``, which is an error; adding
    that file is the first thing deploy does.  Without the exemption the app
    would refuse to perform the fix on the grounds that the fix had not been
    performed, which is the kind of loop that makes people stop trusting a tool.
    """

    #: Errors deploying repairs, and the plan attribute that says it will.
    SELF_HEALED = {"PRJ004": "add_items_ini"}

    def __init__(self, plan: DeployPlan, report: Optional[validatemod.Report]) -> None:
        self.plan = plan
        self.report = report

    @property
    def blockers(self) -> List:
        """Error diagnostics that deploying will not fix."""
        if self.report is None:
            return []
        return [
            d
            for d in self.report.diagnostics
            if d.severity == validatemod.Severity.ERROR
            and not getattr(self.plan, self.SELF_HEALED.get(d.code, ""), False)
        ]

    @property
    def blocked(self) -> bool:
        return bool(self.blockers)

    @property
    def unvalidated(self) -> bool:
        """True when nothing was checked, so "no blockers" means nothing.

        A gate that could not run must not read as a gate that passed -- the
        same rule the diagnostics engine applies to a skipped check.
        """
        return self.report is None

    def blocker_lines(self) -> List[str]:
        return [f"{d.code}  {d.message}" + (f"  ({d.path})" if d.path else "")
                for d in self.blockers]

    def __repr__(self) -> str:  # pragma: no cover
        return f"<DeployGate blocked={self.blocked} plan={self.plan!r}>"


class Session:
    """Everything the application knows about the current work.

    Deliberately *not* an implicit singleton: tests build one per case, and a
    future "compare two mods" feature needs two.
    """

    def __init__(self) -> None:
        self.game_path: Optional[str] = None
        #: ``"install"`` when reading .cpr archives directly, ``"extracted"``
        #: when pointed at a pre-extracted tree.  The former is the normal case.
        self.game_kind: Optional[str] = None
        self.mod: Optional[Mod] = None
        self.project: Optional[ProjectFile] = None
        self.stock: Optional[vfsmod.Vfs] = None
        self.index: Optional[idxmod.AssetIndex] = None
        self.report: Optional[validatemod.Report] = None
        self._listeners: List[Callable[[str], None]] = []
        self._preview_cache: Dict[str, dict] = {}
        #: {source image: (tex, page, rect)} over all ten .tex indexes,
        #: read once per session because the Interface tab wants it for
        #: every screen it opens.
        self._sprites = None
        #: The documented Lua API, read once; False when this build has none.
        self._lua_api = None
        #: Functions the visible Lua sources define themselves; see
        #: :meth:`check_script`.  Cleared with the other caches.
        self._lua_defined_cache = None
        #: The API the executable really registers, and the parameters
        #: the reference says are StringIds.  Both read once.
        self._lua_engine = None
        self._lua_string_ids = None
        #: The stock string tables, read from the open game folder on first
        #: use and dropped when a different game folder is opened.
        self._stock_strings = None
        #: The shipped stock mission table, read once.
        self._missions = None
        #: The game's own sound database, read once per game folder.
        self._stock_sound_db = None
        #: Decoded textures, kept between scene builds.  See
        #: :meth:`scene_geometry` and :attr:`TEXTURE_CACHE_BYTES`.
        self._texture_cache: Dict[tuple, object] = {}
        #: ``{vpath: "1024x1024 DXT5, 11 mip(s)"}``.  Unbounded on purpose: it
        #: is one short string per texture, and it is cleared with the preview
        #: cache whenever the bytes behind a path can have changed.
        self._format_cache: Dict[str, Optional[str]] = {}
        #: Persistent preferences.  Lives on the session because that is the
        #: object every widget already has a reference to, and because it is
        #: Qt-free -- the same reason the rest of this module is.
        self.settings = settings_mod.Settings()

    # -- observation ---------------------------------------------------------

    def subscribe(self, fn: Callable[[str], None]) -> None:
        self._listeners.append(fn)

    def _emit(self, what: str) -> None:
        # A decoded preview is only valid for the bytes it came from.  Opening a
        # game or mod, and above all *saving* an edit, changes which bytes a
        # path resolves to -- and a stale cache would show the user the picture
        # they just replaced, which is indistinguishable from the save failing.
        if what in ("game", "mod"):
            self._preview_cache.clear()
            # Which scripts exist, and what they define, changes with the mod.
            self._lua_defined_cache = None
            # And which ids the stock game already defines changes with the
            # game folder, so a stale table would answer for the wrong install.
            self._stock_strings = None
            self._stock_sound_db = None
            # Same reasoning: a replaced texture is very often a *differently
            # encoded* texture, and the format line is the one thing the user
            # reads to decide what to save.  Stale there is worse than absent.
            self._format_cache.clear()
            # And the decoded pixels behind the viewport, or replacing a texture
            # would reopen the scene and redraw it with the old one -- which is
            # precisely the "my edit did nothing" trap this project keeps
            # meeting.
            self._texture_cache.clear()
            self._sprites = None
        for fn in list(self._listeners):
            fn(what)

    # -- game ----------------------------------------------------------------

    def autodetect_game(self) -> Optional[str]:
        """Find an installation without asking.  ``None`` if there is none."""
        return locate.find_game()

    def open_game(self, path: str) -> None:
        """Open a game installation, or a folder of extracted archives.

        The installation is the normal case and needs no preparation: ``.cpr``
        is a plain ZIP container, so the app reads the installed game directly.
        An extracted tree is still accepted for people who already have one.
        """
        if not os.path.isdir(path):
            raise DsoError(f"not a directory: {path}", path=path)

        if locate.looks_like_game(path):
            self.stock = vfsmod.from_install(path)
            self.game_kind = "install"
        elif locate.looks_like_extracted(path):
            self.stock = vfsmod.from_extracted(path)
            self.game_kind = "extracted"
        else:
            raise DsoError(
                "this folder is neither a Darkstar One installation "
                f"(no {locate.EXECUTABLE} and no .cpr archives) nor a folder of "
                "extracted archives (no ds_* directories)",
                path=path,
            )

        self.game_path = path
        self.index = None
        self._emit("game")

    @property
    def game_summary(self) -> str:
        if not self.stock:
            return "no game data"
        kind = "installation" if self.game_kind == "install" else "extracted archives"
        return f"{kind}: {len(self.stock.layers)} layers, {len(self.stock)} assets"

    # -- mod -----------------------------------------------------------------

    def discover_mods(self, customization_dir: Optional[str] = None) -> List[Mod]:
        d = customization_dir or Mod.default_customization_dir()
        return Mod.discover(d) if d else []

    def create_mod(self, name: str, description: str = "",
                   customization_dir: Optional[str] = None) -> Mod:
        """Create a new mod and open it.

        The stock ``items.ini`` is copied in silently when a game is open --
        without it the game will not list the mod at all and says nothing, so
        making the user find that out is not an option.
        """
        target = customization_dir or Mod.default_customization_dir()
        if not target:
            raise DsoError(
                "cannot find your Customization folder "
                "(Documents/Ascaron Entertainment/Darkstar One/Customization)"
            )
        os.makedirs(target, exist_ok=True)
        mod = Mod.create(target, name, description, stock=self.stock)
        self.open_mod(mod.root)
        return mod

    def open_mod(self, path: str) -> None:
        mod = Mod(path)
        _ = mod.display_name  # raises if the manifest is missing or unreadable
        self.mod = mod
        self.project = ProjectFile.load(path)
        self.report = None
        self._emit("mod")

    def update_mod_metadata(self, name: str, description: str,
                            folder: Optional[str] = None) -> Mod:
        """Rewrite the manifest, optionally renaming the folder too.

        Order matters: the manifest is written **first**, into the folder that
        still exists.  If the rename then fails -- the folder is open in
        Explorer, the game holds it -- the metadata edit has still landed and
        the mod is left in a coherent state.  Doing it the other way round can
        leave a renamed folder carrying the old name inside it.
        """
        if not self.mod:
            raise DsoError("open a mod first")
        self.mod.set_metadata(name, description)

        mod = self.mod
        if folder and folder != mod.name:
            mod = mod.rename_folder(folder)

        self.open_mod(mod.root)
        return mod

    def mod_rename_warning(self, folder: str) -> Optional[str]:
        """Why renaming this folder might bite -- or ``None`` if it will not.

        Returned rather than raised: this is a thing to tell the user before
        they commit, not a reason to refuse.
        """
        if not self.mod or not folder or folder == self.mod.name:
            return None
        if self.mod.is_selected_in():
            return (
                "This mod is the one currently selected in the game. The "
                "selection is stored by folder name, so renaming it will "
                "deselect the mod — pick it again in the game's mod list "
                "afterwards."
            )
        return None

    # -- work ----------------------------------------------------------------

    def build_index(self, progress=None, deep: bool = True) -> idxmod.AssetIndex:
        """Index stock plus the active mod, so the graph reflects what loads."""
        # open_vfs() releases the mod's user_data.zip on the way out.  That is
        # not tidiness: the index has already read everything into SQLite, and a
        # Deploy that follows an index build has to replace that very file --
        # which Windows refuses while any handle is open.
        with self.open_vfs() as merged:
            self.index = idxmod.build_index(merged, progress=progress, deep=deep)
        self._emit("index")
        return self.index

    def validate(self, progress=None) -> validatemod.Report:
        if not self.mod:
            raise DsoError("open a mod first")
        self.report = validatemod.validate_mod(self.mod, self.stock, progress=progress)
        self._emit("report")
        return self.report

    # -- fixing what the report found ----------------------------------------
    #
    # Four diagnostics have a repair that is **exactly mechanical**: there is
    # one correct outcome, it needs nothing from the author, and the machinery
    # to carry it out already exists.  Those four are offered.
    #
    # The rest are deliberately not, and the reason is the same in every case:
    # they need a decision.  ``PRJ006`` (the same path loose and zipped) cannot
    # know which copy is the newer edit, and ``SND002`` (a shipped file nothing
    # declares) cannot know whether the answer is to declare it or delete it.
    # A menu entry that quietly picks one is worse than no menu entry, because
    # it looks like the tool knew.
    #
    # A fix acts on the whole rule group rather than one row.  That matches how
    # the report is presented -- grouped by code -- and it is what the author
    # means: "there are eleven loose 3DView files" is one mistake, not eleven.

    #: ``{code: (button text, what it will do)}`` for every offered fix.
    FIXES = {
        "PRJ004": ("Write inifiles/items.ini",
                   "Copies the stock inifiles/items.ini into the mod, or "
                   "writes a minimal stub when no game folder is open. "
                   "Without that file the game does not list the mod at all."),
        "PRJ005": ("Move into user_data.zip",
                   "Moves the loose files the engine never reads into "
                   "user_data.zip, where it does read them. A path that "
                   "exists in both places is left alone and reported."),
        "PRJ007": ("Move scripts out of the zip",
                   "Writes the zipped scripts loose into the mod's scripts "
                   "folder and drops them from the archive. The engine reads "
                   "scripts as real files and ignores the archived copies."),
        "SND004": ("Correct the declared numbers",
                   "Re-reads each audio file and rewrites the rate, channel "
                   "count and length in user_sounds.xml. The engine believes "
                   "the database rather than the file."),
    }

    def fix_for(self, code: str) -> Optional[Tuple[str, str]]:
        """``(button text, what it will do)``, or ``None`` when none is offered."""
        return self.FIXES.get(code)

    def apply_fix(self, code: str) -> str:
        """Carry out the mechanical fix for ``code``; return what happened.

        The report is dropped afterwards rather than adjusted.  A repair that
        edits the mod invalidates every other finding in the report as well --
        moving files into the zip changes what ``PRJ006`` would say -- and a
        problem list that is right about the row just fixed and stale about
        the rest is worse than one that admits it needs re-running.
        """
        if not self.mod:
            raise DsoError("open a mod first")
        if code not in self.FIXES:
            raise DsoError(f"there is no automatic fix for {code}")

        summary = getattr(self, f"_fix_{code.lower()}")()

        self.report = None
        self.mod.close()
        self._emit("mod")
        return summary

    def _fix_prj004(self) -> str:
        """Write ``inifiles/items.ini`` -- and nothing else."""
        plan = self.mod.deploy_plan(stock=self.stock)
        if not plan.add_items_ini:
            return "inifiles/items.ini is already there."
        plan.relocate = []              # this fix is only about the manifest
        plan.conflicts = []
        result = self.mod.apply_deploy_plan(
            plan, stock=self.stock, project=self.project)
        if self.project is not None:
            self.project.save(self.mod.root)
        source = ("copied from the game" if result.items_ini_source == "stock"
                  else "written as a minimal stub, no game folder being open")
        return f"inifiles/items.ini {source}."

    def _fix_prj005(self) -> str:
        """Move loose files the engine never reads into ``user_data.zip``."""
        plan = self.mod.deploy_plan(stock=self.stock)
        plan.add_items_ini = False      # PRJ004's fix, offered separately
        if not plan.relocate:
            return "Nothing loose is left to move."
        result = self.mod.apply_deploy_plan(
            plan, stock=self.stock, project=self.project)
        if self.project is not None:
            self.project.save(self.mod.root)
        parts = [f"{len(result.written)} file(s) moved into user_data.zip"]
        if result.not_removed:
            parts.append(f"{len(result.not_removed)} loose copy(ies) could not "
                         "be deleted and are now duplicated")
        if result.conflicts:
            parts.append(f"{len(result.conflicts)} left alone: the zip already "
                         "has that path, and which copy is wanted is not "
                         "something this can know")
        return "; ".join(parts) + "."

    def _fix_prj007(self) -> str:
        """Move scripts out of ``user_data.zip``, where they are never read."""
        moved = self.mod.unzip_scripts()
        if not any(moved.values()):
            return "The archive holds no scripts."
        parts = [f"{len(moved['moved'])} script(s) written loose"]
        if moved["identical"]:
            parts.append(f"{len(moved['identical'])} already loose and "
                         "identical, so only the archived copy went")
        if moved["conflicts"]:
            parts.append(f"{len(moved['conflicts'])} left in the archive: a "
                         "different loose file of that name already exists, "
                         "and overwriting it could destroy the newer edit")
        return "; ".join(parts) + "."

    def _fix_snd004(self) -> str:
        """Re-read each declared file and rewrite the numbers that disagree."""
        db = self._mod_sounds()
        if db is None:
            return "This mod declares no sounds."
        corrected = []
        for entry in db.entries():
            if not entry.is_mod_relative:
                continue                # the game's own, and not ours to touch
            full = os.path.join(self.mod.root,
                                entry.path().replace("/", os.sep))
            if not os.path.exists(full):
                continue                # SND001's finding, not this one's
            try:
                info = audiofmt.probe(full)
            except DsoError:
                continue                # undecodable here; the engine may differ
            if not validatemod.sound_metadata_drift(entry, info):
                continue
            entry.set_metadata(info.as_attributes())
            corrected.append(entry.name)
        if not corrected:
            return "Every declaration already matches its file."
        self._write_sound_db(db)
        shown = ", ".join(corrected[:5])
        more = f" and {len(corrected) - 5} more" if len(corrected) > 5 else ""
        return f"{len(corrected)} declaration(s) corrected: {shown}{more}."

    # -- deploy --------------------------------------------------------------

    def deploy_preview(self, progress=None, *, revalidate: bool = True) -> DeployGate:
        """Plan the deploy and run the validation gate in front of it.

        Validation is re-run rather than reusing :attr:`report`, because the
        report may predate edits made since, and a stale gate is worse than no
        gate.  ``revalidate=False`` exists for callers that have just validated.
        """
        if not self.mod:
            raise DsoError("open a mod first")
        plan = self.mod.deploy_plan(stock=self.stock)
        if revalidate or self.report is None:
            self.validate(progress=progress)
        return DeployGate(plan, self.report)

    def deploy(self, gate: Optional[DeployGate] = None, *, force: bool = False) -> DeployResult:
        """Write the mod into the shape the engine actually reads.

        Refuses when the gate is blocked unless ``force`` is set.  ``force`` is
        offered rather than withheld: the diagnostics engine is not infallible,
        and a tool that cannot be overridden gets worked around by hand, which
        is where the silent mistakes come from.  What it must never do is let
        the override happen *quietly* -- so the caller has to pass it, and the
        GUI spells out what is being overridden first.
        """
        if not self.mod:
            raise DsoError("open a mod first")
        if gate is None:
            gate = self.deploy_preview()
        if gate.blocked and not force:
            raise DsoError(
                "this mod has "
                f"{len(gate.blockers)} error(s) that deploying will not fix; "
                "fix them or deploy anyway explicitly",
                path=self.mod.root,
            )

        result = self.mod.apply_deploy_plan(
            gate.plan, stock=self.stock, project=self.project
        )
        if self.project is not None and self.stock is not None:
            self.project.record_base_game(self.stock)
        if self.project is not None:
            self.project.save(self.mod.root)

        # The mod on disk has changed, so every view of it is now stale.
        self.report = None
        self._emit("mod")
        return result

    # -- textures ------------------------------------------------------------

    #: What the Textures tab can open, and what each extension means there.
    #: ``.anim`` is deliberately absent: a drawable is not a picture, it is a
    #: size declaration *about* one, and it is reached through its page.
    TEXTURE_KINDS = {
        ".tex": "atlas index",
        ".aim": "image",
        ".dds": "texture",
    }

    @contextlib.contextmanager
    def open_vfs(self) -> Iterator[vfsmod.Vfs]:
        """Stock plus the active mod, for the duration of the block.

        A context manager, not a cached attribute, and that is the whole point:
        the mod's ``user_data.zip`` is mounted as a :class:`ZipLayer`, which
        holds the archive **open**.  Windows refuses to replace a file anyone
        still has open, so a Textures tab that kept a VFS alive would make the
        next save fail with a bare ``PermissionError`` -- the same defect that
        broke Deploy until the suite was first run on Windows.

        Everything here reads eagerly, so callers get plain bytes and images
        that outlive the block; nothing needs the VFS after it closes.
        """
        if not self.stock:
            raise DsoError("open a game folder first")
        merged = vfsmod.Vfs(self.stock.layers)
        mod_layers = list(iter_mod_layers(self.mod)) if self.mod else []
        for layer in mod_layers:
            merged.add(layer)
        try:
            yield merged
        finally:
            for layer in mod_layers:
                try:
                    layer.close()
                except OSError:
                    pass

    def atlas_pages(self, vfs) -> Dict[str, str]:
        """``{page vpath: the .tex that names it}``, read from the indexes.

        Derived rather than guessed from filenames.  It is what lets the
        browser separate the three things that all end in ``.aim`` and look
        alike: the page the game draws from, the packer's leftover source
        images, and standalone artwork.
        """
        from dsotools.formats import a2d

        out: Dict[str, str] = {}
        for vpath in vfs.iter_paths():
            if not vpath.lower().endswith(".tex"):
                continue
            try:
                index = a2d.parse(vfs.read(vpath))
            except DsoError:
                continue
            page = vfsmod.normalise(index.page.replace("\\", "/"))
            entry = vfs.resolve_reference(page, scene_path=vpath, base="")
            out[(entry.vpath if entry else page).lower()] = vpath
        return out

    def texture_assets(self, kinds: Optional[List[str]] = None) -> List[dict]:
        """Every image-ish asset, with where it comes from and what it *is*.

        ``in_mod`` is the column that matters most: it is the difference
        between "you are looking at the stock asset" and "you are looking at
        your own replacement", and getting that wrong is how someone edits a
        page they think they already modified.

        ``kind`` is the second: three quite different things end in ``.aim``,
        and treating them alike is the single most confusing thing about this
        part of the game's data.
        """
        want = set(k.lower() for k in (kinds or self.TEXTURE_KINDS))
        mod_paths = set()
        if self.mod:
            mod_paths = {k for k in self.mod.files() if not k.startswith("loose:")}

        rows: List[dict] = []
        with self.open_vfs() as vfs:
            pages = self.atlas_pages(vfs)
            for vpath in vfs.iter_paths():
                ext = posixpath.splitext(vpath)[1].lower()
                if ext not in want:
                    continue
                entry = vfs.find(vpath)
                if entry is None:              # raced, or an unreadable layer
                    continue
                low = entry.vpath.lower()
                if ext == ".aim" and low in pages:
                    kind = "atlas page"
                elif ext == ".aim" and low.startswith("images/"):
                    # Named by no .tex.  specs/README.md 3: these are the
                    # packer's inputs and the engine does not read them, so
                    # editing one changes nothing on screen.
                    kind = "packer source (not read)"
                else:
                    kind = self.TEXTURE_KINDS[ext]
                rows.append({
                    "vpath": entry.vpath,
                    "kind": kind,
                    "ext": ext,
                    "size": entry.size,
                    "source": entry.origin,
                    "in_mod": low in mod_paths,
                    "atlas": pages.get(low),
                })
        rows.sort(key=lambda r: r["vpath"].lower())
        return rows

    def read_asset(self, vpath: str) -> bytes:
        with self.open_vfs() as vfs:
            return vfs.read(vpath)

    #: Decoded previews, newest last.  Small on purpose: a 1024x1024 page is
    #: 4 MB of RGBA, so an unbounded cache of 168 images would be ~600 MB.
    PREVIEW_CACHE_SIZE = 8

    def _cache_preview(self, key: str, value: dict) -> dict:
        self._preview_cache[key] = value
        while len(self._preview_cache) > self.PREVIEW_CACHE_SIZE:
            self._preview_cache.pop(next(iter(self._preview_cache)))
        return value

    def texture_format(self, vpath: str) -> Optional[str]:
        """``"1024x1024 DXT5, 11 mip(s)"`` for a ``.dds``, or ``None``.

        Header only -- no decode -- because this answers a question asked while
        a dialog is opening: *what should I save mine as?*  The best possible
        answer is what this exact file already is, and the app is the only thing
        in the room that can read it.

        Cached, because the same texture is bound by many submeshes: PlayerShim
        has 368 texture bindings over 103 distinct files.
        """
        if posixpath.splitext(vpath)[1].lower() != ".dds":
            return None
        key = vfsmod.normalise(vpath).lower()
        if key in self._format_cache:
            return self._format_cache[key]
        from dsotools.formats import dds as ddsfmt

        try:
            value = ddsfmt.parse(self.read_asset(vpath), path=vpath).describe()
        except DsoError:
            value = None
        self._format_cache[key] = value
        return value

    def texture_formats(self, vpaths) -> Dict[str, Optional[str]]:
        """``{vpath: format}`` for many textures at once.

        Exists so a caller can fill them in **on a worker**: about 4 ms each, so
        a whole scene's textures is a fifth of a second -- nothing off the GUI
        thread, and a visible hitch on it.
        """
        return {v: self.texture_format(v) for v in vpaths if v}

    def export_asset(self, vpath: str, dest: str, *, image=None) -> str:
        """Write one asset out as a normal file.

        Three different things, chosen by the **destination's** extension:

        ``.png`` / ``.jpg`` / ``.bmp``
            re-encode the *decoded* pixels.
        ``.glb``
            convert a ``.3do`` through :mod:`dsotools.convert.gltf` -- the same
            converter ``3do2gltf.py`` uses, so a model exported here comes back
            through Replace… byte-identically if the DCC tool leaves it alone.
        anything else
            copy the asset's **original bytes**, untouched.

        The last one is the default because it is the one that cannot lose
        anything: a ``.dds`` round-tripped through PNG loses its mipmaps and its
        DXT compression, and a ``.3do`` written back from glTF is only
        byte-identical while nothing has touched it.  Both formats are offered
        for a model, and which one the user wants depends on what they are about
        to do with it -- editing it (``.glb``) or keeping it (``.3do``).

        ``image`` overrides the source pixels, so the Textures tab can export
        the page as edited rather than as stored.
        """
        ext = posixpath.splitext(dest)[1].lower()
        if ext in (".glb", ".gltf"):
            src_ext = posixpath.splitext(vpath)[1].lower()
            if src_ext != ".3do":
                raise DsoError(
                    f"only a .3do model can be exported as glTF; {vpath} is a "
                    f"{src_ext.lstrip('.') or 'file'}",
                    path=vpath,
                )
            if ext == ".gltf":
                # Refuse by name rather than write GLB bytes under a name that
                # says otherwise: `.gltf` is the JSON-plus-sidecars form, and
                # this converter writes the single-file binary one.
                raise DsoError(
                    "this writes the single-file binary form; save it as .glb",
                    path=dest,
                )
            from dsotools.convert import gltf
            from dsotools.formats import threedo

            gltf.export_glb(threedo.parse(self.read_asset(vpath)), dest)
            return dest

        if ext in (".png", ".jpg", ".jpeg", ".bmp"):
            if not atlasmod.have_pillow():
                raise DsoError("exporting an image needs Pillow; install dsotools[image]")
            from PIL import Image

            if image is None:
                decoded = self.decode_preview(vpath)
                image = Image.frombytes(
                    "RGBA", (decoded["width"], decoded["height"]), decoded["rgba"]
                )
            if ext in (".jpg", ".jpeg"):
                image = image.convert("RGB")     # JPEG has no alpha channel
            image.save(dest)
            return dest

        with open(dest, "wb") as fh:
            fh.write(self.read_asset(vpath))
        return dest

    def decode_preview(self, vpath: str) -> dict:
        """One image, decoded to RGBA, with a line describing what it is.

        Returns ``{"width", "height", "rgba", "summary"}``.  Deliberately plain
        data rather than anything Qt-shaped: this is where the decode work
        happens, so it belongs on the side of the boundary that has tests.

        ``.aim`` and ``.dds`` are different formats with different failure
        modes, and the summary says which one you are looking at -- an atlas
        page and a model texture look identical on screen and are edited
        completely differently.
        """
        from dsotools.formats import aim as aimfmt
        from dsotools.formats import dds as ddsfmt

        key = vfsmod.normalise(vpath).lower()
        if key in self._preview_cache:
            return self._preview_cache[key]

        data = self.read_asset(vpath)
        ext = posixpath.splitext(vpath)[1].lower()

        if ext == ".dds":
            img = ddsfmt.parse(data, path=vpath)
            surf = img.surface(0)
            return self._cache_preview(key, {
                "width": surf.width,
                "height": surf.height,
                "rgba": surf.rgba,
                "summary": f"DDS  {img.describe()}",
            })

        if ext == ".aim":
            parsed = aimfmt.parse(data)
            if not atlasmod.have_pillow():
                raise DsoError(
                    "previewing .aim needs Pillow; install dsotools[image]"
                )
            pil = aimfmt.to_image(parsed).convert("RGBA")
            return self._cache_preview(key, {
                "width": pil.width,
                "height": pil.height,
                "rgba": pil.tobytes(),
                "summary": f"AIM  {aimfmt.describe(parsed)}",
            })

        raise DsoError(f"not a previewable image: {vpath}", path=vpath)

    def open_atlas(self, tex_path: str, *, load_anims: bool = True) -> "atlasmod.AtlasPage":
        """Load a ``.tex`` page and everything bound to it.

        The page is fully in memory when this returns, so the VFS -- and the
        mod's zip handle with it -- is closed before the caller ever sees it.
        """
        with self.open_vfs() as vfs:
            return atlasmod.AtlasPage.open(vfs, tex_path, load_anims=load_anims)

    @staticmethod
    def page_writable(page: "atlasmod.AtlasPage") -> Optional[str]:
        """``None`` if this page saves back unchanged, else what will differ.

        Never a refusal any more: every page is editable.  Two of the ten stock
        pages are ``IMJPG24A``, which the library reads but cannot write, and
        those are re-encoded to ``IMTC32`` on save -- lossless, alpha intact,
        and the format six of the other pages already use.

        The string it returns is a *notice*, not an error.  Saying it when the
        page opens rather than at save time is the point: the file will stop
        matching stock byte-for-byte, and that is worth knowing before the work
        rather than after it.
        """
        recoded = getattr(page, "recoded_to", None)
        if not recoded:
            return None
        return (
            f"This page is stored as {page.page_encoding}, which this tool can "
            f"read but not write. Saving re-encodes it as {recoded} — lossless, "
            "and a format the game already uses for other pages, but the file "
            "will no longer match stock byte-for-byte."
        )

    def atlas_problems(self, page: "atlasmod.AtlasPage") -> List[str]:
        """The page's own checks, phrased for a human.

        These are the TEX rules from the validation catalogue, run against the
        *edited* page rather than what is on disk -- so the answer is about what
        saving would produce, which is the only version worth showing.
        """
        out = []
        for sp in page.out_of_bounds():
            out.append(f"TEX002  {sp.stem} lies outside the page ({sp.w}x{sp.h} at {sp.x},{sp.y})")
        for a, b in page.overlaps():
            out.append(f"TEX003  {a.stem} overlaps {b.stem}")
        for vpath, declared, actual in page.anim_mismatches():
            out.append(
                f"TEX004  {posixpath.basename(vpath)} declares "
                f"{declared[0]}x{declared[1]} but its rectangle is {actual[0]}x{actual[1]}"
            )
        return out

    def commit_atlas(self, page: "atlasmod.AtlasPage", *, source: Optional[str] = None,
                     operation: str = "atlas-edit") -> Dict[str, str]:
        """Write every file the edit touched into the mod, in one act.

        ``AtlasPage.save`` hands back ``{vpath: bytes}`` precisely so this can
        be atomic: a page written without its rewritten index is worse than no
        edit at all, because the UI then draws the wrong region of the right
        image and nothing anywhere reports an error.
        """
        if not self.mod:
            raise DsoError("open a mod first")
        files = page.save()
        if not files:
            return {}
        return self._commit_files(files, source=source, operation=operation)

    # -- scenes and models ---------------------------------------------------

    def scenes(self, *, include_low: bool = False) -> List[dict]:
        """Every ``WalhallaScene`` under ``3DView/``, cheaply.

        Listing only -- no XML is parsed here.  612 scenes is enough that
        parsing them all to show a browser would cost seconds for information
        nobody has asked for yet; :meth:`scene_detail` does that on selection.

        ``_low`` twins are hidden by default. 394 scenes have one, they carry
        *independent* bindings, and showing both doubles the list with entries
        that look like duplicates and are not (see ``SCN004``).
        """
        mod_paths = set()
        if self.mod:
            mod_paths = {k for k in self.mod.files() if not k.startswith("loose:")}

        rows: List[dict] = []
        with self.open_vfs() as vfs:
            for vpath in vfs.iter_paths():
                low = vpath.lower()
                if not low.startswith("3dview/") or not low.endswith(".xml"):
                    continue
                if not include_low and low.endswith("_low.xml"):
                    continue
                entry = vfs.find(vpath)
                if entry is None:
                    continue
                rel = entry.vpath[len("3DView/"):] if len(entry.vpath) > 7 else entry.vpath
                rows.append({
                    "vpath": entry.vpath,
                    "name": rel[:-4] if rel.lower().endswith(".xml") else rel,
                    "size": entry.size,
                    "source": entry.origin,
                    "in_mod": entry.vpath.lower() in mod_paths,
                })
        # Shallowest first, then alphabetical.  The ships and stations live at
        # the top of 3DView/; the 42 ActionCams camera scenes and the Canyon set
        # live in subfolders, and sorting purely by path buries every model
        # anyone wants to look at under them.
        rows.sort(key=lambda r: (r["name"].count("/"), r["name"].lower()))
        return rows

    def scene_detail(self, scene_path: str) -> dict:
        """The structured readout: what this scene binds, and whether it adds up.

        Everything the Models tab shows outside the viewport, computed in one
        place so it is testable: the mesh tree, each submesh's shader and
        texture slots, and the ``SCN001`` check that catches the DCC-export
        drift no structural check sees.
        """
        from dsotools.formats import bsd9 as bsd9fmt
        from dsotools.formats import scene as scenefmt
        from dsotools.formats import threedo

        shader_cache: Dict[str, Optional[tuple]] = {}

        with self.open_vfs() as vfs:

            def shader_info(ref: Optional[str]):
                """``(slots, semantics, defaults, material default)``, or ``None``.

                ``None`` means "could not be read", which is different from
                "declares nothing" and has to stay different: 466 effects in
                stock data name a shader that is not installed, and telling
                their author their parameters are inert would be a lie.
                """
                if not ref:
                    return None
                key = ref.lower()
                if key in shader_cache:
                    return shader_cache[key]
                out = None
                entry = vfs.resolve_reference(ref, scene_path=scene_path)
                if entry is not None:
                    try:
                        sh = bsd9fmt.parse(entry.read(), path=entry.vpath)
                        sem = sh.semantics()
                        scalars = {
                            name: p.default[0]
                            for name, p in sem.items()
                            if p.default and len(p.default) == 1
                        }
                        out = (
                            list(sh.texture_slots),
                            sorted(sem),
                            scalars,
                            _material_default(sem),
                        )
                    except DsoError:
                        out = None
                shader_cache[key] = out
                return out

            sc = scenefmt.parse(vfs.read(scene_path), path=scene_path)
            meshes = []
            for mesh in sc.meshes():
                entry = (
                    vfs.resolve_reference(mesh.model, scene_path=scene_path)
                    if mesh.model else None
                )
                submesh_total = lods = None
                if entry is not None:
                    try:
                        model = threedo.parse(entry.read())
                        lods = len(model.lods)
                        submesh_total = sum(len(lod.submeshes) for lod in model.lods)
                    except DsoError:
                        pass

                effects = mesh.effects
                slots = []
                for i, eff in enumerate(effects):
                    material = eff.material
                    entries = [
                        vfs.resolve_reference(t, scene_path=scene_path)
                        for t in eff.textures
                    ]
                    info = shader_info(eff.shader)
                    slots.append({
                        "index": i,
                        "shader": eff.shader,
                        # What the `.bsd9` itself declares, or None when it
                        # could not be read.  `slot_names` names the texture
                        # bindings; `semantics` is the set a <Parameters> block
                        # can actually address -- anything a scene writes that
                        # is not in it lands nowhere, and the editor says so.
                        "slot_names": info[0] if info else None,
                        "semantics": info[1] if info else None,
                        # The shader's compiled-in defaults, which is what
                        # "reset to defaults" resets *to*.  Only meaningful
                        # since `.bsd9` was decoded; before that the editor had
                        # no defensible value to offer.
                        "defaults": info[2] if info else None,
                        "material_default": info[3] if info else None,
                        # What the scene says, verbatim.  Shown to the author,
                        # and what an edit writes back.
                        "textures": list(eff.textures),
                        # What each of those actually resolves to, or None.
                        #
                        # A scene names its textures *relative to itself*
                        # (`textures/x.dds`), and that string is not a vpath --
                        # handing it to anything that reads the VFS fails with
                        # VFS001.  `model_vpath` has always carried the resolved
                        # form for the mesh's `.3do`; textures had no equivalent,
                        # so every consumer either resolved them again itself or
                        # silently used the raw reference.  Resolved here, once,
                        # where the VFS is open and the scene path is known --
                        # which is also the only place that can resolve them
                        # *correctly*, since the reference is relative to this
                        # scene and nothing else.
                        "texture_vpaths": [e.vpath if e else None for e in entries],
                        "resolved": [e is not None for e in entries],
                        "parameters": dict(eff.parameters),
                        "material": material.values if material else (),
                    })

                meshes.append({
                    "name": mesh.name,
                    # The scene-graph path, and the only key that identifies a
                    # mesh.  Names are *not* unique: PlayerShip has 85 meshes
                    # under 28 distinct names, because each of its eleven body
                    # variants contains a mesh called `main_`.  Matching a draw
                    # call to its detail by name therefore picks an arbitrary
                    # variant -- see `mesh_for`.
                    "path": mesh.path(),
                    "model": mesh.model,
                    "model_vpath": entry.vpath if entry else None,
                    "resolved": entry is not None,
                    "lods": lods,
                    "submesh_total": submesh_total,
                    "effect_count": len(effects),
                    # SCN001, the invariant a DCC export breaks silently.
                    "scn001_ok": (
                        None if submesh_total is None
                        else submesh_total == len(effects)
                    ),
                    "slots": slots,
                })

        return {"path": scene_path, "meshes": meshes}

    def model_geometry(self, model_path: str, *, lod: int = 0):
        """Draw calls for a bare ``.3do``, with no scene and no textures.

        A model on its own has no material binding -- that lives in whichever
        scene references it -- so this is deliberately the *raw* mesh.  It is
        what "preview this model" can honestly mean without picking a scene on
        the user's behalf.
        """
        from dsotools.edit import meshview
        from dsotools.formats import threedo

        with self.open_vfs() as vfs:
            model = threedo.parse(vfs.read(model_path))

        if not model.lods:
            raise DsoError(f"{model_path} has no LODs", path=model_path)
        level = model.lods[min(lod, len(model.lods) - 1)]

        import struct

        calls = []
        for i, sub in enumerate(level.submeshes):
            vbuf = bytearray()
            lo, hi = sub.vert_start, sub.vert_start + sub.vert_count
            for v in level.vertices[lo:hi]:
                px, py, pz = v.position
                nx, ny, nz = v.normal
                u, vv = v.uv
                t = v.tangent or (0.0, 0.0, 0.0, 1.0)
                vbuf += struct.pack("<12f", px, py, pz, nx, ny, nz, u, vv,
                                    t[0], t[1], t[2], t[3])
            ibuf = bytearray()
            start = sub.face_start * 3
            for idx in level.indices[start : start + sub.face_count * 3]:
                ibuf += struct.pack("<I", max(0, idx - sub.vert_start))
            calls.append(
                meshview.DrawCall(
                    name=f"submesh {i}", node=posixpath.basename(model_path),
                    lod=lod, index=i, vertices=bytes(vbuf), indices=bytes(ibuf),
                    node_path=f"submesh_{i}",
                )
            )
        return meshview.SceneGeometry(
            model_path, calls, [], {model_path: len(model.lods)}
        )

    def scene_geometry(self, scene_path: str, *, lod: int = 0, mip: int = 1,
                       progress=None):
        """Draw calls for the viewport.  See :mod:`dsotools.edit.meshview`.

        ``mip=1`` by default: the base level of a 1024x1024 texture is 4 MB of
        RGBA and a scene binds dozens, which is memory spent on detail nobody
        can see at viewport size.
        """
        from dsotools.edit import meshview

        with self.open_vfs() as vfs:
            geometry = meshview.build_scene_geometry(
                vfs, scene_path, lod=lod, mip=mip, progress=progress,
                texture_cache=self._texture_cache,
            )
        self._trim_texture_cache()
        return geometry

    #: Roughly how much decoded texture to keep between scene builds.  At
    #: ``mip=1`` a 1024x1024 texture is 1 MB of RGBA, and PlayerShip binds 69
    #: distinct ones -- so this holds a couple of big scenes and then starts
    #: dropping the oldest.  Bounded because the alternative is a viewer that
    #: grows without limit as you browse 612 scenes.
    TEXTURE_CACHE_BYTES = 192 * 1024 * 1024

    def _trim_texture_cache(self) -> None:
        # The build shares one dict between decoded textures and the shader
        # slot-name lookups, so size only what actually carries pixels.
        def weight(value) -> int:
            return len(getattr(value, "rgba", b"") or b"")

        total = sum(weight(v) for v in self._texture_cache.values())
        while total > self.TEXTURE_CACHE_BYTES and self._texture_cache:
            key, dropped = next(iter(self._texture_cache.items()))
            self._texture_cache.pop(key)
            total -= weight(dropped)

    # -- interface layouts (.screen) ------------------------------------------
    #
    # The chain the Interface tab exists to make navigable:
    #
    #     .screen -> element -> scripts\X.anim -> images\Y.aim
    #                                 (via the .tex indexes) page + rectangle
    #
    # Measured end to end on stock data: 1,433 of 1,433 drawable references in
    # the 83 screens reach a real page rectangle, so this is a chain the app can
    # follow rather than one it has to hedge about.

    def screens(self) -> List[dict]:
        """Every ``.screen``, with where it resolves from."""
        mod_paths = set()
        if self.mod:
            mod_paths = {k for k in self.mod.files() if not k.startswith("loose:")}

        rows: List[dict] = []
        with self.open_vfs() as vfs:
            for vpath in vfs.iter_paths():
                if not vpath.lower().endswith(".screen"):
                    continue
                entry = vfs.find(vpath)
                if entry is None:
                    continue
                rows.append({
                    "vpath": entry.vpath,
                    "name": posixpath.basename(entry.vpath),
                    "source": entry.origin,
                    "in_mod": entry.vpath.lower() in mod_paths,
                    "size": entry.size,
                })
        rows.sort(key=lambda r: r["name"].lower())
        return rows

    def open_screen(self, vpath: str) -> dict:
        """One layout as plain data: the screen, and every element on it.

        Each element carries what it *is* (class, name), where it is (its
        rectangle), and what it draws -- the drawable reference resolved one
        step further, to the atlas page and rectangle the pixels really live
        at.  ``scripts\\ND_FrameDark.anim`` is not something anyone can look at;
        a page and a rectangle is.
        """
        from dsotools.edit import screentree
        from dsotools.formats import anim as animfmt
        from dsotools.formats import screen as screenfmt

        with self.open_vfs() as vfs:
            parsed = screenfmt.parse(vfs.read(vpath), path=vpath)
            sprites = self._sprite_index(vfs)
            tree = screentree.resolve(parsed)

            elements = []
            for index, element in enumerate(parsed):
                anims = [r for r in element.references()
                         if r.lower().endswith(".anim")]
                drawables = [
                    self._resolve_drawable(vfs, reference, sprites, animfmt)
                    for reference in anims
                ]
                node = tree[index]
                elements.append({
                    "index": index,
                    "class": element.class_name,
                    "name": element.name,
                    "rect": element.rect,
                    "references": element.references(),
                    "drawables": drawables,
                    # Where it really is: a child's rectangle is an offset from
                    # its parent, so this is what anything drawing must use.
                    "origin": node["origin"],
                    "parent": node["parent"],
                    "depth": node["depth"],
                    # A button names one drawable per state and the disabled
                    # one comes first; at rest it is not the disabled one.
                    "resting": screenfmt.resting_index(element.class_name, anims),
                })

        return {
            "vpath": vpath,
            "name": parsed.name,
            "rect": parsed.screen.rect,
            "declared_children": parsed.declared_children,
            # The derivation accounts for the declared count on all 83 stock
            # screens; if it ever does not, the caller should stay flat rather
            # than place children on a parent it guessed at.
            "tree_consistent": screentree.consistent(parsed),
            "elements": elements,
        }

    # -- Lua scripting ---------------------------------------------------------
    #
    # Two kinds of script, and the difference decides where an edit can go:
    #
    #   mission scripts   <mod>/scripts/*.lua   read loose from the mod folder
    #   libraries         lua/**               only exist in the game root, in
    #                                          no archive; a mod changes them
    #                                          through its root/ payload
    #
    # A library edit is therefore saved into the mod's payload, not into the
    # game -- so it stays reversible and installs like everything else.

    #: How a script row is addressed: ``mod:scripts/X.lua`` or
    #: ``root:lua/mission/X.lua``.
    SCRIPT_MOD = "mod"
    SCRIPT_ROOT = "root"

    def scripts(self) -> List[dict]:
        """Every Lua script in play, and where an edit to it would go."""
        rows: List[dict] = []

        if self.mod:
            folder = os.path.join(self.mod.root, "scripts")
            for name in sorted(os.listdir(folder)) if os.path.isdir(folder) else []:
                if not name.lower().endswith(".lua"):
                    continue
                full = os.path.join(folder, name)
                rows.append({
                    "key": f"{self.SCRIPT_MOD}:scripts/{name}",
                    "name": name,
                    "kind": "mission script",
                    "where": "mod folder",
                    "size": os.path.getsize(full),
                    "editable": True,
                    "path": full,
                })

            payload = rootfiles.payload(self.mod.root)
            for path, item in sorted(payload.items()):
                if not path.lower().endswith(".lua"):
                    continue
                rows.append({
                    "key": f"{self.SCRIPT_ROOT}:{path}",
                    "name": posixpath.basename(path),
                    "kind": "library",
                    "where": "mod payload",
                    "size": item.size,
                    "editable": True,
                    "path": item.source,
                })

        # The game's own libraries, minus anything the mod already carries.
        carried = {r["key"] for r in rows}
        if self.game_path:
            base = os.path.join(self.game_path, "lua")
            for dirpath, _dirnames, filenames in os.walk(base) if os.path.isdir(base) else []:
                for name in sorted(filenames):
                    if not name.lower().endswith(".lua"):
                        continue
                    full = os.path.join(dirpath, name)
                    relative = os.path.relpath(full, self.game_path).replace("\\", "/")
                    key = f"{self.SCRIPT_ROOT}:{relative}"
                    if key in carried:
                        continue
                    rows.append({
                        "key": key,
                        "name": name,
                        "kind": "library",
                        "where": "game folder",
                        "size": os.path.getsize(full),
                        # Editable, but saving copies it into the mod's payload
                        # first -- the game folder is never written directly.
                        "editable": bool(self.mod),
                        "path": full,
                    })
        rows.sort(key=lambda r: (r["kind"], r["name"].lower()))
        return rows

    def read_script(self, key: str) -> str:
        """The text of one script.  cp1252, like everything else the game reads."""
        row = self._script_row(key)
        with open(row["path"], "rb") as handle:
            return handle.read().decode("cp1252", "replace")

    #: What a new non-mission script starts as.
    #:
    #: Top-level code rather than an empty file, because an empty ``.lua`` is
    #: indistinguishable from one that failed to load.  The loader globs
    #: ``scripts\\*.lua`` and runs each one, so a script takes effect simply by
    #: existing -- which also makes top-level code the fallback that works in
    #: the start system, where an ``MTYPE_ALWAYS`` mission gets no callback at
    #: all [game].
    NEW_SCRIPT = (
        "-- {name}\r\n"
        "--\r\n"
        "-- Loaded by the engine's glob over <mod>\\scripts\\*.lua, so this file\r\n"
        "-- runs simply by being here. Top-level code runs at load; a mission\r\n"
        "-- needs NScript.Register, which \u201cNew mission\u2026\u201d writes for you.\r\n"
        "\r\n"
    )

    def new_script(self, name: str) -> str:
        """Write an empty-but-not-blank ``scripts/<name>.lua``; return its path.

        Refuses to land on a script that exists rather than replacing it: the
        Scripting tab lists what is there, and silently overwriting one from a
        *new* action is how work disappears.
        """
        if not self.mod:
            raise DsoError("open a mod first")
        stem = os.path.splitext(os.path.basename(name.strip()))[0]
        if not stem:
            raise DsoError("give the script a name")
        vpath = f"scripts/{stem}.lua"
        why = self.check_new_path(vpath)
        if why is not None:
            raise DsoError(why, path=vpath)

        folder = os.path.join(self.mod.root, "scripts")
        os.makedirs(folder, exist_ok=True)
        full = os.path.join(folder, f"{stem}.lua")
        with open(full, "wb") as handle:
            handle.write(self.NEW_SCRIPT.format(name=f"{stem}.lua")
                         .encode("cp1252", "replace"))
        self.mod.close()
        self.report = None
        self._emit("mod")
        return full

    def save_script(self, key: str, text: str) -> str:
        """Write a script back, and say where it went.

        A library saves into the mod's ``root/`` payload even when it was read
        from the game folder: that keeps the edit reversible and installable,
        instead of quietly overwriting an installation.
        """
        if not self.mod:
            raise DsoError("open a mod first -- scripts are saved into it")
        kind, _, relative = key.partition(":")
        data = text.encode("cp1252", "replace")

        if kind == self.SCRIPT_MOD:
            target = os.path.join(self.mod.root, relative.replace("/", os.sep))
        elif kind == self.SCRIPT_ROOT:
            target = os.path.join(rootfiles.payload_dir(self.mod.root),
                                  relative.replace("/", os.sep))
        else:
            raise DsoError(f"not a script key: {key!r}")

        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "wb") as handle:
            handle.write(data)
        if kind == self.SCRIPT_ROOT:
            self._record_root_manifest()
        self._emit("mod")
        return target

    def _script_row(self, key: str) -> dict:
        for row in self.scripts():
            if row["key"] == key:
                return row
        raise DsoError(f"no such script: {key}")

    def lua_api(self) -> Optional[dict]:
        """The documented API, or ``None`` when this build ships none."""
        if self._lua_api is None:
            self._lua_api = scriptdoc.bundled() or False
        return self._lua_api or None

    def lua_symbols(self) -> Dict[str, dict]:
        """``{'NComm.AddMessage': symbol}``, empty without a database."""
        database = self.lua_api()
        return scriptdoc.index(database) if database else {}

    def check_script(self, text: str) -> List[dict]:
        """What is wrong with this script, judged against the real build.

        The scan itself is :func:`dsotools.luascan.check`; this method's job
        is to gather what it judges against -- the documented reference, the
        table of what the executable really registers, and every function the
        Lua in play defines.  The same call backs the mod-wide ``PRJ012``
        rule, so the editor and the problem list cannot drift apart.
        """
        return luascan.check(
            text,
            symbols=self.lua_symbols(),
            engine=self._engine_table(),
            string_ids=self._string_id_parameters(),
            defined=self._lua_defined(),
        )

    def script_syntax(self, text: str, name: str = "script.lua") -> dict:
        """Parse a script with the game's own compiler.

        ``ScriptCompiler.exe`` is ``luac`` for this engine's modified Lua 4.1,
        so it is the only parser that agrees with the game.  Returns
        ``{ok, message, checked}``; ``checked`` is False when the modding tools
        are not installed, which must not read as "your script is fine".
        """
        if not luac.available():
            return {"ok": True, "checked": False,
                    "message": "the Darkstar One Modding Tools are not "
                               "installed, so the syntax was not checked"}
        ok, message = luac.check_syntax(text, name=name)
        return {"ok": ok, "checked": True, "message": message}

    def build_script_bundle(self, *, out: Optional[str] = None) -> dict:
        """Compile the mod's ``scripts/*.lua`` into ``scripts/user_scripts.bin``.

        Both delivery routes load -- measured in game -- so a mod that ships
        the bundle *and* the sources registers every mission twice.  The second
        registration wins (``SCRIPT_TABLE`` is keyed by mission name), so it is
        harmless, but the caller is told how many sources are involved so it
        can offer to remove them.
        """
        if not self.mod:
            raise DsoError("open a mod first")
        folder = os.path.join(self.mod.root, "scripts")
        sources = sorted(
            os.path.join(folder, f) for f in os.listdir(folder)
            if f.lower().endswith(".lua")) if os.path.isdir(folder) else []
        if not sources:
            raise DsoError("this mod has no scripts/*.lua to compile")
        target = out or os.path.join(folder, "user_scripts.bin")
        written = luac.compile_bundle(sources, target)
        self._emit("mod")
        return {
            "bundle": written,
            "sources": [os.path.basename(s) for s in sources],
            "size": os.path.getsize(written),
        }

    def bundle_contents(self, path: str) -> List[str]:
        """The source names inside a compiled bundle; no compiler needed."""
        with open(path, "rb") as handle:
            return luac.chunk_names(handle.read())

    # ------------------------------------------------------------------
    # sound
    # ------------------------------------------------------------------
    #
    # Two databases are in play and they are additive: the game's
    # ``KlangErzeugerDefault.xml`` and the mod's ``user_sounds.xml``. Both are
    # listed together because the question a modder actually has is "what
    # sounds exist and which are mine", and answering half of it is answering
    # none of it.

    #: Where a new sound's file goes, by kind -- mirroring the stock layout so
    #: a mod's folders look like the game's.
    SOUND_FOLDERS = {
        "Stream": "sound/music(stream)/grp_USER",
        "Sound2D": "sound/sfx(2d)/grp_USER",
        "Sound3D": "sound/sfx(3d)/grp_USER",
    }

    #: The mod's own database, relative to its root.
    SOUND_DB = "user_sounds.xml"

    #: The game's, relative to the installation.
    STOCK_SOUND_DB = "KlangErzeugerDefault.xml"

    def sounds(self) -> List[dict]:
        """Every declared sound, the mod's first, each saying where it lives."""
        rows: List[dict] = []
        for db, where in ((self._mod_sounds(), "mod"),
                          (self._stock_sounds(), "game")):
            if db is None:
                continue
            for entry in db.entries():
                rows.append(self._sound_row(entry, where))
        return rows

    def _sound_row(self, entry, where: str) -> dict:
        full = self._sound_file(entry)
        return {
            "reference": sounddbfmt.qualified(entry),
            "name": entry.name,
            "kind": entry.kind,
            "group": entry.group,
            "resource": entry.resource,
            "where": where,
            "seconds": entry.seconds,
            "channels": entry.channels,
            "frequency": entry.frequency,
            "path": full,
            "exists": bool(full and os.path.exists(full)),
            "editable": where == "mod",
        }

    def _sound_file(self, entry) -> Optional[str]:
        """Absolute path of the file an entry names, if it can be located.

        Audio is loose in the installation -- no ``.cpr`` holds a single WAV or
        MP3 -- so this is a real path a player can open, not a VFS lookup.
        """
        relative = entry.path().replace("/", os.sep)
        root = self.mod.root if entry.is_mod_relative else self.game_path
        if not root:
            return None
        return os.path.join(root, relative)

    def _mod_sounds(self):
        if not self.mod:
            return None
        full = os.path.join(self.mod.root, self.SOUND_DB)
        if not os.path.exists(full):
            return None
        try:
            with open(full, "rb") as handle:
                return sounddbfmt.parse(handle.read(), path=full)
        except (DsoError, OSError):
            return None

    def _stock_sounds(self):
        if self._stock_sound_db is None:
            found = False
            if self.game_path:
                full = os.path.join(self.game_path, self.STOCK_SOUND_DB)
                if os.path.exists(full):
                    try:
                        with open(full, "rb") as handle:
                            found = sounddbfmt.parse(handle.read(), path=full)
                    except (DsoError, OSError):
                        found = False
            self._stock_sound_db = found
        return self._stock_sound_db or None

    def sound_groups(self) -> List[str]:
        """Group paths a new sound could go into, the mod's own first."""
        mine, theirs = [], []
        db = self._mod_sounds()
        if db is not None:
            mine = [g.path for g in db.all_groups()]
        stock = self._stock_sounds()
        if stock is not None:
            theirs = [g.path for g in stock.all_groups()]
        seen, out = set(), []
        for path in mine + ["USER"] + theirs:
            if path and path.lower() not in seen:
                seen.add(path.lower())
                out.append(path)
        return out

    def add_sound(self, source: str, *, name: Optional[str] = None,
                  kind: str = "Sound2D", group: str = "USER") -> dict:
        """Copy a WAV or MP3 into the mod and declare it.

        The file's own headers supply ``Channels``, ``Freq`` and ``Duration``:
        the engine reads those from the database rather than from the file, so
        a wrong number there is a real defect and guessing is not an option.
        """
        if not self.mod:
            raise DsoError("no mod open")
        if kind not in sounddbfmt.SOUND_TAGS:
            raise DsoError(f"{kind!r} is not a sound kind")
        info = audiofmt.probe(source)          # raises before anything is copied

        stem = name or os.path.splitext(os.path.basename(source))[0]
        folder = self.SOUND_FOLDERS[kind]
        target_rel = f"{folder}/{os.path.basename(source)}"
        target = os.path.join(self.mod.root, target_rel.replace("/", os.sep))
        self._check_sound_target(target, source)

        db = self._mod_sounds() or self._new_sound_db()
        resource = sounddbfmt.MOD_PREFIX + target_rel.replace("/", "\\")
        entry = db.add_entry(kind, stem, resource, group=group,
                             **info.as_attributes())

        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copyfile(source, target)
        self._write_sound_db(db)
        return self._sound_row(entry, "mod")

    def override_sound(self, reference: str, source: str) -> dict:
        """Shadow a stock sound: declare its group and name against a new file.

        There is no "replace a stock sound" operation in the engine, any more
        than there is one for missions: a mod declares the same group and name
        and something has to decide between the two. Overriding and adding are
        therefore the same call with a different name in it, which is exactly
        why it is worth its own entry point -- doing it by accident and doing
        it on purpose need to look different in the UI.

        **The mod's declaration wins** [game]: a mod carrying one entry for
        ``Mainmenu/MUSIC_Mainmenu`` played its own track at the main menu
        instead of the stock theme. So this really does displace a stock sound,
        with no game-folder payload involved.

        The stock entry's *kind* is carried over. A ``Stream`` re-declared as a
        ``Sound2D`` would load a whole music track into memory, and nothing
        would say so.
        """
        if not self.mod:
            raise DsoError("no mod open")
        stock = self._stock_sounds()
        entry = stock.resolve(reference) if stock else None
        if entry is None:
            raise DsoError(f"the game does not declare {reference!r}")
        if self._mod_sounds() and self._mod_sounds().resolve(reference):
            raise DsoError(
                f"this mod already declares {reference!r}. Use 'Replace file' "
                f"to point it somewhere else.")
        return self.add_sound(source, name=entry.name, kind=entry.kind,
                              group=entry.group)

    def remove_sound(self, reference: str, *, delete_file: bool = False) -> bool:
        """Undeclare a sound.  The file stays unless ``delete_file``."""
        db = self._mod_sounds()
        if db is None:
            return False
        entry = db.resolve(reference)
        if entry is None:
            return False
        full = self._sound_file(entry)
        mine = entry.is_mod_relative
        if not db.remove_entry(reference):
            return False
        self._write_sound_db(db)
        if delete_file:
            self._prune_sound_file(full, mine)
        return True

    def _prune_sound_file(self, path: Optional[str], mod_relative: bool) -> bool:
        """Delete an audio file the mod no longer needs.  ``True`` if it went.

        Three refusals, and each one is a way this could destroy something:

        * a path that is **not** ``%MOD%``-relative belongs to the game
          installation, and nothing here may delete out of that;
        * a path outside the mod root, which a hand-written ``Resrc`` with
          ``..`` in it could produce;
        * a file **another declaration still names** -- two entries may share
          one file quite legitimately, and deleting it would silence the other
          one while leaving it looking perfectly healthy.
        """
        if not path or not mod_relative or not os.path.exists(path):
            return False
        root = os.path.realpath(self.mod.root)
        target = os.path.realpath(path)
        if os.path.commonpath([root, target]) != root:
            return False
        db = self._mod_sounds()
        if db is not None:
            for other in db.entries():
                if not other.is_mod_relative:
                    continue
                still = self._sound_file(other)
                if still and os.path.normcase(os.path.realpath(still)) == \
                        os.path.normcase(target):
                    return False
        try:
            os.remove(target)
        except OSError:
            return False
        return True

    def replace_sound_file(self, reference: str, source: str, *,
                           delete_old: bool = True) -> dict:
        """Point an existing declaration at a different file, and re-probe it.

        The declared metadata is rewritten too. Leaving a stale ``Duration``
        behind is the kind of thing that plays the first two seconds of a
        thirty-second track and looks like a corrupt file.

        The file that was there is **deleted**, unless something else still
        names it or it belongs to the game. Keeping it was the old behaviour
        and it was wrong: a mod that had swapped a 2.8 MB track twice shipped
        both of the tracks nobody could hear any more, and only ``SND002``
        noticed. ``delete_old=False`` keeps it for a caller that wants the
        file left alone.
        """
        if not self.mod:
            raise DsoError("no mod open")
        db = self._mod_sounds()
        entry = db.resolve(reference) if db else None
        if entry is None:
            raise DsoError(f"this mod does not declare {reference!r}")
        info = audiofmt.probe(source)
        folder = self.SOUND_FOLDERS[entry.kind]
        target_rel = f"{folder}/{os.path.basename(source)}"
        target = os.path.join(self.mod.root, target_rel.replace("/", os.sep))
        previous = self._sound_file(entry)
        previous_was_mine = entry.is_mod_relative
        replacing_in_place = (os.path.normcase(target)
                              == os.path.normcase(previous or ""))
        if not replacing_in_place:
            self._check_sound_target(target, source)
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copyfile(source, target)

        db.set_resource(reference,
                        sounddbfmt.MOD_PREFIX + target_rel.replace("/", "\\"))
        entry.set_metadata(info.as_attributes())
        self._write_sound_db(db)

        # After the write, so the "is anything else using it" check sees the
        # database as it now stands rather than as it was.
        if delete_old and not replacing_in_place:
            self._prune_sound_file(previous, previous_was_mine)
        return self._sound_row(entry, "mod")

    def probe_sound(self, path: str) -> dict:
        """What a file on disk says about itself, before declaring it."""
        info = audiofmt.probe(path)
        return {"kind": info.kind, "channels": info.channels,
                "frequency": info.frequency, "samples": info.samples,
                "seconds": info.seconds, "bytes": info.bytes}

    def _check_sound_target(self, target: str, source: str) -> None:
        """Refuse to overwrite a different file that already sits there.

        Two sounds added from ``Click.wav`` in different folders would land on
        one path: the second copy would replace the first, and the first
        entry -- still declared, still listed, still apparently fine -- would
        quietly start playing the second's audio. Identical bytes are let
        through, because re-adding the same file is not a conflict.
        """
        if not os.path.exists(target):
            return
        try:
            same = (os.path.getsize(target) == os.path.getsize(source)
                    and open(target, "rb").read() == open(source, "rb").read())
        except OSError:
            same = False
        if not same:
            raise DsoError(
                f"{os.path.basename(target)} already exists in this mod with "
                f"different contents. Rename the file you are adding — "
                f"overwriting it would silently change whatever already points "
                f"at it.",
                path=target)

    def _new_sound_db(self):
        """An empty ``user_sounds.xml``, shaped like the ones Ascaron shipped."""
        blank = (b'<?xml version="1.0" encoding="ISO-8859-1"?>\r\n'
                 b"<ASE_Database>\r\n</ASE_Database>\r\n")
        return sounddbfmt.parse(blank,
                                path=os.path.join(self.mod.root, self.SOUND_DB))

    def _write_sound_db(self, db) -> str:
        full = os.path.join(self.mod.root, self.SOUND_DB)
        with open(full, "wb") as handle:
            handle.write(db.to_bytes())
        self.mod.close()
        self._emit("mod")
        return full

    # ------------------------------------------------------------------
    # missions
    # ------------------------------------------------------------------
    #
    # There is no "replace a mission" API.  ``NScript.Register`` keys the
    # mission table by ``Name``, and a mod's ``scripts\\`` is read after
    # ``lua/mission/missions.bin``, so registering an existing name overwrites
    # it.  Everything below is built on that one fact.

    def stock_missions(self) -> List[dict]:
        """Every mission the stock game registers, sorted by name."""
        return [{"name": m.name, "type": m.type, "group": m.group,
                 "states": list(m.states), "source": m.source,
                 "overridden": self._overrides_mission(m.name)}
                for m in missionsmod.stock(self._mission_table())]

    def _mission_table(self):
        if self._missions is None:
            try:
                self._missions = missionsmod.bundled() or False
            except DsoError:
                self._missions = False
        return self._missions or None

    def stock_mission(self, name: str) -> Optional[dict]:
        """One stock mission by name, or ``None``.

        Goes through the session's cached table rather than re-reading the
        shipped JSON, because the UI asks this on every keystroke.
        """
        found = missionsmod.by_name(name, self._mission_table())
        if found is None:
            return None
        return {"name": found.name, "type": found.type, "group": found.group,
                "states": list(found.states), "source": found.source}

    def mission_states(self) -> List[str]:
        """Every state name the stock missions use, for a new mission."""
        seen = set()
        for mission in missionsmod.stock(self._mission_table()):
            seen.update(mission.states)
        return sorted(seen) or list(missionsmod.DEFAULT_STATES)

    def registered_in_mod(self) -> Dict[str, str]:
        """``{mission name: script file}`` for what the open mod registers.

        Read from the mod's own Lua rather than from a record, so a script
        hand-edited outside the suite still counts.
        """
        if not self.mod:
            return {}
        found = missionsmod.registrations(os.path.join(self.mod.root, "scripts"))
        return {name: files[0] for name, files in found.items()}

    def _overrides_mission(self, name: str) -> Optional[str]:
        return self.registered_in_mod().get(name)

    def create_mission_override(self, name: str, *, overwrite: bool = False) -> str:
        """Write a script that replaces the stock mission ``name``.

        The file is named after the *mission*, not after the stock chunk: two
        stock chunks register a name that differs from their file name, and it
        is the name that decides what gets replaced.
        """
        mission = missionsmod.by_name(name, self._mission_table())
        if mission is None:
            raise DsoError(f"no stock mission called {name!r}")
        return self._write_mission(mission.file_name,
                                   missionsmod.override_template(mission),
                                   overwrite=overwrite)

    def create_mission(self, name: str, *, type: str = "MTYPE_ALWAYS",
                       group: int = 0, states=None,
                       overwrite: bool = False) -> str:
        """Write a new mission script.

        Refuses a name the stock game already uses unless the caller asked for
        an override, because doing it by accident silently disables a stock
        mission.
        """
        clash = missionsmod.by_name(name, self._mission_table())
        if clash is not None and not overwrite:
            raise DsoError(
                f"{name!r} is a stock mission; registering it would replace "
                f"that mission rather than add a new one. Use "
                f"'override a stock mission' if that is what you want.")
        template = missionsmod.new_template(
            name, type=type, group=group,
            states=states or missionsmod.DEFAULT_STATES)
        return self._write_mission(f"{name}.lua", template, overwrite=overwrite)

    def _write_mission(self, file_name: str, text: str, *, overwrite: bool) -> str:
        if not self.mod:
            raise DsoError("no mod open")
        folder = os.path.join(self.mod.root, "scripts")
        os.makedirs(folder, exist_ok=True)
        full = os.path.join(folder, file_name)
        if os.path.exists(full) and not overwrite:
            raise DsoError(f"{file_name} already exists in this mod", path=full)
        with open(full, "w", encoding="utf-8", newline="\r\n") as handle:
            handle.write(text)
        self._lua_defined_cache = None
        self.mod.close()
        self._emit("mod")
        return full

    # ------------------------------------------------------------------
    # string tables
    # ------------------------------------------------------------------
    #
    # A ``.res`` stores hashed ids only, so the authored pairs live in the
    # ``.dsoproject`` and the table is a build product.  Everything the UI
    # needs -- including "does this id already mean something in the stock
    # game" -- is answered here so no tab has to know the format.

    #: Where a mod's own table has to live for the engine to load it.
    STRINGS_PATH = "strings/user_strings.res"

    def strings(self) -> List[dict]:
        """The mod's authored text, one row per StringId.

        ``in_table`` says whether the built ``.res`` currently on disk carries
        that id; a row that is ``False`` has been edited but not built.
        """
        if not self.mod:
            return []
        built = self._built_string_table()
        keys = set(built.by_hash()) if built else set()
        rows = []
        for identifier, text in self.project.strings:
            key = resfmt.string_hash(identifier)
            rows.append({
                "id": identifier,
                "text": text,
                "hash": key,
                "in_table": key in keys,
            })
        return rows

    def _built_string_table(self):
        """The ``.res`` as it stands on disk, or ``None``."""
        if not self.mod:
            return None
        full = os.path.join(self.mod.root, "strings", "user_strings.res")
        if not os.path.exists(full):
            return None
        try:
            with open(full, "rb") as handle:
                return resfmt.parse(handle.read(), path=full)
        except (DsoError, OSError, UnicodeDecodeError):
            return None

    def orphan_strings(self) -> List[int]:
        """Keys in the built table that no authored id accounts for.

        These are unreachable: nothing can name them any more, because the
        table does not store ids.  Usually the sign of a hand-edited file or a
        project record that was lost.
        """
        built = self._built_string_table()
        if built is None:
            return []
        authored = {resfmt.string_hash(i) for i, _t in self.project.strings}
        return sorted(k for k in built.by_hash() if k not in authored)

    def save_strings(self, pairs: Sequence) -> str:
        """Record the pairs in the project and write the ``.res``.

        Returns the path written.  Raises :class:`DsoError` on a collision --
        two ids on one key means one of the two texts can never be shown, and
        the shipped converter refused it too.
        """
        if not self.mod:
            raise DsoError("no mod open")
        table = resfmt.from_pairs([(str(i), str(x)) for i, x in pairs])
        folder = os.path.join(self.mod.root, "strings")
        os.makedirs(folder, exist_ok=True)
        full = os.path.join(folder, "user_strings.res")
        with open(full, "wb") as handle:
            handle.write(resfmt.build(table))
        self.project.record_strings([(str(i), str(x)) for i, x in pairs])
        self.project.save(self.mod.root)
        # The mod gained a file.  Dropping the cached index is not enough --
        # nothing rebuilds from it until something asks, so the Project tab
        # went on showing a mod without its string table until a validation
        # run happened to refresh it.  Every write here has to announce itself.
        self.mod.close()
        self._emit("mod")
        return full

    def stock_string(self, identifier: str) -> Optional[str]:
        """What a StringId means in the stock game, if anything.

        Lets the UI warn before a mod silently shadows an id the game already
        uses, and lets an author reuse stock text on purpose.
        """
        for table in self._stock_string_tables():
            hit = table.text(identifier)
            if hit is not None:
                return hit
        return None

    def _stock_string_tables(self) -> List["resfmt.StringTable"]:
        if self._stock_strings is None:
            found = []
            if self.stock is not None:
                for candidate in validatemod.STOCK_TABLES:
                    try:
                        found.append(resfmt.parse(self.stock.read(candidate)))
                    except (DsoError, KeyError, OSError, UnicodeDecodeError):
                        continue
            self._stock_strings = found
        return self._stock_strings

    def _engine_table(self):
        if self._lua_engine is None:
            try:
                self._lua_engine = scriptdoc.engine() or False
            except DsoError:
                self._lua_engine = False
        return self._lua_engine or None

    def _string_id_parameters(self):
        if self._lua_string_ids is None:
            database = self.lua_api()
            self._lua_string_ids = (
                scriptdoc.string_id_parameters(database) if database else {})
        return self._lua_string_ids

    def _lua_defined(self) -> set:
        """Every function the Lua sources in play define for themselves.

        Cached per session: it reads a dozen files, and the check runs on
        every keystroke pause in the editor.
        """
        if self._lua_defined_cache is None:
            found = set()
            for row in self.scripts():
                try:
                    with open(row["path"], "rb") as handle:
                        text = handle.read().decode("cp1252", "replace")
                except OSError:
                    continue
                found |= luascan.definitions(
                    luascan.strip_comments(text))
            self._lua_defined_cache = found
        return self._lua_defined_cache

    # -- files the mod puts into the game installation -------------------------
    #
    # Not everything can be delivered from the mod folder.  No .cpr archive
    # holds a single ``lua/`` entry, so the shared mission libraries exist only
    # as loose files in the game root, and a mod that changes them has to
    # overwrite the installation.  ``dsotools.rootfiles`` does that reversibly;
    # these are the calls the app makes.

    def installation_state(self, *, quick: bool = True) -> dict:
        """How this installation differs from a stock one.

        The stock state is recorded data (``dsotools.baseline``), not something
        measured from whatever happens to be installed -- which is the mistake
        that once made a modded ``lua/`` look like the game's own.  Everything
        else is reported as additive: modified, added, missing.

        Works the same on both editions; the GOG and Steam builds differ only
        in the DRM wrapper over ``.text``.
        """
        if not self.game_path:
            raise DsoError("open a game folder first")
        if baseline.bundled() is None:
            return {"known": False, "edition": None,
                    "unchanged": [], "modified": [], "added": [], "missing": []}
        edition = baseline.detect_edition(self.game_path)
        result = baseline.classify(self.game_path, edition=edition, quick=quick)
        return {"known": True, "edition": edition, **result}

    def non_stock_files(self, *, quick: bool = True) -> List[dict]:
        """Only the differences, with who owns each one if anything does.

        A file the ledger knows about was installed by a mod through this
        tool; anything else was put there by hand, and is exactly what
        :meth:`adopt_root_files` exists to bring under control.
        """
        state = self.installation_state(quick=quick)
        if not state["known"]:
            return []
        ledger = rootfiles.load_ledger(self.game_path)
        out = []
        for kind in ("modified", "added", "missing"):
            for path in state[kind]:
                out.append({
                    "path": path,
                    "state": kind,
                    "owner": rootfiles.owner_of(ledger, path),
                })
        return out

    def root_payload(self) -> List[dict]:
        """What the open mod delivers into the game folder."""
        if not self.mod:
            return []
        ledger = (rootfiles.load_ledger(self.game_path).get("mods", {})
                  if self.game_path else {})
        mine = ledger.get(self.mod.name, {}).get("files", {})
        rows = []
        for path, item in sorted(rootfiles.payload(self.mod.root).items()):
            live = os.path.join(self.game_path or "", path.replace("/", os.sep))
            on_disk = os.path.isfile(live) if self.game_path else False
            rows.append({
                "path": path,
                "size": item.size,
                "sha256": item.sha256,
                "installed": path in mine,
                "current": bool(on_disk
                                and mine.get(path, {}).get("sha256") == item.sha256),
                "in_game": on_disk,
            })
        return rows

    def root_plan(self) -> List[dict]:
        """What installing the payload would do, file by file."""
        self._need_game_and_mod()
        items = rootfiles.payload(self.mod.root)
        return [{"path": a.path, "what": a.what, "owner": a.owner, "size": a.size}
                for a in rootfiles.plan(self.game_path, self.mod.name, items)]

    def install_root_files(self, *, allow_conflicts: bool = False) -> dict:
        """Place the payload into the installation, backing up what it displaces."""
        self._need_game_and_mod()
        result = rootfiles.install(self.game_path, self.mod.name, self.mod.root,
                                   allow_conflicts=allow_conflicts)
        self._record_root_manifest()
        self._emit("mod")
        return _root_result(result)

    def uninstall_root_files(self, mod_name: Optional[str] = None, *,
                             force: bool = False) -> dict:
        """Undo an install: restore originals, remove what was added."""
        name = self._root_mod_name(mod_name)
        result = rootfiles.uninstall(self.game_path, name, force=force)
        self._emit("mod")
        return _root_result(result)

    def swap_root_files(self, from_mod: str) -> dict:
        """Take one mod's payload out and put the open mod's in, in one step."""
        self._need_game_and_mod()
        result = rootfiles.swap(self.game_path, from_mod, self.mod.name,
                                self.mod.root)
        self._record_root_manifest()
        self._emit("mod")
        return _root_result(result)

    def adopt_root_files(self, paths: Sequence[str],
                         mod_name: Optional[str] = None) -> dict:
        """Record files already in the installation as a mod's.

        For the state this tool usually finds: the payload was copied in by
        hand and whatever it replaced is gone.  Adopting makes the ownership
        describable without inventing a backup that never existed.
        """
        name = self._root_mod_name(mod_name)
        result = rootfiles.adopt(self.game_path, name, paths)
        self._emit("mod")
        return _root_result(result)

    def import_root_zip(self, zip_path: str, *,
                        only: Optional[Sequence[str]] = None) -> List[str]:
        """Take a "copy this into the game root" archive into the mod."""
        if not self.mod:
            raise DsoError("open a mod first")
        written = rootfiles.import_zip(zip_path, self.mod.root, only=only)
        self._record_root_manifest()
        self._emit("mod")
        return written

    def installed_root_mods(self) -> List[dict]:
        """Every mod this installation carries root files for."""
        if not self.game_path:
            return []
        ledger = rootfiles.load_ledger(self.game_path)
        return [
            {
                "name": name,
                "files": len(record.get("files", {})),
                "installed": record.get("installed", ""),
                "adopted": bool(record.get("adopted")),
                "restorable": sum(1 for e in record.get("files", {}).values()
                                  if e.get("displaced") or e.get("was_absent")),
            }
            for name, record in sorted(ledger.get("mods", {}).items())
        ]

    def verify_root_files(self, mod_name: Optional[str] = None) -> Dict[str, str]:
        """``{path: what is wrong}`` -- empty when the installation matches."""
        if not self.game_path:
            return {}
        return rootfiles.verify(self.game_path, mod_name)

    def unclaimed_root_files(self) -> List[dict]:
        """Files in the game folder that look like the mod's and nobody claims.

        Reported so they can be adopted, which is the only way an installation
        that predates this tool becomes manageable.  Matched against the open
        mod's payload, because that is the only evidence there is.
        """
        if not self.game_path or not self.mod:
            return []
        ledger = rootfiles.load_ledger(self.game_path)
        out = []
        for path, item in sorted(rootfiles.payload(self.mod.root).items()):
            if rootfiles.owner_of(ledger, path):
                continue
            live = os.path.join(self.game_path, path.replace("/", os.sep))
            if not os.path.isfile(live):
                continue
            out.append({
                "path": path,
                "identical": _sha256_of(live) == item.sha256,
                "size": os.path.getsize(live),
            })
        return out

    def _record_root_manifest(self) -> None:
        """Keep the .dsoproj manifest in step with what is in ``root/``."""
        if not self.mod or self.project is None:
            return
        self.project.record_root_files({
            path: {"sha256": item.sha256, "size": item.size}
            for path, item in rootfiles.payload(self.mod.root).items()
        })
        self.project.save(self.mod.root)

    def _root_mod_name(self, mod_name: Optional[str]) -> str:
        if not self.game_path:
            raise DsoError("open a game folder first")
        name = mod_name or (self.mod.name if self.mod else None)
        if not name:
            raise DsoError("open a mod first")
        return name

    def _need_game_and_mod(self) -> None:
        if not self.game_path:
            raise DsoError("open a game folder first")
        if not self.mod:
            raise DsoError("open a mod first")

    def _sprite_index(self, vfs) -> Dict[str, tuple]:
        """``{source image: (tex, page, rect)}`` over every ``.tex``.

        Cached for the session: it reads all ten indexes, and the Interface tab
        wants it for every screen it opens.
        """
        if self._sprites is None:
            from dsotools.formats import a2d

            out: Dict[str, tuple] = {}
            for vpath in vfs.iter_paths():
                if not vpath.lower().endswith(".tex"):
                    continue
                try:
                    index = a2d.parse(vfs.read(vpath))
                except DsoError:
                    continue
                for sub in index.subimages:
                    key = sub.name.replace("\\", "/").lower()
                    out[key] = (vpath, index.page, (sub.x, sub.y, sub.w, sub.h))
            self._sprites = out
        return self._sprites

    def _resolve_drawable(self, vfs, reference, sprites, animfmt) -> dict:
        """One ``scripts\\X.anim`` reference, followed as far as it goes."""
        out = {
            "reference": reference,
            "anim_vpath": None,
            "source": None,
            "drawn_size": None,
            "source_size": None,
            "tex": None,
            "page": None,
            "rect": None,
        }
        entry = vfs.find(reference.replace("\\", "/"))
        if entry is None:
            return out
        out["anim_vpath"] = entry.vpath
        try:
            drawable = animfmt.parse(entry.read(), path=entry.vpath)
        except DsoError:
            return out
        out["source"] = drawable.source
        out["drawn_size"] = drawable.size
        out["source_size"] = drawable.source_size
        found = sprites.get(drawable.source.replace("\\", "/").lower())
        if found:
            out["tex"], out["page"], out["rect"] = found
        return out

    def screen_artwork(self, vpath: str, progress=None) -> List[dict]:
        """What every element on a screen actually draws, as pixels.

        ``[{index, rect, width, height, rgba, how}]`` -- ``how`` being
        ``"exact"``, ``"nine-slice"`` or ``"stretched"``, because the third is
        an approximation and a preview that will not say so is worse than one
        that shows outlines.

        Measured over the 83 stock screens: of the 694 elements that draw,
        **399 are exact, 123 are nine-slice frames with all nine tiles present,
        and 172 are stretched** single sprites.

        Pages are opened **once each**, not once per element: a screen binds
        three or four atlas pages and each is a 1024x1024 decode, so the naive
        loop costs sixty of them.
        """
        from dsotools.edit import atlas as atlasmod
        from dsotools.edit import nineslice

        if not atlasmod.have_pillow():
            raise DsoError("drawing the artwork needs Pillow; install dsotools[image]")

        detail = self.open_screen(vpath)
        # The resting state, not the first reference: a button lists its
        # disabled artwork first, and a preview full of greyed-out buttons is
        # not the screen the game draws.
        wanted = [e for e in detail["elements"]
                  if e["drawables"] and _resting(e).get("tex")]

        by_tex: Dict[str, list] = {}
        for element in wanted:
            by_tex.setdefault(_resting(element)["tex"], []).append(element)

        out: List[dict] = []
        done = 0
        with self.open_vfs() as vfs:
            pages = {}
            for tex, elements in by_tex.items():
                if progress:
                    progress(done, len(wanted), posixpath.basename(tex))
                if tex not in pages:
                    pages[tex] = atlasmod.AtlasPage.open(vfs, tex, load_anims=False)
                page = pages[tex]

                for element in elements:
                    drawable = _resting(element)
                    image, how = self._element_artwork(
                        page, pages, vfs, atlasmod, nineslice, drawable, element["rect"])
                    done += 1
                    if image is None:
                        continue
                    out.append({
                        "index": element["index"],
                        "rect": element["rect"],
                        "origin": element["origin"],
                        "width": image.width,
                        "height": image.height,
                        "rgba": image.tobytes(),
                        "how": how,
                    })
        if progress:
            progress(len(wanted), len(wanted), "")
        return out

    def _element_artwork(self, page, pages, vfs, atlasmod, nineslice, drawable, rect):
        """One element's pixels, and how honestly they were produced."""
        from PIL import Image

        target = (max(int(rect[2]), 1), max(int(rect[3]), 1))
        source = drawable["source"]
        try:
            sprite = page.extract(source).convert("RGBA")
        except Exception:                      # noqa: BLE001 - not in this page
            return None, "missing"

        if (sprite.width, sprite.height) == target:
            return sprite, "exact"

        siblings = nineslice.sibling_names(source)
        if siblings:
            tiles = {}
            for suffix, name in siblings.items():
                found = self._sprite_index(vfs).get(name.replace("\\", "/").lower())
                if not found:
                    tiles = {}
                    break
                tex = found[0]
                if tex not in pages:
                    pages[tex] = atlasmod.AtlasPage.open(vfs, tex, load_anims=False)
                try:
                    tiles[suffix] = pages[tex].extract(name)
                except Exception:              # noqa: BLE001
                    tiles = {}
                    break
            if tiles:
                return nineslice.compose(tiles, target), "nine-slice"

        return sprite.resize(target, Image.Resampling.NEAREST), "stretched"

    def set_screen_rects(self, vpath: str, edits) -> Dict[str, str]:
        """Apply ``[(element index, (x, y, w, h))]`` and save into the mod.

        Only those rectangles are rewritten: the parser keeps every element's
        bytes verbatim, so a moved button changes the four integers that say
        where it is and nothing else -- which is what keeps diff-against-stock
        readable on a 216 KB layout.
        """
        if not self.mod:
            raise DsoError("open a mod first")

        from dsotools.formats import screen as screenfmt

        with self.open_vfs() as vfs:
            parsed = screenfmt.parse(vfs.read(vpath), path=vpath)

        from dsotools.edit import screentree

        parent_of = screentree.parents(parsed.elements)
        for index, rect in edits:
            if not 0 <= index < len(parsed.elements):
                raise DsoError(
                    f"{vpath} has {len(parsed.elements)} elements; no {index}",
                    path=vpath,
                )
            if parent_of[index] >= 0:
                owner = parsed.elements[parent_of[index]].name
                raise DsoError(
                    f"{parsed.elements[index].name} is part of {owner}; the "
                    f"engine places it, so moving it here would not hold",
                    path=vpath,
                )
            parsed.elements[index].rect = tuple(int(v) for v in rect)

        return self._commit_files({vpath: parsed.to_bytes()},
                                  operation="edit-screen")

    def screen_element_image(self, vpath: str, index: int, which=None) -> dict:
        """The pixels one element draws.

        Cropped out of the atlas page the chain ends at, because that is the
        only place the artwork exists: the ``images\\*.aim`` of the same name is
        the packer's leftover source and is not what the game reads.

        ``which`` picks a state by position; left out, it is the resting one,
        which for a button is not the disabled artwork it lists first.
        """
        detail = self.open_screen(vpath)
        elements = detail["elements"]
        if not 0 <= index < len(elements):
            raise DsoError(f"no element {index} in {vpath}", path=vpath)
        drawables = elements[index]["drawables"]
        if which is None:
            which = elements[index].get("resting", 0)
        if not drawables or which >= len(drawables):
            raise DsoError("this element draws nothing", path=vpath)
        drawable = drawables[which]
        if not drawable["tex"] or not drawable["source"]:
            raise DsoError(
                f"{drawable['reference']} does not reach an atlas page", path=vpath)
        if not atlasmod.have_pillow():
            raise DsoError("previewing needs Pillow; install dsotools[image]")

        with self.open_vfs() as vfs:
            page = atlasmod.AtlasPage.open(vfs, drawable["tex"], load_anims=False)
            image = page.extract(drawable["source"]).convert("RGBA")
        width, height = image.size
        return {
            "width": width,
            "height": height,
            "rgba": image.tobytes(),
            "summary": f"{drawable['source']}  {width}x{height}",
        }

    # -- the data tables (inifiles/) ------------------------------------------
    #
    # 68 files, 13,091 sections, 120,034 entries, and no reverse engineering
    # needed for any of it -- which is why this is the biggest un-tooled surface
    # in the game and the cheapest one to tool.
    #
    # A *section* is the unit, not a row in a grid.  Only 27 of the 68 files are
    # uniform enough for a grid (Planets.ini is 5,970 sections sharing one key
    # set, 99%), while the cluster files mix object types and share a key set
    # only 40-55% of the time.  An editor built on the grid would work for a
    # third of the data and lie about the rest.

    def ini_files(self) -> List[dict]:
        """Every ``inifiles/*.ini``, with where it resolves from."""
        mod_paths = set()
        if self.mod:
            mod_paths = {k for k in self.mod.files() if not k.startswith("loose:")}

        rows: List[dict] = []
        with self.open_vfs() as vfs:
            for vpath in vfs.iter_paths():
                low = vpath.lower()
                if not low.endswith(".ini") or not low.startswith("inifiles/"):
                    continue
                entry = vfs.find(vpath)
                if entry is None:
                    continue
                rows.append({
                    "vpath": entry.vpath,
                    "name": posixpath.basename(entry.vpath),
                    "source": entry.origin,
                    "in_mod": entry.vpath.lower() in mod_paths,
                    "size": entry.size,
                })
        rows.sort(key=lambda r: r["name"].lower())
        return rows

    def open_ini(self, vpath: str) -> dict:
        """One INI file as plain data: sections, entries, and its own problems.

        ``{"vpath", "sections": [{"name", "entries": [{"key", "value",
        "comment"}], "duplicate_keys"}], "duplicate_sections"}``.

        Duplicates are reported rather than resolved.  Real files contain them
        and the engine tolerates them, but which one wins is not something this
        tool should decide quietly on an author's behalf -- and a duplicated key
        is usually a mistake whose effect is the opposite of what was intended.
        """
        from dsotools.formats import ini as inifmt

        with self.open_vfs() as vfs:
            parsed = inifmt.parse(vfs.read(vpath), path=vpath)

        return {
            "vpath": vpath,
            "duplicate_sections": parsed.duplicate_sections(),
            "sections": [
                {
                    "name": s.name,
                    "duplicate_keys": s.duplicate_keys(),
                    "entries": [
                        {"key": e.key, "value": e.value, "comment": e.comment}
                        for e in s.entries
                    ],
                }
                for s in parsed.sections
            ],
        }

    def set_ini_values(self, vpath: str,
                       edits: List[tuple]) -> Dict[str, str]:
        """Apply ``[(section, key, value)]`` to an INI and save it into the mod.

        Re-read, edited and written through :mod:`dsotools.formats.ini`, whose
        round-trip is line-preserving: an untouched file comes back byte for
        byte and a changed value rewrites exactly its own line.  That is what
        keeps diff-against-stock meaningful on a 3 MB file where the author
        changed one number.

        Refuses a value the dialect cannot store rather than mangling it -- a
        newline in a value does not wrap, it ends the entry and turns the rest
        into a line that parses as nothing.
        """
        if not self.mod:
            raise DsoError("open a mod first")

        from dsotools.formats import ini as inifmt

        with self.open_vfs() as vfs:
            parsed = inifmt.parse(vfs.read(vpath), path=vpath)

        for section_name, key, value in edits:
            section = parsed.section(section_name)
            if section is None:
                raise DsoError(f"no section [{section_name}] in {vpath}",
                               path=vpath)
            if section.entry(key) is None:
                raise DsoError(
                    f"[{section_name}] has no key {key!r}; this edits values, "
                    "it does not invent them",
                    path=vpath,
                )
            section.set(key, inifmt.check_value(value))

        return self._commit_files({vpath: parsed.to_bytes()},
                                  operation="edit-ini")

    # -- linked assets --------------------------------------------------------

    def used_by(self, vpath: str) -> List[dict]:
        """Everything that references ``vpath``, from the asset index.

        The reverse lookup the app exists to make instant: select a texture and
        see every scene that binds it *before* deciding whether editing it is
        safe.  Requires the index; says so rather than returning a misleading
        empty list.
        """
        if self.index is None:
            raise DsoError(
                "the asset index is not built yet — Tools ▸ Rebuild asset index"
            )
        return [
            {"src": r["src"], "kind": r["kind"], "slot": r["slot"], "node": r["node"]}
            for r in self.index.used_by(vpath)
        ]

    def asset_info(self, refs) -> Dict[str, dict]:
        """``{reference: facts}`` for many assets, in one pass.

        The facts are everything a row about an asset needs to be honest:
        where it resolves from, whether the open mod owns it, what format it is
        (for a texture), and whether it can be reset to stock.  Keyed by the
        string the caller passed, so a raw scene reference and a resolved vpath
        both work as lookups.

        One method rather than four so the two views of the linked-assets panel
        cannot disagree.  They did: the whole-scene tree built its own rows and
        knew only "does it resolve", so a texture the mod had just replaced
        showed **no source and no in-mod marker at all** -- exactly the state
        the panel exists to make visible.

        Worth calling on a worker: the lookups are cheap, but a texture's format
        means reading its header.
        """
        mod_paths = set()
        if self.mod:
            mod_paths = {k for k in self.mod.files() if not k.startswith("loose:")}

        out: Dict[str, dict] = {}
        with self.open_vfs() as vfs:
            for ref in refs:
                if not ref or ref in out:
                    continue
                entry = vfs.find(ref) or vfs.resolve_reference(ref)
                vpath = entry.vpath if entry else ref
                out[ref] = {
                    "vpath": vpath,
                    "resolved": entry is not None,
                    "source": entry.origin if entry else "",
                    "in_mod": vpath.lower() in mod_paths,
                    # What it is, not just where it is.  This panel is where a
                    # replacement starts, and "which DXT do I save as?" is the
                    # question that decides whether the result looks right --
                    # so the answer belongs on the row, permanently, not only
                    # in a notice that can be told never to appear again.
                    "format": self.texture_format(vpath) if entry else None,
                    # `None` means "yes, it can".  Passed the hoisted file set
                    # because the un-hoisted call walks the mod and opens its
                    # archive, and doing that once per asset in a scene is a
                    # hundred walks for one panel.
                    "reset_reason": self.can_reset_to_stock(
                        vpath, mod_files=mod_paths
                    ),
                }
        return out

    def describe_assets(self, vpaths: List[tuple]) -> List[dict]:
        """``[(role, vpath)]`` -> rows for the linked-assets panel."""
        info = self.asset_info([ref for _role, ref in vpaths])
        rows: List[dict] = []
        for role, ref in vpaths:
            if not ref:
                rows.append({"role": role, "vpath": None, "resolved": False,
                             "source": "", "in_mod": False, "format": None,
                             "reset_reason": "No file."})
                continue
            rows.append({"role": role, **info[ref]})
        return rows

    # -- replacing bound assets ----------------------------------------------

    def replace_asset(self, vpath: str, source_path: str, *,
                      operation: Optional[str] = None) -> Dict[str, str]:
        """Put ``source_path`` into the mod at ``vpath``.

        The rules differ by target, and each is a decision rather than a
        default:

        ``.dds``
            Installed **byte for byte**.  There is no DDS writer in this
            project and deliberately so: re-encoding would mean choosing a DXT
            compressor and silently changing mipmap counts and quality on the
            author's behalf.  Bring a ``.dds``; the game's own textures are the
            best template.
        ``.3do``
            Built from a ``.glb`` through the existing importer, or copied
            verbatim from another ``.3do``.
        anything else
            Copied verbatim.
        """
        if not self.mod:
            raise DsoError("open a mod first")

        target_ext = posixpath.splitext(vpath)[1].lower()
        source_ext = os.path.splitext(source_path)[1].lower()

        if target_ext == ".dds" and source_ext != ".dds":
            # Names the format the original is in, because "save it as DDS" is
            # the half of the answer that never goes wrong -- picking the DXT
            # flavour is the half that does.
            current = self.texture_format(vpath)
            like = f" It is {current}." if current else ""
            raise DsoError(
                f"{posixpath.basename(vpath)} is a .dds, and this tool does not "
                f"write DDS files.{like} Save your image as DDS from an image "
                "editor with the same format and mipmaps, and pick that instead "
                "(DXT5 unless the line above says otherwise; never DXT3, which "
                "no stock texture uses).",
                path=source_path,
            )

        if target_ext == ".3do" and source_ext in (".glb", ".gltf"):
            from dsotools.convert import gltf

            data = threedofmt.build(gltf.import_glb(source_path))
            operation = operation or "import-glb"
        else:
            with open(source_path, "rb") as fh:
                data = fh.read()
            operation = operation or "replace-file"

        return self._commit_files({vpath: data}, source=source_path,
                                  operation=operation)

    # -- adding an asset of a known kind ---------------------------------------
    #
    # "Add a file at a path you type" is a plumbing feature, not a modding one.
    # What an author actually wants is "add a texture", "add a model", "add a
    # script" -- each of which knows its own folder, its own acceptable source
    # formats, and, most importantly, **what it needs before it does anything
    # at all**.  That last field is the one that earns this table: three of the
    # four kinds are inert on their own, and an app that writes the file and
    # says nothing has just built a very tidy no-op.
    #
    # The tabs read this rather than each holding its own opinion, which is the
    # same reason the delivery rules live in one function: four copies of
    # "textures go in 3DView/textures/" is four chances to be wrong.

    # ``model`` is deliberately absent.  A new model needs a scene to name it,
    # and nothing a mod can write reaches a new scene name: ``NWing.Create``
    # takes fixed ``WINGTYPE_*``/``RACE_*`` constants, no ini maps a wing type
    # to a family, and the class-to-family mapping lives in the executable
    # (``specs/scene.md`` 4.3.4).  Replacing the ``.3do`` an existing scene
    # already names is the route that works, and the linked-assets panel has
    # done that all along.
    ADD_KINDS = {
        "texture": {
            "label": "texture",
            "folder": "3DView/textures",
            "extensions": (".dds",),
            "target_ext": ".dds",
            "inert": "A texture does nothing until a scene binds it. Add it, "
                     "then use Shader options on a mesh in the Models tab to "
                     "point a texture slot at it.",
        },
        "script": {
            "label": "script",
            "folder": "scripts",
            "extensions": (".lua",),
            "target_ext": ".lua",
            # Not inert: the loader globs scripts/*.lua and runs each one, so
            # a script takes effect by existing.  Measured in game.
            "inert": None,
        },
        "sound": {
            "label": "sound",
            "folder": "sound",
            "extensions": (".wav", ".mp3"),
            "target_ext": None,
            "inert": "A sound file is inaudible until user_sounds.xml declares "
                     "it, which is what the Audio tab's Add does.",
        },
    }

    def add_kind(self, kind: str) -> dict:
        """What ``kind`` needs.  Raises rather than guessing at an unknown one."""
        try:
            return self.ADD_KINDS[kind]
        except KeyError:
            raise DsoError(f"{kind!r} is not an asset kind this can add") from None

    def suggest_add_path(self, kind: str, source_path: str) -> str:
        """Where a file of this kind belongs, given what it is called.

        A proposal, not a rule -- the author can put it anywhere
        ``check_mod_path`` allows.  The extension is rewritten to the target
        where the kind has one, because a ``.glb`` becomes a ``.3do`` on the
        way in and offering ``objects/ship.glb`` would name a file that never
        exists.
        """
        spec = self.add_kind(kind)
        name = posixpath.basename(source_path.replace("\\", "/"))
        stem, ext = posixpath.splitext(name)
        if spec["target_ext"]:
            ext = spec["target_ext"]
        return f"{spec['folder']}/{stem}{ext}"

    def check_add_source(self, kind: str, source_path: str) -> Optional[str]:
        """``None`` if this file can become an asset of ``kind``, else why not.

        Checked against the kind rather than against the target path, so the
        message can name the formats this kind accepts instead of leaving the
        author to infer them from a refusal.
        """
        spec = self.add_kind(kind)
        if not source_path:
            return f"Choose a {spec['label']} to add."
        if not os.path.isfile(source_path):
            return "That file does not exist."
        ext = os.path.splitext(source_path)[1].lower()
        if ext not in spec["extensions"]:
            allowed = ", ".join(spec["extensions"])
            return (f"A {spec['label']} is added from {allowed}. "
                    f"{os.path.basename(source_path)} is {ext or 'extensionless'}.")
        return None

    def check_new_path(self, vpath: str) -> Optional[str]:
        """``None`` if the mod could hold a **new** file at ``vpath``, else why not.

        Two questions, and they fail differently.  ``check_mod_path`` answers
        "is this somewhere the engine reads at all", which is about the
        delivery rules.  This adds "is it already taken", which is about
        intent: replacing a file the mod has is a different operation with a
        different meaning, and quietly doing it under the name *add* is how an
        author loses work they did not know was there.
        """
        if not self.mod:
            return "Open a mod first."
        why = check_mod_path(vpath)
        if why is not None:
            return why
        key = vfsmod.normalise(vpath).lower()
        files = self.mod.files()
        if key in files or f"loose:{key}" in files:
            return (
                f"This mod already has {vfsmod.normalise(vpath)}. Use Replace "
                "on that file instead — adding it again would overwrite what "
                "is there."
            )
        return None

    def new_path_note(self, vpath: str) -> Optional[str]:
        """What is worth saying about a path that is allowed but unusual.

        Not a refusal.  A file the game has no stock version of is a perfectly
        ordinary thing for a mod to add -- but nothing in the game asks for it
        either, so unless something the mod itself ships names it, it will sit
        there doing nothing.  That is the trap this app exists to surface, and
        it is worth one sentence before the file is written rather than a
        puzzled hour afterwards.
        """
        if not self.stock:
            return None
        if self.stock.find(vpath) is not None:
            return None
        root = vfsmod.normalise(vpath).split("/", 1)[0].lower()
        if root in ("scripts", "sound", "strings", "inifiles"):
            # These are read by name or scanned wholesale; a new file in them
            # is meaningful on its own.
            return None
        return (
            "The game has no file at this path, so nothing asks for it yet. "
            "It will only have an effect once something the mod ships names "
            "it — for a texture, that means binding it in a scene."
        )

    def add_asset(self, vpath: str, source_path: str) -> Dict[str, str]:
        """Put a file into the mod at a path the game does not have.

        The library has always been able to do this -- ``Mod.deploy`` routes by
        the delivery rules and does not care whether the path is new -- and
        nothing in the app ever asked it to, because every route in picks a
        vpath from a list of things that already exist.  This is that missing
        entry point, and it is deliberately thin: the format rules live in
        :meth:`replace_asset` and the path rules in ``project.check_mod_path``,
        so there is one statement of each.
        """
        why = self.check_new_path(vpath)
        if why is not None:
            raise DsoError(why, path=vpath)
        return self.replace_asset(vfsmod.normalise(vpath), source_path,
                                  operation="add-file")

    # -- taking a file back out -----------------------------------------------
    #
    # The counterpart to adding, and it was missing for exactly as long:
    # ``reset_to_stock`` covers a file the *game* also has, and said so --
    # "Delete it outside the app if you want it gone" -- which is a tool
    # admitting it cannot finish what it started.

    def removal_kind(self, vpath: str) -> str:
        """``"reset"`` when stock has this file too, otherwise ``"remove"``.

        The same delete either way; two different things to a reader.  Dropping
        an override means the base game shows through, and dropping an addition
        means the file is simply gone -- so the menu says which, rather than
        offering one word for both.
        """
        if self.stock is not None and self.stock.find(vpath) is not None:
            return "reset"
        return "remove"

    def can_remove_from_mod(self, vpath: str) -> Optional[str]:
        """``None`` if the file can be taken out of the mod, else why not.

        Asked before the menu is built, so the action is disabled with a reason
        rather than offered and then refused -- the same contract
        :meth:`can_reset_to_stock` keeps.

        Only two things are actually refused, and both because removing them
        breaks something the author cannot see: ``inifiles/items.ini``, without
        which the game does not list the mod at all, and a file a sound
        declaration points at, which would leave the declaration naming
        nothing.  The sound case is a redirection rather than a wall -- the
        Audio tab removes the declaration and the file together.
        """
        if not self.mod:
            return "No mod is open."
        key = vfsmod.normalise(vpath).lower()
        if key == VALIDITY_TOKEN.lower():
            return (
                f"{VALIDITY_TOKEN} has to stay: without it the game does not "
                "list this mod at all, and says nothing about why."
            )
        files = self.mod.files()
        if key not in files and f"loose:{key}" not in files:
            return "This mod does not contain that file."
        declaring = self._sound_declaring(key)
        if declaring:
            return (
                f"{declaring} is declared in user_sounds.xml and points at "
                "this file. Remove the sound from the Audio tab instead — it "
                "takes the declaration and the file together, so neither is "
                "left naming the other."
            )
        return None

    def _sound_declaring(self, key: str) -> Optional[str]:
        """The mod sound whose resource is ``key``, if any."""
        db = self._mod_sounds()
        if db is None:
            return None
        for entry in db.entries():
            if not entry.is_mod_relative:
                continue
            if vfsmod.normalise(entry.path()).lower() == key:
                return sounddbfmt.qualified(entry)
        return None

    def removal_notes(self, vpath: str) -> List[str]:
        """What the author should know before this file goes, without blocking.

        Distinct from :meth:`can_remove_from_mod` on purpose.  A refusal is for
        damage the author cannot see and would not want; a note is for a
        consequence they may well intend.  Rolling the two together would mean
        either blocking legitimate work or deleting quietly, and both are worse
        than saying so.
        """
        notes: List[str] = []
        if not self.mod:
            return notes
        v = vfsmod.normalise(vpath)
        key = v.lower()

        if key == vfsmod.normalise(self.STRINGS_PATH).lower():
            notes.append(
                "This is the mod's string table. The texts themselves are kept "
                "in the project file, so they can be written out again — but "
                "every StringId this mod defines stops resolving until they are."
            )

        users = self._mod_scenes_binding(v)
        if users:
            shown = ", ".join(users[:4]) + (" …" if len(users) > 4 else "")
            notes.append(
                f"{len(users)} scene(s) this mod ships bind this file: {shown}. "
                "They will fall back to the stock asset if there is one, and "
                "resolve to nothing if there is not."
            )

        if self.removal_kind(v) == "reset":
            notes.append(
                "The game has its own copy, so removing the mod's override "
                "makes the stock version show through again."
            )
        else:
            notes.append(
                "Nothing in the game has this path, so the file is simply gone."
            )
        return notes

    def _mod_scenes_binding(self, vpath: str) -> List[str]:
        """Scenes **the mod itself ships** that name ``vpath``.

        Deliberately narrow.  The asset index would answer this for the whole
        game, but a stock scene binding a stock texture says nothing about
        whether the mod may drop its own copy -- what matters is whether the
        mod's own content would be left pointing at nothing.

        Both kinds of reference count: a scene names its textures *and* its
        ``.3do`` models the same relative way, and removing either leaves the
        same dangling binding.
        """
        from dsotools.formats import scene as scenefmt

        if not self.mod:
            return []
        want = vfsmod.normalise(vpath).lower()
        out: List[str] = []
        try:
            with self.open_vfs() as vfs:
                for key, mf in sorted(self.mod.files().items()):
                    if key.startswith("loose:") or not key.endswith(".xml"):
                        continue
                    try:
                        data = mf.read()
                        if not scenefmt.is_scene(data):
                            continue
                        sc = scenefmt.parse(data, path=mf.vpath)
                    except (DsoError, OSError):
                        continue
                    if any(self._resolves_to(vfs, ref, mf.vpath, want)
                           for ref in _scene_references(sc)):
                        out.append(mf.vpath)
        except DsoError:
            return []
        return out

    @staticmethod
    def _resolves_to(vfs, reference: str, scene_path: str, want: str) -> bool:
        entry = vfs.resolve_reference(reference, scene_path=scene_path)
        return entry is not None and entry.vpath.lower() == want

    def remove_from_mod(self, vpath: str) -> Dict[str, str]:
        """Take a file out of the mod, wherever it lives.

        Refuses on anything :meth:`can_remove_from_mod` refuses, so the two
        cannot drift -- the same arrangement :meth:`reset_to_stock` has with
        :meth:`can_reset_to_stock`.
        """
        why = self.can_remove_from_mod(vpath)
        if why is not None:
            raise DsoError(why, path=vpath)
        removed = self.mod.remove([vfsmod.normalise(vpath)])
        if self.project is not None:
            self.project.forget(vfsmod.normalise(vpath))
            self.project.save(self.mod.root)
        if removed:
            self.report = None
            self._emit("mod")
        return removed

    @staticmethod
    def _blinker_labels(groups: List[dict]) -> None:
        """Give each group a label that says *which* part's lights it is.

        The name does not: ``PlayerShip`` has **63 groups under 5 distinct
        names**, ``blinks_0`` alone appearing 33 times, because every body,
        wing and booster variant carries its own.  The node path does say --
        ``bodys|body_3|blinks_0`` -- so the label is built from it.

        The top container is dropped (``bodys``, ``wings``, ``boosts``): the
        variant it holds already names it, and ``body_3`` is not ambiguous with
        ``booster_3``.  **Unless dropping it would make two labels the same**,
        in which case those keep the full path -- a shortened label that no
        longer identifies the thing is worse than a long one, and this is the
        same "names are not keys" trap that has bitten three times here.
        """
        def shorten(path: str) -> str:
            parts = [p for p in (path or "").split("|") if p]
            middle = parts[1:-1] if len(parts) > 2 else []
            return " / ".join(middle)

        counts: Dict[str, int] = {}
        for g in groups:
            counts[shorten(g["path"])] = counts.get(shorten(g["path"]), 0) + 1

        for g in groups:
            short = shorten(g["path"])
            where = short if short and counts[short] == 1 else (
                " / ".join(p for p in (g["path"] or "").split("|")[:-1] if p)
            )
            name = g["name"] or "?"
            g["label"] = f"{where} · {name}" if where else name

    def blinker_groups(self, scene_path: str) -> List[dict]:
        """Every ``CBlinkerGroup`` in a scene, as plain data.

        A blinker group has no geometry and no effect -- it is a texture and a
        list of point sprites -- so it is the one thing in a scene the Models
        tab cannot show you through the usual mesh/material path.

        ``texture`` is the scene's own reference and ``texture_vpath`` is that
        reference resolved.  Both, because they are **different types**: the
        reference is relative to this scene, and anything that reads the VFS
        needs the resolved form.  Storing only the first is what once made
        Preview, Export and Open-in-its-tab fail with ``VFS001`` on every
        texture row in the app.
        """
        from dsotools.formats import scene as scenefmt

        with self.open_vfs() as vfs:
            sc = scenefmt.parse(vfs.read(scene_path), path=scene_path)

            def resolved(ref):
                if not ref:
                    return None
                entry = vfs.resolve_reference(ref, scene_path=scene_path)
                return entry.vpath if entry else None

            groups = [
                {
                    "name": g.name,
                    "path": g.path,
                    "texture": g.texture,
                    "texture_vpath": resolved(g.texture),
                    "blinkers": [
                        {
                            "position": b.position,
                            "size": b.size,
                            "vrow": b.vrow,
                            "animtime": b.animtime,
                        }
                        for b in g.blinkers
                    ],
                }
                for g in sc.blinker_groups()
            ]
        self._blinker_labels(groups)
        return groups

    def set_blinkers(self, scene_path: str, node_path: str, blinkers: List[dict],
                     *, texture: Optional[str] = None) -> Dict[str, str]:
        """Replace one group's blinker list, and save the scene into the mod.

        Rewrites in place through the same path as an effect edit, so an
        untouched scene still round-trips byte-for-byte and a change to one
        light touches one line.

        The whole list is passed rather than a diff: add, delete and edit are
        one operation from the table's point of view, and reconciling three
        kinds of change against an index would be a way to get it wrong.
        """
        from dsotools.formats import scene as scenefmt

        if not self.mod:
            raise DsoError("open a mod first")

        with self.open_vfs() as vfs:
            raw = vfs.read(scene_path)
        sc = scenefmt.parse(raw, path=scene_path)

        group = next(
            (g for g in sc.blinker_groups() if g.path == node_path), None
        )
        if group is None:
            raise DsoError(
                f"no blinker group at {node_path} in {scene_path}", path=scene_path
            )
        if texture is not None:
            group.set_texture(texture)

        # Trim or extend to the requested length, then write every row.  Doing
        # it this way keeps the existing elements -- and their indentation --
        # for the rows that survive.
        while len(group) > len(blinkers):
            group.remove(len(group) - 1)
        while len(group) < len(blinkers):
            group.add()
        for row, blinker in zip(blinkers, group.blinkers):
            blinker.set_values(
                row.get("position", (0.0, 0.0, 0.0)),
                float(row.get("size", 0.2)),
                row.get("vrow"),
                row.get("animtime"),
            )

        return self._commit_files(
            {scene_path: sc.to_bytes()}, operation="edit-blinkers"
        )

    def preflight_glb(self, vpath: str, source_path: str) -> dict:
        """What importing ``source_path`` over ``vpath`` would do to `SCN001`.

        The one check worth running **before** the write rather than after.
        `SCN001` is "one ``EffectContainer`` per submesh, across all LODs",
        which holds 9,806/9,806 on stock data -- and a ``.glb`` round-tripped
        through a DCC tool is exactly what breaks it, because merging or
        splitting materials changes the submesh count without touching the
        scene that binds them.  The engine does not complain; it binds the
        wrong material to the wrong surface, or none at all.

        The **structural** rules run here too, over the exact bytes the import
        would write: MDL001-MDL007 fire on nothing Ascaron ships and nothing in
        the user's mods, and a model that has been through a DCC tool is the
        one place they realistically can fire.  Same reasoning as `SCN001` --
        before the write is the only moment where the answer is still cheap.

        Returns ``{"submesh_total", "lods", "scenes", "conflicts", "indexed",
        "problems"}``.  ``conflicts`` is ``[(scene, node, expected, got)]`` --
        empty means every scene that references this model still adds up.
        ``problems`` is ``[(code, severity, message)]``.

        Nothing is written and nothing is decided here: the caller shows this
        and asks.  A check that silently refused would be worse than the drift
        it is guarding against, because a modder deliberately re-cutting a mesh
        also has to update the scene, and that is a legitimate two-step edit.
        """
        from dsotools import validate
        from dsotools.convert import gltf
        from dsotools.formats import scene as scenefmt
        from dsotools.formats import threedo as threedofmt

        model = gltf.import_glb(source_path)
        submesh_total = sum(len(lod.submeshes) for lod in model.lods)

        # The rules see what would land on disk, not the in-memory model: a
        # defect the writer introduces is exactly as bad as one the DCC tool
        # did, and only the bytes can show both.
        problems = [
            (d.code, d.severity, d.message)
            for d in validate.check_model(threedofmt.build(model), vpath)
        ]

        try:
            users = self.used_by(vpath)
        except DsoError:
            # No index yet.  Say so rather than reporting a clean bill of
            # health that was never checked.
            return {
                "submesh_total": submesh_total,
                "lods": len(model.lods),
                "scenes": 0,
                "conflicts": [],
                "indexed": False,
                "problems": problems,
            }

        target = vfsmod.normalise(vpath).lower()
        conflicts = []
        scenes = set()
        with self.open_vfs() as vfs:
            for use in users:
                src = use.get("src") or ""
                if not src.lower().endswith(".xml"):
                    continue
                entry = vfs.find(src)
                if entry is None:
                    continue
                try:
                    sc = scenefmt.parse(entry.read(), path=src)
                except DsoError:
                    continue
                scenes.add(src)
                for mesh in sc.meshes():
                    if not mesh.model:
                        continue
                    resolved = vfs.resolve_reference(mesh.model, scene_path=src)
                    if resolved is None or resolved.vpath.lower() != target:
                        continue
                    got = len(mesh.effects)
                    if got != submesh_total:
                        conflicts.append((src, mesh.name, submesh_total, got))
        return {
            "submesh_total": submesh_total,
            "lods": len(model.lods),
            "scenes": len(scenes),
            "conflicts": conflicts,
            "indexed": True,
            "problems": problems,
        }

    def set_effect(self, scene_path: str, node_path: str, effect_index: int, *,
                   shader: Optional[str] = None,
                   parameters: Optional[Dict[str, Optional[float]]] = None,
                   material: Optional[List[float]] = None,
                   textures: Optional[Dict[int, str]] = None) -> Dict[str, str]:
        """Edit one ``EffectContainer`` in a scene and save the scene.

        Rewrites the XML in place through :mod:`dsotools.formats.scene`, whose
        serialiser preserves the document byte-for-byte apart from what
        changed -- which is what keeps diff-against-stock meaningful.

        ``textures`` is ``{slot: vpath}``, and it is what makes adding a
        texture worth doing: a file at a path the game has never had does
        nothing until something names it, and this is the naming.

        **The vpath is translated, not written.** A scene does not name its
        assets by virtual path -- it writes ``textures/x.dds``, resolved first
        against the scene's own folder and only then against ``3DView/``.
        Writing a vpath straight in produces a reference that resolves to
        nothing, or to a different file that happens to sit at that name.
        :meth:`Vfs.reference_for` works out the spelling and proves it by
        resolving it back; a texture that cannot be named from this scene is
        refused rather than written wrong.
        """
        if not self.mod:
            raise DsoError("open a mod first")

        from dsotools.formats import scene as scenefmt

        with self.open_vfs() as vfs:
            sc = scenefmt.parse(vfs.read(scene_path), path=scene_path)

        target = None
        for mesh in sc.meshes():
            if mesh.path() == node_path:
                target = mesh
                break
        if target is None:
            raise DsoError(f"no mesh node at {node_path}", path=scene_path)
        effects = target.effects
        if not 0 <= effect_index < len(effects):
            raise DsoError(
                f"{node_path} has {len(effects)} effect(s); no index {effect_index}",
                path=scene_path,
            )

        effect = effects[effect_index]
        if shader is not None:
            effect.shader = shader
        if parameters:
            for name, value in parameters.items():
                effect.set_parameter(name, value)
        if material is not None:
            effect.set_material(material)
        if textures:
            self._rebind_textures(effect, textures, scene_path)

        return self._commit_files(
            {scene_path: sc.to_bytes()}, operation="edit-effect"
        )

    def add_submesh(self, scene_path: str, node_path: str) -> Dict[str, str]:
        """Give a mesh one more ``EffectContainer``, and save the scene.

        ``SCN001`` -- one container per submesh across all LODs -- was
        reportable and not fixable until this existed. Bind a mesh to a model
        with more submeshes than the scene has containers and the engine binds
        the wrong material to the wrong surface; the only remedy was to edit
        the XML by hand, which is exactly the workflow this app replaces.

        The new container is a copy of the last one rather than a blank: the
        shader, parameter block and texture-slot count that make sense are the
        neighbouring submesh's, and an empty container draws nothing.
        """
        return self._edit_submeshes(
            scene_path, node_path, lambda mesh: mesh.add_effect(),
            "add-submesh")

    def remove_submesh(self, scene_path: str, node_path: str,
                       index: int) -> Dict[str, str]:
        """Drop one ``EffectContainer`` from a mesh, and save the scene."""
        return self._edit_submeshes(
            scene_path, node_path, lambda mesh: mesh.remove_effect(index),
            "remove-submesh")

    def _edit_submeshes(self, scene_path: str, node_path: str, change,
                        operation: str) -> Dict[str, str]:
        if not self.mod:
            raise DsoError("open a mod first")

        from dsotools.formats import scene as scenefmt

        with self.open_vfs() as vfs:
            sc = scenefmt.parse(vfs.read(scene_path), path=scene_path)
        mesh = next((m for m in sc.meshes() if m.path() == node_path), None)
        if mesh is None:
            raise DsoError(f"no mesh node at {node_path}", path=scene_path)
        try:
            change(mesh)
        except (ParseError, IndexError) as exc:
            raise DsoError(str(exc), path=scene_path) from exc
        return self._commit_files({scene_path: sc.to_bytes()},
                                  operation=operation)

    def set_mesh_model(self, scene_path: str, node_path: str,
                       vpath: str) -> Dict[str, str]:
        """Point one mesh at a different ``.3do`` and save the scene.

        The model half of what :meth:`set_effect`'s ``textures`` does, and it
        exists for the same reason: a model added at a path the game has never
        had is inert until a scene names it, and ``Resrc3DO`` is the naming.

        The same translation applies -- ``Resrc3DO`` is a scene-relative
        reference, not a virtual path -- and the same strictness: a model that
        can only be reached through the bare-path candidate is refused, because
        no stock reference resolves that way.

        **``SCN001`` is checked before the write, not after.** One
        ``EffectContainer`` per submesh is what the engine assumes, and pointing
        a mesh at a model with a different submesh count breaks it silently:
        the wrong material binds to the wrong surface, or none does. The count
        is reported back so the caller can say so; it is not refused, because
        re-cutting a mesh and fixing up the scene is a legitimate two-step edit.
        """
        if not self.mod:
            raise DsoError("open a mod first")

        from dsotools.formats import scene as scenefmt

        with self.open_vfs() as vfs:
            if vfs.find(vpath) is None:
                raise DsoError(
                    f"there is no model at {vpath}; add it to the mod first, "
                    "then bind it", path=vpath)
            reference = vfs.reference_for(vpath, scene_path=scene_path,
                                          strict=True)
            if reference is None:
                raise DsoError(
                    f"{vpath} cannot be named from {scene_path}. A scene "
                    "reaches its own folder and 3DView/, and nothing else, so "
                    "a model outside those cannot be bound to it. Add the "
                    "model under 3DView/ instead.",
                    path=vpath)
            sc = scenefmt.parse(vfs.read(scene_path), path=scene_path)

        target = None
        for mesh in sc.meshes():
            if mesh.path() == node_path:
                target = mesh
                break
        if target is None:
            raise DsoError(f"no mesh node at {node_path}", path=scene_path)

        target.model = reference
        return self._commit_files({scene_path: sc.to_bytes()},
                                  operation="bind-model")

    def bindable_models(self, scene_path: str) -> List[dict]:
        """Every ``.3do`` a scene at this path can actually name, mod's first.

        "Can actually name" is the whole point, and it is narrower than "every
        model in the game": a scene resolves against its own folder and
        ``3DView/``, so a model outside those cannot be bound to it at all.
        Offering one would let the author pick something that writes a
        reference resolving to nothing -- which is precisely the silent failure
        this app exists to prevent, so the list is filtered rather than the
        choice refused afterwards.

        The mod's own models come first: binding is nearly always the second
        half of "I added a model", and burying those among Ascaron's 2,000 is
        how a feature ends up looking broken.
        """
        mod_paths = set()
        if self.mod:
            mod_paths = {k for k in self.mod.files() if not k.startswith("loose:")}

        rows: List[dict] = []
        with self.open_vfs() as vfs:
            for vpath in vfs.iter_paths():
                if not vpath.lower().endswith(".3do"):
                    continue
                if vfs.reference_for(vpath, scene_path=scene_path,
                                     strict=True) is None:
                    continue
                rows.append({"vpath": vpath,
                             "in_mod": vpath.lower() in mod_paths})
        rows.sort(key=lambda r: (not r["in_mod"], r["vpath"].lower()))
        return rows

    def mesh_model_fit(self, scene_path: str, node_path: str,
                       vpath: str) -> dict:
        """Whether binding this model keeps ``SCN001`` true for this mesh.

        ``{"submesh_total", "effects", "fits"}``.  Asked before the bind so the
        dialog can say what will be wrong, rather than the problem list saying
        it afterwards.
        """
        from dsotools.formats import scene as scenefmt

        out = {"submesh_total": None, "effects": None, "fits": None}
        try:
            with self.open_vfs() as vfs:
                sc = scenefmt.parse(vfs.read(scene_path), path=scene_path)
                mesh = next((m for m in sc.meshes() if m.path() == node_path),
                            None)
                if mesh is None:
                    return out
                out["effects"] = len(mesh.effects)
                entry = vfs.find(vpath)
                if entry is None:
                    return out
                head = entry.read()[:0x1000]
        except (DsoError, OSError):
            return out

        # The **root header's** count at 0x30, which is what `SCN001` is
        # defined against and what `validate_mod` reads -- not a full parse.
        # Two reasons: it is a stat and a short read rather than decoding every
        # LOD, and a model whose geometry this build cannot decode still has a
        # perfectly readable count, so refusing to answer would be worse than
        # the answer.
        if head[:4] != b"OD3 " or len(head) < 0x34:
            return out
        out["submesh_total"] = struct.unpack_from("<I", head, 0x30)[0]
        out["fits"] = out["submesh_total"] == out["effects"]
        return out

    def _rebind_textures(self, effect, textures: Dict[int, str],
                         scene_path: str) -> None:
        """Point texture slots at other files, by the name the scene must use."""
        with self.open_vfs() as vfs:
            for slot, vpath in sorted(textures.items()):
                target = vfs.find(vpath)
                if target is None:
                    raise DsoError(
                        f"there is no asset at {vpath}; add it to the mod "
                        "first, then bind it",
                        path=vpath)
                # strict: a scene resolves against its own folder and
                # 3DView/, and every one of the 45,322 resolving references in
                # stock does exactly that.  Writing anything that needs a
                # looser rule would work here and resolve to nothing in game.
                reference = vfs.reference_for(vpath, scene_path=scene_path,
                                              strict=True)
                if reference is None:
                    raise DsoError(
                        f"{vpath} cannot be named from {scene_path}. A scene "
                        "reaches its own folder and 3DView/, and nothing else, "
                        "so a texture outside those cannot be bound to it. "
                        "Add the texture under 3DView/ instead.",
                        path=vpath)
                try:
                    effect.set_texture(slot, reference)
                except IndexError as exc:
                    raise DsoError(str(exc), path=scene_path) from exc

    def _commit_files(self, files: Dict[str, bytes], *, source: Optional[str] = None,
                      operation: str = "edit") -> Dict[str, str]:
        """Write files into the mod, record provenance, invalidate, notify."""
        if not self.mod:
            raise DsoError("open a mod first")

        stock_hashes = {}
        if self.stock is not None:
            for vpath in files:
                entry = self.stock.find(vpath)
                if entry is not None:
                    try:
                        stock_hashes[vpath] = hashlib.sha1(entry.read()).hexdigest()
                    except OSError:
                        pass

        routed = self.mod.deploy(files, project=self.project)
        if self.project is not None:
            for vpath in files:
                self.project.record(
                    vpath, source=source, operation=operation,
                    stock_sha1=stock_hashes.get(vpath),
                )
            if self.stock is not None:
                self.project.record_base_game(self.stock)
            self.project.save(self.mod.root)

        self.report = None
        self._emit("mod")
        return routed

    # -- views ---------------------------------------------------------------

    def mod_tree(self) -> List[dict]:
        """The Project tab's main view: every mod file and what it does.

        Sorted so the rows that matter appear first -- a dead file or an
        override is worth more attention than an addition, and burying them
        alphabetically is how they get missed.
        """
        if not self.mod:
            return []
        files = self.mod.classify(self.stock) if self.stock else self.mod.files()
        order = {
            FileState.DEAD: 0,
            FileState.IDENTICAL: 1,
            FileState.OVERRIDE: 2,
            FileState.ADDITION: 3,
            None: 4,
        }
        rows = [
            {
                "vpath": f.vpath,
                "source": f.source,
                "state": f.state or "unknown",
                "size": f.size,
                "stock_origin": f.stock_origin,
                "target": Mod.deploy_target(f.vpath),
                # `items.ini` is the one file whose presence is load-bearing
                # regardless of its contents: without it the game does not
                # list the mod at all.  Flagged here so the view can say that
                # instead of calling it dead weight -- which is the advice
                # PRJ002 used to give, and it broke mods.
                "required": f.vpath.lower() == VALIDITY_TOKEN.lower(),
            }
            for key, f in files.items()
            if not key.startswith("loose:")
        ]
        rows.sort(key=lambda r: (order.get(r["state"], 4), r["vpath"].lower()))
        return rows

    def can_reset_to_stock(self, vpath: str, *, mod_files=None) -> Optional[str]:
        """``None`` if the file can be reset, else why it cannot.

        Asked before the menu is built, so the action is disabled with a reason
        rather than offered and then refused.

        ``mod_files`` lets a caller asking about many assets hoist the mod's
        file list out of the loop -- ``Mod.files()`` walks the folder and opens
        the archive, and a panel listing a scene's hundred bindings would
        otherwise do that a hundred times.
        """
        if not self.mod:
            return "No mod is open."
        if not self.stock:
            return "No game folder is open, so there is nothing to compare against."
        key = vfsmod.normalise(vpath).lower()
        if key == VALIDITY_TOKEN.lower():
            return (
                f"{VALIDITY_TOKEN} has to stay: without it the game does not "
                "list this mod at all, and says nothing about why."
            )
        if key not in (self.mod.files() if mod_files is None else mod_files):
            return "This mod does not contain that file."
        # `self.stock` is the base game alone -- deliberately not `open_vfs()`,
        # which mounts the mod on top and would find the mod's own copy.
        if self.stock.find(vpath) is None:
            return (
                "There is no stock version of this file — it is content this "
                "mod adds, so there is nothing to reset to. Delete it outside "
                "the app if you want it gone."
            )
        return None

    def reset_to_stock(self, vpath: str) -> Dict[str, str]:
        """Drop the mod's copy of ``vpath`` so the engine reads stock again.

        **Removes rather than overwrites.**  Writing the stock bytes back would
        leave a file that is byte-identical to stock -- dead weight the app then
        reports as having no effect (``PRJ002``).  Removing it is what "reset"
        actually means here: the override goes away and the base game shows
        through.

        Refuses on anything :meth:`can_reset_to_stock` refuses, so the two
        cannot drift.
        """
        why = self.can_reset_to_stock(vpath)
        if why is not None:
            raise DsoError(why, path=vpath)
        removed = self.mod.remove([vpath])
        if self.project is not None:
            self.project.forget(vpath)
            self.project.save(self.mod.root)
        if removed:
            # Same channel a save uses: the diff tree, the problem list and the
            # decoded-preview cache are all stale now, and the cache above all
            # -- it would otherwise show the picture that was just removed.
            self._emit("mod")
        return removed

    def mod_summary(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for row in self.mod_tree():
            counts[row["state"]] = counts.get(row["state"], 0) + 1
        return counts

    def status_line(self) -> str:
        """What the status bar says when nothing is running.

        Lives here rather than in the window because it is a question about
        state, not about widgets -- and because putting it here means it is
        covered by tests instead of by clicking.
        """
        if not self.stock and not self.mod:
            return "Open a game folder to begin."
        bits = []
        if self.stock:
            bits.append(self.game_summary)
        if self.mod:
            total = sum(self.mod_summary().values())
            name = self.mod.display_name or self.mod.name
            bits.append(f"{name}: {total} file(s)")
            if self.report is None:
                bits.append("not validated")
            else:
                counts = ", ".join(f"{v} {k}" for k, v in self.report.counts().items())
                bits.append(counts or "no findings")
        return "  —  ".join(bits)

    #: Machine-readable base-game states, and how to say each one to a human.
    #:
    #: The wording is the point.  This line used to read "base game: not
    #: recorded", which states an internal fact about a sidecar file the user
    #: has never heard of and leaves them to guess whether something is wrong.
    #: Nothing *is* wrong -- it is the normal state for any mod not created in
    #: this app -- so the text has to say so.
    BASE_GAME_TEXT = {
        "unknown": (
            "not checked",
            "Open both a game folder and a mod to compare them.",
        ),
        "unrecorded": (
            "not recorded — nothing to compare against",
            "This mod has no .dsoproj sidecar naming the game build it was "
            "authored against, which is normal for a mod that was not created "
            "in this app. It is not a problem: it only means the app cannot "
            "warn you if the mod was built against a different version of the "
            "game than the one you have open. Saving a change to the mod here "
            "records it from then on.",
        ),
        "matches": (
            "matches this installation",
            "The mod was authored against a game with the same archives you "
            "have open, so paths and stock comparisons are trustworthy.",
        ),
        "differs": (
            "authored against a DIFFERENT installation",
            "The archives this mod was built against are not the ones you have "
            "open — a different patch level, a different edition, or extra "
            "archives. Diffs against stock and unresolved-reference warnings "
            "may be misleading.",
        ),
    }

    def base_game_state(self) -> str:
        """Which of :attr:`BASE_GAME_TEXT` applies.  The testable half."""
        if not (self.project and self.stock):
            return "unknown"
        match = self.project.base_game_matches(self.stock)
        if match is None:
            return "unrecorded"
        return "matches" if match else "differs"

    def base_game_status(self) -> str:
        """A phrase a user can act on, for the Sources line."""
        return self.BASE_GAME_TEXT[self.base_game_state()][0]

    def base_game_explanation(self) -> str:
        """The longer form, for a tooltip."""
        return self.BASE_GAME_TEXT[self.base_game_state()][1]

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Session game={self.game_path!r} mod={self.mod.name if self.mod else None!r}>"


__all__ = ["Session", "APP_NAME"]


def _resting(element: dict) -> dict:
    """The drawable an element shows when nothing is happening to it."""
    drawables = element["drawables"]
    index = element.get("resting", 0)
    return drawables[index] if index < len(drawables) else drawables[0]


def _sha256_of(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _root_result(result) -> dict:
    """A rootfiles Result as plain data, which is what the tabs consume."""
    return {
        "written": list(result.written),
        "removed": list(result.removed),
        "restored": list(result.restored),
        "backed_up": list(result.backed_up),
        "skipped": dict(result.skipped),
        "summary": result.summary(),
        "clean": result.clean,
    }


def _scene_references(sc) -> Iterator[str]:
    """Every asset a scene names: each mesh's model, then its textures.

    Both resolve by the same relative rules, so anything asking "would
    removing this file break one of the mod's own scenes" has to look at both.
    """
    for mesh in sc.meshes():
        if mesh.model:
            yield mesh.model
        for effect in mesh.effects:
            yield from effect.textures
