"""
How one documented API symbol is presented.

Two rules earn their place here, and both are about not lying to the author:
an optional parameter must not read as required, and an inserted skeleton must
not pre-fill optional keys that then have to be deleted.  ``NComm.AddMessage``
takes six fields of which four are optional, so getting this wrong is the
normal case, not the edge case.
"""

from __future__ import annotations

import os
import sys

APP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app")
if APP not in sys.path:
    sys.path.insert(0, APP)

from dso_app.api_view import call_skeleton, symbol_html          # noqa: E402


def _symbol(**overrides):
    base = {
        "name": "AddMessage",
        "qualified": "NComm.AddMessage",
        "namespace": "NComm",
        "kind": "command",
        "signature": "AddMessage( { Text, Voice, Video } ) : { Message }",
        "summary": "Sends a high-priority radio message.",
        "parameters": [
            {"name": "Text", "type": "string", "comment": "What to show",
             "optional": False},
            {"name": "Video", "type": "string", "comment": "What to play",
             "optional": True},
        ],
        "returns": [{"name": "Message", "type": "unsigned int",
                     "comment": "Id", "optional": False}],
        "example": "V.Id = NComm.AddMessage( { Text = 'X' } ).Message",
        "trigger": "",
    }
    base.update(overrides)
    return base


def test_a_skeleton_carries_the_required_fields_only():
    """Four of AddMessage's six fields are optional; pre-filling them all
    makes the author delete more than they type."""
    assert call_skeleton(_symbol()) == "NComm.AddMessage( { Text= } )"


def test_a_positional_helper_gets_a_positional_skeleton():
    """CameraLib is documented as plain Lua arguments, not a table."""
    symbol = _symbol(
        qualified="CameraLib.BigBoy", name="BigBoy", kind="camera",
        signature="BigBoy (WingId, Time)",
        parameters=[{"name": "WingId", "optional": False},
                    {"name": "Time", "optional": False}],
    )

    assert call_skeleton(symbol) == "CameraLib.BigBoy(WingId, Time)"


def test_a_function_with_no_parameters_still_gets_its_brackets():
    assert call_skeleton(_symbol(parameters=[])) == "NComm.AddMessage()"


def test_an_event_is_inserted_as_the_string_a_script_registers():
    """An event is named by a string in an event table; it is never called."""
    symbol = _symbol(name="ActionCamStart", qualified="ActionCamStart",
                     namespace="", kind="event")

    assert call_skeleton(symbol) == '"ActionCamStart"'


def test_the_documentation_marks_the_optional_parameter_as_optional():
    html = symbol_html(_symbol())

    assert "NComm.AddMessage" in html
    assert "optional" in html
    # The required one is not labelled, so the label means something.
    assert html.index("Text") < html.index("optional")


def test_an_events_trigger_is_shown_because_that_is_what_it_has():
    html = symbol_html(_symbol(kind="event", trigger="A camera is created."))

    assert "Triggered by" in html
    assert "A camera is created." in html


def test_documentation_text_is_escaped_rather_than_rendered():
    """The 2006 reference contains angle brackets -- ``_<RACE>_<ACTOR>``."""
    html = symbol_html(_symbol(summary="suffix _<RACE>_<ACTOR>"))

    assert "&lt;RACE&gt;" in html
    assert "<RACE>" not in html
