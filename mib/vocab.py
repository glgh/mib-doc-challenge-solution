"""Closed vocabularies (from train labels — policy-level entities, not per-case
data) and fuzzy repair for OCR debris. Snapping is guarded: values far from any
known term pass through unchanged, so unseen private-set values survive."""
import difflib
import re

from . import grammar

HOME_WORLDS = [
    "Barnard-c", "Eris Relay", "Europa Station", "Gliese-581g", "Kepler-186f",
    "Luyten-b", "Mars Dome-7", "Proxima-b", "Sirius Outpost", "TRAPPIST-1e",
    "Titan Freeport", "Wolf-1061c", "Zeta Reticuli",
]
SPECIES = [
    "ALPHA_DRACONIAN", "ANDROMEDAN", "AQUARIAN_MANTIS", "ARCTURIAN",
    "CENTAURI_SYNTH", "JOVIAN_GASFORM", "KAIJU_MICRO", "LUNA_SECURID",
    "ORION_GRAYS", "SIRIUS_AVIAN", "TRIANGULAN", "VENUSIAN_MYCELIAL",
]
PURPOSES = [
    "archive audit", "cultural exchange", "diplomatic", "field repair",
    "medical consult", "reactor maintenance", "research", "transit",
    "translation", "xenobotany",
]
VISAS = ["XW-1", "XW-2", "DIP-1", "MED-3", "TRANSIT-7"]
FEES = ["paid", "waived", "unpaid", "unknown"]

# The generator COMPOSES applicant/registry names as prefix+suffix: the 1,000
# train truth names use exactly the full 12x12 cross-product below (144 name
# parts, verified grid == mined pool, 2026-07-26) — a closed universe by
# construction, the same class of mined enumeration as HOME_WORLDS/SPECIES.
# Out-of-pool tokens in an emitted name are misreads with measured
# P(correct) = 0/61 on dev; in-pool tokens run 0.959.
_NAME_PREFIXES = ("ari", "ixo", "lu", "mira", "nex", "ori",
                  "qor", "sol", "tek", "vee", "xan", "za")
_NAME_SUFFIXES = ("dane", "ix", "kesh", "mora", "nax", "quell",
                  "rix", "tari", "ul", "vara", "voss", "zarn")
NAME_PARTS = frozenset(p + s for p in _NAME_PREFIXES for s in _NAME_SUFFIXES)

# Manual-published + train-inferred (each 11-14 non-DIP occurrences, zero
# approvals; independently corroborated). Policy inference, not case memorization.
# Lives here rather than in mib/policy.py from the era when repair consulted it
# (the fabrication guard, removed row 69); staying put keeps the import
# direction — a vocabulary reaching forward into the rule engine was a circular
# import dodged by a function-local import.
REVOKED_SPONSORS = {
    "SPN-0007", "SPN-0139", "SPN-4040",   # FIELD_MANUAL.md
    "SPN-2718", "SPN-7331", "SPN-9090",   # inferred from train labels
}
FLAGS = ["memory_tampering", "planetary_embargo", "active_warrant", "biohazard_red",
         "identity_conflict", "sponsor_mismatch", "illegible_biometrics",
         "rescinded_denial", "none"]

# The id/date OCR-tolerant coercions (case_id / sponsor_id / arrival_date, with
# their glyph-confusion translate tables) live in `mib.grammar`; `snap` delegates
# to them below. See grammar.coerce_* for the recovery-anchor evidence.


def _closest(value, options, cutoff):
    match = difflib.get_close_matches(value, options, n=1, cutoff=cutoff)
    return match[0] if match else None


# Confusion-weighted snap bars (rows 70-71). difflib's subsequence ratio has
# no indel penalty, so shorter entries steal garbles of longer ones (`naid` ->
# `unpaid` over `paid`; `translation` garbles -> `transit`) and same-score ties
# resolve by list order (`XW-L` sat at 0.75 from both XW-1 and XW-2). Weighted
# levenshtein prices indels 1.0 / glyph confusions 0.3 — the OCR-shaped prior —
# and the margin is the unique-best guard every other matcher already carries.
# Fee bar mined on the row-70 440-tail table; the rest carry their old difflib
# cutoffs onto the new metric, priced by the row-71 replay diff.
_SNAP_MARGIN = 0.05
_SNAP_BARS = {"fee_status": 0.72, "visa_class": 0.65,
              "species_code": 0.70, "home_world": 0.70,
              "declared_purpose": 0.60}


