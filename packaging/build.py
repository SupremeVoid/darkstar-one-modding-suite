#!/usr/bin/env python3
"""
Build the desktop application into a distributable folder, and check it.

    python packaging/build.py                # build and verify
    python packaging/build.py --check        # prerequisites only, no build
    python packaging/build.py --debug-console

WHY A SCRIPT RATHER THAN "just run pyinstaller"
-----------------------------------------------
Because a build that produces a file is not the same as a build that works, and
the difference is invisible until a user meets it.  The application's packaged
configuration -- windowed, therefore no stdout and no stderr -- is exactly the
one no developer ever runs.  This script asserts the properties that
configuration is supposed to have:

* the executable is a **GUI** binary, read out of its own PE header, not assumed
  from the flag we passed;
* Qt's platform plugin is actually in the bundle (its absence is the classic
  "this application failed to start because no Qt platform plugin could be
  initialized" that users cannot diagnose);
* the third-party licence notice shipped, which is what makes an MIT
  application linking LGPL Qt legitimate (docs/ARCHITECTURE.md §3).

``--debug-console`` builds a *second*, console-attached executable for
diagnosing a broken build.  It deliberately does not modify the normal one:
switching the real build to a console subsystem to debug it changes the thing
under investigation.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from typing import Optional

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SPEC = os.path.join(HERE, "dso_app.spec")
DIST = os.path.join(ROOT, "dist")
BUILD = os.path.join(ROOT, "build")
APP_FOLDER = "DarkstarOneModdingSuite"

#: Qt cannot start without this, and the failure message names a plugin the
#: user has never heard of.  Checked by name rather than trusted to the hook.
REQUIRED_QT_PLUGINS = {
    "win32": ["platforms/qwindows.dll"],
    "linux": ["platforms/libqxcb.so"],
    "darwin": ["platforms/libqcocoa.dylib"],
}

SUBSYSTEM_GUI = 2
SUBSYSTEM_CONSOLE = 3


# --------------------------------------------------------------------------
# verification helpers
# --------------------------------------------------------------------------


def pe_subsystem(path: str) -> int:
    """Read the Windows subsystem field out of a PE executable.

    2 is GUI, 3 is console.  This is the only honest way to confirm the build
    really is windowed: the spec's ``console=`` flag says what we asked for, and
    this says what we got.  A stray ``DSO_BUILD_CONSOLE`` in the environment, or
    a spec edited and forgotten, is otherwise undetectable until someone
    launches the app and a black window appears behind it.

    The PE parsing itself is ``tools/pyside_doctor.pe_info`` -- the project's one
    PE reader, already used to diagnose Qt DLL failures.  A second copy of this
    offset arithmetic would be the copy that drifts.

    Raises :class:`ValueError` if the file is not a readable PE image.
    """
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    import pyside_doctor

    info = pyside_doctor.pe_info(path)
    if "error" in info:
        raise ValueError(f"{info['error']}: {path}")
    return info["subsystem"]


def executable_name(console: bool = False) -> str:
    name = APP_FOLDER + ("-console" if console else "")
    return name + (".exe" if sys.platform == "win32" else "")


def _plugin_names() -> list:
    key = "win32" if sys.platform == "win32" else (
        "darwin" if sys.platform == "darwin" else "linux"
    )
    return REQUIRED_QT_PLUGINS[key]


# --------------------------------------------------------------------------
# checks that need no build
# --------------------------------------------------------------------------


def check_environment() -> list:
    """Everything that would make the build fail or produce a broken result."""
    problems = []

    # Matches `requires-python` in pyproject.toml.  These drifted once
    # already: the floor there moved to 3.11 and this stayed at 3.9, so the
    # build would have accepted an interpreter the project does not support.
    if sys.version_info < (3, 11):
        problems.append(
            f"Python 3.11+ is required to build; this is {sys.version.split()[0]}")

    try:
        import PySide6  # noqa: F401
    except ImportError as exc:
        problems.append(f"PySide6 is not importable: {exc}")

    try:
        import PyInstaller
    except ImportError:
        problems.append("PyInstaller is not installed:  pip install pyinstaller")
    else:
        # The spec uses the PyInstaller 6 API -- ``PYZ(a.pure)`` without the
        # 5.x ``zipped_data`` argument, and one-folder output under
        # ``_internal``.  On 5.x it fails with a confusing TypeError from
        # inside the spec, which reads like a bug in this project.
        version = getattr(PyInstaller, "__version__", "0")
        try:
            major = int(version.split(".")[0])
        except ValueError:
            major = 0
        if major < 6:
            problems.append(
                f"PyInstaller {version} is too old; the spec needs 6.0 or later "
                "(pip install -U pyinstaller)"
            )

    for name in ("PIL", "numpy"):
        try:
            __import__(name)
        except ImportError:
            problems.append(
                f"{name} is missing; the Textures tab needs it "
                "(pip install -e .[image])"
            )

    if not os.path.exists(SPEC):
        problems.append(f"missing spec file: {SPEC}")
    for required in ("runtime_hook_streams.py", "THIRD_PARTY_LICENSES.md"):
        if not os.path.exists(os.path.join(HERE, required)):
            problems.append(f"missing {required}")

    if sys.platform != "win32":
        problems.append(
            "note: a Windows executable can only be built on Windows -- "
            "PyInstaller does not cross-compile"
        )
    return problems


#: How long the packaged app gets to build its main window and exit.  Generous
#: because a cold start unpacks a 274 MB one-folder build off disk.
SELFTEST_TIMEOUT = 180


def verify_it_starts(folder: str, *, console: bool) -> list:
    """Run the packaged app with ``--selftest`` and see whether it comes up.

    Every other check here reads the build. This one *runs* it, which is the
    only way to catch a missing module: the app imports its tabs at startup, so
    anything PyInstaller failed to bundle raises before the window exists.

    This exists because a build once passed every check and died on launch.
    ``PySide6.QtMultimedia`` was in the spec's exclude list while the Audio tab
    imported it; an exclude beats a static import, so the module was simply
    absent and the app raised ``ModuleNotFoundError`` in front of the user
    while running perfectly from source. Reading the PE header cannot see that,
    and neither can listing the bundle.
    """
    exe = os.path.join(folder, executable_name(console))
    if not os.path.exists(exe):
        return [f"no executable at {exe}"]
    try:
        done = subprocess.run(
            [exe, "--selftest"], capture_output=True, timeout=SELFTEST_TIMEOUT,
            cwd=folder)
    except subprocess.TimeoutExpired:
        return [f"{os.path.basename(exe)} --selftest did not finish within "
                f"{SELFTEST_TIMEOUT}s; the packaged app hangs on startup"]
    except OSError as exc:
        return [f"could not run {os.path.basename(exe)}: {exc}"]

    output = (done.stdout + done.stderr).decode("utf-8", "replace").strip()
    if done.returncode != 0:
        tail = "\n        ".join(output.splitlines()[-6:]) or "(no output)"
        return [f"{os.path.basename(exe)} --selftest exited {done.returncode}; "
                f"the packaged app does not start.\n        {tail}"]
    if "selftest ok" not in output:
        return [f"{os.path.basename(exe)} --selftest said nothing recognisable: "
                f"{output[:200]!r}"]
    return []


def verify_build(folder: str, *, console: bool) -> list:
    """Assert the properties the packaged build is supposed to have."""
    problems = []
    exe = os.path.join(folder, executable_name(console))
    if not os.path.exists(exe):
        return [f"no executable at {exe}"]

    if sys.platform == "win32":
        want = SUBSYSTEM_CONSOLE if console else SUBSYSTEM_GUI
        try:
            got = pe_subsystem(exe)
        except ValueError as exc:
            problems.append(str(exc))
        else:
            if got != want:
                problems.append(
                    f"{os.path.basename(exe)} is subsystem {got}, expected {want} "
                    f"({'console' if want == SUBSYSTEM_CONSOLE else 'GUI'})"
                )

    internal = os.path.join(folder, "_internal")
    base = internal if os.path.isdir(internal) else folder
    for rel in _plugin_names():
        candidates = [
            os.path.join(base, "PySide6", "plugins", *rel.split("/")),
            os.path.join(base, "PySide6", "Qt", "plugins", *rel.split("/")),
            os.path.join(base, "platforms", os.path.basename(rel)),
        ]
        if not any(os.path.exists(c) for c in candidates):
            problems.append(
                f"Qt platform plugin {rel} is not in the bundle; the app will "
                "fail to start with 'no Qt platform plugin could be initialized'"
            )

    if not any(
        os.path.exists(os.path.join(d, "THIRD_PARTY_LICENSES.md"))
        for d in (folder, base)
    ):
        problems.append("THIRD_PARTY_LICENSES.md did not ship; PySide6 is LGPLv3")

    return problems


# --------------------------------------------------------------------------
# building
# --------------------------------------------------------------------------


def write_version_info() -> str:
    """Windows version resource, generated from the library's own version.

    Generated rather than checked in so it cannot drift from ``__version__``.
    """
    sys.path.insert(0, os.path.join(ROOT, "src"))
    from dsotools import __version__

    parts = [int(p) for p in (__version__.split(".") + ["0", "0", "0"])[:4]]
    quad = ", ".join(str(p) for p in parts)
    text = f"""# Generated by packaging/build.py -- do not edit.
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=({quad}), prodvers=({quad}),
    mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('FileDescription', 'Darkstar One Modding Suite'),
      StringStruct('FileVersion', '{__version__}'),
      StringStruct('InternalName', 'DarkstarOneModdingSuite'),
      StringStruct('OriginalFilename', 'DarkstarOneModdingSuite.exe'),
      StringStruct('ProductName', 'Darkstar One Modding Suite'),
      StringStruct('ProductVersion', '{__version__}'),
    ])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""
    path = os.path.join(HERE, "version_info.txt")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    return path


