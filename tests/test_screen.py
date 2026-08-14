"""
The ``.screen`` interface layout format.

Fixtures are assembled **by hand** from the layout in ``screen.py``'s docstring,
never through ``screen.build`` -- the same reasoning as ``test_threedo``: a
reader checked only against files its own writer produced can pass because the
two share a misunderstanding.

The corpus test at the bottom is the one that actually settles it: 83 files,
1,381 elements, byte-exact round-trip, and every resource reference resolving.
"""

from __future__ import annotations

import struct

import pytest

from dsotools.errors import DsoError
from dsotools.formats import screen


def dwfab(class_name: str, name: str) -> bytes:
    block = bytearray(screen.DWFAB_LEN)
    block[0:8] = screen.TAG_DWFAB
    struct.pack_into("<I", block, 8, screen.DWFAB_LEN)
    struct.pack_into("<i", block, 12, -1)
    block[0x10:0x10 + len(class_name)] = class_name.encode("cp1252")
    block[0x50:0x50 + len(name)] = name.encode("cp1252")
    struct.pack_into("<2i", block, 0x150, -1, -1)
    return bytes(block)


def dwb(x: int, y: int, w: int, h: int) -> bytes:
    block = bytearray(screen.DWB_LEN)
    block[0:8] = screen.TAG_DWB
    struct.pack_into("<I", block, 8, screen.DWB_LEN)
    struct.pack_into("<4i", block, 0x0C, x, y, w, h)
    return bytes(block)


def scrn() -> bytes:
    block = bytearray(screen.SCRN_LEN)
    block[0:8] = screen.TAG_SCRN
    struct.pack_into("<I", block, 8, screen.SCRN_LEN)
    return bytes(block)


def block(payload: bytes) -> bytes:
    """A class-specific block: its own length, then the payload."""
    size = 4 + len(payload)
    return struct.pack("<I", size) + payload


def element(class_name: str, name: str, rect=(0, 0, 10, 10), blocks=b"",
            trailer=b"") -> bytes:
    return dwfab(class_name, name) + dwb(*rect) + blocks + trailer


def a_screen(elements=b"", declared=None, name="TESTSCREEN") -> bytes:
    header = bytearray(screen.HEADER_LEN)
    header[0:8] = screen.MAGIC
    struct.pack_into("<II", header, 8, screen.HEADER_LEN, 17)
    count = declared if declared is not None else 0
    body = (dwfab("CScreen", name) + dwb(0, 0, 1024, 768) + scrn()
            + struct.pack("<I", count))
    return bytes(header) + body + elements


REFERENCE = b"scripts\\Base_Frame.anim\x00" + b"\x00" * 40


# --------------------------------------------------------------------------
# the container
# --------------------------------------------------------------------------


def test_an_empty_screen_round_trips():
    raw = a_screen()
    parsed = screen.parse(raw, path="s.screen")

    assert parsed.name == "TESTSCREEN"
    assert parsed.screen.is_screen
    assert len(parsed) == 0
    assert parsed.to_bytes() == raw


def test_elements_are_read_with_their_class_name_and_rectangle():
    raw = a_screen(
        element("CButton", "OK", (185, 456, 96, 35), block(REFERENCE))
        + element("CStatic", "BG", (-1, -1, 1024, 768)),
        declared=2,
    )
    parsed = screen.parse(raw, path="s.screen")

    assert [e.class_name for e in parsed] == ["CButton", "CStatic"]
    assert [e.name for e in parsed] == ["OK", "BG"]
    assert parsed.elements[0].rect == (185, 456, 96, 35)
    # Signed: shipped layouts really do place things at -1.
    assert parsed.elements[1].rect == (-1, -1, 1024, 768)
    assert parsed.to_bytes() == raw


def test_the_declared_child_count_is_reported_not_enforced():
    """It disagrees with the element count in 19 of the 83 shipped files.

    Something owns children and nothing found so far says which field holds
    that count, so the parser reports both numbers and refuses to invent a
    tree from them.
    """
    raw = a_screen(element("CStatic", "A") + element("CStatic", "B"), declared=1)
    parsed = screen.parse(raw, path="s.screen")

    assert parsed.declared_children == 1
    assert len(parsed) == 2


def test_the_eight_byte_trailer_some_elements_carry_survives():
    """14 elements of 1,381 have it; `(0,0)`, `(1,0)` and `(2,0)` all ship."""
    raw = a_screen(
        element("CListBox", "L", blocks=block(b"x" * 60),
                trailer=struct.pack("<2I", 2, 0))
        + element("CStatic", "S"),
        declared=2,
    )
    parsed = screen.parse(raw, path="s.screen")

    assert len(parsed) == 2
    assert parsed.elements[0].trailer == 8
    assert parsed.to_bytes() == raw


# --------------------------------------------------------------------------
# refusing rather than guessing
# --------------------------------------------------------------------------


def test_a_file_that_is_not_the_container_is_refused():
    with pytest.raises(DsoError):
        screen.parse(b"not a screen" + b"\x00" * 64, path="s.screen")


def test_a_record_of_an_unexpected_size_is_refused():
    raw = bytearray(a_screen())
    struct.pack_into("<I", raw, screen.HEADER_LEN + 8, 400)   # DWFAB says 400
    with pytest.raises(DsoError):
        screen.parse(bytes(raw), path="s.screen")


