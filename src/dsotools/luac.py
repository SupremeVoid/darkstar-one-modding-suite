"""
The game's own Lua compiler, as a library.

``ScriptCompiler.exe`` in the *Darkstar One Modding Tools* **is `luac`** -- it
prints ``usage: luac [options] [filenames]`` and identifies itself as
``Lua 4.1.0 (ASCARON_LUA,modified 4.0.1 source)``.  That matters more than it
sounds: the engine's Lua has no standard library and is a modified 4.0, so
nothing else on a modern machine parses these scripts the way the game does.

Three uses, in order of how often they are wanted:

* ``-p`` **parse only** -- a syntax check by the exact parser the game uses.
  A mod script that fails here will fail in the game, and the message names
  the line.
* ``-o`` **compile** many sources into one ``user_scripts.bin``.  The main
  chunk calls each file's chunk in order, so the command-line order is the
  load order.
* ``-l`` **list** -- disassemble a bundle, including the game's own
  ``missions.bin``.  This is how the stock mission structure was read.

:func:`chunk_names` needs no compiler at all: the source name of every chunk
is embedded in the bytecode, so a bundle can be inspected on any machine.

Nothing here is required for the suite to run.  The compiler ships with the
modding tools, not the game, so every entry point degrades to a clear "not
installed" rather than an exception.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from typing import List, Optional, Sequence, Tuple

from .errors import DsoError

VERSION = "1.0"

#: Lua 4 bytecode, as this engine writes it.
SIGNATURE = b"\x1bLuaA"

#: Where the modding tools install by default.
DEFAULT_ROOTS = (
    r"C:\Program Files\Darkstar One Modding Tools",
    r"C:\Program Files (x86)\Darkstar One Modding Tools",
)

COMPILER = "ScriptCompiler.exe"

#: ``@Game/lua/mission/ALWAYS_000.lua`` -- every chunk records its source.
_CHUNK_NAME = re.compile(rb"@[\w./\\ -]{3,80}\.lua")


def find_compiler(*roots) -> Optional[str]:
    """Locate ``ScriptCompiler.exe``, or ``None`` if the tools are absent."""
    for root in (roots or DEFAULT_ROOTS):
        if not root or not os.path.isdir(root):
            continue
        for dirpath, _dirnames, filenames in os.walk(root):
            for filename in filenames:
                if filename.lower() == COMPILER.lower():
                    return os.path.join(dirpath, filename)
    return None


def available(*roots) -> bool:
    return find_compiler(*roots) is not None


def _run(compiler: str, arguments: Sequence[str], cwd: str) -> Tuple[int, str]:
    try:
        done = subprocess.run(
            [compiler, *arguments], cwd=cwd, capture_output=True, timeout=120)
    except (OSError, subprocess.SubprocessError) as exc:
        raise DsoError(f"could not run {os.path.basename(compiler)}: {exc}",
                       path=compiler) from exc
    output = (done.stdout + done.stderr).decode("cp1252", "replace").strip()
    return done.returncode, output


def check_syntax(text: str, *, name: str = "script.lua",
                 compiler: Optional[str] = None) -> Tuple[bool, str]:
    """Parse ``text`` with the game's parser.  ``(ok, message)``.

    The text is written to a temporary file because ``luac`` reads files; the
    name it is given shows up in any error message, so it is worth passing the
    real one.
    """
    compiler = compiler or find_compiler()
    if not compiler:
        return True, "the modding tools are not installed; syntax not checked"

    folder = tempfile.mkdtemp(prefix="dso_luac_")
    try:
        target = os.path.join(folder, os.path.basename(name) or "script.lua")
        with open(target, "wb") as handle:
            handle.write(text.encode("cp1252", "replace"))
        code, output = _run(compiler, ["-p", os.path.basename(target)], folder)
        if code == 0 and not output:
            return True, "parses"
        return code == 0, output or "parses"
    finally:
        _cleanup(folder)


def compile_bundle(sources: Sequence[str], out: str, *,
                   compiler: Optional[str] = None) -> str:
    """Compile ``sources`` into one bundle at ``out``; return ``out``.

    Order is preserved: the bundle's main chunk calls each file's chunk in the
    order given, which is also how the game's own ``missions.bin`` is built.
    """
    compiler = compiler or find_compiler()
    if not compiler:
        raise DsoError(
            f"{COMPILER} was not found. It ships with the Darkstar One Modding "
            f"Tools, not with the game.")
    if not sources:
        raise DsoError("nothing to compile")
    missing = [s for s in sources if not os.path.isfile(s)]
    if missing:
        raise DsoError(f"no such script: {missing[0]}", path=missing[0])

    out = os.path.abspath(out)
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    # Run in a folder holding copies, so the chunk names recorded in the
    # bundle are bare file names -- which is what the shipped mods look like.
    folder = tempfile.mkdtemp(prefix="dso_luac_")
    try:
        names = []
        for source in sources:
            name = os.path.basename(source)
            with open(source, "rb") as src, open(os.path.join(folder, name), "wb") as dst:
                dst.write(src.read())
            names.append(name)
        code, output = _run(compiler, ["-o", out, *names], folder)
        if code != 0 or not os.path.isfile(out):
            raise DsoError(f"compilation failed: {output or 'no output written'}")
        return out
    finally:
        _cleanup(folder)


def list_bundle(path: str, *, compiler: Optional[str] = None) -> str:
    """Disassemble a compiled bundle -- the ``-l`` listing, as text."""
    compiler = compiler or find_compiler()
    if not compiler:
        raise DsoError(f"{COMPILER} was not found")
    if not os.path.isfile(path):
        raise DsoError(f"no such bundle: {path}", path=path)
    folder = os.path.dirname(os.path.abspath(path)) or "."
    code, output = _run(compiler, ["-l", os.path.abspath(path)], folder)
    if code != 0 and not output:
        raise DsoError(f"could not list {os.path.basename(path)}")
    return output


def chunk_names(data: bytes) -> List[str]:
    """The source file names recorded in a compiled bundle.

    Needs no compiler: Lua 4 stores each chunk's source name in the bytecode.
    ``missions.bin`` names 154 files this way, a mod's ``user_scripts.bin``
    however many it was built from.
    """
    if not data.startswith(SIGNATURE):
        raise DsoError("not Lua 4 bytecode (no \\x1bLuaA signature)")
    seen, out = set(), []
    for match in _CHUNK_NAME.finditer(data):
        name = match.group()[1:].decode("latin-1")
        if name not in seen:
            seen.add(name)
            out.append(name)
    return out


def is_bundle(path: str) -> bool:
    try:
        with open(path, "rb") as handle:
            return handle.read(len(SIGNATURE)) == SIGNATURE
    except OSError:
        return False


def _cleanup(folder: str) -> None:
    import shutil

    shutil.rmtree(folder, ignore_errors=True)


__all__ = [
    "VERSION", "SIGNATURE", "COMPILER", "DEFAULT_ROOTS",
    "find_compiler", "available", "check_syntax", "compile_bundle",
    "list_bundle", "chunk_names", "is_bundle",
]
