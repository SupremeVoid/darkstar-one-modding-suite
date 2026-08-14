"""
Coordinated edits: the atlas editor, deployment, and the project sidecar.

The rescale test is the important one. "You upscaled the page but did not update
the coordinates" is the failure this whole layer exists to make impossible, and
the only way to prove it is to check all three file types after one call.
"""

from __future__ import annotations

import json
import os
import struct
import zipfile

import pytest

from dsotools import vfs as vfsmod
from dsotools.errors import ValidationError
from dsotools.formats import a2d, aim, anim
from dsotools.project import Mod, ProjectFile

PIL = pytest.importorskip if False else None
try:
    from PIL import Image

    HAVE_PIL = True
except ImportError:  # pragma: no cover
    HAVE_PIL = False


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------


def _tex(page_name, sprites):
    """Build a .tex from ``[(name, x, y, w, h)]``."""
    subs = [a2d.SubImage(n, x, y, w, h, page_name) for n, x, y, w, h in sprites]
    return a2d.build(a2d.TexturePage(page_name, subs))


def _anim(source, w, h):
    b = bytearray(anim.RECORD_SIZE)
    b[0:8] = anim.TAG
    struct.pack_into("<I", b, anim.OFF_FRAMES, 1)
    for off, v in (
        (anim.OFF_WIDTH, w), (anim.OFF_WIDTH2, w),
        (anim.OFF_HEIGHT, h), (anim.OFF_HEIGHT2, h),
    ):
        struct.pack_into("<I", b, off, v)
    raw = source.encode("cp1252")
    b[anim.OFF_SOURCE : anim.OFF_SOURCE + len(raw)] = raw
    return bytes(b)


@pytest.fixture()
def atlas_world(tmp_path):
    if not HAVE_PIL:
        pytest.skip("Pillow not installed")
    root = tmp_path / "extracted" / "ds_interface"
    (root / "scripts").mkdir(parents=True)
    (root / "images").mkdir(parents=True)

    page = Image.new("RGBA", (64, 64), (10, 20, 30, 255))
    page.paste(Image.new("RGBA", (16, 16), (255, 0, 0, 255)), (0, 0))
    page.paste(Image.new("RGBA", (16, 16), (0, 255, 0, 255)), (32, 32))
    (root / "images" / "Page.aim").write_bytes(aim.from_image(page))

    (root / "scripts" / "TexPage1.tex").write_bytes(
        _tex(r"images\Page.aim", [
            (r"images\Alpha.aim", 0, 0, 16, 16),
            (r"images\Beta.aim", 32, 32, 16, 16),
        ])
    )
    (root / "scripts" / "Alpha.anim").write_bytes(_anim(r"images\Alpha.aim", 16, 16))
    (root / "scripts" / "Beta.anim").write_bytes(_anim(r"images\Beta.aim", 16, 16))
    return vfsmod.from_extracted(str(tmp_path / "extracted"))


@pytest.fixture()
def page(atlas_world):
    from dsotools.edit.atlas import AtlasPage

    return AtlasPage.open(atlas_world, "scripts/TexPage1.tex")


# --------------------------------------------------------------------------
# reading
# --------------------------------------------------------------------------


def test_opens_page_index_and_dependent_anims(page):
    assert page.size == (64, 64)
    assert {s.stem for s in page.sprites} == {"Alpha", "Beta"}
    assert len(page.anims) == 2


def test_extract_crops_the_right_region(page):
    assert page.extract("Alpha").getpixel((0, 0)) == (255, 0, 0, 255)
    assert page.extract("Beta").getpixel((0, 0)) == (0, 255, 0, 255)


def test_clean_page_has_no_structural_problems(page):
    assert page.out_of_bounds() == []
    assert page.overlaps() == []
    assert page.anim_mismatches() == []


def test_detects_overlap_and_out_of_bounds(page):
    page.index.subimages[1].x = 8          # must move on BOTH axes to intersect
    page.index.subimages[1].y = 8
    assert [(a.stem, b.stem) for a, b in page.overlaps()] == [("Alpha", "Beta")]
    page.index.subimages[1].x = 60         # now runs off the right edge
    page.index.subimages[1].y = 60
    assert [s.stem for s in page.out_of_bounds()] == ["Beta"]


