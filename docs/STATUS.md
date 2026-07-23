# Status: what we tried, where things are

_Last updated: 2026-07-22 (late). Rewrite this file in place; do not append._

The front page. [experiments.md](experiments.md) has one scored row per change,
[JOURNAL.md](JOURNAL.md) has the chronological log, [PLAN.md](PLAN.md) has the roadmap, and
[CLAUDE.md](../CLAUDE.md) briefs a newcomer on the problem. This file answers the two questions
those don't: **what has been tried**, and **where are we right now**.

---

## Where things are

**Committed: dev 115.43, CFA 0, 0 missing rows.** Shipped config is `MIB_RESTORE=skew` with the
rules decider. **In flight (uncommitted): ~117.98**, which would be the best result yet — the
previous best-ever, 116.88, required `MIB_RESTORE=bands`, whose runtime does not fit the budget.

| step | commit | dev | note |
| --- | --- | ---: | --- |
| pre-P0.5 shipped default | `d6427f8` | 114.50 | container shipped `off` while the log's headline read 116.88 |
| P0.5 instruments + ship `skew` | `ff497f6` | **115.20** | +0.70 for one line; banks a result rows 11–14 had already measured |
| P1a staged seams + records | `b5542cb` | 115.20 | pure refactor, byte-identical |
| P1b per-field candidate preference | `0285f25` | **115.43** | extraction 38.76 → 39.00 |
| P2 runner robustness | `0aa7031` | 115.43 | score-neutral by design |
| P3 parse fixes | *uncommitted* | **~117.98** | provisional — see below |

Two sessions have been working this repo in parallel today:

| workstream | owns | landed |
| --- | --- | --- |
| extraction pipeline | `mib/{records,runner,config,cache,packet,parse,vocab}.py`, `mib/stages/`, `tests/`, most of `scripts/` | `ff497f6`, `b5542cb`, `0285f25`, `0aa7031` |
| decision layer | `mib/{features,decision}.py`, `mib/decision_model.npz`, `scripts/{train,export}_decision.py`, the S5 block in `mib/runner.py` | `adff813`, `a21fc36` |

### The dirty tree

The working tree holds the P3 parse work: `mib/{packet,parse,policy,signals,vocab}.py`,
`tests/test_regression.py`, and the regenerated fixture. It is **not one change** and should not be
committed as one:

**Solid** — suite green, CFA 0, five new regression tests, measured at 117.98 (`output/replay_p3d`):
prose extraction for the sponsor attestation, the `Purpose:` alias, the fabricated-`"none"` fix,
the flag-evidence guard on the risk-concealment census, and damage-marker rejection.

**Should come out** — corroborated loose key matching. It scores +0.29 on top
(`output/replay_p3e`, 118.27, CFA 0) and it admits `arrival_date = 2026-03-41`, because
`parse.valid_value` checks the *shape* `\d{4}-\d{2}-\d{2}` and never asks whether the date exists.
It also produced `declared_purpose = 'Uipremenes ainlamatic'` and flipped a policy branch on
MIB-000805. A change that scores well by admitting garbage is the kind this repo has been careful
to reject.

With the loose-key change still present, **4 characterization tests fail** — that is the fixture
being stale against it, not a defect. Removing it and regenerating should return the suite to green.

### Why 117.98 is provisional

It was measured by replaying a page-text cache that is only **~92% current** (11 of the first 133
rebuilt cases had different page text). The rebuild was stopped at 300/1000 — the host was
saturated by unrelated load, and a contended timing run is worth nothing. `output/cache/train_skew_p3.jsonl`
holds the partial rebuild. **Finish it and re-score before quoting the number anywhere.**

---

## What we tried and kept

Mechanism, not just the delta — the delta is in [experiments.md](experiments.md).

- **Ship `skew` as the default** (+0.70). The default was `off`, so a container built from the last
  commit scored 114.50 while the log's headline read 116.88. Only the default moved.
- **Split the pipeline at the cache boundary** (0.00, byte-identical). `runner.read_case` (S1+S2,
  expensive, impure) and `runner.predict_from_evidence` (S3→emit, pure) — which is what makes the
  seconds-long replay loop possible at all. `Read` became plural per page; `ocr_page` used to score
  several OCR variants and return only the winner, so an ensemble could not be written down.
- **Per-field candidate preference** (+0.23). `packet.docs` sorted by `(doc_type, source)`, which
  ranks whole *documents* — so an OCR'd high-trust document won every field at once over a clean
  text-layer copy elsewhere. `Miravoss` was losing to `Mirayoss`. Preferring the clean read and
  settling ties by field-manual trust order fixed both flagged cases against truth.
- **Runner robustness** (0.00 on train, by design). `pool.map` blocked until all 5,000 PDFs
  finished and only then wrote, so a container stopped at the 30,000s limit was scored on an
  **empty file**; verified that killing a run after 25s now leaves 24 valid rows. Plus: a failed
  case emits a NEEDS_REVIEW row instead of nothing, `case_id` is coerced, `spawn` is pinned.
