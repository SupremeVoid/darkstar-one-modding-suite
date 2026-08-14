"""
What changes when the app is a packaged executable instead of a script.

Three things, and all three fail *silently* -- which is why they get a module of
their own rather than a few conditionals scattered about.  Every one of them was
found by reading the packaging configuration against the code, not by running
it; none of them can happen while you develop, because a developer always has a
console and always writes to a folder they own.

1. **There is no console, so the standard streams are ``None``.**
   PyInstaller's windowed mode gives a process with no stdout and no stderr, and
   in Python those become ``None`` rather than a sink.  ``print(x, file=sys.stderr)``
   then raises ``AttributeError: 'NoneType' object has no attribute 'write'``.
   The code that does this in this app is the *PySide6 failed to load* branch --
   the one path whose entire job is to explain a failure to the user.  Packaged,
   it would have raised inside the error handler and exited with no window and
   no message at all.

2. **The folder next to the executable is usually not writable.**
   ``C:\\Program Files\\...`` is not, for an unelevated process.  The crash
   reporter wrote there, caught ``OSError``, and returned an empty path -- so a
   crash in an installed build produced no report and told the user nothing.

3. **Nothing has anywhere to complain to.**
   Before ``QApplication`` exists there is no message box, and after a Qt import
   failure there never will be.  So the last resort is a file, and the user has
   to be told which one.

Nothing here imports Qt.
"""

from __future__ import annotations

import os
import sys
import tempfile
from typing import Optional

#: Where reports go when the executable's own folder is read-only.  Windows
#: gives every user this; the others are conventional enough.
_APP_DIR_NAME = "Darkstar One Modding Suite"


class _NullStream:
    """A stand-in for a stream that does not exist.

    Deliberately not ``io.StringIO``: nothing will ever read it, and a buffer
    that grows for the life of the process to hold text nobody sees is a leak
    with extra steps.
    """

    encoding = "utf-8"
    errors = "replace"

    def write(self, text: str) -> int:
        return len(text)

    def writelines(self, lines) -> None:
        pass

    def flush(self) -> None:
        pass

    def isatty(self) -> bool:
        return False

    def fileno(self) -> int:
        raise OSError("no file descriptor: this process has no console")

    def close(self) -> None:
        pass


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def ensure_streams() -> bool:
    """Guarantee ``sys.stdout`` and ``sys.stderr`` can be written to.

    Returns True if anything had to be replaced -- i.e. "this process has no
    console".  Call it before anything else, including argument parsing:
    ``argparse`` writes usage to stderr and would take the process down with it.
    """
    replaced = False
    for name in ("stdout", "stderr"):
        if getattr(sys, name, None) is None:
            setattr(sys, name, _NullStream())
            replaced = True
    return replaced


def has_console() -> bool:
    """True when there is a real console to print to."""
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is not None and not isinstance(stream, _NullStream):
            return True
    return False


def _writable(directory: str) -> bool:
    """Can this process actually create a file here?

    Asked by trying, not by ``os.access``: on Windows ``os.access`` answers from
    the read-only attribute and cheerfully reports a directory writable that
    ACLs then refuse.
    """
    try:
        os.makedirs(directory, exist_ok=True)
        fd, probe = tempfile.mkstemp(dir=directory, prefix=".write-probe-")
    except OSError:
        return False
    os.close(fd)
    try:
        os.unlink(probe)
    except OSError:
        pass
    return True


def resource(*parts: str) -> str:
    """A data file that ships inside the package, frozen or not.

    ``dso_app/resources/`` travels beside the module in both layouts -- as a
    folder in the checkout, and as one PyInstaller copies into ``_internal`` --
    so the module's own directory locates it either way.  Callers must still
    cope with the file being absent: a missing icon is not worth a crash.
    """
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "resources", *parts)


def install_dir() -> str:
    """The folder the application is running out of.

    Next to the executable when frozen, the working directory otherwise.  It is
    the first place :func:`report_dir` and the settings file look -- a file the
    user finds without being told beats a tidy one they never see -- and it is
    also the one that is **not writable** in an installed build, which is why
    every caller has a fallback.
    """
    if is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.getcwd()


def user_data_dir() -> str:
    """Per-user folder for reports and settings.  Never inside the install."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    elif sys.platform == "darwin":
        base = os.path.join(os.path.expanduser("~"), "Library", "Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME") or os.path.join(
            os.path.expanduser("~"), ".local", "share"
        )
    return os.path.join(base, _APP_DIR_NAME)


def report_dir() -> str:
    """Somewhere a report can actually be written.

    Order: next to the executable when that works, because a report the user
    finds without being told is worth more than a tidy one; then the per-user
    data folder; then the temp directory, which always works and is better than
    losing the report.
    """
    candidates = [install_dir(), user_data_dir()]
    candidates.append(os.path.join(tempfile.gettempdir(), _APP_DIR_NAME))

    for directory in candidates:
        if directory and _writable(directory):
            return directory
    return tempfile.gettempdir()


def fatal(title: str, message: str, *, detail: str = "") -> Optional[str]:
    """Report a failure that happens before -- or instead of -- a main window.

    Tries, in order: a Qt message box, the console, and a file.  It does not stop
    at the first success: a user who saw the dialog still benefits from the file
    when they come to report it, and a user with a console still wants the
    dialog.  Returns the path of the file written, or ``None``.

    Every step is wrapped, because this runs when things are already wrong and
    an exception raised *here* is the one that leaves no trace at all.
    """
    body = message if not detail else f"{message}\n\n{detail}"

    try:
        from PySide6.QtWidgets import QApplication, QMessageBox

        app = QApplication.instance()
        if app is None and is_frozen():
            # Only a packaged build gets a QApplication created for it.  Doing
            # this unconditionally would pop a modal dialog in the middle of a
            # test run on any machine that has Qt installed, and block it.
            app = QApplication([])
        if app is not None:
            QMessageBox.critical(None, title, body)
    except Exception:  # noqa: BLE001 - Qt is exactly what may be broken here
        pass

    if has_console():
        try:
            print(f"{title}: {body}", file=sys.stderr)
        except Exception:  # noqa: BLE001
            pass

    try:
        path = os.path.join(report_dir(), "dso-startup-error.txt")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(f"{title}\n\n{body}\n")
        return path
    except OSError:
        return None


__all__ = [
    "ensure_streams",
    "fatal",
    "has_console",
    "is_frozen",
    "install_dir",
    "report_dir",
    "user_data_dir",
]
