"""
The bug report, and what it must never carry.

Two properties matter and only one of them is about usefulness.

A report is worth the round trip it saves, so it fills in the build, the
platform and what was open. But it goes into a **public** issue tracker, so the
harder requirement is the negative one: nothing in it may say anything about
the machine it came from. The tests below are mostly that second property,
because it is the one that fails quietly and cannot be taken back.
"""

from __future__ import annotations

import os
import sys
import urllib.parse

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))

from dso_app import bugreport  # noqa: E402


def _compose(**kw):
    kw.setdefault("qt_version", "6.9.0")
    kw.setdefault("app_version", "0.1.0")
    kw.setdefault("frozen", False)
    return bugreport.compose(**kw)


# -- what it carries --------------------------------------------------------


def test_the_build_facts_are_filled_in():
    _title, text, _url = _compose()
    assert "| Suite | 0.1.0 |" in text
    assert "| Qt (PySide6) | 6.9.0 |" in text
    assert "| Python |" in text and "| OS |" in text


def test_a_packaged_build_says_so():
    """Frozen and source builds fail differently; the report should not hide it."""
    _t, source, _u = _compose(frozen=False)
    _t, packaged, _u = _compose(frozen=True)
    assert "running from source" in source
    assert "| Packaged build | yes |" in packaged


def test_whether_a_game_and_mod_are_open_is_recorded():
    _t, text, _u = _compose(game_open=True, mod_open=True, game_kind="install")
    assert "| Game folder open | yes |" in text
    assert "| Mod open | yes |" in text
    assert "| Game source | install |" in text


def test_the_template_asks_the_three_questions():
    _t, text, _u = _compose()
    for heading in ("What happened", "Steps to reproduce", "Files involved"):
        assert heading in text


def test_the_title_is_left_for_the_reporter():
    """A title someone wrote is better than one a tool guessed."""
    title, _text, _url = _compose()
    assert title == ""


# -- what it must never carry -----------------------------------------------


def test_no_path_from_this_machine_reaches_the_report():
    _t, text, url = _compose(game_open=True, mod_open=True, game_kind="install")
    haystack = (text + urllib.parse.unquote(url)).lower()
    for leak in ("c:\\", "c:/", "/home/", "/users/", os.path.expanduser("~").lower()):
        assert leak not in haystack, leak


def test_the_user_name_does_not_reach_the_report():
    _t, text, url = _compose()
    who = (os.environ.get("USERNAME") or os.environ.get("USER") or "").strip()
    if not who or len(who) < 3:
        return          # nothing to leak on this machine
    assert who.lower() not in (text + urllib.parse.unquote(url)).lower()


def test_the_facts_are_a_fixed_set():
    """A field added without thought is how a path ends up in a public issue."""
    facts = bugreport.environment(qt_version="x", app_version="y")
    facts.update(bugreport.session_facts(game_open=False, mod_open=False))
    assert set(facts) == {
        "Suite", "Python", "Qt (PySide6)", "OS", "Machine", "Packaged build",
        "Game folder open", "Game source", "Mod open"}


# -- the URL ----------------------------------------------------------------


def test_the_url_points_at_the_repository_s_new_issue_form():
    _t, _text, url = _compose()
    assert url.startswith(bugreport.REPOSITORY + "/issues/new?")
    assert "labels=bug" in url


def test_the_body_survives_the_round_trip_through_the_url():
    _t, text, url = _compose()
    query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    assert query["body"][0] == text


def test_a_long_body_is_truncated_rather_than_silently_cut_by_a_browser():
    url = bugreport.issue_url("t", "x" * (bugreport.MAX_BODY + 500))
    body = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)["body"][0]
    assert len(body) < bugreport.MAX_BODY + 100
    assert body.endswith("<!-- truncated -->")


def test_markdown_and_spaces_are_encoded():
    url = bugreport.issue_url("a title", "# heading & <tag>")
    assert " " not in url
    assert "%23" in url and "%26" in url