- **Prose extraction + the `Purpose` alias** (+2.20, in flight). The sponsor attestation states its
  facts in sentences, so `parse_kv` saw nothing on it. The sentence *wraps*, so matching has to join
  lines first — `expected on Earth for reactor / maintenance.` splits a purpose across a newline.
- **Unreadable risk lines stop being repaired into `"none"`** (+0.35, in flight, and it removed a
  CFA). See hazards below.
- **Damage markers are not values** (0.00, in flight). 51 emitted values were literal document
  damage text — `[NAME CUT OUT]` as an applicant name. Scores the same as `unknown` either way;
  emitting document damage as an answer is wrong on its own terms.

---

## What we tried and rejected

The more useful half. Each of these looked reasonable and was killed by a measurement.

- **Vocabulary passthrough for unseen values** (−0.08, then −0.04 after the preference fix).
  `mib/vocab.py`'s docstring promises unseen values pass through so private-set values survive;
  only `declared_purpose` delivers it, and a strict-xfail test was standing by to confirm the bug.
  It measured as a *loss* twice: deleting a value snapping could not vouch for was quietly acting as
  a quality filter, letting a cleaner copy on another document supply the field. Its premise was
  also false — 1,000 train cases yield exactly 13 home worlds, 12 species and 10 purposes, and the
  vocabulary lists *are* those enumerations. A fourteenth world would be expected ~77 times in a
  sample that size, so the universe is closed and there was nothing to rescue. The xfail is now an
  ordinary test asserting the deletion is deliberate.
- **Corroborated loose key matching** (+0.29, admits invalid dates). See the dirty-tree section.
- **`b13_census` → APPROVED wholesale** (+0.80–0.91 by attribution). Manufactures false approvals
  on the 12 truly-denied cases in that branch. **Split the branch, never flip it.**
- **300-DPI grayscale/autocontrast OCR retry** (+0.21 for 43× runtime, [row 8](experiments.md)).
  The lesson that superseded it: those pages were not low-resolution, they were turned, skewed and
  shredded. ~5 ms of numpy on the geometry axis bought +2.38. *Diagnose the transform before
  scaling the compute.*
- **The hidden-text substring invariant** — the *test* was wrong, not the pipeline. It asserted no
  emitted value may appear only in hidden text. But the injected answer keys hold the **true**
  values, and correct OCR repair converges on them: page text reading `Home World: Woll-1081c` is
  repaired to `Wolf-1061c`, which is absent from the visible text and present in the injected key.
  A substring test calls that a leak; it is the opposite. Replaced with two differential tests —
  blank the hidden lines, and inject a hostile answer key; the record must not move either way.
  Both pass on all 72 fixture cases, which is a far stronger claim than the original could make.
- **A 45s per-case OCR budget.** It truncated a real case (MIB-000008) purely because the host was
  busy. It is a wall-clock guard, so any value low enough to bite makes output depend on machine
  load. Now 120s — above the worst case ever measured (107s), so it bounds a runaway without ever
  firing normally.

---

## How to measure anything

**The fast loop** — seconds per variant, instead of a 40-minute pipeline run:

```bash
scripts/dump_text.py <pdf_dir> output/cache/train_skew.jsonl skew   # once, ~18 min idle
scripts/replay.py output/cache/train_skew.jsonl output/replay_foo   # seconds
scripts/score_split.py output/replay_foo dev
```

A full 2×2 matrix of merge-preference × vocab-passthrough was scored in about a minute this way.

**The two gates.** Use the right one — they cover opposite halves of the pipeline:

| change touches | gate | passes when |
| --- | --- | --- |
| S3–S5, emit (downstream of OCR) | `scripts/replay.py <cache> --reference <predictions.jsonl>` | prints `IDENTICAL` |
| S1–S2 (extract, render) | `scripts/verify_render.py` | re-reads real PDFs, reproduces cached page text |

`replay.py` starts *from* the cache, so it proves nothing about the code that produces it. That is
why `verify_render.py` exists, and it has already caught a real regression.

**The three instruments:**

- `scripts/attribute.py <eval_dir> <split>` — splits classification loss per `(branch, prediction)`
  into *relabel* (the entire ML ceiling) vs *finer partition*. It reproduces the official scorer's
  classification number exactly, which is what makes the two comparable.
- `scripts/recoverable.py <cache> <eval_dir> <split> [branch]` — is the truth value even present in
  the text? Splits misses into `absent` (a rendering problem) and `parsefail` (a parsing problem),
  in points. The optional branch filter answers "is this branch blocked by missing evidence, or by
  our use of it?"
- `pytest` — 72-case characterization fixture, invariants, regression. ~3s.

**Provenance.** Every artifact is stamped with its render config and git rev (`mib/config.py`), and
consumers call `config.require_agreement` before joining. This exists because a metric run silently
joined `skew`-derived text against `bands`-derived predictions and produced confident wrong numbers.

