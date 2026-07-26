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

# Geometric scan restoration. S2 always runs the full ladder — deskew,
# quarter-turn, shred-band realignment — because it recovers turned/skewed/
# shredded scan pages that no cheaper rung reaches. The selectable `off`/`skew`/
# `turn` rungs are gone: they existed to A/B the ladder (experiments.md rows
# 11-14, still on the record) and to retreat if the full ladder overran the
# budget, but the submission runs the image with no `-e`, so the retreat was
# always a rebuild — i.e. a checkout — not a flag.
#
# Kept as a stamp constant so caches written before the removal stay joinable,
# and so a legacy `skew` cache is still correctly *refused* rather than silently
# mixed into a bands run.
RESTORE = "bands"

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
SCHEMA = 4

# A mismatch here means the two artifacts describe different pipelines and any
# join across them is meaningless.
CRITICAL_KEYS = ("restore", "early_stop", "ocr_passes")
# A mismatch here is usually just as invalidating, but the working tree is
# routinely dirty mid-phase and rebuilding every artifact per commit is not
# affordable, so it warns instead of failing.
ADVISORY_KEYS = ("git_rev",)


OCR_PASS_MODES = ("psm11", "dual")
DEFAULT_OCR_PASSES = "psm11"


def ocr_passes():
    """How many Tesseract page-segmentation passes S2 runs per image.

    `psm11` (default): the single sparse-text pass this corpus was tuned on.
    `dual`: also run PSM 3 (full auto layout) per image and let `best()` keep the
    stronger reading — the top competitor's recipe. PSM 3 reads dense/tabular forms
    that PSM 11 fragments; PSM 11 wins on the scattered fragments a restored scan
    leaves. Critical + stamped: dual-pass changes which reading wins, so its page
    text must never be joined with a single-pass cache (exactly like `restore`).
    Off by default so the change lands score-neutral until measured; opt in with
    MIB_OCR_PASSES=dual.
    """
    mode = os.environ.get("MIB_OCR_PASSES", DEFAULT_OCR_PASSES).lower()
    return mode if mode in OCR_PASS_MODES else DEFAULT_OCR_PASSES


SELECT_METRICS = ("ev", "conf")
DEFAULT_SELECT = "conf"


def select_metric():
    """Which metric `records.best_read` ranks readings by.

    `conf` (default since row 42's A/B, user-approved): guarded excess
    confidence mass from the engine's own per-word conf
    (records.conf_excess_mass). `ev` (evidence_score, the hand-built shape
    score) remains selectable via MIB_SELECT=ev for A/Bs until Phase E deletes
    it. Reads without conf (pre-conf caches) always fall back to `quality`, so
    old caches replay unchanged under either setting. Stamped: selection
    changes which reading is primary, so it is part of an artifact's identity.
    """
    mode = os.environ.get("MIB_SELECT", DEFAULT_SELECT).strip().lower()
    return mode if mode in SELECT_METRICS else DEFAULT_SELECT


def ocr_optical():
    """Whether S2 adds optical variants — local-adaptive threshold + autocontrast
    (`mib.imaging`) — to the OCR ensemble. Recovers faint/unevenly-lit scans a
    global binarization erases; the scan-page miner shows label-proven headroom.

    Off by default so it lands score-neutral until measured; opt in with
    MIB_OCR_OPTICAL=on. Stamped for visibility but deliberately NOT yet in
    CRITICAL_KEYS: it changes which reading wins, so promote it to critical once
    `require_agreement` tolerates keys absent from stamps written before it
    existed — otherwise every join with an older cache false-positives on
    None-vs-False.
    """
    return os.environ.get("MIB_OCR_OPTICAL", "").strip().lower() in ("on", "1", "true", "yes")


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
        "restore": RESTORE,
        "early_stop": EARLY_STOP,
        "ocr_passes": ocr_passes(),
        "ocr_optical": ocr_optical(),
        "select": select_metric(),
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
    ocr = f" ocr={passes}" if passes and passes != DEFAULT_OCR_PASSES else ""
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
        seen = {m.get(key) for _n, m in stamped}
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
