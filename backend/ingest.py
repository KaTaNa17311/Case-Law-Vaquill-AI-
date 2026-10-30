"""Resumable source import and optional embedding. Run with python -m backend.ingest."""
import argparse, hashlib, json, os, sys, time, uuid
from datetime import datetime, timezone
from pathlib import Path
from . import store, providers

def source_rows(path):
    if path.suffix=='.jsonl':
        with path.open() as f:
            for line in f:
                if line.strip(): yield json.loads(line)
    elif path.suffix=='.parquet':
        import pyarrow.parquet as pq
        for batch in pq.ParquetFile(path).iter_batches(batch_size=1000): yield from batch.to_pylist()
    else: raise ValueError('Use a .parquet or .jsonl file')

def finalise_pending(release):
    # Disk-backed work queue: do not accumulate millions of document IDs in RAM.
    done=0
    while True:
        with store.connect() as c:
            ids=[r[0] for r in c.execute('SELECT p.document_id FROM pending_documents p JOIN documents d ON d.id=p.document_id WHERE d.release=? LIMIT 200',(release,))]
        if not ids: break
        for did in ids:
            store.rebuild_document(did);done+=1
        print(f'Reconstructed {done} pending documents',flush=True)


def ingest(path,release,court=None,max_rows=None):
    key=str(path.resolve()); skipped=0; count=0; batch=[]
    with store.connect() as c:
        existing=c.execute('SELECT * FROM imports WHERE file=?',(key,)).fetchone()
        if existing and existing['revision']!=release: raise ValueError('Same file path already has another release; use a separate downloaded path')
        skipped=existing['rows'] if existing else 0
        c.execute('INSERT OR IGNORE INTO imports VALUES(?,?,?,?,?)',(key,release,0,'importing',datetime.now(timezone.utc).isoformat()))
        # Recover unfinished documents from earlier app versions too.
        c.execute("INSERT OR IGNORE INTO pending_documents SELECT id FROM documents WHERE release=? AND text=''",(release,))
    if existing and existing['status']=='imported' and not max_rows:
        finalise_pending(release)
        print('Already imported:',path.name,flush=True);return
    exhausted=True
    for i,row in enumerate(source_rows(path)):
        if i<skipped: continue
        if max_rows and count>=max_rows:
            exhausted=False;break
        batch.append(row);count+=1
        if len(batch)>=1000:
            touched,n=store.ingest_rows(batch,release,court);skipped+=n;batch=[]
            with store.connect() as c: c.execute('UPDATE imports SET rows=?,status=?,updated_at=? WHERE file=?',(skipped,'importing',datetime.now(timezone.utc).isoformat(),key))
            print(f'{path.name}: {skipped:,} source rows saved',flush=True)
    if batch:
        touched,n=store.ingest_rows(batch,release,court);skipped+=n
    with store.connect() as c:
        c.execute('UPDATE imports SET rows=?,status=?,updated_at=? WHERE file=?',(skipped,'reconstructing',datetime.now(timezone.utc).isoformat(),key))
    finalise_pending(release)
    with store.connect() as c:
        c.execute('UPDATE imports SET status=?,updated_at=? WHERE file=?',('imported' if exhausted else 'limited',datetime.now(timezone.utc).isoformat(),key))
    print(json.dumps(store.coverage(),indent=2),flush=True)

def embed(limit=None):
    if os.environ.get('INDEX_MODE','owned')=='snapshots': raise ValueError('Snapshot mode reuses existing embeddings; do not embed into those collections')
    collection=providers.ensure_collection(); signature=providers.model_signature(); done=0
    while True:
        with store.connect() as c:
            rows=c.execute('''SELECT ch.*,d.court_key,d.year FROM chunks ch JOIN documents d ON d.id=ch.document_id LEFT JOIN vector_state v ON v.chunk_id=ch.id AND v.model=? WHERE v.chunk_id IS NULL LIMIT 1''',(signature+'|'+collection,)).fetchall()
        if not rows: break
        if limit: rows=rows[:max(0,limit-done)]
        if not rows: break
        vectors=providers.embeddings([r['text'] for r in rows]); points=[]
        if len(vectors)!=len(rows): raise providers.ProviderError('Provider returned an incomplete embedding batch; progress was not advanced.')
        for r,v in zip(rows,vectors):
            if len(v)!=int(os.environ.get('EMBEDDING_DIM','768')): raise ValueError('Unexpected vector dimension')
            points.append({'id':str(uuid.uuid5(uuid.NAMESPACE_URL,r['id'])),'vector':{'dense':v},'payload':{'chunk_id':r['id'],'document_id':r['document_id'],'court_key':r['court_key'],'year':r['year']}})
        providers.qdrant('PUT',f'/collections/{collection}/points?wait=true',{'points':points})
        with store.connect() as c: c.executemany('INSERT OR IGNORE INTO vector_state VALUES(?,?)',[(r['id'],signature+'|'+collection) for r in rows])
        done+=len(rows); print(f'Embedded {done} new chunks (saved)',flush=True)
        if not limit or done<limit:
            time.sleep(max(0,float(os.environ.get('EMBEDDING_INTERVAL_SECONDS','4'))))

def main():
    p=argparse.ArgumentParser(); sub=p.add_subparsers(dest='cmd',required=True)
    sub.add_parser('list-hf',help='List actual dataset filenames and current revision')
    hf=sub.add_parser('hf'); hf.add_argument('--file',required=True); hf.add_argument('--revision',required=True); hf.add_argument('--court'); hf.add_argument('--max-rows',type=int)
    local=sub.add_parser('local'); local.add_argument('path'); local.add_argument('--release',required=True); local.add_argument('--court'); local.add_argument('--max-rows',type=int)
    em=sub.add_parser('embed'); em.add_argument('--limit',type=int)
    sub.add_parser('coverage'); a=p.parse_args(); store.init()
    if a.cmd=='list-hf':
        from huggingface_hub import HfApi
        api=HfApi(token=os.environ.get('HF_TOKEN')); info=api.dataset_info('vaquill/open-india-law')
        print('Revision:',info.sha)
        for f in api.list_repo_files('vaquill/open-india-law',repo_type='dataset',revision=info.sha):
            if f.endswith('.parquet'): print(f)
    elif a.cmd=='hf':
        from huggingface_hub import hf_hub_download
        path=hf_hub_download('vaquill/open-india-law',a.file,repo_type='dataset',revision=a.revision,token=os.environ.get('HF_TOKEN'))
        ingest(Path(path),a.revision,a.court,a.max_rows)
    elif a.cmd=='local': ingest(Path(a.path),a.release,a.court,a.max_rows)
    elif a.cmd=='embed':
        try: embed(a.limit)
        except providers.ProviderError as error:
            print('Embedding paused: '+str(error),file=sys.stderr,flush=True)
            print('Completed chunks are saved. Run the same command again when the provider limit permits.',file=sys.stderr)
            raise SystemExit(1)
    else: print(json.dumps(store.coverage(),indent=2))
if __name__=='__main__': main()
