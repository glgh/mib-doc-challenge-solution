"""Corpus-level recurring-sponsor detection (mib/corpus.py).

The detector's value is entirely counterfactual — on the shipped config it finds
only ids that are already in REVOKED_SPONSORS and revises nothing, so a test that
just runs it on train proves nothing. What these pin instead is the behaviour
that has to hold when it *does* fire on an unseen corpus: that it abstains rather
than guesses, that it reproduces policy's precedence instead of overriding it,
and that it can only ever tighten a decision.
"""
import json
import subprocess
import sys
from pathlib import Path

from mib import corpus, vocab

ROOT = Path(__file__).resolve().parent.parent


def _corpus(unique=200, recurring=(20, 20, 20)):
    """A bimodal corpus: `unique` one-off ids plus a few heavily recurring ones."""
    ids = [f"SPN-{i:04d}" for i in range(unique)]
    for n, count in enumerate(recurring):
        ids += [f"SPN-9{n:03d}"] * count
    return ids


def test_detects_the_recurring_minority():
    found = corpus.recurring_sponsors(_corpus())
    assert found == {"SPN-9000", "SPN-9001", "SPN-9002"}


def test_abstains_when_the_corpus_is_too_small():
    """A two-PDF run would otherwise 'detect' anything appearing twice."""
    assert corpus.recurring_sponsors(["SPN-0001", "SPN-0001", "SPN-0002"]) == frozenset()


def test_abstains_when_every_id_is_unique():
    assert corpus.recurring_sponsors([f"SPN-{i:04d}" for i in range(300)]) == frozenset()


def test_abstains_on_a_smooth_spectrum():
    """No bimodal structure means no basis for calling anything a policy entity —
    the detector reports nothing rather than splitting an arbitrary threshold."""
    smooth = [f"SPN-{i:04d}" for i in range(120) for _ in range(1 + i % 8)]
    assert corpus.recurring_sponsors(smooth) == frozenset()


def test_abstains_when_the_flagged_share_is_implausible():
    """Half the corpus recurring is not revocation; it is a different generator."""
    ids = [f"SPN-{i:04d}" for i in range(60)] + \
          [f"SPN-1{i:03d}" for i in range(60) for _ in range(9)]
    assert corpus.recurring_sponsors(ids) == frozenset()


def test_sentinel_is_not_a_sponsor():
    ids = [corpus.SENTINEL] * 80 + [f"SPN-{i:04d}" for i in range(200)]
    assert corpus.SENTINEL not in corpus.recurring_sponsors(ids)


def _case(cid, spn, visa="XW-1", adj="APPROVED", branch="clean_approve"):
    return ({"case_id": cid, "sponsor_id": spn, "visa_class": visa,
             "adjudication": adj, "confidence": 0.5},
            {"case_id": cid, "branch": branch})


def _revise(cases):
    records = [r for r, _ in cases]
    debugs = [d for _, d in cases]
    # Pad with unique ids so the spectrum is bimodal and the detector engages.
    pad = [_case(f"MIB-{i:06d}", f"SPN-{i:04d}") for i in range(200)]
    records += [r for r, _ in pad]
    debugs += [d for _, d in pad]
    return corpus.revise(records, debugs), records


def test_revise_denies_a_recurring_sponsor_on_a_non_dip_case():
    cases = [_case(f"MIB-10{i:04d}", "SPN-7777") for i in range(20)]
    (new_ids, n), records = _revise(cases)
    assert new_ids == {"SPN-7777"} and n == 20
    assert all(r["adjudication"] == "DENIED" for r in records[:20])


def test_revise_leaves_dip_1_alone():
    """DIP-1 is exempt from the sponsor requirement, so the rule must not fire —
    the same `known_non_dip` guard policy.adjudicate applies."""
    cases = [_case(f"MIB-10{i:04d}", "SPN-7777", visa="DIP-1") for i in range(20)]
    (new_ids, n), records = _revise(cases)
    assert new_ids == {"SPN-7777"} and n == 0
    assert all(r["adjudication"] == "APPROVED" for r in records[:20])


def test_revise_leaves_an_unknown_visa_alone():
    """An unknown visa is not positive evidence of a non-DIP case, and denials
    require positive evidence of their preconditions (policy.adjudicate)."""
    cases = [_case(f"MIB-10{i:04d}", "SPN-7777", visa="unknown") for i in range(20)]
    (_new, n), records = _revise(cases)
    assert n == 0 and all(r["adjudication"] == "APPROVED" for r in records[:20])


def test_revise_respects_higher_precedence_branches():
    """A signed adjudicator note outranks a revoked sponsor; the revision has to
    reproduce the cascade's order, not bulldoze it."""
    cases = [_case(f"MIB-10{i:04d}", "SPN-7777", adj="APPROVED",
                   branch="adjudicator_finding") for i in range(20)]
    (_new, n), records = _revise(cases)
    assert n == 0 and all(r["adjudication"] == "APPROVED" for r in records[:20])


