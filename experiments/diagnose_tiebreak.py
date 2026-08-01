import sys, re
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent; sys.path.insert(0,str(ROOT))
from mib import cache, packet, parse, textmatch
from experiments.probe_arbitration import domain_hits

def norm(s): return re.sub(r'[^a-z0-9]','',str(s).lower())
CASES=[("MIB-000085","visa_class","MED-3","XW-2"),
       ("MIB-000395","sponsor_id","SPN-6146","SPN-6148"),
       ("MIB-000609","applicant_name","Solzarn Nexzarn","Nexrix Nexquell")]
meta,recs=cache.read(ROOT/"output/cache/train_grid.jsonl"); byid={r["stem"]:r for r in recs}

def line_for(kv, value):
    """(engine_conf, domain_hits, full read domain worth) for the line carrying value."""
    w=norm(value); best=None
    for e in (kv.get("_conf") or []):
        if len(e)>3 and w in norm(e[3]):
            if best is None or e[0]>best[0]: best=(e[0], domain_hits(e[3]), e[3])
    read_dom=sum(domain_hits(e[3]) for e in (kv.get("_conf") or []) if len(e)>3)
    return best, read_dom

for cid,field,truth,wrong in CASES:
    print(f"\n===== {cid}  {field}   truth={truth!r}  current-winner={wrong!r} =====")
    pages,reads=cache.to_case(byid[cid]["pages"])
    pkt=packet.assemble(pages,reads,fallback_case_id=cid)
    kvs=[kv for _dt,_s,kv in pkt.docs]+[kv for _dt,kv in pkt.variant_docs]
    for label,target in [("TRUTH",truth),("WINNER",wrong)]:
        confs=[]; domln=[]; domrd=[]
        for kv in kvs:
            v=kv.get(field)
            if not v or norm(v)!=norm(target): continue
            best,rd=line_for(kv,v)
            if best: confs.append(best[0]); domln.append(best[1]); domrd.append(rd)
        n=len(confs)
        if n:
            print(f"  {label:6} {target!r}: {n} reads | line_conf max={max(confs):.0f} mean={sum(confs)/n:.0f}"
                  f" | line_dom_hits max={max(domln)} | read_dom max={max(domrd)}")
        else:
            print(f"  {label:6} {target!r}: 0 reads carry it verbatim")
