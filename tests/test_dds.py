"""
DDS container and S3TC block decoding.

The numpy/pure-Python cross-check is useful but **weaker than it looks**: both
paths were written by the same hand from the same formula, so they agree just as
readily when that formula is wrong.  It caught nothing when the DXT5 alpha
weights were off by one in both -- see ``test_dxt5_alpha_table_matches_spec``,
which compares against values computed outside this codebase.

Cross-checks verify transcription.  Only external references verify maths.
"""

from __future__ import annotations

import struct

import pytest

from conftest import collect
from dsotools.errors import ParseError, UnsupportedFormat
from dsotools.formats import dds


def _dds_header(width, height, fourcc=b"DXT1", mips=1, caps2=0):
    h = bytearray(4 + 124)
    h[0:4] = b"DDS "
    struct.pack_into("<7I", h, 4, 124, dds.DDSD_MIPMAPCOUNT, height, width, 0, 0, mips)
    pf = 4 + 72
    struct.pack_into("<2I", h, pf, 32, dds.DDPF_FOURCC)
    h[pf + 8 : pf + 12] = fourcc
    struct.pack_into("<I", h, 4 + 108, caps2)
    return bytes(h)


# a single 4x4 block, opaque 4-colour mode (c0 > c1)
BLOCK_OPAQUE = struct.pack("<HHI", 0xFFFF, 0x0000, 0b11100100)
# c0 <= c1 selects DXT1's 1-bit-alpha mode: index 3 is transparent black
BLOCK_PUNCH = struct.pack("<HHI", 0x0000, 0xFFFF, 0b11100100)


def test_parse_minimal_dxt1():
    img = dds.parse(_dds_header(4, 4) + BLOCK_OPAQUE)
    assert (img.width, img.height, img.fourcc) == (4, 4, "DXT1")
    assert img.compressed
    assert not img.is_cubemap
    assert img.surface(0).rgba != b""


def test_rejects_bad_magic():
    with pytest.raises(ParseError):
        dds.parse(b"NOPE" + b"\x00" * 200)


def test_rejects_short_file():
    with pytest.raises(ParseError):
        dds.parse(b"DDS ")


def test_rejects_unknown_fourcc():
    with pytest.raises(UnsupportedFormat):
        dds.parse(_dds_header(4, 4, fourcc=b"ATI2") + b"\x00" * 16)


def test_dxt1_punchthrough_alpha():
    """c0 <= c1 makes index 3 fully transparent.

    Getting this backwards is the classic S3TC bug -- the image looks fine
    except that cut-out edges are wrong, which is easy to miss by eye.
    """
    img = dds.parse(_dds_header(4, 4) + BLOCK_PUNCH)
    alpha = list(img.surface(0).rgba[3::4])
    assert alpha[:4] == [255, 255, 255, 0]
    opaque = dds.parse(_dds_header(4, 4) + BLOCK_OPAQUE)
    assert list(opaque.surface(0).rgba[3::4])[:4] == [255] * 4


@pytest.mark.parametrize("block", [BLOCK_OPAQUE, BLOCK_PUNCH])
def test_numpy_and_python_decoders_agree_dxt1(block):
    if dds._np is None:
        pytest.skip("numpy not installed")
    assert dds._decode_dxt_numpy(block, 4, 4, "DXT1") == dds._decode_dxt_python(
        block, 4, 4, "DXT1"
    )


@pytest.mark.parametrize("a0,a1", [(255, 0), (0, 255), (128, 128), (200, 40)])
def test_numpy_and_python_decoders_agree_dxt5(a0, a1):
    """Both DXT5 alpha modes: a0 > a1 gives 8 interpolated levels, a0 <= a1
    gives 6 plus explicit 0 and 255."""
    if dds._np is None:
        pytest.skip("numpy not installed")
    alpha = bytes([a0, a1]) + bytes([0x12, 0x34, 0x56, 0x78, 0x9A, 0xBC])
    block = alpha + BLOCK_OPAQUE
    assert dds._decode_dxt_numpy(block, 4, 4, "DXT5") == dds._decode_dxt_python(
        block, 4, 4, "DXT5"
    )


def test_numpy_and_python_decoders_agree_dxt3():
    if dds._np is None:
        pytest.skip("numpy not installed")
    block = bytes(range(8)) + BLOCK_OPAQUE
    assert dds._decode_dxt_numpy(block, 4, 4, "DXT3") == dds._decode_dxt_python(
        block, 4, 4, "DXT3"
    )


def test_truncated_mip_chain_keeps_usable_levels():
    """A short file should still yield its base level.

    Stock data is well-formed, but a modder's export tool may not be, and
    refusing the file outright would hide a texture the user can plainly see.
    """
    img = dds.parse(_dds_header(4, 4, mips=3) + BLOCK_OPAQUE)
    assert len(img.levels) == 1
    assert img.surface(0).width == 4


def test_non_power_of_two_partial_block():
    """A 3x2 surface still occupies one full 4x4 block; output must be cropped."""
    img = dds.parse(_dds_header(3, 2) + BLOCK_OPAQUE)
    s = img.surface(0)
    assert (s.width, s.height) == (3, 2)
    assert len(s.rgba) == 3 * 2 * 4


