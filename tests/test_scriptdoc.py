"""
Reading the official Lua reference into an API database.

The fixtures here are cut down from real pages of ``ds1doc_eng.chm``, keeping
the things that actually cost work: 2006 hand-written HTML that does not close
its tags, optionality carried only in italics, two pages whose ``<title>`` is
copied from a neighbour, and one helper documented twice.

The corpus test at the bottom runs against the shipped database, which is the
only place the real numbers can be checked.
"""

from __future__ import annotations

import json

import pytest

from dsotools import scriptdoc
from dsotools.errors import DsoError

COMMAND_PAGE = """<html><head><title>AddMessage</title></head><body>
<p id='functionname'>AddMessage( { Text, <i>Video</i> } )&nbsp;: { Message }</p><p>
Sends a high-priority radio message.
</p><p><b>Parameter table</b><br>
<table>
<tbody><tr>
<th> Identifier </th><th> Data type </th><th> Values </th><th> Comment
</th></tr>
<tr>
<td> Text </td><td> string </td><td> StringId </td><td> Text to display.
</td></tr>
<tr>
<td> <i>Video</i> </td><td> string </td><td> Path to a .bik </td><td> Video to play.
</td></tr></tbody></table>
</p><p><b>Return table</b><br>
<table>
<tbody><tr>
<th> Identifier </th><th> Data type </th><th> Values </th><th> Comment
</th></tr>
<tr>
<td> Message </td><td> unsigned int </td><td> n/a </td><td> Id of the message.
</td></tr></tbody></table>
</p><p><b>Example</b><br><pre>
V.MsgId = NComm.AddMessage( { Text="IDM_X" } ).Message
</pre></p></body></html>"""

EVENT_PAGE = """<html><head><title>ActionCamEnd</title></head><body>
<p id="functionname">ActionCamStart : { Camera } : {}</p>

<p>
A camera starts recording.
<p><b>Trigger</b><br>
On the program's side by setting of a new active camera.
        </p>
<p><b>Parameter table</b><br>
<table>
<tbody><tr>
<th>Field identifier </th><th> Data type </th><th> Values </th><th> Comment
</th></tr>
<tr>
<td> Camera </td><td> unsigned int </td><td> CCameraHandle </td><td> The camera
</td></tr></tbody></table>
</p>
<p><b>Return table</b><br>
    empty
</p>
</body></html>"""

CAMERA_PAGE = """<html><head><title>BigBoy</title></head><body>
<p id="functionname">BigBoy</p><p><b>Syntax</b></p><b>BigBoy</b>&nbsp;(WingId, Time)<br>
<br><b>Description</b><br>
        Camera does a flyby at "WingID".
<br><br><b>Example</b><br><pre>V.Camera = CameraLib.BigBoy(V.Wing, 20)<br></pre></body></html>"""


def _write(root, relative, text):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode("cp1252"))
    return path


@pytest.fixture()
def docs(tmp_path):
    """A decompiled-CHM folder, in miniature."""
    _write(tmp_path, "Commands/NComm/AddMessage.htm", COMMAND_PAGE)
    _write(tmp_path, "Events/Camera/ActionCamStart.htm", EVENT_PAGE)
    _write(tmp_path, "Camera/BigBoy.htm", CAMERA_PAGE)
    _write(tmp_path, "contants.htm", """<html><body>
<h3>GUI</h3><ul><li> GUI_NAVMAP</li><li> GUI_TARGETVIEW </li></ul>
<h3>Wing types</h3><ul><li> WINGTYPE_HUNTER</li></ul>
</body></html>""")
    return tmp_path


# --------------------------------------------------------------------------
# one page
# --------------------------------------------------------------------------


def test_a_command_page_yields_its_signature_and_tables():
    symbol = scriptdoc.parse_page(COMMAND_PAGE, namespace="NComm")

    assert symbol.qualified == "NComm.AddMessage"
    assert symbol.signature.startswith("AddMessage( { Text, Video } )")
    assert symbol.summary == "Sends a high-priority radio message."
    assert [p.name for p in symbol.parameters] == ["Text", "Video"]
    assert [p.name for p in symbol.returns] == ["Message"]
    assert symbol.parameters[0].type == "string"
    assert symbol.parameters[0].comment == "Text to display."
    assert "NComm.AddMessage" in symbol.example


def test_an_optional_parameter_is_only_marked_by_italics():
    """Nothing else says so, and dropping it turns "may" into "must"."""
    symbol = scriptdoc.parse_page(COMMAND_PAGE, namespace="NComm")

    assert symbol.parameters[0].optional is False       # Text
    assert symbol.parameters[1].optional is True        # <i>Video</i>


def test_the_header_row_is_not_a_parameter():
    symbol = scriptdoc.parse_page(COMMAND_PAGE, namespace="NComm")

    assert "Identifier" not in [p.name for p in symbol.parameters]


