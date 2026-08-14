# Packaging

Builds the desktop application into a distributable folder.

```
python packaging/build.py            # build, then verify what was built
python packaging/build.py --check    # prerequisites only; safe anywhere
```

## What you need

Windows, for a Windows executable — PyInstaller does not cross-compile, and
`--check` says so rather than letting you find out at the end.

```
pip install -e .[image]
pip install pyinstaller        # 6.0 or later; the spec uses the 6.x API
```

`--check` names anything missing. It is the right first command on a machine
that has never built this before.

## What comes out

`dist/DarkstarOneModdingSuite/` — a one-folder build. The executable sits at the
top; Qt, PySide6 and the Python runtime live in `_internal/`.

One folder rather than one file, for three reasons:

- **Licensing.** PySide6 is LGPLv3 and this application is MIT. That works
  because Qt ships as separate dynamically-linked files a user can replace.
  A one-file build unpacks to a temp directory at run time and muddies that.
- **Startup.** One-file re-extracts about 200 MB of Qt on every launch.
- **Crash reports.** They land next to the executable, where the user can find
  them, instead of in a temp folder that is deleted on exit.

## Why the build verifies itself

Producing a file is not the same as producing a working application, and the
packaged configuration — windowed, so no `stdout` and no `stderr` — is the one
configuration no developer ever runs. So `build.py` checks the properties that
configuration is supposed to have:

| Check | Why |
|---|---|
| The executable's PE subsystem is **GUI**, read from its own header | The spec's `console=` flag records what we asked for. This records what we got. |
| Qt's platform plugin is in the bundle | Without it the app dies with *"no Qt platform plugin could be initialized"*, which a user cannot act on. |
| `THIRD_PARTY_LICENSES.md` shipped | It is what makes an MIT application linking LGPL Qt legitimate. |

## Debugging a build

```
python packaging/build.py --debug-console
```

This produces a **second**, console-attached executable. It deliberately leaves
the normal one alone: flipping the real build to a console subsystem to debug it
changes the thing under investigation, and the no-console behaviour is precisely
what tends to be broken.

If Qt itself will not load, `tools/pyside_doctor.py` names the DLL and the
reason. The packaged app runs it automatically and puts the result in a dialog
when there is no console to print it to.

## The no-console rules

`app/dso_app/frozen.py` holds them, and `tests/test_frozen.py` covers them
without building anything — `sys.stdout = None` is exactly what PyInstaller
hands a windowed process.

- The standard streams are `None`, so anything that writes to them raises.
  `argparse` writes usage to `stderr`; the Qt-failure branch printed to it too.
- The folder next to the executable is usually not writable
  (`C:\Program Files\...`), so crash reports fall back to `%LOCALAPPDATA%`.
- Before `QApplication` exists there is nowhere to show a message, so the last
  resort is `dso-startup-error.txt` in the report folder.

`packaging/runtime_hook_streams.py` applies the first of these before any
application code runs, in case an import writes a warning on the way in.

## When the Models tab lands

Qt Quick 3D is excluded from the bundle today (see `QT_UNUSED` in the spec).
Phase 5 will need `QtQuick3D`, `QtQuick` and `QtQml` taken off that list, and
the bundle will grow accordingly.
