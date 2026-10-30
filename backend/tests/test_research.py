"""Synthetic fixtures only. These are not real cases or legal propositions."""
import os, tempfile
from unittest.mock import patch
import pytest
from fastapi.testclient import TestClient
from backend import store

@pytest.fixture(autouse=True)
def database(monkeypatch):
    with tempfile.TemporaryDirectory() as folder:
        monkeypatch.setenv('DATABASE_PATH',folder+'/test.sqlite3')
        monkeypatch.setenv('BACKEND_API_TOKEN','test-token')
        monkeypatch.delenv('GEMINI_API_KEY',raising=False)
        monkeypatch.delenv('GEMINI_MODEL',raising=False)
        store.init(); yield

def row(doc='fixture-a',court='Karnataka High Court',text='Synthetic alpha document; no legal content.',idx=0,total=1,start=0):
    return {'doc_id':doc,'case_id':doc,'court':court,'title':'SYNTHETIC TEST DOCUMENT','text_original':text,'chunk_index':idx,'total_chunks':total,'char_start':start,'char_end':start+len(text),'year':2024,'decision_date':'2024-01-01','language':'en'}
def load(rows):
    ids,n=store.ingest_rows(rows,'test-release')
    for id in ids: store.rebuild_document(id)
    return next(iter(ids))
def client():
    from backend.app import app
    return TestClient(app)
def auth(): return {'Authorization':'Bearer test-token'}

def test_filters_do_not_leak_other_courts():
    load([row(),row('fixture-b','Delhi High Court')])
    result=store.lexical('alpha',['karnataka'])
    assert len(result)==1
    assert store.get_document(result[0]['document_id'])['court_key']=='karnataka'
    assert store.lexical('alpha',['karnataka'],2025)==[]

def test_overlap_reconstruction_and_phrase_crossing_chunks():
    text='Synthetic alpha document has overlapping content and a boundary.'
    a=row(text=text[:44],total=2); b=row(text=text[25:],idx=1,total=2,start=25)
    did=load([a,b]); d=store.get_document(did)
    assert d['text']==text
    assert d['completeness']=='all_chunks_offsets_checked'
    assert store.lexical('content and a boundary',['karnataka'],exact=True)

def test_missing_chunk_is_never_complete():
    did=load([row(total=3)])
    assert store.get_document(did)['completeness']=='partial_or_unverified'

def test_repeated_import_is_idempotent_and_conflict_rejected():
    load([row()]); load([row()]); assert store.coverage()['chunks']==1
    with pytest.raises(ValueError): load([row(text='Changed synthetic text')])

def test_languages_and_releases_do_not_merge():
    a=row(); b=row(); b['language']='kn'
    ids,n=store.ingest_rows([a,b],'release-one'); assert len(ids)==2
    ids2,n=store.ingest_rows([a],'release-two'); assert not ids.intersection(ids2)

def test_quotes_and_evidence_ids_must_match():
    evidence=[{'id':'E1','text':'Synthetic source text with a sufficient quotation length.'}]
    good={'statement':'Synthetic assertion.','evidence_ids':['E1'],'quotes':[{'evidence_id':'E1','text':evidence[0]['text']}]}
    bad={'statement':'Unsupported assertion.','evidence_ids':['E9'],'quotes':[{'evidence_id':'E9','text':'Invented quote with enough length'}]}
    altered={'statement':'Wrong quote.','evidence_ids':['E1'],'quotes':[{'evidence_id':'E1','text':'Synthetic source text WHICH WAS ALTERED.'}]}
    accepted,rejected=store.validate_claims({'claims':[good,bad,altered]},evidence)
    assert accepted==[good] and rejected==2

def test_auth_required_and_no_provider_key_exposed():
    c=client(); assert c.get('/api/status').status_code==401
    r=c.get('/api/status',headers=auth()); assert r.status_code==200
    assert r.json()['coverage']['documents']==0
    assert 'test-token' not in r.text

def test_empty_corpus_abstains_and_rejects_invalid_scope():
    c=client()
    r=c.post('/api/chat',headers=auth(),json={'message':'synthetic query','jurisdiction':'karnataka'})
    assert r.json()['status']=='insufficient_evidence' and r.json()['claims']==[]
    assert c.post('/api/chat',headers=auth(),json={'message':'synthetic query','jurisdiction':'invented'}).status_code==422
    assert c.post('/api/chat',headers=auth(),json={'message':'synthetic query','start_year':2025,'end_year':2024}).status_code==422

