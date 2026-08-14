"""
Working out which interface elements sit on which.

The header says how many elements are top level; it does not say who owns the
rest.  This is the derivation, and the reason it is trustworthy enough to draw:
the count it is never told still comes out right on every shipped screen.
"""

from __future__ import annotations

from dsotools.edit import screentree


class _E:
    """The two attributes the derivation reads."""

    def __init__(self, class_name, name, rect=(0, 0, 10, 10)):
        self.class_name = class_name
        self.name = name
        self.rect = rect


class _S:
    def __init__(self, elements, declared):
        self.elements = elements
        self.declared_children = declared


def _slider(prefix, at=(100, 50, 20, 200)):
    """A slider and the four sub-controls the engine builds for it."""
    return [
        _E("CSlider", prefix, at),
        _E("CStatic", prefix + "_Background", (2, -1, 7, 190)),
        _E("CButton", prefix + "_Button +", (0, 180, 17, 18)),
        _E("CButton", prefix + "_Button -", (0, 0, 17, 18)),
        _E("CButton", prefix + "_Button Drag", (1, 40, 17, 30)),
    ]


def test_a_flat_screen_has_no_children():
    elements = [_E("CStatic", "BG"), _E("CButton", "OK"), _E("CTextBox", "Text")]

    assert screentree.parents(elements) == [-1, -1, -1]


def test_a_slider_owns_the_four_records_that_follow_it():
    elements = [_E("CStatic", "BG")] + _slider("Panel_VSlider")

    assert screentree.parents(elements) == [-1, -1, 1, 1, 1, 1]


def test_a_child_is_placed_on_its_parent():
    """The whole point: the drag handle belongs on the slider, not at (1, 40)."""
    elements = [_E("CStatic", "BG")] + _slider("Panel_VSlider", at=(444, 59, 26, 297))
    par = screentree.parents(elements)

    assert screentree.origins(elements, par)[5] == (445, 99)
    assert screentree.depths(par) == [0, 0, 1, 1, 1, 1]


def test_a_list_box_owns_its_template_its_cell_and_its_slider():
    """Nesting really is two deep: list box -> slider -> drag handle."""
    elements = [
        _E("CListBox", "LB"),
        _E("CButton", "LB_template[0][0]"),
        _E("CTextBoxEx", "LB(Col:0 Row:0)"),
    ] + _slider("LB_VSlider")
    par = screentree.parents(elements)

    assert par == [-1, 0, 0, 0, 3, 3, 3, 3]
    assert max(screentree.depths(par)) == 2


def test_a_sibling_that_merely_shares_a_prefix_is_not_a_child():
    """``WH_Background2`` is not part of ``WH_Background``, and
    ``S0000_GFX_Radar_Freund_selektiert`` is not part of ``..._Freund``.
    Both appear in screens that declare themselves flat.
    """
    elements = [
        _E("CStaticImg", "WH_Background"),
        _E("CStaticImg", "WH_Background2"),
        _E("CStatic", "S0000_GFX_Radar_Freund"),
        _E("CStatic", "S0000_GFX_Radar_Freund_selektiert"),
    ]

    assert screentree.parents(elements) == [-1, -1, -1, -1]


def test_an_abbreviated_child_name_is_still_recognised():
    """``LOGBUCH_CAREER`` names the slider ``_vsl`` and its parts ``_bdr``."""
    elements = [
        _E("CSlider", "LB_Goods_vsl"),
        _E("CStatic", "LB_Goods_vsl_back"),
        _E("CButton", "LB_Goods_vsl_bin"),
        _E("CButton", "LB_Goods_vsl_bde"),
        _E("CButton", "LB_Goods_vsl_bdr"),
    ]

    assert screentree.parents(elements) == [-1, 0, 0, 0, 0]


def test_a_run_that_is_not_the_slider_pattern_is_left_alone():
    """Four following buttons are not sub-controls just by being four."""
    elements = [
        _E("CSlider", "Panel"),
        _E("CButton", "Panel_Button +"),
        _E("CButton", "Panel_Button -"),
        _E("CButton", "Panel_Button Drag"),
        _E("CButton", "Panel_Background"),
    ]

    assert screentree.parents(elements) == [-1, -1, -1, -1, -1]


def test_consistency_is_checked_against_the_count_in_the_file():
    elements = [_E("CStatic", "BG")] + _slider("Panel_VSlider")

    assert screentree.consistent(_S(elements, declared=2))
    assert not screentree.consistent(_S(elements, declared=6))
