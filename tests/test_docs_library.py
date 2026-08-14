"""
The documentation catalogue.

The window layer has no unit tests by design -- Qt widgets are driven by the
``tools/drive_*.py`` scripts -- so everything about *which* documents exist,
what they are called and how a link between them resolves lives here, where it
can be checked.

The load-bearing test is the last one: every document the catalogue promises
must actually be in the repository. A help menu that opens an empty page is
worse than no help menu, and the failure mode is a renamed spec file, which no
amount of care in the window would catch.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app"))

from dso_app import docs_library  # noqa: E402


def test_the_guide_comes_first():
    """Order carries meaning: "what do I do" before "how does this file work"."""
    first = docs_library.CATALOGUE[0]
    assert first[0] == "specs/modding_guide.md"
    assert first[2] == "Start here"


def test_every_promised_document_exists():
    """The one that catches a renamed spec before a user finds a blank page."""
    missing = [relative for relative, _t, _s in docs_library.CATALOGUE
               if docs_library.locate(relative) is None]
    assert missing == []


def test_documents_are_listed_in_catalogue_order():
    listed = [d["id"] for d in docs_library.documents()]
    assert listed == [c[0] for c in docs_library.CATALOGUE]


def test_the_project_s_own_records_are_not_offered():
    """STATE/TODOS/ARCHITECTURE are about building the suite, not the game."""
    ids = {c[0] for c in docs_library.CATALOGUE}
    for internal in ("docs/STATE.md", "docs/TODOS.md",
                     "docs/ARCHITECTURE.md"):
        assert internal not in ids


def test_a_document_reads_as_text():
    text = docs_library.read("specs/modding_guide.md")
    assert text.startswith("# Modding Darkstar One")
    assert "Ascaron" in text


def test_reading_something_that_is_not_there_raises():
    with pytest.raises(OSError):
        docs_library.read("specs/nope.md")


# -- links ------------------------------------------------------------------


def test_a_sibling_link_resolves():
    assert docs_library.resolve_link(
        "specs/modding_guide.md", "scene.md") == "specs/scene.md"


def test_a_relative_link_out_of_a_folder_resolves():
    assert docs_library.resolve_link(
        "cli/cli_3do.md", "../specs/scene.md") == "specs/scene.md"


def test_an_anchor_is_stripped_before_resolving():
    assert docs_library.resolve_link(
        "specs/README.md", "scene.md#4-path-resolution") == "specs/scene.md"


def test_an_external_link_is_left_alone():
    """The browser's job, not the reader's."""
    assert docs_library.resolve_link(
        "specs/README.md", "https://example.com/a.md") is None


def test_a_link_to_something_unshipped_is_refused():
    assert docs_library.resolve_link("specs/README.md", "STATE.md") is None


# -- outline and search -----------------------------------------------------


def test_headings_are_found():
    found = docs_library.outline("# One\n\ntext\n\n## Two\n### Three\n")
    assert [(h["level"], h["text"]) for h in found] == [
        (1, "One"), (2, "Two"), (3, "Three")]


def test_a_hash_inside_a_code_fence_is_not_a_heading():
    """The specs are full of shell examples, and every one would be a heading."""
    found = docs_library.outline("# Real\n\n```bash\n# not a heading\n```\n")
    assert [h["text"] for h in found] == ["Real"]


def test_search_finds_the_documents_that_mention_a_term():
    hits = docs_library.search("items.ini")
    assert hits
    assert all(h["count"] > 0 for h in hits)
    # Ranked by how much each has to say about it.
    assert hits == sorted(hits, key=lambda h: -h["count"])


def test_search_for_nothing_finds_nothing():
    assert docs_library.search("   ") == []


def test_search_misses_cleanly():
    assert docs_library.search("zzz-not-in-any-document") == []
