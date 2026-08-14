"""
Telling a stock installation from a modded one.

This exists because the suite once could not, and wrote the difference down as
fact: an installation carrying a mod's own libraries was measured
and recorded as "13 files under lua/" when a stock install has 9, and "1,018
functions the libraries define" when a stock install defines 179.  Recorded
stock state, measured delta -- in that order.

The hardest case is the quiet one: two of the twelve files that mod puts in the
game folder are byte-different at exactly the stock size, so comparing sizes
alone reports a modded install as clean.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from dsotools import baseline
from dsotools.errors import DsoError


def _write(path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


@pytest.fixture()
def world(tmp_path):
    """A tiny installation and a baseline that describes it."""
    game = tmp_path / "game"
    stock = {
        "lua/mission/missionlib.lua": b"-- stock library\n",
        "lua/mission/missions.bin": b"\x1bLuaA" + b"A" * 100,
        "video/subtitles/one.xml": b"<x/>",
    }
    for relative, data in stock.items():
        _write(game / relative, data)
    _write(game / "DarkStarOne.exe", b"MZ" + b"\0" * 200)

    table = {
        "schema": 1,
        "build": {
            "data_fingerprint": "abc123",
            "editions": {
                "gog": {"exe_size": 202,
                        "exe_sha256": hashlib.sha256(b"MZ" + b"\0" * 200).hexdigest(),
                        "text_wrapped": False, "archives": {}},
            },
        },
        "roots": ["lua", "video"],
        "volatile": ["lua/config.lua"],
        "shared": {k: [len(v), hashlib.sha256(v).hexdigest()]
                   for k, v in stock.items()},
        "editions": {"gog": {}},
    }
    return str(game), table


def test_a_stock_installation_reports_no_differences(world):
    game, table = world

    result = baseline.classify(game, baseline=table, edition="gog")

    assert result[baseline.MODIFIED] == []
    assert result[baseline.ADDED] == []
    assert result[baseline.MISSING] == []
    assert len(result[baseline.UNCHANGED]) == 3


def test_a_replaced_file_is_modified_and_a_new_one_is_added(world, tmp_path):
    game, table = world
    _write(tmp_path / "game" / "lua/mission/missionlib.lua", b"-- the mod's\n")
    _write(tmp_path / "game" / "lua/mission/tools.lua", b"-- added by a mod\n")

    result = baseline.classify(game, baseline=table, edition="gog")

    assert result[baseline.MODIFIED] == ["lua/mission/missionlib.lua"]
    assert result[baseline.ADDED] == ["lua/mission/tools.lua"]


def test_an_edit_that_keeps_the_size_is_still_caught(world, tmp_path):
    """The real case: 39 single-byte edits inside missions.bin.

    Size alone would call this installation clean.
    """
    game, table = world
    same_size = b"\x1bLuaA" + b"A" * 99 + b"B"
    _write(tmp_path / "game" / "lua/mission/missions.bin", same_size)

    result = baseline.classify(game, baseline=table, edition="gog")

    assert result[baseline.MODIFIED] == ["lua/mission/missions.bin"]


def test_a_deleted_file_is_missing(world, tmp_path):
    game, table = world
    (tmp_path / "game" / "video" / "subtitles" / "one.xml").unlink()

    result = baseline.classify(game, baseline=table, edition="gog")

    assert result[baseline.MISSING] == ["video/subtitles/one.xml"]


def test_a_file_the_game_rewrites_is_not_a_modification(world, tmp_path):
    """``lua/config.lua`` is the engine's own saved registry."""
    game, table = world
    _write(tmp_path / "game" / "lua" / "config.lua", b"Registry = { }\n")

    result = baseline.classify(game, baseline=table, edition="gog")

    assert result[baseline.ADDED] == []
    assert result[baseline.MODIFIED] == []


def test_the_edition_is_detected_from_the_executable(world):
    game, table = world

    assert baseline.detect_edition(game, table) == "gog"


def test_one_file_can_be_asked_about_directly(world, tmp_path):
    game, table = world

    assert baseline.is_stock(game, "lua/mission/missionlib.lua", baseline=table)
    assert baseline.is_stock(game, "lua/mission/nothing.lua", baseline=table) is None

    _write(tmp_path / "game" / "lua/mission/missionlib.lua", b"changed\n")
    assert not baseline.is_stock(game, "lua/mission/missionlib.lua", baseline=table)


def test_a_baseline_from_a_future_schema_is_refused(tmp_path, monkeypatch):
    path = tmp_path / "stock_baseline.json"
    path.write_text(json.dumps({"schema": 99}), encoding="utf-8")
    monkeypatch.setattr(baseline, "bundled_path", lambda: str(path))

    with pytest.raises(DsoError):
        baseline.bundled()


# --------------------------------------------------------------------------
# the baseline this build ships
# --------------------------------------------------------------------------


def test_the_shipped_baseline_covers_both_editions():
    """One baseline for both: of 2,687 loose files, none differ between them."""
    table = baseline.bundled()
    if table is None:
        pytest.skip("this build ships no stock baseline")

    editions = table["build"]["editions"]
    assert set(editions) == {"gog", "steam"}
    # The Steam copy is DRM-wrapped and the GOG one is not, yet they share a
    # build: the wrapper touches .text, and the fingerprint covers .rdata/.data.
    assert editions["steam"]["text_wrapped"] is True
    assert editions["gog"]["text_wrapped"] is False
    assert len(table["shared"]) == 2687
    assert table["editions"]["gog"] == {}
    assert len(table["editions"]["steam"]) == 37
