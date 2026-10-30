"""Restore explicitly requested Vaquill snapshots. Requires substantial persistent disk."""
import argparse, hashlib, re
from . import providers
BASE='https://oss-data-in.vaquill.ai/qdrant'

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--collections',nargs='+',choices=['legal_corpus_v1','legal_corpus_v2','acts_india'],required=True);p.add_argument('--confirm-large-download',action='store_true');a=p.parse_args()
    if not a.confirm_large_download: p.error('Read data/QDRANT_RESTORE.md upstream and pass --confirm-large-download. All collections total roughly 464 GB before workspace/backups.')
    existing={x['name'] for x in providers.qdrant('GET','/collections')['result']['collections']}
    import httpx
    for name in a.collections:
        shards=2 if name=='acts_india' else 4
        if name in existing: raise RuntimeError('Refusing to overwrite an existing collection: '+name+'. Restore into a fresh Qdrant instance or remove it intentionally first.')
        providers.qdrant('PUT','/collections/'+name,{'shard_number':shards,'vectors':{'dense':{'size':1024,'distance':'Cosine','on_disk':True}},'sparse_vectors':{'sparse':{'modifier':'idf','index':{'on_disk':True}}}})
        for n in range(shards):
            base=f'{BASE}/{name}/shard-{n}'
            with httpx.Client(timeout=60) as c:
                manifest=c.get(base+'/index.json');manifest.raise_for_status();filename=manifest.json()['snapshot']
                if not re.fullmatch(r'[A-Za-z0-9_.-]+\.snapshot',filename): raise ValueError('Invalid snapshot filename')
                checksum=c.get(base+'/'+filename+'.checksum');checksum.raise_for_status();checksum=checksum.text.strip().split()[0]
            if not re.fullmatch(r'[a-fA-F0-9]{64}',checksum): raise ValueError('Invalid SHA256')
            # Recovery is asynchronous: poll shard status/collection counts before serving queries.
            providers.qdrant('PUT',f'/collections/{name}/shards/{n}/snapshots/recover',{'location':base+'/'+filename,'checksum':checksum,'priority':'snapshot'})
            print('Restore requested:',name,'shard',n,flush=True)
    print('Check collection point counts, shard recovery status and court coverage before enabling INDEX_MODE=snapshots. Do not equate accepted requests with completed recovery.')
if __name__=='__main__':main()
