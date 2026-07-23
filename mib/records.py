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
    must never reach field extraction (docs/fraud-signals.md §1).
    """
    page_no: int = 0
    visible_lines: list = field(default_factory=list)
    hidden_lines: list = field(default_factory=list)
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
    """
    page_no: int = 0
    lines: list = field(default_factory=list)
    variant: str = ""
    quality: float = 0.0
    cost_ms: int = 0


# --- S3 --------------------------------------------------------------------


@dataclass
class Candidate:
    """One value for one field, with everything needed to prefer another.

    `raw_value` is what the document actually said before vocabulary repair;
    keeping both is what lets a later stage tell a clean read from a rescued one.
    """
    field_name: str = ""
    value: str = ""
    raw_value: str = ""
    doc_type: int = 0
    source: int = 0
    page_no: int = 0
    quality: float = 0.0
