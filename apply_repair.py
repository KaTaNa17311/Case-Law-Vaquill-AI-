#!/usr/bin/env python3
"""Install source repairs into an existing project; never touch keys or runtime data."""
import argparse
import datetime
import os
from pathlib import Path
import shutil
import subprocess
import json
import sys


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('target',type=Path,help='Existing advohq-case-research folder')
    p.add_argument('--dry-run',action='store_true')
    p.add_argument('--recover-env',action='store_true',help='If .env is missing, recover selected existing settings from the running named API container without displaying keys')
    a=p.parse_args();source=Path(__file__).resolve().parent;target=a.target.expanduser().resolve()
    if source==target:p.error('Unzip the repair separately and choose your existing project as the target.')
    if not (target/'backend/app.py').is_file() or not (target/'compose.yaml').is_file():p.error('Target is not an existing AdvoHQ project.')
    files=[]
    for folder in ['backend','frontend','tools','scripts','worker']:
        for file in (source/folder).rglob('*'):
            if file.is_file() and '__pycache__' not in file.parts and '.pytest_cache' not in file.parts:
                files.append(file.relative_to(source))
    for name in ['compose.yaml','package.json','README.md','.env.example','.dockerignore']:
        if (source/name).is_file():files.append(Path(name))
    # Fixed allowlist: .env, imports/, databases and Docker volume contents cannot be included.
    assert all('.env'!=str(path) and path.parts[0] not in ('imports','.git') for path in files)
    changes=[path for path in sorted(files) if not (target/path).exists() or (source/path).read_bytes()!=(target/path).read_bytes()]
    if a.dry_run:
        print('Would update',len(changes),'source files:')
        for path in changes:print(path)
        print('No files changed.');return
    if a.recover_env and not (target/'.env').exists():
        try:
            raw=subprocess.check_output(['docker','inspect','advohq-case-research-api-1','--format','{{json .Config.Env}}'],text=True,stderr=subprocess.PIPE)
            values=dict(item.split('=',1) for item in json.loads(raw) if '=' in item)
            allowed={line.split('=',1)[0] for line in (source/'.env.example').read_text().splitlines() if '=' in line and not line.lstrip().startswith('#')}
            lines=[]
            if not values.get('BACKEND_API_TOKEN'):p.error('The running container has no backend token to recover.')
            for key in sorted(allowed):
                if key not in values:continue
                value=values[key]
                if any(character in value for character in ('\n','\r','\x00','"','$','\\')):
                    p.error('Setting '+key+' needs manual .env recovery; no values were displayed or written.')
                lines.append(key+'="'+value+'"')
            with os.fdopen(os.open(target/'.env',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'w') as handle:
                handle.write('\n'.join(lines)+'\n')
            print('Missing .env recovered from the running container. Keys were not displayed.')
        except (subprocess.SubprocessError,ValueError,OSError):p.error('Could not recover .env from the running container. Keep that container running and check Docker access.')
    if not changes:print('Repair already installed. Nothing changed.');return
    timestamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup=target/'repair-backups'/timestamp
    for path in changes:
        existing=target/path
        if existing.is_symlink():p.error('Refusing to overwrite symbolic link: '+str(path))
        if existing.exists():
            (backup/path).parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(existing,backup/path)
    for path in changes:
        dest=target/path;dest.parent.mkdir(parents=True,exist_ok=True)
        temporary=dest.with_name(dest.name+'.repair-tmp')
        temporary.write_bytes((source/path).read_bytes());temporary.chmod(0o755 if dest.suffix=='.sh' else 0o644);os.replace(temporary,dest)
    print('Installed',len(changes),'source repairs.')
    print('Backups:',backup)
    print('Your .env, imported data and embeddings were not touched.')
    print('Next: cd to the existing project and run docker compose up --build -d --force-recreate api')
if __name__=='__main__':main()
