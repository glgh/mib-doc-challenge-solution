#!/usr/bin/env python3
"""Re-run everything downstream of page text against a cached dump, in seconds.

Two jobs, and the second is why this exists:

  1. The fast iteration loop. Parse, merge, signals, policy, confidence and emit
     are pure functions of page text, so a change to any of them can be scored
     without paying for OCR again. `scripts/dump_text.py` pays that cost once.

  2. The refactor gate. Replaying a cache and diffing against predictions from a
     real run tests three claims at once: that `solution.predict_from_pages` is a
     faithful seam (same records as the fused path), that the cache is a faithful
     recording of page text (nothing lost in serialization), and that OCR was
     deterministic when the cache was built. Any pure refactor in the staged
     pipeline must keep this diff empty.

Provenance is checked, not assumed: replaying a cache built at one restoration
level against predictions from another compares two different pipelines and the
diff would be meaningless.

Usage: scripts/replay.py [cache.jsonl] [out_dir] [--reference <predictions.jsonl>]
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from mib import cache, config, emit, runner  # noqa: E402


def replay(cache_path):
    """-> (records, debugs), matching what solution.main would have written."""
    meta, records = cache.read(cache_path)
    out, debugs, failures = [], [], []
    for rec in records:
        if rec.get("error"):
            failures.append(rec["stem"])
            continue
        pages, reads = cache.to_case(rec["pages"])
        record, debug = runner.predict_from_evidence(pages, reads, rec["stem"])
        out.append(record)
        debugs.append(debug)
    return meta, emit.dedupe(out), debugs, failures


def main(cache_path, out_dir=None, reference=None):
    meta, records, debugs, failures = replay(cache_path)
    inputs = [(str(cache_path), meta)]

    if out_dir:
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "predictions.jsonl").write_text(
            "".join(json.dumps(r, sort_keys=True) + "\n" for r in records))
        (out_dir / "debug.jsonl").write_text(
            "".join(json.dumps(d, sort_keys=True) + "\n" for d in debugs))
        (out_dir / "meta.json").write_text(json.dumps(
            config.stamp(artifact="eval", replayed_from=str(cache_path),
                         replayed_from_meta=meta, n_records=len(records)),
            indent=2, sort_keys=True) + "\n")
        print(f"wrote {len(records)} predictions to {out_dir}")

    if not reference:
        return 0

    ref_path = Path(reference)
    ref_meta_path = ref_path.parent / "meta.json"
    inputs.append((str(ref_path), json.loads(ref_meta_path.read_text())
                   if ref_meta_path.exists() else None))
    config.require_agreement(inputs)

    ref = {json.loads(l)["case_id"]: json.loads(l) for l in open(ref_path)}
    got = {r["case_id"]: r for r in records}

    only_ref = sorted(set(ref) - set(got))
    only_got = sorted(set(got) - set(ref))
    diffs = []
    for cid in sorted(set(ref) & set(got)):
        for field in sorted(set(ref[cid]) | set(got[cid])):
            a, b = ref[cid].get(field), got[cid].get(field)
            if a != b:
                diffs.append(f"{cid}.{field}: {a!r} -> {b!r}")

    print(f"== replay vs {ref_path} ==")
    print(f"  {len(ref)} reference records, {len(got)} replayed, "
          f"{len(set(ref) & set(got))} in common")
    if failures:
        print(f"  {len(failures)} cached case(s) carry a read error and were skipped")
    if only_ref:
        print(f"  MISSING from replay ({len(only_ref)}): {only_ref[:5]}")
    if only_got:
        print(f"  EXTRA in replay ({len(only_got)}): {only_got[:5]}")
    if diffs:
        print(f"  {len(diffs)} field difference(s):")
        for d in diffs[:40]:
            print(f"    {d}")
        if len(diffs) > 40:
            print(f"    ... and {len(diffs) - 40} more")
    if not (diffs or only_ref or only_got):
        print("  IDENTICAL — seam is behavior-preserving, cache is faithful, "
              "OCR was deterministic")
        return 0
    return 1


if __name__ == "__main__":
    import argparse

    level = config.DEFAULT_RESTORE
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cache", nargs="?",
                    default=str(ROOT / f"output/cache/train_{level}.jsonl"))
    ap.add_argument("out_dir", nargs="?", default=None)
    ap.add_argument("--reference", help="predictions.jsonl to diff against")
    a = ap.parse_args()
    sys.exit(main(a.cache, a.out_dir, a.reference))
