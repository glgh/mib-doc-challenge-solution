#!/usr/bin/env python3
"""Probe: is there recoverable value from combining OCR reads at the sub-page
(per-label-line) level, below the page-level ensemble S2/S4 already run?

Context: `merge_fields` already picks a field's value from ANY parsed read on
a page (packet.variant_docs holds every losing S2 read, not just the winner
`best_read` picks). So whole-document selection already covers "a losing read
had the best copy of this field." Splicing only pays for something NEW if no
single read's label-line tail is vocab/pattern-valid, while two+ reads that
share GEOMETRY (same source + correction chain, differing only in optical
treatment: raw gray vs adapt vs autocon) carry different, non-trivial tails
for that same label. That would mean the value is fragmented across reads the
pipeline currently treats as competing wholes, never reconciled.

This is a probe (house rule: prove before building). It counts the
opportunity on the cached hard-set OCR ensemble and prints examples against
train truth. It changes nothing in mib/.
"""
import csv
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
CH = ROOT.parent / "mib-doc-challenge"

from mib import cache, vocab
from mib.stages.render import _TELL_LABELS, _label_tail, TELL_LABEL_SIM, TELL_TAIL_MIN
from mib.vocab import _weighted_sim

CACHE_PATH = ROOT / "output/cache/hard_set_grid.jsonl"

_LABEL_FIELD = {
    "case id": "case_id",
    "applicant": "applicant_name",
    "registry name": "applicant_name",
    "species code": "species_code",
    "home world": "home_world",
    "visa class": "visa_class",
    "sponsor id": "sponsor_id",
    "arrival date": "arrival_date",
    "purpose": "declared_purpose",
    "declared purpose": "declared_purpose",
    "observed flags": "risk_flags",
}
# vocab.snap's internal field name differs from the schema/truth column name
# only for this one label (risk_flags in the CSV, observed_flags in snap()).
_SNAP_FIELD = dict(_LABEL_FIELD, **{"observed flags": "observed_flags"})

_OPTICAL_SUFFIX_RE = re.compile(r"\+(adapt|autocon)$")
_norm = lambda s: " ".join(s.split()).lower()


def chain_key(variant):
    return _OPTICAL_SUFFIX_RE.sub("", variant)


def label_tails(lines):
    """label -> longest tail seen on this one read."""
    out = {}
    for line in lines:
        if len(line.split()) < 2:
            continue
        head, tail = _label_tail(line)
        if tail is None:
            continue
        for label in _TELL_LABELS:
            if _weighted_sim(head, label) >= TELL_LABEL_SIM:
                if label not in out or len(tail) > len(out[label]):
                    out[label] = tail
                break
    return out


def is_valid(label, tail):
    field = _SNAP_FIELD.get(label)
    if field is None or not tail:
        return False
    return vocab.snap(field, tail) is not None


def load_truth():
    truth = {}
    path = CH / "data/train_labels.csv"
    if path.exists():
        with open(path) as f:
            for row in csv.DictReader(f):
                truth[row["case_id"]] = row
    return truth


def main():
    meta, records = cache.read(CACHE_PATH)
    print(f"cache: {CACHE_PATH.name}  n={meta.get('n_subset')}  "
          f"restore={meta.get('restore')}  git={meta.get('git_rev')}")
    truth = load_truth()

    opportunities = []
    pages_scanned = 0
    groups_with_sibling = 0
    for rec in records:
        stem = rec["stem"]
        pages, reads_by_page = cache.to_case(rec["pages"])
        for pt in pages:
            if not pt.is_scan_only:
                continue
            pages_scanned += 1
            reads = reads_by_page.get(pt.page_no) or []
            groups = defaultdict(list)
            for r in reads:
                if _OPTICAL_SUFFIX_RE.search(r.variant):
                    groups[chain_key(r.variant)].append(r)
            for chain, siblings in groups.items():
                base = next((r for r in reads if r.variant == chain), None)
                group_reads = ([base] if base else []) + siblings
                if len(group_reads) < 2:
                    continue
                groups_with_sibling += 1
                per_read_tails = [(r.variant, label_tails(r.lines)) for r in group_reads]
                labels_seen = set()
                for _v, tails in per_read_tails:
                    labels_seen.update(tails)
                for label in labels_seen:
                    entries = [(v, t[label]) for v, t in per_read_tails if label in t]
                    if len(entries) < 2:
                        continue
                    if len({_norm(t) for _v, t in entries}) < 2:
                        continue  # reads agree -- nothing to splice
                    if any(is_valid(label, t) for _v, t in entries):
                        continue  # some read already validates alone --
                                  # merge_fields already reaches this
                    if all(len(t.replace(" ", "")) <= TELL_TAIL_MIN for _v, t in entries):
                        continue  # every fragment too short to be informative
                    field = _LABEL_FIELD.get(label, "")
                    opportunities.append({
                        "case": stem, "page": pt.page_no, "chain": chain,
                        "label": label, "entries": entries,
                        "truth": truth.get(stem, {}).get(field),
                    })

    print(f"scan-only pages: {pages_scanned}   "
          f"(chain, page) groups with an optical sibling: {groups_with_sibling}")
    print(f"\n{len(opportunities)} (case, page, label) instances where same-geometry "
          f"reads disagree and NO single read validates alone\n")
    for o in opportunities[:30]:
        print(f"{o['case']} p{o['page']} chain={o['chain']!r} label={o['label']!r} "
              f"truth={o['truth']!r}")
        for v, t in o["entries"]:
            print(f"    {v:55s} tail={t!r}")
    if len(opportunities) > 30:
        print(f"... and {len(opportunities) - 30} more")


if __name__ == "__main__":
    main()
