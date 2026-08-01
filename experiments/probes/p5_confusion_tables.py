#!/usr/bin/env python3
"""P5 (light) — are the two OCR-confusion tables actually unifiable/groundable?
Compare vocab._OCR_SUB_COST (text fuzzy-match) vs grammar._DIGIT_CELL_FIXES
(digit-cell translate) vs the measured ID-cell confusions."""
import os, sys
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)
from mib import vocab, grammar

sub = {k for k in vocab._OCR_SUB_COST}                      # symmetric letter/near pairs
sub_pairs = {tuple(sorted(k)) for k in sub}
digit_fixes = dict(grammar._DIGIT_CELL_FIXES)              # glyph -> digit

print("vocab._OCR_SUB_COST — unordered pairs (flat cost 0.3), text fuzzy-match:")
print("  ", sorted(sub_pairs))
print(f"  count: {len(sub_pairs)} pairs\n")
print("grammar._DIGIT_CELL_FIXES — glyph -> digit (hard translate, id cells):")
print("  ", digit_fixes, "\n")

# domain overlap: how many _OCR_SUB_COST pairs involve a digit vs pure-letter?
letter_only = [p for p in sub_pairs if all(c.isalpha() for c in p)]
with_digit = [p for p in sub_pairs if any(c.isdigit() for c in p)]
print(f"_OCR_SUB_COST is {len(letter_only)} letter-letter pairs + {len(with_digit)} letter-digit:")
print("  letter-letter:", sorted(letter_only))
print("  letter-digit :", sorted(with_digit))

# do the two tables agree where they touch digits?
shared = {p for p in with_digit}
print("\noverlap with _DIGIT_CELL_FIXES (glyph->digit):")
for a, b in sorted(with_digit):
    letter, digit = (a, b) if a.isalpha() else (b, a)
    inmap = digit_fixes.get(letter) or digit_fixes.get(letter.upper())
    print(f"  {letter}<->{digit}: _DIGIT_CELL_FIXES says {letter}->{inmap!r}  "
          f"{'AGREE' if inmap == digit else 'differ/absent'}")

print("\n-- verdict --")
print("The two tables are largely DIFFERENT DOMAINS: _OCR_SUB_COST is dominated by")
print("letter<->letter confusions (o/c, o/e, t/f, d/o, e/c, v/y) that never occur in a")
print("digit id cell, while _DIGIT_CELL_FIXES is glyph->digit for the id cells. They")
print("share only the handful of letter<->digit lookalikes (o/0, s/5, l/1, i/1).")
print("=> Not duplicate tables to merge. A single *grounded* matrix would need mining")
print("   the closed-vocab fields' letter confusions (species/world/name aligned to")
print("   truth) — a separate job the ID-cell matrix does not supply. The unify win is")
print("   smaller than hoped: at most (a) dedup the shared letter<->digit lookalikes,")
print("   (b) replace _OCR_SUB_COST's flat 0.3 with measured costs IF vocab-field")
print("   confusions are mined. Code-health only, ~0 score.")
