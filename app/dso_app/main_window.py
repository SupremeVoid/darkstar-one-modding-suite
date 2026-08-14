"""
The application shell: tabs, status, and the plumbing they share.

Deliberately thin.  Everything worth testing lives in :mod:`dso_app.session`
(no Qt) or in ``dsotools`` (no Qt, no CLI); this module wires widgets to it.
If logic starts accumulating here, it belongs one layer down.

Tabs are **capability-gated** rather than hidden: an area whose format is only
partly understood still appears, and says so.  Hiding it would misrepresent what
the tool can do, and the point of this project is that failures are visible.
"""

from __future__ import annotations

import os
from typing import Optional

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QStatusBar,
    QTabWidget,
    QWidget,
)

from dsotools.errors import DsoError

from . import diagnostics, workers
from .session import APP_NAME, Session
from .tabs.models_tab import ModelsTab
from .tabs.problems_tab import ProblemsTab
from .tabs.project_tab import ProjectTab
from . import theme
from .tabs.audio_tab import AudioTab
from .tabs.data_tab import DataTab
from .tabs.interface_tab import InterfaceTab
from .tabs.scripting_tab import ScriptingTab
from .tabs.textures_tab import TexturesTab


class SessionBridge(QObject):
    """Marshal Session notifications onto the GUI thread.

    ``Session`` calls its subscribers synchronously, and validation and indexing
    run on a worker.  Without this, a worker thread ends up calling
    ``ProjectTab.refresh()`` directly -- touching widgets from a non-GUI thread,
    which Qt reports as ``QBasicTimer::start: Timers cannot be started from
    another thread`` and then, reliably enough, crashes.

    A signal emitted across threads is delivered by a queued connection, i.e. on
    the receiver's thread.  So the session stays Qt-free and the widgets stay on
    the thread that owns them.
    """

    changed = Signal(str)


