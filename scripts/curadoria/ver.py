import sqlite3, json, sys, re
c=sqlite3.connect('file:data/ted_jurisprudencia.db?mode=ro',uri=True)
cols=[r[1] for r in c.execute('pragma table_info(julgados)')]
idc='source_id' if 'source_id' in cols else 'id'
uf,kind,n=sys.argv[1],sys.argv[2],int(sys.argv[3])
ids=json.load(open(f'/tmp/diff_{uf}.json'))[kind][:n]
for i in ids:
    r=c.execute(f"select substr(coalesce(inteiro_teor_texto,descricao_ementa),1,330) from julgados where {idc}=?",(i,)).fetchone()
    print(i.split(':')[-1],'|',re.sub(r'\s+',' ',r[0]) if r else 'NAO NO BANCO'); print()
