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


@pytest.fixture(scope="session")
def actual(cases):
    """Every fixture case run through the pipeline once, for the whole session.

    The per-field characterization test is parametrized so a diff names the field
    that moved; without this it would also re-run the pipeline once per field,
    doing the same work twelve times. The suite is the inner loop of every
    change, so its cost is worth keeping honest.
    """
    return [predict(case) for case in cases]


def rehydrate(page_dicts):
    """Frozen page text -> (pages, reads), ready for runner.predict_from_evidence.

    Shared with the replay path rather than reimplemented here: a test helper
    that rehydrates differently from the tool under test can pass while the real
    thing is broken.
    """
    from mib import cache
    return cache.to_case(page_dicts)


def predict(case, pages=None):
    """Run the real pipeline over a fixture case's frozen page text."""
    from mib import runner
    pages, reads = rehydrate(case["pages"] if pages is None else pages)
    return runner.predict_from_evidence(pages, reads, case["stem"])
