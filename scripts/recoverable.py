#!/usr/bin/env python3
"""Per-stage extraction ceiling: is the truth value even *present* in the text?

There is no per-page ground truth in this corpus, but the truth field values
are strings, so "does this case's truth value appear in the text this stage
produced?" is a label-derived, stage-local metric. It gives:

  * a scoreboard for OCR/repair that does not run the rule cascade, so an S1/S2
    change is judged on its own terms in seconds;
  * an upper bound on extraction — a value absent from every rendering cannot
    be parsed out, so the miss belongs to S2, not to the parser;
  * a local proxy for the private scorer's `unrecoverable_fields` (public
    train_labels.csv has no such column, so our local extraction score is
    systematically pessimistic vs. the leaderboard);
  * the honest ceiling under the no-hidden-text rule: the `hidden-only` tier is
    exactly the injection trap, and those values must stay unused.

The absent/parsefail split joins page text against per-case scores, so the two
inputs must come from the same pipeline; mismatched configs are refused rather
than reported (mib/config.py). Measuring "the value is in the text but we did
not extract it" against text the predicting run never saw is how this metric
lies most convincingly.

Restricting to one policy branch answers the question the roadmap keeps asking
about a branch that loses classification points: is the evidence there at all?
A branch whose truth values are simply absent from the document cannot be split
finer no matter how good the parser gets.

Usage: scripts/recoverable.py [text_dump.jsonl] [eval_dir] [dev|holdout|all] [branch]
"""
import csv
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
CH = ROOT.parent / "mib-doc-challenge"

from mib import cache, config  # noqa: E402
from mib.textmatch import normalize as norm, present  # noqa: E402

FIELDS = ["applicant_name", "species_code", "home_world", "visa_class",
          "sponsor_id", "arrival_date", "declared_purpose", "fee_status"]
WEIGHTS = {"risk_flags": 8, "species_code": 6, "applicant_name": 5,
           "home_world": 5, "visa_class": 5, "sponsor_id": 5,
           "arrival_date": 4, "fee_status": 4, "declared_purpose": 3}
EXTRACTION_POINTS = 50.0


def _extraction_max_raw(eval_dir, split):
    """The scorer's own extraction denominator, so points here are its points.

    Worth taking from the evaluation rather than recomputing: on the private set
    `unrecoverable_fields` removes some fields from a case's maximum, and that
    reduction lands here rather than in a local reimplementation of the rule.
    """
    if not eval_dir:
        return None
    path = Path(eval_dir) / f"evaluation_{split}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())["raw"]["extraction_max_raw"]


def tiers(pages):
    """Text available at each trust tier, cumulatively where it makes sense."""
    vis = norm(" ".join(l for p in pages for l in p["visible_lines"]))
    ocr = norm(" ".join(l for p in pages for l in p["ocr_lines"]))
    hid = norm(" ".join(l for p in pages for l in p["hidden_lines"]))
    return {"visible": vis, "visible+ocr": vis + " " + ocr,
            "hidden": hid, "any": vis + " " + ocr + " " + hid}


