"""
Byte-exact XML: read a document, edit part of it, write back an identical file
everywhere you did not touch.

Ascaron shipped several XML formats -- ``WalhallaScene`` scenes, ``ASE_Database``
sound databases -- and modders hand-edit all of them.  Diffing a mod against
stock is a core feature here, so a save that reflows a file turns a one-line
edit into a whole-file diff and destroys the only tool anyone has for seeing
what a mod actually changed.

Reconstructing a start tag from ``el.attrib`` throws away everything about it
that XML does not consider meaningful, and Ascaron's exporters used all of it:

* ``<AABB ... />`` versus ``<AABB .../>`` -- whether a space precedes the
  self-closing slash;
* nodes written one attribute per line with the ``=`` signs aligned, which a
  rebuilt tag collapses onto one line;
* the exact declaration, which differs per format (scenes carry none,
  ``KlangErzeugerDefault.xml`` declares ``ISO-8859-1``).

So each element's source spelling is recorded and re-emitted **verbatim only
while the element still says what it said when it was read**.  Editing an
attribute invalidates that one element's recorded tag and it falls back to a
reconstructed one -- a minimal diff on precisely what changed.

This module was extracted from ``scene.py``, where it was proved on 1,187 stock
scenes round-tripping byte-identically; that corpus check is still the thing
that guards it.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional

VERSION = "1.0"

#: XML's five, plus the two that only matter inside an attribute value.
_ATTR_ESCAPES = (("&", "&amp;"), ("<", "&lt;"), (">", "&gt;"),
                 ('"', "&quot;"), ("\t", "&#9;"), ("\n", "&#10;"), ("\r", "&#13;"))
_TEXT_ESCAPES = (("&", "&amp;"), ("<", "&lt;"), (">", "&gt;"))


def esc_attr(value: str) -> str:
    for a, b in _ATTR_ESCAPES:
        value = value.replace(a, b)
    return value


def esc_text(value: str) -> str:
    for a, b in _TEXT_ESCAPES:
        value = value.replace(a, b)
    return value


class Layout:
    """How one element's tags were spelled in the source."""

    __slots__ = ("start", "end", "tag", "attrib", "self_closing")

    def __init__(self, start: str, end: Optional[str], tag: str,
                 attrib: Dict[str, str], self_closing: bool) -> None:
        #: The whole start tag, ``<`` to ``>``, exactly as written.
        self.start = start
        #: The whole end tag, or ``None`` when the source self-closed.
        self.end = end
        self.tag = tag
        self.attrib = attrib
        self.self_closing = self_closing

    def usable_for(self, el: ET.Element) -> bool:
        """Is the recorded spelling still an honest rendering of ``el``?"""
        if el.tag != self.tag or el.attrib != self.attrib:
            return False
        # A self-closed element that has since gained children or text can no
        # longer be written as `<Tag />`.
        return self.self_closing == (not len(el) and el.text is None)


LayoutMap = Dict[ET.Element, Layout]


def write_element(el: ET.Element, out: List[str],
                  layout: Optional[LayoutMap] = None) -> None:
    lay = layout.get(el) if layout else None
    if lay is not None and lay.usable_for(el):
        out.append(lay.start)
        if lay.self_closing:
            if el.tail:
                out.append(esc_text(el.tail))
            return
        if el.text:
            out.append(esc_text(el.text))
        for child in el:
            write_element(child, out, layout)
        out.append(lay.end or ("</" + el.tag + ">"))
        if el.tail:
            out.append(esc_text(el.tail))
        return

    out.append("<" + el.tag)
    for k, v in el.attrib.items():
        out.append(f' {k}="{esc_attr(v)}"')
    children = list(el)
    if not children and el.text is None:
        out.append(" />")
    else:
        out.append(">")
        if el.text:
            out.append(esc_text(el.text))
        for child in children:
            write_element(child, out, layout)
        out.append("</" + el.tag + ">")
    if el.tail:
        out.append(esc_text(el.tail))