def test_separated_sprites_do_not_count_as_overlapping(page):
    """Guards the sweep against being over-eager on one axis."""
    page.index.subimages[1].x = 0          # same column, far below Alpha
    assert page.overlaps() == []
    page.index.subimages[1].x, page.index.subimages[1].y = 32, 0   # same row, to the right
    assert page.overlaps() == []


def test_overlap_sweep_sees_a_tall_sprite_spanning_later_rows(page):
    """A tall rectangle must still be compared against ones further down."""
    page.index.subimages[0].h = 48         # Alpha now spans y 0..48
    page.index.subimages[1].x = 0          # Beta sits at y 32, inside that span
    assert [(a.stem, b.stem) for a, b in page.overlaps()] == [("Alpha", "Beta")]


def test_detects_anim_size_disagreement(page):
    page.index.subimages[0].w = 24
    bad = page.anim_mismatches()
    assert len(bad) == 1 and bad[0][1] == (16, 16) and bad[0][2] == (24, 16)


# --------------------------------------------------------------------------
# replacing
# --------------------------------------------------------------------------


def test_replace_locks_dimensions_by_default(page):
    """A larger replacement would silently overwrite the neighbour's art."""
    with pytest.raises(ValidationError) as exc:
        page.replace("Alpha", Image.new("RGBA", (32, 32), (0, 0, 255, 255)))
    assert exc.value.code == "TEX001"
    assert not page.dirty


def test_replace_composites_at_the_bound_rectangle(page):
    page.replace("Beta", Image.new("RGBA", (16, 16), (0, 0, 255, 255)))
    assert page.image.getpixel((32, 32)) == (0, 0, 255, 255)
    assert page.image.getpixel((0, 0)) == (255, 0, 0, 255)      # Alpha untouched
    assert page.dirty


def test_resize_updates_the_rectangle_and_the_anim(page):
    page.replace("Alpha", Image.new("RGBA", (8, 8), (1, 2, 3, 255)), allow_resize=True)
    assert page.sprite("Alpha").w == 8
    assert page.anims["scripts/Alpha.anim"].size == (8, 8)


def test_resize_that_would_collide_is_refused(page):
    with pytest.raises(ValidationError) as exc:
        page.replace("Alpha", Image.new("RGBA", (40, 40)), allow_resize=True)
    assert exc.value.code == "TEX003"


# --------------------------------------------------------------------------
# rescale -- the coordinated operation
# --------------------------------------------------------------------------


def test_rescale_updates_page_rectangles_and_anims_together(page):
    """One call, three file types, no way to do two of the three."""
    page.rescale(2)

    assert page.size == (128, 128)
    alpha = page.sprite("Alpha")
    beta = page.sprite("Beta")
    assert (alpha.x, alpha.y, alpha.w, alpha.h) == (0, 0, 32, 32)
    assert (beta.x, beta.y, beta.w, beta.h) == (64, 64, 32, 32)
    assert page.anims["scripts/Alpha.anim"].size == (32, 32)
    assert page.anims["scripts/Beta.anim"].size == (32, 32)

    assert page.out_of_bounds() == []
    assert page.overlaps() == []
    assert page.anim_mismatches() == []


def test_rescale_writes_every_affected_file(page):
    page.rescale(2)
    out = page.save()
    assert set(out) == {
        "images/Page.aim",
        "scripts/TexPage1.tex",
        "scripts/Alpha.anim",
        "scripts/Beta.anim",
    }
    # the emitted files must parse back to the new state
    assert a2d.parse(out["scripts/TexPage1.tex"]).subimages[0].w == 32
    assert anim.parse(out["scripts/Alpha.anim"]).size == (32, 32)
    # The source size follows the rectangle, and for an ordinary drawable the
    # drawn size follows with it -- so the two still agree.
    assert anim.parse(out["scripts/Alpha.anim"]).source_size == (32, 32)
    assert not anim.parse(out["scripts/Alpha.anim"]).stretched
    assert aim.to_image(aim.parse(out["images/Page.aim"])).size == (128, 128)


