"""
The arithmetic behind the audio transport, free of Qt.

Kept out of the tab for the same reason as ``api_view.py`` and ``theme.py``:
this is the part with rules worth testing, and the tab around it cannot be
unit-tested at all -- the suite deliberately never builds a ``QApplication``,
because importing the media stack to check a division would cost more than it
proves.

Two rules, both of which read as trivial and are not:

* a clock **truncates**. 999 ms is ``0:00``, not ``0:01``: a player that
  announces a second before it has elapsed is wrong at exactly the moment
  someone is watching it.
* clicking a scrubber goes **where you clicked**. Qt's default is a page step,
  which is right for a scrollbar and wrong for a seek bar, and the conversion
  has to survive a zero-width widget and an empty range -- both of which happen
  before any sound is loaded.
"""

from __future__ import annotations

VERSION = "1.0"


def clock(milliseconds: int) -> str:
    """``m:ss``, truncated, never negative.

    A player reports a negative position briefly while seeking, and ``-0:01``
    on screen looks like a bug in the file rather than in the reader.
    """
    total = max(0, int(milliseconds)) // 1000
    return f"{total // 60}:{total % 60:02d}"


def seek_value(x: float, width: float, minimum: int, maximum: int) -> int:
    """Where a click ``x`` pixels along a ``width``-wide scrubber points.

    Clamped to the range, so a click on the very edge -- or past it, which
    happens with a wide handle -- cannot ask the player to seek outside the
    media.
    """
    if maximum <= minimum or width <= 0:
        return minimum
    fraction = min(1.0, max(0.0, x / width))
    return round(minimum + fraction * (maximum - minimum))


__all__ = ["VERSION", "clock", "seek_value"]
