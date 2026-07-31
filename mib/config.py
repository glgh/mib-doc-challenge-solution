"""What configuration a run is at, and how to stamp it onto an artifact.

Every expensive artifact — the page-text cache, an eval directory — is a
function of the render config *and* of the code that produced it. Joining two
artifacts built at different configs silently produces wrong numbers rather than
an error: a text dump at one restoration level joined against predictions from
another mixes two pipelines, so every "the value is in the text but we failed to
extract it" verdict is measured against text the predicting run never saw. That
happened, and nothing caught it. Hence: producers stamp, consumers check.

P1 replaces the remaining environment reads with a frozen config object threaded
from the entrypoint; this module is where that object will live. Everything the
pipeline's shape depends on is already owned here, so stages ask rather than
re-parsing the environment themselves.
"""
import datetime
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# The S2 enumeration plan (render.reads_for): the composition grid (docs/TODO.md
# Track 6) — raw + every orientation's in-frame correction chain unconditionally
# (turn-gating on page-level weakness was offline-proven unsafe — MIB-000509-class
# pages clear the bar on raw while the turn read carries the fields); weak pages
# (frozen page_score) expand with optical modules composed OVER the corrected
# frames; a still-dead page may get one last-resort PSM-3 pass. Anchors:
# MIB-000061 (skew+deshred+adapt reads the fee line), MIB-000030 p2 (turn1
# chains read a six-field intake block).
#
# The `ladder` legacy enumerator (the pre-grid `bands+local` pipeline, kept
# behind MIB_PLAN for A/Bs) was deleted in the de-special-casing batch
# (2026-07-26) after the grid proved itself (rows 59-60); it lives in git
# history, and its retired caches are replay-only.
#
# The MIB_GEOM_SET / MIB_OPT_SET / MIB_OPT_BASE / MIB_LAYOUT_PASS env knobs
# override individual grid fields for A/Bs. Plan identity feeds the `restore`
# stamp, so require_agreement/verify_render refuse cross-plan joins with no
# new code.
GRID_PRESETS = {
    # layout_pass default-on 2026-07-27 (TODO 6.7): one PSM-3 re-read on a page
    # whose field label is present but its value truncated. Subset A/B FIXED 3 /
    # BROKE 0 / CFA 0 + one correct adjudication (362 NR->DENIED); `off` reverts.
    "grid": {"name": "grid", "geom": ("skew", "turn1", "turn3", "deshred", "local"),
             "opt": ("adapt", "autocon"), "opt_base": "frames", "layout_pass": "psm3",
             "render_base": "up200"},
}
DEFAULT_PLAN = "grid"            # flipped 2026-07-26 (Track 6 Phase 3: dev 124.94 -> 125.35, CFA 0, FIXED 25 / BROKE 0)


def grid_plan():
    """The resolved S2 enumeration plan for this process."""
    plan = dict(GRID_PRESETS[DEFAULT_PLAN])
    if os.environ.get("MIB_GEOM_SET"):
        plan["geom"] = tuple(t.strip() for t in os.environ["MIB_GEOM_SET"].split(",") if t.strip())
    if os.environ.get("MIB_OPT_SET"):
        plan["opt"] = tuple(t.strip() for t in os.environ["MIB_OPT_SET"].split(",") if t.strip())
    if os.environ.get("MIB_OPT_BASE") in ("raw", "frames"):
        plan["opt_base"] = os.environ["MIB_OPT_BASE"]
    if os.environ.get("MIB_LAYOUT_PASS") in ("off", "psm3"):
        plan["layout_pass"] = os.environ["MIB_LAYOUT_PASS"]
    # render_base: how the whole-page render source is resolved. "up200" (default)
    # keeps the historical >=200-DPI floor (upscaling the ~144-DPI train scans);
    # "native" renders at the largest embedded image's own resolution instead, so
    # tesseract reads the raw scan grid rather than an interpolated upscale (probe:
    # the 200-DPI upscale garbles marginal digits the native read gets — MIB-000543
    # SPN-0007). A/B knob; stamped into `restore`.
    if os.environ.get("MIB_RENDER_BASE") in ("up200", "native"):
        plan["render_base"] = os.environ["MIB_RENDER_BASE"]
    return plan


