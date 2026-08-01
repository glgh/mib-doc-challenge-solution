"""Case assembly: active case id, document census, precedence-ordered field merge. The display-only value-first fallbacks (fee/closed-vocab/visa/sponsor) live in mib/fallbacks.py."""
import difflib
import re
from collections import Counter, namedtuple
from dataclasses import dataclass, field

from . import parse, textmatch, vocab
from .adversarial import INJECTION_RE
from .parse import DOC_ADJUDICATOR, DOC_BIOMETRIC, DOC_REGISTRY, DOC_SPONSOR
from .records import Candidate, best_read

SRC_TEXT = 0  # clean digital text layer
SRC_OCR = 1   # OCR of scan pixels — same doc type ranks below its text-layer peer

# An injection-shaped hidden line whose decision payload claims APPROVED (the
# `answer key ... ,APPROVED,0.99` shape). Its PRESENCE caps an unforced approval
# at NEEDS_REVIEW (policy.adjudicate); its content is never trusted or sourced.
# Matched on the already-lowercased, INJECTION_RE-gated `hidden_norm` set, so it
# inherits the blinding-attack guard (only injection-shaped lines qualify).
_INJECTED_APPROVAL_RE = re.compile(r"\bapproved\b\W{0,3}0\.\d\d")


@dataclass
class Packet:
    case_id: str = ""
    docs: list = field(default_factory=list)  # [(doc_type, source, kv)] sorted by trust
    variant_docs: list = field(default_factory=list)  # [(doc_type, kv)] from losing OCR variants
    scan_only_pages: int = 0                  # pages with an image but ~no visible text
    injected_approval: bool = False           # an injection-shaped hidden line claims APPROVED

    def doc(self, dtype):
        return next((kv for d, _, kv in self.docs if d == dtype), {})

    def has_doc(self, dtype):
        return any(d == dtype for d, _, _ in self.docs)

    @property
    def biometric(self):
        return self.doc(DOC_BIOMETRIC)

    @property
    def sponsor_letter(self):
        return self.doc(DOC_SPONSOR)

    @property
    def registry(self):
        return self.doc(DOC_REGISTRY)

    @property
    def adjudicator(self):
        return self.doc(DOC_ADJUDICATOR) or None

    @property
    def ocr_kvs(self):
        """Every OCR kv the ensemble read: losing variants + OCR'd docs (not text-layer)."""
        return ([kv for _dt, kv in self.variant_docs]
                + [kv for _dt, src, kv in self.docs if src == SRC_OCR])


def _repair_ocr_kv(kv):
    """Snap OCR-read values toward closed vocabularies; drop hopeless id fields."""
    for fname in list(kv):
        if fname.startswith("_"):
            continue
        snapped = vocab.snap(fname, kv[fname])
        if snapped:
            kv[fname] = snapped
        elif fname in ("sponsor_id", "case_id", "arrival_date", "visa_class",
                       "fee_status", "species_code", "home_world",
                       "observed_flags"):
            # Garbage id/enum: absent beats wrong for policy safety. observed_flags
            # belongs here for a sharper reason — left in place, an unreadable risk
            # line still counts as flag evidence, so a packet whose B-13 OCR'd to
            # debris looks like a slip that reported nothing.
            del kv[fname]
    return kv


def _parse_lines(lines, ocr):
    """One reading -> kv, the single parse path for docs and variants alike.

    Prose fills only what the labelled lines did not: an explicit
    `Purpose: research` on the same document outranks a sentence.
    """
    kv = parse.parse_kv(lines)
    for fname, value in parse.parse_prose(lines).items():
        kv.setdefault(fname, value)
    # OCR registry pages get the eroded-label recovery: label tails and bare
    # values that faint scans leave behind (text layers never erode, so the
    # fallback stays off them). Same snap/validation path as labelled reads.
    if ocr and parse.detect_doc_type(lines) == parse.DOC_REGISTRY:
        for fname, value in parse.registry_fallback_kv(lines).items():
            kv.setdefault(fname, value)
    return _repair_ocr_kv(kv) if ocr else kv


# "Key: Value" on one line, so an inline strike ("Fee Status: unpaid") matches by
# its value part as well as the whole line.
_INLINE_KV_RE = re.compile(r"^[A-Za-z0-9][A-Za-z _0-9]{1,28}?\s*[:.;]\s*(.+)$")


