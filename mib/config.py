"""What configuration a run is at, and how to stamp it onto an artifact.

Every expensive artifact — the page-text cache, an eval directory — is a
function of the render config *and* of the code that produced it. Joining two
artifacts built at different configs silently produces wrong numbers rather than
an error: a text dump at one restoration level joined against predictions from
another mixes two pipelines, so every "the value is in the text but we failed to
extract it" verdict is measured against text the predicting run never saw. That
happened, and nothing caught it. Hence: producers stamp, consumers check.

P1 replaces the environment read with a frozen config object threaded from the
entrypoint; this module is where that object will live. `restore_level()` is
already the single owner of the answer, so `mib.ocr` asks rather than re-parsing
the environment itself.
"""
import datetime
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Geometric scan restoration, cumulative: each level includes the ones before it.
RESTORE_LEVELS = ("off", "skew", "turn", "bands")
DEFAULT_RESTORE = "skew"

SCHEMA = 1

# A mismatch here means the two artifacts describe different pipelines and any
# join across them is meaningless.
CRITICAL_KEYS = ("restore", "early_stop")
# A mismatch here is usually just as invalidating, but the working tree is
# routinely dirty mid-phase and rebuilding every artifact per commit is not
# affordable, so it warns instead of failing.
ADVISORY_KEYS = ("git_rev",)


def restore_level():
    level = os.environ.get("MIB_RESTORE", DEFAULT_RESTORE).lower()
    return level if level in RESTORE_LEVELS else DEFAULT_RESTORE


def at_least(level):
    return RESTORE_LEVELS.index(restore_level()) >= RESTORE_LEVELS.index(level)


def early_stop():
    """Whether S2 stops OCRing a page once a reading looks 'good enough'.

    Off by default: an exhaustive read (every geometric variant, keep the best)
    scored +0.21 dev over stopping early — the early stop was settling for a worse
    variant — for ~0.5s/case of a 6s budget (see docs/experiments.md). Opt back in
    with MIB_EARLY_STOP=1. Critical + stamped: a cache built one way must never be
    joined with predictions built the other, exactly like `restore`.
    """
    return os.environ.get("MIB_EARLY_STOP", "0") == "1"


# Decider config is stamped for visibility (shown by `describe`) but deliberately
# NOT in CRITICAL_KEYS yet: a page-text cache is decider-independent, so enforcing
# it would false-positive cache<->eval joins until `require_agreement` learns to
# skip keys absent from some inputs. Promoting to critical is a follow-up.
def decider():
    """Which S5 decider produced an eval artifact (`rules` | `mlp`)."""
    return os.environ.get("MIB_DECIDER", "rules").lower()


def cfa_veto():
    """The learned decider's P(DENIED) demotion threshold (`MIB_CFA_VETO`)."""
    return os.environ.get("MIB_CFA_VETO", "1.0")


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
        "restore": restore_level(),
        "early_stop": early_stop(),
        "decider": decider(),
        "cfa_veto": cfa_veto(),
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
    es = " early_stop" if meta.get("early_stop") else ""
    dec = f" decider={meta['decider']}" if meta.get("decider") else ""
    return f"restore={meta.get('restore', '?')}{es}{dec} rev={meta.get('git_rev') or '?'}{dirty}{back}"


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
                f"rebuild the stale input at the config you want to measure, e.g.\n"
                f"  MIB_RESTORE=<level> scripts/dump_text.py <pdf_dir> <cache.jsonl>\n")
        print(f"  WARNING: {message} — results may mix code revisions.")
    if any(m.get("git_dirty") for _n, m in stamped):
        print("  note: at least one input was built from a dirty tree; "
              "its git_rev does not fully identify the code that produced it.")
    print()
