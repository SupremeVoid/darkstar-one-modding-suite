"""
Reading the metadata of a WAV or MP3 without decoding it.

The sound database records ``Channels``, ``Freq`` and ``Duration`` for every
entry, and the engine reads them rather than asking the file.  So a mod that
declares a sound has to get them right, and the suite cannot ask a decoder:
:mod:`dsotools` never imports Qt, and shelling out to one would make adding a
sound depend on a media stack.

Both formats say what is needed in their first few dozen bytes.

WAV is trivial -- RIFF, then the ``fmt `` chunk gives rate and channels and the
``data`` chunk's size gives the length.

MP3 is the awkward one.  There is no header for the file, only for each frame,
so the length has to be worked out:

* a **Xing/Info** or **VBRI** tag in the first frame carries the exact frame
  count, and that is used when present -- it is what encoders write for VBR;
* otherwise the file is assumed constant-bitrate and the length comes from the
  payload size over the bitrate, which is exact for CBR.

Checked against a real decoder on the game's own files: every one agrees to
within 0.2%, and the CBR estimate is exact on most.  ``Duration`` in the
database is in **samples**, which is what :func:`probe` returns.
"""

from __future__ import annotations

import os
import struct
from typing import Optional

from ..errors import ParseError

VERSION = "1.0"

#: MPEG-1/2/2.5 layer III bitrates, keyed (version_is_mpeg1, bitrate_index).
_BITRATES_V1 = (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 0)
_BITRATES_V2 = (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160, 0)

#: Sample rates by (mpeg version id, rate index).
_RATES = {
    3: (44100, 48000, 32000),      # MPEG-1
    2: (22050, 24000, 16000),      # MPEG-2
    0: (11025, 12000, 8000),       # MPEG-2.5
}

#: Samples per frame: MPEG-1 layer III is 1152, MPEG-2/2.5 layer III is 576.
_FRAME_SAMPLES = {3: 1152, 2: 576, 0: 576}


class AudioInfo:
    """What a sound file says about itself."""

    __slots__ = ("kind", "channels", "frequency", "samples", "bytes")

    def __init__(self, kind, channels, frequency, samples, size):
        self.kind = kind                    # "wav" | "mp3"
        self.channels = channels
        self.frequency = frequency
        #: Length in samples -- the unit the database's ``Duration`` uses.
        self.samples = samples
        self.bytes = size

    @property
    def seconds(self) -> Optional[float]:
        if not self.samples or not self.frequency:
            return None
        return self.samples / self.frequency

    def as_attributes(self) -> dict:
        """The attributes a database entry needs, spelled as the format does."""
        out = {}
        if self.channels:
            out["Channels"] = str(self.channels)
        if self.samples:
            out["Duration"] = f":{self.samples}"
        if self.frequency:
            out["Freq"] = str(self.frequency)
        return out

    def __repr__(self) -> str:  # pragma: no cover
        return (f"<AudioInfo {self.kind} {self.frequency}Hz "
                f"{self.channels}ch {self.seconds:.2f}s>"
                if self.seconds else f"<AudioInfo {self.kind}>")


#: Uncompressed. Anything else stores fewer bytes than samples, so the byte
#: count cannot be divided into frames -- most of the game's effects are
#: WAVE_FORMAT_IMA_ADPCM (17), four bits a sample in 256-byte blocks.
_WAVE_PCM = (1, 3)      # PCM, IEEE float


