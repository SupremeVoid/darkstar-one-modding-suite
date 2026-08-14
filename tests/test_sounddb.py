"""
The sound database: nesting, group-scoped names, and byte-exact editing.

The fixture below is shaped like the real thing rather than minimised, because
the two facts this module got wrong were both structural and both invisible in
a flat example:

* groups nest, and a reader that looks one level down finds almost nothing --
  the first version of ``sounddb`` saw **3 of 442** sounds in the stock
  database;
* a sound's identity is ``group/name``, not ``name``. Stock reuses 38 names
  across different groups, pointing at different files.
"""

from __future__ import annotations

import pathlib

import pytest

from dsotools.errors import BuildError, ParseError
from dsotools.formats import sounddb

DB = (
    b'<?xml version="1.0" encoding="ISO-8859-1"?>\r\n'
    b"<ASE_Database>\r\n"
    b'  <DocumentProperties><Version>2.8.1</Version></DocumentProperties>\r\n'
    b'  <Group Name="MUSIC" Volume="0.88" Wet="0.0" >\r\n'
    b'    <Group Name="Death" Select="Random2">\r\n'
    b'      <Stream Name="gameover" Resrc="sound\\music(stream)\\grp_Death\\over.mp3"'
    b' Channels="2" Duration=":2048256" Freq="44100" />\r\n'
    b"    </Group>\r\n"
    b'    <Group Name="Failed" Select="Random2">\r\n'
    b'      <Stream Name="gameover" Resrc="sound\\music(stream)\\grp_Failed\\over.mp3"'
    b' Channels="2" Freq="44100" />\r\n'
    b"    </Group>\r\n"
    b"  </Group>\r\n"
    b'  <Group Name="USER">\r\n'
    b'    <Sound2D Name="Click" Resrc="%MOD%sound\\sfx(2d)\\grp_USER\\Click.wav"'
    b' Freq="22050" />\r\n'
    b"  </Group>\r\n"
    b"</ASE_Database>\r\n"
)


def test_nested_groups_are_read_all_the_way_down():
    """The regression that started this: one level deep found 3 of 442."""
    db = sounddb.parse(DB)
    assert [g.path for g in db.all_groups()] == [
        "MUSIC", "MUSIC/Death", "MUSIC/Failed", "USER"]
    assert sum(1 for _ in db.entries()) == 3


def test_a_group_carries_its_attributes():
    db = sounddb.parse(DB)
    music = db.group("MUSIC")
    assert music.volume == "0.88"
    assert music.attrs["Wet"] == "0.0"
    # Select turns a group into a random pool; losing it changes the sound.
    assert db.group("MUSIC/Death").select == "Random2"
    assert music.select is None


def test_group_lookup_is_by_path_and_case_insensitive():
    db = sounddb.parse(DB)
    assert db.group("music/death") is db.group("MUSIC/Death")
    assert db.group("Death") is None            # a name is not a path
    assert db.group("MUSIC/Nope") is None


def test_the_same_name_in_two_groups_is_two_sounds():
    """Stock does this 38 times; a flat name index loses one of each pair."""
    db = sounddb.parse(DB)
    both = db.by_name()["gameover"]
    assert len(both) == 2
    assert {e.group for e in both} == {"MUSIC/Death", "MUSIC/Failed"}
    assert {e.path() for e in both} == {
        "sound/music(stream)/grp_Death/over.mp3",
        "sound/music(stream)/grp_Failed/over.mp3",
    }
    # ...and that is not an error, because they are separately addressable.
    assert db.duplicate_names() == {}


def test_resolving_needs_a_group_when_the_name_repeats():
    db = sounddb.parse(DB)
    assert db.resolve("gameover") is None                    # ambiguous
    got = db.resolve("MUSIC/Death/gameover")
    assert got is not None and got.group == "MUSIC/Death"
    assert db.resolve("Click").kind == "Sound2D"             # unambiguous
    assert len(db.find("gameover", "MUSIC/Failed")) == 1


def test_mod_prefix_and_backslashes():
    db = sounddb.parse(DB)
    click = db.resolve("USER/Click")
    assert click.is_mod_relative
    assert click.path() == "sound/sfx(2d)/grp_USER/Click.wav"
    assert click.frequency == 22050
    game = db.resolve("MUSIC/Death/gameover")
    assert not game.is_mod_relative
    assert game.duration == 2048256


def test_a_missing_duration_is_none_not_zero():
    db = sounddb.parse(DB)
    entry = db.resolve("MUSIC/Failed/gameover")
    assert entry.duration is None
    assert entry.seconds is None


def test_duration_is_samples_so_playing_time_needs_the_rate():
    """Checked against a decoder: Duration/Freq is the real length.

    2,048,256 samples at 44,100 Hz is 46.4 s, and the file plays for 46.405 s.
    Reading it as milliseconds would say 34 minutes.
    """
    entry = sounddb.parse(DB).resolve("MUSIC/Death/gameover")
    assert entry.duration == 2048256
    assert entry.seconds == pytest.approx(46.446, abs=0.01)


# --------------------------------------------------------------------------
# writing
# --------------------------------------------------------------------------


def test_round_trip_is_byte_exact():
    db = sounddb.parse(DB)
    assert db.to_bytes() == DB


def test_the_declaration_is_preserved_not_normalised():
    """The stock database says ISO-8859-1; rewriting that changes the file."""
    out = sounddb.parse(DB).to_bytes()
    assert out.startswith(b'<?xml version="1.0" encoding="ISO-8859-1"?>')