def test_rescale_keeps_adjacent_sprites_adjacent(atlas_world):
    """Rounding edges, not origin-and-size, or seams open up."""
    from dsotools.edit.atlas import AtlasPage

    p = AtlasPage.open(atlas_world, "scripts/TexPage1.tex")
    p.index.subimages[0].x, p.index.subimages[0].w = 0, 7
    p.index.subimages[1].x, p.index.subimages[1].w = 7, 9
    p.index.subimages[1].y = 0
    p.rescale(1.5)
    a, b = p.sprites
    assert a.x + a.w == b.x, "a one-pixel seam opened between adjacent sprites"


def test_rescale_rejects_a_nonpositive_factor(page):
    with pytest.raises(ValueError):
        page.rescale(0)


def test_save_is_empty_until_something_changes(page):
    assert page.save() == {}
    assert not page.dirty


# --------------------------------------------------------------------------
# deployment
# --------------------------------------------------------------------------


@pytest.fixture()
def mod(tmp_path):
    root = tmp_path / "Customization" / "M"
    root.mkdir(parents=True)
    (root / "darkstarmod.ini").write_bytes(b"[darkstarmod]\r\nmod_name = M\r\nmod_desc = d\r\n")
    (root / "inifiles").mkdir()
    (root / "inifiles" / "items.ini").write_bytes(b"[i]\r\n")
    with zipfile.ZipFile(root / "user_data.zip", "w") as zf:
        zf.writestr("3DView/Keep.xml", b"<keep/>")
    return Mod(str(root))


def test_deploy_routes_each_file_to_where_the_engine_reads_it(mod):
    routed = mod.deploy({
        "3DView/textures/new.dds": b"DDS ",
        "inifiles/Goods.ini": b"[goods]\r\n",
    })
    assert routed == {"3DView/textures/new.dds": "zip", "inifiles/Goods.ini": "loose"}
    with zipfile.ZipFile(mod.zip_path) as zf:
        assert "3DView/textures/new.dds" in zf.namelist()
    assert os.path.exists(os.path.join(mod.root, "inifiles", "Goods.ini"))
    # nothing 3DView-shaped may land loose, where it would be dead
    assert not os.path.exists(os.path.join(mod.root, "3DView"))


def test_deploy_preserves_existing_zip_entries(mod):
    mod.deploy({"3DView/New.xml": b"<new/>"})
    with zipfile.ZipFile(mod.zip_path) as zf:
        assert sorted(zf.namelist()) == ["3DView/Keep.xml", "3DView/New.xml"]
        assert zf.read("3DView/Keep.xml") == b"<keep/>"


def test_deploy_replaces_rather_than_duplicates(mod):
    mod.deploy({"3DView/Keep.xml": b"<replaced/>"})
    with zipfile.ZipFile(mod.zip_path) as zf:
        assert zf.namelist() == ["3DView/Keep.xml"]
        assert zf.read("3DView/Keep.xml") == b"<replaced/>"


def test_deploy_leaves_no_temporary_files(mod):
    mod.deploy({"3DView/New.xml": b"<new/>", "inifiles/X.ini": b"[x]\r\n"})
    leftovers = [f for f in os.listdir(mod.root) if f.endswith(".tmp") or ".tmp" in f]
    assert leftovers == []


def test_atlas_save_feeds_straight_into_deploy(atlas_world, mod):
    """The two halves must fit together without the caller adapting anything.

    One edit, two destinations, and that is not an inconsistency: the page goes
    into user_data.zip because a loose ``images/`` file is never read (tested in
    game -- an edited page did nothing loose and appeared at once from the zip),
    while ``scripts/`` genuinely is read loose.
    """
    from dsotools.edit.atlas import AtlasPage

    p = AtlasPage.open(atlas_world, "scripts/TexPage1.tex")
    p.rescale(2)
    routed = mod.deploy(p.save())
    assert routed["images/Page.aim"] == "zip"
    assert routed["scripts/TexPage1.tex"] == "loose"
    assert os.path.exists(os.path.join(mod.root, "scripts", "TexPage1.tex"))
    with zipfile.ZipFile(os.path.join(mod.root, "user_data.zip")) as zf:
        assert "images/Page.aim" in zf.namelist()


