from unittest.mock import patch
import httpx
import pytest
from backend import providers, store, ingest


class FakeClient:
    responses=[]
    def __init__(self,**kwargs): pass
    def __enter__(self): return self
    def __exit__(self,*args): pass
    def request(self,*args,**kwargs): return self.responses.pop(0)


def response(status,payload,headers=None):
    return httpx.Response(status,json=payload,headers=headers,request=httpx.Request('POST','https://generativelanguage.googleapis.com/test'))


def test_provider_errors_redact_keys_and_obey_long_retry_after(monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY','secret-api-key')
    FakeClient.responses=[response(429,{'error':{'message':'Daily quota exceeded secret-api-key'}},{'Retry-After':'120'})]
    with patch.object(providers.httpx,'Client',FakeClient),patch.object(providers.time,'sleep') as sleep:
        with pytest.raises(providers.ProviderError) as error:
            providers.request('POST','https://generativelanguage.googleapis.com/test',_budget=5)
    assert 'secret-api-key' not in str(error.value)
    assert 'Daily quota' in str(error.value) and error.value.status==429
    sleep.assert_not_called()


def test_retry_after_is_used_for_temporary_limits():
    FakeClient.responses=[response(429,{'error':{'message':'Slow down'}},{'Retry-After':'2'}),response(200,{'ok':True})]
    with patch.object(providers.httpx,'Client',FakeClient),patch.object(providers.time,'sleep') as sleep:
        assert providers.request('POST','https://generativelanguage.googleapis.com/test')=={'ok':True}
    sleep.assert_called_once_with(2)


def test_each_successful_embedding_survives_next_quota_error(monkeypatch,tmp_path):
    monkeypatch.setenv('DATABASE_PATH',str(tmp_path/'progress.db'))
    monkeypatch.setenv('EMBEDDING_INTERVAL_SECONDS','0')
    monkeypatch.setenv('EMBEDDING_DIM','2')
    store.init();ids,n=store.ingest_rows([{'case_id':'fixture','court':'Karnataka High Court','chunk_index':i,'total_chunks':3,'text_original':'Synthetic source '+str(i)} for i in range(3)],'test')
    vector=[1.,0.]
    with patch.object(providers,'ensure_collection',return_value='test'),patch.object(providers,'embeddings',side_effect=[[vector],providers.ProviderError('Quota',429)]),patch.object(providers,'qdrant',return_value={}):
        with pytest.raises(providers.ProviderError): ingest.embed(3)
    signature=providers.model_signature()+'|test'
    with store.connect() as c:
        assert c.execute('SELECT count(*) FROM vector_state WHERE model=?',(signature,)).fetchone()[0]==1
    with patch.object(providers,'ensure_collection',return_value='test'),patch.object(providers,'embeddings',return_value=[vector]),patch.object(providers,'qdrant',return_value={}):
        ingest.embed(2)
    with store.connect() as c:
        assert c.execute('SELECT count(*) FROM vector_state WHERE model=?',(signature,)).fetchone()[0]==3