def running_from_dist() -> Optional[int]:
    """PID of a copy of the app running out of ``dist/``, if there is one.

    Building deletes ``dist/`` first, and Windows will not delete a file a
    running process has mapped.  PyInstaller then stops on a bare
    ``PermissionError`` naming some ``.pyd`` deep inside ``_internal`` -- which
    says nothing about the actual cause, and by then the previous build is
    already half-deleted.

    Checked *before* anything is removed so the build can refuse while the old
    one is still intact.  Best effort: no psutil here, so this shells out to
    ``tasklist`` on Windows and simply returns ``None`` anywhere else.
    """
    if sys.platform != "win32":
        return None
    exe = APP_FOLDER + ".exe"
    try:
        proc = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {exe}", "/FO", "CSV", "/NH"],
            capture_output=True, timeout=20,
        )
    except (OSError, subprocess.SubprocessError):      # pragma: no cover
        return None
    # Decoded here, from bytes, and never with ``text=True``.  The console
    # codepage is not the locale codepage: on a German Windows ``tasklist``
    # emits bytes that are not valid cp1252, and ``text=True`` raises
    # ``UnicodeDecodeError`` *inside subprocess's reader thread* -- so
    # ``.stdout`` comes back ``None`` and the traceback names a line that looks
    # unrelated.  Same family as the `read_text()` defect in the offline test
    # runner: on Windows, an implicit encoding is a bug waiting for the right
    # machine.  Everything wanted here is ASCII, so replacing is lossless.
    out = (proc.stdout or b"").decode("utf-8", "replace")
    for line in out.splitlines():
        parts = [p.strip('" ') for p in line.split('","')]
        if len(parts) >= 2 and parts[0].lower() == exe.lower():
            try:
                return int(parts[1])
            except ValueError:                          # pragma: no cover
                return None
    return None


