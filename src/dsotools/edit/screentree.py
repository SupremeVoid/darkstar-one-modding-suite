"""
Which elements of a ``.screen`` belong to which, and where they really sit.

WHY THIS EXISTS
---------------
A ``.screen`` stores its elements as a flat run of records, and the header says
how many of them are **top level**.  In 64 of the 83 shipped screens that number
equals the number of records and the layout is flat.  In the other 19 it is
smaller, and the difference is the point: those files hold 133 records that are
not placed on the screen at all, but *on another element*.

That matters because a child's rectangle is relative to its parent.  Read flat,
``MOD_slider_Button Drag (2, 48, 17, 30)`` lands in the top-left corner of the
window instead of on the slider at ``(444, 59)``; ``MOD_slider_Background``
starts at ``y = -1`` and vanishes off the top edge.  That is what makes the
MOD_MANAGER layout look like it is missing most of its elements.

WHAT IS AND IS NOT READ FROM THE FILE
-------------------------------------
The top-level count **is** in the file.  The parentage is **not**: no dword at
any fixed offset in the 720-byte common part of an element holds a child count
or a parent index -- the 948 elements of the 64 flat screens pin every candidate
offset to zero, and none of the 551 survivors reads 4 at the two sliders that
own four children.  So the tree here is *derived*, from what the engine's
widgets are: a ``CSlider`` builds a background and three buttons, a list box
builds row templates and a slider.  Those follow their owner in the file, and
their names extend their owner's name.

Two things keep that honest:

* the derived number of top-level elements equals the number the header
  declares in **83 of 83** screens -- an exact integer per file that the rule is
  never given;
* of the 133 children it finds, **105** sit on their parent's box once resolved
  relatively (mean overlap 77%), against **11** read flat (mean overlap 7%).

A caller that must not guess can compare :func:`parents` against
``declared_children`` itself; :func:`consistent` does exactly that.
"""

from __future__ import annotations

from typing import Dict, List, Sequence, Tuple

VERSION = "1.0"

#: Classes that own the sub-controls the engine builds for them.
LIST_CLASSES = ("CListBox", "CDrawable")

#: Classes a list box's row template or cell is built from.
CELL_CLASSES = ("CTextBoxEx", "CButton", "CTextBox", "CStatic",
                "CDrawable", "CStaticImg")

#: A slider owns exactly these four, in this order: a background, then the
#: two step buttons and the drag handle.
SLIDER_CHILDREN = 4


def _extends(child: str, parent: str) -> bool:
    """Is ``child`` a name built from ``parent``'s?

    Deliberately not requiring a separator, and not a fixed vocabulary of
    suffixes: the shipped data abbreviates (``..._Goods_vsl_bdr``) and one
    file's cell name is mangled (``STT_NEWS_ListBox_NewsBox_Story Row:0)``).
    The class pattern below is what carries the weight; the name only confirms.
    """
    return bool(parent) and len(parent) < len(child) and child.startswith(parent)


def parents(elements: Sequence) -> List[int]:
    """``[parent index or -1]``, one per element, in file order.

    ``elements`` is anything with ``.class_name``, ``.name`` -- a parsed
    :class:`~dsotools.formats.screen.Screen` iterates exactly that.
    """
    out = [-1] * len(elements)
    cls = [e.class_name for e in elements]
    name = [e.name for e in elements]

    def subtree(i: int) -> int:
        """Consume element ``i`` and all it owns; return the next index."""
        j = i + 1
        if cls[i] == "CSlider":
            run = elements[j:j + SLIDER_CHILDREN]
            if (len(run) == SLIDER_CHILDREN
                    and [e.class_name for e in run[1:]] == ["CButton"] * 3
                    and run[0].class_name != "CButton"
                    and all(_extends(e.name, name[i]) for e in run)):
                for k in range(j, j + SLIDER_CHILDREN):
                    out[k] = i
                j += SLIDER_CHILDREN
        elif cls[i] in LIST_CLASSES:
            while j < len(elements) and _extends(name[j], name[i]):
                if cls[j] == "CSlider":
                    out[j] = i
                    j = subtree(j)
                elif cls[j] in CELL_CLASSES:
                    out[j] = i
                    j += 1
                else:
                    break
        return j

    i = 0
    while i < len(elements):
        i = subtree(i)
    return out


def depths(parent_of: Sequence[int]) -> List[int]:
    """How deep each element sits; 0 for a top-level one."""
    out: List[int] = []
    for par in parent_of:
        out.append(0 if par < 0 else out[par] + 1)
    return out


def origins(elements: Sequence, parent_of: Sequence[int]) -> List[Tuple[int, int]]:
    """Where each element actually is, resolving children through their parent.

    Coordinates are relative to the screen's own box, not to the desktop: two
    of the 83 screens have a non-zero origin (``STATUSLEISTE`` is at
    ``(300, 0)``) and their elements are laid out inside it, so subtracting that
    origin is what pushes them off the canvas.
    """
    out: List[Tuple[int, int]] = []
    for i, element in enumerate(elements):
        x, y = element.rect[0], element.rect[1]
        if parent_of[i] < 0:
            out.append((x, y))
        else:
            px, py = out[parent_of[i]]
            out.append((px + x, py + y))
    return out


def resolve(screen) -> List[Dict]:
    """``[{index, parent, depth, origin, rect}]`` for a parsed screen."""
    parent_of = parents(screen.elements)
    deep = depths(parent_of)
    where = origins(screen.elements, parent_of)
    return [
        {
            "index": i,
            "parent": parent_of[i],
            "depth": deep[i],
            "origin": where[i],
            "rect": element.rect,
        }
        for i, element in enumerate(screen.elements)
    ]


def consistent(screen) -> bool:
    """Does the derived tree account for exactly the declared top-level count?

    True for all 83 shipped screens.  A caller that will not act on a guess can
    fall back to a flat reading when this is False.
    """
    parent_of = parents(screen.elements)
    return sum(1 for p in parent_of if p < 0) == screen.declared_children


__all__ = ["VERSION", "LIST_CLASSES", "CELL_CLASSES", "SLIDER_CHILDREN",
           "parents", "depths", "origins", "resolve", "consistent"]
