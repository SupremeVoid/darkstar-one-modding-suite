#!/usr/bin/env python3
"""
Recover the Lua API the game **actually registers**, straight from the exe.

    python tools/exe_api_scan.py --game "<install>"
    python tools/exe_api_scan.py --game "<install>" --out src/dsotools/data/lua_engine.json

WHY THIS EXISTS
---------------
``lua_api.json`` says what Ascaron *documented* in 2006. This says what the
shipped executable *registers*, and the two disagree in both directions:

* **5 documented functions do not exist** -- ``NGUI.Enable``, ``NGUI.Disable``,
  ``NContainer.SetArtefact`` and both ``NTutorial`` entries. A mod calling one
  of those fails at runtime with nothing to explain it.
* **30 exist that nobody documented**, including ``NPlayer.FirePlasma``,
  ``NObject.EnterActionTurret`` and ``NShip.SetHidden``.

Validating a script against the documentation alone therefore produces both
false alarms and false silence. This closes that gap.

HOW
---
The engine registers each namespace as a table in the data section: a pointer
to the namespace name, then entries of ``(name pointer, function pointer,
count)``. Entries are 12 bytes apart and the namespace header sits 8 bytes
before the first one -- so the tables can be recovered by scanning for adjacent
(identifier pointer, code pointer) pairs and grouping them by stride, with no
disassembler involved. Verified against the GOG build: 21 namespaces, 219
functions.

**Both editions work.** The Steam copy is DRM-wrapped, but the wrapper
encrypts ``.text`` only: ``.rdata``, ``.data``, ``.rsrc`` and ``.reloc`` are
byte-identical to the GOG build, and the tables live in ``.data``. The scan
never reads code -- it only checks that a pointer lands inside ``.text``'s
address range, which the section header gives whether or not the bytes are
readable. Verified by scanning both editions and comparing: the same 219
functions.
"""

from __future__ import annotations

import argparse
import json
import math
import hashlib
import os
import re
import struct
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if os.path.join(ROOT, "src") not in sys.path:
    sys.path.insert(0, os.path.join(ROOT, "src"))

DEFAULT_OUT = os.path.join(ROOT, "src", "dsotools", "data", "lua_engine.json")

#: Functions measured to be present but inert -- they never read their
#: argument. Recorded here because no scan of the tables can see it: the
#: registration looks exactly like a working one.
KNOWN_STUBS = {
    "NDebug.Message": "39 bytes: builds an empty return table, never reads the "
                      "argument. Writes nothing anywhere, despite the "
                      "documentation promising a message in the script channel",
    "NDebug.SetPlasmaEffect": "3 bytes: a bare return",
}

IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{1,40}$")


class Image:
    """Just enough PE to turn addresses into bytes."""

    def __init__(self, path):
        with open(path, "rb") as handle:
            self.data = handle.read()
        if self.data[:2] != b"MZ":
            raise ValueError(f"{path} is not a PE image")
        pe = struct.unpack_from("<I", self.data, 0x3C)[0]
        opt = pe + 24
        self.base = struct.unpack_from("<I", self.data, opt + 28)[0]
        n_sections = struct.unpack_from("<H", self.data, pe + 6)[0]
        opt_size = struct.unpack_from("<H", self.data, pe + 20)[0]
        self.sections = []
        for i in range(n_sections):
            off = opt + opt_size + i * 40
            name = self.data[off:off + 8].rstrip(b"\0").decode("latin-1")
            vsize, vaddr, rawsize, rawptr = struct.unpack_from("<IIII", self.data, off + 8)
            self.sections.append((name, vaddr, vsize, rawptr, rawsize))

    def section(self, want):
        for entry in self.sections:
            if entry[0] == want:
                return entry
        return None

    def offset_of(self, va):
        rva = va - self.base
        for _name, vaddr, vsize, rawptr, rawsize in self.sections:
            if vaddr <= rva < vaddr + max(vsize, rawsize):
                return rawptr + (rva - vaddr)
        return None

    def string_at(self, va):
        off = self.offset_of(va)
        if off is None:
            return None
        end = self.data.find(b"\0", off)
        if end < 0 or end - off > 42:
            return None
        text = self.data[off:end].decode("latin-1", "replace")
        return text if IDENT.match(text) else None

    def dword(self, off):
        return struct.unpack_from("<I", self.data, off)[0]

    def is_wrapped(self):
        """Is ``.text`` encrypted by a DRM wrapper?

        Reported, not refused.  The registration tables are in ``.data``, which
        no wrapper touches, so a wrapped image scans exactly the same -- the
        two editions differ in ``.text`` alone.
        """
        text = self.section(".text")
        if text is None:
            return True
        raw = self.data[text[3]:text[3] + text[4]]
        if not raw:
            return True
        counts = Counter(raw)
        entropy = -sum((c / len(raw)) * math.log2(c / len(raw)) for c in counts.values())
        return entropy > 7.5 or self.section(".bind") is not None

    #: The sections the tables live in.  ``.text`` is excluded because a DRM
    #: wrapper encrypts it, and ``.bind`` because only the wrapped copy has one.
    FINGERPRINT_SECTIONS = (".rdata", ".data")

    def data_fingerprint(self):
        """Identity of the data the scan reads.

        Equal for the GOG and Steam copies of the same build -- they differ in
        ``.text`` and in the wrapper's extra ``.bind`` section, and in nothing
        else.  That makes this the right thing to record as "which build is
        this", where a hash of the whole file is not.
        """
        digest = hashlib.sha1()
        for name in self.FINGERPRINT_SECTIONS:
            found = self.section(name)
            if found is None:
                continue
            digest.update(name.encode("latin-1"))
            digest.update(self.data[found[3]:found[3] + found[4]])
        return digest.hexdigest()