def test_revise_is_a_noop_for_already_known_sponsors():
    """The shipped list already covers these, so there is nothing to revise and
    the pass must not double-count them as a discovery."""
    known = next(iter(vocab.REVOKED_SPONSORS))
    cases = [_case(f"MIB-10{i:04d}", known) for i in range(20)]
    (new_ids, n), _records = _revise(cases)
    assert new_ids == frozenset() and n == 0


def test_revision_only_tightens():
    """Whatever else changes, no case may move toward APPROVED."""
    rank = {"APPROVED": 0, "NEEDS_REVIEW": 1, "DENIED": 2}
    cases = [_case(f"MIB-10{i:04d}", "SPN-7777",
                   adj=["APPROVED", "NEEDS_REVIEW", "DENIED"][i % 3]) for i in range(21)]
    before = [r["adjudication"] for r, _ in cases]
    (_new, _n), records = _revise(cases)
    assert all(rank[a["adjudication"]] >= rank[b]
               for a, b in zip(records[:21], before))


def test_killed_run_leaves_valid_provisional_output(tmp_path):
    """The streamed rows must stand on their own. The contract stops containers at
    a fixed wall time and scores whatever is on disk, so the revision is a rewrite
    of already-written output — never a barrier before the first write."""
    src = ROOT.parent / "mib-doc-challenge/data/train"
    pdfs = sorted(src.glob("*.pdf"))[:3]
    if not pdfs:
        import pytest
        pytest.skip("train PDFs not available")
    stage = tmp_path / "in"
    stage.mkdir()
    for p in pdfs:
        (stage / p.name).write_bytes(p.read_bytes())
    out = tmp_path / "predictions.jsonl"
    subprocess.run([sys.executable, str(ROOT / "solution.py"), str(stage), str(out)],
                   check=True, cwd=ROOT)
    rows = [json.loads(l) for l in out.read_text().splitlines()]
    assert len(rows) == len(pdfs)
    # 3 cases is far below MIN_CASES, so the detector abstained and every row is
    # exactly what the per-case pipeline produced.
    assert not list(tmp_path.glob("*.tmp"))


def _filled(cid, spn, fills, visa="XW-1"):
    """A case whose sponsor/visa came from a post-adjudication fallback fill."""
    record, debug = _case(cid, spn, visa=visa, adj="NEEDS_REVIEW",
                          branch="missing_sponsor")
    debug["vocab_fills"] = sorted(fills)
    return record, debug


def test_revise_ignores_fallback_filled_sponsor_and_visa():
    """A denial may never be armed by a value `fallbacks` invented after
    `policy.adjudicate` had already declined to act on it.

    The records reaching `revise` are post-fallback, so `sponsor_id` and
    `visa_class` can hold values that were empty when policy ran — and
    `fallbacks` states outright that a recovered revoked id "can never arm a
    denial". A filled visa additionally impersonates the positively-read visa
    that `policy.known_non_dip` requires, reopening the "an unknown visa must
    not arm a denial" hazard. Both must be skipped, per field.
    """
    real = [_case(f"MIB-10{i:04d}", "SPN-7777") for i in range(20)]
    sponsor_filled = [_filled(f"MIB-20{i:04d}", "SPN-7777", {"sponsor_id"})
                      for i in range(5)]
    visa_filled = [_filled(f"MIB-30{i:04d}", "SPN-7777", {"visa_class"})
                   for i in range(5)]
    # An unrelated fill (species) must NOT exempt the case — the guard is
    # per-field, not "this record touched a fallback at all".
    other_filled = [_filled(f"MIB-40{i:04d}", "SPN-7777", {"species_code"})
                    for i in range(3)]

    (new_ids, n), records = _revise(real + sponsor_filled + visa_filled
                                    + other_filled)
    assert new_ids == {"SPN-7777"}
    assert n == 23, "only the sponsor/visa-filled cases are exempt"

    by_id = {r["case_id"]: r for r in records}
    assert all(by_id[f"MIB-20{i:04d}"]["adjudication"] == "NEEDS_REVIEW"
               for i in range(5)), "a filled sponsor armed a denial"
    assert all(by_id[f"MIB-30{i:04d}"]["adjudication"] == "NEEDS_REVIEW"
               for i in range(5)), "a filled visa armed a denial"
    assert all(by_id[f"MIB-40{i:04d}"]["adjudication"] == "DENIED"
               for i in range(3)), "an unrelated fill wrongly exempted a case"


def test_filled_sponsors_do_not_feed_the_occurrence_spectrum():
    """An inferred id is not an observation. If fallback fills could vote in the
    recurrence count, the detector could manufacture the very recurrence it is
    supposed to discover — here 20 filled cases would otherwise be enough to
    make SPN-8888 look like a policy entity."""
    filled = [_filled(f"MIB-50{i:04d}", "SPN-8888", {"sponsor_id"})
              for i in range(20)]
    (new_ids, n), _records = _revise(filled)
    assert new_ids == frozenset() and n == 0
