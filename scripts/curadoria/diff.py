import json
a=json.load(open('/tmp/snap_antes.json')); b=json.load(open('/tmp/snap_depois.json'))
for uf in a:
    A,B=a[uf]['in'],b[uf]['in']
    up=[k for k in A if k in B and A[k]==0 and B[k]==1]; dn=[k for k in A if k in B and A[k]==1 and B[k]==0]
    saiu=[k for k in A if k not in B]; entrou=[k for k in B if k not in A]
    print(uf,'0->1',len(up),'1->0',len(dn),'saiu',len(saiu),'entrou',len(entrou))
    json.dump({'up':up,'dn':dn,'saiu':saiu,'entrou':entrou},open(f'/tmp/diff_{uf}.json','w'))
