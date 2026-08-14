"""
Crash reporting and the in-app log.

A packaged application has no console.  Without this, every failure a user meets
is "it didn't work", and the PySide6 DLL episode in this project's own history is
exactly what that costs.

So: an excepthook that writes a report file and shows the user where it is, a
log buffer the Log panel renders, and a link to :mod:`tools.pyside_doctor` for
the one failure class that is about the installation rather than the code.
"""

from __future__ import annotations

import datetime
import io
import logging
import os
import platform
import sys
import traceback
from typing import Callable, List, Optional

LOG = logging.getLogger("dso")

_BUFFER: List[str] = []
_LISTENERS: List[Callable[[str], None]] = []


class BufferHandler(logging.Handler):
    """Keep the last N records so the Log panel can show history from startup."""

    def __init__(self, limit: int = 5000) -> None:
        super().__init__()
        self.limit = limit

    def emit(self, record: logging.LogRecord) -> None:
        line = self.format(record)
        _BUFFER.append(line)
        if len(_BUFFER) > self.limit:
            del _BUFFER[: len(_BUFFER) - self.limit]
        for fn in list(_LISTENERS):
            try:
                fn(line)
            except Exception:  # noqa: BLE001 - logging must never raise
                pass


def install(level: int = logging.INFO) -> None:
    handler = BufferHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-7s %(name)s: %(message)s",
                                           datefmt="%H:%M:%S"))
    LOG.setLevel(level)
    LOG.addHandler(handler)
    from . import frozen

    if frozen.has_console():
        stream = logging.StreamHandler()
        stream.setFormatter(handler.formatter)
        LOG.addHandler(stream)


def subscribe(fn: Callable[[str], None]) -> None:
    _LISTENERS.append(fn)


def history() -> List[str]:
    return list(_BUFFER)


def report_dir() -> str:
    """Where crash reports go.

    Delegated to :mod:`dso_app.frozen`, which checks that the directory is
    actually writable.  This used to return the executable's own folder
    unconditionally; in an installed build that is ``C:\\Program Files\\...``,
    the write failed, and the crash report -- the entire diagnosis available to
    a user with no console -- was silently dropped.
    """
    from . import frozen

    return frozen.report_dir()


def write_crash_report(exc_type, exc, tb) -> str:
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = os.path.join(report_dir(), f"dso-crash-{stamp}.txt")
    buf = io.StringIO()
    buf.write(f"Darkstar One Modding Suite crash report\n{stamp}\n\n")
    buf.write(f"python   {platform.python_version()} ({sys.executable})\n")
    buf.write(f"platform {platform.platform()}\n")
    buf.write(f"frozen   {getattr(sys, 'frozen', False)}\n\n")
    try:
        import PySide6

        buf.write(f"PySide6  {PySide6.__version__}\n\n")
    except Exception:  # noqa: BLE001
        buf.write("PySide6  <unavailable>\n\n")
    buf.write("".join(traceback.format_exception(exc_type, exc, tb)))
    buf.write("\n\n--- recent log ---\n")
    buf.write("\n".join(_BUFFER[-200:]))
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(buf.getvalue())
    except OSError:
        return ""
    return path


def install_excepthook(notify: Optional[Callable[[str, str], None]] = None) -> None:
    """Catch anything that escapes, write a report, and tell the user where.

    ``notify(summary, path)`` is called on the UI thread if supplied; without it
    the report is still written, because a silent crash is the worst outcome.
    """
    previous = sys.excepthook

    def hook(exc_type, exc, tb):
        path = write_crash_report(exc_type, exc, tb)
        LOG.error("unhandled %s: %s (report: %s)", exc_type.__name__, exc, path or "not written")
        if notify:
            try:
                notify(f"{exc_type.__name__}: {exc}", path)
            except Exception:  # noqa: BLE001
                pass
        previous(exc_type, exc, tb)

    sys.excepthook = hook


__all__ = [
    "LOG", "install", "install_excepthook", "subscribe", "history",
    "write_crash_report", "report_dir",
]
