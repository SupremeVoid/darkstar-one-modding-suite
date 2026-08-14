"""
The one place the application changes how Qt looks, and why it has to.

THE PROBLEM WITH THE DEFAULT SELECTION
--------------------------------------
Every list in this app codes information into the *colour of the text*: blue
means "your mod supplies this", amber means "this was inferred rather than
read", red means "missing".  The platform's selected-row style is the system
accent colour with white text -- and white text is not the colour those rows
were painted, so selecting a row throws the information away.  Worse, the
custom colours that do survive land on a saturated blue background.

Measured, in WCAG contrast ratios, on the Windows accent (`#0078d4`):

    normal text  3.8      "from the mod" blue  1.1
    amber        1.4      red                  1.2

Anything under 3 is unreadable at small sizes; 1.1 is invisible.  That is not a
matter of taste, and it is what "the blue makes the text hard to read" is.

WHAT IT IS INSTEAD
------------------
A **soft** selection background and **the item keeps its own colour**.  Setting
only ``background-color`` would leave Qt painting the text with
``HighlightedText`` (white); setting ``color: palette(text)`` as well makes an
uncoloured row use the normal text colour, while a row that set its own
foreground keeps it -- the model's ForegroundRole wins over the palette.

On ``#d7e9f7`` the same four colours measure:

    normal text 14.0      "from the mod" blue  3.5
    amber        3.6      red                  4.4

(The amber was darkened from ``#b8860b`` to ``#9a6f09`` to get there; it was
3.3 even on plain white, which was already marginal.)

**Hover is a separate, lighter wash**, because "the row under the pointer" and
"the row you chose" are different facts and a list that renders them the same
way makes people click to find out which is which.

Themes are told apart by the palette's own lightness rather than by asking the
OS, so a Qt style change or a forced dark palette is picked up the same way.
"""

from __future__ import annotations

#: Amber, darkened.  Named here rather than repeated as a literal in four files
#: -- it is a *legibility* decision, and one place is where it can stay one.
AMBER = "#9a6f09"
BLUE = "#2980b9"
RED = "#c0392b"
GREY = "#7f8c8d"

#: Colour by diagnostic severity, keyed by ``dsotools.validate.Severity``'s
#: own string values.  Keyed by string rather than by importing the class so
#: this module stays free of everything, which is what lets it be tested for
#: contrast without a game, a session or Qt.
#:
#: Both the Problems tab and the Project tab read this.  They used to decide
#: independently, and disagreed: a dead file was red in one and amber in the
#: other, for the same underlying finding.  A colour that means "error" in one
#: list and "warning" in the next teaches the reader to ignore it.
SEVERITY = {
    "error": RED,
    "warning": AMBER,
    "info": BLUE,
    "hint": GREY,
}

_LIGHT = """
QTreeView::item:hover, QTableView::item:hover, QListView::item:hover {
    background-color: #eef4fb;
}
QTreeView::item:selected, QTableView::item:selected, QListView::item:selected {
    background-color: #d7e9f7;
    color: palette(text);
}
QTreeView::item:selected:!active, QTableView::item:selected:!active,
QListView::item:selected:!active {
    background-color: #e6eef5;
    color: palette(text);
}
/* No ``QTreeView::branch`` rule, deliberately. Styling that subcontrol
   at all makes Qt stop drawing the native expand arrow and wait for an
   image instead -- so a branch:hover rule that only sets a background
   makes the arrow *disappear under the cursor*, which is where it is
   most needed. Tinting the branch gutter is not worth a tree you
   cannot see how to expand. */
"""

_DARK = """
QTreeView::item:hover, QTableView::item:hover, QListView::item:hover {
    background-color: #223244;
}
QTreeView::item:selected, QTableView::item:selected, QListView::item:selected {
    background-color: #24405c;
    color: palette(text);
}
QTreeView::item:selected:!active, QTableView::item:selected:!active,
QListView::item:selected:!active {
    background-color: #1e2c3a;
    color: palette(text);
}
/* No ``QTreeView::branch`` rule, deliberately. Styling that subcontrol
   at all makes Qt stop drawing the native expand arrow and wait for an
   image instead -- so a branch:hover rule that only sets a background
   makes the arrow *disappear under the cursor*, which is where it is
   most needed. Tinting the branch gutter is not worth a tree you
   cannot see how to expand. */
"""


def is_dark(palette) -> bool:
    """Is this a dark palette?  Asked of the palette, not of the OS."""
    return palette.color(palette.ColorRole.Window).lightness() < 128


def stylesheet(palette) -> str:
    """The item-view rules for ``palette``'s kind of theme."""
    return _DARK if is_dark(palette) else _LIGHT


__all__ = ["AMBER", "BLUE", "RED", "is_dark", "stylesheet"]
