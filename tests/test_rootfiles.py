"""
Mod files that have to live in the game installation folder.

The case these exist for is real and was met on disk: a large mod
ships a second archive whose readme says "copy into game root", holding nine
``lua/mission`` files -- and no ``.cpr`` archive contains a single ``lua/``
entry, so there is no stock copy to fall back on.  Overwrite that by hand and
the original is gone.  Every test below is about not losing it.
"""

from __future__ import annotations

import os
import zipfile

import pytest

from dsotools import rootfiles
from dsotools.errors import DsoError


def _write(path, text):
    """Write exactly the bytes given, on every platform.

    ``newline=""`` is load-bearing: without it Python translates ``\n`` to
    ``os.linesep``, so the same fixture produced a 6-byte file on Windows and a
    5-byte one on Linux -- and the size assertion below was written against
    whichever ran first.  These tests compare sizes and hashes of payload
    files, so the fixture has to be byte-exact or it is testing the platform.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)
    return path


@pytest.fixture()
def game(tmp_path):
    """An installation with one stock library in it."""
    root = tmp_path / "game"
    _write(str(root / "lua" / "mission" / "MissionLib.lua"), "-- stock\n")
    return str(root)


def _mod(tmp_path, name, files):
    root = tmp_path / name
    for relative, text in files.items():
        _write(str(root / rootfiles.PAYLOAD_DIR / relative), text)
    return str(root)


# --------------------------------------------------------------------------
# reading a payload
# --------------------------------------------------------------------------


def test_a_mod_with_no_payload_folder_has_no_payload(tmp_path):
    assert rootfiles.payload(str(tmp_path)) == {}


def test_the_payload_is_addressed_by_game_root_relative_path(tmp_path):
    mod = _mod(tmp_path, "m", {"lua/mission/Tools.lua": "-- x\n"})

    found = rootfiles.payload(mod)

    assert list(found) == ["lua/mission/Tools.lua"]
    # len("-- x\n") -- the fixture writes it verbatim, so this is the same
    # number everywhere rather than whatever os.linesep made of it.
    assert found["lua/mission/Tools.lua"].size == 5


@pytest.mark.parametrize("bad", [
    "../outside.txt",
    "DarkStarOne.exe",
    ".dso_installed.json",
])
def test_paths_that_would_escape_or_patch_the_game_are_refused(bad):
    assert rootfiles.is_refused(bad)


def test_a_synchronisation_lock_is_not_mod_content():
    """``sync.ffs_lock`` really is in the shipped archive, and in the game."""
    assert rootfiles.is_junk("lua/mission/sync.ffs_lock")
    assert not rootfiles.is_junk("lua/mission/Tools.lua")


def test_importing_a_game_root_archive_fills_the_payload(tmp_path):
    archive = tmp_path / "Lua.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("lua/mission/Tools.lua", "-- tools\n")
        z.writestr("lua/mission/sync.ffs_lock", "lock")
        z.writestr("video/subtitles/indoor16.xml", "<x/>")
    mod = str(tmp_path / "m")

    written = rootfiles.import_zip(str(archive), mod)

    assert written == ["lua/mission/Tools.lua", "video/subtitles/indoor16.xml"]
    assert "lua/mission/sync.ffs_lock" not in rootfiles.payload(mod)


def test_importing_can_be_limited_to_one_tree(tmp_path):
    archive = tmp_path / "Lua.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("lua/mission/Tools.lua", "-- tools\n")
        z.writestr("video/subtitles/indoor16.xml", "<x/>")
    mod = str(tmp_path / "m")

    assert rootfiles.import_zip(str(archive), mod, only=["lua"]) == [
        "lua/mission/Tools.lua"]


# --------------------------------------------------------------------------
# installing
# --------------------------------------------------------------------------


def test_the_plan_says_what_each_file_would_do(tmp_path, game):
    mod = _mod(tmp_path, "A", {
        "lua/mission/MissionLib.lua": "-- mod A\n",
        "lua/mission/BattleLibEx.lua": "-- new\n",
    })

    steps = {a.path: a.what for a in rootfiles.plan(game, "A", rootfiles.payload(mod))}

    assert steps["lua/mission/MissionLib.lua"] == rootfiles.REPLACE_STOCK
    assert steps["lua/mission/BattleLibEx.lua"] == rootfiles.NEW


def test_installing_backs_up_what_it_displaces(tmp_path, game):
    """The whole point: the stock library has no other copy anywhere."""
    mod = _mod(tmp_path, "A", {"lua/mission/MissionLib.lua": "-- mod A\n"})

    result = rootfiles.install(game, "A", mod)

    live = os.path.join(game, "lua", "mission", "MissionLib.lua")
    assert open(live).read() == "-- mod A\n"
    assert result.backed_up == ["lua/mission/MissionLib.lua"]
    vault = os.path.join(game, rootfiles.BACKUP_DIR)
    assert len(os.listdir(vault)) == 1


def test_uninstalling_puts_the_original_back_and_removes_what_was_added(tmp_path, game):
    mod = _mod(tmp_path, "A", {
        "lua/mission/MissionLib.lua": "-- mod A\n",
        "lua/mission/BattleLibEx.lua": "-- new\n",
    })
    rootfiles.install(game, "A", mod)

    result = rootfiles.uninstall(game, "A")

    assert open(os.path.join(game, "lua", "mission", "MissionLib.lua")).read() == "-- stock\n"
    assert not os.path.exists(os.path.join(game, "lua", "mission", "BattleLibEx.lua"))
    assert result.clean
    assert rootfiles.installed_mods(game) == []


def test_a_second_install_does_not_lose_the_original(tmp_path, game):
    """Re-installing must keep the *stock* backup, not back up its own file."""
    mod = _mod(tmp_path, "A", {"lua/mission/MissionLib.lua": "-- v1\n"})
    rootfiles.install(game, "A", mod)
    _write(os.path.join(mod, rootfiles.PAYLOAD_DIR, "lua/mission/MissionLib.lua"),
           "-- v2\n")

    rootfiles.install(game, "A", mod)
    rootfiles.uninstall(game, "A")

    assert open(os.path.join(game, "lua", "mission", "MissionLib.lua")).read() == "-- stock\n"


def test_a_file_another_mod_owns_stops_the_install(tmp_path, game):
    mod_a = _mod(tmp_path, "A", {"lua/mission/MissionLib.lua": "-- A\n"})
    mod_b = _mod(tmp_path, "B", {"lua/mission/MissionLib.lua": "-- B\n"})
    rootfiles.install(game, "A", mod_a)

    with pytest.raises(DsoError) as caught:
        rootfiles.install(game, "B", mod_b)

    assert "A" in str(caught.value)
    assert open(os.path.join(game, "lua", "mission", "MissionLib.lua")).read() == "-- A\n"


def test_swapping_restores_the_first_mod_before_laying_down_the_second(tmp_path, game):
    """Interleaved, the second install would back up the *first mod's* file
    and call it the original."""
    mod_a = _mod(tmp_path, "A", {"lua/mission/MissionLib.lua": "-- A\n"})
    mod_b = _mod(tmp_path, "B", {"lua/mission/MissionLib.lua": "-- B\n"})
    rootfiles.install(game, "A", mod_a)

    rootfiles.swap(game, "A", "B", mod_b)
    assert open(os.path.join(game, "lua", "mission", "MissionLib.lua")).read() == "-- B\n"

    rootfiles.uninstall(game, "B")
    assert open(os.path.join(game, "lua", "mission", "MissionLib.lua")).read() == "-- stock\n"


def test_a_file_edited_since_install_is_left_alone_and_reported(tmp_path, game):
    mod = _mod(tmp_path, "A", {"lua/mission/MissionLib.lua": "-- A\n"})
    rootfiles.install(game, "A", mod)
    _write(os.path.join(game, "lua", "mission", "MissionLib.lua"), "-- hand-edited\n")

    result = rootfiles.uninstall(game, "A")

    assert "lua/mission/MissionLib.lua" in result.skipped
    assert open(os.path.join(game, "lua", "mission", "MissionLib.lua")).read() == \
        "-- hand-edited\n"
    # Still recorded, so the edit can be dealt with rather than forgotten.
    assert rootfiles.installed_mods(game) == ["A"]


def test_verify_reports_what_no_longer_matches(tmp_path, game):
    mod = _mod(tmp_path, "A", {"lua/mission/MissionLib.lua": "-- A\n"})
    rootfiles.install(game, "A", mod)

    assert rootfiles.verify(game) == {}

    os.remove(os.path.join(game, "lua", "mission", "MissionLib.lua"))
    assert "missing" in rootfiles.verify(game)["lua/mission/MissionLib.lua"]


# --------------------------------------------------------------------------
# the state this tool will actually meet
# --------------------------------------------------------------------------


def test_adopting_records_ownership_without_inventing_a_backup(game):
    """Payload copied in by hand months ago: the original is already gone."""
    result = rootfiles.adopt(game, "Example Mod",
                             ["lua/mission/MissionLib.lua"])

    assert result.written == ["lua/mission/MissionLib.lua"]
    assert rootfiles.installed_mods(game) == ["Example Mod"]
    entry = rootfiles.load_ledger(game)["mods"]["Example Mod"]["files"]
    assert entry["lua/mission/MissionLib.lua"]["displaced"] is None


def test_uninstalling_an_adopted_file_refuses_rather_than_deleting_it(game):
    """Deleting it would leave the game without a library it needs, and no
    original exists to put back.  Say so instead."""
    rootfiles.adopt(game, "VC", ["lua/mission/MissionLib.lua"])

    result = rootfiles.uninstall(game, "VC")

    assert os.path.exists(os.path.join(game, "lua", "mission", "MissionLib.lua"))
    assert "adopted" in result.skipped["lua/mission/MissionLib.lua"]


def test_adopting_a_file_another_mod_owns_is_refused(tmp_path, game):
    mod = _mod(tmp_path, "A", {"lua/mission/MissionLib.lua": "-- A\n"})
    rootfiles.install(game, "A", mod)

    result = rootfiles.adopt(game, "B", ["lua/mission/MissionLib.lua"])

    assert "A" in result.skipped["lua/mission/MissionLib.lua"]


def test_an_installation_with_no_ledger_is_simply_empty(game):
    assert rootfiles.load_ledger(game)["mods"] == {}
    assert rootfiles.installed_mods(game) == []
