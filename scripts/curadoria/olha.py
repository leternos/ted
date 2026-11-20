import sqlite3,sys
c=sqlite3.connect('file:data/ted_jurisprudencia.db?mode=ro',uri=True,timeout=60)
uf=sys.argv[1]; n=int(sys.argv[2])
for i in sys.argv[3:]:
    r=c.execute("select is_ted_disciplinar,orgao_julgador_raw,inteiro_teor_texto from julgados where id=?",(f"DEOAB_{uf}:{i}",)).fetchone()
    if not r: print(i,'NAO ESTA'); continue
    print(f"## {i} flag={r[0]} org={r[1]}\n{(r[2] or '')[:n]}\n")
