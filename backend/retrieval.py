import os
from . import store, providers


def semantic(query,courts,start,end):
    if os.environ.get('INDEX_MODE','owned')=='snapshots':
        if os.environ.get('EMBEDDING_PROVIDER')!='voyage' or int(os.environ.get('EMBEDDING_DIM','768'))!=1024:
            raise providers.ProviderError('Snapshots require compatible Voyage embeddings and EMBEDDING_DIM=1024')
        collections=['legal_corpus_v1','legal_corpus_v2']
        # Use only raw court values observed in local source records. No guessed payload labels.
        with store.connect() as c:
            where,args=store.filters(courts,start,end)
            raw=[r[0] for r in c.execute('SELECT DISTINCT court FROM documents d WHERE 1=1'+where,args)]
        if not raw: return []
        must=[{'key':os.environ.get('SNAPSHOT_COURT_FIELD','court'),'match':{'any':raw}}]
    else:
        collections=[providers.collection()]
        # Verify the index identity on query as well as at embedding time.
        manifest=providers.qdrant('POST',f'/collections/{collections[0]}/points',{'ids':['00000000-0000-0000-0000-000000000000'],'with_payload':True})['result']
        if not manifest or manifest[0]['payload'].get('model_signature')!=providers.model_signature():
            raise providers.ProviderError('Index model signature missing or incompatible. Run the embed job or select the correct collection.')
        must=[{'key':'court_key','match':{'any':courts}}] if courts else []
    if start or end:
        must.append({'key':'year','range':dict(([('gte',start)] if start else [])+([('lte',end)] if end else []))})
    vector=providers.embeddings([query],query=True)[0]
    result=[]
    for collection in collections:
        data=providers.qdrant('POST',f'/collections/{collection}/points/query',{'query':vector,'using':'dense','filter':{'must':must,'must_not':[{'key':'is_manifest','match':{'value':True}}]},'limit':100,'with_payload':True})
        for point in data['result']['points']:
            p=point.get('payload',{}); chunk=None
            with store.connect() as c:
                if os.environ.get('INDEX_MODE','owned')=='snapshots':
                    # Mapping is source-based. Unmapped snapshots cannot become displayed legal records.
                    original=str(p.get('doc_id') or p.get('case_id') or '')
                    matches=c.execute('SELECT id FROM documents WHERE (original_id=? OR case_id=?) AND release=?',(original,original,os.environ.get('SNAPSHOT_RELEASE','v2026.08.1'))).fetchall()
                    idx=store.number(p.get('chunk_index'))
                    if len(matches)==1 and idx is not None: chunk=c.execute('SELECT * FROM chunks WHERE document_id=? AND idx=?',(matches[0]['id'],idx)).fetchone()
                else: chunk=c.execute('SELECT * FROM chunks WHERE id=?',(p.get('chunk_id',''),)).fetchone()
            if not chunk: continue
            d=store.get_document(chunk['document_id'],False)
            if courts and d['court_key'] not in courts: continue
            if start and (d['year'] is None or d['year']<start): continue
            if end and (d['year'] is None or d['year']>end): continue
            result.append(dict(document_id=d['id'],chunk_id=chunk['id'],text=chunk['text'],page_start=chunk['page_start'],page_end=chunk['page_end'],section_type=chunk['section_type'],method='semantic'))
    return result


def retrieve(query,courts,start=None,end=None,mode='meaning'):
    lex=store.lexical(query,courts,start,end,exact=mode=='phrase'); warnings=[]
    if store.coverage()['documents']==0: return [],[]
    dense=[]
    if mode=='meaning':
        try: dense=semantic(query,courts,start,end)
        except (providers.ProviderError,KeyError,ValueError) as e:
            warnings.append('Semantic retrieval unavailable. Only keyword evidence was searched. '+str(e))
    # Interleave routes, cap passages per document; no relevance numbers exposed.
    pool=[]; used=set(); per={}
    for i in range(max(len(lex),len(dense))):
        for items in (dense,lex):
            if i>=len(items): continue
            e=items[i]; key=(e['document_id'],e['chunk_id'] or e['text'])
            if key in used or per.get(e['document_id'],0)>=3: continue
            if e['document_id'] not in per and len(per)>=6: continue
            used.add(key); per[e['document_id']]=per.get(e['document_id'],0)+1
            e['id']='E'+str(len(pool)+1); e['document']=store.get_document(e['document_id'],False)
            # Adjacent chunks provide context without converting an argument into a holding.
            if e['chunk_id']:
                with store.connect() as c:
                    hit=c.execute('SELECT idx FROM chunks WHERE id=?',(e['chunk_id'],)).fetchone()
                    near=c.execute('SELECT idx,text FROM chunks WHERE document_id=? AND idx BETWEEN ? AND ? ORDER BY idx',(e['document_id'],hit['idx']-1,hit['idx']+1)).fetchall()
                e['context']=[{'chunk_index':r['idx'],'text':r['text']} for r in near]
            pool.append(e)
    return pool,warnings
