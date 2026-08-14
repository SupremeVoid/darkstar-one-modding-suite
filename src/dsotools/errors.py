"""
Exception hierarchy for dsotools.

Design rules, in force across the whole package:

* Every exception raised by this library derives from :class:`DsoError`, so a
  caller can wrap any operation in one ``except``.
* A library never calls ``sys.exit`` and never raises ``SystemExit``.  The CLI
  front-ends translate exceptions into exit codes; the GUI translates them into
  diagnostics.  (The pre-library ``png2aim.py`` raised ``SystemExit`` from code
  that was otherwise reusable -- that is the bug this rule exists to prevent.)
* A library never prints.  Progress is a callback, logging goes through the
  ``logging`` module.
* Every error carries the ``path`` it happened in where one is known, and the
  byte ``offset`` where that is meaningful.  The GUI turns those two fields
  directly into a clickable problem-list entry, so filling them in is not
  optional politeness -- it is the feature.

``UnsupportedFormat`` is deliberately distinct from ``ParseError``:
"I recognise this and refuse to guess" is a different situation from
"this is malformed", and the UI says different things about them.
"""

from __future__ import annotations

from typing import Optional


class DsoError(Exception):
    """Root of every error this package raises."""

    #: Short stable identifier used by the diagnostics engine, e.g. ``MDL001``.
    #: ``None`` on errors that are not part of the published rule catalogue.
    code: Optional[str] = None

    def __init__(
        self,
        message: str,
        *,
        path: Optional[str] = None,
        offset: Optional[int] = None,
        code: Optional[str] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.path = path
        self.offset = offset
        if code is not None:
            self.code = code

    def __str__(self) -> str:  # pragma: no cover - formatting only
        bits = []
        if self.code:
            bits.append(f"[{self.code}]")
        if self.path:
            bits.append(f"{self.path}:")
        if self.offset is not None:
            bits.append(f"at 0x{self.offset:x}:")
        bits.append(self.message)
        return " ".join(bits)


class ParseError(DsoError):
    """A file claimed to be a known format but does not conform to it."""


class UnsupportedFormat(DsoError):
    """A recognised container using a variant this build cannot handle.

    Raised rather than guessing.  ``aim`` raises this for encodings with no
    decoder; ``threedo`` raises it for a LOD above the 16-bit index ceiling.
    """


class BuildError(DsoError):
    """In-memory model cannot be serialised back to a valid file."""


class ResolutionError(DsoError):
    """A reference could not be resolved through the virtual file system."""

    def __init__(
        self,
        message: str,
        *,
        reference: Optional[str] = None,
        tried: Optional[list] = None,
        **kw,
    ) -> None:
        super().__init__(message, **kw)
        self.reference = reference
        #: Every candidate path that was attempted, in order.  The GUI shows
        #: this verbatim -- "where did you look?" is the first question a user
        #: asks about a failed reference.
        self.tried = list(tried or ())


class ValidationError(DsoError):
    """A structural rule was violated.

    Raised only by the strict entry points.  The diagnostics engine reports
    findings as :class:`dsotools.validate.Diagnostic` objects instead, because
    it must keep going after the first problem.
    """


class ProjectError(DsoError):
    """The mod project is inconsistent, or an operation on it is not allowed."""


__all__ = [
    "DsoError",
    "ParseError",
    "UnsupportedFormat",
    "BuildError",
    "ResolutionError",
    "ValidationError",
    "ProjectError",
]