def scan(path):
    """-> {namespace: [(function, address)]}"""
    image = Image(path)
    text = image.section(".text")
    if text is None:
        raise SystemExit(f"{path} has no .text section")
    code_lo = image.base + text[1]
    code_hi = code_lo + text[2]

    pairs = []
    for name, vaddr, _vsize, rawptr, rawsize in image.sections:
        if name not in (".data", ".rdata"):
            continue
        base_va = image.base + vaddr
        for i in range(0, max(rawsize - 8, 0), 4):
            label = image.string_at(image.dword(rawptr + i))
            target = image.dword(rawptr + i + 4)
            if label and code_lo <= target < code_hi:
                pairs.append((base_va + i, label, target))

    # Entries are 12 bytes apart; the namespace name sits 8 bytes before the
    # first entry of each run.
    by_address = {a: (n, f) for a, n, f in pairs}
    tables, used = {}, set()
    for address in sorted(by_address):
        if address in used:
            continue
        run, cursor = [], address
        while cursor in by_address:
            run.append((cursor, *by_address[cursor]))
            used.add(cursor)
            cursor += 12
        namespace = image.string_at(image.dword(image.offset_of(run[0][0] - 8)))
        if not namespace:
            continue
        tables.setdefault(namespace, []).extend((n, f) for _a, n, f in run)
    return tables


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.strip().split("\n")[0])
    ap.add_argument("--game", required=True, help="any installation; both editions work")
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--print", action="store_true", help="list what was found")
    args = ap.parse_args(argv)

    exe = os.path.join(args.game, "DarkStarOne.exe")
    if not os.path.isfile(exe):
        print(f"no DarkStarOne.exe in {args.game}")
        return 2

    image = Image(exe)
    tables = scan(exe)
    engine = {ns: sorted(n for n, _f in fns) for ns, fns in tables.items()
              if ns.startswith("N") or ns in ("MissionLib", "CameraLib")}
    total = sum(len(v) for v in engine.values())

    from dsotools import scriptdoc
    documented = scriptdoc.bundled()
    doc_engine = set()
    if documented:
        doc_engine = {s["qualified"] for s in documented["symbols"]
                      if s["kind"] == "command"
                      and not s["qualified"].startswith("MissionLib.")}
    implemented = {f"{ns}.{n}" for ns, names in engine.items() for n in names}

    database = {
        "schema": 1,
        "source": os.path.basename(exe),
        "namespaces": {k: engine[k] for k in sorted(engine)},
        "count": total,
        # Documented but never registered: calling one fails at runtime.
        "missing": sorted(doc_engine - implemented),
        # Registered but undocumented: real, just unwritten-about.
        "undocumented": sorted(implemented - doc_engine),
        "stubs": KNOWN_STUBS,
    }
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(database, handle, ensure_ascii=False, indent=1, sort_keys=False)

    database["data_fingerprint"] = image.data_fingerprint()
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(database, handle, ensure_ascii=False, indent=1, sort_keys=False)

    print(f"{args.out}")
    print(f"   {total} functions in {len(engine)} namespaces")
    print(f"   .text is {'DRM-wrapped (does not matter here)' if image.is_wrapped() else 'plain'}"
          f"; data fingerprint {database['data_fingerprint'][:12]}")
    print(f"   {len(database['missing'])} documented but absent: "
          f"{', '.join(database['missing'])}")
    print(f"   {len(database['undocumented'])} registered but undocumented")
    print(f"   {len(KNOWN_STUBS)} known to do nothing")
    if args.print:
        for ns in sorted(engine):
            print(f"   {ns:14s} {len(engine[ns]):3d}  {', '.join(engine[ns])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