def _restore_for(plan):
    """Plan identity as the `restore` stamp value. Any deviation from the grid
    preset is spelled out so no two behaviours share a stamp. (The retired
    ladder's caches carry the historical "bands+local" stamp, which nothing
    produces anymore — they refuse to join current artifacts by construction.)"""
    diffs = []
    base = GRID_PRESETS["grid"]
    for key in ("geom", "opt", "opt_base", "layout_pass", "render_base"):
        if plan[key] != base[key]:
            val = ",".join(plan[key]) if isinstance(plan[key], tuple) else plan[key]
            diffs.append(f"{key}={val}")
    return "grid" + (f"[{';'.join(diffs)}]" if diffs else "")


RESTORE = _restore_for(grid_plan())

# 2: page dicts carry `struck` (red-strikethrough value cells). Older schema-1
# caches lack the key and rehydrate with struck=[] (no voiding) — backward
# compatible, so this is a documentation signal, not a join gate (require_agreement
# does not check SCHEMA).
# 3: reads carry `conf` — per-line (mean word conf, n_words, y_frac) from the
# tsv renderer of the same recognition pass. Older caches rehydrate conf=None.
# 4: conf entries gain the line's cleaned TEXT as a 4th element, making
# per-line confidence queryable (vote ties, per-field preference, consensus).
# Schema-3 caches rehydrate 3-tuples; consumers index [:3] and must not unpack
# by arity.
# 5: reads carry `cost_ms` (that pass's tesseract wall time). Older caches
# rehydrate cost_ms=0. Wall clock is nondeterministic by construction, so it
# must NEVER join a comparison key (verify_render excludes it); it exists so
# pass-level cost is learnable offline (the row-20 PSM-3 gate-direction
# question, plan-ablation cost accounting).
SCHEMA = 5

# A mismatch here means the two artifacts describe different pipelines and any
# join across them is meaningless. A key MISSING from a stamp (artifact written
# before the key existed) is tolerated — only two present-but-different values
# refuse the join.
CRITICAL_KEYS = ("restore", "early_stop", "ocr_passes", "ocr_optical")
# A mismatch here is usually just as invalidating, but the working tree is
# routinely dirty mid-phase and rebuilding every artifact per commit is not
# affordable, so it warns instead of failing.
ADVISORY_KEYS = ("git_rev",)


# S2 runs a single PSM 11 (sparse text) pass per image. The MIB_OCR_PASSES=dual
# second PSM 3 pass (+0.87 dev, unshipped for cost — row 20) was deleted in the
# de-special-casing batch (2026-07-26); the grid's last-resort tier is the
# designated revival path (one PSM 3 call per still-dead page, not per image).
# The stamp keeps emitting `ocr_passes` so old caches still join-check.
OCR_PASSES = "psm11"

# `records.best_read` ranks by guarded excess confidence mass (conf, default
# since row 43); the `MIB_SELECT=ev` legacy selector (evidence_score, the
# hand-built shape score) was deleted in the same batch. Reads without conf
# (tesseract tsv failure) fall back to `quality`. The stamp keeps emitting
# `select` so old caches still join-check.
SELECT_METRIC = "conf"


def ocr_optical():
    """Whether S2 adds optical variants — local-adaptive threshold + autocontrast
    (`mib.imaging`) — to weak pages of the OCR ensemble (render.reads_for gates
    on the frozen page_score staying below WEAK_BAR). Recovers faint/unevenly-lit
    scans a global binarization erases.

    ON by default since the conf-selection era (row 48): its killer under ev
    was well-formed binarized garbage outscoring correct readings (11 recovered
    / 10 corrupted, unguarded); under conf + the weak-page gate the hard-set
    A/B measured 1 better / 0 worse. ~55% of OCR pages are gate-eligible
    (~+36% S2 time on the hard tail, well inside the 6 s/PDF budget). Opt out
    with MIB_OCR_OPTICAL=off for A/Bs. Critical: it changes which reading wins,
    so caches built either side of the flip must not be joined
    (`require_agreement` tolerates stamps from before the key existed).
    """
    return os.environ.get("MIB_OCR_OPTICAL", "on").strip().lower() in ("on", "1", "true", "yes")


