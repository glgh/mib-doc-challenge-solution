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
def ocr(a,psm):
    with tempfile.TemporaryDirectory() as tmp:
        p=Path(tmp)/"f.png"; p.write_bytes(imaging.to_pnm_bytes(a.astype(np.uint8)))
        lines,_=render._recognize(p,psm=psm)
    return [l for l in lines if l.strip()]

def qof(variant):
    v=variant.split("+psm")[0]
    return 3 if "turn3" in v else 1 if "turn1" in v else 0

def restored(cid,pno,variant):   # probe-1 winning frame (for PSM sweep)
    variant=variant.split("+psm")[0]; toks=variant.split("+")
    rest=toks[1:] if toks[0] in ("render","embedded") else toks
    opt=rest[-1] if rest and rest[-1] in OPT else None
    gc=tuple(t for t in rest if t!=opt); q=qof(variant)
    with tempfile.TemporaryDirectory() as tmp:
        doc=fitz.open(CH/f"data/train/{cid}.pdf"); page=doc[pno]
        srcs=list(render._sources(doc,page))
        gray=next((g for n,e,g in srcs if n=="render"), srcs[0][2])
        sk=imaging.orientation_profile(gray)[q]["skew_deg"]; img=None
        for ch,im in render._orientation_chains(gray,q,sk,GEOM):
            if ch==gc: img=im; break
        if img is None:
            img=imaging.turn(gray,q) if q else gray
            if q: img=imaging.rotate(img,sk)
        if opt: img=OPT[opt](img)
        return np.asarray(img).astype(np.uint8)

def hidpi(cid,pno,q):   # native high-DPI render + turn/skew + field-block crop
    doc=fitz.open(CH/f"data/train/{cid}.pdf"); page=doc[pno]
    pix=page.get_pixmap(matrix=fitz.Matrix(5,5), colorspace=fitz.csGRAY)
    gray=np.frombuffer(pix.samples,dtype=np.uint8).reshape(pix.height,pix.width)
    sk=imaging.orientation_profile(gray)[q]["skew_deg"]
    if q: gray=imaging.turn(gray,q)
    if abs(sk)>=imaging.MIN_SKEW: gray=imaging.rotate(gray,sk)
    h,w=gray.shape; crop=gray[:int(0.48*h),:]        # top field band, post-orient
    return ocr(crop,6)+ocr(crop,11)+ocr(gray,6)

dev=set(json.loads((ROOT/"data_splits.json").read_text())["dev"])
truth={r["case_id"]:r for r in __import__("csv").DictReader(open(CH/"data/train_labels.csv"))}
pred={json.loads(l)["case_id"]:json.loads(l) for l in open(ROOT/"output/replay_rebaseline/predictions.jsonl")}
meta,recs=cache.read(ROOT/"output/cache/train_grid.jsonl"); byid={r["stem"]:r for r in recs}
sample=sorted(dev)[::20]
print(f"sample {len(sample)} cases; axes: current + PSM-sweep(probe1) + native-hiDPI(5x) + field-crop", flush=True)
tot=dict(wrong=0,sel=0,psm=0,hidpi=0,ceiling=0); bad_ex=[]
for i,cid in enumerate(sample):
    if cid not in byid or byid[cid].get("error"): continue
    pages,reads=cache.to_case(byid[cid]["pages"])
    cur=[l for rl in reads.values() for r in rl for l in (r.lines or [])]
    psm=[]; hd=[]
    for pno,rl in reads.items():
        best=records.best_read(rl)
        if not best: continue
        try:
            fr=restored(cid,pno,best.variant)
            for p in (3,4,6): psm+=ocr(fr,p)
        except Exception as e: pass
        try: hd+=hidpi(cid,pno,qof(best.variant))
        except Exception as e: pass
    T,P=truth[cid],pred.get(cid,{})
    for f in FIELDS:
        tv=T.get(f,"")
        if not tv or tv.lower()=="none" or norm(P.get(f,""))==norm(tv): continue
        tot["wrong"]+=1
        if reachable(tv,cur): tot["sel"]+=1
        elif reachable(tv,cur+psm): tot["psm"]+=1
        elif reachable(tv,cur+hd): tot["hidpi"]+=1; bad_ex.append(f"{cid}.{f}={tv}")
        else: tot["ceiling"]+=1
    print(f"  [{i+1}/{len(sample)}] {cid}", flush=True)
print("\n===== FULL achievable delta (all axes) =====")
print(f"currently-wrong: {tot['wrong']}")
print(f"  selection (in current reads):          {tot['sel']}")
print(f"  PSM-sweep NEW (probe 1):               {tot['psm']}")
print(f"  hiDPI/crop NEW (this test):            {tot['hidpi']}   {bad_ex}")
print(f"  ceiling (unreachable, ALL axes):       {tot['ceiling']}")
