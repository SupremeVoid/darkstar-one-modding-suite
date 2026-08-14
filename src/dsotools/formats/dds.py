"""
DirectDraw Surface (``.dds``) container and S3TC/DXT block decoding.  v1.0

Darkstar One's 3D textures are ordinary DDS files -- ``DDS `` magic, ``DXT1`` or
``DXT5`` FourCC, mipmapped, typically 1024x1024 with 11 levels.  There is no
Ascaron wrapper here at all, which is why this module implements a public
Microsoft format rather than a reverse-engineered one.

Do not confuse this with the ``IMSLDXT*`` encodings inside ``.aim``: those are
S3TC blocks wrapped in Ascaron's SLD compressor and used on the UI side.  The
block decoders below are shared, so when ``.aim`` DXT support lands it should
call :func:`decode_dxt` rather than growing a second implementation.

DEPENDENCIES
------------
Header parsing is pure stdlib.  Block decoding uses numpy when available -- a
1024x1024 DXT5 surface is 65,536 blocks and the pure-Python path takes seconds
where the vectorised one takes milliseconds.  Both are kept, and they are tested
against each other, because the core of this package must stay dependency-free.

REFERENCES
----------
* DDS header:      learn.microsoft.com/windows/win32/direct3ddds/dds-header
* S3TC block math: the colour endpoint interpolation and the 1/3-2/3 vs 1/2
  distinction on DXT1 alpha are the two places naive implementations go wrong.
"""

from __future__ import annotations

import struct
from typing import List, Optional, Tuple

from ..errors import ParseError, UnsupportedFormat

VERSION = "1.0"

MAGIC = b"DDS "
_HEADER_SIZE = 124

# dwFlags
DDSD_MIPMAPCOUNT = 0x20000
# dwCaps2
DDSCAPS2_CUBEMAP = 0x200
# dwPixelFormat.dwFlags
DDPF_FOURCC = 0x4
DDPF_RGB = 0x40
DDPF_ALPHAPIXELS = 0x1

_BLOCK_BYTES = {"DXT1": 8, "DXT2": 16, "DXT3": 16, "DXT4": 16, "DXT5": 16}

try:  # pragma: no cover - availability varies
    import numpy as _np
except ImportError:  # pragma: no cover
    _np = None


class DdsSurface:
    """One mip level: RGBA8 pixels plus its dimensions."""

    __slots__ = ("width", "height", "rgba")

    def __init__(self, width: int, height: int, rgba: bytes) -> None:
        self.width = width
        self.height = height
        self.rgba = rgba

    def __repr__(self) -> str:  # pragma: no cover
        return f"<DdsSurface {self.width}x{self.height}>"


class DdsImage:
    """A parsed DDS file.

    Mip payloads are kept as raw bytes and decoded on demand -- a 1.4 MB texture
    with 11 levels costs nothing to open, and the app opens thousands of them
    while indexing.  Call :meth:`surface` for the pixels you actually need.
    """

    def __init__(
        self,
        *,
        width: int,
        height: int,
        fourcc: Optional[str],
        mip_count: int,
        levels: List[bytes],
        rgb_bit_count: int = 0,
        masks: Tuple[int, int, int, int] = (0, 0, 0, 0),
        path: Optional[str] = None,
        faces: Optional[List[List[bytes]]] = None,
    ) -> None:
        self.width = width
        self.height = height
        self.fourcc = fourcc
        self.mip_count = mip_count
        self.levels = levels
        self.rgb_bit_count = rgb_bit_count
        self.masks = masks
        self.path = path
        #: Cubemaps carry 6 faces, each with its own mip chain.  ``levels`` is
        #: face 0 so the common single-surface path stays simple; the
        #: environment maps (``defaultspace.dds``) are the reason this exists.
        self.faces = faces if faces is not None else [levels]

    @property
    def compressed(self) -> bool:
        return self.fourcc is not None

    def level_size(self, level: int) -> Tuple[int, int]:
        return max(1, self.width >> level), max(1, self.height >> level)

    def surface(self, level: int = 0) -> DdsSurface:
        """Decode one mip level to RGBA8."""
        if not 0 <= level < len(self.levels):
            raise IndexError(f"mip level {level} out of range (have {len(self.levels)})")
        w, h = self.level_size(level)
        data = self.levels[level]
        if self.fourcc:
            rgba = decode_dxt(data, w, h, self.fourcc)
        else:
            rgba = _decode_uncompressed(data, w, h, self.rgb_bit_count, self.masks)
        return DdsSurface(w, h, rgba)

    def to_image(self, level: int = 0):
        """Return a Pillow ``Image``.  Requires the ``image`` extra."""
        try:
            from PIL import Image
        except ImportError:  # pragma: no cover
            raise UnsupportedFormat(
                "Pillow is required for DDS -> Image; install dsotools[image]"
            ) from None
        s = self.surface(level)
        return Image.frombytes("RGBA", (s.width, s.height), s.rgba)

    @property
    def is_cubemap(self) -> bool:
        return len(self.faces) > 1

    def surface_of_face(self, face: int, level: int = 0) -> DdsSurface:
        """Decode one mip level of one cubemap face."""
        chain = self.faces[face]
        w, h = self.level_size(level)
        data = chain[level]
        rgba = (
            decode_dxt(data, w, h, self.fourcc)
            if self.fourcc
            else _decode_uncompressed(data, w, h, self.rgb_bit_count, self.masks)
        )
        return DdsSurface(w, h, rgba)

    def describe(self) -> str:
        kind = self.fourcc or f"RGB{self.rgb_bit_count}"
        cube = f", cubemap {len(self.faces)} faces" if self.is_cubemap else ""
        return f"{self.width}x{self.height} {kind}, {self.mip_count} mip(s){cube}"

    def __repr__(self) -> str:  # pragma: no cover
        return f"<DdsImage {self.describe()}>"


