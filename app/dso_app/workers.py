"""
Background work, and the rule that nothing slow happens on the UI thread.

``ds_3dtex`` alone is 1.5 GB and a full index touches ~11,400 files.  A frozen
window during a scan reads as a broken application, so every call into
``dsotools`` that might take more than a frame goes through here.

WHY THE CALLBACKS GO THROUGH A QObject
--------------------------------------
This is subtle and it bit this project twice.

Connecting a signal to a **plain Python callable** gives Qt no receiver to
associate with a thread, so the call runs wherever ``emit()`` was called -- on
the worker.  A callback that then touches a widget is a cross-thread widget
access: Qt prints ``QBasicTimer::start: Timers cannot be started from another
thread`` and behaves unpredictably afterwards.  The visible symptoms were a
status line that never updated on one mod and updated fine on another, which is
exactly what undefined behaviour looks like from the outside.

Connecting to a **bound method of a QObject** is different: Qt knows which
thread that object lives in and, with the default AutoConnection, queues the
call onto it.  So every callback here is routed through :class:`_Callbacks`,
which is created on the calling (UI) thread.  Widgets are then only ever touched
from the thread that owns them, and callers can keep passing ordinary
functions.

WHY THE WORKERS ARE KEPT IN A REGISTRY
--------------------------------------
And this is the sting in the tail of the above.

``QThreadPool`` owns a ``QRunnable`` and, with the default ``autoDelete``,
destroys it the moment ``run()`` returns.  But ``run()`` only *emits*; a queued
connection means the slots have not been called yet -- the events are still
sitting in the UI thread's queue.  Nothing in the application held a reference
to the worker (``run()``'s return value is routinely discarded), so the Python
object was collected, taking ``_bridge`` with it.

**Qt silently discards a queued call whose receiver has been destroyed.**  That
is the correct behaviour -- delivering to a dead object would be worse -- but it
means the callbacks simply never arrive, with no error anywhere.  The visible
symptom was a task that finished correctly and left the UI as if it were still
running: the status stuck on "Validating…", the progress bar still on screen.
It was intermittent, because it is a race between the garbage collector and the
event loop, which is exactly why it survived one round of fixing.

So a worker is registered on start and released only after its ``done``
callback has actually been delivered.  ``done`` is emitted last, and queued
calls between one sender and one receiver keep their order, so by the time the
release runs every other callback has already been made.
"""

from __future__ import annotations

import sys
import traceback
from typing import Any, Callable, Optional, Set

from PySide6.QtCore import QObject, QRunnable, Qt, QThreadPool, Signal, Slot


#: Workers whose callbacks have not yet been delivered.  Only touched from the
#: UI thread -- :func:`run` is called there, and the release runs there too --
#: so it needs no lock.
_LIVE: Set["Worker"] = set()


class WorkerSignals(QObject):
    """Emitted from the worker thread.  Never connect a bare lambda to these."""

    finished = Signal(object)          # the callable's return value
    failed = Signal(str, str)          # message, traceback
    progress = Signal(int, int, str)   # done, total, label
    done = Signal()                    # always, success or failure


class _Callbacks(QObject):
    """Receiver that lives on the UI thread and forwards to plain callables.

    The whole point of this class is its thread affinity: it is constructed by
    :func:`run`, which is called from the UI thread, so Qt queues every slot
    invocation onto that thread.  Without it the callbacks execute on the
    worker.
    """

    def __init__(self, on_result, on_error, on_progress, on_done) -> None:
        super().__init__()
        self._on_result = on_result
        self._on_error = on_error
        self._on_progress = on_progress
        self._on_done = on_done

    @staticmethod
    def _guard(fn, what: str, *args) -> None:
        """Call a UI callback without letting it take the process down.

        PySide6 6.5 and later treat an unhandled Python exception inside a slot
        as fatal: it calls ``qFatal`` and the window vanishes with no dialog and
        nothing on screen.  A packaged build has no console either, so the
        entire diagnosis available to the user is "the app just closes".

        Turning that into a logged error and a crash report costs nothing and is
        the difference between a bug someone can report and one they cannot.
        The task is already over by this point, so swallowing here loses no
        work -- only a callback's own side effect.
        """
        if not fn:
            return
        try:
            fn(*args)
        except Exception:  # noqa: BLE001 - a callback must never kill the app
            from . import diagnostics

            diagnostics.LOG.exception("error in the %s callback", what)
            try:
                diagnostics.write_crash_report(*sys.exc_info())
            except Exception:  # noqa: BLE001 - reporting must not raise either
                pass

    @Slot(object)
    def result(self, value) -> None:  # pragma: no cover - requires Qt
        self._guard(self._on_result, "result", value)

    @Slot(str, str)
    def error(self, message, tb) -> None:  # pragma: no cover
        self._guard(self._on_error, "error", message, tb)

    @Slot(int, int, str)
    def progress(self, done, total, label) -> None:  # pragma: no cover
        self._guard(self._on_progress, "progress", done, total, label)

    @Slot()
    def done(self) -> None:  # pragma: no cover
        self._guard(self._on_done, "done")


