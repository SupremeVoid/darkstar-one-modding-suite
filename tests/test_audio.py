"""
Reading WAV and MP3 metadata without a decoder.

The suite has to fill in ``Channels``, ``Freq`` and ``Duration`` when a mod
declares a sound, and it cannot ask a media stack: :mod:`dsotools` never
imports Qt. So the numbers come out of the file's own headers, and the check
that matters is the corpus one at the bottom -- the game's own database
declares all three for 442 sounds, and the prober has to reproduce every one.

Three shapes have to be handled, and only the first is obvious:

* PCM WAV, where the data size divides into frames;
* **IMA ADPCM WAV**, four bits a sample, where it does not -- most of the
  game's effects. The ``fact`` chunk carries the real count;
* MP3, which has no file header at all, only frame headers, and whose first
  frame is not always at byte zero.
"""

from __future__ import annotations

import pathlib
import struct

import pytest

from dsotools.errors import ParseError
from dsotools.formats import audio


def _wav(fmt=1, channels=2, rate=44100, bits=16, frames=1000, fact=None):
    data = b"\0" * (frames * channels * (bits // 8) if fmt == 1 else frames)
    chunks = struct.pack("<4sIHHIIHH", b"fmt ", 16, fmt, channels, rate,
                         rate * channels * bits // 8, channels * bits // 8, bits)
    if fact is not None:
        chunks += struct.pack("<4sII", b"fact", 4, fact)
    chunks += struct.pack("<4sI", b"data", len(data)) + data
    return b"RIFF" + struct.pack("<I", 4 + len(chunks)) + b"WAVE" + chunks


def _mp3_frame(pad=b""):
    """One MPEG-1 layer III frame header: 128 kbps, 44.1 kHz, stereo."""
    # 11111111 111 11 01 1 | 1001 00 0 0 | 00 ...  -> bitrate idx 9, rate idx 0
    return pad + bytes([0xFF, 0xFB, 0x90, 0x00]) + b"\0" * 413


def test_pcm_wav_reads_rate_channels_and_length(tmp_path):
    p = tmp_path / "a.wav"
    p.write_bytes(_wav(frames=44100))
    info = audio.probe(str(p))
    assert (info.kind, info.channels, info.frequency) == ("wav", 2, 44100)
    assert info.samples == 44100
    assert info.seconds == pytest.approx(1.0)


def test_mono_eight_bit_pcm_is_not_assumed_to_be_stereo_sixteen(tmp_path):
    p = tmp_path / "a.wav"
    p.write_bytes(_wav(channels=1, bits=8, rate=22050, frames=22050))
    info = audio.probe(str(p))
    assert (info.channels, info.frequency, info.samples) == (1, 22050, 22050)


def test_compressed_wav_trusts_the_fact_chunk(tmp_path):
    """ADPCM stores fewer bytes than samples, so the data size says nothing."""
    p = tmp_path / "a.wav"
    p.write_bytes(_wav(fmt=17, channels=1, bits=4, frames=51712, fact=101533))
    info = audio.probe(str(p))
    assert info.samples == 101533          # not the 51,712 bytes of payload


def test_stereo_compressed_wav_divides_fact_by_channels(tmp_path):
    """Matches what the game's own database records for its 14 stereo effects."""
    p = tmp_path / "a.wav"
    p.write_bytes(_wav(fmt=17, channels=2, bits=4, frames=1000, fact=111349))
    assert audio.probe(str(p)).samples == 55674


def test_a_wav_without_fmt_is_refused(tmp_path):
    p = tmp_path / "a.wav"
    p.write_bytes(b"RIFF" + struct.pack("<I", 4) + b"WAVE")
    with pytest.raises(ParseError):
        audio.probe(str(p))


def test_mp3_frame_header_gives_rate_and_channels(tmp_path):
    p = tmp_path / "a.mp3"
    p.write_bytes(_mp3_frame() * 40)
    info = audio.probe(str(p))
    assert (info.kind, info.channels, info.frequency) == ("mp3", 2, 44100)
    assert info.samples > 0


def test_an_mp3_whose_first_frame_is_not_at_the_start(tmp_path):
    """Several of the game's music tracks open with a run of zero bytes."""
    p = tmp_path / "a.mp3"
    p.write_bytes(_mp3_frame(pad=b"\0" * 5000) * 3)
    assert audio.probe(str(p)).frequency == 44100


def test_an_id3_tag_is_skipped(tmp_path):
    p = tmp_path / "a.mp3"
    tag = b"ID3\x04\x00\x00" + bytes([0, 0, 0x01, 0x00])      # syncsafe 128
    p.write_bytes(tag + b"\0" * 128 + _mp3_frame() * 3)
    assert audio.probe(str(p)).frequency == 44100


def test_something_that_is_neither_is_refused(tmp_path):
    p = tmp_path / "a.bin"
    p.write_bytes(b"not audio at all" * 100)
    with pytest.raises(ParseError):
        audio.probe(str(p))


def test_attributes_are_spelled_the_way_the_database_spells_them(tmp_path):
    p = tmp_path / "a.wav"
    p.write_bytes(_wav(channels=1, rate=22050, frames=11025))
    attrs = audio.probe(str(p)).as_attributes()
    assert attrs == {"Channels": "1", "Duration": ":11025", "Freq": "22050"}


def test_is_audio_is_by_extension():
    assert audio.is_audio("x.MP3") and audio.is_audio("x.wav")
    assert not audio.is_audio("x.ogg")


# --------------------------------------------------------------------------
# the real files
# --------------------------------------------------------------------------


def _installation():
    from dsotools import locate

    for candidate in locate.candidates():
        if candidate.has_exe:
            root = pathlib.Path(candidate.path)
            if (root / "KlangErzeugerDefault.xml").is_file():
                return root
    return None


def test_the_prober_reproduces_every_declared_value():
    """442 sounds, three declared numbers each, no decoder involved.

    This is the check the module exists to pass: the game's own tool wrote
    those numbers, so agreeing with all of them means a sound the suite adds
    will carry the same values Ascaron's would have.

    Skipped, never silently passed, when no installation is present.
    """
    from dsotools.formats import sounddb

    root = _installation()
    if root is None:
        pytest.skip("no Darkstar One installation on this machine")
    db = sounddb.parse((root / "KlangErzeugerDefault.xml").read_bytes())

    checked = 0
    wrong = []
    worst_mp3 = 0.0
    for entry in db.entries():
        full = root / entry.path().replace("/", "\\")
        if not full.is_file():
            continue
        info = audio.probe(str(full))
        checked += 1
        if info.frequency != entry.frequency:
            wrong.append(f"{entry.name}: {info.frequency} vs {entry.frequency} Hz")
            continue
        if entry.channels and info.channels != entry.channels:
            wrong.append(f"{entry.name}: {info.channels} vs {entry.channels} ch")
            continue
        if not entry.duration:
            continue
        # WAV length is read, so it is exact. MP3 length is *derived* -- from a
        # Xing frame count where there is one, otherwise from payload over
        # bitrate -- and encoder padding puts it a fraction out. Holding both
        # to one tolerance would either hide a WAV bug or fail on arithmetic
        # that is behaving exactly as designed.
        error = abs(info.samples - entry.duration) / entry.duration
        if info.kind == "wav":
            if info.samples != entry.duration:
                wrong.append(f"{entry.name}: {info.samples} vs {entry.duration}")
        else:
            worst_mp3 = max(worst_mp3, error)
            if error > 0.005:
                wrong.append(f"{entry.name}: {info.samples} vs {entry.duration} "
                             f"({error:.2%})")
    assert checked > 400, f"only {checked} files found"
    assert not wrong, wrong[:5]
    assert worst_mp3 < 0.005, f"worst MP3 estimate off by {worst_mp3:.2%}"
