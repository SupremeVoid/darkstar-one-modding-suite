"""
The stock mission table and overriding an entry in it.

The extraction is a parse of a *disassembly*, so the fixture below is a real
excerpt of ``ScriptCompiler -l`` output rather than something invented: the
shape it has to survive is tabs, instruction numbers, source-line numbers in
brackets, and nested functions between the record and the end of the chunk.

The one behaviour worth stating twice, because everything else rests on it: the
mission's identity is the ``Name`` field, not the file it lives in. Two stock
chunks prove it — ``BAR_006_02.lua`` registers ``BAR_006``.
"""

from __future__ import annotations

import pytest

from dsotools import missions
from dsotools.errors import DsoError

LISTING = """\
main <0:=(luac)> (4 instructions/16 bytes at 008C1838)
0 params, 1 stack, 0 locals, 0 strings, 0 numbers, 2 functions, 0 lines
     1\t[-]\tCLOSURE    \t0 0\t; 008C18B8
     2\t[-]\tCALL       \t0 0
     3\t[-]\tCLOSURE    \t1 0\t; 008CD388
     4\t[-]\tEND        \t

main <0:@Game/lua/mission/BAR_006_02.lua> (20 instructions/80 bytes at 007F18B8)
0 params, 9 stacks, 0 locals, 9 strings, 0 numbers, 2 functions, 30 lines
     1\t[10]\tGETGLOBAL  \t0\t; source
     2\t[10]\tPUSHSTRING \t1\t; "lua/mission/BattleLib.lua"
     3\t[10]\tCALL       \t0 0
     4\t[80]\tGETGLOBAL  \t2\t; NScript
     5\t[80]\tGETDOTTED  \t3\t; Register
     6\t[80]\tCREATETABLE\t4
     7\t[82]\tPUSHSTRING \t4\t; "Name"
     8\t[82]\tPUSHSTRING \t5\t; "BAR_006"
     9\t[84]\tPUSHSTRING \t6\t; "Group"
    10\t[84]\tPUSHINT    \t3
    11\t[86]\tPUSHSTRING \t7\t; "Type"
    12\t[86]\tGETGLOBAL  \t8\t; MTYPE_BAR
    13\t[88]\tPUSHSTRING \t9\t; "Transitions"
    14\t[88]\tCREATETABLE\t2
    15\t[88]\tCREATETABLE\t4
    16\t[91]\tPUSHNIL    \t2
    17\t[92]\tPUSHSTRING \t10\t; "Init"
    18\t[114]\tCLOSURE    \t0 0\t; 007F8FB8
    19\t[114]\tSETLIST    \t0 4
    20\t[119]\tCREATETABLE\t4
    21\t[121]\tPUSHNIL    \t2
    22\t[122]\tPUSHSTRING \t11\t; "Create"
    23\t[125]\tCLOSURE    \t1 0\t; 007F9B70
    24\t[125]\tSETLIST    \t0 4
    25\t[125]\tSETMAP     \t4
    26\t[125]\tCALL       \t0 0
    27\t[130]\tEND        \t

function <2:@Game/lua/mission/BAR_006_02.lua> (2 instructions/8 bytes at 007F8FB8)
     1\t[92]\tPUSHSTRING \t0\t; "Name"
     2\t[92]\tRETURN     \t0

main <0:@Game/lua/mission/MissionLib.lua> (3 instructions/12 bytes at 008F0000)
0 params, 2 stacks, 0 locals, 2 strings, 0 numbers, 0 functions, 9 lines
     1\t[3]\tGETGLOBAL  \t0\t; MissionLib
     2\t[3]\tCREATETABLE\t0
     3\t[3]\tEND        \t
"""


def test_a_registration_is_read_out_of_the_disassembly():
    found = missions.parse_listing(LISTING)
    assert len(found) == 1
    mission = found[0]
    assert mission.name == "BAR_006"
    assert mission.type == "MTYPE_BAR"
    assert mission.group == 3
    assert mission.states == ["Init", "Create"]
    assert mission.source == "Game/lua/mission/BAR_006_02.lua"


def test_the_mission_name_is_not_the_file_name():
    mission = missions.parse_listing(LISTING)[0]
    assert mission.source.endswith("BAR_006_02.lua")
    assert mission.file_name == "BAR_006.lua"


def test_a_chunk_that_registers_nothing_is_skipped_not_invented():
    """Libraries live in the same bundle; they are not missions."""
    names = {m.source for m in missions.parse_listing(LISTING)}
    assert not any("MissionLib" in n for n in names)


