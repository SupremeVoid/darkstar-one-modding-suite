"""
The game's own Lua compiler, used as a library.

``ScriptCompiler.exe`` is ``luac`` for the engine's modified Lua 4.1, which
makes it the only parser that agrees with the game -- no modern Lua accepts
the same dialect, and the engine ships no standard library at all.

Most of these tests need the modding tools installed and skip without them.
``chunk_names`` does not: the source names are in the bytecode, so a bundle can
be inspected anywhere, and that is the part the app relies on most.
"""

from __future__ import annotations


import pytest

from dsotools import luac
from dsotools.errors import DsoError


def _need_compiler():
    if not luac.available():
        pytest.skip("the Darkstar One Modding Tools are not installed")


# --------------------------------------------------------------------------
# reading a bundle -- no compiler required
# --------------------------------------------------------------------------


def _bundle(names):
    """A minimal Lua 4 bundle: the signature and some chunk names."""
    body = b"".join(b"@" + n.encode("latin-1") + b"\x00padding" for n in names)
    return luac.SIGNATURE + b"\x01\x04\x04\x04 \x06\x09" + body


def test_the_chunk_names_are_read_out_of_the_bytecode():
    data = _bundle(["AAA_lib.lua", "ZZZ_mission.lua"])

    assert luac.chunk_names(data) == ["AAA_lib.lua", "ZZZ_mission.lua"]


def test_a_name_is_listed_once_however_often_it_appears():
    """Every function of a file records the same source name."""
    data = _bundle(["one.lua", "one.lua", "two.lua", "one.lua"])

    assert luac.chunk_names(data) == ["one.lua", "two.lua"]


def test_something_that_is_not_bytecode_is_refused():
    with pytest.raises(DsoError):
        luac.chunk_names(b"-- just a Lua source file\n")


def test_a_bundle_is_told_from_a_source_file(tmp_path):
    binary = tmp_path / "user_scripts.bin"
    binary.write_bytes(_bundle(["x.lua"]))
    source = tmp_path / "x.lua"
    source.write_text("X = 1\n")

    assert luac.is_bundle(str(binary))
    assert not luac.is_bundle(str(source))
    assert not luac.is_bundle(str(tmp_path / "absent.lua"))


# --------------------------------------------------------------------------
# with the compiler
# --------------------------------------------------------------------------


def test_a_good_script_parses():
    _need_compiler()

    ok, message = luac.check_syntax('NScript.Register( { Name = "X" } )\n')

    assert ok, message


def test_a_broken_script_is_reported_with_its_reason():
    _need_compiler()

    ok, message = luac.check_syntax('NScript.Register( { Name = "X"\n',
                                    name="broken.lua")

    assert not ok
    assert "error" in message.lower()


def test_the_syntax_check_says_so_when_it_cannot_run(monkeypatch):
    """Silence must not read as approval."""
    monkeypatch.setattr(luac, "find_compiler", lambda *roots: None)

    ok, message = luac.check_syntax("this is not lua at all !!!")

    assert ok is True
    assert "not installed" in message


def test_sources_compile_into_one_bundle_in_order(tmp_path):
    _need_compiler()
    first = tmp_path / "AAA_lib.lua"
    first.write_text("Lib = {}\n")
    second = tmp_path / "ZZZ_mission.lua"
    second.write_text("X = 1\n")
    out = tmp_path / "user_scripts.bin"

    luac.compile_bundle([str(first), str(second)], str(out))

    assert luac.is_bundle(str(out))
    # Order is load order, and the names are bare -- as in the shipped mods.
    assert luac.chunk_names(out.read_bytes()) == ["AAA_lib.lua", "ZZZ_mission.lua"]


def test_compiling_nothing_is_refused(tmp_path):
    with pytest.raises(DsoError):
        luac.compile_bundle([], str(tmp_path / "out.bin"))


def test_compiling_a_missing_file_names_it(tmp_path):
    _need_compiler()

    with pytest.raises(DsoError) as caught:
        luac.compile_bundle([str(tmp_path / "nope.lua")], str(tmp_path / "o.bin"))
    assert "nope.lua" in str(caught.value)


# --------------------------------------------------------------------------
# the game's own bundle
# --------------------------------------------------------------------------


@pytest.mark.corpus
def test_the_games_mission_bundle_reads(corpus):
    """154 chunks, and the libraries are compiled in alongside the missions."""
    from conftest import collect

    found = collect(corpus, "missions.bin")
    if not found:
        pytest.skip("no missions.bin in the corpus")
    names = luac.chunk_names(found[0].read_bytes())

    assert len(names) > 100
    assert any(n.endswith("MissionLib.lua") for n in names)
