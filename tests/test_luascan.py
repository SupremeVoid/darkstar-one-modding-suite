"""
Reading Lua as text: the machinery both the editor and the validator use.

Two properties are load-bearing and everything else follows from them.

*Blanking preserves position.* A comment or a string is replaced by spaces,
never removed, so a line number computed on the stripped text is still the line
number in the file the author is looking at. A finding on the wrong line is
worse than no finding.

*The scan is deliberately not a parser.* It has to say something useful about a
file that is halfway written, which no parser will accept. The cost is a class
of things it cannot read, and the rule is that it says so rather than guessing:
the tests below pin both sides of that.
"""

from __future__ import annotations

from dsotools import luascan


# --------------------------------------------------------------------------
# blanking
# --------------------------------------------------------------------------


def test_stripping_comments_keeps_every_line_and_column():
    source = 'a = 1 -- gone\nb = 2\n'
    stripped = luascan.strip_comments(source)
    assert len(stripped) == len(source)
    assert stripped.count("\n") == source.count("\n")
    assert stripped.startswith("a = 1 ")
    assert "gone" not in stripped
    assert "b = 2" in stripped


def test_a_long_comment_ends_at_its_own_level():
    """``--[==[`` closes at ``]==]``, not at the first ``]]`` inside it."""
    source = "--[==[ hidden ]] still hidden ]==]\nreal = 1\n"
    stripped = luascan.strip_comments(source)
    assert "hidden" not in stripped
    assert "real = 1" in stripped


def test_an_unterminated_long_comment_swallows_the_rest():
    stripped = luascan.strip_comments("--[[ open\nx = 1\n")
    assert stripped.strip() == ""


def test_stripping_strings_keeps_the_quotes_and_the_length():
    source = 'name = "end of it"\n'
    masked = luascan.strip_strings(source)
    assert len(masked) == len(source)
    assert masked == 'name = "         "\n'


def test_an_escaped_quote_does_not_end_the_string():
    masked = luascan.strip_strings(r'x = "a\"b" y = 1')
    assert masked == r'x = "    " y = 1'


def test_a_long_string_is_blanked_too():
    masked = luascan.strip_strings("x = [=[ end ]=]\n")
    assert masked == "x = [=[     ]=]\n"


# --------------------------------------------------------------------------
# what a source defines
# --------------------------------------------------------------------------


def test_both_ways_of_declaring_a_library_function_are_found():
    code = ("function MissionLib.Restore( V )\nend\n"
            "MissionLib.Other = function( V )\nend\n")
    assert luascan.definitions(code) == {"MissionLib.Restore", "MissionLib.Other"}


def test_a_definition_inside_a_comment_does_not_count():
    sources = ["-- function MissionLib.Ghost( V )\nfunction MissionLib.Real( V )\nend\n"]
    assert luascan.defined_in(sources) == {"MissionLib.Real"}


# --------------------------------------------------------------------------
# the check itself
# --------------------------------------------------------------------------

SYMBOLS = {
    "NComm.AddMessage": {"namespace": "NComm", "name": "AddMessage"},
    "NTutorial.Start": {"namespace": "NTutorial", "name": "Start"},
    "NGUI.ShowInfoText": {"namespace": "NGUI", "name": "ShowInfoText"},
    "MissionLib.Helper": {"namespace": "MissionLib", "name": "Helper"},
}

ENGINE = {
    "namespaces": {"NComm": ["AddMessage"], "NGUI": ["ShowInfoText", "Enable"],
                   "NTutorial": []},
    "stubs": {"NGUI.Enable": "registered, but the body never reads its argument"},
}

STRING_IDS = {"NGUI.ShowInfoText": ["Text"]}


def _check(text, **kw):
    kw.setdefault("symbols", SYMBOLS)
    kw.setdefault("engine", ENGINE)
    kw.setdefault("string_ids", STRING_IDS)
    return luascan.check(text, **kw)


def test_without_a_reference_nothing_is_claimed():
    """No database means no basis to judge, which is not the same as clean."""
    assert luascan.check("NComm.Nonesuch( { } )\n", symbols={}) == []


def test_a_registered_function_is_quiet():
    assert _check("NComm.AddMessage( { } )\n") == []


def test_a_documented_function_the_build_does_not_register_is_absent():
    found = _check("NTutorial.Start( { } )\n")
    assert [f["kind"] for f in found] == ["absent"]
    assert found[0]["symbol"] == "NTutorial.Start"
    assert found[0]["line"] == 1


def test_a_registered_function_that_does_nothing_is_a_stub():
    found = _check("\n\nNGUI.Enable( { } )\n")
    assert [(f["kind"], f["line"]) for f in found] == [("stub", 3)]
    assert "never reads its argument" in found[0]["detail"]


def test_a_call_to_nothing_at_all_is_unknown():
    found = _check("NComm.Nonesuch( { } )\n")
    assert [f["kind"] for f in found] == ["unknown"]


def test_a_function_the_lua_in_play_defines_is_not_unknown():
    assert _check("MissionLib.Elsewhere( V )\n",
                  defined={"MissionLib.Elsewhere"}) == []


def test_a_function_this_file_defines_is_not_unknown():
    assert _check("function MissionLib.Mine( V )\nend\nMissionLib.Mine( V )\n") == []


def test_prose_where_a_stringid_belongs_is_reported():
    found = _check('NGUI.ShowInfoText( { Text = "hello there" } )\n')
    assert [f["kind"] for f in found] == ["literal"]
    assert "nothing" in found[0]["detail"]


def test_an_identifier_where_a_stringid_belongs_is_accepted():
    assert _check('NGUI.ShowInfoText( { Text = "IDM_NEW_EMAIL" } )\n') == []


def test_a_call_inside_a_comment_is_not_a_call():
    assert _check("-- NComm.Nonesuch( { } )\n") == []
    assert _check("--[[\nNComm.Nonesuch( { } )\n]]\n") == []


def test_findings_come_back_in_source_order():
    found = _check("NComm.Nonesuch( { } )\n"
                   "NGUI.Enable( { } )\n"
                   "NTutorial.Start( { } )\n")
    assert [f["line"] for f in found] == [1, 2, 3]


def test_the_same_mistake_twice_is_reported_once():
    found = _check("NComm.Nonesuch( { } )\nNComm.Nonesuch( { } )\n")
    assert len(found) == 1