def _void_struck(kv, struck):
    """Drop any field whose value the document crossed out (red strikethrough).

    `struck` is the joined text of struck value cells for this page (from
    stages.extract). A struck value is the document voiding its own printed value
    — not sourceable evidence, like a hidden span or a damage marker — so it must
    not source a field; the true value comes from another document, or the field
    degrades to unknown. Match is normalized equality (never substring: a struck
    `unpaid` must not void a `paid` elsewhere on the page), plus the value part of
    an inline `Key: Value` strike. `_raw` is left intact, so flag scanning and
    manual-correction reading are unaffected.
    """
    if not struck:
        return kv
    voided = {textmatch.normalize(s) for s in struck}
    for s in struck:
        m = _INLINE_KV_RE.match(s)
        if m:
            voided.add(textmatch.normalize(m.group(1)))
    for fname in list(kv):
        if not fname.startswith("_") and textmatch.normalize(kv[fname]) in voided:
            del kv[fname]
    return kv


def _decoy(lines, case_id, ocr):
    """Does this reading belong to a different applicant?

    An OCR page whose ID is one glyph off the active case is the applicant's
    own page misread, not a decoy. Text-layer pages get no such tolerance:
    text layers don't misread, so a near-miss ID there is a genuine decoy
    (sequential case ids make those cheap to plant).
    """
    ids = set(parse.page_case_ids(lines))
    if not ids or case_id in ids:
        return False
    return not ocr or not any(textmatch.plausible_misread(case_id, i) for i in ids)


def _without_hidden_echo(lines, hidden_norm):
    """Drop OCR lines that replicate the packet's own quarantined hidden text.

    S1 separates hidden spans (white ink, off-crop) from visible lines, but an
    injection printed white-on-white re-enters through PIXELS when an optical
    variant makes it OCR-readable (MIB-000114 p2: render+autocon reads the full
    answer key off the page). The quarantine hands us the exact planted
    strings, so an OCR line matching one is the same plant seen through a
    different channel — dropped here, before parse, doc typing, and every
    raw-line scan, instead of trusting the comma-payload shape to defeat
    parse_kv structurally. Value-first tiers keep their INJECTION_RE guard
    for injection shapes this per-packet set cannot know.
    """
    if not hidden_norm:
        return lines
    kept = []
    for line in lines:
        ln = " ".join(line.lower().split())
        if len(ln) >= 15 and any(
                ln in h or h in ln
                or difflib.SequenceMatcher(None, ln, h).ratio() >= 0.85
                for h in hidden_norm):
            continue
        kept.append(line)
    return kept


def assemble(pages, reads_by_page, fallback_case_id):
    """Build a Packet from Page records; pages of other applicants are dropped.

    `reads_by_page` maps page_no -> every OCR reading S2 produced. The primary
    reading (`records.best_read`) plays the role the single chosen reading used
    to: it defines the page's document. The losing readings survive as
    `variant_docs` — parsed, decoy-filtered kvs the per-field merge and the flag
    scan consult, because a losing variant can still hold the best copy of one
    field or the only legible risk flag.
    """
    primary = {}
    for pt in pages:
        best = best_read(reads_by_page.get(pt.page_no) or [])
        if best is not None:
            primary[pt.page_no] = best

    id_votes = Counter()
    for pt in pages:
        id_votes.update(parse.page_case_ids(pt.visible_lines))
        best = primary.get(pt.page_no)
        id_votes.update(parse.page_case_ids(best.lines if best else []))
    case_id = id_votes.most_common(1)[0][0] if id_votes else fallback_case_id

    # Only injection-SHAPED hidden lines poison the echo set. Unconditional
    # echo-matching would hand injectors a blinding attack: mirror the page's
    # true visible text in white ink and the filter suppresses the real
    # evidence. A hidden line that merely duplicates legitimate content
    # (`Observed flags: ...`) therefore never enters the set; answer keys and
    # SYSTEM prompts do.
    hidden_norm = [" ".join(l.lower().split())
                   for pt in pages for l in pt.hidden_lines
                   if INJECTION_RE.search(l)]
    hidden_norm = [h for h in hidden_norm if len(h) >= 20]

    packet = Packet(case_id=case_id)
    packet.injected_approval = any(_INJECTED_APPROVAL_RE.search(h) for h in hidden_norm)
    for pt in pages:
        lines, source, conf = (pt.visible_lines, SRC_TEXT, None)
        if pt.is_scan_only:
            packet.scan_only_pages += 1
            best = primary.get(pt.page_no)
            if best is not None and best.lines:
                lines, source, conf = (best.lines, SRC_OCR, best.conf)
            for r in reads_by_page.get(pt.page_no) or []:
                if r.lines is lines or not r.lines or _decoy(r.lines, case_id, ocr=True):
                    continue
                r_lines = _without_hidden_echo(r.lines, hidden_norm)
                if not r_lines:
                    continue
                kv = _parse_lines(r_lines, ocr=True)
                kv["_raw"] = r_lines
                kv["_page_no"] = pt.page_no
                kv["_conf"] = r.conf
                packet.variant_docs.append((parse.detect_doc_type(r_lines), kv))
        if source == SRC_OCR:
            lines = _without_hidden_echo(lines, hidden_norm)
        if _decoy(lines, case_id, ocr=(source == SRC_OCR)):
            continue  # decoy page for a different applicant
        kv = _parse_lines(lines, ocr=(source == SRC_OCR))
        _void_struck(kv, pt.struck)   # a value the page crossed out is not evidence
        kv["_raw"] = lines
        kv["_page_no"] = pt.page_no
        kv["_conf"] = conf
        packet.docs.append((parse.detect_doc_type(lines), source, kv))
    packet.docs.sort(key=lambda t: (t[0], t[1]))
    return packet


