"""
The packaging checks, tested without a Windows build machine.

``packaging/build.py`` asserts that the shipped executable is a GUI binary by
reading its PE header rather than by trusting the flag the spec passed.  That
check is the only thing standing between "we set console=False" and "the build
really is windowed", so it gets tested itself -- against synthetic PE images,
which is enough, because the part that can be wrong is the offset arithmetic.

Nothing here runs PyInstaller.  A Windows executable can only be built on
Windows, and pretending otherwise in CI produces a test that skips forever.
"""

from __future__ import annotations

import os
import pathlib
import struct
import sys

import pytest

PACKAGING = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "packaging"
)
if PACKAGING not in sys.path:
    sys.path.insert(0, PACKAGING)

import build as buildmod  # noqa: E402


#: Fixed part of the optional header, before the data directories.
_OPT_FIXED = {0x10B: 96, 0x20B: 112}


def _fake_pe(subsystem: int, *, magic: int = 0x20B, pe_offset: int = 0x80) -> bytes:
    """A PE image with just enough structure to be read, and no sections.

    Only the fields the reader navigates are filled in; everything else is zero.
    That is deliberate -- if it ever starts depending on a field this fixture
    does not set, the test should notice rather than quietly keep passing.

    The optional header is sized properly (rather than truncated at the
    subsystem field) because the shared reader in ``tools/pyside_doctor.py``
    also walks the data directories to find the import table.
    """
    opt_fixed = _OPT_FIXED.get(magic, 112)
    opt = pe_offset + 24
    size = opt + opt_fixed + 16 * 8            # + all sixteen data directories
    buf = bytearray(size)
    buf[0:2] = b"MZ"
    struct.pack_into("<I", buf, 0x3C, pe_offset)
    buf[pe_offset:pe_offset + 4] = b"PE\0\0"
    struct.pack_into("<HH", buf, pe_offset + 4, 0x8664, 0)      # machine, 0 sections
    struct.pack_into("<H", buf, pe_offset + 20, opt_fixed + 16 * 8)   # opt header size
    struct.pack_into("<H", buf, opt, magic)
    struct.pack_into("<H", buf, opt + 68, subsystem)
    return bytes(buf)


def _written(tmp_path, data: bytes, name: str = "x.exe") -> str:
    path = tmp_path / name
    path.write_bytes(data)
    return str(path)


def test_reads_a_gui_subsystem(tmp_path):
    assert buildmod.pe_subsystem(_written(tmp_path, _fake_pe(2))) == 2


def test_reads_a_console_subsystem(tmp_path):
    assert buildmod.pe_subsystem(_written(tmp_path, _fake_pe(3))) == 3


def test_reads_pe32_as_well_as_pe32_plus(tmp_path):
    """Subsystem sits at the same optional-header offset in both forms."""
    assert buildmod.pe_subsystem(_written(tmp_path, _fake_pe(2, magic=0x10B))) == 2


def test_follows_the_pe_offset_rather_than_assuming_one(tmp_path):
    assert buildmod.pe_subsystem(_written(tmp_path, _fake_pe(2, pe_offset=0x108))) == 2


def test_rejects_a_file_that_is_not_an_executable(tmp_path):
    with pytest.raises(ValueError):
        buildmod.pe_subsystem(_written(tmp_path, b"not an exe at all" * 20))


def test_rejects_a_dos_stub_with_no_pe_signature(tmp_path):
    data = bytearray(_fake_pe(2))
    data[0x80:0x84] = b"\0\0\0\0"
    with pytest.raises(ValueError):
        buildmod.pe_subsystem(_written(tmp_path, bytes(data)))


def test_the_reader_is_the_one_the_doctor_uses(tmp_path):
    """One PE reader in the project, not two that can disagree."""
    sys.path.insert(0, os.path.join(os.path.dirname(PACKAGING), "tools"))
    import pyside_doctor

    path = _written(tmp_path, _fake_pe(2))

    assert pyside_doctor.pe_info(path)["subsystem"] == buildmod.pe_subsystem(path)


def test_rejects_an_unknown_optional_header(tmp_path):
    with pytest.raises(ValueError):
        buildmod.pe_subsystem(_written(tmp_path, _fake_pe(2, magic=0x107)))


# -- the environment check --------------------------------------------------


