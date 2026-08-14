"""
``.res`` string tables -- the only way a mod can put its own text on screen.

Every engine call that shows words takes a *StringId*, never a literal, and
resolves it against the loaded string tables (``strings\\ENG\\global.res`` for
the game, ``<mod>\\strings\\user_strings.res`` for a mod).  The file stores
only a 32-bit hash of the id, so an id cannot be recovered from a table -- but
it can be recomputed, which is all a writer needs.

Layout, confirmed against ``global.res`` (9,378 entries), ``Ascaron.Exception.res``
(88) and the modding tutorial's ``user_strings.res`` (53) -- 0 malformed
records, string data ending exactly at EOF in all three:

    u32        count
    count *    u32 hash        the id, hashed by :func:`string_hash`
               u32 offset      of the text, relative to the end of ``count``
               u32 reserved    always 0 in every file examined
               u32 length      of the text in *bytes*, i.e. 2 per character
    ...        the text, UTF-16LE, packed back to back, no terminators

Offsets are relative to byte 4, so the first entry's offset equals
``count * 16``.  Entries are *not* sorted -- the writer emitted them in
hashtable order -- so a reader must not binary-search.

The hash was recovered from ``Xml2ResConverter.exe`` (``Converter.Hash``,
44 bytes of IL) and then ground-truthed by invoking that method directly on all
53 tutorial ids plus 8 probes: 61/61.  The subtlety that defeated every earlier
guess is IL opcode ``0x5D`` -- signed ``rem``, not ``rem.un``.  The accumulator
is a 32-bit *signed* int that wraps on the multiply, so it goes negative, and
the remainder then keeps that sign; only the final value is reinterpreted as
unsigned.  That is why observed keys exceed the 999,999,991 modulus.
"""

from __future__ import annotations

import struct
from typing import Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

from ..errors import BuildError, ParseError

VERSION = "1.0"

#: Multiplier and modulus of ``Converter.Hash``.
HASH_FACTOR = 113
HASH_MODULUS = 999999991

#: Bytes per table entry.
ENTRY_SIZE = 16

#: Where a mod's own table has to live for the engine to load it.
MOD_TABLE = "strings\\user_strings.res"

_SIGN = 1 << 31
_WRAP = 1 << 32


def string_hash(identifier: str) -> int:
    """Hash a StringId to the 32-bit key a ``.res`` stores.

    Faithful to the shipped converter, including its signed overflow:
    ``h = (h * 113 + c) % 999999991`` in 32-bit signed arithmetic, where both
    the multiply and the remainder follow C# semantics (wrap, and a remainder
    that takes the sign of the dividend).  The result is read back unsigned.
    """
    h = 0
    for ch in identifier.encode("latin-1", "replace"):
        v = (h * HASH_FACTOR) & 0xFFFFFFFF
        if v >= _SIGN:
            v -= _WRAP
        v = (v + ch) & 0xFFFFFFFF
        if v >= _SIGN:
            v -= _WRAP
        h = -(-v % HASH_MODULUS) if v < 0 else v % HASH_MODULUS
    return h & 0xFFFFFFFF


class StringEntry:
    """One row of a table: a hashed id and its text."""

    __slots__ = ("hash", "text", "reserved")

    def __init__(self, hash: int, text: str, reserved: int = 0) -> None:
        self.hash = hash & 0xFFFFFFFF
        self.text = text
        self.reserved = reserved

    @classmethod
    def for_id(cls, identifier: str, text: str) -> "StringEntry":
        return cls(string_hash(identifier), text)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"StringEntry(0x{self.hash:08x}, {self.text[:32]!r})"


