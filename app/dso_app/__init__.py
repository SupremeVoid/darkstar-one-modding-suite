"""Desktop application for the Darkstar One modding tools.

Layering rule, from docs/ARCHITECTURE.md §1: this package may import ``dsotools``; nothing
in ``dsotools`` may import this.  ``session.py`` and ``diagnostics.py`` hold the
logic and import no Qt, so they are testable without a display.
"""

__all__ = []
