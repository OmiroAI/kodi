#!/usr/bin/env python3
"""Fixed QA entry point on an operator-managed independent host. Never register a public self-hosted runner.

GitHub Actions runs ordinary CI on GitHub-hosted machines. Kodi independently
checks the same commit here, in an offline disposable container.
"""
import base64
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path.home()/'.local/state/omiro-qa'
COMMANDS = {
    'system': 'python -m unittest discover -s tests -v',
    'repository': 'python .kodi/qa.py',
    'astro': 'cmp package-lock.json /opt/astro/package-lock.json && cp -a /opt/astro/node_modules /workspace/node_modules && python .kodi/qa.py',
}


def profiles(path=None):
    """Additional commands/images are operator configuration, never issue input."""
    path=Path(path) if path else Path.home()/'.config/omiro-dev/qa-profiles.json'
    result={name:{'command':command,'image':'omiro-qa:astro' if name=='astro' else 'omiro-qa:phase1'}
            for name,command in COMMANDS.items()}
    if path.exists():
        extra=json.loads(path.read_text())
        if not isinstance(extra,dict):raise ValueError('Invalid private QA profiles')
        for name,value in extra.items():
            if not re.fullmatch('[a-z0-9_-]+',name) or not isinstance(value,dict):
                raise ValueError('Invalid QA profile')
            if not isinstance(value.get('command'),str) or not value['command']:
                raise ValueError('Missing QA command')
            if not isinstance(value.get('image'),str) or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9_.:/@-]+',value['image']):
                raise ValueError('Invalid QA image')
            result[name]=value
    return result


def main():
    data = json.loads(base64.b64decode(sys.argv[1]))
    sha = data['sha']
    if not re.fullmatch('[a-f0-9]{40}', sha): raise ValueError('Invalid commit SHA')
    suite = data['suite']
    available=profiles()
    if suite not in available: raise ValueError('Unknown QA suite')
    profile=available[suite]
    archive = ROOT/'incoming'/(sha+'.tar')
    ROOT.mkdir(parents=True, exist_ok=True)
    if not archive.is_file() or archive.stat().st_size > 50*1024*1024:
        raise ValueError('Missing or oversized source archive')
    name = 'kodi-qa-'+sha[:12]
    try:
        with tempfile.TemporaryDirectory(prefix='job-',dir=ROOT) as temporary:
            workspace = Path(temporary)
            with tarfile.open(archive) as tar:
                for entry in tar:
                    parts = Path(entry.name).parts
                    if entry.name.startswith('/') or '..' in parts or entry.issym() or entry.islnk():
                        raise ValueError('Unsafe archive entry')
                    if not (entry.isfile() or entry.isdir()): raise ValueError('Unsupported archive entry')
                tar.extractall(workspace, filter='data')
            os.chmod(workspace, 0o755)
            result = subprocess.run([
                'docker','run','--rm','--name',name,'--network','none','--cpus','1',
                '--memory','2g','--memory-swap','2g','--pids-limit','128','--read-only',
                '--cap-drop','ALL','--security-opt','no-new-privileges',
                '--user',str(os.getuid())+':'+str(os.getgid()),
                '--tmpfs','/tmp:rw,nosuid,nodev,size=256m',
                '--mount',f'type=bind,src={workspace},dst=/workspace',
                '-e','PYTHONDONTWRITEBYTECODE=1','-e','HOME=/tmp',
                '-e','ASTRO_TELEMETRY_DISABLED=1',
                profile['image'],'sh','-c',profile['command']],
                text=True,capture_output=True,timeout=600)
            print(json.dumps({'sha':sha,'passed':result.returncode==0,
                              'returncode':result.returncode,
                              'log':(result.stdout+result.stderr)[-24000:]}))
    except subprocess.TimeoutExpired:
        print(json.dumps({'sha':sha,'passed':False,'log':'Independent QA exceeded ten minutes'}))
    finally:
        subprocess.run(['docker','rm','-f',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        archive.unlink(missing_ok=True)


if __name__=='__main__': main()