def test_adding_a_sound_changes_only_its_own_line():
    db = sounddb.parse(DB)
    db.add_entry("Sound2D", "Beep", r"%MOD%sound\sfx(2d)\grp_USER\Beep.wav",
                 group="USER", Freq="22050")
    out = db.to_bytes()
    before = DB.decode("latin-1").splitlines()
    after = out.decode("latin-1").splitlines()
    added = [line for line in after if line not in before]
    assert len(added) == 1 and "Beep" in added[0]
    assert [line for line in before if line not in after] == []
    assert sounddb.parse(out).resolve("USER/Beep") is not None


def test_adding_creates_a_missing_group():
    db = sounddb.parse(DB)
    db.add_entry("Sound3D", "Boom", r"%MOD%sound\sfx(3d)\Boom.wav",
                 group="FX/Explosions")
    out = sounddb.parse(db.to_bytes())
    assert out.group("FX/Explosions") is not None
    assert out.resolve("FX/Explosions/Boom").kind == "Sound3D"


def test_a_name_clash_inside_one_group_is_refused():
    db = sounddb.parse(DB)
    with pytest.raises(BuildError):
        db.add_entry("Stream", "gameover", "x.mp3", group="MUSIC/Death")


def test_the_same_name_in_a_different_group_is_allowed():
    db = sounddb.parse(DB)
    db.add_entry("Stream", "gameover", r"%MOD%sound\other.mp3", group="USER")
    assert len(sounddb.parse(db.to_bytes()).by_name()["gameover"]) == 3


def test_an_unknown_kind_is_refused():
    db = sounddb.parse(DB)
    with pytest.raises(BuildError):
        db.add_entry("Sound4D", "X", "x.wav", group="USER")


def test_removing_a_sound_needs_an_unambiguous_reference():
    db = sounddb.parse(DB)
    assert db.remove_entry("gameover") is False        # two of them
    assert db.remove_entry("MUSIC/Death/gameover") is True
    out = sounddb.parse(db.to_bytes())
    assert len(out.by_name().get("gameover", [])) == 1
    assert out.group("MUSIC/Death") is not None        # the group stays


def test_removing_something_absent_says_so():
    assert sounddb.parse(DB).remove_entry("USER/Nope") is False


def test_repointing_a_sound_rewrites_one_attribute():
    db = sounddb.parse(DB)
    db.set_resource("USER/Click", r"%MOD%sound\sfx(2d)\grp_USER\Other.wav")
    out = db.to_bytes()
    assert b"Other.wav" in out and b"Click.wav" not in out
    assert sounddb.parse(out).resolve("USER/Click").path().endswith("Other.wav")


def test_repointing_something_absent_raises():
    with pytest.raises(BuildError):
        sounddb.parse(DB).set_resource("USER/Nope", "x.wav")


# --------------------------------------------------------------------------
# malformed input
# --------------------------------------------------------------------------


@pytest.mark.parametrize("blob", [
    b"<NotADatabase/>",
    b"<ASE_Database><unclosed></ASE_Database>",
    b"",
])
def test_malformed_input_raises_rather_than_guessing(blob):
    with pytest.raises(ParseError):
        sounddb.parse(blob)


def test_sniffing_does_not_parse():
    assert sounddb.is_sound_database(DB)
    assert not sounddb.is_sound_database(b"<WalhallaScene/>")


# --------------------------------------------------------------------------
# the real files
# --------------------------------------------------------------------------


def _stock_database():
    from dsotools import locate

    for candidate in locate.candidates():
        if not candidate.has_exe:
            continue
        path = pathlib.Path(candidate.path) / "KlangErzeugerDefault.xml"
        if path.is_file():
            return path
    return None


def test_the_stock_database_reads_and_rebuilds():
    """285 groups and 442 sounds, byte for byte.

    Skipped, never silently passed, when no installation is present.
    """
    path = _stock_database()
    if path is None:
        pytest.skip("no Darkstar One installation on this machine")
    raw = path.read_bytes()
    db = sounddb.parse(raw, path=str(path))
    assert sum(1 for _ in db.all_groups()) == 285
    assert sum(1 for _ in db.entries()) == 442
    assert db.to_bytes() == raw
    # Names repeat across groups, and every repeat is a different file.
    repeats = {n: v for n, v in db.by_name().items() if len(v) > 1}
    assert len(repeats) == 38
    assert all(len({e.path().lower() for e in v}) == len(v) for v in repeats.values())
    # Group-scoped, though, every one is unique.
    assert db.duplicate_names() == {}


def test_setting_metadata_updates_the_element_and_the_reading():
    """The engine reads these three, so they are not documentation."""
    db = sounddb.parse(DB)
    entry = db.resolve("USER/Click")
    entry.set_metadata({"Channels": "1", "Freq": "44100", "Duration": ":1000"})

    assert entry.channels == 1
    assert entry.frequency == 44100
    assert entry.duration == 1000
    # The element behind it moved too, or the write would lose the change.
    assert b'Freq="44100"' in db.to_bytes()
    assert b'Duration=":1000"' in db.to_bytes()


def test_setting_metadata_leaves_the_rest_of_the_file_alone():
    db = sounddb.parse(DB)
    db.resolve("USER/Click").set_metadata({"Freq": "44100"})
    written = db.to_bytes()
    assert written.count(b"<Stream") == 2
    assert b'Select="Random2"' in written