# --------------------------------------------------------------------------
# .dsoproj
# --------------------------------------------------------------------------


def test_project_file_round_trips(mod):
    pf = ProjectFile()
    pf.record("3DView/textures/a.dds", source="art/a.png", operation="replace_pixels")
    pf.save(mod.root)
    again = ProjectFile.load(mod.root)
    assert again.provenance_of("3DView/textures/a.dds")["source"] == "art/a.png"
    assert again.data["tool_version"]


def test_project_file_is_absent_not_an_error(mod):
    assert ProjectFile.load(mod.root).data["provenance"] == {}


def test_rebuildable_lists_files_with_a_recorded_source(mod):
    pf = ProjectFile()
    pf.record("a.dds", source="art/a.png")
    pf.record("b.dds", operation="manual")
    assert pf.rebuildable() == {"a.dds": "art/a.png"}


def test_base_game_fingerprint_detects_a_different_install(tmp_path):
    a = tmp_path / "one" / "ds_add" / "inifiles"
    a.mkdir(parents=True)
    (a / "items.ini").write_bytes(b"[i]\r\n")
    stock_a = vfsmod.from_extracted(str(tmp_path / "one"))

    pf = ProjectFile()
    pf.record_base_game(stock_a)
    assert pf.base_game_matches(stock_a) is True

    (a / "extra.ini").write_bytes(b"[e]\r\n")
    stock_b = vfsmod.from_extracted(str(tmp_path / "one"))
    assert pf.base_game_matches(stock_b) is False


def test_base_game_unknown_when_never_recorded(tmp_path):
    (tmp_path / "ds_add").mkdir(parents=True)
    assert ProjectFile().base_game_matches(vfsmod.from_extracted(str(tmp_path))) is None


def test_project_file_rejects_a_newer_schema(mod):
    from dsotools.errors import ProjectError

    with open(os.path.join(mod.root, ".dsoproj"), "w") as fh:
        json.dump({"schema": 99}, fh)
    with pytest.raises(ProjectError):
        ProjectFile.load(mod.root)


def test_deploy_records_result_hashes(mod):
    pf = ProjectFile()
    mod.deploy({"inifiles/X.ini": b"[x]\r\n"}, project=pf)
    assert pf.provenance_of("inifiles/X.ini")["result_sha1"]


# --------------------------------------------------------------------------
# encoding preservation: what a replacement keeps from the asset it replaces
# --------------------------------------------------------------------------


def _bmpres_page(width=64, height=64, colour=(10, 20, 30, 255)):
    """A BMPRES page, i.e. the encoding TexPage_0_4.aim actually ships in."""
    from PIL import Image

    img = Image.new("RGBA", (width, height), colour)
    return aim.parse(
        aim.from_image(img, flags=18, footer_extra=(0, 0, 1), encoding="BMPRES")
    )


def test_from_image_like_keeps_the_encoding():
    """A BMPRES page rewritten as IMTC32 is a file the game does not display.

    This is what shipped in a real mod and did nothing in game.
    """
    if not HAVE_PIL:
        pytest.skip("Pillow not installed")
    src = _bmpres_page()

    rebuilt = aim.parse(aim.from_image_like(src, aim.to_image(src)))

    assert src.tiles[0].encoding.strip() == "BMPRES"
    assert rebuilt.tiles[0].encoding.strip() == "BMPRES"


def test_from_image_like_keeps_flags_and_footer_extra():
    """Every shipped page carries footer_extra (0,0,1); the default is (0,0,0).

    Losing it makes diff-against-stock report a change in bytes nobody edited.
    """
    if not HAVE_PIL:
        pytest.skip("Pillow not installed")
    src = _bmpres_page()

    rebuilt = aim.parse(aim.from_image_like(src, aim.to_image(src)))

    assert rebuilt.footer_extra == (0, 0, 1)
    assert rebuilt.flags == src.flags


def test_from_image_like_round_trips_byte_identically():
    """An untouched page must re-emit exactly, or diff-vs-stock lies."""
    if not HAVE_PIL:
        pytest.skip("Pillow not installed")
    for encoding in ("IMTC32", "BMPRES"):
        from PIL import Image

        original = aim.from_image(
            Image.new("RGBA", (64, 64), (7, 8, 9, 255)),
            flags=18,
            footer_extra=(0, 0, 1),
            encoding=encoding,
        )
        src = aim.parse(original)

        assert aim.from_image_like(src, aim.to_image(src)) == original, encoding


