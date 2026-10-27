#!/usr/bin/env python3
"""Fixed QA entry point on an operator-managed independent host. Never register a public self-hosted runner.

GitHub Actions runs ordinary CI on GitHub-hosted machines. Kodi independently
checks the same commit here, in an offline disposable container.
"""
import base64
import json
import os
import posixpath
import re
import shutil
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
            validate_archive_options(value)
            result[name]=value
    return result



DEFAULT_ARCHIVE_BYTES = 50 * 1024 * 1024
MAX_ARCHIVE_BYTES = 512 * 1024 * 1024


def validate_archive_options(profile):
    for key in ('archive_max_bytes', 'expanded_max_bytes'):
        value=profile.get(key, DEFAULT_ARCHIVE_BYTES)
        if type(value) is not int or not 1 <= value <= MAX_ARCHIVE_BYTES:
            raise ValueError('Invalid QA archive bound')
    memory=profile.get('memory_mb',2048)
    if type(memory) is not int or not 256 <= memory <= 8192:
        raise ValueError('Invalid operator QA memory budget')
    if type(profile.get('materialize_internal_links',False)) is not bool:
        raise ValueError('Invalid QA link option')


def extract_archive(archive, destination, profile):
    """Expand only committed regular bytes, with no filesystem link creation."""
    validate_archive_options(profile)
    limit=profile.get('archive_max_bytes',DEFAULT_ARCHIVE_BYTES)
    expanded_limit=profile.get('expanded_max_bytes',DEFAULT_ARCHIVE_BYTES)
    if not archive.is_file() or archive.stat().st_size > limit:
        raise ValueError('Missing or oversized source archive')
    with tarfile.open(archive) as source:
        entries={}
        directories={''}
        for entry in source:
            name=entry.name.rstrip('/')
            parts=name.split('/')
            if (not name or name.startswith('/') or any(p in ('','..','.') for p in parts)
                    or name in entries or len(entries)>=100000):
                raise ValueError('Unsafe archive path')
            if entry.size < 0:
                raise ValueError('Invalid archive entry size')
            if not (entry.isfile() or entry.isdir() or entry.issym()):
                raise ValueError('Unsupported archive entry')
            if entry.issym() and not profile.get('materialize_internal_links',False):
                raise ValueError('Archive links require an operator profile')
            entries[name]=entry
            for i in range(1,len(parts)):
                directories.add('/'.join(parts[:i]))
            if entry.isdir(): directories.add(name)
        for name in directories:
            if name in entries and not entries[name].isdir():
                raise ValueError('Archive entry shadows a directory')

        def resolve(name, seen):
            if name in seen or len(seen)>=64:
                raise ValueError('Archive link cycle')
            entry=entries.get(name)
            if entry is None:
                if name in directories: return name
                raise ValueError('Dangling archive link')
            if not entry.issym(): return name
            if entry.linkname.startswith('/'):
                raise ValueError('Absolute archive link')
            target=posixpath.normpath(posixpath.join(posixpath.dirname(name),entry.linkname))
            if target in ('','.') or target=='..' or target.startswith('../'):
                raise ValueError('Archive link escapes or targets root')
            # Resolve a link in any target prefix without traversing the host FS.
            parts=target.split('/')
            for i in range(1,len(parts)):
                prefix='/'.join(parts[:i])
                candidate=entries.get(prefix)
                if candidate and candidate.issym():
                    target=posixpath.join(resolve(prefix,seen|{name}),*parts[i:])
            return resolve(target,seen|{name})

        children={name:[] for name in directories}
        for name in set(entries)|directories:
            if name: children.setdefault(posixpath.dirname(name),[]).append(name)
        planned={}
        total=0

        def expand(origin, output, ancestors):
            nonlocal total
            target=resolve(origin,set())
            if target in ancestors:
                raise ValueError('Archive directory link cycle')
            entry=entries.get(target)
            if target in directories:
                planned[output]=None
                for child in sorted(children.get(target,[])):
                    expand(child,posixpath.join(output,posixpath.basename(child)),ancestors|{target})
            else:
                total+=entry.size
                if total>expanded_limit: raise ValueError('Expanded source archive exceeds budget')
                planned[output]=entry
            if len(planned)>100000: raise ValueError('Expanded source entry budget exceeded')

        for name in sorted(children['']): expand(name,name,set())
        # Validation is complete before any source bytes are written.
        if destination.is_symlink() or (destination.exists() and any(destination.iterdir())):
            raise ValueError('QA extraction needs an empty destination')
        destination.mkdir(parents=True,exist_ok=True)
        for name,entry in sorted(planned.items()):
            path=destination/name
            if entry is None:
                path.mkdir(parents=True,exist_ok=True)
            else:
                path.parent.mkdir(parents=True,exist_ok=True)
                with source.extractfile(entry) as data, path.open('xb') as output:
                    shutil.copyfileobj(data,output)
                path.chmod(0o755 if entry.mode & 0o111 else 0o644)

def failure_signal(result, name):
    if result.returncode:
        # Exit 137 alone also means SIGKILL; require Docker's OOM event.
        try:
            inspected = subprocess.run(
                ['docker', 'inspect', '--format', '{{.State.OOMKilled}}', name],
                text=True, capture_output=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            inspected = None
        if inspected is not None and inspected.returncode == 0 and inspected.stdout.strip() == 'true':
            return 'oom_event'
        if any(marker in result.stdout + result.stderr
               for marker in ('AssertionError:', 'FAILED (failures=')):
            return 'test_diagnostic'
    return 'unknown'


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
    if not archive.is_file() or archive.stat().st_size > profile.get('archive_max_bytes',DEFAULT_ARCHIVE_BYTES):
        raise ValueError('Missing or oversized source archive')
    name = 'kodi-qa-'+sha[:12]
    try:
        with tempfile.TemporaryDirectory(prefix='job-',dir=ROOT) as temporary:
            workspace = Path(temporary)
            extract_archive(archive,workspace,profile)
            os.chmod(workspace, 0o755)
            result = subprocess.run([
                'docker','run','--name',name,'--network','none','--cpus','1',
                '--memory',str(profile.get('memory_mb',2048))+'m',
                '--memory-swap',str(profile.get('memory_mb',2048))+'m','--pids-limit','128','--read-only',
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
                              'failure_signal':failure_signal(result, name),
                              'source_export':('internal_links_materialized' if profile.get('materialize_internal_links',False) else 'regular_files'),
                              'log':(result.stdout+result.stderr)[-24000:]}))
    except subprocess.TimeoutExpired:
        print(json.dumps({'sha':sha,'passed':False,'failure_signal':'execution_deadline','log':'Independent QA exceeded ten minutes'}))
    finally:
        subprocess.run(['docker','rm','-f',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        archive.unlink(missing_ok=True)


if __name__=='__main__': main()