# "Manual correction: <field> is <value>." — signed-manual-note evidence, the
# manual's highest precedence tier. Train census: 136 packets carry one, and
# every parseable correction matches the truth label (59/59 checked).
_CORRECTION_RE = re.compile(
    r"Manual correction[:.]?\s*"
    r"(fee status|visa class|sponsor(?: id)?|applicant(?: name)?|arrival date|"
    r"home world|species code|declared purpose)\s+is\s+(.+?)\.?\s*$",
    re.IGNORECASE)

_CORRECTION_FIELDS = {
    "fee status": "fee_status", "visa class": "visa_class",
    "sponsor": "sponsor_id", "sponsor id": "sponsor_id",
    "applicant": "applicant_name", "applicant name": "applicant_name",
    "arrival date": "arrival_date", "home world": "home_world",
    "species code": "species_code", "declared purpose": "declared_purpose",
}


def manual_corrections(packet):
    """field -> corrected value, from Manual correction lines on any document."""
    corrections = {}
    for _dtype, source, kv in packet.docs:
        for line in kv.get("_raw", []):
            m = _CORRECTION_RE.search(line)
            if not m:
                continue
            fname = _CORRECTION_FIELDS[m.group(1).lower()]
            value = m.group(2).strip()
            if source == SRC_OCR and not parse.valid_value(fname, value):
                value = vocab.snap(fname, value) or value
            if parse.valid_value(fname, value) or fname == "applicant_name":
                corrections.setdefault(fname, value)
    return corrections


def candidates(packet):
    """Every value seen for every schema field, in trust order.

    `packet.docs` is already sorted by (doc_type, source), so generation order is
    trust order and the winner is simply the first valid entry per field. The
    point of materializing the losers is that today they are unrecoverable: the
    sort ranks whole *documents*, so an OCR'd high-trust document outranks a
    clean text-layer lower-trust one for every field at once, and 37 dev
    field-instances are lost that way — a correct `Miravoss` on one page losing
    to a garbled `Mirayoss` on another. Preferring per field needs the
    alternatives to still exist at this seam.
    """
    out = []
    for dtype, source, kv in packet.docs:
        page_no = kv.get("_page_no", 0)
        for fname in parse.FIELDS:
            raws = [kv.get(fname)]
            # The registry extract names the applicant under its own key.
            if fname == "applicant_name" and dtype == DOC_REGISTRY:
                raws.append(kv.get("registry_name"))
            for raw in raws:
                if not raw:
                    continue
                out.append(Candidate(
                    field_name=fname, value=raw.strip(),
                    doc_type=dtype, source=source, page_no=page_no,
                    valid=parse.valid_value(fname, raw)))
    return out


