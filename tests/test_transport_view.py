"""
The audio transport's arithmetic.

No Qt here, deliberately: this suite never builds a ``QApplication``, and the
one time it did -- to click a real slider -- importing the media stack left
threads behind that turned an eleven-second run into a hang. The widget is
verified by ``tools/drive_audio_tab.py`` instead; what is pinned here is the
maths the widget defers to.
"""

from __future__ import annotations

import os
import sys

import pytest

APP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app")
if APP not in sys.path:
    sys.path.insert(0, APP)

from dso_app.transport_view import clock, seek_value  # noqa: E402


@pytest.mark.parametrize("milliseconds,shown", [
    (0, "0:00"),
    (999, "0:00"),          # not "0:01" -- the second has not elapsed
    (1000, "0:01"),
    (59999, "0:59"),
    (60000, "1:00"),
    (168118, "2:48"),       # the game's menu theme
    (3600000, "60:00"),     # no hours field; nothing here is that long
])
def test_the_clock_truncates_like_every_other_player(milliseconds, shown):
    assert clock(milliseconds) == shown


def test_a_negative_position_reads_as_zero():
    """Players report one briefly while seeking; '-0:01' looks like a bug."""
    assert clock(-1) == "0:00"
    assert clock(-100000) == "0:00"


@pytest.mark.parametrize("x,expected", [
    (0, 0),
    (50, 25000),
    (100, 50000),
    (200, 100000),
])
def test_clicking_a_scrubber_seeks_where_you_clicked(x, expected):
    """Qt's default pages towards the click, which is wrong for a seek bar."""
    assert seek_value(x, 200, 0, 100000) == expected


def test_a_click_outside_the_widget_is_clamped():
    """A wide handle can report a position past either edge."""
    assert seek_value(-20, 200, 0, 100000) == 0
    assert seek_value(260, 200, 0, 100000) == 100000


def test_an_empty_range_cannot_be_divided_by():
    """Before a sound is chosen there is no duration and no width to scale to."""
    assert seek_value(100, 200, 0, 0) == 0
    assert seek_value(100, 0, 0, 100000) == 0
    assert seek_value(100, 200, 500, 500) == 500


def test_a_range_that_does_not_start_at_zero_still_scales():
    assert seek_value(100, 200, 1000, 3000) == 2000
