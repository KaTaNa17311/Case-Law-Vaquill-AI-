"""Source registry and bounded lexical retrieval. No generated legal records."""
import hashlib, json, os, re, sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

COURTS = {'supreme':'Supreme Court of India','karnataka':'Karnataka High Court','delhi':'Delhi High Court','bombay':'Bombay High Court','madras':'Madras High Court','kerala':'Kerala High Court','allahabad':'Allahabad High Court','calcutta':'Calcutta High Court','gujarat':'Gujarat High Court','patna':'Patna High Court','telangana':'Telangana High Court','andhra':'Andhra Pradesh High Court','chhattisgarh':'Chhattisgarh High Court','punjab':'Punjab and Haryana High Court','rajasthan':'Rajasthan High Court','madhya':'Madhya Pradesh High Court','jharkhand':'Jharkhand High Court','orissa':'Orissa High Court','gauhati':'Gauhati High Court','himachal':'Himachal Pradesh High Court','uttarakhand':'Uttarakhand High Court','jammu':'Jammu and Kashmir High Court','tripura':'Tripura High Court','manipur':'Manipur High Court','meghalaya':'Meghalaya High Court','sikkim':'Sikkim High Court'}

def norm(value): return re.sub(r'\s+', ' ', str(value or '')).strip()
def court_key(value):
    value = norm(value).lower()
    if value in COURTS: return value
    for k, label in COURTS.items():
        if k in value or label.lower() == value: return k
    return 'unknown'

def db_path(): return os.environ.get('DATABASE_PATH', '/data/research.sqlite3')
@contextmanager
def connect():
    path=db_path(); os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    conn=sqlite3.connect(path, timeout=60); conn.row_factory=sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL'); conn.execute('PRAGMA foreign_keys=ON')
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback(); raise
    finally: conn.close()

def init():
    with connect() as c:
        c.executescript('''
        CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY, original_id TEXT, case_id TEXT, court_key TEXT, court TEXT, title TEXT, citation TEXT, case_number TEXT, decision_date TEXT, year INTEGER, pdf_url TEXT, source_url TEXT, release TEXT, language TEXT, text TEXT DEFAULT '', completeness TEXT DEFAULT 'unverified', expected_chunks INTEGER, sha256 TEXT, imported_at TEXT);
        CREATE INDEX IF NOT EXISTS doc_case ON documents(case_id);
        CREATE INDEX IF NOT EXISTS doc_court ON documents(court_key,year);
        CREATE TABLE IF NOT EXISTS chunks(id TEXT PRIMARY KEY, document_id TEXT REFERENCES documents(id), idx INTEGER, text TEXT, char_start INTEGER, char_end INTEGER, page_start INTEGER, page_end INTEGER, section_type TEXT, UNIQUE(document_id,idx));
        CREATE INDEX IF NOT EXISTS chunks_doc ON chunks(document_id,idx);
        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(id UNINDEXED, document_id UNINDEXED, text, tokenize='unicode61');
        CREATE TABLE IF NOT EXISTS pending_documents(document_id TEXT PRIMARY KEY REFERENCES documents(id));
        CREATE TABLE IF NOT EXISTS source_metadata(document_id TEXT PRIMARY KEY REFERENCES documents(id), metadata_json TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS vector_state(chunk_id TEXT, model TEXT, PRIMARY KEY(chunk_id,model));
        CREATE TABLE IF NOT EXISTS imports(file TEXT PRIMARY KEY, revision TEXT, rows INTEGER DEFAULT 0, status TEXT, updated_at TEXT);
        ''')

def val(row, *names, default=None):
    for key in names:
        x=row.get(key)
        if x is not None and str(x).strip(): return x
    return default

def number(v):
    try: return int(v)
    except (ValueError,TypeError): return None

