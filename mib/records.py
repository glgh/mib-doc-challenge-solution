"""The records that cross stage seams.

Seams sit where the **grain changes** and where **cost and purity change**:

    S1 extract   PDF         -> Page[]        pure, cheap
    S2 render    Page        -> Read[]        IMPURE, EXPENSIVE  <- cache boundary
    S3 parse     Read[]      -> Candidate[]   pure, cheap
    S4 assemble  Candidate[] -> CaseRow       pure, cheap
    S5 decide    CaseRow     -> Decision      pure, cheap        <- rules <-> ML swap

What each record *keeps* is the point of this module. The previous pipeline lost
information at every seam — one OCR variant per page, one value per field, one
rule branch per case — and each loss closed off a class of improvement before it
could be attempted. Concretely:

* `Read` is **plural per page**. The old `ocr_page` scored several variants and
  returned only the winner, so an ensemble over variants could not be written
  down, let alone measured.
* `Candidate` keeps **every** value seen for a field, not the one that won
  precedence. 37 dev field-instances are lost today because `packet.docs` sorts
  by `(doc_type, source)` and an OCR'd high-trust document outranks a clean
  text-layer lower-trust one for every field at once (`Miravoss` loses to
  `Mirayoss`). Per-field source preference needs the alternatives to still exist.
* `CaseRow` carries **every** rule predicate, not just the first that fired, so
  the same row serves as heuristic input, ML design matrix and debug sidecar.

These are plain dataclasses, not validated models: they are internal plumbing,
and `mib/emit.py` remains the single place where anything is guaranteed
well-formed.
"""
from dataclasses import dataclass, field

# --- S1 --------------------------------------------------------------------


@dataclass
class Page:
    """One page's text layer, with hidden spans quarantined.

    Hidden lines are retained for diagnostics and for the injection tests, and
    must never reach field extraction (docs/BACKGROUND.md section 3).

    `struck` holds value-cell text the document crossed out with a red
    strikethrough (S1 detects it from the vector layer). A struck value is the
    document voiding its own printed value — not sourceable evidence, exactly
    like a hidden span or a damage marker — so the merge drops any field whose
    value matches one (mib/packet.py).
    """
    page_no: int = 0
    visible_lines: list = field(default_factory=list)
    hidden_lines: list = field(default_factory=list)
    struck: list = field(default_factory=list)
    image_count: int = 0

    @property
    def is_scan_only(self):
        """Visible content is pixels, not text — this page needs S2."""
        return self.image_count > 0 and len(self.visible_lines) <= 3


# --- S2 --------------------------------------------------------------------


@dataclass
class Read:
    """One OCR reading of one page. Many per page by design.

    `variant` names what produced it (engine x preprocessing x geometry) so a
    later ensemble can weight readings by provenance, and so the cost of each
    strategy is attributable.

    `conf` is the engine's own per-line self-assessment from the same
    recognition pass: [(mean word conf, n_words, y_frac)] per tsv line, or None
    for reads rehydrated from a pre-conf cache. It is a parallel measurement of
    the page, NOT aligned 1:1 with `lines` (the two tesseract renderers group
    lines differently) — page-level metrics need no alignment.
    """
    page_no: int = 0
    lines: list = field(default_factory=list)
    variant: str = ""
    quality: float = 0.0
    conf: list = None
    cost_ms: int = 0


# Guarded excess-mass constants (probe 1.1, user-graduated; re-derived corpus-
# wide in the 1.3 A/B). CONF_BASELINE: a word contributes only its confidence
# above the junk floor, so debris volume adds ~nothing (raw mass's trap) while
# a few confident garbage words can't outrank a dense honest read (mean's
# trap). FOOTER_Y/watermark: page furniture OCRs at conf 90+ on the render
# source only, which would bias any mass metric toward `render` regardless of
# how the field block read.
CONF_BASELINE = 40.0
FOOTER_Y = 0.90


def conf_excess_mass(read):
    """Engine-confidence selection metric for one Read, or None without conf.

    Sum over non-furniture tsv lines of max(0, mean_conf - CONF_BASELINE) *
    n_words. Furniture = the printed footer band (y_frac >= FOOTER_Y) — the
    positional guard, preferred over wording regexes because it needs no
    vocabulary of furniture strings.
    """
    if read.conf is None:
        return None
    total = 0.0
    for line_conf, n_words, y_frac in read.conf:
        if y_frac >= FOOTER_Y:
            continue
        total += max(0.0, line_conf - CONF_BASELINE) * n_words
    return total


def best_read(reads):
    """The highest-ranked reading, or None. Earliest wins ties, because S2
    generates readings cheapest-first, so the earlier read cost less to obtain.

    Ranking metric: `evidence_score` (stored as `quality`) by default;
    `config.select_metric()=conf` ranks by `conf_excess_mass` instead — reads
    without conf (pre-conf caches) keep ranking by `quality`, so old caches
    replay unchanged under either setting.

    Lives here (not in stages.render) because it is a pure function of stored
    Reads that both S2 tooling and the S4 merge consult — the selection itself
    crosses the seam now that the whole ensemble does.
    """
    from . import config
    use_conf = config.select_metric() == "conf"
    chosen, chosen_key = None, None
    for r in reads:
        key = r.quality
        if use_conf:
            m = conf_excess_mass(r)
            if m is not None:
                key = m
        if chosen is None or key > chosen_key:
            chosen, chosen_key = r, key
    return chosen


# --- S3 --------------------------------------------------------------------


@dataclass
class Candidate:
    """One value for one field, with everything needed to prefer another.

    `raw_value` is what the document actually said before whitespace and
    vocabulary repair; keeping both is what lets a later stage tell a clean read
    from a rescued one.

    `valid` is schema conformance (does this look like a sponsor id at all),
    which is a different question from `quality` (how well did we read the page
    it came from). Today only `valid` is consulted; separating them is what makes
    "prefer the clean text-layer copy over the OCR'd one" expressible later.
    """
    field_name: str = ""
    value: str = ""
    raw_value: str = ""
    doc_type: int = 0
    source: int = 0
    page_no: int = 0
    valid: bool = False
    quality: float = 0.0
