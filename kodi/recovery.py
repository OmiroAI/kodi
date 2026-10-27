"""Bounded recovery in disposable workers; publisher access stays on the host."""
import shutil
import subprocess
import time
from pathlib import Path

from .policy import changed_paths_safe
from .runner import git, run_worker


class TransientError(RuntimeError):
    """A positively identified temporary transport/provider failure."""


def transient(message):
    text = message.lower()
    if any(word in text for word in ('401', '403', 'unauthorized', 'refresh_token',
                                     'token_expired', 'sign in', 'quota', 'billing')):
        return False
    if 'request failed:' in text and ': eof' in text:
        return True
    return any(word in text for word in ('429', '502', '503', '504', 'rate limit',
        'connection reset', 'connection refused', 'temporary failure', 'timed out',
        'service unavailable', 'connection closed unexpectedly'))


def retry(store, job, action, candidate, operation, directory=None):
    while True:
        try:
            return operation()
        except TransientError:
            number = store.reserve_recovery(job, action, candidate, 2)
            if directory and Path(directory).exists():
                import hashlib
                key=hashlib.sha256(candidate.encode()).hexdigest()[:12]
                archive = Path(directory).with_name(Path(directory).name+'-retry-'+key+'-'+str(number))
                if not archive.exists():
                    shutil.copytree(directory, archive, symlinks=True)
            time.sleep((2, 10)[number-1])


def repair_rebase(config, store, job, checkout, base):
    """Keep the original and resolve only conflicted files in an isolated copy."""
    number = store.reserve_recovery(job['id'], 'rebase', job['commit_sha'], 2)
    key=job['commit_sha'][:12]
    directory = checkout.parent/('rebase-recovery-'+key+'-'+str(number))
    directory.mkdir(mode=0o700)
    repair = directory/'checkout'
    shutil.copytree(checkout, repair, symlinks=True)
    # Preserve the old, clean candidate separately. Never reset a canonical clone.
    git(checkout, '-c', 'core.hooksPath=/dev/null', 'rebase', '--abort')
    try:
        for step in range(2):
            paths = git(repair, 'diff', '--name-only', '--diff-filter=U', '-z').split('\0')
            paths = [p for p in paths if p]
            if not paths:
                raise RuntimeError('Rebase failed without resolvable content conflicts')
            changed_paths_safe(paths)
            if any((repair/p).is_symlink() for p in paths):
                raise RuntimeError('Symlink conflict requires operator inspection')
            if any((repair/p).is_file() and b'\0' in (repair/p).read_bytes() for p in paths):
                raise RuntimeError('Binary conflict requires operator inspection')
            prompt = ('Resolve only the listed rebase conflicts, preserving both the task and '
                'upstream behavior. Edit files only: no Git writes, commits, publication, '
                'deployment, credentials or permission changes. Do not modify any other file. '
                'Return JSON with summary, test_steps and acceptance_evidence.\nTask: '+
                job['requirements']+'\nConflicted paths:\n'+'\n'.join(paths))
            step_dir=directory/('step-'+str(step));step_dir.mkdir(mode=0o700)
            retry(store,job['id'],'rebase-transport',key+':'+str(number)+':'+str(step),
                lambda:run_worker(config, repair, step_dir, prompt, 'author', 'codex',
                    on_usage_event=lambda u,s,m:store.usage(job['id'],'codex','rebase recovery',u,s,**m),usage_phase='recovery'),
                step_dir/'author-output')
            modified = set(git(repair, 'diff', '--name-only', '-z').split('\0'))-{''}
            untracked = git(repair, 'ls-files', '--others', '--exclude-standard')
            if not modified <= set(paths) or untracked:
                raise RuntimeError('Conflict worker changed files outside its assigned conflicts')
            if any((repair/p).is_symlink() for p in paths):
                raise RuntimeError('Conflict worker introduced a symlink')
            for path in paths:
                if (repair/path).is_file() and any(line.startswith((b'<<<<<<< ', b'=======', b'>>>>>>> '))
                        for line in (repair/path).read_bytes().splitlines()):
                    raise RuntimeError('Conflict markers remain after repair')
            git(repair, 'add', '--', *paths)
            result = subprocess.run(['git','-c','core.hooksPath=/dev/null','-c','core.editor=true',
                '-c','user.name=Kodi','-c','user.email=kodi@example.invalid','rebase','--continue'],
                cwd=repair, capture_output=True, timeout=120)
            if result.returncode == 0:
                if git(repair, 'status', '--porcelain'):
                    raise RuntimeError('Repaired checkout is not clean')
                git(repair, 'merge-base', '--is-ancestor', base, 'HEAD')
                checkout.rename(directory/'original-checkout')
                repair.rename(checkout)
                return
        raise RuntimeError('Rebase conflict repair budget exhausted')
    except Exception:
        # The clean original and failed repair remain available for inspection.
        raise


def repair_candidate(config, store, job, checkout, feedback):
    number=store.reserve_recovery(job['id'],'base-repair',job['commit_sha'],2)
    key=job['commit_sha'][:12]
    directory=checkout.parent/('validation-recovery-'+key+'-'+str(number))
    directory.mkdir(mode=0o700)
    repair=directory/'checkout'
    shutil.copytree(checkout,repair,symlinks=True)
    prompt=('Repair the rebased candidate using the independent findings below. '
        'Edit files only: no Git writes, deployment, publication, credentials, '
        'workflow, authentication, migration or permission changes. Return JSON '
        'with summary, nonempty test_steps and acceptance_evidence.\nRequirements:\n'+
        job['requirements']+'\nIndependent findings:\n'+feedback)
    author,_,_=retry(store,job['id'],'base-repair-transport',key+':'+str(number),
        lambda:run_worker(config,repair,directory,prompt,'author','codex',
            on_usage_event=lambda u,s,m:store.usage(job['id'],'codex','base repair',u,s,**m),usage_phase='recovery'),
        directory/'author-output')
    if not isinstance(author,dict) or not isinstance(author.get('test_steps'),list) or not author['test_steps']:
        raise RuntimeError('Repair author must supply manual testing instructions')
    paths=[p for p in git(repair,'ls-files','--modified','--others','--exclude-standard','-z').split('\0') if p]
    if not paths: raise RuntimeError('Repair author made no changes')
    changed_paths_safe(paths)
    if any((repair/p).is_symlink() for p in paths):
        raise RuntimeError('Repair author introduced a symlink')
    git(repair,'add','--',*paths)
    git(repair,'-c','user.name=Kodi','-c','user.email=kodi@example.invalid',
        'commit','-m','Repair independent findings after base update')
    checkout.rename(directory/'original-checkout');repair.rename(checkout)
    return author
