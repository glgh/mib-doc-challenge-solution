"""Fixture-hosted checks that survive behaviour change.

The per-field value snapshot that used to live here was retired: the pipeline's
extracted *values* are meant to improve, so byte-freezing them turned the file red
on every intended gain and demanded a regen — a treadmill, not a safety net, and one
that only restated what stronger gates already prove. The division of labour now:

  * value-level drift during a *pure refactor* -> scripts/replay.py (all 1,000 cases
    against a reference run, stronger than a 71-case snapshot);
  * properties that must hold whatever the pipeline does -> test_invariants.py;
  * specific past bugs -> test_regression.py.

What stays here is what a fixture uniquely adds without churning: the coverage
guarantee that lets those invariants actually fail, and the determinism the whole
cache/replay architecture rests on.
"""
from conftest import predict


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


def test_deterministic(cases):
    """Same input twice, same output — nothing in the downstream stages may vary.

    Deliberately does not reuse the session-cached `actual`: this is the one test
    that must pay for a second real run.
    """
    for case in cases[:15]:
        assert predict(case)[0] == predict(case)[0], f"{case['stem']} is nondeterministic"
