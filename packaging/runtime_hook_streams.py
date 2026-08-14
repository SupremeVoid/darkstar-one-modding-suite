"""
Runs before any application code in a frozen build.

PyInstaller executes runtime hooks after the bootloader has set the process up
and before ``main.py``.  That is the only point early enough to fix the standard
streams: by the time ``dso_app.__main__.main()`` runs, an import of some third
party module may already have tried to write a warning to ``sys.stderr`` -- which
in a windowed build is ``None`` -- and taken the process down before anything of
ours could report it.

``dso_app.frozen.ensure_streams()`` does the same thing and is what the tests
cover; this hook is the belt to that module's braces, and is written to work
even if ``dso_app`` itself cannot be imported yet.
"""

import sys


class _NullStream:
    encoding = "utf-8"
    errors = "replace"

    def write(self, text):
        return len(text)

    def writelines(self, lines):
        pass

    def flush(self):
        pass

    def isatty(self):
        return False

    def fileno(self):
        raise OSError("no file descriptor: this process has no console")

    def close(self):
        pass


for _name in ("stdout", "stderr"):
    if getattr(sys, _name, None) is None:
        setattr(sys, _name, _NullStream())