def build(*, console: bool = False, clean: bool = True) -> int:
    env = dict(os.environ)
    env["DSO_BUILD_CONSOLE"] = "1" if console else "0"

    if clean:
        for d in (BUILD, os.path.join(DIST, APP_FOLDER)):
            shutil.rmtree(d, ignore_errors=True)

    if sys.platform == "win32":
        write_version_info()

    cmd = [sys.executable, "-m", "PyInstaller", "--noconfirm", SPEC]
    if clean:
        cmd.insert(3, "--clean")
    print(" ".join(cmd))
    return subprocess.call(cmd, cwd=ROOT, env=env)


NOTE_PREFIX = "note:"


def classify(problems) -> tuple:
    """Split findings into ``(blocking, notes)``.

    A separate function so the distinction can be tested without capturing
    stdout.  It matters: "a Windows build needs Windows" is information, and if
    it were counted as a failure then ``--check`` could never pass anywhere
    else -- which is the same mistake as a validation rule that cannot be
    satisfied.
    """
    blocking = [p for p in problems if not p.startswith(NOTE_PREFIX)]
    notes = [p for p in problems if p.startswith(NOTE_PREFIX)]
    return blocking, notes


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.strip().split("\n")[0])
    ap.add_argument("--check", action="store_true",
                    help="check prerequisites and exit without building")
    ap.add_argument("--debug-console", action="store_true",
                    help="also produce a console-attached build for diagnosis")
    ap.add_argument("--no-clean", action="store_true")
    args = ap.parse_args(argv)

    problems = check_environment()
    blocking, _notes = classify(problems)
    for p in problems:
        print(("  NOTE  " if p.startswith(NOTE_PREFIX) else "  FAIL  ") + p)
    if args.check:
        print(f"\n{len(blocking)} blocking problem(s)")
        return 1 if blocking else 0
    if blocking:
        print("\nrefusing to build; fix the above first")
        return 1

    pid = running_from_dist()
    if pid is not None:
        # Refuse *before* dist/ is touched, so the working build survives.
        print(
            f"\n  FAIL  {APP_FOLDER}.exe is running (PID {pid}).\n"
            "        Building deletes dist/ first, and Windows will not delete "
            "files a\n        running process has open — PyInstaller would "
            "stop partway and leave\n        the previous build broken. Close "
            "the app and run this again."
        )
        return 1

    code = build(console=args.debug_console, clean=not args.no_clean)
    if code != 0:
        print(f"\nPyInstaller failed with exit code {code}")
        return code

    folder = os.path.join(DIST, APP_FOLDER)
    problems = verify_build(folder, console=args.debug_console)
    # Only worth launching a build whose parts are all present; a failure here
    # would just repeat what the checks above already said.
    if not problems:
        print("  ..    starting the packaged app to check it comes up")
        problems = verify_it_starts(folder, console=args.debug_console)
    for p in problems:
        print(f"  FAIL  {p}")
    if problems:
        print(f"\nthe build completed but {len(problems)} check(s) failed")
        return 1

    print(f"\nbuilt, verified and started: {folder}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
