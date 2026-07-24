#!/usr/bin/env python3
"""Freeze a characterization fixture: page text in, emitted record out.

Reads page text from a materialized dump (scripts/dump_text.py) rather than
re-reading PDFs, so the expensive stage is paid for once and every derived
artifact is consistent with it. Both halves of each fixture case come from the
same page text, so the fixture also pins determinism: re-running the downstream
stages against the stored text must reproduce the stored record exactly.

Sampling is stratified by policy branch and drawn from the dev split only. The
fixture compares our output against our own output, never against labels, so it
carries no scoring information and no holdout exposure.

The sample is then topped up until it demonstrably exercises what the test suite
claims to check. A fixture that happens to contain no scanned page, or no
inferred risk flag, does not fail — it silently stops testing those things, and
a strict-xfail marker on the corresponding invariant flips to XPASS for reasons
that have nothing to do with the code. Coverage is a property of the fixture, so
it is guaranteed here and asserted in test_fixture_covers_the_policy_surface.

Usage: scripts/make_fixture.py [dump.jsonl] [n_per_branch] [eval_dir]
"""
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
SEED = 8090

from mib import cache, config  # noqa: E402
from mib.textmatch import trusted_text, unsourced_flags  # noqa: E402


def build_case(rec):
    """One fixture case: frozen page text plus the record it currently produces."""
    from mib import runner

    pages, ocr_lines = cache.to_case(rec["pages"])
    record, debug = runner.predict_from_evidence(pages, ocr_lines, rec["stem"])
    return {
        "stem": rec["stem"],
        "pages": cache.from_case(pages, ocr_lines),
        "expected_record": record,
        "expected_branch": debug["branch"],
    }


def coverage(case):
    """Which suite-relevant properties this case exercises."""
    return {
        "scan": any(p["is_scan_only"] for p in case["pages"]),
        "inferred_flag": bool(unsourced_flags(
            case["expected_record"]["risk_flags"], trusted_text(case["pages"]))),
        "hidden_text": any(p["hidden_lines"] for p in case["pages"]),
    }


def main(dump_path, per_branch=5, eval_dir=None):
    eval_dir = Path(eval_dir or ROOT / f"output/ab_{config.DEFAULT_RESTORE}")
    dump_meta, records = cache.read(dump_path)
    debug_path = eval_dir / "debug.jsonl"
    eval_meta_path = eval_dir / "meta.json"
    # The branch labels only steer sampling spread — expected_branch is recorded
    # from the actual run below — but a spread computed from a different config
    # stratifies over branches this dump does not produce, so it is still checked.
    config.require_agreement([
        (str(dump_path), dump_meta),
        (str(debug_path), json.loads(eval_meta_path.read_text())
         if eval_meta_path.exists() else None),
    ])

    dev = set(json.loads((ROOT / "data_splits.json").read_text())["dev"])
    by_branch = defaultdict(list)
    for line in open(debug_path):
        r = json.loads(line)
        if r["case_id"] in dev:
            by_branch[r["branch"]].append(r["case_id"])

    rng = random.Random(SEED)
    picked, rest = set(), []
    for branch in sorted(by_branch):
        ids = sorted(by_branch[branch])
        rng.shuffle(ids)
        picked.update(ids[:per_branch])
        rest.extend(ids[per_branch:])

    usable = {r["stem"]: r for r in records
              if r["stem"] in dev and not r.get("error")}
    cases = [build_case(usable[s]) for s in sorted(picked) if s in usable]

    # Top up any property the sample failed to exercise, in the same seeded order
    # so the fixture stays reproducible.
    rng.shuffle(rest)
    for prop in ("scan", "inferred_flag", "hidden_text"):
        if any(coverage(c)[prop] for c in cases):
            continue
        for stem in rest:
            if stem in picked or stem not in usable:
                continue
            candidate = build_case(usable[stem])
            if coverage(candidate)[prop]:
                cases.append(candidate)
                picked.add(stem)
                print(f"  topped up for {prop!r}: added {stem}")
                break
        else:
            print(f"  WARNING: no dev case in this dump exercises {prop!r}")

    cases.sort(key=lambda c: c["stem"])
    out = ROOT / "tests/fixtures/characterization.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({
        "source_dump": str(Path(dump_path).name),
        "source_dump_meta": dump_meta,
        "sampled_from": str(debug_path),
        "seed": SEED,
        "per_branch": per_branch,
        "cases": cases,
    }, indent=1, sort_keys=True) + "\n")

    got = {p: sum(1 for c in cases if coverage(c)[p])
           for p in ("scan", "inferred_flag", "hidden_text")}
    branches = {c["expected_branch"] for c in cases}
    print(f"wrote {out}")
    print(f"  {len(cases)} cases, {len(branches)} branches")
    print(f"  coverage: " + ", ".join(f"{k}={v}" for k, v in got.items()))


if __name__ == "__main__":
    level = config.DEFAULT_RESTORE
    main(sys.argv[1] if len(sys.argv) > 1 else ROOT / f"output/cache/train_{level}.jsonl",
         int(sys.argv[2]) if len(sys.argv) > 2 else 5,
         sys.argv[3] if len(sys.argv) > 3 else None)
