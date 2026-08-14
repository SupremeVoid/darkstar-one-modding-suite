"""
``.res`` string tables.

The hash is the whole point of this module, so it is tested against values
taken from the shipped ``Xml2ResConverter.exe`` itself -- its ``Converter.Hash``
was invoked by reflection on the tutorial's ids and on probes chosen to force
the two behaviours that every earlier guess got wrong: the 32-bit wrap on the
multiply, and the *signed* remainder that follows it.  ``ID_MODTUT_TEXT`` and
``abcdef...`` are the load-bearing vectors -- both exceed the 999,999,991
modulus, which is only possible if the accumulator went negative first.
"""

from __future__ import annotations

import pathlib
import struct

import pytest

from dsotools.errors import BuildError, ParseError
from dsotools.formats import res

#: id -> key, produced by Xml2ResConverter.Converter.Hash.
GROUND_TRUTH = {
    "A": 65,
    "AB": 7411,
    "ABC": 837510,
    "ZZZZZ": 920380811,
    "ID_MODTUT_HEADER": 496664849,
    "ID_MODTUT_CLIENT": 722107582,
    "ID_MODTUT_TEXT": 4125049956,
    "ID_MODTUTCAM_FILTER1": 4150452974,
    "abcdefghijklmnopqrstuvwxyz0123456789": 4190600536,
}


@pytest.mark.parametrize("identifier,expected", sorted(GROUND_TRUTH.items()))
def test_hash_matches_the_shipped_converter(identifier, expected):
    assert res.string_hash(identifier) == expected


def test_hash_of_the_empty_id_is_zero():
    assert res.string_hash("") == 0


def test_keys_that_exceed_the_modulus_are_reachable():
    """Guards the signed-remainder detail.

    A naive ``% 999999991`` caps every key below 10**9; two of the ground-truth
    ids sit above 4 * 10**9.  If someone "simplifies" the arithmetic, this is
    the test that notices.
    """
    over = [v for v in GROUND_TRUTH.values() if v > res.HASH_MODULUS]
    assert over, "the fixture no longer covers the interesting case"
    assert all(res.string_hash(k) == v for k, v in GROUND_TRUTH.items() if v in over)


def test_round_trip_is_byte_exact():
    table = res.from_pairs([
        ("ID_ONE", "Hello"),
        ("ID_TWO", "L\u00e4ngerer Text mit Umlauten"),
        ("ID_THREE", ""),
    ])
    blob = res.build(table)
    again = res.parse(blob)
    assert res.build(again) == blob
    assert [e.text for e in again] == ["Hello", "L\u00e4ngerer Text mit Umlauten", ""]


def test_layout_is_what_the_engine_expects():
    table = res.from_pairs([("ID_A", "ab"), ("ID_B", "cde")])
    blob = res.build(table)
    count, = struct.unpack_from("<I", blob, 0)
    assert count == 2
    first = struct.unpack_from("<4I", blob, 4)
    second = struct.unpack_from("<4I", blob, 4 + res.ENTRY_SIZE)
    assert first[0] == res.string_hash("ID_A")
    assert first[1] == count * res.ENTRY_SIZE      # relative to byte 4
    assert first[2] == 0
    assert first[3] == 4                           # two characters, UTF-16
    assert second[1] == first[1] + first[3]
    assert blob[4 + second[1]:].decode("utf-16-le") == "cde"
    assert len(blob) == 4 + count * res.ENTRY_SIZE + 4 + 6


def test_lookup_is_by_id_not_by_position():
    table = res.from_pairs([("ID_A", "first"), ("ID_B", "second")])
    round_tripped = res.parse(res.build(table))
    assert round_tripped.text("ID_B") == "second"
    assert round_tripped.text("ID_MISSING") is None
    assert round_tripped.has("ID_A")
    assert round_tripped.resolve(["ID_A", "ID_X"]) == {"ID_A": "first", "ID_X": None}


#: A genuine collision, found by search.  Short ids never collide -- the hash
#: stays injective while it is below the modulus -- so this pair is 15 chars.
COLLIDING = ("ID_BRCBHZSVVRVG", "ID_CICQUQNQSTPJ")


def test_the_collision_fixture_really_collides():
    a, b = COLLIDING
    assert res.string_hash(a) == res.string_hash(b) == 0xF6CF0D05


def test_a_collision_is_refused_rather_than_silently_dropped():
    """Two ids on one key means one text is unreachable in game."""
    a, b = COLLIDING
    with pytest.raises(BuildError):
        res.from_pairs([(a, "one"), (b, "two")])


def test_the_same_id_twice_is_allowed():
    table = res.from_pairs([("ID_A", "old"), ("ID_A", "new")])
    assert res.parse(res.build(table)).text("ID_A") == "new"


def test_collisions_are_reported():
    table = res.StringTable([res.StringEntry(7, "a"), res.StringEntry(7, "b")])
    assert table.collisions() == [(7, 2)]


@pytest.mark.parametrize("blob,why", [
    (b"", "too short"),
    (b"\x01", "too short"),
    (struct.pack("<I", 99), "claims more entries than it holds"),
    (struct.pack("<I", 1) + struct.pack("<4I", 1, 16, 0, 3), "odd byte length"),
    (struct.pack("<I", 1) + struct.pack("<4I", 1, 16, 0, 40), "runs past the end"),
    (struct.pack("<I", 1) + struct.pack("<4I", 1, 0, 0, 2) + b"ab", "points into the table"),
])
def test_malformed_files_raise_rather_than_guess(blob, why):
    with pytest.raises(ParseError):
        res.parse(blob)
    assert not res.is_string_table(blob), why


def test_sniffing_accepts_a_real_table():
    assert res.is_string_table(res.build(res.from_pairs([("ID_A", "x")])))


def _installed_tables():
    """Every ``.res`` in any installation this machine happens to have."""
    from dsotools import locate

    roots = [pathlib.Path(c.path) for c in locate.candidates() if c.has_exe]
    tutorial = pathlib.Path(
        r"C:\Program Files\Darkstar One Modding Tools\Modding\Tutorial"
    )
    if tutorial.is_dir():
        roots.append(tutorial)
    found = []
    for root in roots:
        found.extend(sorted(root.rglob("*.res")))
    return found


def test_every_shipped_table_parses():
    """The claim the format documentation rests on, re-checked on real files.

    Skipped, never silently passed, when no installation is present.
    """
    tables = _installed_tables()
    if not tables:
        pytest.skip("no Darkstar One installation on this machine")
    for path in tables:
        blob = path.read_bytes()
        table = res.parse(blob, path=str(path))
        assert len(table) > 0, path
        assert res.build(table) == blob, f"{path} does not round-trip"
