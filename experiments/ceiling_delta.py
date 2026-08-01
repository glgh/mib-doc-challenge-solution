import sys, re, json, tempfile, difflib
from pathlib import Path
import numpy as np
from PIL import Image
ROOT = Path(__file__).resolve().parent.parent; CH = ROOT.parent/"mib-doc-challenge"
sys.path.insert(0, str(ROOT))
import fitz
from mib import cache, imaging, records
from mib.stages import render
GEOM={"turn1","turn3","skew","deshred","local"}; OPT=render._OPTICAL_MODULES
FIELDS=["applicant_name","species_code","home_world","visa_class","sponsor_id","arrival_date","declared_purpose","fee_status"]

def norm(s): return re.sub(r'[^a-z0-9]','',str(s).lower())
def reachable(value, lines):
    v=norm(value)
    if len(v)<3: return False
    for L in lines:
        n=norm(L)
        if v in n: return True
        if len(n)>=len(v):
            step=max(1,(len(n)-len(v))//12+1)
            for i in range(0,len(n)-len(v)+1,step):
                if difflib.SequenceMatcher(None,v,n[i:i+len(v)]).ratio()>=0.86: return True
    return False

def restored(cid,pno,variant):
    variant=variant.split("+psm")[0]
    toks=variant.split("+"); rest=toks[1:] if toks[0] in ("render","embedded") else toks
    opt=rest[-1] if rest and rest[-1] in OPT else None
    gc=tuple(t for t in rest if t!=opt); q=3 if "turn3" in gc else 1 if "turn1" in gc else 0
    with tempfile.TemporaryDirectory() as tmp:
        doc=fitz.open(CH/f"data/train/{cid}.pdf"); page=doc[pno]
        srcs=list(render._sources(doc,page,tmp))
        gray=next((g for n,e,g in srcs if n=="render"), srcs[0][2])
        sk=imaging.orientation_profile(gray)[q]["skew_deg"]; img=None
        for ch,im in render._orientation_chains(gray,q,sk,GEOM):
            if ch==gc: img=im; break
        if img is None:
            img=imaging.turn(gray,q) if q else gray
            if q: img=imaging.rotate(img,sk)
        if opt: img=OPT[opt](img)
        return np.asarray(img).astype(np.uint8)

def ocr(a,psm):
    with tempfile.TemporaryDirectory() as tmp:
        p=Path(tmp)/"f.png"; p.write_bytes(imaging.to_pnm_bytes(a))
        lines,_=render._recognize(p,psm=psm)
    return [l for l in lines if l.strip()]

dev=set(json.loads((ROOT/"data_splits.json").read_text())["dev"])
truth={r["case_id"]:r for r in __import__("csv").DictReader(open(CH/"data/train_labels.csv"))}
pred={json.loads(l)["case_id"]:json.loads(l) for l in open(ROOT/"output/replay_rebaseline/predictions.jsonl")}
meta,recs=cache.read(ROOT/"output/cache/train_grid.jsonl")
byid={r["stem"]:r for r in recs}

sample=sorted(dev)[::20]   # ~35 systematic
print(f"sample: {len(sample)} dev cases"); 
tot=dict(wrong=0, sel=0, config=0, ceiling=0); by_field={}
for i,cid in enumerate(sample):
    if cid not in byid or byid[cid].get("error"): continue
    pages,reads=cache.to_case(byid[cid]["pages"])
    cur_lines=[l for rl in reads.values() for r in rl for l in (r.lines or [])]
    swp_lines=[]
    for pno,rl in reads.items():
        best=records.best_read(rl)
        if not best: continue
        try: frame=restored(cid,pno,best.variant)
        except Exception: continue
        for psm in (3,4,6): swp_lines+=ocr(frame,psm)
        im=Image.fromarray(frame); up=np.asarray(im.resize((im.width*2,im.height*2),Image.LANCZOS))
        swp_lines+=ocr(up,6)
    T,P=truth[cid],pred.get(cid,{})
    for f in FIELDS:
        tv=T.get(f,"")
        if not tv or tv.lower()=="none": continue
        if norm(P.get(f,""))==norm(tv): continue   # already correct
        tot["wrong"]+=1; by_field.setdefault(f,[0,0,0])
        cur=reachable(tv,cur_lines); swp=reachable(tv,cur_lines+swp_lines)
        if cur: tot["sel"]+=1; by_field[f][0]+=1
        elif swp: tot["config"]+=1; by_field[f][1]+=1
        else: tot["ceiling"]+=1; by_field[f][2]+=1
    print(f"  [{i+1}/{len(sample)}] {cid} done", flush=True)

print("\n===== ACHIEVABLE - CURRENT delta =====")
print(f"currently-wrong fields: {tot['wrong']}")
print(f"  (a) SELECTION headroom  (truth already in current reads, mis-picked): {tot['sel']}")
print(f"  (b) CONFIG headroom     (truth NEW under PSM sweep, current missed):  {tot['config']}")
print(f"  (c) CEILING/absent      (unreachable even under sweep):               {tot['ceiling']}")
print("\nby field  [sel / config / ceiling]:")
for f,v in sorted(by_field.items(), key=lambda x:-sum(x[1])): print(f"  {f:16} {v}")
