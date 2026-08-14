"""
dsotools -- format libraries and modding operations for Darkstar One (Ascaron, 2006).

Standalone by design.  This package knows nothing about the GUI, nothing about
the CLI, and imports no UI toolkit.  Anyone can ``pip install dsotools`` and
write a five-line script against it; the desktop application is just its largest
consumer.

Layering:

    dsotools.formats   parsers and builders, one module per file format
    dsotools.convert   format <-> interchange (glTF, PNG, OBJ)
    dsotools.vfs       the game's assets as one namespace, with load order
    dsotools.edit      high-level operations that keep linked files in step
    dsotools.validate  the diagnostic rule engine
    dsotools.project   mod projects, diff against stock, deployment

The core is pure standard library and stays that way; pixel work lives behind
the ``image`` extra (Pillow, numpy).  ``import dsotools`` must never require it.
"""

from .errors import (
    BuildError,
    DsoError,
    ParseError,
    ProjectError,
    ResolutionError,
    UnsupportedFormat,
    ValidationError,
)

__version__ = "1.0.0"

__all__ = [
    "__version__",
    "DsoError",
    "ParseError",
    "UnsupportedFormat",
    "BuildError",
    "ResolutionError",
    "ValidationError",
    "ProjectError",
]
