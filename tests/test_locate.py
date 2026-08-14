"""
Finding the game.

Every candidate is *validated* rather than trusted: an uninstalled Steam entry
leaves the folder behind, and a registry key can outlive the game it points at.
Handing the user a path that turns out to be empty is worse than admitting
nothing was found.
"""

from __future__ import annotations

import os


from dsotools import locate


def _install(root, *, exe=True, archives=("ds_3dgen.cpr",)):
    root.mkdir(parents=True, exist_ok=True)
    if exe:
        (root / locate.EXECUTABLE).write_bytes(b"MZ")
    for a in archives:
        (root / a).write_bytes(b"PK\x05\x06" + b"\0" * 18)
    return str(root)


def test_recognises_a_real_installation(tmp_path):
    path = _install(tmp_path / "DarkStar One")
    assert locate.looks_like_game(path)
    c = locate.Candidate(path, "test")
    assert c.valid and c.why_not == ""


def test_rejects_a_folder_with_the_exe_but_no_archives(tmp_path):
    """A Steam uninstall can leave the folder and a stub behind."""
    path = _install(tmp_path / "Hollow", archives=())
    c = locate.Candidate(path, "test")
    assert not c.valid
    assert c.why_not == "no .cpr archives"


def test_rejects_a_folder_named_right_but_empty(tmp_path):
    path = str(tmp_path / "DarkStar One")
    os.makedirs(path)
    c = locate.Candidate(path, "test")
    assert not c.valid
    assert "DarkStarOne.exe" in c.why_not


def test_rejects_a_missing_folder(tmp_path):
    c = locate.Candidate(str(tmp_path / "gone"), "test")
    assert not c.valid
    assert c.why_not == "folder does not exist"


def test_explicit_path_wins_and_is_still_validated(tmp_path):
    good = _install(tmp_path / "good")
    bad = str(tmp_path / "bad")
    os.makedirs(bad)
    assert locate.find_game([bad, good]) == os.path.normpath(good)


def test_environment_override(tmp_path, monkeypatch):
    path = _install(tmp_path / "env")
    monkeypatch.setenv(locate.ENV_VAR, path)
    assert locate.find_game() == os.path.normpath(path)


def test_find_game_returns_none_rather_than_raising(tmp_path, monkeypatch):
    """'Not found' is an ordinary state the UI handles by asking.

    The registry and common-location searches are stubbed out, because
    otherwise this asserts "no Darkstar One is installed on the test machine" --
    which is false on exactly the developer machines that matter, and made the
    test fail there while passing in CI.
    """
    monkeypatch.setenv(locate.ENV_VAR, str(tmp_path / "nothing here"))
    monkeypatch.setattr(locate, "_registry_candidates", lambda: [])
    monkeypatch.setattr(locate, "_common_paths", lambda: [])
    assert locate.find_game() is None


def test_describe_explains_why_each_candidate_was_rejected(tmp_path, monkeypatch):
    bad = str(tmp_path / "bad")
    os.makedirs(bad)
    monkeypatch.setenv(locate.ENV_VAR, bad)
    rows = locate.describe()
    assert rows
    path, source, status = rows[0]
    assert source.endswith(locate.ENV_VAR)
    assert status != "ok"          # says what was wrong, not just that it failed


def test_extracted_tree_is_recognised_separately(tmp_path):
    root = tmp_path / "extracted"
    (root / "ds_3dobj").mkdir(parents=True)
    assert locate.looks_like_extracted(str(root))
    assert not locate.looks_like_game(str(root))


def test_extracted_check_is_false_for_a_missing_folder(tmp_path):
    assert not locate.looks_like_extracted(str(tmp_path / "nope"))


def test_steam_roots_is_safe_off_windows():
    """winreg is imported lazily so the module works everywhere."""
    assert isinstance(locate.steam_roots(), list)
