#!/usr/bin/env python3
"""
Find out *which* DLL breaks a PySide6 import, and why.

    python tools/pyside_doctor.py

WHY THIS EXISTS
---------------
Windows reports a failed DLL load as::

    ImportError: DLL load failed while importing QtCore:
    The specified procedure could not be found.        (ERROR_PROC_NOT_FOUND, 127)

That message names neither the DLL nor the procedure.  It does not mean PySide6
is missing -- "procedure not found" means a DLL *was* located but did not export
something its caller wanted, i.e. a version mismatch somewhere in a dependency
chain six or seven links long.

Checking that the files exist is not enough; the first version of this tool did
exactly that, reported "nothing obviously wrong", and was useless.  So this one
loads each library individually with ``ctypes``, parses the PE import table to
find what Qt6Core actually depends on, resolves each dependency the way Windows
would, and prints the file version of what it finds.

The packaged application will meet this same failure on users' machines with no
console to read it (``docs/ARCHITECTURE.md`` §7).  This logic is meant to move into the
app's diagnostics panel, which is why it is a standalone module rather than
buried in the spike.
"""

from __future__ import annotations

import os
import struct
import sys

IS_WINDOWS = os.name == "nt"


# --------------------------------------------------------------------------
# minimal PE reader -- just enough to list a DLL's imports and architecture
# --------------------------------------------------------------------------

MACHINE = {0x014C: "x86 (32-bit)", 0x8664: "x64", 0xAA64: "ARM64"}


def pe_info(path):
    """Return ``{'machine': str, 'subsystem': int, 'imports': [dll names]}``.

    ``subsystem`` is 2 for a GUI binary and 3 for a console one.  It is read
    here rather than in a second PE reader elsewhere: ``packaging/build.py``
    needs it to confirm the shipped executable really is windowed, and two
    copies of this offset arithmetic would be one copy too many.
    """
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError as exc:
        return {"error": str(exc)}
    if len(data) < 0x40 or data[:2] != b"MZ":
        return {"error": "not a PE file"}

    pe = struct.unpack_from("<I", data, 0x3C)[0]
    if data[pe : pe + 4] != b"PE\0\0":
        return {"error": "bad PE signature"}

    machine, nsections = struct.unpack_from("<HH", data, pe + 4)
    opt_size = struct.unpack_from("<H", data, pe + 20)[0]
    opt = pe + 24
    magic = struct.unpack_from("<H", data, opt)[0]
    if magic not in (0x10B, 0x20B):
        return {"error": f"unknown optional header magic 0x{magic:x}"}
    # Subsystem sits at offset 68 of the optional header in both PE32 and PE32+.
    subsystem = struct.unpack_from("<H", data, opt + 68)[0]
    # DataDirectory sits after the fixed part of the optional header: 112 bytes
    # for PE32+, 96 for PE32.  Entry 1 is the import table.
    dd = opt + (112 if magic == 0x20B else 96)
    import_rva, _import_size = struct.unpack_from("<II", data, dd + 8)

    sections = []
    sec = opt + opt_size
    for i in range(nsections):
        base = sec + i * 40
        vaddr, _vsize = struct.unpack_from("<II", data, base + 12)[0], 0
        vsize = struct.unpack_from("<I", data, base + 8)[0]
        rawsize, rawptr = struct.unpack_from("<II", data, base + 16)
        sections.append((vaddr, max(vsize, rawsize), rawptr))

    def rva_to_off(rva):
        for vaddr, size, rawptr in sections:
            if vaddr <= rva < vaddr + size:
                return rawptr + (rva - vaddr)
        return None

    imports = []
    off = rva_to_off(import_rva) if import_rva else None
    if off:
        while off + 20 <= len(data):
            name_rva = struct.unpack_from("<I", data, off + 12)[0]
            if name_rva == 0:
                break
            noff = rva_to_off(name_rva)
            if noff is None:
                break
            end = data.find(b"\0", noff)
            imports.append(data[noff:end].decode("ascii", "replace"))
            off += 20

    return {
        "machine": MACHINE.get(machine, hex(machine)),
        "subsystem": subsystem,
        "imports": imports,
    }


