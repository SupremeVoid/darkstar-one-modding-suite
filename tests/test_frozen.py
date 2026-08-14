"""
The packaged-build behaviours, tested without packaging anything.

Everything in ``dso_app.frozen`` exists because a windowed executable differs
from a development run in ways that are invisible until a user hits them: no
standard streams, and an install directory nobody can write to.  Both are
reproducible here in a few lines -- ``sys.stdout = None`` *is* what PyInstaller
hands the process -- so none of this has to wait for a build machine.

The alternative was to find out from a bug report saying "it just closes".
"""

from __future__ import annotations

import logging
import os
import sys

import pytest

APP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app")
if APP not in sys.path:
    sys.path.insert(0, APP)

from dso_app import diagnostics, frozen  # noqa: E402


def _no_console(monkeypatch):
    """Exactly what a PyInstaller windowed build looks like from inside.

    A helper rather than a fixture on purpose: pytest re-installs its capture
    objects over ``sys.stdout``/``sys.stderr`` at the start of each test phase,
    so a fixture that replaced them during setup would be quietly undone before
    the test body ran -- and the test would then pass for the wrong reason.
    """
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    frozen.ensure_streams()


# -- streams ---------------------------------------------------------------


def test_ensure_streams_replaces_missing_streams(monkeypatch):
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)

    assert frozen.ensure_streams() is True

    assert sys.stdout is not None and sys.stderr is not None
    print("this must not raise")
    print("nor this", file=sys.stderr)


def test_ensure_streams_leaves_a_real_console_alone():
    before_out, before_err = sys.stdout, sys.stderr

    frozen.ensure_streams()

    assert sys.stdout is before_out
    assert sys.stderr is before_err


def test_has_console_is_false_once_the_streams_are_stand_ins(monkeypatch):
    _no_console(monkeypatch)

    assert not frozen.has_console()


def test_a_stand_in_stream_has_no_file_descriptor(monkeypatch):
    """Code that asks for fileno() must get an error it can catch, not a lie."""
    _no_console(monkeypatch)

    with pytest.raises(OSError):
        sys.stdout.fileno()


def test_logging_adds_no_stream_handler_without_a_console(monkeypatch):
    _no_console(monkeypatch)
    log = logging.getLogger("dso")
    before = list(log.handlers)
    try:
        log.handlers.clear()
        diagnostics.install()
        assert not any(type(h) is logging.StreamHandler for h in log.handlers)
    finally:
        log.handlers[:] = before


# -- where reports go ------------------------------------------------------


def test_report_dir_falls_back_when_the_install_folder_is_read_only(monkeypatch, tmp_path):
    """``C:\\Program Files\\...`` is the normal case, not the exotic one."""
    installed = tmp_path / "Program Files" / "DSO"
    installed.mkdir(parents=True)
    userdata = tmp_path / "AppData" / "DSO"

    # Through the module's own seam rather than by inventing ``sys.frozen``:
    # the attribute does not exist off a frozen build, and a test that has to
    # create one is testing PyInstaller's convention instead of this code.
    monkeypatch.setattr(frozen, "is_frozen", lambda: True)
    monkeypatch.setattr(sys, "executable", str(installed / "dso.exe"))
    monkeypatch.setattr(frozen, "user_data_dir", lambda: str(userdata))
    monkeypatch.setattr(
        frozen, "_writable", lambda d: os.path.abspath(d) != os.path.abspath(installed)
    )

    assert frozen.report_dir() == str(userdata)


def test_report_dir_prefers_the_executables_own_folder(monkeypatch, tmp_path):
    """A portable build: the user finds the report without being told where."""
    installed = tmp_path / "portable"
    installed.mkdir()
    monkeypatch.setattr(frozen, "is_frozen", lambda: True)
    monkeypatch.setattr(sys, "executable", str(installed / "dso.exe"))

    assert frozen.report_dir() == str(installed)


def test_writable_answers_by_trying(tmp_path):
    blocker = tmp_path / "file.txt"
    blocker.write_bytes(b"not a directory")

    assert frozen._writable(str(tmp_path))
    assert not frozen._writable(str(blocker / "below-a-file"))
    # ...and the probe leaves nothing behind
    assert [p.name for p in tmp_path.iterdir()] == ["file.txt"]


def test_crash_report_survives_an_unwritable_install_dir(monkeypatch, tmp_path):
    """The regression this module was written for: no console, no report, no clue."""
    installed = tmp_path / "Program Files" / "DSO"
    installed.mkdir(parents=True)
    fallback = tmp_path / "fallback"
    fallback.mkdir()

    # Through the module's own seam rather than by inventing ``sys.frozen``:
    # the attribute does not exist off a frozen build, and a test that has to
    # create one is testing PyInstaller's convention instead of this code.
    monkeypatch.setattr(frozen, "is_frozen", lambda: True)
    monkeypatch.setattr(sys, "executable", str(installed / "dso.exe"))
    monkeypatch.setattr(frozen, "user_data_dir", lambda: str(fallback))
    monkeypatch.setattr(
        frozen, "_writable", lambda d: os.path.abspath(d) != os.path.abspath(installed)
    )

    try:
        raise ValueError("boom")
    except ValueError:
        path = diagnostics.write_crash_report(*sys.exc_info())

    assert path and os.path.exists(path)
    assert os.path.dirname(path) == str(fallback)
    assert "boom" in open(path, encoding="utf-8").read()


# -- last-resort reporting -------------------------------------------------


def test_fatal_writes_a_file_when_there_is_nothing_else(monkeypatch, tmp_path):
    _no_console(monkeypatch)
    monkeypatch.setattr(frozen, "report_dir", lambda: str(tmp_path))

    path = frozen.fatal("Qt could not be loaded", "the DLL is missing", detail="details")

    assert path == str(tmp_path / "dso-startup-error.txt")
    text = open(path, encoding="utf-8").read()
    assert "Qt could not be loaded" in text and "details" in text


def test_fatal_never_raises_even_when_it_cannot_write(monkeypatch):
    """It runs when things are already wrong; raising here loses the diagnosis."""
    _no_console(monkeypatch)
    monkeypatch.setattr(frozen, "report_dir", lambda: os.path.join(os.sep, "no", "such"))

    assert frozen.fatal("t", "m") is None


# -- the entry point -------------------------------------------------------


def test_a_bad_argument_does_not_crash_without_a_console(monkeypatch, tmp_path):
    """argparse writes usage to stderr, which in a windowed build is None.

    The failure mode was an AttributeError raised *inside* the error handler:
    no window, no message, exit code 1.
    """
    import dso_app.__main__ as entry

    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    monkeypatch.setattr(frozen, "report_dir", lambda: str(tmp_path))

    with pytest.raises(SystemExit) as caught:
        entry.main(["--no-such-flag"])

    assert caught.value.code != 0
    assert (tmp_path / "dso-startup-error.txt").exists()


def test_a_missing_qt_is_reported_rather_than_silent(monkeypatch, tmp_path):
    import builtins

    import dso_app.__main__ as entry

    real_import = builtins.__import__

    def refuse_qt(name, *args, **kwargs):
        if name.startswith("PySide6"):
            raise ImportError("DLL load failed while importing QtWidgets")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    monkeypatch.setattr(builtins, "__import__", refuse_qt)
    monkeypatch.setattr(frozen, "report_dir", lambda: str(tmp_path))

    code = entry.main([])

    assert code == 1
    text = open(tmp_path / "dso-startup-error.txt", encoding="utf-8").read()
    assert "DLL load failed" in text