def test_reading_a_bundle_without_the_compiler_is_an_error_not_an_empty_list(
        tmp_path, monkeypatch):
    """"No compiler" and "no missions" must never look alike."""
    from dsotools import luac

    monkeypatch.setattr(luac, "find_compiler", lambda *a, **k: None)
    bundle = tmp_path / "missions.bin"
    bundle.write_bytes(b"\x1bLuaA")
    with pytest.raises(DsoError):
        missions.index(str(bundle))


# --------------------------------------------------------------------------
# what a mod registers
# --------------------------------------------------------------------------


def test_registered_names_ignores_comments():
    text = (
        '-- Name = "COMMENTED_OUT"\n'
        'NScript.Register( { Name = "REAL_ONE",\n'
        '    Type = MTYPE_ALWAYS } )\n'
        "--[[ Name = 'ALSO_COMMENTED' ]]\n"
    )
    assert missions.registered_names(text) == ["REAL_ONE"]


def test_registered_names_keeps_order_and_deduplicates():
    text = 'Name = "A"\nName = "B"\nName = "A"\n'
    assert missions.registered_names(text) == ["A", "B"]


def test_registrations_reports_every_file_a_name_appears_in(tmp_path):
    folder = tmp_path / "scripts"
    folder.mkdir()
    (folder / "one.lua").write_text('Name = "DUPE"\n', encoding="utf-8")
    (folder / "two.lua").write_text('Name = "DUPE"\nName = "OTHER"\n', encoding="utf-8")
    (folder / "notes.txt").write_text('Name = "IGNORED"\n', encoding="utf-8")
    found = missions.registrations(str(folder))
    assert found == {"DUPE": ["one.lua", "two.lua"], "OTHER": ["two.lua"]}


def test_registrations_of_a_missing_folder_is_empty(tmp_path):
    assert missions.registrations(str(tmp_path / "nope")) == {}


# --------------------------------------------------------------------------
# templates
# --------------------------------------------------------------------------


def test_an_override_carries_the_stock_record_forward():
    mission = missions.Mission("BAR_006", "MTYPE_BAR", 3, ["Init", "Create", "Text"])
    text = missions.override_template(mission)
    assert 'Name = "BAR_006"' in text
    assert "Type = MTYPE_BAR" in text
    assert "Group = 3" in text
    for state in mission.states:
        assert f'"{state}"' in text
    # And it says what it does, because nothing in game will.
    assert "replaces the stock" in text
    assert missions.registered_names(text) == ["BAR_006"]


def test_init_returns_ready_or_the_mission_never_runs():
    """The one line a stub cannot leave out.

    ``Init`` decides whether the mission is created; returning nothing means it
    never is, with no error and no log line. Stock ``Init`` bodies end in
    exactly this. A template that omitted it would look finished and be inert.
    """
    for text in (missions.override_template(
                     missions.Mission("X", "MTYPE_ALWAYS", 0, ["Init", "Create"])),
                 missions.new_template("Y", states=["Init"])):
        assert "return { Ready = true }" in text
        # and only in Init, not pasted into every state
        assert text.count("return { Ready = true }") == 1


def test_an_override_of_a_mission_with_no_recorded_states_still_builds():
    text = missions.override_template(missions.Mission("X"))
    assert 'Name = "X"' in text
    for state in missions.DEFAULT_STATES:
        assert f'"{state}"' in text


def test_a_new_mission_template_uses_the_chosen_type_and_states():
    text = missions.new_template("MY_PATROL", type="MTYPE_SPACE", group=2,
                                 states=["Init", "Wing"])
    assert 'Name = "MY_PATROL"' in text
    assert "Type = MTYPE_SPACE" in text
    assert "Group = 2" in text
    assert '"Wing"' in text
    assert '"Create"' not in text
    assert "replaces the stock" not in text


# --------------------------------------------------------------------------
# the shipped table
# --------------------------------------------------------------------------


def test_the_shipped_table_accounts_for_the_stock_bundle():
    """154 chunks, 150 missions, 4 libraries — the measurement this rests on."""
    table = missions.bundled()
    if table is None:
        pytest.skip("this build ships no stock_missions.json")
    found = missions.stock(table)
    assert len(found) == 150
    assert all(m.name and m.type and m.states for m in found)
    assert {m.type for m in found} >= {"MTYPE_ALWAYS", "MTYPE_BAR", "MTYPE_STORY"}


def test_the_two_undocumented_types_are_still_there():
    """The reference lists eight types; the bundle uses ten."""
    table = missions.bundled()
    if table is None:
        pytest.skip("this build ships no stock_missions.json")
    used = {m.type for m in missions.stock(table)}
    assert used - set(missions.MISSION_TYPES) == {"MTYPE_STORY_CHAPTER", "MTYPE_MENU"}