def serialise(root: ET.Element, *, decl: bytes = b"", newline: str = "\r\n",
              trailing_newline: bool = True, layout: Optional[LayoutMap] = None,
              encoding: str = "cp1252") -> bytes:
    """Serialise a tree back to bytes, preserving the original layout.

    ``newline`` exists because XML 1.0 §2.11 requires a parser to normalise
    every line break to ``\\n`` before the application sees it.  Ascaron's files
    are CRLF throughout, so a naive re-emit shrinks every file by one byte per
    line and turns a no-op save into a whole-file diff.

    ``trailing_newline`` exists because not every file has one -- every
    Ascaron-authored scene does, and one modder-authored file in the wild did
    not.  Appending one unconditionally was a two-byte difference that broke
    byte-exact round-trip on exactly that file.

    ``decl`` is the source's own declaration *including* its line break, which
    differs per format and must not be normalised: scenes carry
    ``<?xml version="1.0"?>``, the stock sound database declares
    ``ISO-8859-1``.
    """
    out: List[str] = []
    write_element(root, out, layout)
    body = "".join(out)
    if trailing_newline and not body.endswith("\n"):
        body += "\n"
    elif not trailing_newline:
        body = body.rstrip("\n")
    if newline != "\n":
        body = body.replace("\n", newline)
    return decl + body.encode(encoding, errors="xmlcharrefreplace")


# --------------------------------------------------------------------------
# reading the source's own spelling
# --------------------------------------------------------------------------

#: The only three bytes that can end a tag or hide the end of one.
_TAG_STOP = re.compile(rb"""["'>]""")

#: Matches the declaration and whatever line break follows it.
_DECL = re.compile(rb"^\s*<\?xml[^>]*\?>[\r\n]*")


def split_declaration(data: bytes) -> bytes:
    """The leading ``<?xml ...?>`` plus its line break, or ``b""``."""
    found = _DECL.match(data)
    return found.group(0) if found else b""


def scan_tag_end(data: bytes, i: int) -> int:
    """Index just past the ``>`` closing the tag that starts at ``i``.

    Only attribute values can quote a ``>`` inside a tag, so tracking quotes is
    the whole of it -- comments, PIs and CDATA cannot occur within one.
    """
    quote = None
    for m in _TAG_STOP.finditer(data, i):
        ch = m.group()
        if quote is None:
            if ch == b">":
                return m.end()
            quote = ch
        elif ch == quote:
            quote = None
    return len(data)


def capture_layout(parsed: bytes, original: Optional[bytes] = None) -> List[Layout]:
    """One :class:`Layout` per element, in document order.

    ``parsed`` is what expat can read; ``original`` is what the file actually
    says.  They may differ only where a caller repaired something that does not
    change any offset -- scene XML corrects a close tag's *case*, which never
    changes its length -- so every offset taken from one indexes the other.
    """
    import xml.parsers.expat

    if original is None:
        original = parsed
    p = xml.parsers.expat.ParserCreate()
    out: List[Optional[Layout]] = []
    stack: List[tuple] = []

    def raw(lo: int, hi: int) -> str:
        # Stored with `\n` endings because :func:`serialise` re-applies the
        # source's own newline to the whole body at the end.  A tag kept as
        # `\r\n` would come back out as `\r\r\n` -- and a start tag really can
        # span lines: some nodes put one attribute per line.
        text = original[lo:hi].decode("utf-8", "replace")
        return text.replace("\r\n", "\n").replace("\r", "\n")

    def on_start(name, attrs):
        out.append(None)
        stack.append((len(out) - 1, p.CurrentByteIndex, name, dict(attrs)))

    def on_end(_name):
        slot, start, tag, attrib = stack.pop()
        raw_start = raw(start, scan_tag_end(parsed, start))
        # Read self-closing off the tag itself.  Expat's byte index for the end
        # of an empty element is not dependable -- it coincides with the start
        # for some and not for others -- and `<Tag />` says so in the source.
        if raw_start.endswith("/>"):
            out[slot] = Layout(raw_start, None, tag, attrib, True)
            return
        close = p.CurrentByteIndex
        out[slot] = Layout(
            raw_start, raw(close, scan_tag_end(parsed, close)), tag, attrib, False
        )

    p.StartElementHandler = on_start
    p.EndElementHandler = on_end
    p.Parse(parsed, True)
    return [lay for lay in out if lay is not None]


def layout_for(root: ET.Element, readable: bytes,
               original: Optional[bytes] = None) -> LayoutMap:
    """Pair each element with its source spelling, or give up cleanly.

    Layout is an optimisation, never a gate: if the two sequences disagree the
    map is dropped rather than pairing tags with the wrong elements. A rebuilt
    file is a worse diff; a misattributed tag is simply wrong.
    """
    try:
        spellings = capture_layout(readable, original)
    except Exception:  # noqa: BLE001
        return {}
    elements = list(root.iter())
    if len(spellings) != len(elements):
        return {}
    return dict(zip(elements, spellings))


__all__ = [
    "VERSION", "Layout", "LayoutMap", "esc_attr", "esc_text", "write_element",
    "serialise", "scan_tag_end", "capture_layout", "layout_for",
    "split_declaration",
]
