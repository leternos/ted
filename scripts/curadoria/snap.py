import sys, json, importlib
sys.path.insert(0, "src")
from pathlib import Path
from ted_search import varredura as v
out = {}
ufs = sys.argv[2:] or ["CE","BA","ES","GO","PE","SE","RN","MT","PA","PB","MA","PI","AC","AL","TO","AP","RR","RJ","MG","RO","MS","AM","SC"]
for uf in ufs:
    try:
        pubs = v.ler_decisoes(uf, None, Path("data/raw"))
    except Exception as e:
        print(uf, "ERRO", e); continue
    recs, rej = v.normalizar_decisoes(uf, pubs)
    out[uf] = {"in": {str(r.get("source_id") or r.get("id")): int(bool(r.get("is_ted_disciplinar"))) for r in recs},
               "rej": {str((x.get("id") or x.get("source_id"))): x.get("motivo") for x in rej}}
    print(uf, len(recs), sum(out[uf]["in"].values()), len(rej), flush=True)
json.dump(out, open(sys.argv[1], "w"))