def ingest_rows(rows, release='local', court_override=None):
    """Each Parquet row maps to a source document, never a generated judgment."""
    touched=set(); count=0
    with connect() as c:
        for r in rows:
            original=norm(val(r,'doc_id','case_id')); case_id=norm(val(r,'case_id','doc_id'))
            text=val(r,'text_original', default=None)
            # The schema's `text` may contain an indexing header. Remove only its documented header.
            if text is None:
                text=str(val(r,'text', default=''))
                if text.startswith('Case: ') and '\n\n' in text: text=text.split('\n\n',1)[1]
            text=str(text)
            if not original or not text.strip(): raise ValueError('Each row requires doc_id/case_id and nonempty text_original/text')
            idx=number(val(r,'chunk_index', default=0))
            if idx is None or idx<0: raise ValueError('Invalid chunk_index')
            court=norm(court_override or val(r,'court','court_name',default='Unknown court'))
            language=norm(val(r,'language','language_code',default='en'))
            # Include court/language/release: never merge different versions or translations.
            document_identity=original if r.get('doc_id') else original+'|'+norm(val(r,'pdf_url','source_pdf_url','decision_date',default=''))
            did=hashlib.sha256(f'{court_key(court)}|{document_identity}|{language}|{release}'.encode()).hexdigest()[:32]
            cid=f'{did}:{idx}'
            year=number(val(r,'year'))
            data=(did,original,case_id,court_key(court),court,norm(val(r,'title','case_title',default=case_id)),norm(val(r,'citation')),norm(val(r,'case_number')),norm(val(r,'decision_date')),year,norm(val(r,'pdf_url','source_pdf_url')),norm(val(r,'source_url')),release,language,number(val(r,'total_chunks')),datetime.now(timezone.utc).isoformat())
            c.execute('''INSERT INTO documents(id,original_id,case_id,court_key,court,title,citation,case_number,decision_date,year,pdf_url,source_url,release,language,expected_chunks,imported_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET expected_chunks=COALESCE(excluded.expected_chunks,documents.expected_chunks)''',data)
            raw_meta={k:v for k,v in r.items() if k not in ('text','text_original','content','embedding','vector')}
            c.execute('INSERT OR IGNORE INTO source_metadata VALUES(?,?)',(did,json.dumps(raw_meta,ensure_ascii=False,default=str)))
            old=c.execute('SELECT text FROM chunks WHERE id=?',(cid,)).fetchone()
            if old and old['text']!=text: raise ValueError('Conflicting chunk text in the same release; import as a new release')
            if not old:
                c.execute('INSERT INTO chunks VALUES(?,?,?,?,?,?,?,?,?)',(cid,did,idx,text,number(r.get('char_start')),number(r.get('char_end')),number(r.get('page_start')),number(r.get('page_end')),norm(r.get('section_type'))))
                c.execute('INSERT INTO chunks_fts(id,document_id,text) VALUES(?,?,?)',(cid,did,text))
            c.execute('INSERT OR IGNORE INTO pending_documents VALUES(?)',(did,))
            touched.add(did); count+=1
    return touched,count

def rebuild_document(did):
    with connect() as c:
        d=c.execute('SELECT * FROM documents WHERE id=?',(did,)).fetchone()
        chunks=c.execute('SELECT * FROM chunks WHERE document_id=? ORDER BY idx',(did,)).fetchall()
        expected=d['expected_chunks']; sequence=[x['idx'] for x in chunks]
        contiguous=bool(expected and sequence==list(range(expected)))
        offsets_ok=True; output=''; end=0
        for x in chunks:
            start=x['char_start']; stop=x['char_end']; text=x['text']
            if start is None or stop is None or stop-start!=len(text) or start>end:
                offsets_ok=False; break
            if start<end and output[start:end] != text[:end-start]: offsets_ok=False; break
            output+=text[max(0,end-start):]; end=stop
        if not offsets_ok:
            output=''; previous=None
            for x in chunks:
                t=x['text']; overlap=0
                if previous is not None and x['idx']==previous+1:
                    for n in range(min(len(output),len(t),2048),31,-1):
                        if output[-n:]==t[:n]: overlap=n; break
                output+= ('\n\n' if output and not overlap else '')+t[overlap:]; previous=x['idx']
        # Chunk coverage is not proof of identity/PDF fidelity.
        state='all_chunks_offsets_checked' if contiguous and offsets_ok else ('all_chunks_reconstructed' if contiguous else 'partial_or_unverified')
        c.execute('UPDATE documents SET text=?,completeness=?,sha256=? WHERE id=?',(output,state,hashlib.sha256(output.encode()).hexdigest(),did))
        c.execute('DELETE FROM pending_documents WHERE document_id=?',(did,))

