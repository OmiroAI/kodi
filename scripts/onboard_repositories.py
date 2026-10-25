#!/usr/bin/env python3
"""Operator-only catalog setup. Does not merge, deploy, or grant App permissions."""
import argparse
import json
import subprocess
import re
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from kodi import github,github_app
from kodi.source import refresh_source


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--catalog',type=Path,required=True)
    parser.add_argument('--reviewer',required=True)
    parser.add_argument('--workspace',type=Path,help='Sync a separate ChatGPT development workspace after registration')
    args=parser.parse_args()
    config=json.loads(args.config.read_text());catalog=json.loads(args.catalog.read_text())
    if any(not re.fullmatch('[A-Za-z0-9_-]+',key) or not re.fullmatch(
            '[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',r['github_repo']) or
            any(part in ('.','..') for part in r['github_repo'].split('/'))
            for key,r in catalog.items()):
        raise ValueError('Invalid operator catalog')
    names=[r['github_repo'].split('/')[1] for r in catalog.values()]
    organizations={r['github_repo'].split('/')[0] for r in catalog.values()}
    if len(organizations)!=1:raise ValueError('One organization per operator catalog')
    scope=github_app.CONFIG.parent/'repositories.json'
    # This is a non-secret allowlist. App grants must already exist at GitHub.
    existing=github_app.repository_scope(json.loads(github_app.CONFIG.read_text()))
    previous=scope.read_bytes() if scope.exists() else None
    from kodi.workspace import atomic
    try:
        atomic(scope,json.dumps(list(dict.fromkeys(existing+names)),indent=2)+'\n')
        github_app._cached=None
        granted={r['full_name'].lower() for page in github.api_pages('installation/repositories?per_page=100') for r in page['repositories']}
        missing=[r['github_repo'] for r in catalog.values() if r['github_repo'].lower() not in granted]
        if missing:raise RuntimeError('GitHub App installation access required for '+', '.join(missing))
    except Exception:
        # A failed token request must not poison the live coordinator's scope.
        if previous is None:scope.unlink(missing_ok=True)
        else:atomic(scope,previous.decode())
        github_app._cached=None
        raise
    root=Path(config['state_dir'])/'repos';root.mkdir(exist_ok=True)
    for key,profile in catalog.items():
        name=profile['github_repo'].split('/')[1];source=root/name
        if not source.exists():
            result=subprocess.run(['git','-c','core.hooksPath=/dev/null','-c','credential.helper=',
                '-c','credential.helper=!gh auth git-credential','clone','--no-recurse-submodules',
                'https://github.com/'+profile['github_repo']+'.git',str(source)],
                env=github_app.environment(),capture_output=True)
            if result.returncode:raise RuntimeError('Canonical clone failed: '+name)
        entry={**config['repos'].get(key,{}),**profile,'source':str(source),'reviewers':[args.reviewer]}
        refresh_source(entry)
        if key not in config['repos']:
            # Onboarding is complete only after its checked QA files are on main.
            entry['enabled']=False
            entry['auto_merge']=False
        config['repos'][key]=entry
        print(name, 'enabled' if entry.get('enabled',True) else 'awaiting onboarding merge')
    config['reviewers']=list(dict.fromkeys(config.get('reviewers',[])+[args.reviewer]))
    config['require_explicit_repo']=True
    pending=args.config.with_suffix('.next.json');pending.write_text(json.dumps(config,indent=2)+'\n')
    pending.chmod(0o600);pending.replace(args.config)
    mounts=['services:','  dashboard:','    volumes:']
    for entry in config['repos'].values():
        name=entry['github_repo'].split('/')[1]
        mounts.extend(['      - type: bind','        source: '+entry['source'],
                       '        target: /repositories/'+name,'        read_only: true'])
    (args.config.parent/'dashboard-repositories.compose.yaml').write_text('\n'.join(mounts)+'\n')
    if args.workspace:
        from kodi.workspace import sync
        sync(config,args.workspace)
    print('Catalog saved. Recreate the dashboard with the generated read-only mount override.')
    print('Restart Kodi after the coordinator change has passed human merge approval.')


if __name__=='__main__':main()
