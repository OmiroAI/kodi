"""Bounded recovery in disposable workers; publisher access stays on the host."""
import shutil
import json
import hashlib

from .delivery import author_contract, context, report_fields, report_repair_eligible, rejection
import subprocess
import time
from pathlib import Path

from .policy import changed_paths_safe
from .runner import git, run_worker
from .profiles import author_config


from .failures import DeliveryFailure, execute


class TransientError(DeliveryFailure):
    """A positively identified temporary transport/provider failure."""
    def __init__(self, message):
        super().__init__('provider_outage')


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


def retry(store, job, action, candidate, operation, directory=None, recovery_inputs=None):
    def guarded():
        if store.invocation:
            with store.write(job): pass
        return operation()
    return execute(store, job, action, candidate, guarded, recovery_inputs)


def repair_rebase(config, store, job, checkout, base):
    """Keep the original and resolve only conflicted files in an isolated copy."""
    from .routing import conservative_config
    config=author_config(conservative_config(config),store,job['id'],recovery=True)
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
            prompt = rebase_prompt(store.delivery_contract(job['id']), job, paths)
            step_dir=directory/('step-'+str(step));step_dir.mkdir(mode=0o700)
            retry(store,job['id'],'rebase-transport',key+':'+str(number)+':'+str(step),
                lambda:run_worker(config, repair, step_dir, prompt, 'author', 'codex',
                    on_usage_event=lambda u,s,m:store.usage(job['id'],'codex',(config.get('codex_profile') or config['selected_coding_profile'])['model'] or 'unknown',u,s,**m),usage_phase='rebase_conflict_repair',
                    context_revision=base),
                step_dir/'author-evidence')
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
    from .routing import conservative_config
    config=author_config(conservative_config(config),store,job['id'],recovery=True)
    number=store.reserve_recovery(job['id'],'base-repair',job['commit_sha'],2)
    key=job['commit_sha'][:12]
    directory=checkout.parent/('validation-recovery-'+key+'-'+str(number))
    directory.mkdir(mode=0o700)
    repair=directory/'checkout'
    shutil.copytree(checkout,repair,symlinks=True)
    prompt = candidate_repair_prompt(store.delivery_contract(job['id']), job, feedback)
    author,_,_=retry(store,job['id'],'base-repair-transport',key+':'+str(number),
        lambda:run_worker(config,repair,directory,prompt,'author','codex',
            on_usage_event=lambda u,s,m:store.usage(job['id'],'codex',(config.get('codex_profile') or config['selected_coding_profile'])['model'] or 'unknown',u,s,**m),usage_phase='rebased_candidate_repair'),
        directory/'author-evidence')
    author = reconcile_report(config, store, job, repair, directory, author)
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


def checkout_identity(checkout):
    paths = git(checkout, 'ls-files', '--modified', '--others', '--exclude-standard', '-z').split('\0')
    changed_paths_safe([name for name in paths if name])
    digest = hashlib.sha256()
    digest.update(git(checkout, 'rev-parse', 'HEAD').encode())
    digest.update(git(checkout, 'diff', '--binary', 'HEAD').encode())
    for name in sorted(set(paths) - {''}):
        path = checkout / name
        digest.update(name.encode())
        digest.update(str(path.lstat().st_mode).encode() if path.exists() or path.is_symlink() else b'deleted')
        if path.is_symlink():
            digest.update(str(path.readlink()).encode())
        elif path.is_file():
            digest.update(path.read_bytes())
    return digest.hexdigest()


def reconcile_report(config, store, job, checkout, directory, author):
    """One durable, read-only report invocation; never an acceptance verdict."""
    from .failures import ReportFailure
    contract = store.delivery_contract(job['id'])
    if not contract:
        return author
    fields = report_fields(contract, author)
    if not fields or (isinstance(author, dict) and author.get('no_change') is True):
        return author
    store.evidence(job['id'], job['commit_sha'] or '', 'report_invalid', {'fields': fields, 'author': author})
    if contract['task_type'] != 'IMPLEMENTATION':
        raise ReportFailure(fields)
    changed = git(checkout, 'status', '--porcelain')
    if not report_repair_eligible(contract, author):
        text = json.dumps(author).lower()
        if 'permission' in text:
            raise DeliveryFailure('worker_permission_request')
        if any(term in text for term in ('missing', 'needs_input', 'clarif')):
            raise DeliveryFailure('missing_business_input')
        raise DeliveryFailure('implementation_incomplete')
    if not changed:
        raise DeliveryFailure('implementation_incomplete')
    current = store.get(job['id'])
    if current['requirements'] != job['requirements'] or current['requirements'] != contract['requirements']:
        raise ReportFailure(fields)
    identity = checkout_identity(checkout)
    snapshot = store.snapshot(job['id'])
    try:
        # Job-wide reservation: changed candidates and transport retries never reset it.
        with store.write(job['id']):
            owner = store.invocation_status(job['id'])
            if owner and owner['status'] == 'running' and owner['id'] != store.invocation:
                raise ReportFailure(fields)
            store.reserve_recovery(job['id'], 'author-report', 'job', 1)
            store.evidence(job['id'], identity, 'report_repair_reserved', {
                'fields': fields, 'contract': contract, 'snapshot': snapshot, 'prior_report': author})
        prompt = (author_contract(contract) + context(contract) +
            '\nRead-only report repair. Inspect retained source and tests. Do not edit any file or Git metadata. '
            'Supply only evidence verified against existing changes; report missing business input or permission blockers honestly.' +
            '\nOriginal requirements:\n' + job['requirements'] +
            '\nPrior report:\n' + json.dumps(author) + '\nExisting changes:\n' + changed)
        repaired, _, _ = run_worker(config, checkout, directory, prompt, 'report', 'codex',
            usage_phase='author_report_repair',
            on_usage_event=lambda u, s, m: store.usage(job['id'], 'codex',
                (config.get('codex_profile') or config.get('selected_coding_profile', {})).get('model') or 'unknown', u, s, **m))
        if (checkout_identity(checkout) != identity or store.snapshot(job['id']) != snapshot
                or store.delivery_contract(job['id']) != contract):
            raise ReportFailure(fields)
        unresolved = report_fields(contract, repaired)
        store.evidence(job['id'], identity, 'report_repair_result', {'author': repaired, 'fields': unresolved})
        if unresolved or rejection(contract, repaired):
            raise ReportFailure(unresolved or fields)
        return repaired
    except ReportFailure:
        raise
    except Exception as error:
        raise ReportFailure(fields) from error


def rebase_prompt(contract, job, paths):
    return ('Resolve only the listed rebase conflicts, preserving both the task and '
                'upstream behavior. Edit files only: no Git writes, commits, publication, '
                'deployment, credentials or permission changes. Do not modify any other file. ' +
                author_contract(contract) + context(contract) + '\nTask: '+
                job['requirements']+'\nConflicted paths:\n'+'\n'.join(paths))


def candidate_repair_prompt(contract, job, feedback):
    return ('Repair the rebased candidate using the independent findings below. '
        'Edit files only: no Git writes, deployment, publication, credentials, '
        'workflow, authentication, migration or permission changes. ' +
        author_contract(contract) + context(contract) + '\nRequirements:\n'+
        job['requirements']+'\nIndependent findings:\n'+feedback)