class MainWindow(QMainWindow):
    def __init__(self, session: Optional[Session] = None) -> None:
        super().__init__()
        self.session = session or Session()
        self.setWindowTitle(APP_NAME)
        self.resize(1180, 760)

        # Applied to the window rather than the QApplication so that every
        # entry point gets it -- the packaged app, `python -m dso_app`, and the
        # tools that build a MainWindow directly to drive it.
        self.setStyleSheet(theme.stylesheet(self.palette()))

        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        self.project_tab = ProjectTab(self)
        self.problems_tab = ProblemsTab(self)
        self.tabs.addTab(self.project_tab, "Project")
        self.models_tab = ModelsTab(self)
        self.tabs.addTab(self.models_tab, "Models")
        self.textures_tab = TexturesTab(self)
        self.tabs.addTab(self.textures_tab, "Textures")
        self.data_tab = DataTab(self)
        self.tabs.addTab(self.data_tab, "Data")
        self.interface_tab = InterfaceTab(self)
        self.scripting_tab = ScriptingTab(self)
        self.tabs.addTab(self.interface_tab, "Interface")
        self.audio_tab = AudioTab(self)
        self.tabs.addTab(self.audio_tab, "Audio")
        self.tabs.addTab(self.scripting_tab, "Scripting")
        self.tabs.addTab(self.problems_tab, "Problems")
        self.tabs.addTab(self._make_log_tab(), "Log")

        self._make_menu()

        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status_label = QLabel("Open a game folder to begin.")
        #: Bumped whenever the game or mod changes.  A result carrying an old
        #: generation is discarded: a validation of the mod you just switched
        #: away from must not overwrite the status line for the new one.
        self._generation = 0
        self.progress = QProgressBar()
        self.progress.setMaximumWidth(220)
        self.progress.hide()
        self.status.addWidget(self.status_label, 1)
        self.status.addPermanentWidget(self.progress)

        # Never connect the session straight to a widget: see SessionBridge.
        self.bridge = SessionBridge()
        self.bridge.changed.connect(self._on_session_changed, Qt.ConnectionType.QueuedConnection)
        self.session.subscribe(self.bridge.changed.emit)

        self.log_bridge = SessionBridge()
        self.log_bridge.changed.connect(self._append_log, Qt.ConnectionType.QueuedConnection)
        diagnostics.subscribe(self.log_bridge.changed.emit)
        QTimer.singleShot(0, self._restore_hint)

    # -- construction --------------------------------------------------------

    def _make_log_tab(self) -> QWidget:
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(5000)
        self.log_view.setPlainText("\n".join(diagnostics.history()))
        return self.log_view

    def _make_menu(self) -> None:
        bar = self.menuBar()
        file_menu = bar.addMenu("&File")

        act_game = QAction("Open &game data…", self)
        act_game.setShortcut(QKeySequence("Ctrl+G"))
        act_game.triggered.connect(self.choose_game)
        file_menu.addAction(act_game)

        act_mod = QAction("Open &mod…", self)
        act_mod.setShortcut(QKeySequence.StandardKey.Open)
        act_mod.triggered.connect(self.choose_mod)
        file_menu.addAction(act_mod)

        file_menu.addSeparator()
        act_quit = QAction("&Quit", self)
        act_quit.setShortcut(QKeySequence.StandardKey.Quit)
        act_quit.triggered.connect(self.close)
        file_menu.addAction(act_quit)

        tools = bar.addMenu("&Tools")
        act_validate = QAction("&Validate mod", self)
        act_validate.setShortcut(QKeySequence("Ctrl+R"))
        act_validate.triggered.connect(self.run_validation)
        tools.addAction(act_validate)

        act_index = QAction("Rebuild asset &index", self)
        act_index.triggered.connect(self.run_index)
        tools.addAction(act_index)

        tools.addSeparator()
        act_deploy = QAction("&Deploy mod…", self)
        act_deploy.setShortcut(QKeySequence("Ctrl+D"))
        act_deploy.triggered.connect(self.run_deploy)
        tools.addAction(act_deploy)

        helpm = bar.addMenu("&Help")
        act_docs = QAction("&Documentation", self)
        act_docs.setShortcut("F1")
        act_docs.setToolTip(
            "The modding guide and every format reference this build ships")
        act_docs.triggered.connect(self.open_documentation)
        helpm.addAction(act_docs)
        helpm.addSeparator()
        act_bug = QAction("&Report a bug…", self)
        act_bug.setToolTip(
            "Open a prefilled issue on GitHub. Nothing is sent until you "
            "submit it yourself.")
        act_bug.triggered.connect(self.report_a_bug)
        helpm.addAction(act_bug)
        helpm.addSeparator()
        act_about = QAction("&About", self)
        act_about.triggered.connect(self._about)
        helpm.addAction(act_about)

    # -- actions -------------------------------------------------------------

    def report_a_bug(self) -> None:
        """Open GitHub's new-issue form, prefilled with what is worth knowing.

        Confirmed first, and the confirmation says what will travel: this opens
        a **public** page, and the one thing a bug reporter should never do by
        accident is paste their folder layout into it. What goes in is the
        build, the platform and *whether* a game and mod are open — never where
        any of it lives.

        Nothing is submitted here. The browser lands on a form the reporter
        reads, edits and sends; a tool that filed issues on someone's behalf
        would be filing them without their having read what it wrote.
        """
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        from dsotools import __version__

        from . import bugreport
        from .frozen import is_frozen

        try:
            from PySide6 import __version__ as qt_version
        except ImportError:                                   # pragma: no cover
            qt_version = None

        _title, text, url = bugreport.compose(
            qt_version=qt_version,
            app_version=__version__,
            game_open=bool(self.session.game_path),
            mod_open=bool(self.session.mod),
            game_kind=self.session.game_kind,
            frozen=is_frozen(),
        )

        if QMessageBox.question(
            self, "Report a bug",
            "This opens a new issue on GitHub in your browser, prefilled "
            "with:<br><br>"
            "<code>" + "<br>".join(
                line.strip("| ").replace(" | ", ": ")
                for line in text.splitlines()
                if line.startswith("| ") and not line.startswith("| |")
                and not line.startswith("|---")
            ) + "</code><br><br>"
            "No paths, file names or anything else about your computer are "
            "included, and <b>nothing is sent</b> until you submit the form "
            "yourself.<br><br>Open it?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Yes,
        ) != QMessageBox.StandardButton.Yes:
            return

        if not QDesktopServices.openUrl(QUrl(url)):
            QMessageBox.information(
                self, "Report a bug",
                "No browser could be opened. The issue tracker is at:<br>"
                f"<a href='{bugreport.REPOSITORY}/issues'>"
                f"{bugreport.REPOSITORY}/issues</a>")

    def open_documentation(self) -> None:
        """Open the docs beside the window rather than over it.

        Non-modal: documentation is what you read *while* doing the thing it
        describes, and a help window you have to close first is a worse place
        for the rules than the repository they came from.
        """
        from .docs_window import open_docs

        open_docs(self)


    def choose_game(self) -> None:
        from dsotools import locate

        start = self.session.game_path or locate.find_game() or ""
        path = QFileDialog.getExistingDirectory(
            self, "Darkstar One installation folder", start
        )
        if path:
            self.open_game_async(path)

    def open_game_async(self, path: str) -> None:
        """Mount the game off the UI thread.

        Measured on a real install: seven layers and 13,948 paths take about
        7.5 seconds to index, most of it walking the loose install directory.
        Doing that synchronously froze the window before it had even painted.
        """
        gen = self._generation
        self.busy(f"Opening {os.path.basename(path.rstrip(os.sep)) or path}…")
        workers.run(
            self.session.open_game,
            path,
            on_error=lambda msg, tb: (
                diagnostics.LOG.warning("open game data failed: %s", msg),
                QMessageBox.warning(self, "Could not open game data", msg),
            ),
            on_done=lambda: self.idle(gen),
        )

    def open_mod_async(self, path: str) -> None:
        gen = self._generation
        self.busy("Opening mod…")
        workers.run(
            self.session.open_mod,
            path,
            on_error=lambda msg, tb: (
                diagnostics.LOG.warning("open mod failed: %s", msg),
                QMessageBox.warning(self, "Could not open mod", msg),
            ),
            on_done=lambda: self.idle(gen),
        )

    def choose_mod(self) -> None:
        start = ""
        from dsotools.project import Mod

        default = Mod.default_customization_dir()
        if default:
            start = default
        path = QFileDialog.getExistingDirectory(self, "Mod folder", start)
        if path:
            self.open_mod_async(path)

    def run_validation(self) -> None:
        if not self.session.mod:
            self._set_status("Open a mod first.")
            return
        name = self.session.mod.display_name or self.session.mod.name
        gen = self._generation
        self.busy(f"Validating {name}…")

        def finished(report):
            if gen != self._generation:
                return                      # the user has moved on
            counts = ", ".join(f"{v} {k}" for k, v in report.counts().items())
            dropped = sum(report.truncated().values())
            extra = f" (+{dropped} not listed)" if dropped else ""
            self._set_status(
                f"{name}: {counts or 'no findings'}{extra}"
                + ("" if report.ok else "  —  not deployable")
            )

        workers.run(
            self.session.validate,
            wants_progress=True,
            on_progress=self._progress,
            on_result=finished,
            on_error=self._worker_error,
            on_done=lambda: self.idle(gen),
        )

    def run_index(self) -> None:
        if not self.session.stock:
            self._set_status("Open a game folder first.")
            return
        gen = self._generation
        self.busy("Indexing…")

        def finished(idx):
            if gen != self._generation:
                return
            st = idx.stats()
            self._set_status(
                f"indexed {st['assets']:,} assets, {st['references']:,} references"
                + (f", {st['unresolved']:,} unresolved" if st["unresolved"] else "")
            )

        workers.run(
            self.session.build_index,
            wants_progress=True,
            on_progress=self._progress,
            on_result=finished,
            on_error=self._worker_error,
            on_done=lambda: self.idle(gen),
        )

    #: Which tab opens which kind of asset.  One table so "jump to this thing"
    #: behaves the same wherever it is invoked from, and adding a tab is one
    #: line rather than a hunt through context menus.
    ASSET_ROUTES = {
        ".dds": "textures",
        ".aim": "textures",
        ".tex": "textures",
        ".xml": "models",
        ".3do": "models",
        ".ini": "data",
        ".screen": "interface",
    }

    def open_asset(self, vpath: str) -> bool:
        """Show ``vpath`` in whichever tab handles it.  ``False`` if none does.

        The point of the Models and Textures tabs sharing a linked-assets panel
        is that you can follow a binding without going hunting: a texture named
        on a submesh opens in the Textures tab, a scene opens in Models.
        """
        import posixpath

        ext = posixpath.splitext(vpath)[1].lower()
        target = self.ASSET_ROUTES.get(ext)
        if target == "textures":
            self.tabs.setCurrentWidget(self.textures_tab)
            self.textures_tab.reveal(vpath)
            return True
        if target == "models":
            self.tabs.setCurrentWidget(self.models_tab)
            self.models_tab.reveal(vpath)
            return True
        if target == "data":
            self.tabs.setCurrentWidget(self.data_tab)
            self.data_tab.reveal(vpath)
            return True
        if target == "interface":
            self.tabs.setCurrentWidget(self.interface_tab)
            self.interface_tab.reveal(vpath)
            return True
        diagnostics.LOG.info("no tab handles %s", vpath)
        return False

    def run_deploy(self) -> None:
        """Plan the deploy off the UI thread, then ask, then do it.

        Three steps rather than one because the middle one is a question.  The
        plan needs a full validation pass -- seconds on a large mod -- so it
        cannot happen on the UI thread; the dialog cannot happen off it.

        The dialog is opened from a zero-delay timer rather than directly in the
        result callback.  A modal dialog spins a nested event loop, and opening
        one while this worker's remaining callbacks are still queued would run
        them *inside* that loop -- ``idle()`` firing behind an open dialog.  It
        happens to be harmless here, and relying on that is how the next person
        gets bitten.
        """
        if not self.session.mod:
            self._set_status("Open a mod first.")
            return
        gen = self._generation
        self.busy("Checking what deploying would change…")

        def ready(gate):
            if gen != self._generation:
                return                      # the user has moved on
            QTimer.singleShot(0, lambda: self._ask_deploy(gate, gen))

        workers.run(
            self.session.deploy_preview,
            wants_progress=True,
            on_progress=self._progress,
            on_result=ready,
            on_error=self._worker_error,
            on_done=lambda: self.idle(gen),
        )

    def _ask_deploy(self, gate, generation: int) -> None:
        from .tabs.project_tab import DeployDialog

        if generation != self._generation:
            return
        self.problems_tab.refresh()         # the gate just revalidated
        dialog = DeployDialog(gate, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            self._set_status(self._context_status())
            return

        forced = dialog.forced()
        if forced:
            diagnostics.LOG.warning(
                "deploying %s over %d unresolved error(s), at the user's request",
                self.session.mod.name, len(gate.blockers),
            )
        self.busy("Deploying…")

        def finished(result):
            diagnostics.LOG.info("deploy: %s", result.summary())
            self._set_status(f"Deployed — {result.summary()}")
            if result.clean:
                return
            parts = []
            if result.not_removed:
                parts.append(
                    "These were copied into user_data.zip but the loose "
                    "original could not be deleted:\n"
                    + "\n".join(f"  {p} — {why}" for p, why in result.not_removed[:10])
                    + "\n\nThe game reads the zip, so it will behave correctly; "
                    "the loose copies are just clutter. Close anything holding "
                    "them and deploy again."
                )
            if result.missing:
                parts.append(
                    "These were gone by the time the deploy ran, so nothing was "
                    "written for them:\n"
                    + "\n".join(f"  {p}" for p in result.missing[:10])
                )
            QMessageBox.warning(
                self, "Deployed, with leftovers", "\n\n".join(parts)
            )

        workers.run(
            self.session.deploy,
            gate,
            force=forced,
            on_result=finished,
            on_error=self._worker_error,
            on_done=lambda: self.idle(),
        )

    # -- helpers -------------------------------------------------------------

    def guard(self, fn, what: str) -> None:
        """Run something that may raise a DsoError and report it properly.

        A typed exception from the library becomes a message box with the path
        it happened in -- not a traceback, and not silence.
        """
        try:
            fn()
        except DsoError as exc:
            diagnostics.LOG.warning("%s failed: %s", what, exc)
            QMessageBox.warning(self, f"Could not {what}", str(exc))
        except Exception as exc:  # noqa: BLE001
            diagnostics.LOG.exception("%s failed", what)
            QMessageBox.critical(self, f"Could not {what}", f"{type(exc).__name__}: {exc}")

    def busy(self, message: str) -> None:
        self._set_status(message)
        self.progress.setRange(0, 0)
        self.progress.show()

    def idle(self, generation: Optional[int] = None) -> None:
        """Finish a task: hide progress, and never leave a stale "…" message.

        If the task's own result already replaced the status line this is a
        no-op.  If it did not -- superseded, cancelled, or nothing to say -- the
        line falls back to describing the current state rather than sitting on
        "Validating…" forever, which is what made a finished run look hung.
        """
        self.progress.hide()
        if generation is not None and generation != self._generation:
            return
        if self.status_label.text().endswith("…"):
            self._set_status(self._context_status())

    def _set_status(self, message: str) -> None:
        self.status_label.setText(message)

    def _context_status(self) -> str:
        return self.session.status_line()

    def _progress(self, done: int, total: int, label: str) -> None:
        """Show real progress, or keep the indeterminate sweep -- never a fake 0%.

        ``setRange(0, 0)`` is Qt's indeterminate mode: a bar that sweeps, saying
        "working, duration unknown".  A *determinate* bar sitting at 0% says
        something quite different -- "started and got nowhere" -- and that is
        what a task reporting ``0/0`` used to produce.  So a total of zero keeps
        the sweep rather than switching to a stalled-looking 0%.
        """
        if total <= 0:
            self.progress.setRange(0, 0)
            return
        self.progress.setRange(0, total)
        self.progress.setValue(done)

    def _worker_error(self, message: str, tb: str) -> None:
        diagnostics.LOG.error("background task failed: %s\n%s", message, tb)
        QMessageBox.critical(self, "Task failed", message)

    def _append_log(self, line: str) -> None:
        self.log_view.appendPlainText(line)

    def _on_session_changed(self, what: str) -> None:
        if what in ("game", "mod"):
            # Anything still running belongs to the previous selection.
            self._generation += 1
            self.progress.hide()
        if what == "game":
            diagnostics.LOG.info("game data: %s", self.session.game_summary)
        elif what == "mod":
            diagnostics.LOG.info("mod: %s", self.session.mod.display_name)
        self.project_tab.refresh()
        self.problems_tab.refresh()
        self.textures_tab.refresh()
        self.models_tab.refresh()
        self.data_tab.refresh()
        self.interface_tab.refresh()
        self.scripting_tab.refresh()
        self.audio_tab.refresh()
        self._update_title()
        if what in ("game", "mod"):
            self._set_status(self._context_status())

    def _update_title(self) -> None:
        bits = [APP_NAME]
        if self.session.mod:
            bits.insert(0, self.session.mod.display_name or self.session.mod.name)
        self.setWindowTitle(" — ".join(bits))

    def _restore_hint(self) -> None:
        diagnostics.LOG.info("%s started", APP_NAME)
        if self.session.stock is not None:
            return
        found = self.session.autodetect_game()
        if found:
            diagnostics.LOG.info("found a game installation at %s", found)
            self.open_game_async(found)
        else:
            self._set_status(
                "No Darkstar One installation found automatically — "
                "use File ▸ Open game data."
            )

    def _about(self) -> None:
        from dsotools import __version__

        QMessageBox.about(
            self,
            f"About {APP_NAME}",
            f"<b>{APP_NAME}</b><br>dsotools {__version__}<br><br>"
            "Modding tools for Darkstar One (Ascaron, 2006).<br>"
            f"Diagnostics are written to:<br><code>{diagnostics.report_dir()}</code>",
        )


__all__ = ["MainWindow"]