def _preference(cand):
    """Sort key deciding which candidate for a field wins: source (clean text
    beats OCR), then field-manual trust order. Source IS the read-quality signal
    at this seam; a numeric per-candidate quality was carried alongside it for a
    while, never consulted, and removed 2026-08-01.

    `packet.docs` is ordered (doc_type, source), which ranks whole *documents* and
    therefore lets an OCR'd high-trust document win every field at once over a
    clean text-layer copy elsewhere — a correct `Miravoss` losing to a garbled
    `Mirayoss`. Preferring the clean read first and settling ties by field-manual
    trust order is worth +0.23 dev (38.76 -> 39.00 extraction, CFA unchanged).

    This does not contradict the manual's evidence precedence. That order ranks
    *kinds of evidence*, and both copies here are the same kind — one is simply a
    better reading of it. Trust order still decides between different documents,
    which is what the tie-break does.
    """
    return (cand.source, cand.doc_type)


VOTE_DOC = 99  # provenance doc_type marking a value settled by the variant vote

# Stroke merges the single-char confusion table cannot price: tesseract reads
# `rn` as `m` and `ri` as `n` (adjacent strokes fusing), and it does so MORE
# confidently than the true glyphs, so engine conf must not arbitrate between
# the two forms. This mechanism survives its own ablation at corpus scale
# (row 49): with the merge disabled on the schema-4 substrate, per-line conf
# alone regresses 7 names / recovers 0 — the merge is load-bearing, not a
# case patch. Positions are census-bound (train labels): `rn`>`m` anywhere;
# `ri`>`n` token-final only (inner `ri`/`n` are both common in real names —
# an inner merge conflated `Miradane` with the hallucinated `Miradarie`,
# row 45). No two distinct truth names collide under this map, so merging
# cannot conflate two real applicants; the expanded form is the
# representative (the merge direction never runs backward in this corpus).
_NAME_COLLAPSES = ((re.compile("rn"), "m"), (re.compile(r"ri\b"), "n"))


def _edge_strip(normalized):
    """Tokens with edge punctuation removed; inner structure (id hyphens, date
    dashes) untouched. `Zazam_` -> `zazam`, `spn-1234.` -> `spn-1234`."""
    toks = (re.sub(r"^[^a-z0-9]+|[^a-z0-9]+$", "", t) for t in normalized.split())
    return " ".join(t for t in toks if t)


def _vote_key(value):
    """Agreement key for the variant vote: edge punctuation is not disagreement
    (`Zazam_`, `Zazam.` and `Zazam` are one reading with debris)."""
    return _edge_strip(textmatch.normalize(value))


def _collapse(key):
    """Stroke-collapsed form of a name key: `Xanzarn`/`Xanzam` are one name
    read twice, not a 1-1 tie. Applied AFTER truncation pooling — a truncated
    `nextar` can only reach its expansion `nextari` pre-collapse (the collapse
    rewrites `nextari` to `nextan`, which no truncation is a prefix of)."""
    for expanded, merged in _NAME_COLLAPSES:
        key = expanded.sub(merged, key)
    return key


def _line_conf(kv, value):
    """The engine's confidence in the tsv line that carries `value`, or None.

    Matched by alnum substring against the conf entries' line text (schema 4);
    schema-3 caches have no text and yield None, so everything downstream falls
    back to first-seen order exactly as before.
    """
    want = re.sub(r"[^a-z0-9]", "", str(value).lower())
    if not want:
        return None
    best = None
    for entry in kv.get("_conf") or []:
        if want in re.sub(r"[^a-z0-9]", "", entry[3].lower()):
            best = entry[0] if best is None else max(best, entry[0])
    return best


# Truncation POOLING (merging `qormora nextar` into `qormora nextari` by
# token-wise prefix) was measured and REJECTED here (row 47): it recovered
# MIB-000665 but consolidation strengthens whichever name family FRAGMENTS
# more — on MIB-000250 the conflicting name's six reads splintered into four
# keys, pooled into a bloc, and outvoted the truth's three consistent reads.
# Cross-family balance is a page-level question; revisit only with a per-page
# two-level vote.


# One OCR reading's contribution to a field vote — grouped by `_vote_key`, then
# ranked. A namedtuple so the five fields are read by name, not by position.
_Vote = namedtuple("_Vote", "value clean line_conf seq page_no")


