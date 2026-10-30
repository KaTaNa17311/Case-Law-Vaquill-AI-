import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from backend import bulk_import, ingest, store


@pytest.fixture(autouse=True)
def database(monkeypatch,tmp_path):
    monkeypatch.setenv('DATABASE_PATH',str(tmp_path/'research.db'))
    store.init()


def test_discovery_only_selects_judgments_and_pins_revision():
    files=[SimpleNamespace(rfilename=n,size=100) for n in ['in_karnataka_judgments.parquet','in_delhi_judgments.parquet','in_supreme-court_judgments.parquet','in_central_legislation.parquet','in_sebi_regulations.parquet']]
    api=SimpleNamespace(dataset_info=lambda *a,**k:SimpleNamespace(sha='pinned-sha',siblings=files))
    with patch.object(bulk_import,'HfApi',return_value=api):
        revision,found=bulk_import.discover('release-ref')
    assert revision=='pinned-sha'
    assert len(found)==3 and found[0]['filename']=='in_supreme-court_judgments.parquet'
    assert all(f['filename'].endswith('_judgments.parquet') for f in found)


def rows():
    return [{'case_id':'synthetic-'+str(i//2),'court':'High Court of Karnataka','text_original':'Synthetic chunk '+str(i),'chunk_index':i%2,'total_chunks':2} for i in range(6)]


def test_limited_import_resumes_without_duplicate_records(tmp_path):
    path=tmp_path/'source.jsonl';path.write_text('\n'.join(json.dumps(r) for r in rows()))
    ingest.ingest(path,'test-revision',max_rows=3)
    assert store.coverage()['chunks']==3
    ingest.ingest(path,'test-revision')
    assert store.coverage()['chunks']==6 and store.coverage()['documents']==3
    with store.connect() as c:
        assert c.execute('SELECT count(*) FROM pending_documents').fetchone()[0]==0
        assert c.execute('SELECT status FROM imports').fetchone()[0]=='imported'
    with patch.object(ingest,'source_rows',side_effect=AssertionError('Completed file must not rescan source')):
        ingest.ingest(path,'test-revision')


def test_interrupted_reconstruction_is_recovered_without_reimport(tmp_path):
    path=tmp_path/'source.jsonl';path.write_text('\n'.join(json.dumps(r) for r in rows()))
    original=store.rebuild_document
    calls=0
    def interrupted(did):
        nonlocal calls
        calls+=1
        if calls==2:raise RuntimeError('Simulated interruption')
        return original(did)
    with patch.object(store,'rebuild_document',side_effect=interrupted):
        with pytest.raises(RuntimeError):ingest.ingest(path,'test-revision')
    with store.connect() as c:assert c.execute('SELECT count(*) FROM pending_documents').fetchone()[0]>0
    ingest.ingest(path,'test-revision')
    assert store.coverage()['chunks']==6
    with store.connect() as c:
        assert c.execute('SELECT count(*) FROM pending_documents').fetchone()[0]==0
        assert c.execute("SELECT count(*) FROM documents WHERE text='' ").fetchone()[0]==0


def test_bulk_plan_does_not_download_or_embed(monkeypatch):
    monkeypatch.setattr('sys.argv',['bulk_import','--revision','test-sha'])
    with patch.object(bulk_import,'discover',return_value=('test-sha',[{'filename':'in_delhi_judgments.parquet','size_bytes':100}])),patch.object(bulk_import,'download_file',side_effect=AssertionError('Plan must not download')):
        bulk_import.main()


def test_installer_recovers_missing_env_without_exposing_keys(monkeypatch,tmp_path,capsys):
    import importlib.util
    module_path=Path(__file__).resolve().parents[2]/'apply_repair.py'
    spec=importlib.util.spec_from_file_location('repair_installer',module_path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    target=tmp_path/'existing';(target/'backend').mkdir(parents=True)
    (target/'backend/app.py').write_text('old')
    (target/'compose.yaml').write_text('services: {}')
    fake=['BACKEND_API_TOKEN=synthetic-secret','GEMINI_API_KEY=synthetic-key','HF_TOKEN=synthetic-hf','GEMINI_MODEL=fixture-model','PATH=/do-not-copy']
    monkeypatch.setattr('sys.argv',['apply_repair.py',str(target),'--recover-env'])
    with patch.object(module.subprocess,'check_output',return_value=json.dumps(fake)):
        module.main()
    output=capsys.readouterr().out
    assert 'synthetic-secret' not in output and 'synthetic-key' not in output
    content=(target/'.env').read_text()
    assert 'synthetic-secret' in content and 'PATH=' not in content
    assert (target/'.env').stat().st_mode & 0o777 == 0o600
