#!/usr/bin/env python3
"""
Run the test suite without pytest installed.

CI and normal development use pytest -- ``pyproject.toml`` declares it and the
tests are written against it.  This runner exists for environments with no
package index reachable (air-gapped machines, locked-down build agents, the
sandbox this project is partly developed in), where "cannot install pytest"
would otherwise mean "cannot verify anything".

It implements only the pytest surface the suite actually uses:

    @pytest.fixture (function and session scope, incl. tmp_path)
    @pytest.mark.parametrize / @pytest.mark.corpus
    pytest.raises / pytest.approx / pytest.skip

If a test needs something beyond that, add it to pytest's dependency list and
run pytest -- do not grow this file into a second test framework.

    python3 tools/offline_test_runner.py [-k SUBSTRING] [-v]
"""

from __future__ import annotations

import argparse
import contextlib
import inspect
import pathlib
import shutil
import sys
import tempfile
import traceback
import types

ROOT = pathlib.Path(__file__).resolve().parent.parent


class Skipped(Exception):
    pass


class _Approx:
    # ``abs`` is pytest's spelling and the one tests actually write; the
    # trailing-underscore form is kept because this shim used to be the only
    # thing accepting it. A fallback that rejects the real API's keyword is a
    # fallback that fails on working tests.
    def __init__(self, expected, rel=1e-6, abs=1e-12, abs_=None):
        self.expected = expected
        self.rel = rel
        self.abs = abs_ if abs_ is not None else abs

    def __eq__(self, other):
        try:
            return abs(other - self.expected) <= max(
                self.abs, self.rel * abs(self.expected)
            )
        except TypeError:
            return NotImplemented

    def __repr__(self):  # pragma: no cover
        return f"approx({self.expected})"


def _build_pytest_module() -> types.ModuleType:
    mod = types.ModuleType("pytest")

    def fixture(func=None, **kw):
        def wrap(f):
            f._is_fixture = True
            f._scope = kw.get("scope", "function")
            return f

        return wrap(func) if func is not None else wrap

    @contextlib.contextmanager
    def raises(exc, **_kw):
        holder = types.SimpleNamespace(value=None)
        try:
            yield holder
        except exc as e:
            holder.value = e
            return
        raise AssertionError(f"expected {exc.__name__} to be raised")

    def skip(reason=""):
        raise Skipped(reason)

    class _Mark:
        def __getattr__(self, name):
            if name == "parametrize":

                def parametrize(argnames, argvalues, **_kw):
                    names = (
                        [a.strip() for a in argnames.split(",")]
                        if isinstance(argnames, str)
                        else list(argnames)
                    )

                    def deco(f):
                        cases = getattr(f, "_params", [])
                        cases.append((names, list(argvalues)))
                        f._params = cases
                        return f

                    return deco

                return parametrize

            def marker(f=None, **_kw):
                if f is None:
                    return lambda g: g
                return f

            return marker

    def importorskip(name, *_args, **_kw):
        """Skip rather than fail when an optional dependency is absent.

        The packaging tests need PySide6 and PyInstaller, neither of which
        installs in a cloud session. Without this they *fail* here while
        skipping under pytest -- and a fallback runner that disagrees with the
        thing it stands in for is worse than none.
        """
        import importlib

        try:
            return importlib.import_module(name)
        except ImportError as exc:
            raise Skipped(f"could not import {name!r}: {exc}") from None

    mod.fixture = fixture
    mod.raises = raises
    mod.skip = skip
    mod.importorskip = importorskip
    mod.approx = _Approx
    mod.mark = _Mark()
    mod.Skipped = Skipped
    return mod


class _MonkeyPatch:
    """The small slice of pytest's monkeypatch the suite uses.

    Records an undo for every change and the runner applies them after the test,
    so ordering stays independent -- a chdir that leaked would make later tests
    pass or fail depending on which ran first.
    """

    def __init__(self, undos):
        self._undos = undos

    def chdir(self, path):
        import os

        previous = os.getcwd()
        self._undos.append(lambda: os.chdir(previous))
        os.chdir(str(path))

    def setattr(self, target, name, value):
        previous = getattr(target, name)
        self._undos.append(lambda: setattr(target, name, previous))
        setattr(target, name, value)

    def setenv(self, name, value):
        import os

        previous = os.environ.get(name)
        def undo():
            if previous is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = previous
        self._undos.append(undo)
        os.environ[name] = value