def _variant_vote(field_name, kvs):
    """Plurality over valid values for one field across OCR readings, grouped
    by `_vote_key` so debris and stroke-merge variants pool their votes instead
    of splitting them (the 1-1 "ties" of row 42 were mostly one reading
    fragmented by punctuation).

    Genuine ties break by per-line engine confidence (`_line_conf`), then
    first-seen generation order. A PAGE-level conf weight was tried and
    rejected (row 43): whole-read mass is uncorrelated with one line's
    correctness — the line the value sits on is the right grain. A steer-ties-
    away-from-revoked-sponsors guard was tried in the same batch and also
    reverted: MIB-000130's `SPN-4040` is genuinely revoked (truth DENIED) and
    the guard handed the case to its misread neighbor `SPN-4080` — on a tie,
    the revoked reading is the deny-safe direction, not the hazard (the hazard
    is *repair* translating digits toward the list, guarded in vocab.snap).

    Returns (representative raw value, agreement count) or (None, 0).
    """
    groups = {}   # vote key -> [_Vote]
    for seq, kv in enumerate(kvs):
        v = kv.get(field_name)
        if not v or not parse.valid_value(field_name, v):
            continue
        # An INNER colon in a voted value means the parse mis-keyed a line —
        # the value swallowed another field's label (`Home World: Europa
        # Station` winning the *name* vote). Schema-grounded, not case-bound:
        # ZERO of the 12,000 train truth values contain a colon (census, row
        # 49). A trailing colon is mere edge debris (`Solul Qorzarn:`) — the
        # edge-strip key already absorbs it, and dropping those reads
        # reshuffled first-seen ties into regressions.
        if re.search(r":\s*\S", v):
            continue
        key = _vote_key(v)
        if not key:
            continue
        norm = textmatch.normalize(v)
        clean = norm == _edge_strip(norm)   # stripping removed nothing: no debris
        groups.setdefault(key, []).append(
            _Vote(v, clean, _line_conf(kv, v), seq, kv.get("_page_no")))
    if field_name == "applicant_name":
        collapsed = {}
        for key, entries in groups.items():
            collapsed.setdefault(_collapse(key), []).extend(entries)
        groups = collapsed
    if not groups:
        return None, 0

    def group_rank(entries):
        # For closed-vocabulary fields, in-vocabulary groups outrank everything:
        # the value universe is closed and saturated (13 worlds / 12 species /
        # 10 purposes — the passthrough post-mortem), so an unsnappable group is
        # never the truth. Without this, MIB-000134's systematic truncation
        # `fiel` (reproduced on TWO pages — a cross-page OCR failure mode, not
        # agreement) outranked the one-page in-vocabulary `field repair`.
        snappable = True
        if field_name in ("declared_purpose", "species_code", "home_world",
                          "visa_class", "fee_status"):
            rep = entries[0].value
            # purpose's snap passes unmatched values through (free-text form),
            # so the STRICT membership check is repairable_purpose.
            snappable = (vocab.repairable_purpose(rep)
                         if field_name == "declared_purpose"
                         else vocab.snap(field_name, rep) is not None)
        # Distinct contributing PAGES outrank raw read count: one conflicting
        # page's fan-out of fragmented reads pooled into a bloc and outvoted
        # the truth's consistent reads (rows 47/50/52 — one extra read of that
        # page kept tipping votes, and the grid multiplies per-page variants).
        # Cross-page agreement is what fan-out cannot fake; within a page-count
        # tie, read count still lets a page's plurality beat single junk reads.
        pages = len({e.page_no for e in entries})
        best_conf = max((e.line_conf for e in entries if e.line_conf is not None),
                        default=-1.0)
        first_seen = min(e.seq for e in entries)
        return (not snappable, -pages, -len(entries), -best_conf, first_seen)

    entries = min(groups.values(), key=group_rank)

    def rep_rank(e):
        # Cleanest first (no edge debris), then the expanded stroke-merge form
        # (longer), then the engine's own preference, then generation order.
        return (not e.clean, -len(textmatch.normalize(e.value)),
                -(e.line_conf if e.line_conf is not None else -1.0), e.seq)

    return min(entries, key=rep_rank).value, len(entries)