def _probe_wav(data: bytes, size: int, path) -> AudioInfo:
    if len(data) < 12 or data[8:12] != b"WAVE":
        raise ParseError("not a RIFF/WAVE file", path=path)
    fmt = channels = frequency = None
    samples = fact = None
    bits = 0
    i = 12
    while i + 8 <= len(data):
        chunk, length = struct.unpack_from("<4sI", data, i)
        body = i + 8
        if chunk == b"fmt " and body + 16 <= len(data):
            fmt, channels, frequency, _bps, _align, bits = struct.unpack_from(
                "<HHIIHH", data, body)
        elif chunk == b"fact" and body + 4 <= len(data):
            # For a compressed format this is the authoritative sample count,
            # which is what the chunk exists for.
            fact = struct.unpack_from("<I", data, body)[0]
        elif chunk == b"data":
            if fmt in _WAVE_PCM:
                frame = max(1, (channels or 1) * max(1, bits // 8))
                samples = length // frame
            elif fact is not None:
                # The encoder that made these wrote `fact` as the total across
                # channels, and Ascaron's tool divided by them: on the game's
                # 14 stereo ADPCM effects `fact // channels` reproduces the
                # declared Duration exactly, and on the 400-odd mono ones the
                # division changes nothing. Matching the data beats arguing
                # with the encoder about what the spec meant.
                samples = fact // max(1, channels or 1)
            break
        i = body + length + (length & 1)          # chunks are word-aligned
    if not channels or not frequency:
        raise ParseError("WAVE file has no fmt chunk", path=path)
    return AudioInfo("wav", channels, frequency, samples if samples else fact, size)


def _frame_header(data: bytes, i: int):
    """Decode a frame header at ``i``, or ``None`` if it is not one."""
    if i + 4 > len(data) or data[i] != 0xFF or (data[i + 1] & 0xE0) != 0xE0:
        return None
    version = (data[i + 1] >> 3) & 0x03           # 3=MPEG-1, 2=MPEG-2, 0=2.5
    layer = (data[i + 1] >> 1) & 0x03             # 1 = layer III
    if version == 1 or layer != 1:
        return None
    bitrate_index = (data[i + 2] >> 4) & 0x0F
    rate_index = (data[i + 2] >> 2) & 0x03
    if bitrate_index in (0, 15) or rate_index == 3:
        return None
    table = _BITRATES_V1 if version == 3 else _BITRATES_V2
    bitrate = table[bitrate_index] * 1000
    frequency = _RATES[version][rate_index]
    padding = (data[i + 2] >> 1) & 1
    channels = 1 if ((data[i + 3] >> 6) & 0x03) == 3 else 2
    per_frame = _FRAME_SAMPLES[version]
    length = (per_frame // 8 * bitrate // frequency) + padding
    return version, frequency, channels, bitrate, per_frame, length


def _id3_size(data: bytes) -> int:
    """Length of a leading ID3v2 tag, which is not part of the audio."""
    if len(data) < 10 or data[:3] != b"ID3":
        return 0
    # A syncsafe integer: seven bits per byte.
    size = 0
    for b in data[6:10]:
        size = (size << 7) | (b & 0x7F)
    return size + 10


def _probe_mp3(data: bytes, size: int, path) -> AudioInfo:
    # The first frame is not necessarily at the start. Several of the game's
    # music tracks open with a long run of zero bytes -- 66_gameover_final has
    # no sync word until well into the file -- so the whole buffer is scanned
    # rather than a fixed window near the top.
    start = _id3_size(data)
    header = None
    for i in range(start, len(data) - 4):
        header = _frame_header(data, i)
        if header is not None:
            start = i
            break
    if header is None:
        raise ParseError("no MPEG audio frame found", path=path)
    version, frequency, channels, bitrate, per_frame, length = header

    # A Xing/Info or VBRI tag inside the first frame gives the exact count.
    frames = None
    window = data[start:start + length + 4]
    for tag in (b"Xing", b"Info"):
        at = window.find(tag)
        if at >= 0 and at + 12 <= len(window):
            flags = struct.unpack_from(">I", window, at + 4)[0]
            if flags & 0x1:
                frames = struct.unpack_from(">I", window, at + 8)[0]
            break
    else:
        at = window.find(b"VBRI")
        if at >= 0 and at + 18 <= len(window):
            frames = struct.unpack_from(">I", window, at + 14)[0]

    if frames:
        samples = frames * per_frame
    else:
        # Constant bitrate: the payload over the bitrate is exact.
        payload = size - start
        samples = int(payload * 8 / bitrate * frequency)
    return AudioInfo("mp3", channels, frequency, samples, size)


def probe(path: str) -> AudioInfo:
    """Read a sound file's channels, rate and length.  WAV and MP3 only."""
    size = os.path.getsize(path)
    with open(path, "rb") as handle:
        head = handle.read(1 << 16)
    if head[:4] == b"RIFF":
        return _probe_wav(head, size, path)
    # Anything else is tried as MP3 rather than sniffed on its first bytes: a
    # frame can start a long way in, and "no frame anywhere" is the honest test.
    try:
        return _probe_mp3(head, size, path)
    except ParseError:
        raise ParseError(
            f"{os.path.basename(path)} is neither WAV nor MP3; the engine "
            f"reads only those two", path=path) from None


def is_audio(path: str) -> bool:
    return path.lower().endswith((".wav", ".mp3"))


__all__ = ["VERSION", "AudioInfo", "probe", "is_audio"]