def test_check_environment_reports_rather_than_raises():
    """It must run on a machine that cannot build, and say why.

    This suite's own machine is one of those, which is the point: the check is
    what tells a contributor what they are missing, so it has to work before
    anything is installed.
    """
    problems = buildmod.check_environment()

    assert isinstance(problems, list)
    assert all(isinstance(p, str) for p in problems)


def test_check_environment_notes_that_windows_builds_need_windows(monkeypatch):
    # Import the optional dependencies *before* faking the platform.  Faking
    # sys.platform is global, and check_environment() imports PyInstaller --
    # whose compat module reads sys.platform at import time and, believing it
    # is on Linux, shells out to `ldd`.  On Windows that raises
    # FileNotFoundError from inside a third-party import and the test fails for
    # a reason that has nothing to do with what it is checking.  Priming
    # sys.modules makes the inner import a dict lookup instead.
    for name in ("PyInstaller", "PySide6"):
        try:
            __import__(name)
        except ImportError:
            pass                # absent is fine; check_environment reports it

    monkeypatch.setattr(sys, "platform", "linux")

    problems = buildmod.check_environment()

    assert any("only be built on Windows" in p for p in problems)


def test_a_note_does_not_block_the_build(monkeypatch):
    """Notes and failures are different things and must not be conflated.

    If the cross-compilation note counted as a failure, ``--check`` could never
    pass on any machine that is not the build machine -- and a check that can
    never pass gets ignored, taking the real findings with it.
    """
    blocking, notes = buildmod.classify(["note: just so you know"])
    assert blocking == [] and len(notes) == 1

    monkeypatch.setattr(buildmod, "check_environment", lambda: ["note: just so you know"])
    assert buildmod.main(["--check"]) == 0


def test_a_real_problem_blocks_the_build(monkeypatch):
    blocking, notes = buildmod.classify(["PySide6 is not importable"])
    assert len(blocking) == 1 and notes == []

    monkeypatch.setattr(buildmod, "check_environment", lambda: ["PySide6 is not importable"])
    assert buildmod.main(["--check"]) == 1


def test_a_running_copy_blocks_the_build_before_anything_is_deleted(monkeypatch):
    """Met for real: the app was open while a rebuild was started.

    ``--clean`` removes ``dist/`` first and Windows will not delete files a
    running process has mapped, so PyInstaller stopped on a bare
    ``PermissionError`` naming a ``.pyd`` inside ``_internal`` -- by which time
    the previous, working build was already half-deleted.  Refusing up front is
    what keeps the old build intact, so the check has to run *before* `build`.
    """
    called = []
    monkeypatch.setattr(buildmod, "check_environment", lambda: [])
    monkeypatch.setattr(buildmod, "running_from_dist", lambda: 4321)
    monkeypatch.setattr(buildmod, "build", lambda **kw: called.append(kw) or 0)

    assert buildmod.main([]) == 1
    assert called == [], "must refuse before deleting or building anything"


def test_no_running_copy_lets_the_build_proceed(monkeypatch):
    called = []
    monkeypatch.setattr(buildmod, "check_environment", lambda: [])
    monkeypatch.setattr(buildmod, "running_from_dist", lambda: None)
    monkeypatch.setattr(buildmod, "build", lambda **kw: called.append(kw) or 7)

    # 7 is PyInstaller's exit code here: the point is that build() was reached.
    assert buildmod.main([]) == 7
    assert len(called) == 1


def test_running_from_dist_survives_console_bytes_that_are_not_locale_text(monkeypatch):
    """Met for real on a German Windows.

    ``tasklist``'s output is in the *console* codepage, not the locale one.
    With ``text=True`` the decode raises inside subprocess's reader thread,
    ``.stdout`` comes back ``None``, and the traceback points somewhere that
    looks unrelated.  Same family as the ``read_text()`` defect in the offline
    test runner: on Windows an implicit encoding is a bug awaiting the right
    machine.
    """
    if sys.platform != "win32":
        pytest.skip("tasklist is Windows-only")

    class _Proc:
        # 0x81 is not valid cp1252; a real run produced exactly this.
        stdout = b'"DarkstarOneModdingSuite.exe","20452","Console","1","211.\x81340 K"\r\n'

    monkeypatch.setattr(buildmod.subprocess, "run", lambda *a, **kw: _Proc())
    assert buildmod.running_from_dist() == 20452