def test_lookup_is_case_insensitive_but_the_name_is_not_rewritten():
    table = missions.bundled()
    if table is None:
        pytest.skip("this build ships no stock_missions.json")
    found = missions.by_name("bar_006", table)
    assert found is not None and found.name == "BAR_006"
    assert missions.by_name("NOT_A_MISSION", table) is None


def test_save_and_reload_round_trips(tmp_path):
    original = [missions.Mission("B", "MTYPE_BAR", 1, ["Init"]),
                missions.Mission("A", "MTYPE_SPACE", 0, ["Init", "Create"])]
    path = missions.save(original, str(tmp_path / "t.json"), edition="gog")
    import json

    table = json.load(open(path, encoding="utf-8"))
    back = missions.stock(table)
    assert [m.name for m in back] == ["A", "B"]
    assert back[0].states == ["Init", "Create"]


# --------------------------------------------------------------------------
# does Init say yes?
# --------------------------------------------------------------------------
#
# Init is a readiness question, not a constructor: an Init that returns
# nothing leaves the mission uncreated, with no error and no log line.  The
# generated template got this wrong until stock bytecode was read, so the
# first thing worth pinning is that the templates themselves are right.


def _register(body, name="MINE", state="Init"):
    return (
        'NScript.Register( {\n'
        '    Name = "%s",\n'
        '    Transitions = {\n'
        '        { nil, nil, "%s",\n'
        '            function( V, Data )\n%s\n            end\n'
        '        },\n'
        '    },\n'
        '} )\n' % (name, state, body)
    )


def test_the_generated_templates_return_ready():
    """The failure this rule exists for, in the suite's own output."""
    assert missions.init_states(missions.new_template("NEW_ONE")) == [
        ("NEW_ONE", missions.INIT_READY)]
    stock = missions.Mission("OVER", "MTYPE_ALWAYS", 0, ["Init", "Create"])
    assert missions.init_states(missions.override_template(stock)) == [
        ("OVER", missions.INIT_READY)]


def test_an_init_that_returns_nothing_is_the_finding():
    source = _register("                NDebug.Message( { Message = \"hi\" } )")
    assert missions.init_states(source) == [("MINE", missions.INIT_NO_RETURN)]


def test_declining_deliberately_is_not_a_finding():
    source = _register("                return { Ready = false }")
    assert missions.init_states(source) == [("MINE", missions.INIT_READY)]


def test_a_return_the_scan_cannot_read_is_unclear_rather_than_wrong():
    """``return Helper( V )`` is legitimate, and guessing would cry wolf."""
    source = _register("                return MissionLib.Decide( V )")
    assert missions.init_states(source) == [("MINE", missions.INIT_UNCLEAR)]


def test_a_registration_with_no_init_claims_nothing():
    source = _register("", state="Create")
    assert missions.init_states(source) == [("MINE", missions.INIT_ABSENT)]


def test_nested_blocks_do_not_end_the_body_early():
    """A premature ``end`` would read the return as belonging to nothing."""
    body = ("                for i = 1, 3 do\n"
            "                    if i > 2 then\n"
            "                        local f = function() return 1 end\n"
            "                    elseif i == 1 then\n"
            "                        NDebug.Message( { Message = \"the end\" } )\n"
            "                    end\n"
            "                end\n"
            "                return { Ready = true }")
    assert missions.init_states(_register(body)) == [("MINE", missions.INIT_READY)]


def test_ready_in_a_comment_does_not_count_as_returning_it():
    body = "                -- return { Ready = true }"
    assert missions.init_states(_register(body)) == [("MINE", missions.INIT_NO_RETURN)]


def test_ready_in_a_string_does_not_count_either():
    body = '                NGUI.ShowInfoText( { Text = "Ready" } )'
    assert missions.init_states(_register(body)) == [("MINE", missions.INIT_NO_RETURN)]


def test_every_registration_in_a_file_is_read():
    source = (_register("                return { Ready = true }", name="A")
              + _register("                local x = 1", name="B"))
    assert missions.init_states(source) == [
        ("A", missions.INIT_READY), ("B", missions.INIT_NO_RETURN)]


def test_a_folder_is_read_file_by_file(tmp_path):
    (tmp_path / "b.lua").write_text(_register("                local x = 1", name="B"))
    (tmp_path / "a.lua").write_text(
        _register("                return { Ready = true }", name="A"))
    (tmp_path / "notes.txt").write_text("ignored")
    assert missions.init_states_by_file(str(tmp_path)) == [
        ("a.lua", "A", missions.INIT_READY),
        ("b.lua", "B", missions.INIT_NO_RETURN)]


def test_a_folder_that_is_not_there_is_not_an_error(tmp_path):
    assert missions.init_states_by_file(str(tmp_path / "nope")) == []