---

## Open questions

| # | question | what would settle it |
| --- | --- | --- |
| 1 | Is ~117.98 real? | finish the cache rebuild (stopped at 300/1000), replay, re-score |
| 2 | Does the pipeline fit the runtime budget? | a representative Docker parity run — see below |
| 3 | Can `turn`/`bands` (+1.68) be made affordable? | detect-first geometry: measure skew/orientation once, apply one transform, OCR once |
| 4 | Is CFA 0 a hard gate or a priced cost? | **a decision, not a measurement** — see hazards |
| 5 | Where do the remaining 16.66 classification points go? | `fee_unknown` 7.37 and `b13_census` 5.37 are the two big cells, and neither is an OCR problem |
| 6 | Does the dev→holdout gap still hold? | holdout untouched since 113.46 at `v1`; read only at a milestone |

**Docker parity (question 2) is partially answered.** The container has now been run under the
exact contract flags for the first time: image **0.13 GiB** against a 4 GiB cap, `--network none`,
read-only root, tmpfs `/tmp`, 4 cpu / 8 GiB — it runs and writes correct output. A 12-PDF sample
ran at 1.11 s/PDF, extrapolating to **~1.55 h for 5,000** against the 8.3 h budget, which agrees
with the laptop projection. **This is not yet a real answer**: 12 PDFs contain none of the heavy
tail (p99 57s, max 107s per case), and every laptop timing in this repo was taken under load.

**On question 5**, the evidence is already in and it points away from OCR:

- `fee_unknown` (7.37 pts): its `fee_status` is visible in **0.0%** of cases, 3.6% with OCR, and
  hidden-only in 41 of 139. The fee genuinely is not in the document.
- `b13_census` (5.37 pts): **89 of 95** cases have no B-13 anywhere and only 6 are detection
  misses — but the other fields on those cases read at **98.0%**. It needs a split on evidence we
  already hold, not better reading.

---

## Known hazards

Each of these has cost time once already.

**S3's vocabulary silently drives S2's OCR.** `stages/render.evidence_score` scores a page by how
many labels `parse.key_for` recognizes. Adding one `KEY_MAP` entry (`purpose`) changed which OCR
variant won — **in both directions** — and invalidated the page-text cache. `verify_render.py`
caught it at 4/6 identical. This means S2 is *not* a pure function of (image, render config), so
the cache boundary the whole fast loop rests on is weaker than it looks: **a parser change can
silently invalidate your cache.** The fix is to give `render` its own frozen label set for
page-quality scoring; that is a behaviour change and needs its own measured row. Making `_key_for`
public during P1a legitimized this dependency instead of removing it — that was the wrong call.

**Better extraction can remove a safety net.** Filling `visa_class` and `sponsor_id` on MIB-000672
stopped the missing-field guards from firing, and the case fell through to `clean_approve` — a
catastrophic false approval. The underlying defect was worse: its B-13 risk line had OCR'd to
`Observed fans: =-*` / `rant` (truly `active_warrant`), and `vocab.snap` **repaired that debris
into `"none"`**, fabricating a positive assertion that no risk flag was observed. Fixed in two
places — unreadable flags now return `None`, and the risk-concealment census asks whether the risk
line was *read*, not whether a slip was *detected*. Generally: **when extraction improves, re-check
the branches whose job was to catch missing data.**

**The two workstreams disagree about the CFA gate, and it is unreconciled.** `docs/PLAN.md`'s
amendment says catastrophic false approvals are now *priced, not banned* — expected-points argmax
already charges −4 for one, with `MIB_CFA_VETO` as a tunable demotion threshold. The extraction
side has been treating CFA 0 as a hard gate and rejected changes on that basis. Both positions are
defensible; the learned decider currently shows **5 CFAs** on a train-fit dev A/B. This needs a
deliberate decision, not resolution by whoever commits last.

**Parallel sessions interfere, and the interference looks like a result.** The journal records a
phantom "extraction changed under mlp" that was actually an in-flight `packet.py` edit landing
between two replays. If two people are editing, pin the tree (`git stash`, or replay from a fixed
commit) before an A/B, and diff the non-decision fields to catch it.

**Laptop timings are not evidence.** Wall figures on rows 11–14 were wrong by 10–50× and a
conclusion drawn from them ("wall-clock falls as restoration deepens") had to be retracted from two
documents. Only `scripts/run_docker_submission.py` under the real limits settles a runtime question.

---

## Immediate next steps

1. Drop the loose-key change, regenerate the fixture, confirm the suite is green, commit the solid
   P3 work with the score marked provisional.
2. Finish the cache rebuild on a quiet machine; re-score; correct the number if it moved.
3. Representative Docker parity run over the full train corpus under contract limits.
4. Decide the CFA question (hazard 3) before either workstream builds further on its assumption.
5. Then either detect-first geometry (question 3) or the `b13_census` split (question 5).