def test_running_from_dist_is_none_when_nothing_matches(monkeypatch):
    if sys.platform != "win32":
        pytest.skip("tasklist is Windows-only")

    class _Proc:
        stdout = b"INFO: No tasks are running which match the specified criteria.\r\n"

    monkeypatch.setattr(buildmod.subprocess, "run", lambda *a, **kw: _Proc())
    assert buildmod.running_from_dist() is None


def test_running_from_dist_tolerates_empty_output(monkeypatch):
    if sys.platform != "win32":
        pytest.skip("tasklist is Windows-only")

    class _Proc:
        stdout = None            # what the decode failure used to produce

    monkeypatch.setattr(buildmod.subprocess, "run", lambda *a, **kw: _Proc())
    assert buildmod.running_from_dist() is None


def test_notes_and_failures_are_separated_in_a_mixed_list():
    blocking, notes = buildmod.classify(
        ["note: a", "PyInstaller is not installed", "note: b"]
    )

    assert blocking == ["PyInstaller is not installed"]
    assert notes == ["note: a", "note: b"]


# -- verification of a finished build ---------------------------------------


def _fake_dist(tmp_path, *, subsystem=2, plugin=True, licence=True, console=False):
    folder = tmp_path / "DarkstarOneModdingSuite"
    internal = folder / "_internal"
    internal.mkdir(parents=True)
    (folder / buildmod.executable_name(console)).write_bytes(_fake_pe(subsystem))
    if plugin:
        plugins = internal / "PySide6" / "plugins" / "platforms"
        plugins.mkdir(parents=True)
        (plugins / "qwindows.dll").write_bytes(b"")
        (plugins / "libqxcb.so").write_bytes(b"")
        (plugins / "libqcocoa.dylib").write_bytes(b"")
    if licence:
        (internal / "THIRD_PARTY_LICENSES.md").write_text("licences")
    return str(folder)


