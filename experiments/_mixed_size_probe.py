"""Size the mixed-page channel: how often does a packet carry pages of a second
page size, and does that coincide with a truth risk_flag we miss? (row-67 residue,
TODO 6.12.) Usage: _mixed_size_probe.py [replay_dir]"""
import csv, json, sys, fitz
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent
CH=(ROOT.parent/"mib-doc-challenge").resolve()
RD=Path(sys.argv[1]) if len(sys.argv)>1 else ROOT/"output/replay_vf_c"
truth={r["case_id"]:r for r in csv.DictReader(open(CH/"data/train_labels.csv"))}
pred={json.loads(l)["case_id"]:json.loads(l) for l in open(RD/"predictions.jsonl")}
idx={}
for p in (CH/"data/train").rglob("MIB-*.pdf"):
    cid=p.name[:10]
    idx.setdefault(cid,p)
mixed_cases=0; total_mixed_pages=0
buckets={"mixed_truthflag_wemiss":0,"mixed_truthflag_weget":0,"mixed_noflag":0}
for cid,t in truth.items():
    p=idx.get(cid)
    if not p: continue
    doc=fitz.open(p); mp=0
    for pg in doc:
        nlines=len([l for l in pg.get_text("text").splitlines() if l.strip()])
        if len(pg.get_images())>0 and nlines>3: mp+=1
    doc.close()
    if mp:
        mixed_cases+=1; total_mixed_pages+=mp
        tflag=(t["risk_flags"] or "none")!="none"
        pflag=(pred[cid].get("risk_flags") or "none")!="none"
        if tflag and not pflag: buckets["mixed_truthflag_wemiss"]+=1
        elif tflag and pflag: buckets["mixed_truthflag_weget"]+=1
        else: buckets["mixed_noflag"]+=1
print(f"cases with >=1 mixed page (image + >3 text lines): {mixed_cases}/1000")
print(f"total mixed pages: {total_mixed_pages}")
print("among mixed-page cases:", json.dumps(buckets))