@pytest.mark.parametrize(
    "a0,a1,expected",
    [
        # Hand-computed from the S3TC spec, NOT from this module's code.
        # a0 > a1: six interpolated values, weights (6-i):(1+i) over 7.
        (255, 0, [255, 0, 218, 182, 145, 109, 72, 36]),
        # a0 <= a1: four interpolated, weights (4-i):(1+i) over 5, then 0 and 255.
        (0, 255, [0, 255, 51, 102, 153, 204, 0, 255]),
        (200, 100, [200, 100, 185, 171, 157, 142, 128, 114]),
    ],
)
def test_dxt5_alpha_table_matches_spec(a0, a1, expected):
    """Independent check on the alpha weights.

    The numpy/pure-Python cross-check cannot catch an error here: both paths
    were written from the same formula and both were wrong the same way. Only
    values derived outside this codebase test the maths rather than the
    transcription.
    """
    assert dds.dxt5_alpha_table(a0, a1) == expected


def test_dxt5_alpha_never_leaves_byte_range():
    """The regression that exposed the bug: a fully-opaque block overflowed."""
    for a0 in (0, 1, 127, 254, 255):
        for a1 in (0, 1, 127, 254, 255):
            table = dds.dxt5_alpha_table(a0, a1)
            assert len(table) == 8
            assert all(0 <= v <= 255 for v in table), (a0, a1, table)


# --------------------------------------------------------------------------
# uncompressed surfaces
#
# The decoder shifted each channel into place but never scaled it, which is
# right only while every mask is 8 bits wide.  Three stock textures are
# A4R4G4B4, so their channels are 4 bits, and they were refused outright --
# which is why previewing them failed.
# --------------------------------------------------------------------------


def _rgb_header(width, height, bit_count, masks, mips=1):
    h = bytearray(4 + 124)
    h[0:4] = b"DDS "
    struct.pack_into("<7I", h, 4, 124, dds.DDSD_MIPMAPCOUNT, height, width, 0, 0, mips)
    pf = 4 + 72
    flags = dds.DDPF_RGB | (dds.DDPF_ALPHAPIXELS if masks[3] else 0)
    struct.pack_into("<2I", h, pf, 32, flags)
    struct.pack_into("<I", h, pf + 12, bit_count)
    struct.pack_into("<4I", h, pf + 16, *masks)
    return bytes(h)


#: The format the three stock `_nrm` files use.
A4R4G4B4 = (0x0F00, 0x00F0, 0x000F, 0xF000)


def test_a4r4g4b4_scales_each_channel_to_the_full_byte_range():
    """A 4-bit channel at its maximum is 255, not 15.

    Hand-computed, not taken from the decoder: 0xFA51 is A=15, R=10, G=5, B=1,
    and widening multiplies each by 17 (255 // 15).
    """
    px = struct.pack("<H", 0xFA51)
    img = dds.parse(_rgb_header(1, 1, 16, A4R4G4B4) + px)

    assert img.fourcc is None
    assert list(img.surface(0).rgba) == [10 * 17, 5 * 17, 1 * 17, 15 * 17]


def test_a4r4g4b4_endpoints_are_exactly_black_and_white():
    """0 must stay 0 and the maximum must reach 255, or every image is dim."""
    data = struct.pack("<HH", 0x0000, 0xFFFF)
    rgba = dds.parse(_rgb_header(2, 1, 16, A4R4G4B4) + data).surface(0).rgba
    assert list(rgba[:4]) == [0, 0, 0, 0]
    assert list(rgba[4:]) == [255, 255, 255, 255]


def test_eight_bit_channels_are_returned_unchanged():
    """The generalisation must not disturb the 24/32-bpp files that worked.

    A mask of 0xFF has a maximum of 255, so the scaling is the identity.
    """
    masks = (0x00FF0000, 0x0000FF00, 0x000000FF, 0xFF000000)
    px = struct.pack("<I", 0x8003C07F)
    rgba = dds.parse(_rgb_header(1, 1, 32, masks) + px).surface(0).rgba
    assert list(rgba) == [0x03, 0xC0, 0x7F, 0x80]


def test_a_missing_alpha_mask_means_opaque():
    masks = (0x00FF0000, 0x0000FF00, 0x000000FF, 0)
    px = struct.pack("<BBB", 0x11, 0x22, 0x33)
    rgba = dds.parse(_rgb_header(1, 1, 24, masks) + px).surface(0).rgba
    assert list(rgba) == [0x33, 0x22, 0x11, 255]


def test_an_undecodable_depth_is_refused_by_name():
    """A rule that cannot run must not look like a rule that passed."""
    with pytest.raises(UnsupportedFormat) as exc:
        dds.parse(_rgb_header(1, 1, 4, A4R4G4B4) + b"\x00").surface(0)
    assert "4 bpp" in str(exc.value)


@pytest.mark.corpus
def test_corpus_files_parse_and_decode(corpus):
    files = collect(corpus, "*.dds")
    if not files:
        pytest.skip("no .dds in corpus")
    for p in files:
        img = dds.parse(p.read_bytes(), path=str(p))
        # decode the smallest level: proves the whole path without the cost
        s = img.surface(len(img.levels) - 1)
        assert len(s.rgba) == s.width * s.height * 4


@pytest.mark.corpus
def test_corpus_decoders_agree(corpus):
    if dds._np is None:
        pytest.skip("numpy not installed")
    files = [p for p in collect(corpus, "*.dds")]
    checked = 0
    for p in files:
        img = dds.parse(p.read_bytes())
        if not img.fourcc:
            continue
        lvl = min(6, len(img.levels) - 1)
        w, h = img.level_size(lvl)
        assert dds._decode_dxt_numpy(img.levels[lvl], w, h, img.fourcc) == (
            dds._decode_dxt_python(img.levels[lvl], w, h, img.fourcc)
        )
        checked += 1
    if not checked:
        pytest.skip("no compressed .dds in corpus")
