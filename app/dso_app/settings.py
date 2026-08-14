"""
Small persistent preferences: window state, and the notices you have dismissed.

WHY A FILE OF OUR OWN RATHER THAN ``QSettings``
-----------------------------------------------
``QSettings`` on Windows writes to the registry, which is invisible, awkward to
reset and impossible to carry with a portable copy of the app.  This is a
modding tool that people unzip somewhere and run; a JSON file **next to the
executable** matches how it is actually used, and deleting it is an obvious way
to start clean.

WHERE IT GOES, AND WHY THAT IS NOT ALWAYS NEXT TO THE EXECUTABLE
----------------------------------------------------------------
Next to the executable *when that is writable*, and the per-user data folder
otherwise.  ``C:\\Program Files\\…`` is not writable by an unelevated process,
and a preference that silently fails to save is worse than one kept somewhere
less obvious -- this is the same reasoning, and the same fallback order, that
:func:`frozen.report_dir` already uses for crash reports.

NOTHING HERE IS LOAD-BEARING
----------------------------
Every read takes a default and every failure is swallowed: a corrupt or
unreadable settings file must never stop the application starting.  Losing a
"do not show this again" tick is a nuisance; refusing to launch over it would
be absurd.  It also means the file can be deleted at any time.

No Qt here, so it can be tested headlessly like everything else in this layer.
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any, Dict, Optional

from . import frozen

FILENAME = "dso_app_settings.json"

#: Bumped only if the shape changes incompatibly; unknown keys are preserved on
#: save, so a file written by a newer build survives a round-trip through an
#: older one rather than losing its settings.
SCHEMA = 1


def settings_path() -> str:
    """Where the settings file lives.  Next to the executable if possible."""
    base = frozen.install_dir()
    if frozen._writable(base):
        return os.path.join(base, FILENAME)
    fallback = frozen.user_data_dir()
    try:
        os.makedirs(fallback, exist_ok=True)
    except OSError:                                   # pragma: no cover
        return os.path.join(tempfile.gettempdir(), FILENAME)
    return os.path.join(fallback, FILENAME)


class Settings:
    """A flat key/value store backed by one JSON file."""

    def __init__(self, path: Optional[str] = None) -> None:
        self.path = path or settings_path()
        self._data: Dict[str, Any] = {"schema": SCHEMA}
        self.load()

    # -- io ------------------------------------------------------------------

    def load(self) -> None:
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            # Missing is the normal case on first run; corrupt is rare and not
            # worth a dialog.  Either way, defaults.
            return
        if isinstance(data, dict):
            self._data.update(data)
            self._data["schema"] = SCHEMA

    def save(self) -> bool:
        """Write the file.  ``False`` if it could not be written.

        Temp-and-replace, so an interrupted write cannot leave a half-file that
        then fails to parse on next start.
        """
        directory = os.path.dirname(self.path)
        try:
            if directory:
                os.makedirs(directory, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=directory or None, suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh, indent=2, sort_keys=True)
            os.replace(tmp, self.path)
            return True
        except OSError:
            return False

    # -- values --------------------------------------------------------------

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """Set and persist.  Saving on every change is affordable here.

        The file is a few hundred bytes and changes happen at human speed, so
        batching writes would only create a window in which a crash loses the
        setting the user just chose.
        """
        if self._data.get(key) == value:
            return
        self._data[key] = value
        self.save()

    # -- dismissible notices -------------------------------------------------

    #: Prefix so a suppressed notice cannot collide with an ordinary setting.
    NOTICE_PREFIX = "notice_hidden."

    def notice_hidden(self, key: str) -> bool:
        return bool(self._data.get(self.NOTICE_PREFIX + key, False))

    def hide_notice(self, key: str, hidden: bool = True) -> None:
        self.set(self.NOTICE_PREFIX + key, bool(hidden))

    def show_all_notices(self) -> None:
        """Undo every "do not show this again"."""
        for key in [k for k in self._data if k.startswith(self.NOTICE_PREFIX)]:
            del self._data[key]
        self.save()

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Settings {self.path}>"


__all__ = ["Settings", "settings_path", "FILENAME", "SCHEMA"]