class Worker(QRunnable):
    """Run one callable off the UI thread.

    If the callable accepts a ``progress`` keyword it is given one that emits
    :attr:`WorkerSignals.progress`; that is how ``AssetIndex.build`` and
    ``validate_mod`` report without knowing Qt exists.
    """

    def __init__(self, fn: Callable[..., Any], *args, wants_progress: bool = False, **kwargs):
        super().__init__()
        self.signals = WorkerSignals()
        self._fn = fn
        self._args = args
        self._kwargs = dict(kwargs)
        if wants_progress:
            self._kwargs["progress"] = self._report

    def _report(self, done: int, total: int, label: str = "") -> None:
        self.signals.progress.emit(done, total, label)

    def _emit(self, signal, *args) -> bool:
        """Emit unless the receiving end has already been destroyed.

        At shutdown Qt deletes the C++ side of :class:`WorkerSignals` while this
        runnable may still be on the pool, and every ``emit`` then raises
        ``RuntimeError: Signal source has been deleted``.  That is not an error
        worth reporting: the application is exiting and there is nobody left to
        notify.

        It has to be caught around *each* emit, not around the block.  Without
        it, the ``except`` branch below raised while handling the original
        exception and the ``finally`` raised again -- so closing the window
        during the startup scan wrote a three-deep crash report every time,
        which is the same shape as the packaging bugs: the path whose job is
        reporting a failure failing itself.
        """
        try:
            signal.emit(*args)
            return True
        except RuntimeError:
            return False

    @Slot()
    def run(self) -> None:  # pragma: no cover - requires Qt
        try:
            result = self._fn(*self._args, **self._kwargs)
        except Exception as exc:  # noqa: BLE001 - a worker must never kill the app
            self._emit(self.signals.failed, str(exc), traceback.format_exc())
        else:
            self._emit(self.signals.finished, result)
        finally:
            self._emit(self.signals.done)


def run(
    fn: Callable[..., Any],
    *args,
    on_result: Optional[Callable[[Any], None]] = None,
    on_error: Optional[Callable[[str, str], None]] = None,
    on_progress: Optional[Callable[[int, int, str], None]] = None,
    on_done: Optional[Callable[[], None]] = None,
    wants_progress: bool = False,
    **kwargs,
) -> Worker:
    """Queue ``fn`` on the global pool; deliver every callback on this thread.

    Must be called from the UI thread -- that is what fixes the callbacks'
    thread affinity.  See the module docstring for why that matters, and for
    why the worker is registered rather than left to the garbage collector.
    """
    worker = Worker(fn, *args, wants_progress=wants_progress or on_progress is not None, **kwargs)
    # Qt must not destroy the runnable when run() returns: its callbacks have
    # not been delivered at that point.  Lifetime is managed below instead.
    worker.setAutoDelete(False)

    def release() -> None:
        # Runs on the UI thread, after every other callback for this worker.
        try:
            if on_done:
                on_done()
        finally:
            _LIVE.discard(worker)

    bridge = _Callbacks(on_result, on_error, on_progress, release)
    worker._bridge = bridge  # type: ignore[attr-defined]
    _LIVE.add(worker)

    worker.signals.finished.connect(bridge.result, Qt.ConnectionType.QueuedConnection)
    worker.signals.failed.connect(bridge.error, Qt.ConnectionType.QueuedConnection)
    worker.signals.progress.connect(bridge.progress, Qt.ConnectionType.QueuedConnection)
    worker.signals.done.connect(bridge.done, Qt.ConnectionType.QueuedConnection)

    QThreadPool.globalInstance().start(worker)
    return worker


def live_count() -> int:
    """How many workers are still awaiting delivery.  For tests and diagnostics."""
    return len(_LIVE)


__all__ = ["Worker", "WorkerSignals", "run", "live_count"]