def test_from_image_like_retiles_when_the_image_was_resized():
    """rescale() changes the size, so the old grid would crop the new image."""
    if not HAVE_PIL:
        pytest.skip("Pillow not installed")
    src = _bmpres_page(64, 64)
    bigger = aim.to_image(src).resize((128, 128))

    rebuilt = aim.parse(aim.from_image_like(src, bigger))

    assert aim.to_image(rebuilt).size == (128, 128)
    assert rebuilt.footer_extra == (0, 0, 1)      # still preserved


def test_from_image_like_refuses_an_encoding_it_cannot_write():
    """A refusal you can read beats a file the engine ignores."""
    if not HAVE_PIL:
        pytest.skip("Pillow not installed")
    src = _bmpres_page()
    src.tiles[0].encoding = "IMJPG24A"

    with pytest.raises(aim.UnsupportedAim) as exc:
        aim.from_image_like(src, aim.to_image(src))

    assert "IMJPG24A" in str(exc.value)


def test_atlas_save_preserves_the_page_encoding(atlas_world):
    """The whole chain: AtlasPage must not re-encode the page it edits.

    Takes only ``atlas_world`` -- asking for ``tmp_path`` alongside it gets a
    *second* temporary directory under tools/offline_test_runner.py, which
    caches only session-scoped fixtures.
    """
    import os

    from PIL import Image

    from dsotools.edit.atlas import AtlasPage

    # rewrite the fixture's page as BMPRES so there is something to preserve
    layer = next(ly for ly in atlas_world.layers if ly.name.endswith("ds_interface"))
    page_file = os.path.join(layer.root, "images", "Page.aim")
    src_img = Image.new("RGBA", (64, 64), (10, 20, 30, 255))
    with open(page_file, "wb") as fh:
        fh.write(
            aim.from_image(src_img, flags=18, footer_extra=(0, 0, 1),
                           encoding="BMPRES")
        )
    world = vfsmod.from_extracted(os.path.dirname(layer.root))
    page = AtlasPage.open(world, "scripts/TexPage1.tex")

    page.replace("Alpha", Image.new("RGBA", (16, 16), (255, 0, 0, 255)))
    written = aim.parse(page.save()["images/Page.aim"])

    assert written.tiles[0].encoding.strip() == "BMPRES"
    assert written.footer_extra == (0, 0, 1)


def test_from_image_like_recodes_when_given_a_fallback():
    """IMJPG24A pages must be editable, not permanently read-only.

    The engine reads the codec from the chunk tag rather than the filename, so
    a page named TexPage_1_3.aim holding IMTC32 loads.
    """
    if not HAVE_PIL:
        pytest.skip("Pillow not installed")
    src = _bmpres_page()
    src.tiles[0].encoding = "IMJPG24A"

    rebuilt = aim.parse(
        aim.from_image_like(src, aim.to_image(src), fallback=aim.FALLBACK_ENCODING)
    )

    assert rebuilt.tiles[0].encoding.strip() == "IMTC32"
    assert rebuilt.footer_extra == (0, 0, 1)      # everything else still kept


def test_from_image_like_still_refuses_without_a_fallback():
    """Substituting a format silently is the bug this guards against."""
    if not HAVE_PIL:
        pytest.skip("Pillow not installed")
    src = _bmpres_page()
    src.tiles[0].encoding = "IMJPG24A"

    with pytest.raises(aim.UnsupportedAim):
        aim.from_image_like(src, aim.to_image(src))


def test_a_writable_page_is_never_recoded(atlas_world):
    """recoded_to must stay None for the eight pages we can write exactly."""
    from dsotools.edit.atlas import AtlasPage

    page = AtlasPage.open(atlas_world, "scripts/TexPage1.tex")

    assert page.page_encoding == "IMTC32"
    assert page.recoded_to is None