def _norm_vocab(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


_WEIGHTED_TABLES = {
    "fee_status": [(_norm_vocab(o), o) for o in FEES],
    "visa_class": [(_norm_vocab(o), o) for o in VISAS],
    "species_code": [(_norm_vocab(o), o) for o in SPECIES],
    "home_world": [(_norm_vocab(o), o) for o in HOME_WORLDS],
    "declared_purpose": [(_norm_vocab(o), o) for o in PURPOSES],
}


def _best_scored(pairs):
    """Top candidate over (score, label) pairs, and its margin over the runner-up.

    Returns (label, score, score - runner_up_score) — the shared top-2 step of
    every confusion-weighted matcher. The accept bar/margin gate stays at each
    call site because they differ (the flag-value matcher returns raw scores;
    the others gate). Ties break by label (the sort's second key), as always.
    """
    scored = sorted(pairs, reverse=True)
    (best_s, best_l), (second_s, _) = scored[0], scored[1]
    return best_l, best_s, best_s - second_s


def _weighted_closest(field, value):
    """Canonical entry for a garbled read, or None without a clear winner."""
    vn = _norm_vocab(value)
    if not vn:
        return None
    best, best_s, margin = _best_scored(
        (_weighted_sim(vn, n), o) for n, o in _WEIGHTED_TABLES[field])
    if best_s >= _SNAP_BARS[field] and margin >= _SNAP_MARGIN:
        return best
    return None


_NAME_SNAP_BAR = 0.72     # dev sweep plateau 0.70-0.72 (10 recoveries); 0.75 drops to 7
_NAME_SNAP_MARGIN = 0.08  # unique-best guard: 104 pool pairs sit within 0.75 of
                          # each other (arimora/orimora...), so runner-up margin is
                          # what prevents cross-name conflation


def _snap_name(value):
    """Per-token repair of an OCR-read name toward the closed part pool.

    Tokens already in the pool are NEVER touched (they are 95.9% correct on
    dev; a real `Luix` must not become `Lurix`). An out-of-pool token — 0/61
    correct on dev, so any change is a free roll — snaps to its unique best
    pool part at the mined bar/margin, else stays as read.
    """
    toks = value.split()
    if not toks:
        return value
    out = []
    for tok in toks:
        tl = tok.lower()
        if tl in NAME_PARTS:
            out.append(tl.capitalize())
            continue
        best_sim, second, best = 0.0, 0.0, None
        for part in NAME_PARTS:
            s = _weighted_sim(tl, part)
            if s > best_sim:
                best_sim, second, best = s, best_sim, part
            elif s > second:
                second = s
        if best and best_sim >= _NAME_SNAP_BAR and best_sim - second >= _NAME_SNAP_MARGIN:
            out.append(best.capitalize())
        else:
            out.append(tok)
    return " ".join(out)


def repairable_purpose(value):
    """Does the value land in the closed purpose vocabulary at the snap bar?

    `snap("declared_purpose")` passes unmatched values through (free-text
    field), so corroboration checks — is this string really a purpose? — need
    the strict form."""
    return _weighted_closest("declared_purpose", value or "") is not None


def snap(field, value):
    """Repair an OCR-read value toward its closed vocabulary; None if hopeless."""
    v = (value or "").strip()
    if not v:
        return None
    if field in ("applicant_name", "registry_name"):
        # Per-token pool snap + capitalization normalization (truth names are
        # always Capitalized — census; lowercase reads were exact-match misses).
        return _snap_name(v)
    if field == "visa_class":
        return _weighted_closest(field, v)
    if field == "fee_status":
        # 688's shred-decapitated `naid` family (row 70): difflib ranked it
        # closer to `unpaid` than `paid` and the garbles outvoted the repaired
        # verbatim reads. The verbatim-only unpaid guard was removed on user
        # call (rows 68-69); the weighted metric is what replaced difflib.
        return _weighted_closest(field, v)
    # home_world and species_code return None when nothing is close, and
    # packet._repair_ocr_kv then deletes the field. That looks like a bug against
    # this module's own docstring, and it was measured as one: passing the value
    # through instead cost 0.08 dev points, because deleting an unrepairable OCR
    # read was quietly acting as a quality filter and letting a cleaner copy on
    # another document supply the value. With per-field source preference in
    # packet._preference the cost falls to 0.04, but it does not become a gain.
    #
    # The gain was supposed to come from unseen private-set values surviving. All
    # 1,000 train cases yield exactly 13 home worlds, 12 species and 10 purposes,
    # and these lists are those enumerations — a saturated sample, since a
    # fourteenth world would be expected ~77 times. The value universe is closed,
    # so there are no unseen values for passthrough to rescue. Deletion stays.
    if field == "species_code":
        return _weighted_closest(field, v)
    if field == "home_world":
        return _weighted_closest(field, v)
    if field == "declared_purpose":
        return _weighted_closest(field, v) or v
    if field == "sponsor_id":
        return grammar.coerce_sponsor_id(v)
    if field == "case_id":
        return grammar.coerce_case_id(v)
    if field == "arrival_date":
        return grammar.coerce_arrival_date(v)
    if field == "observed_flags":
        tokens = [t for t in re.split(r"[|,;\s]+", v.lower()) if t]
        # One flag matcher everywhere: the confusion-weighted, margin-guarded
        # token resolver signals._flags_in_line uses — not a parallel difflib
        # path with its own cutoff. "none" is not a flag (match_flag_token
        # excludes it), so it keeps a plain closeness test of its own.
        fixed = []
        for t in tokens:
            f = match_flag_token(t)
            if f:
                fixed.append(f)
            elif _closest(t, ["none"], 0.8):
                fixed.append("none")
        # Unreadable is not the same as clear. This used to fall through to
        # "none", turning scan debris into a positive assertion that no risk flag
        # was observed — MIB-000672's B-13 read `Observed fans: =-*` / `rant`
        # (truly `active_warrant`), was repaired to "none", and the case was
        # approved despite a truth of DENIED. Returning None deletes the field so
        # the missing-flag-evidence guard in policy can see there is nothing here.
        if not fixed:
            return None
        return "|".join(f for f in fixed if f != "none") or "none"
    return v


def clean_ocr_line(line):
    """Strip OCR junk: stray quotes/brackets at edges, unicode quotes, id-case fixes."""
    line = line.replace("‘", "").replace("’", "").replace("“", "").replace("”", "")
    line = re.sub(r"^[^A-Za-z0-9]+", "", line)
    line = re.sub(r"\bM[iI1l][bB]-", "MIB-", line)
    line = re.sub(r"\b[S5]PN[-–—]", "SPN-", line)
    return line.strip()


# OCR shape confusions: swapping one glyph for a look-alike is cheaper than a
# full edit, because that is the mistake the scanner actually makes. This is the
# letter-shape counterpart of grammar's id-cell digit fixes, expressed as edit costs so a
# corrupted flag word still resolves. Pairs are symmetric; cost < 1 (a real edit).
_CONFUSION_COST = 0.3
_OCR_SUB_COST = {}
for _x, _y in [
    ("o", "c"), ("o", "e"), ("o", "0"), ("o", "a"), ("c", "e"), ("a", "e"),
    ("z", "x"), ("z", "2"), ("r", "n"), ("r", "y"), ("n", "m"), ("u", "v"),
    ("h", "b"), ("h", "n"), ("b", "8"), ("b", "6"), ("g", "9"), ("g", "q"),
    ("l", "1"), ("l", "i"), ("i", "1"), ("i", "j"), ("s", "5"), ("s", "z"),
    ("t", "f"), ("t", "l"), ("d", "a"), ("d", "o"), ("e", "c"), ("v", "y"),
]:
    _OCR_SUB_COST[(_x, _y)] = _CONFUSION_COST
    _OCR_SUB_COST[(_y, _x)] = _CONFUSION_COST


def _sub_cost(x, y):
    if x == y:
        return 0.0
    return _OCR_SUB_COST.get((x, y), 1.0)


def _weighted_levenshtein(a, b):
    """Edit distance with substitution priced by OCR look-alike cost; ins/del = 1."""
    n = len(b)
    prev = [float(j) for j in range(n + 1)]
    for i in range(1, len(a) + 1):
        cur = [float(i)] + [0.0] * n
        ai = a[i - 1]
        for j in range(1, n + 1):
            cur[j] = min(prev[j] + 1.0,                       # delete a[i-1]
                         cur[j - 1] + 1.0,                    # insert b[j-1]
                         prev[j - 1] + _sub_cost(ai, b[j - 1]))  # substitute
        prev = cur
    return prev[n]


def _weighted_sim(a, b):
    if not a and not b:
        return 1.0
    return 1.0 - _weighted_levenshtein(a, b) / max(len(a), len(b))


# Only the risk flags — never "none", which is the absence of one.
_REAL_FLAGS = [f for f in FLAGS if f != "none"]


_VALUE_NORM_RE = re.compile(r"[^a-z0-9 ]+")


def match_flag_value(value):
    """Whole-value flag resolution: (best_flag, score, margin_over_runner_up).

    The value-level counterpart of `match_flag_token`, for `Observed flags:`
    values OCR shattered past any single token's reach (`Bagitie bematics`,
    `Beghie_ ju. ics`). Same confusion-weighted metric; normalization strips to
    lowercase alphanumerics+spaces (the geometry table in BACKGROUND §3 is
    computed exactly this way — keep them in sync). Returns scores, not a
    verdict: the emission bars live with the caller (mib.signals), because
    single-read and cross-variant-consensus acceptance use different ones.
    """
    v = _VALUE_NORM_RE.sub("", (value or "").lower()).strip()
    if len(v) < 4:
        return None, 0.0, 0.0
    return _best_scored((_weighted_sim(v, f.replace("_", " ")), f) for f in _REAL_FLAGS)


def match_flag_token(token, cutoff=0.7, margin=0.15):
    """Resolve one OCR-mangled token to a risk flag, or None.

    Confusion-weighted so `bichaxarc_yed` reaches `biohazard_red`, and
    margin-guarded so a benign flag-substring word cannot pose as a flag: an
    edit-distance metric normalized by the longer string already scores
    `biometrics` vs `illegible_biometrics` at ~0.5 (a whole inserted prefix is
    not an OCR confusion), and requiring the winner to beat the runner-up by
    `margin` closes the rest of the gap.
    """
    t = (token or "").strip().lower()
    if len(t) < 4:
        return None
    best_f, best_s, gap = _best_scored((_weighted_sim(t, f), f) for f in _REAL_FLAGS)
    if best_s >= cutoff and gap >= margin:
        return best_f
    return None