def report(dump_path, eval_dir=None, split="dev", branch=None):
    truth = {r["case_id"]: r for r in csv.DictReader(open(CH / "data/train_labels.csv"))}
    ids = None if split == "all" else set(
        json.loads((ROOT / "data_splits.json").read_text())[split])

    if branch:
        debug = (json.loads(l) for l in open(Path(eval_dir) / "debug.jsonl"))
        in_branch = {r["case_id"] for r in debug if r["branch"] == branch}
        ids = in_branch if ids is None else (ids & in_branch)

    dump_meta, dump_records = cache.read(dump_path)

    matched = {}
    inputs = [(str(dump_path), dump_meta)]
    if eval_dir:
        cs = Path(eval_dir) / f"case_scores_{split}.jsonl"
        if cs.exists():
            inputs.append((str(cs), json.loads((Path(eval_dir) / "meta.json").read_text())
                           if (Path(eval_dir) / "meta.json").exists() else None))
            for line in open(cs):
                r = json.loads(line)
                matched[r["case_id"]] = {
                    f: v["status"] == "matched" for f, v in r["field_results"].items()}
    config.require_agreement(inputs)

    counts = {t: Counter() for t in ("visible", "visible+ocr", "hidden", "any")}
    total = Counter()
    hidden_only = Counter()
    lost = Counter()          # in text but not extracted -> parse/merge problem
    absent = Counter()        # not in any visible text   -> evidence problem
    cases = 0

    for rec in dump_records:
        cid = rec["stem"]
        if cid not in truth or (ids is not None and cid not in ids):
            continue
        cases += 1
        text = tiers(rec["pages"])
        for f in FIELDS:
            p = {t: present(truth[cid][f], text[t]) for t in text}
            if p["visible+ocr"] is None:
                continue                                # empty/none truth value
            total[f] += 1
            for t in counts:
                counts[t][f] += bool(p[t])
            if p["hidden"] and not p["visible+ocr"]:
                hidden_only[f] += 1
            if cid in matched and f in matched[cid]:
                if p["visible+ocr"] and not matched[cid][f]:
                    lost[f] += 1
                elif not p["visible+ocr"] and not matched[cid][f]:
                    absent[f] += 1

    # Counts rank fields by how often we miss; points rank them by what a miss
    # costs, which is the ordering the roadmap actually needs. The denominator
    # is the scorer's own, so a "points" column here is comparable with the
    # extraction number in docs/experiments.md rather than merely proportional.
    max_raw = _extraction_max_raw(eval_dir, split) or (cases * sum(WEIGHTS.values()))
    to_points = EXTRACTION_POINTS / max_raw if max_raw else 0.0

    scope = f"{split}" + (f" / branch={branch}" if branch else "")
    print(f"== extraction ceiling: {dump_path} / {scope} ({cases} cases) ==\n")
    print(f"{'field':18s}{'n':>5}{'visible':>9}{'+ocr':>8}{'hid-only':>10}"
          f"{'  |':>3}{'absent':>8}{'pts':>6}{'parsefail':>11}{'pts':>6}")
    for f in sorted(FIELDS, key=lambda f: -(absent[f] + lost[f]) * WEIGHTS[f]):
        n = total[f] or 1
        print(f"{f:18s}{total[f]:5d}{counts['visible'][f] / n:9.1%}"
              f"{counts['visible+ocr'][f] / n:8.1%}{hidden_only[f]:10d}"
              f"{'  |':>3}{absent[f]:8d}{absent[f] * WEIGHTS[f] * to_points:6.2f}"
              f"{lost[f]:11d}{lost[f] * WEIGHTS[f] * to_points:6.2f}")

    tn = sum(total.values()) or 1
    absent_pts = sum(absent[f] * WEIGHTS[f] for f in FIELDS) * to_points
    lost_pts = sum(lost[f] * WEIGHTS[f] for f in FIELDS) * to_points
    print(f"\n{'TOTAL':18s}{sum(total.values()):5d}"
          f"{sum(counts['visible'].values()) / tn:9.1%}"
          f"{sum(counts['visible+ocr'].values()) / tn:8.1%}"
          f"{sum(hidden_only.values()):10d}{'  |':>3}"
          f"{sum(absent.values()):8d}{absent_pts:6.2f}"
          f"{sum(lost.values()):11d}{lost_pts:6.2f}")
    if matched:
        print("\n  absent    = truth value in no visible text -> S2 (render/OCR) problem")
        print("  parsefail = value IS in the text but we did not extract it -> S3/S4")
        print(f"\n  the two levers, in points of the {EXTRACTION_POINTS:.0f}-point "
              f"extraction section:")
        print(f"    better rendering  (close every `absent`)     {absent_pts:6.2f}"
              f"   <- expensive; upper bound, some are truly unrecoverable")
        print(f"    better parsing    (close every `parsefail`)  {lost_pts:6.2f}"
              f"   <- cheap; no OCR cost, pure S3/S4")
        print("  Both are ceilings, not forecasts, and neither counts the "
              "classification points\n  that better evidence would unlock "
              "downstream (see scripts/attribute.py).")
    print(f"\n  local `unrecoverable_fields` proxy: {tn - sum(counts['visible+ocr'].values())}"
          f" of {tn} field-instances are in no visible rendering")
    return {"total": dict(total), "visible+ocr": dict(counts["visible+ocr"]),
            "absent_points": absent_pts, "parsefail_points": lost_pts}


if __name__ == "__main__":
    level = config.DEFAULT_RESTORE
    report(sys.argv[1] if len(sys.argv) > 1 else ROOT / f"output/cache/train_{level}.jsonl",
           sys.argv[2] if len(sys.argv) > 2 else ROOT / f"output/ab_{level}",
           sys.argv[3] if len(sys.argv) > 3 else "dev",
           sys.argv[4] if len(sys.argv) > 4 else None)