def _expand(func):
    """Yield ``(suffix, kwargs)`` for each parametrize combination."""
    params = getattr(func, "_params", None)
    if not params:
        yield "", {}
        return
    combos = [({}, "")]
    for names, values in reversed(params):
        nxt = []
        for base, label in combos:
            for v in values:
                vals = v if isinstance(v, tuple) else (v,)
                kw = dict(base)
                kw.update(dict(zip(names, vals)))
                nxt.append((kw, f"{label}[{'-'.join(map(str, vals))}]"))
        combos = nxt
    for kw, label in combos:
        yield label, kw


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-k", dest="filter", default="")
    ap.add_argument("-v", dest="verbose", action="store_true")
    args = ap.parse_args()

    sys.modules["pytest"] = _build_pytest_module()
    sys.path.insert(0, str(ROOT / "src"))
    sys.path.insert(0, str(ROOT / "tests"))

    import conftest  # noqa: F401  (registers sys.path and fixtures)

    session_cache: dict = {}
    passed = failed = skipped = 0
    failures = []

    for path in sorted((ROOT / "tests").glob("test_*.py")):
        module = types.ModuleType(path.stem)
        module.__file__ = str(path)
        sys.modules[path.stem] = module
        try:
            # Python source is UTF-8 by definition (PEP 3120); read_text()
            # without an encoding uses the *locale* codepage, so on a German
            # Windows a test asserting on "höhere" silently compared against
            # "hÃ¶here" and failed.  The import machinery gets this right; this
            # runner bypasses it and so has to say so explicitly.
            exec(
                compile(path.read_text(encoding="utf-8"), str(path), "exec"),
                module.__dict__,
            )
        except Exception:
            failed += 1
            failures.append((path.name, "<import>", traceback.format_exc()))
            continue

        fixtures = {
            n: f
            for n, f in vars(module).items()
            if callable(f) and getattr(f, "_is_fixture", False)
        }
        for n, f in vars(conftest).items():
            if callable(f) and getattr(f, "_is_fixture", False):
                fixtures.setdefault(n, f)

        for name, func in sorted(vars(module).items()):
            if not name.startswith("test_") or not callable(func):
                continue
            for label, kwargs in _expand(func):
                test_id = f"{path.name}::{name}{label}"
                if args.filter and args.filter not in test_id:
                    continue
                tmpdirs = []
                undos = []
                # One tmp_path per *test*, not per request. A test that takes
                # both a fixture and ``tmp_path`` -- ``world(tmp_path)`` plus
                # ``tmp_path`` -- is looking at the same directory the fixture
                # built, and handing out a second one made four baseline tests
                # fail here while passing under pytest. A fallback runner that
                # disagrees with the thing it stands in for is worse than none.
                per_test = {}

                def resolve(pname, _seen=None):
                    # Fixtures may request other fixtures (``layered(tmp_path)``),
                    # so resolution is recursive with cycle detection.
                    _seen = _seen or set()
                    if pname in _seen:
                        raise RuntimeError(f"fixture cycle at {pname!r}")
                    _seen = _seen | {pname}
                    if pname == "tmp_path":
                        if "tmp_path" not in per_test:
                            d = pathlib.Path(tempfile.mkdtemp())
                            tmpdirs.append(d)
                            per_test["tmp_path"] = d
                        return per_test["tmp_path"]
                    if pname == "monkeypatch":
                        return _MonkeyPatch(undos)
                    if pname not in fixtures:
                        raise RuntimeError(f"no fixture named {pname!r}")
                    fx = fixtures[pname]
                    if fx._scope == "session" and pname in session_cache:
                        return session_cache[pname]
                    if pname in per_test:
                        return per_test[pname]
                    sub = {
                        a: resolve(a, _seen) for a in inspect.signature(fx).parameters
                    }
                    val = fx(**sub)
                    if fx._scope == "session":
                        session_cache[pname] = val
                    else:
                        # Same rule as tmp_path: one instance per test, so two
                        # parameters that reach the same fixture see one object.
                        per_test[pname] = val
                    return val

                try:
                    call = dict(kwargs)
                    for pname in inspect.signature(func).parameters:
                        if pname not in call:
                            call[pname] = resolve(pname)
                    func(**call)
                    passed += 1
                    if args.verbose:
                        print(f"  PASS {test_id}")
                except Skipped as e:
                    skipped += 1
                    if args.verbose:
                        print(f"  SKIP {test_id}: {e}")
                except Exception:
                    failed += 1
                    failures.append((path.name, f"{name}{label}", traceback.format_exc()))
                    print(f"  FAIL {test_id}")
                finally:
                    for undo in reversed(undos):
                        try:
                            undo()
                        except Exception:  # noqa: BLE001
                            pass
                    for d in tmpdirs:
                        shutil.rmtree(d, ignore_errors=True)

    for fname, tname, tb in failures:
        print(f"\n=== {fname}::{tname} ===\n{tb}")
    print(f"\n{passed} passed, {failed} failed, {skipped} skipped")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