def test_an_unwritable_page_reports_what_it_will_become(atlas_world):
    from dsotools.edit.atlas import AtlasPage

    page = AtlasPage.open(atlas_world, "scripts/TexPage1.tex")
    page.source.tiles[0].encoding = "IMJPG24A"

    assert page.page_encoding == "IMJPG24A"
    assert page.recoded_to == "IMTC32"


def test_saving_an_unwritable_page_produces_a_loadable_file(atlas_world):
    """The end of the chain: edit an IMJPG24A page and get usable bytes."""
    from PIL import Image

    from dsotools.edit.atlas import AtlasPage

    page = AtlasPage.open(atlas_world, "scripts/TexPage1.tex")
    page.source.tiles[0].encoding = "IMJPG24A"

    page.replace("Alpha", Image.new("RGBA", (16, 16), (255, 0, 0, 255)))
    written = aim.parse(page.save()["images/Page.aim"])

    assert written.tiles[0].encoding.strip() == "IMTC32"
    assert aim.to_image(written).getpixel((2, 2))[:3] == (255, 0, 0)


# --------------------------------------------------------------------------
# the two sizes inside a .anim
#
# Read as one size written twice for a long time.  They are the size a drawable
# is DRAWN at and the size of its SOURCE IMAGE, and 57 shipped drawables differ
# on purpose because they are stretched nine-slice frames.
# --------------------------------------------------------------------------


def _stretched_anim(source, drawn, source_size):
    b = bytearray(_anim(source, *drawn))
    struct.pack_into("<I", b, anim.OFF_WIDTH2, source_size[0])
    struct.pack_into("<I", b, anim.OFF_HEIGHT2, source_size[1])
    return bytes(b)


def test_the_two_sizes_are_separate_fields():
    a = anim.parse(_stretched_anim(r"images\FrameTL.aim", (511, 215), (3, 3)))
    assert a.size == (511, 215)
    assert a.source_size == (3, 3)
    assert a.stretched


def test_set_size_no_longer_clobbers_the_source_size():
    """It used to write both, which collapsed a frame onto its corner tile."""
    a = anim.parse(_stretched_anim(r"images\FrameTL.aim", (511, 215), (3, 3)))
    a.set_size(600, 300)
    assert a.size == (600, 300)
    assert a.source_size == (3, 3)        # untouched

    a.set_source_size(6, 6)
    assert a.source_size == (6, 6)
    assert a.size == (600, 300)           # and the reverse


def test_an_ordinary_drawable_is_not_stretched():
    a = anim.parse(_anim(r"images\Auftraege.aim", 27, 38))
    assert a.source_size == a.size
    assert not a.stretched


def test_rescale_keeps_a_stretched_frame_in_proportion(atlas_world):
    """The corner tile grows with the page; the frame grows with it.

    Writing the rectangle into both fields -- which is what used to happen --
    turned a 511x215 window into a 3x3 one.
    """
    from dsotools.edit.atlas import AtlasPage, have_pillow

    if not have_pillow():
        pytest.skip("needs Pillow")
    page = AtlasPage.open(atlas_world, "scripts/TexPage1.tex")
    target = page.anims["scripts/Alpha.anim"]
    # Make Alpha a stretched frame: drawn 10x the rectangle it names.
    rect = target.source_size
    target.set_size(rect[0] * 10, rect[1] * 10)

    page.rescale(2.0)

    assert target.source_size == (rect[0] * 2, rect[1] * 2)
    assert target.size == (rect[0] * 20, rect[1] * 20)
    assert target.stretched


def test_tex004_compares_the_source_size(atlas_world):
    """A stretched frame is not a mismatch, and used to be reported as one."""
    from dsotools.edit.atlas import AtlasPage, have_pillow

    if not have_pillow():
        pytest.skip("needs Pillow")
    page = AtlasPage.open(atlas_world, "scripts/TexPage1.tex")
    target = page.anims["scripts/Alpha.anim"]
    target.set_size(target.source_size[0] * 9, target.source_size[1] * 9)

    assert "scripts/Alpha.anim" not in {v for v, _, _ in page.anim_mismatches()}

    # Breaking the *source* size is still a mismatch.
    target.set_source_size(1, 1)
    assert "scripts/Alpha.anim" in {v for v, _, _ in page.anim_mismatches()}