def filters(courts, start=None, end=None, alias='d'):
    clauses=[]; args=[]
    if courts:
        clauses.append(f'{alias}.court_key IN ({",".join("?" for _ in courts)})'); args+=courts
    if start: clauses.append(f'{alias}.year>=?'); args.append(start)
    if end: clauses.append(f'{alias}.year<=?'); args.append(end)
    return (' AND '+' AND '.join(clauses) if clauses else ''),args

def lexical(query,courts,start=None,end=None,exact=False):
    where,args=filters(courts,start,end)
    terms=re.findall(r'\w+',query, flags=re.UNICODE)[:64]
    stop={'find','judgment','judgments','judgement','judgements','case','cases','discuss','discussing','give','show','please','me','a','an','the','in','on','of','to','and','for','that','where','which','is','are','was','were','with','about'}
    terms=[t for t in terms if t.lower() not in stop][:32]
    if not terms: return []
    with connect() as c:
        if exact:
            # Source documents handle phrase matches crossing chunk boundaries.
            rows=c.execute('SELECT d.* FROM documents d WHERE instr(lower(d.text),lower(?))>0'+where+' LIMIT 30',[query]+args).fetchall()
            out=[]
            for d in rows:
                pos=d['text'].lower().find(query.lower()); text=d['text'][max(0,pos-500):pos+len(query)+1500]
                out.append(dict(document_id=d['id'],chunk_id=None,text=text,page_start=None,page_end=None,section_type='',method='exact phrase'))
            return out
        expression=' OR '.join('"'+t+'"' for t in terms)
        rows=c.execute('''SELECT ch.*, d.id AS did FROM chunks_fts f JOIN chunks ch ON ch.id=f.id JOIN documents d ON d.id=ch.document_id WHERE chunks_fts MATCH ?'''+where+' ORDER BY bm25(chunks_fts) LIMIT 80',[expression]+args).fetchall()
        return [dict(document_id=r['did'],chunk_id=r['id'],text=r['text'],page_start=r['page_start'],page_end=r['page_end'],section_type=r['section_type'],method='keyword') for r in rows]

def get_document(did,include_text=True):
    with connect() as c:
        row=c.execute('SELECT * FROM documents WHERE id=?',(did,)).fetchone()
        if not row: return None
        d=dict(row)
        if not include_text: d.pop('text',None)
        d['chunk_count']=c.execute('SELECT count(*) FROM chunks WHERE document_id=?',(did,)).fetchone()[0]
        d['legal_status']='Not checked for subsequent treatment'
        return d

def coverage():
    with connect() as c:
        rows=c.execute('SELECT court_key,court,count(*) documents,min(year) first_year,max(year) last_year,max(imported_at) last_import FROM documents GROUP BY court_key,court').fetchall()
        return {'documents':c.execute('SELECT count(*) FROM documents').fetchone()[0], 'chunks':c.execute('SELECT count(*) FROM chunks').fetchone()[0], 'courts':[dict(r) for r in rows]}

def validate_claims(response,evidence):
    indexed={e['id']:e for e in evidence}; accepted=[]; rejected=0
    for item in response.get('claims',[])[:12]:
        if not isinstance(item,dict): rejected+=1; continue
        ids=item.get('evidence_ids',[]); quotes=item.get('quotes',[])
        if not isinstance(ids,list) or not isinstance(quotes,list) or any(not isinstance(i,str) for i in ids): rejected+=1; continue
        if not ids or not quotes or any(i not in indexed for i in ids): rejected+=1; continue
        checked=[]
        for q in quotes:
            if not isinstance(q,dict): continue
            eid=q.get('evidence_id'); quote=q.get('text','')
            if isinstance(eid,str) and eid in ids and isinstance(quote,str) and len(quote.strip())>=20 and quote in indexed[eid]['text']:
                checked.append({'evidence_id':eid,'text':quote})
        if not checked or set(ids)-{q['evidence_id'] for q in checked}: rejected+=1; continue
        statement=str(item.get('statement','')).strip()
        if not statement: rejected+=1; continue
        accepted.append({'statement':statement[:2500],'evidence_ids':ids,'quotes':checked})
    return accepted,rejected
