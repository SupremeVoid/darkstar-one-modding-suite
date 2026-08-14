"""
The selection colours, checked as contrast rather than as taste.

This exists because the platform default failed and nobody could say so with a
number.  Every list in the app codes meaning into the *colour of the text* --
blue for "your mod supplies this", amber for "inferred", red for "missing" --
and the system accent selection paints white text over a saturated blue, which
both discards that coding and leaves what survives at 1.1:1 against its
background.

So the rules below are the requirement, in the only terms that settle an
argument about colour: WCAG contrast ratios, computed here from first
principles rather than imported from the thing under test.
"""

from __future__ import annotations

import os
import re
import sys

APP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app")
if APP not in sys.path:
    sys.path.insert(0, APP)

from dso_app import theme  # noqa: E402

#: Text colours the app paints into item views.
CODED = {
    "normal": "#1a1a1a",
    "in mod": theme.BLUE,
    "inferred": theme.AMBER,
    "missing": theme.RED,
}
DARK_TEXT = "#e6e6e6"

#: Below this a small label is not readable; the platform default scored 1.1.
MIN_CODED = 3.0
#: Ordinary text has to be comfortably better than merely "readable".
MIN_NORMAL = 7.0


def _luminance(colour: str) -> float:
    colour = colour.lstrip("#")
    channels = [int(colour[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
              for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(a: str, b: str) -> float:
    la, lb = _luminance(a), _luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def test_the_maths_matches_the_published_examples():
    """The ratio function itself, before anything is judged with it."""
    assert round(contrast("#ffffff", "#000000"), 1) == 21.0
    assert round(contrast("#777777", "#ffffff"), 1) == 4.5


def backgrounds(sheet: str):
    return re.findall(r"background-color:\s*(#[0-9a-fA-F]{6})", sheet)


class _Palette:
    """Only what `theme.is_dark` reads."""

    class ColorRole:
        Window = "window"

    def __init__(self, lightness):
        self._lightness = lightness

    def color(self, _role):
        class _C:
            def __init__(self, value):
                self._value = value

            def lightness(self):
                return self._value

        return _C(self._lightness)


def test_light_and_dark_are_told_apart_by_the_palette_not_the_os():
    """A forced dark palette or a Qt style change has to be picked up too."""
    assert theme.is_dark(_Palette(20)) is True
    assert theme.is_dark(_Palette(240)) is False
    assert theme.stylesheet(_Palette(20)) != theme.stylesheet(_Palette(240))


def test_every_coded_colour_stays_readable_on_a_selected_row():
    """The bug, as a rule: 1.1:1 is how it got reported.

    Applies to hover as well -- a row is hovered exactly while it is being
    read.
    """
    for background in backgrounds(theme.stylesheet(_Palette(240))):
        assert contrast(background, CODED["normal"]) >= MIN_NORMAL, background
        for name, colour in CODED.items():
            assert contrast(background, colour) >= MIN_CODED, (background, name)


def test_the_dark_selection_is_readable_too():
    for background in backgrounds(theme.stylesheet(_Palette(20))):
        assert contrast(background, DARK_TEXT) >= MIN_NORMAL, background


def test_hover_and_selection_are_not_the_same_colour():
    """Two different facts -- "under the pointer" and "chosen" -- and a list
    that paints them alike makes people click to find out which is which."""
    for palette in (_Palette(240), _Palette(20)):
        sheet = theme.stylesheet(palette)
        hover = re.search(r":hover \{\s*background-color:\s*(#[0-9a-fA-F]{6})", sheet)
        selected = re.search(
            r":selected \{\s*background-color:\s*(#[0-9a-fA-F]{6})", sheet)
        assert hover and selected
        assert hover.group(1) != selected.group(1)


def test_the_item_keeps_its_own_colour_when_selected():
    """`background-color` alone would leave Qt painting the text white, which
    is what threw the colour coding away in the first place."""
    for palette in (_Palette(240), _Palette(20)):
        sheet = theme.stylesheet(palette)
        for block in re.findall(r"::item:selected[^{]*\{([^}]*)\}", sheet):
            assert "color: palette(text)" in block


def test_the_branch_subcontrol_is_never_styled():
    """Styling it makes the expand arrow vanish under the cursor.

    Qt stops drawing a subcontrol natively as soon as a stylesheet touches it,
    and waits for an ``image`` instead. A ``QTreeView::branch:hover`` rule that
    only set a background therefore hid the expand arrow on exactly the row the
    pointer was over -- reported from the running app. Tinting the branch
    gutter is not worth a tree you cannot see how to expand.
    """
    for palette in (_Palette(240), _Palette(20)):
        sheet = theme.stylesheet(palette)
        # Comments are for the next reader; only live rules matter.
        live = re.sub(r"/\*.*?\*/", "", sheet, flags=re.S)
        assert not re.search(r"QTreeView::branch[^{]*\{", live), (
            "a ::branch rule is back; it hides the expand arrows on hover"
        )