def parse(data: bytes, *, path: Optional[str] = None) -> DdsImage:
    """Parse a DDS file.  Raises :class:`ParseError` / :class:`UnsupportedFormat`."""
    if len(data) < 4 + _HEADER_SIZE:
        raise ParseError("file too short to be a DDS", path=path)
    if data[:4] != MAGIC:
        raise ParseError(f"bad magic {data[:4]!r}, expected {MAGIC!r}", path=path)

    (
        size,
        flags,
        height,
        width,
        _pitch,
        _depth,
        mip_count,
    ) = struct.unpack_from("<7I", data, 4)
    if size != _HEADER_SIZE:
        raise ParseError(f"header size {size}, expected {_HEADER_SIZE}", path=path, offset=4)

    # ddspf sits at header-relative offset 72 (7 DWORDs + dwReserved1[11]).
    pf_off = 4 + 72
    pf_size, pf_flags = struct.unpack_from("<2I", data, pf_off)
    fourcc_raw = data[pf_off + 8 : pf_off + 12]
    rgb_bit_count = struct.unpack_from("<I", data, pf_off + 12)[0]
    masks = struct.unpack_from("<4I", data, pf_off + 16)
    if pf_size != 32:
        raise ParseError(f"pixel-format size {pf_size}, expected 32", path=path, offset=pf_off)

    caps2 = struct.unpack_from("<I", data, 4 + 108)[0]
    cubemap = bool(caps2 & DDSCAPS2_CUBEMAP)
    faces = bin(caps2 & 0xFC00).count("1") if cubemap else 1
    faces = faces or 1

    if not (flags & DDSD_MIPMAPCOUNT) or mip_count == 0:
        mip_count = 1

    fourcc: Optional[str] = None
    if pf_flags & DDPF_FOURCC:
        fourcc = fourcc_raw.decode("ascii", "replace")
        if fourcc not in _BLOCK_BYTES:
            raise UnsupportedFormat(
                f"unsupported DDS FourCC {fourcc!r}; only DXT1/2/3/4/5 are decoded",
                path=path,
            )
    elif not (pf_flags & DDPF_RGB):
        raise UnsupportedFormat(
            f"DDS pixel format flags 0x{pf_flags:x} is neither FourCC nor RGB", path=path
        )

    off = 4 + _HEADER_SIZE
    all_faces: List[List[bytes]] = []
    for _face in range(faces):
        levels: List[bytes] = []
        for level in range(mip_count):
            w = max(1, width >> level)
            h = max(1, height >> level)
            if fourcc:
                nbytes = max(1, (w + 3) // 4) * max(1, (h + 3) // 4) * _BLOCK_BYTES[fourcc]
            else:
                nbytes = w * h * (rgb_bit_count // 8)
            if off + nbytes > len(data):
                # Truncated mip chain: keep what we have rather than refusing
                # the file.  Stock data is well-formed, but a modder's export
                # tool may not be, and a usable base level beats an exception.
                break
            levels.append(data[off : off + nbytes])
            off += nbytes
        if not levels:
            break
        all_faces.append(levels)
    if not all_faces:
        raise ParseError("no complete mip level present", path=path, offset=4 + _HEADER_SIZE)
    levels = all_faces[0]

    return DdsImage(
        width=width,
        height=height,
        fourcc=fourcc,
        mip_count=len(levels),
        levels=levels,
        rgb_bit_count=rgb_bit_count,
        masks=masks,
        path=path,
        faces=all_faces,
    )


# --------------------------------------------------------------------------
# block decoding
# --------------------------------------------------------------------------


def decode_dxt(data: bytes, width: int, height: int, fourcc: str) -> bytes:
    """Decode S3TC blocks to RGBA8.  Uses numpy when importable."""
    if fourcc not in _BLOCK_BYTES:
        raise UnsupportedFormat(f"unsupported block format {fourcc!r}")
    if _np is not None:
        return _decode_dxt_numpy(data, width, height, fourcc)
    return _decode_dxt_python(data, width, height, fourcc)


def _expand565(c: int) -> Tuple[int, int, int]:
    r = (c >> 11) & 0x1F
    g = (c >> 5) & 0x3F
    b = c & 0x1F
    return (r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2)


def _decode_dxt_python(data: bytes, width: int, height: int, fourcc: str) -> bytes:
    bw = max(1, (width + 3) // 4)
    bh = max(1, (height + 3) // 4)
    stride = _BLOCK_BYTES[fourcc]
    out = bytearray(width * height * 4)
    dxt1 = fourcc == "DXT1"
    for by in range(bh):
        for bx in range(bw):
            off = (by * bw + bx) * stride
            block = data[off : off + stride]
            if len(block) < stride:
                break
            if dxt1:
                alpha = None
                colour = block
            elif fourcc in ("DXT2", "DXT3"):
                alpha = _dxt3_alpha(block[:8])
                colour = block[8:]
            else:
                alpha = _dxt5_alpha(block[:8])
                colour = block[8:]

            c0, c1 = struct.unpack_from("<2H", colour, 0)
            bits = struct.unpack_from("<I", colour, 4)[0]
            r0, g0, b0 = _expand565(c0)
            r1, g1, b1 = _expand565(c1)
            # DXT1 encodes 1-bit alpha by c0 <= c1; the third colour is then a
            # 1/2 blend and the fourth is transparent black.  Getting this wrong
            # is the classic "why are the edges wrong" bug.
            punch = dxt1 and c0 <= c1
            if punch:
                palette = [
                    (r0, g0, b0, 255),
                    (r1, g1, b1, 255),
                    ((r0 + r1) // 2, (g0 + g1) // 2, (b0 + b1) // 2, 255),
                    (0, 0, 0, 0),
                ]
            else:
                palette = [
                    (r0, g0, b0, 255),
                    (r1, g1, b1, 255),
                    ((2 * r0 + r1) // 3, (2 * g0 + g1) // 3, (2 * b0 + b1) // 3, 255),
                    ((r0 + 2 * r1) // 3, (g0 + 2 * g1) // 3, (b0 + 2 * b1) // 3, 255),
                ]
            for py in range(4):
                y = by * 4 + py
                if y >= height:
                    break
                for px in range(4):
                    x = bx * 4 + px
                    if x >= width:
                        break
                    idx = (bits >> (2 * (py * 4 + px))) & 3
                    r, g, b, a = palette[idx]
                    if alpha is not None:
                        a = alpha[py * 4 + px]
                    o = (y * width + x) * 4
                    out[o] = r
                    out[o + 1] = g
                    out[o + 2] = b
                    out[o + 3] = a
    return bytes(out)


def _dxt3_alpha(block: bytes) -> List[int]:
    out = []
    for i in range(8):
        v = block[i]
        lo, hi = v & 0xF, v >> 4
        out.append(lo * 17)
        out.append(hi * 17)
    return out


def dxt5_alpha_table(a0: int, a1: int) -> List[int]:
    """The eight alpha values a DXT5 block's 3-bit indices select from.

    Per the S3TC specification:

        a0 > a1   ->  a2..a7 = ((6-i)*a0 + (1+i)*a1) / 7      six interpolated
        a0 <= a1  ->  a2..a5 = ((4-i)*a0 + (1+i)*a1) / 5      four interpolated
                      a6 = 0, a7 = 255                        explicit endpoints

    Exposed and named because getting the numerator wrong here is silent: the
    weights still look plausible, the image still decodes, and only the extreme
    case overflows.  An earlier version used ``(7-i)`` and ``(5-i)``, which
    yields ``8*255/7 = 291`` for a fully-opaque block -- out of range, and wrong
    by a shade everywhere else.  Both the pure-Python and numpy paths carried
    the same error, so cross-checking them proved only that the typo had been
    copied faithfully.  Tests now assert this table against values computed by
    hand from the spec.
    """
    if a0 > a1:
        return [a0, a1] + [((6 - i) * a0 + (1 + i) * a1) // 7 for i in range(6)]
    return [a0, a1] + [((4 - i) * a0 + (1 + i) * a1) // 5 for i in range(4)] + [0, 255]


def _dxt5_alpha(block: bytes) -> List[int]:
    table = dxt5_alpha_table(block[0], block[1])
    bits = int.from_bytes(block[2:8], "little")
    return [table[(bits >> (3 * i)) & 7] for i in range(16)]


def _decode_dxt_numpy(data: bytes, width: int, height: int, fourcc: str) -> bytes:
    np = _np
    stride = _BLOCK_BYTES[fourcc]
    bw = max(1, (width + 3) // 4)
    bh = max(1, (height + 3) // 4)
    need = bw * bh * stride
    if len(data) < need:
        data = data + b"\x00" * (need - len(data))
    raw = np.frombuffer(data[:need], dtype=np.uint8).reshape(bh * bw, stride)

    colour = raw[:, stride - 8 :]
    c = colour.view(np.uint8)
    c0 = c[:, 0].astype(np.uint16) | (c[:, 1].astype(np.uint16) << 8)
    c1 = c[:, 2].astype(np.uint16) | (c[:, 3].astype(np.uint16) << 8)
    bits = (
        c[:, 4].astype(np.uint32)
        | (c[:, 5].astype(np.uint32) << 8)
        | (c[:, 6].astype(np.uint32) << 16)
        | (c[:, 7].astype(np.uint32) << 24)
    )

    def expand(v):
        r = ((v >> 11) & 0x1F).astype(np.uint16)
        g = ((v >> 5) & 0x3F).astype(np.uint16)
        b = (v & 0x1F).astype(np.uint16)
        return (
            ((r << 3) | (r >> 2)).astype(np.uint16),
            ((g << 2) | (g >> 4)).astype(np.uint16),
            ((b << 3) | (b >> 2)).astype(np.uint16),
        )

    r0, g0, b0 = expand(c0)
    r1, g1, b1 = expand(c1)
    n = raw.shape[0]
    pal = np.zeros((n, 4, 4), dtype=np.uint16)
    pal[:, 0, 0], pal[:, 0, 1], pal[:, 0, 2], pal[:, 0, 3] = r0, g0, b0, 255
    pal[:, 1, 0], pal[:, 1, 1], pal[:, 1, 2], pal[:, 1, 3] = r1, g1, b1, 255
    third = (2 * pal[:, 0, :3].astype(np.uint32) + pal[:, 1, :3]) // 3
    fourth = (pal[:, 0, :3].astype(np.uint32) + 2 * pal[:, 1, :3]) // 3
    half = (pal[:, 0, :3].astype(np.uint32) + pal[:, 1, :3]) // 2
    punch = (c0 <= c1) if fourcc == "DXT1" else np.zeros(n, dtype=bool)
    pal[:, 2, :3] = np.where(punch[:, None], half, third)
    pal[:, 2, 3] = 255
    pal[:, 3, :3] = np.where(punch[:, None], 0, fourth)
    pal[:, 3, 3] = np.where(punch, 0, 255)

    shift = (2 * np.arange(16, dtype=np.uint32))[None, :]
    idx = ((bits[:, None] >> shift) & 3).astype(np.intp)
    texels = np.take_along_axis(pal, idx[:, :, None].repeat(4, axis=2), axis=1)

    if fourcc in ("DXT2", "DXT3"):
        a = raw[:, :8]
        nib = np.empty((n, 16), dtype=np.uint16)
        nib[:, 0::2] = (a & 0x0F).astype(np.uint16) * 17
        nib[:, 1::2] = (a >> 4).astype(np.uint16) * 17
        texels[:, :, 3] = nib
    elif fourcc in ("DXT4", "DXT5"):
        a0 = raw[:, 0].astype(np.int32)
        a1 = raw[:, 1].astype(np.int32)
        table = np.zeros((n, 8), dtype=np.int32)
        table[:, 0], table[:, 1] = a0, a1
        gt = a0 > a1
        # Weights must match dxt5_alpha_table() exactly -- see its docstring for
        # why these two loops are the easiest place in this file to be wrong.
        for i in range(6):
            table[:, 2 + i] = np.where(gt, ((6 - i) * a0 + (1 + i) * a1) // 7, 0)
        for i in range(4):
            table[:, 2 + i] = np.where(gt, table[:, 2 + i], ((4 - i) * a0 + (1 + i) * a1) // 5)
        table[:, 6] = np.where(gt, table[:, 6], 0)
        table[:, 7] = np.where(gt, table[:, 7], 255)
        abits = np.zeros(n, dtype=np.uint64)
        for i in range(6):
            abits |= raw[:, 2 + i].astype(np.uint64) << np.uint64(8 * i)
        ashift = (3 * np.arange(16, dtype=np.uint64))[None, :]
        aidx = ((abits[:, None] >> ashift) & np.uint64(7)).astype(np.intp)
        texels[:, :, 3] = np.take_along_axis(table, aidx, axis=1).astype(np.uint16)

    texels = texels.astype(np.uint8).reshape(bh, bw, 4, 4, 4)
    img = texels.transpose(0, 2, 1, 3, 4).reshape(bh * 4, bw * 4, 4)
    return img[:height, :width].tobytes()


#: Uncompressed depths this decodes.  Anything else is refused by name rather
#: than guessed at.
_RGB_BIT_COUNTS = (8, 16, 24, 32)


def _channel(mask: int) -> Tuple[int, int]:
    """``(shift, maximum)`` for one channel mask.  ``(0, 0)`` if it is absent."""
    if not mask:
        return 0, 0
    shift = (mask & -mask).bit_length() - 1
    return shift, mask >> shift


def _decode_uncompressed(
    data: bytes, width: int, height: int, bit_count: int, masks: Tuple[int, int, int, int]
) -> bytes:
    """Decode a mask-described uncompressed surface to RGBA8.

    **A channel is as wide as its mask says**, which is the part the first
    version got wrong.  It shifted each channel into place but never scaled it,
    which is correct only while every mask happens to be 8 bits -- true of the
    24- and 32-bpp formats it accepted, and false of every packed 16-bpp one.
    Three stock textures are ``A4R4G4B4`` (``debris_a_nrm``, ``hunter_g_0_nrm``
    and ``ObjectFieldScripts`` debris), so their channels are 4 bits and land in
    0..15; written out unscaled they would be a near-black image rather than a
    refusal, which is worse than the refusal that was there before.

    Scaling is ``v * 255 // max``: 0 stays 0, the maximum stays 255, and for a
    4-bit channel it is exactly the bit replication :func:`_expand565` already
    uses.  An 8-bit channel has ``max == 255`` and is returned unchanged, so
    the 24/32-bpp files that worked before decode byte-for-byte as they did.
    """
    if bit_count not in _RGB_BIT_COUNTS:
        raise UnsupportedFormat(f"uncompressed DDS with {bit_count} bpp is not decoded")
    bpp = bit_count // 8
    (rs, rmax), (gs, gmax), (bs, bmax), (as_, amax) = (_channel(m) for m in masks)
    rmask, gmask, bmask, amask = masks

    out = bytearray(width * height * 4)
    for i in range(width * height):
        px = int.from_bytes(data[i * bpp : i * bpp + bpp], "little")
        o = i * 4
        out[o] = ((px & rmask) >> rs) * 255 // rmax if rmax else 0
        out[o + 1] = ((px & gmask) >> gs) * 255 // gmax if gmax else 0
        out[o + 2] = ((px & bmask) >> bs) * 255 // bmax if bmax else 0
        # No alpha channel means opaque, not transparent.
        out[o + 3] = ((px & amask) >> as_) * 255 // amax if amax else 255
    return bytes(out)


def is_dds(data: bytes) -> bool:
    return data[:4] == MAGIC


__all__ = [
    "VERSION",
    "DdsImage",
    "DdsSurface",
    "parse",
    "decode_dxt",
    "dxt5_alpha_table",
    "is_dds",
]
