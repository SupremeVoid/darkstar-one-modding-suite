"""
Entry point.

    python -m dso_app                       # from app/
    python app/main.py --data <extracted>    # with a folder preselected

A packaged build has no console, so failures that would otherwise be a silent
exit are turned into a crash report on disk and a message box (see
``diagnostics``).  If Qt itself will not load, that is diagnosed rather than
traced -- ``tools/pyside_doctor.py`` names the DLL and the reason.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import logging
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "src"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _set_icon(app) -> None:
    """Give every window the app's icon, and Windows a reason to use it.

    Set on the ``QApplication`` rather than on the main window so dialogs and
    message boxes inherit it -- a stray dialog wearing the default Qt icon is
    the usual tell that this was done in the wrong place.

    The AppUserModelID is what makes the taskbar show *this* icon rather than
    Python's when the app is run from source. A frozen build gets the icon out
    of the executable and does not need it, but setting it costs nothing and
    keeps the two ways of starting the app looking the same.
    """
    from PySide6.QtGui import QIcon

    from dso_app import frozen

    path = frozen.resource("icon.ico")
    if os.path.exists(path):
        app.setWindowIcon(QIcon(path))

    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "Ascaron.DarkstarOne.ModdingSuite")
        except Exception:  # noqa: BLE001 - cosmetic; never worth failing over
            pass


def main(argv=None) -> int:
    # Before anything else, and before argparse in particular: a windowed build
    # has no stdout or stderr, and argparse writes usage to stderr.  A mistyped
    # flag would otherwise take the process down inside the error handler.
    from dso_app import frozen

    frozen.ensure_streams()

    ap = argparse.ArgumentParser(prog="dso_app")
    ap.add_argument("--data", help="open this extracted-archives folder at startup")
    ap.add_argument("--mod", help="open this mod at startup")
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--selftest", action="store_true",
                    help="build the main window offscreen, then exit; used by "
                         "packaging/build.py to prove the build actually starts")
    try:
        args = ap.parse_args(argv)
    except SystemExit as exc:
        if exc.code and not frozen.has_console():
            frozen.fatal(
                "Bad command line",
                "This build was started with an argument it does not understand.",
                detail=ap.format_usage(),
            )
        raise

    from dso_app import diagnostics

    diagnostics.install(logging.DEBUG if args.debug else logging.INFO)

    try:
        from PySide6.QtWidgets import QApplication, QMessageBox
    except ImportError as exc:
        # The one path that must never fail quietly: without a console there is
        # nothing else left to tell the user with.
        tools = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tools")
        sys.path.insert(0, os.path.abspath(tools))
        try:
            import pyside_doctor
        except ImportError:
            frozen.fatal(
                "Qt could not be loaded",
                f"PySide6 could not be loaded: {exc}",
                detail="Install it with:  pip install PySide6",
            )
            return 1
        if frozen.has_console():
            return pyside_doctor.main()
        # No console: capture the doctor's own report and put it in a dialog.
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(buffer):
            code = pyside_doctor.main()
        frozen.fatal(
            "Qt could not be loaded",
            f"PySide6 could not be loaded: {exc}",
            detail=buffer.getvalue().strip(),
        )
        return code or 1

    # Every import the app needs happens under here, so a build that is missing
    # a module fails on this line rather than in front of a user. A packaged
    # app that cannot start is the one defect the build must never ship, and
    # reading the PE header does not catch it: excluding PySide6.QtMultimedia
    # while the Audio tab imported it produced a build that passed every check
    # and died on launch.
    if args.selftest:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from dso_app.main_window import MainWindow
    from dso_app.session import APP_NAME, Session

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    _set_icon(app)

    session = Session()
    window = MainWindow(session)

    def notify(summary, path):
        QMessageBox.critical(
            window,
            "Unexpected error",
            f"{summary}\n\nA report was written to:\n{path}" if path else summary,
        )

    diagnostics.install_excepthook(notify)

    if args.selftest:
        # Constructed successfully, which is the whole question. Report the
        # tabs so the check is about *this* app rather than about Qt starting.
        names = [window.tabs.tabText(i) for i in range(window.tabs.count())]
        print("selftest ok: " + ", ".join(names))
        return 0

    window.show()

    if args.data:
        window.open_game_async(args.data)
    if args.mod:
        window.open_mod_async(args.mod)

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