def test_an_event_carries_its_trigger_and_is_named_by_its_signature():
    """``ActionCamStart.htm`` is titled ActionCamEnd -- Ascaron's copy-paste.

    A database keyed on the title lists one event twice and loses the other.
    """
    symbol = scriptdoc.parse_page(EVENT_PAGE, kind=scriptdoc.KIND_EVENT,
                                  category="Camera")

    assert symbol.name == "ActionCamStart"
    assert symbol.qualified == "ActionCamStart"        # events have no namespace
    assert symbol.trigger.startswith("On the program's side")
    assert [p.name for p in symbol.parameters] == ["Camera"]
    assert symbol.returns == []                        # "empty", not a table


def test_a_camera_helper_is_documented_with_syntax_not_tables():
    symbol = scriptdoc.parse_page(CAMERA_PAGE, namespace="CameraLib",
                                  kind=scriptdoc.KIND_CAMERA)

    assert symbol.qualified == "CameraLib.BigBoy"
    assert symbol.signature == "BigBoy (WingId, Time)"
    assert "flyby" in symbol.summary
    assert symbol.example.startswith("V.Camera = CameraLib.BigBoy")


def test_a_syntax_section_does_not_repeat_the_name():
    """The page says "BigBoy" twice; the signature must not."""
    symbol = scriptdoc.parse_page(CAMERA_PAGE, namespace="CameraLib")

    assert not symbol.signature.startswith("BigBoy BigBoy")


# --------------------------------------------------------------------------
# a whole folder
# --------------------------------------------------------------------------


def test_building_a_database_groups_by_namespace(docs):
    database = scriptdoc.build(str(docs), source="ds1doc_eng.chm")

    index = scriptdoc.index(database)
    assert set(index) == {"NComm.AddMessage", "ActionCamStart", "CameraLib.BigBoy"}
    assert database["namespaces"]["NComm"] == ["AddMessage"]
    assert database["constants"]["GUI"] == ["GUI_NAVMAP", "GUI_TARGETVIEW"]
    assert database["schema"] == scriptdoc.SCHEMA


def test_a_helper_documented_in_two_categories_is_listed_once(docs):
    """``MissionLib.SetWingName`` is filed under both Other and Wing."""
    page = COMMAND_PAGE.replace("AddMessage", "SetWingName")
    _write(docs, "MissionLib/Other/SetWingName.htm", page)
    _write(docs, "MissionLib/Wing/SetWingName.htm", page)

    database = scriptdoc.build(str(docs))

    names = [s["qualified"] for s in database["symbols"]]
    assert names.count("MissionLib.SetWingName") == 1
    assert database["duplicate_pages"] == ["MissionLib/Wing/SetWingName.htm"]


def test_a_folder_that_is_not_the_reference_is_refused(tmp_path):
    """Better than an empty database an editor would quietly offer nothing from."""
    (tmp_path / "readme.txt").write_text("nothing to see")

    with pytest.raises(DsoError):
        scriptdoc.build(str(tmp_path))


def test_the_database_round_trips_through_json(docs, tmp_path):
    database = scriptdoc.build(str(docs))
    path = tmp_path / "api.json"

    scriptdoc.save(database, str(path))

    assert scriptdoc.load(str(path)) == database


def test_a_database_from_another_schema_is_refused(tmp_path):
    path = tmp_path / "api.json"
    path.write_text(json.dumps({"schema": scriptdoc.SCHEMA + 1, "symbols": []}))

    with pytest.raises(DsoError) as caught:
        scriptdoc.load(str(path))
    assert "chm_to_json" in str(caught.value)


# --------------------------------------------------------------------------
# the database this build ships
# --------------------------------------------------------------------------


def test_the_shipped_database_is_complete_and_addressable():
    """The real numbers, checked against the file the app actually reads.

    Generated from the 325-page reference: 6 of those are the index, the
    overview pages and the modding guide, leaving 319 documented symbols, of
    which ``MissionLib.SetWingName`` appears twice.
    """
    database = scriptdoc.bundled()
    if database is None:
        pytest.skip("this build ships no API database")

    symbols = database["symbols"]
    index = scriptdoc.index(database)

    assert len(symbols) == 318
    assert len(index) == len(symbols), "every symbol must be addressable"
    assert index["NComm.AddMessage"]["parameters"][0]["name"] == "Text"
    assert index["ActionCamStart"]["kind"] == "event"
    assert sum(1 for s in symbols if s["summary"]) >= 310
    # The 22 documented command namespaces, plus MissionLib and CameraLib.
    assert len([n for n in database["namespaces"] if n.startswith("N")]) == 22
    assert sum(len(v) for v in database["constants"].values()) == 223
