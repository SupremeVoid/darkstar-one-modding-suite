#!/usr/bin/env python3
"""
Static check for the Qt layer, which cannot be unit-tested without a display.

    python tools/check_app.py [package_dir ...]

WHY
---
The application's logic lives in ``session.py`` and ``diagnostics.py``, which
import no Qt and are covered by the test suite.  The widget code is not, and
cannot be in CI -- so it needs a different kind of verification.

This exists because of a real failure: an edit spliced ``NewModDialog`` into the
middle of ``ProjectTab``, which silently moved two of ``ProjectTab``'s methods
into the dialog.  The file still compiled, every import succeeded, and the app
died on launch with ``'ProjectTab' object has no attribute '_apply_filter'``.
Nothing short of running Qt would have caught it -- except this, which catches
it in a tenth of a second.

WHAT IT CHECKS
--------------
* ``self.name`` is read somewhere in a class but never defined in it (no method
  of that name, no ``self.name = ...`` anywhere in the class, not inherited from
  a base defined in the same file).  This is the splice bug exactly.
* A ``.connect()`` target that resolves to nothing.
* Duplicate method names in one class -- a later ``def`` silently replacing an
  earlier one is another way a bad merge hides.

It is deliberately conservative: attributes set on ``self`` anywhere in the
class count as defined, and unknown base classes suppress the check for that
class.  A static checker that cries wolf gets switched off, and then the real
findings go with it.
"""

from __future__ import annotations

import ast
import os
import sys
from typing import Dict, List, Set, Tuple

#: Attributes Qt gives every widget.  Not exhaustive; extend when a false
#: positive appears rather than loosening the rule.
INHERITED = {
    "setLayout", "layout", "setWindowTitle", "resize", "show", "close", "parent",
    "setEnabled", "setVisible", "width", "height", "update", "sender", "window",
    "setMinimumWidth", "setFixedHeight", "setStyleSheet", "accept", "reject",
    "exec", "setWindowFlags", "setToolTip", "menuBar", "setCentralWidget",
    "setStatusBar", "statusBar", "addWidget", "setSizePolicy", "font", "setFont",
}


class ClassInfo:
    def __init__(self, node: ast.ClassDef) -> None:
        self.node = node
        self.name = node.name
        # A dotted base (``logging.Handler``) is as unknown as an unresolvable
        # one -- both may supply attributes this file cannot see.
        self.bases = [b.id for b in node.bases if isinstance(b, ast.Name)]
        self.opaque_bases = any(not isinstance(b, ast.Name) for b in node.bases)
        self.methods: List[str] = []
        self.assigned: Set[str] = set()
        self.used: List[Tuple[str, int]] = []
        self.duplicates: List[str] = []
        self._collect()

    def _collect(self) -> None:
        seen: Set[str] = set()
        for item in self.node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if item.name in seen:
                    self.duplicates.append(item.name)
                seen.add(item.name)
                self.methods.append(item.name)
            elif isinstance(item, ast.Assign):
                for t in item.targets:
                    if isinstance(t, ast.Name):
                        self.assigned.add(t.id)
            elif isinstance(item, ast.AnnAssign) and isinstance(item.target, ast.Name):
                self.assigned.add(item.target.id)

        for node in ast.walk(self.node):
            # self.x = ... anywhere in the class defines x
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if _is_self_attr(t):
                        self.assigned.add(t.attr)
            elif isinstance(node, ast.AnnAssign) and _is_self_attr(node.target):
                self.assigned.add(node.target.attr)
            elif isinstance(node, ast.AugAssign) and _is_self_attr(node.target):
                self.assigned.add(node.target.attr)
            elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
                if _is_self_attr(node):
                    self.used.append((node.attr, node.lineno))

    def defines(self, name: str) -> bool:
        return name in self.methods or name in self.assigned


def _is_self_attr(node) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
    )


def check_file(path: str) -> List[str]:
    try:
        tree = ast.parse(open(path, encoding="utf-8").read(), path)
    except SyntaxError as exc:
        return [f"{path}:{exc.lineno}: syntax error: {exc.msg}"]

    classes: Dict[str, ClassInfo] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            classes[node.name] = ClassInfo(node)

    problems: List[str] = []
    for info in classes.values():
        for dup in info.duplicates:
            problems.append(
                f"{path}:{info.node.lineno}: {info.name}.{dup} is defined more than "
                f"once; the later one silently wins"
            )

        # A base we cannot see may define anything -- stay quiet for that class.
        unknown_base = info.opaque_bases or any(b not in classes for b in info.bases)

        for attr, line in sorted(set(info.used)):
            if attr in INHERITED or info.defines(attr):
                continue
            if any(classes[b].defines(attr) for b in info.bases if b in classes):
                continue
            if unknown_base:
                # Only flag names that look like our own private members; Qt
                # base classes supply plenty of public ones.
                if not attr.startswith("_"):
                    continue
            problems.append(
                f"{path}:{line}: {info.name} uses self.{attr} but nothing in the "
                f"class defines it"
            )
    return problems


def iter_python(paths: List[str]):
    for root in paths:
        if os.path.isfile(root):
            yield root
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]
            for fn in sorted(filenames):
                if fn.endswith(".py"):
                    yield os.path.join(dirpath, fn)


def main(argv=None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if not args:
        here = os.path.dirname(os.path.abspath(__file__))
        args = [os.path.join(os.path.dirname(here), "app")]

    problems: List[str] = []
    files = 0
    for path in iter_python(args):
        files += 1
        problems.extend(check_file(path))

    for p in problems:
        print(p)
    print(f"\n{files} file(s) checked, {len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
