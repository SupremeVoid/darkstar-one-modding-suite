"""High-level operations that keep linked files consistent.

The library's format modules read and write one file at a time.  These
operations exist because the game's data is *not* one file at a time: an atlas
page, its ``.tex`` index and its ``.anim`` drawables are three files holding one
truth, and editing one of them alone is the most common way a mod breaks
silently.
"""

from . import atlas

__all__ = ["atlas"]
