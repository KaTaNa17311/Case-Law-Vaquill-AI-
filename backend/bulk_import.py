"""Discover and sequentially import the complete judgment subset. No embedding API calls."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import shutil
import sys
from huggingface_hub import HfApi, hf_hub_download
from . import store, ingest

REPO='vaquill/open-india-law'


def discover(revision, selected=None):
    api=HfApi(token=os.environ.get('HF_TOKEN'))
    info=api.dataset_info(REPO,revision=revision,files_metadata=True)
    selected=set(selected or [])
    files=[]
    for item in info.siblings or []:
        filename=item.rfilename
        if filename.endswith('_judgments.parquet') and (not selected or filename in selected or Path(filename).name in selected):
            files.append({'filename':filename,'size_bytes':item.size})
    if not files: raise ValueError('No matching judgment Parquet files were found in the selected revision.')
    files.sort(key=lambda f:(0 if 'supreme-court' in f['filename'] else 1 if 'karnataka' in f['filename'] else 2,f['filename']))
    if selected:
        found={f['filename'] for f in files}|{Path(f['filename']).name for f in files}
        missing=selected-found
        if missing: raise ValueError('Unknown filenames: '+', '.join(sorted(missing)))
    return info.sha,files


def ensure_lock(revision):
    with store.connect() as c:
        c.execute('CREATE TABLE IF NOT EXISTS bulk_import_runs(revision TEXT PRIMARY KEY, manifest_json TEXT NOT NULL)')


def download_file(filename,revision):
    return Path(hf_hub_download(REPO,filename,repo_type='dataset',revision=revision,token=os.environ.get('HF_TOKEN')))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--revision',required=True,help='Pinned Hugging Face commit SHA or an explicit release ref')
    p.add_argument('--files',nargs='+',help='Optional exact judgment filenames; omit to include all judgment files')
    p.add_argument('--run',action='store_true',help='Actually download and import; default only prints the plan')
    p.add_argument('--reserve-gb',type=float,default=20,help='Minimum free disk headroom before each download (not a full index-size estimate)')
    a=p.parse_args();store.init()
    try:
        revision,files=discover(a.revision,a.files)
        known=sum(f['size_bytes'] or 0 for f in files)
        parent=Path(store.db_path()).resolve().parent
        disk=shutil.disk_usage(parent)
        plan={'repository':REPO,'resolved_revision':revision,'judgment_files':len(files),'published_file_bytes':known,'unknown_file_sizes':sum(f['size_bytes'] is None for f in files),'docker_filesystem_free_gb':round(disk.free/1e9,2),'scope':'Court judgment Parquet files only. Excludes legislation, regulatory instruments and PDF-only tribunals.','embedding_calls':0,'files':files}
        print(json.dumps(plan,indent=2),flush=True)
        print('\nDataset files are compressed. Extracted text, full documents, FTS, WAL, metadata and backups require ADDITIONAL disk. This plan does not certify sufficient capacity or national-scale query performance.',flush=True)
        if not a.run:
            print('\nPLAN ONLY. Add --run to download and import every listed file. No sample row limit is used.',flush=True);return
        lock=(parent/'bulk-import.lock').open('a')
        try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: raise RuntimeError('Another bulk importer is already running. View /data/bulk-import.log instead of starting a second one.')
        if a.reserve_gb<1: raise ValueError('Reserve must be at least 1 GB.')
        cache_parent=Path(os.environ.get('HF_HOME',str(Path.home()/'.cache/huggingface')))
        cache_parent.mkdir(parents=True,exist_ok=True)
        ensure_lock(revision)
        with store.connect() as c:c.execute('INSERT OR REPLACE INTO bulk_import_runs VALUES(?,?)',(revision,json.dumps(plan)))
        for index,file in enumerate(files,1):
            free=min(shutil.disk_usage(parent).free,shutil.disk_usage(cache_parent).free)
            # Conservative per-file check, including a cached file's published size. Not a DB sizing formula.
            needed=(file['size_bytes'] or 0)+int(a.reserve_gb*1e9)
            if free<needed: raise RuntimeError(f'Not enough free Docker filesystem space before {file["filename"]}. Free additional space or move this deployment to a suitably sized host. Saved progress is retained.')
            print(f'\n[{index}/{len(files)}] Download/import: {file["filename"]}',flush=True)
            path=download_file(file['filename'],revision)
            # Use each source row's own court metadata; never force every file to Karnataka.
            ingest.ingest(path,revision,max_rows=None)
        print('\nAll selected source files imported. Keyword/phrase search can use them. Semantic coverage is still limited to the vectors you separately build or restore.',flush=True)
    except KeyboardInterrupt:
        print('\nStopped. Saved import batches and document reconstruction queue are retained; rerun the same command to resume.',file=sys.stderr);raise SystemExit(130)
    except Exception as error:
        message=str(error)
        for name in ('HF_TOKEN','GEMINI_API_KEY','BACKEND_API_TOKEN'):
            secret=os.environ.get(name)
            if secret:message=message.replace(secret,'[redacted]')
        print('\nBulk import paused: '+message,file=sys.stderr)
        print('No embedding calls were made by this command. Rerun after fixing the reported issue to resume saved progress.',file=sys.stderr)
        raise SystemExit(1)
if __name__=='__main__':main()
