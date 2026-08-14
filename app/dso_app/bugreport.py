"""
Composing a bug report, and the URL that opens it prefilled.

A bug report is only worth the round trip it saves. "It crashed" costs two
messages before anything can be looked at; a report that already carries the
build, the platform and what was open costs none. So this fills in everything
that can be answered without asking.

**It fills in nothing that identifies the machine.** No install path, no mod
path, no user name, no folder layout — those are exactly what a bug report does
not need and what nobody should paste into a public issue tracker by accident.
What is stated is *whether* a game and a mod are open, and which edition the
game is, because that changes behaviour; where they live does not.

Qt-free, so the body can be tested. The Qt version is passed in rather than
imported, which is also what lets a test assert on it.

Nothing here sends anything. It builds a URL; the caller opens a browser at
GitHub's *new issue* form, and the person reading it decides what to submit.
"""

from __future__ import annotations

import platform
import sys
import urllib.parse
from typing import Optional

VERSION = "1.0"

#: Where issues go.
REPOSITORY = "https://github.com/SupremeVoid/darkstar-one-modding-suite"

#: GitHub accepts long query strings, but browsers and proxies are less
#: generous and a truncated URL fails in a way nobody can diagnose.  The body
#: is trimmed to this before encoding; anything longer belongs in an
#: attachment the reporter adds themselves.
MAX_BODY = 6000

TEMPLATE = """\
### What happened

<!-- What did you do, what did you expect, what happened instead? -->


### Steps to reproduce

1.
2.
3.


### Files involved

<!-- Which asset or mod, if it matters. Please do not paste full paths. -->


---

### Build

{facts}

<!-- If the app wrote a crash report, attaching it helps a great deal.
     Help ▸ About says where reports are written. -->
"""


def environment(*, qt_version: Optional[str] = None,
                app_version: Optional[str] = None,
                frozen: bool = False) -> dict:
    """The facts about this build that a report should carry.

    Deliberately narrow.  Everything here is a property of the *software*, and
    none of it says anything about the person running it.
    """
    return {
        "Suite": app_version or "unknown",
        "Python": platform.python_version(),
        "Qt (PySide6)": qt_version or "unknown",
        "OS": f"{platform.system()} {platform.release()}",
        "Machine": platform.machine(),
        "Packaged build": "yes" if frozen else "no (running from source)",
    }


def session_facts(*, game_open: bool, mod_open: bool,
                  game_kind: Optional[str] = None) -> dict:
    """What was open, without saying where any of it lives."""
    return {
        "Game folder open": "yes" if game_open else "no",
        "Game source": game_kind or "—",
        "Mod open": "yes" if mod_open else "no",
    }


def body(facts: dict) -> str:
    """The issue body: the template, with a facts table filled in."""
    rows = "\n".join(f"| {k} | {v} |" for k, v in facts.items())
    table = f"| | |\n|---|---|\n{rows}"
    return TEMPLATE.format(facts=table)


def issue_url(title: str, text: str, *, repository: str = REPOSITORY,
              labels: str = "bug") -> str:
    """GitHub's *new issue* form, prefilled.

    Opening this submits nothing: it is a form the reporter reads, edits and
    sends themselves.  That is the point — a tool that filed issues on someone's
    behalf would be filing them without their having read what it wrote.
    """
    if len(text) > MAX_BODY:
        text = text[:MAX_BODY] + "\n\n<!-- truncated -->"
    query = urllib.parse.urlencode(
        {"title": title, "body": text, "labels": labels},
        quote_via=urllib.parse.quote,
    )
    return f"{repository.rstrip('/')}/issues/new?{query}"


def compose(*, qt_version: Optional[str] = None,
            app_version: Optional[str] = None,
            game_open: bool = False, mod_open: bool = False,
            game_kind: Optional[str] = None,
            frozen: Optional[bool] = None) -> tuple:
    """``(title, body, url)`` for a fresh report."""
    if frozen is None:
        frozen = bool(getattr(sys, "frozen", False))
    facts = environment(qt_version=qt_version, app_version=app_version,
                        frozen=frozen)
    facts.update(session_facts(game_open=game_open, mod_open=mod_open,
                               game_kind=game_kind))
    text = body(facts)
    title = ""          # left empty: a title the reporter writes is a better one
    return title, text, issue_url(title, text)


__all__ = ["VERSION", "REPOSITORY", "MAX_BODY", "TEMPLATE",
           "environment", "session_facts", "body", "issue_url", "compose"]