def file_version(path):
    """Windows file version as a string, or None."""
    if not IS_WINDOWS:
        return None
    try:
        import ctypes
        from ctypes import wintypes

        ver = ctypes.WinDLL("version.dll")
        size = ver.GetFileVersionInfoSizeW(ctypes.c_wchar_p(path), None)
        if not size:
            return None
        buf = ctypes.create_string_buffer(size)
        if not ver.GetFileVersionInfoW(ctypes.c_wchar_p(path), 0, size, buf):
            return None
        p = ctypes.c_void_p()
        n = wintypes.UINT()
        if not ver.VerQueryValueW(buf, ctypes.c_wchar_p("\\"), ctypes.byref(p), ctypes.byref(n)):
            return None
        # VS_FIXEDFILEINFO: dwFileVersionMS at +8, dwFileVersionLS at +12
        raw = ctypes.string_at(p, n.value)
        ms, ls = struct.unpack_from("<II", raw, 8)
        return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"
    except Exception:  # noqa: BLE001
        return None


def resolve_dll(name, extra_dirs):
    """Where Windows would most likely find ``name``."""
    dirs = list(extra_dirs)
    dirs.append(os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32"))
    dirs.extend(os.environ.get("PATH", "").split(os.pathsep))
    for d in dirs:
        if not d:
            continue
        cand = os.path.join(d, name)
        if os.path.exists(cand):
            return cand
    return None


def try_load(path):
    """ctypes-load one DLL.  Returns None on success, else the error text."""
    if not IS_WINDOWS:
        return "not Windows"
    import ctypes

    try:
        ctypes.WinDLL(path)
        return None
    except OSError as exc:
        return f"{type(exc).__name__}: {exc}"


# --------------------------------------------------------------------------


def main():  # noqa: C901 - a diagnostic reads better linearly
    import platform

    print("=" * 72)
    print("PySide6 doctor")
    print("=" * 72)
    bits = struct.calcsize("P") * 8
    print(f"  python        {platform.python_version()}  {bits}-bit")
    print(f"  executable    {sys.executable}")
    print(f"  platform      {platform.platform()}")
    in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
    print(f"  venv          {'yes' if in_venv else 'NO  (a clean venv rules out a mixed site-packages)'}")

    problems = []
    if bits != 64:
        problems.append("Python is 32-bit; PySide6 ships 64-bit wheels only.")

    # -- distributions ------------------------------------------------------
    try:
        from importlib import metadata
    except ImportError:
        metadata = None

    versions = {}
    requires_python = None
    if metadata:
        print("\n  distributions:")
        for dist in ("PySide6", "PySide6-Essentials", "PySide6-Addons", "shiboken6"):
            try:
                versions[dist] = metadata.version(dist)
                if dist == "PySide6":
                    requires_python = metadata.metadata(dist).get("Requires-Python")
            except Exception:  # noqa: BLE001
                versions[dist] = None
            print(f"    {dist:<20} {versions[dist] or '-- not installed --'}")
        if requires_python:
            print(f"    Requires-Python      {requires_python}")
            major_minor = f"{sys.version_info.major}.{sys.version_info.minor}"
            if "<" in requires_python:
                cap = requires_python.split("<")[-1].strip().lstrip("=")
                try:
                    if tuple(int(x) for x in major_minor.split(".")) >= tuple(
                        int(x) for x in cap.split(".")[:2]
                    ):
                        problems.append(
                            f"Python {major_minor} is at or beyond this PySide6's "
                            f"supported ceiling ({requires_python}). Use Python "
                            f"3.12, or a newer PySide6."
                        )
                except ValueError:
                    pass
        present = {v for v in versions.values() if v}
        if len(present) > 1:
            problems.append(
                "PySide6 components are at MISMATCHED versions ("
                + ", ".join(f"{k}={v}" for k, v in versions.items() if v)
                + "). Reinstall all four together."
            )

    # -- package layout -----------------------------------------------------
    pkg_dir = None
    try:
        import PySide6

        pkg_dir = os.path.dirname(PySide6.__file__)
        print(f"\n  package dir   {pkg_dir}")
    except Exception as exc:  # noqa: BLE001
        print(f"\n  package dir   <cannot import PySide6: {exc}>")

    shiboken_dir = None
    try:
        import shiboken6

        shiboken_dir = os.path.dirname(shiboken6.__file__)
        print(f"  shiboken dir  {shiboken_dir}")
    except Exception as exc:  # noqa: BLE001
        print(f"  shiboken dir  <cannot import shiboken6: {exc}>")
        problems.append("shiboken6 itself will not import; PySide6 cannot work without it.")

    if not IS_WINDOWS:
        print("\n  (not Windows -- DLL analysis skipped)")
        _verdict(problems)
        return 1

    search_dirs = [d for d in (pkg_dir, shiboken_dir) if d]

    # -- mixed-DLL check ----------------------------------------------------
    if pkg_dir:
        qt_dlls = sorted(f for f in os.listdir(pkg_dir) if f.lower().startswith("qt6") and f.lower().endswith(".dll"))
        print(f"\n  Qt DLLs in package: {len(qt_dlls)}")
        seen_versions = {}
        for f in qt_dlls[:200]:
            v = file_version(os.path.join(pkg_dir, f))
            seen_versions.setdefault(v, []).append(f)
        for v, files in sorted(seen_versions.items(), key=lambda kv: -len(kv[1])):
            sample = ", ".join(files[:4]) + (" ..." if len(files) > 4 else "")
            print(f"    {v or '(no version info)':<22} x{len(files):<4} {sample}")
        real = {v for v in seen_versions if v}
        if len(real) > 1:
            problems.append(
                "Qt DLLs inside the PySide6 package are at MORE THAN ONE version. "
                "pip left files behind from a previous install -- delete the "
                "PySide6/ and shiboken6/ folders in site-packages by hand, then "
                "reinstall."
            )
        if "Qt6Quick3D.dll" not in qt_dlls:
            problems.append("Qt6Quick3D.dll is absent -- PySide6-Addons is not installed.")

    # -- load test, innermost first ----------------------------------------
    print("\n  load test:")
    targets = []
    if shiboken_dir:
        targets += [
            os.path.join(shiboken_dir, f)
            for f in sorted(os.listdir(shiboken_dir))
            if f.lower().endswith(".dll")
        ]
    if pkg_dir:
        for f in ("Qt6Core.dll", "Qt6Gui.dll", "Qt6Quick3D.dll"):
            p = os.path.join(pkg_dir, f)
            if os.path.exists(p):
                targets.append(p)
    first_failure = None
    for t in targets:
        err = try_load(t)
        mark = "ok " if err is None else "FAIL"
        print(f"    [{mark}] {os.path.basename(t):<28} {err or ''}")
        if err and first_failure is None:
            first_failure = t

    # -- dependency walk of the first failure -------------------------------
    culprit = first_failure or (os.path.join(pkg_dir, "Qt6Core.dll") if pkg_dir else None)
    if culprit and os.path.exists(culprit):
        info = pe_info(culprit)
        print(f"\n  dependencies of {os.path.basename(culprit)}  [{info.get('machine', '?')}]:")
        if info.get("error"):
            print(f"    <{info['error']}>")
        for dep in info.get("imports", []):
            found = resolve_dll(dep, search_dirs)
            if not found:
                print(f"    {dep:<28} NOT FOUND")
                problems.append(f"{culprit and os.path.basename(culprit)} needs {dep}, which is not on the search path.")
                continue
            v = file_version(found)
            inside = pkg_dir and os.path.dirname(found).lower() == pkg_dir.lower()
            where = "package" if inside else found
            print(f"    {dep:<28} {v or '-':<18} {where}")
            low = dep.lower()
            if low.startswith("qt6") and not inside:
                problems.append(
                    f"{dep} is resolving to {found}, OUTSIDE the PySide6 package. "
                    "Another Qt installation is shadowing it -- remove it from PATH."
                )
            if low in ("vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll") and v:
                try:
                    build = int(v.split(".")[2])
                    if build < 30000:
                        problems.append(
                            f"{dep} is version {v}, which predates VS2019 (build 30000+). "
                            "Qt6 needs a current Microsoft Visual C++ 2015-2022 "
                            "Redistributable (x64): aka.ms/vs/17/release/vc_redist.x64.exe"
                        )
                except (IndexError, ValueError):
                    pass

    _verdict(problems)
    return 1 if problems else 0


def _verdict(problems):
    print()
    if problems:
        print("LIKELY CAUSE" + ("S" if len(problems) > 1 else "") + ":")
        for i, p in enumerate(problems, 1):
            print(f"  {i}. {p}")
    else:
        print("No cause identified from metadata or the dependency walk.")
        print("Next step -- watch the loader directly with Process Monitor:")
        print("    Process Name is python.exe        Include")
        print("    Operation    is Load Image        Include")
        print("  then run the failing import. The LAST DLL loaded before the")
        print("  failure is next to the one at fault; note where it came from.")
    print()
    print("Standard remedies, in order of how often they work:")
    print("  1. pip uninstall -y PySide6 PySide6-Essentials PySide6-Addons shiboken6")
    print("     then delete any leftover PySide6/ and shiboken6/ folders in site-packages")
    print("     then pip install --no-cache-dir PySide6")
    print("  2. install the VC++ 2015-2022 x64 redistributable (repair, even if present)")
    print("  3. python -m venv .venv  &&  .venv\\Scripts\\activate  &&  pip install PySide6")
    print("  4. pin a known-good pair, e.g. python 3.12 + pip install PySide6==6.8.1")
    print("=" * 72)


if __name__ == "__main__":
    sys.exit(main())
