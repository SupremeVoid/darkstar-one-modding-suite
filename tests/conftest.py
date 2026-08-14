"""
Shared fixtures.

Two kinds of test live here.  Unit tests use synthetic or checked-in fixtures
and always run.  Corpus tests need real game data and are *skipped*, not failed,
when it is absent -- so a clean checkout is green, while a developer with the
game gets the exhaustive run that actually proves the format claims.

Point ``DSO_GAME_DATA`` at a folder of extracted ``.cpr`` archives (one
directory per archive) to enable them.
"""

import os
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

#: Extra folders searched for sample assets when DSO_GAME_DATA is unset.
_FALLBACK_SAMPLES = [pathlib.Path("/mnt/user-data/uploads")]


def _looks_like_archive_root(p: pathlib.Path) -> bool:
    """A folder of extracted archives has one ``ds_*`` directory per archive."""
    try:
        return any(c.is_dir() and c.name.lower().startswith("ds_") for c in p.iterdir())
    except OSError:
        return False


def _corpus_root():
    """Locate a folder of extracted archives.

    Accepts the archive root itself or any parent within two levels, because
    people point DSO_GAME_DATA at whatever folder they happen to be looking at
    and a test that skips over a path typo is worse than useless.
    """
    candidates = []
    env = os.environ.get("DSO_GAME_DATA")
    if env:
        candidates.append(pathlib.Path(env))
    candidates.extend(_FALLBACK_SAMPLES)
    for c in candidates:
        if not c.is_dir():
            continue
        if _looks_like_archive_root(c):
            return c
        for depth in (1, 2):
            for sub in c.glob("/".join(["*"] * depth)):
                if sub.is_dir() and _looks_like_archive_root(sub):
                    return sub
    return None


@pytest.fixture(scope="session")
def corpus():
    root = _corpus_root()
    if root is None:
        pytest.skip("no game data; set DSO_GAME_DATA to a folder of extracted archives")
    return root


def collect(root, pattern):
    return sorted(root.rglob(pattern))


#: A partial extraction (a handful of staged samples) makes rate-based tests
#: meaningless -- almost nothing resolves, and the failure says nothing about
#: the code.  Rate tests gate on this instead.
FULL_CORPUS_MIN_MODELS = 500
FULL_CORPUS_MIN_SCENES = 200


def require_full_corpus(root):
    """Skip unless ``root`` holds a complete extraction, not a sample."""
    import pytest

    models = sum(1 for _ in root.rglob("*.3do"))
    scenes = sum(1 for _ in root.rglob("*.xml"))
    if models < FULL_CORPUS_MIN_MODELS or scenes < FULL_CORPUS_MIN_SCENES:
        pytest.skip(
            f"partial corpus ({models} models, {scenes} scenes); "
            f"rate checks need a full extraction"
        )
