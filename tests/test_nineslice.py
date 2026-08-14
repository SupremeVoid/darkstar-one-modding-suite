"""
Composing a stretched frame from its nine tiles.

This exists because the Interface tab's artwork view is only worth having if it
is *right*: of the 694 elements that draw something across the 83 stock screens,
135 are nine-slice frames whose drawable names only the top-left tile.  Drawing
those by stretching that corner is what makes a preview look like a smear --
``Background_blaugrau`` is a 3x3 tile drawn at 511x215.
"""

from __future__ import annotations

import pytest

from dsotools.edit import atlas as atlasmod
from dsotools.edit import nineslice


def _need_pillow():
    if not atlasmod.have_pillow():
        pytest.skip("Pillow not installed")


def test_a_tile_name_is_split_into_its_family():
    assert nineslice.family_of("images/ND_ListBoxWithBGTL.aim") == (
        "images/ND_ListBoxWithBG", "", ".aim")


def test_the_trailing_digits_are_part_of_the_convention():
    """``ND_ScreenFrameTL1`` is a real family, and so is ``ND_ScreenFrame_02TL1``.

    Requiring the suffix to be last cost 12 of the 135 frames in the corpus.
    """
    assert nineslice.family_of("images/ND_ScreenFrameTL1.aim") == (
        "images/ND_ScreenFrame", "1", ".aim")
    assert nineslice.family_of("images/ND_ScreenFrame_02TL1.aim") == (
        "images/ND_ScreenFrame_02", "1", ".aim")


def test_an_ordinary_sprite_is_not_a_family():
    """Told apart by name, so an ordinary sprite is never composed."""
    assert nineslice.family_of("images/Auftraege.aim") is None
    assert nineslice.sibling_names("images/Auftraege.aim") is None


def test_the_siblings_are_named_for_every_cell():
    names = nineslice.sibling_names("images/Frame_TL1.aim")

    assert names["TC"] == "images/Frame_TC1.aim"
    assert names["BR"] == "images/Frame_BR1.aim"
    assert len(names) == 9


def _tiles(corner=4, edge=2):
    from PIL import Image

    def solid(w, h, colour):
        return Image.new("RGBA", (w, h), colour)

    return {
        "TL": solid(corner, corner, (255, 0, 0, 255)),
        "TC": solid(edge, corner, (0, 255, 0, 255)),
        "TR": solid(corner, corner, (0, 0, 255, 255)),
        "ML": solid(corner, edge, (255, 255, 0, 255)),
        "MC": solid(edge, edge, (255, 255, 255, 255)),
        "MR": solid(corner, edge, (0, 255, 255, 255)),
        "BL": solid(corner, corner, (128, 0, 0, 255)),
        "BC": solid(edge, corner, (0, 128, 0, 255)),
        "BR": solid(corner, corner, (0, 0, 128, 255)),
    }


def test_the_corners_keep_their_size_and_the_middle_stretches():
    _need_pillow()
    out = nineslice.compose(_tiles(corner=4), (40, 30))

    assert out.size == (40, 30)
    # Corners, unscaled and in their own corners.
    assert out.getpixel((0, 0)) == (255, 0, 0, 255)          # TL
    assert out.getpixel((39, 0)) == (0, 0, 255, 255)         # TR
    assert out.getpixel((0, 29)) == (128, 0, 0, 255)         # BL
    assert out.getpixel((39, 29)) == (0, 0, 128, 255)        # BR
    # Edges and centre, filling everything between them.
    assert out.getpixel((20, 1)) == (0, 255, 0, 255)         # TC
    assert out.getpixel((1, 15)) == (255, 255, 0, 255)       # ML
    assert out.getpixel((20, 15)) == (255, 255, 255, 255)    # MC


def test_a_frame_smaller_than_its_own_corners_still_draws():
    """Layouts really do that, and a hole in the preview is worse than a clamp."""
    _need_pillow()
    out = nineslice.compose(_tiles(corner=8), (6, 5))

    assert out.size == (6, 5)
    assert out.getpixel((0, 0))[3] == 255       # something was drawn


def test_a_missing_tile_is_named_rather_than_guessed():
    _need_pillow()
    tiles = _tiles()
    del tiles["MC"]

    with pytest.raises(KeyError) as caught:
        nineslice.compose(tiles, (40, 30))
    assert "MC" in str(caught.value)