def _name_families(packet):
    """Every reading of applicant_name pooled by collapsed vote key, with the
    breadth of its support: which distinct documents assert it (text or OCR
    doc-level parse), on which pages, and how many ensemble reads agree."""
    fams = {}

    def fam(key):
        return fams.setdefault(key, {"docs": set(), "pages": set(), "reads": 0,
                                     "cands": []})

    for c in candidates(packet):
        if c.field_name != "applicant_name" or not c.valid:
            continue
        f = fam(_collapse(_vote_key(c.value)))
        f["docs"].add(c.doc_type)
        f["pages"].add(c.page_no)
        f["cands"].append(c)
    for kv in packet.ocr_kvs:
        v = kv.get("applicant_name")
        if v and parse.valid_value("applicant_name", v):
            f = fam(_collapse(_vote_key(v)))
            f["pages"].add(kv.get("_page_no"))
            f["reads"] += 1
    return fams


def _name_corroboration(packet, incumbent):
    """Multi-document corroboration challenge for applicant_name.

    Identity-conflict packets carry one document whose NAME CELL names a
    different being while its other fields stay true to the active case (row
    67 falsified the older "planted decoy document" reading: the conflicting
    names own no other train case, and the conflicting form matches the
    active truth 6/6 elsewhere — so only the name may be replaced, never the
    document's other fields). The active case id is stamped on every page, so
    the id cannot attribute; `_preference`'s single-document winner is
    sometimes the conflicting name while sponsor letter + registry + a
    near-unanimous OCR vote agree on the labeled applicant (MIB-000081: two
    text layers and 13/13 reads lose to one intake text layer). The measured
    rule (grid counterfactual, FIXED 6 / BROKE 0 dev): a challenger family
    must be asserted by at least TWO distinct documents and strictly more
    documents than the incumbent's family. Weaker clauses (OCR read/page
    breadth, one doc + dominant vote, 1v1 with a zero-ink incumbent) all
    measured net-negative (rows 63/68) — the generator plants conflicts in
    BOTH directions (authored digital form vs genuine foreign scan, 930/402),
    so 1v1 conflicts are structurally ambiguous and the two-doc bar is the
    decidability frontier.

    Returns (value, (doc_type, source)) or None if no challenger qualifies.
    """
    fams = _name_families(packet)
    inc_key = _collapse(_vote_key(incumbent or ""))
    inc_docs = len(fams.get(inc_key, {"docs": ()})["docs"])
    best = None
    for key, f in fams.items():
        if key == inc_key or len(f["docs"]) < 2 or len(f["docs"]) <= inc_docs:
            continue
        rank = (len(f["docs"]), len(f["pages"]), f["reads"])
        if best is None or rank > best[0]:
            best = (rank, f)
    if best is None:
        return None
    winner = min(best[1]["cands"], key=_preference)
    return winner.value, (winner.doc_type, winner.source)


def merge_fields(packet, provenance=None):
    """Best value per schema field: documents in trust order, then a plurality
    vote across the whole OCR ensemble for any field the text layer did not
    supply, then the name-corroboration challenge; a signed manual correction
    overrides everything.

    The vote is why `variant_docs` exists: a reading `best_read` rejected for
    the page can still hold the best copy of one field, and agreement across
    independently-restored variants is strong evidence for an OCR value. Clean
    text-layer values are never outvoted — text layers don't misread — but a
    text-layer name CAN lose the corroboration challenge to a name asserted by
    strictly more documents (multi-applicant packets, `_name_corroboration`).

    If `provenance` is a dict it is filled with fname -> (doc_type, source);
    vote-settled fields carry doc_type VOTE_DOC.
    """
    prov = {} if provenance is None else provenance
    values = {}
    for cand in sorted(candidates(packet), key=_preference):
        if not cand.valid or cand.field_name in values:
            continue
        values[cand.field_name] = cand.value
        prov[cand.field_name] = (cand.doc_type, cand.source)
    ocr_kvs = packet.ocr_kvs
    if ocr_kvs:
        for fname in parse.FIELDS:
            if prov.get(fname, (0, SRC_OCR))[1] == SRC_TEXT:
                continue  # clean text-layer value stays
            value, _agree = _variant_vote(fname, ocr_kvs)
            if value:
                values[fname] = value.strip()
                prov[fname] = (VOTE_DOC, SRC_OCR)
    challenged = _name_corroboration(packet, values.get("applicant_name"))
    if challenged is not None:
        values["applicant_name"] = challenged[0].strip()
        prov["applicant_name"] = challenged[1]
    # Signed manual notes are rank-0 evidence: applied last, they override the
    # doc merge, the vote, and the corroboration challenge alike.
    for fname, value in manual_corrections(packet).items():
        values[fname] = value
        prov[fname] = (0, 0)
    return values
