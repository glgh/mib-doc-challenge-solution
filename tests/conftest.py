import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FIXTURE = ROOT / "tests/fixtures/characterization.json"


@pytest.fixture(scope="session")
def characterization():
    if not FIXTURE.exists():
        pytest.skip("run scripts/make_fixture.py first")
    return json.loads(FIXTURE.read_text())


@pytest.fixture(scope="session")
def cases(characterization):
    return characterization["cases"]


def rehydrate(page_dicts):
    """Frozen page text -> PageText objects.

    Shared with the replay path rather than reimplemented here: a test helper
    that rehydrates differently from the tool under test can pass while the real
    thing is broken.
    """
    from mib import cache
    return cache.to_pages(page_dicts)
