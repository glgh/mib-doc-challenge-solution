"""The refactor safety net.

Runs the real downstream pipeline (`solution.predict_from_pages`) against frozen
page text and asserts the emitted record is unchanged, field by field. Any
behavior change in parse / merge / signals / policy / confidence / emit shows up
here in milliseconds instead of in a 15-minute eval.

When a change is *intended*, regenerate with scripts/make_fixture.py and review
the diff — the fixture is the record of what the pipeline currently does.
"""
import pytest

from conftest import rehydrate

import solution


def _actual(case):
    record, debug = solution.predict_from_pages(rehydrate(case["pages"]), case["stem"])
    return record, debug


def test_fixture_covers_the_policy_surface(characterization, cases):
    """The fixture must exercise what the rest of the suite claims to check.

    Coverage is asserted here rather than defended inside each test because the
    failure mode is silent: a fixture with no inferred risk flag does not fail
    test_every_emitted_risk_flag_has_an_evidence_source, it makes that test pass
    vacuously — and flips its strict-xfail marker to XPASS, which reads like a
    defect was fixed when nothing changed but the sample.
    """
    from mib.textmatch import trusted_text, unsourced_flags

    branches = {c["expected_branch"] for c in cases}
    assert len(cases) >= 40, "fixture too small to characterize the pipeline"
    assert len(branches) >= 12, f"only {len(branches)} branches represented: {branches}"
    assert any(p["is_scan_only"] for c in cases for p in c["pages"]), \
        "fixture must include OCR-sourced cases"
    assert any(p["hidden_lines"] for c in cases for p in c["pages"]), \
        "fixture must include cases carrying injected hidden text"
    assert any(unsourced_flags(c["expected_record"]["risk_flags"],
                               trusted_text(c["pages"])) for c in cases), \
        ("fixture contains no policy-inferred risk flag, so the evidence-source "
         "invariant cannot fail — rebuild with scripts/make_fixture.py")


@pytest.mark.parametrize("field", [
    "case_id", "applicant_name", "species_code", "home_world", "visa_class",
    "sponsor_id", "arrival_date", "declared_purpose", "risk_flags",
    "fee_status", "adjudication", "confidence",
])
def test_records_unchanged(cases, field):
    """Per-field so a diff names the field that moved, not just 'a record changed'."""
    diffs = []
    for case in cases:
        record, _ = _actual(case)
        expected = case["expected_record"][field]
        if record[field] != expected:
            diffs.append(f"{case['stem']}: {expected!r} -> {record[field]!r}")
    assert not diffs, f"{field} changed in {len(diffs)} case(s):\n  " + "\n  ".join(diffs)


def test_branches_unchanged(cases):
    diffs = []
    for case in cases:
        _, debug = _actual(case)
        if debug["branch"] != case["expected_branch"]:
            diffs.append(f"{case['stem']}: {case['expected_branch']} -> {debug['branch']}")
    assert not diffs, "policy branch changed:\n  " + "\n  ".join(diffs)


def test_deterministic(cases):
    """Same input twice, same output — nothing in the downstream stages may vary."""
    for case in cases[:15]:
        assert _actual(case)[0] == _actual(case)[0], f"{case['stem']} is nondeterministic"