class StringTable:
    """A parsed ``.res``.

    Entry order is preserved so that :func:`build` can reproduce the input
    byte for byte; lookups go through :meth:`text` and do not depend on it.
    """

    __slots__ = ("entries", "path")

    def __init__(self, entries: Optional[List[StringEntry]] = None,
                 path: Optional[str] = None) -> None:
        self.entries: List[StringEntry] = list(entries or ())
        self.path = path

    def __len__(self) -> int:
        return len(self.entries)

    def __iter__(self) -> Iterator[StringEntry]:
        return iter(self.entries)

    def by_hash(self) -> Dict[int, str]:
        """Map key -> text.  A later duplicate wins, as it does in a hashtable."""
        return {e.hash: e.text for e in self.entries}

    def text(self, identifier: str) -> Optional[str]:
        """Text for a StringId, or ``None`` if this table does not carry it."""
        return self.by_hash().get(string_hash(identifier))

    def has(self, identifier: str) -> bool:
        return string_hash(identifier) in self.by_hash()

    def resolve(self, identifiers: Iterable[str]) -> Dict[str, Optional[str]]:
        """Look up many ids at once, sharing one hash index."""
        index = self.by_hash()
        return {i: index.get(string_hash(i)) for i in identifiers}

    def collisions(self) -> List[Tuple[int, int]]:
        """``(hash, count)`` for every key used by more than one entry.

        The converter rejected these outright: two ids hashing alike means one
        of the two texts is unreachable.
        """
        seen: Dict[int, int] = {}
        for e in self.entries:
            seen[e.hash] = seen.get(e.hash, 0) + 1
        return sorted((h, n) for h, n in seen.items() if n > 1)


def parse(data: bytes, path: Optional[str] = None) -> StringTable:
    """Read a ``.res``.  Raises :class:`ParseError` on anything inconsistent."""
    if len(data) < 4:
        raise ParseError("too short to hold a string-table header", path=path)
    count, = struct.unpack_from("<I", data, 0)
    table_end = 4 + count * ENTRY_SIZE
    if count > (len(data) - 4) // ENTRY_SIZE:
        raise ParseError(
            f"header claims {count} entries but the file only holds "
            f"{(len(data) - 4) // ENTRY_SIZE}",
            path=path, offset=0,
        )

    entries: List[StringEntry] = []
    for i in range(count):
        at = 4 + i * ENTRY_SIZE
        key, offset, reserved, length = struct.unpack_from("<4I", data, at)
        start = 4 + offset
        if length % 2:
            raise ParseError(
                f"entry {i} has an odd UTF-16 byte length {length}",
                path=path, offset=at + 12,
            )
        if start < table_end or start + length > len(data):
            raise ParseError(
                f"entry {i} points at bytes {start}..{start + length}, "
                f"outside the string data",
                path=path, offset=at + 4,
            )
        text = data[start:start + length].decode("utf-16-le")
        entries.append(StringEntry(key, text, reserved))
    return StringTable(entries, path=path)


def build(table: StringTable) -> bytes:
    """Serialise a table.  Round-trips :func:`parse` byte for byte.

    Strings are packed in entry order starting at ``count * 16``, which is what
    the converter's ``ResFile.WriteToFile`` does.
    """
    count = len(table.entries)
    out = bytearray(struct.pack("<I", count))
    blobs = []
    cursor = count * ENTRY_SIZE
    for e in table.entries:
        try:
            blob = e.text.encode("utf-16-le")
        except UnicodeEncodeError as exc:  # pragma: no cover - surrogates only
            raise BuildError(f"text for 0x{e.hash:08x} is not encodable: {exc}",
                             path=table.path) from exc
        out += struct.pack("<4I", e.hash & 0xFFFFFFFF, cursor, e.reserved, len(blob))
        blobs.append(blob)
        cursor += len(blob)
    for blob in blobs:
        out += blob
    return bytes(out)


def from_pairs(pairs: Sequence[Tuple[str, str]]) -> StringTable:
    """Build a table from ``(StringId, text)`` pairs, in the order given.

    Raises :class:`BuildError` on a hash collision between two different ids --
    silently dropping one is how a mod ends up with blank dialogue.
    """
    seen: Dict[int, str] = {}
    entries: List[StringEntry] = []
    for identifier, text in pairs:
        key = string_hash(identifier)
        if key in seen and seen[key] != identifier:
            raise BuildError(
                f"'{identifier}' and '{seen[key]}' both hash to 0x{key:08x}; "
                f"rename one of them"
            )
        seen[key] = identifier
        entries.append(StringEntry(key, text))
    return StringTable(entries)


def is_string_table(data: bytes) -> bool:
    """Cheap sniff: does this look like a ``.res``?  No exceptions."""
    try:
        parse(data)
    except (ParseError, UnicodeDecodeError, struct.error):
        return False
    return True