def test_a_good_build_passes_verification(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")

    assert buildmod.verify_build(_fake_dist(tmp_path), console=False) == []


def test_a_console_binary_shipped_by_accident_is_caught(tmp_path, monkeypatch):
    """The regression this check exists for: the windowed build is the untested one."""
    monkeypatch.setattr(sys, "platform", "win32")

    problems = buildmod.verify_build(_fake_dist(tmp_path, subsystem=3), console=False)

    assert any("subsystem 3, expected 2" in p for p in problems)


def test_a_missing_qt_platform_plugin_is_caught(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")

    problems = buildmod.verify_build(_fake_dist(tmp_path, plugin=False), console=False)

    assert any("platform plugin" in p for p in problems)


def test_a_missing_licence_notice_is_caught(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")

    problems = buildmod.verify_build(_fake_dist(tmp_path, licence=False), console=False)

    assert any("LGPL" in p for p in problems)


def test_a_build_that_produced_nothing_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    (tmp_path / "empty").mkdir()

    problems = buildmod.verify_build(str(tmp_path / "empty"), console=False)

    assert len(problems) == 1 and "no executable" in problems[0]


def test_the_spec_is_syntactically_valid_python():
    """A spec file is executed by PyInstaller; a typo there fails the build late."""
    import ast

    spec = os.path.join(PACKAGING, "dso_app.spec")
    ast.parse(open(spec, encoding="utf-8").read(), spec)


def test_the_spec_ships_the_lua_api_database():
    """It is read through importlib.resources, so no import points at it.

    PyInstaller finds data files by following imports; this one it cannot see,
    and a build without it silently loses completion, the reference pane and
    the undocumented-call check.
    """
    spec = open(os.path.join(PACKAGING, "dso_app.spec"), encoding="utf-8").read()

    assert '"dsotools", "data"' in spec, "the API database is not in DATAS"


def test_the_runtime_hook_needs_no_imports_from_this_project(tmp_path):
    """It runs before the app's own packages are importable."""
    import ast

    hook = os.path.join(PACKAGING, "runtime_hook_streams.py")
    tree = ast.parse(open(hook, encoding="utf-8").read(), hook)
    imported = {
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    } | {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }

    assert imported <= {"sys", "os"}


def test_the_runtime_hook_does_what_the_module_does(tmp_path, monkeypatch):
    """Two implementations of one rule, so they are checked against each other."""
    import runpy

    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)

    runpy.run_path(os.path.join(PACKAGING, "runtime_hook_streams.py"))

    assert sys.stdout is not None and sys.stderr is not None
    sys.stdout.write("no exception")
    with pytest.raises(OSError):
        sys.stderr.fileno()


# --------------------------------------------------------------------------
# the multimedia backend
# --------------------------------------------------------------------------
#
# QMediaPlayer resolves its backend at runtime out of plugins/multimedia, so
# nothing that follows imports can find it. The first frozen build with an
# Audio tab shipped without it: the tab looked perfectly healthy and played
# nothing, and only in the packaged app. This is the check that says so.


def _spec_namespace():
    """Evaluate the parts of the spec that do not need PyInstaller running."""
    import os

    import pathlib

    root = pathlib.Path(__file__).resolve().parent.parent
    spec = root / "packaging" / "dso_app.spec"
    source = spec.read_text(encoding="utf-8")
    head = source.split("a = Analysis(")[0]
    namespace = {"os": os, "__file__": str(spec), "SPEC": str(spec),
                 "SPECPATH": str(spec.parent), "DISTPATH": str(root / "dist"),
                 "workpath": str(root / "build")}
    exec(compile(head, str(spec), "exec"), namespace)     # noqa: S102
    return namespace


def test_the_spec_bundles_the_multimedia_plugin_and_its_codecs():
    pytest.importorskip("PySide6")
    pytest.importorskip("PyInstaller")
    namespace = _spec_namespace()
    datas = namespace["MEDIA_DATAS"]
    binaries = namespace["MEDIA_BINARIES"]

    assert datas, "no multimedia plugin directory would be bundled"
    source, target = datas[0]
    assert os.path.isdir(source)
    assert "multimedia" in target
    assert any(n.lower().startswith("ffmpegmediaplugin")
               or n.lower().startswith("windowsmediaplugin")
               for n in os.listdir(source)), os.listdir(source)

    # The ffmpeg plugin is what decodes MP3, and it links these by name.
    names = {os.path.basename(src).lower().split("-")[0] for src, _dst in binaries}
    assert {"avcodec", "avformat", "avutil"} <= names, sorted(names)


def test_the_backend_lookup_survives_pyside_being_absent(monkeypatch):
    """The spec is also read on machines that cannot build, e.g. in CI."""
    pytest.importorskip("PyInstaller")
    namespace = _spec_namespace()
    # Make the import itself fail, which is what a machine without PySide6
    # actually looks like.
    import builtins

    real_import = builtins.__import__

    def fail(name, *args, **kwargs):
        if name == "PySide6":
            raise ImportError("no PySide6 here")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fail)
    assert namespace["_multimedia_backend"]() == ([], [])


# --------------------------------------------------------------------------
# the application icon
# --------------------------------------------------------------------------


def test_the_icon_ships_and_covers_the_sizes_windows_asks_for():
    """Both halves matter, and they are separate mechanisms.

    The icon is *embedded* in the executable, which is what Explorer and the
    taskbar read, and *shipped beside the package*, which is what
    ``QApplication.setWindowIcon`` loads for the windows. Configuring one and
    forgetting the other gives an app whose title bar and taskbar disagree.
    """
    from PIL import Image

    root = pathlib.Path(__file__).resolve().parent.parent
    icon = root / "app" / "dso_app" / "resources" / "icon.ico"
    assert icon.is_file(), "run tools/make_icon.py"

    sizes = {w for w, _h in Image.open(icon).ico.sizes()}
    # 16 is the title bar, 32 the taskbar, 256 Explorer's large view. Missing
    # any of the three means Windows scales one of the others, badly.
    assert {16, 32, 256} <= sizes, sorted(sizes)


def test_the_spec_embeds_the_icon_and_ships_it():
    pytest.importorskip("PyInstaller")
    namespace = _spec_namespace()
    icon = namespace["ICON"]
    assert os.path.isfile(icon), icon
    shipped = [dst for _src, dst in namespace["DATAS"]
               if "resources" in str(dst)]
    assert shipped, "the resources folder is not in DATAS, so the windows " \
                    "would have no icon to load"


def test_the_source_image_is_kept_beside_the_generated_icon():
    """A build product with no source is one nobody can correct."""
    root = pathlib.Path(__file__).resolve().parent.parent
    assert (root / "app" / "dso_app" / "resources" / "icon.png").is_file()
    assert (root / "tools" / "make_icon.py").is_file()