def test_evidence_only_download_and_missing_citation():
    did=load([row()]); c=client()
    r=c.post('/api/chat',headers=auth(),json={'message':'alpha','jurisdiction':'karnataka','mode':'phrase','include_supreme':False})
    assert r.json()['status']=='evidence_only'
    d=c.get('/api/judgments/'+did,headers=auth()).json()
    assert not d['citation']
    download=c.get('/api/judgments/'+did+'/download',headers=auth())
    assert 'Citation: Unavailable' in download.text
    assert 'Synthetic alpha' in download.text

def test_generated_answer_is_filtered_before_return(monkeypatch):
    did=load([row()]); monkeypatch.setenv('GEMINI_API_KEY','not-a-real-key'); monkeypatch.setenv('GEMINI_MODEL','test-model')
    quote='Synthetic alpha document; no legal content.'
    generated={'claims':[{'statement':'A synthetic statement.','evidence_ids':['E1'],'quotes':[{'evidence_id':'E1','text':quote}]},{'statement':'Invented authority.','evidence_ids':['E99'],'quotes':[{'evidence_id':'E99','text':'An invented quotation that must not pass.'}]}],'limitations':['Synthetic test only.']}
    with patch('backend.providers.gemini_json',return_value=generated):
        r=client().post('/api/chat',headers=auth(),json={'message':'alpha','mode':'phrase'})
    data=r.json(); assert data['status']=='answered' and len(data['claims'])==1
    assert 'Invented authority' not in r.text
    assert data['warnings']

def test_fts_injection_is_data_not_sql():
    load([row()]); assert isinstance(store.lexical('alpha OR " ) ; DROP TABLE documents; --',['karnataka']),list)
    assert store.coverage()['documents']==1

def test_unknown_document_404():
    assert client().get('/api/judgments/does-not-exist',headers=auth()).status_code==404


def test_random_full_judgment_has_raw_and_readable_without_model():
    did=load([row(text='Synthetic writpetition was filed for finalhearing.\n\nORDER\nSpeling uncorrrected.')])
    with patch('backend.providers.gemini_json',side_effect=AssertionError('Must not call Gemini')):
        result=client().post('/api/chat',headers=auth(),json={'message':'Give me a random full judgment','include_supreme':False}).json()
    assert result['status']=='document'
    document=result['judgment']
    assert document['id']==did and 'writ petition' in document['formatted_text']
    assert 'Speling uncorrrected.' in document['formatted_text']
    assert 'writpetition' in document['text']
    assert document['formatting']['non_whitespace_unchanged']


def test_full_document_cannot_escape_current_jurisdiction():
    did=load([row(court='Delhi High Court')])
    data=client().post('/api/chat',headers=auth(),json={'message':'Show the entire judgment','active_document_id':did,'jurisdiction':'karnataka','include_supreme':False}).json()
    assert data['status']=='insufficient_evidence'
    assert 'judgment' not in data


def test_full_document_by_active_id_and_raw_download():
    did=load([row(text='The writpetition contains uncorrrected text.')]); c=client()
    data=c.post('/api/chat',headers=auth(),json={'message':'Show the entire judgment','active_document_id':did}).json()
    assert data['judgment']['id']==did
    raw=c.get('/api/judgments/'+did+'/download?view=raw',headers=auth()).text
    readable=c.get('/api/judgments/'+did+'/download?view=readable',headers=auth()).text
    assert 'writpetition' in raw and 'writ petition' in readable
    assert 'uncorrrected' in readable


def test_keyword_mode_bypasses_embedding_requests():
    load([row()])
    with patch('backend.providers.embeddings',side_effect=AssertionError('Must not embed in keyword mode')):
        result=client().post('/api/chat',headers=auth(),json={'message':'Synthetic alpha','mode':'keyword'}).json()
    assert result['status']=='evidence_only' and result['evidence']


def test_source_language_code_and_metadata_preserved():
    source=row(); source['language_code']='kn';source.pop('language')
    source['judges']=['UPSTREAM EXTRACTED VALUE'];source['source_url']=''
    did=load([source]); d=client().get('/api/judgments/'+did,headers=auth()).json()
    assert d['language']=='kn'
    assert d['source_metadata']['judges']==source['judges']
    assert not d['source_url']