# The Docker contract gives 4 vCPU, so the submission must default to 4 workers.
# Local dev (this repo is developed on a 10-core M1 Max) is not resource-bound,
# so `MIB_WORKERS` overrides it — e.g. MIB_WORKERS=9. Deliberately NOT stamped and
# NOT in CRITICAL_KEYS: worker count is a wall-clock knob only. Every pool consumes
# its PDFs in order (imap/map), so output is byte-identical regardless of the count
# — a cache built at MIB_WORKERS=9 is joinable with one built at 4.
def workers(default=4):
    try:
        return max(1, int(os.environ.get("MIB_WORKERS") or default))
    except ValueError:
        return default


# S2 reads every geometric variant and keeps the best; the early stop that used
# to be selectable here is gone (it measured −0.21 dev, experiments.md row 16).
# The key stays in the stamp as a frozen False so that caches built before the
# removal — which are not in git, `output/` being ignored — remain joinable.
EARLY_STOP = False


def _git_state():
    try:
        rev = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, timeout=5)
        if rev.returncode:
            return None, None
        dirty = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain"],
                               capture_output=True, text=True, timeout=5)
        return rev.stdout.strip(), bool(dirty.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        return None, None


def stamp(**extra):
    """Provenance for an artifact produced right now, under the current config."""
    rev, dirty = _git_state()
    return {
        "schema": SCHEMA,
        "restore": _restore_for(grid_plan()),   # live, not the import-time constant
        "early_stop": EARLY_STOP,
        "ocr_passes": OCR_PASSES,
        "ocr_optical": ocr_optical(),
        "select": SELECT_METRIC,
        "git_rev": rev,
        "git_dirty": dirty,
        "created": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        **extra,
    }


def describe(meta):
    if not meta:
        return "UNSTAMPED"
    dirty = "+dirty" if meta.get("git_dirty") else ""
    back = " (backfilled)" if meta.get("backfilled") else ""
    es = " early_stop" if meta.get("early_stop") else ""      # legacy caches only
    passes = meta.get("ocr_passes")
    ocr = f" ocr={passes}" if passes and passes != OCR_PASSES else ""
    opt = " optical" if meta.get("ocr_optical") else ""
    # Legacy stamps may carry a `decider` key (the learned decider, deleted);
    # shown so an old mlp eval artifact is still identifiable as one.
    dec = f" decider={meta['decider']}" if meta.get("decider") not in (None, "rules") else ""
    # Uppercase on purpose: a subset cache's scores are not split numbers, and
    # this tag is the only thing standing between a probe and a quoted "dev" score.
    sub = f" SUBSET={meta['subset']}({meta.get('n_subset', '?')})" if meta.get("subset") else ""
    return f"restore={meta.get('restore', '?')}{es}{ocr}{opt}{dec}{sub} rev={meta.get('git_rev') or '?'}{dirty}{back}"


def require_agreement(labelled):
    """Check that artifacts about to be joined came from the same pipeline.

    `labelled` is [(name, meta), ...]. Disagreement on a critical key raises;
    an advisory mismatch or a missing stamp warns. Either way the provenance of
    every input is printed, so the numbers below it are on the record.
    """
    print("== provenance ==")
    for name, meta in labelled:
        print(f"  {name:52s} {describe(meta)}")

    stamped = [(n, m) for n, m in labelled if m]
    unstamped = [n for n, m in labelled if not m]
    if unstamped:
        print(f"  WARNING: no provenance stamp on {', '.join(unstamped)} — "
              f"cannot verify these were built at the same config.")

    problems = []
    for key in CRITICAL_KEYS + ADVISORY_KEYS:
        # None = stamp predates the key; only present-but-different values conflict.
        seen = {m.get(key) for _n, m in stamped} - {None}
        if len(seen) > 1:
            detail = ", ".join(f"{n}={m.get(key)!r}" for n, m in stamped)
            problems.append((key, f"{key} differs across inputs: {detail}"))

    for key, message in problems:
        if key in CRITICAL_KEYS:
            raise SystemExit(
                f"\nrefusing to join artifacts from different pipelines: {message}\n"
                f"rebuild the stale input at the current config, e.g.\n"
                f"  scripts/dump_text.py <pdf_dir> <cache.jsonl>\n")
        print(f"  WARNING: {message} — results may mix code revisions.")
    if any(m.get("git_dirty") for _n, m in stamped):
        print("  note: at least one input was built from a dirty tree; "
              "its git_rev does not fully identify the code that produced it.")
    print()