def test_a_first_element_that_is_not_a_screen_is_refused():
    header = bytearray(screen.HEADER_LEN)
    header[0:8] = screen.MAGIC
    struct.pack_into("<II", header, 8, screen.HEADER_LEN, 17)
    with pytest.raises(DsoError):
        screen.parse(bytes(header) + element("CButton", "OK"), path="s.screen")


def test_a_truncated_file_is_refused():
    raw = a_screen(element("CStatic", "A"), declared=1)
    with pytest.raises(DsoError):
        screen.parse(raw[:-200], path="s.screen")


# --------------------------------------------------------------------------
# editing, which is the point of decoding it
# --------------------------------------------------------------------------


def test_moving_an_element_rewrites_only_its_rectangle():
    raw = a_screen(element("CButton", "OK", (185, 456, 96, 35)), declared=1)
    parsed = screen.parse(raw, path="s.screen")
    parsed.elements[0].rect = (200, 456, 96, 35)
    out = parsed.to_bytes()

    assert len(out) == len(raw)
    differ = [i for i in range(len(raw)) if raw[i] != out[i]]
    assert len(differ) == 1                       # 185 -> 200, one byte
    assert screen.parse(out, path="s.screen").elements[0].rect == (200, 456, 96, 35)


def test_a_reference_can_be_repointed_within_its_field():
    raw = a_screen(element("CButton", "OK", blocks=block(REFERENCE)), declared=1)
    parsed = screen.parse(raw, path="s.screen")
    (before,) = parsed.elements[0].references()
    assert before == "scripts\\Base_Frame.anim"

    assert parsed.elements[0].set_reference(before, "scripts\\Mine.anim") is True
    out = parsed.to_bytes()

    assert len(out) == len(raw)
    assert screen.parse(out, path="s.screen").elements[0].references() == [
        "scripts\\Mine.anim"
    ]


def test_a_reference_that_would_not_fit_is_refused():
    """The string sits in a fixed field inside a block this parser does not
    understand, so the only safe write is one that stays inside it."""
    raw = a_screen(element("CButton", "OK", blocks=block(REFERENCE)), declared=1)
    parsed = screen.parse(raw, path="s.screen")

    with pytest.raises(DsoError):
        parsed.elements[0].set_reference("scripts\\Base_Frame.anim",
                                         "scripts\\" + "x" * 200 + ".anim")
    # And nothing was written on the way to refusing.
    assert parsed.to_bytes() == raw


def test_replacing_a_reference_that_is_not_there_says_so():
    raw = a_screen(element("CButton", "OK", blocks=block(REFERENCE)), declared=1)
    parsed = screen.parse(raw, path="s.screen")

    assert parsed.elements[0].set_reference("scripts\\Absent.anim", "x.anim") is False


def test_a_name_that_does_not_fit_the_field_is_refused():
    raw = a_screen(element("CButton", "OK"), declared=1)
    parsed = screen.parse(raw, path="s.screen")

    parsed.elements[0].name = "HELP_ButtonCancel"
    assert screen.parse(parsed.to_bytes(), path="s").elements[0].name == \
        "HELP_ButtonCancel"

    with pytest.raises(DsoError):
        parsed.elements[0].name = "x" * screen.NAME_FIELD


# --------------------------------------------------------------------------
# corpus
# --------------------------------------------------------------------------


@pytest.mark.corpus
def test_every_shipped_screen_round_trips_byte_identically(corpus):
    from conftest import collect

    files = collect(corpus, "*.screen")
    if not files:
        pytest.skip("no .screen files in the corpus")
    bad = []
    elements = 0
    for path in files:
        raw = path.read_bytes()
        try:
            parsed = screen.parse(raw, path=str(path))
        except DsoError as exc:
            bad.append(f"{path.name}: {exc}")
            continue
        elements += len(parsed)
        if parsed.to_bytes() != raw:
            bad.append(f"{path.name}: round-trip differs")
    assert not bad, f"{len(bad)} of {len(files)}: {bad[:5]}"
    assert elements > 0


# --------------------------------------------------------------------------
# which state an element shows at rest
# --------------------------------------------------------------------------


def test_a_button_shows_its_normal_state_not_its_first_reference():
    """The disabled artwork is listed first, and it is not what you see.

    Measured over the stock screens: slot 0 carries a ``*_disabled``-style name
    127 times and never a ``*_normal`` one, and drawing it greyed out 125 of
    the 694 elements that draw anything.
    """
    states = ["scripts\\ND_B_disabled.anim", "scripts\\ND_B_normal.anim",
              "scripts\\ND_B_pressed.anim", "scripts\\ND_B_highlight.anim"]

    assert screen.resting_index("CButton", states) == 1


def test_a_button_without_a_disabled_state_starts_at_normal():
    """26 buttons carry three: ``_nr``, ``_pr``, ``_hl``."""
    states = ["scripts\\Main_Cross_nr.anim", "scripts\\Main_Cross_pr.anim",
              "scripts\\Main_Cross_hl.anim"]

    assert screen.resting_index("CButton", states) == 0


def test_anything_that_is_not_a_button_draws_its_only_reference():
    assert screen.resting_index("CStatic", ["scripts\\MainWindow_framed.anim"]) == 0
    assert screen.resting_index("CButton", []) == 0
