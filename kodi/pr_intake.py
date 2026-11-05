"""Opt-in host polling of external PRs through the existing acceptance gate."""
import fcntl
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import tempfile

from . import github
from .github_app import environment
from .store import Store
from .submission import SubmissionBusy, submit_pr


def save(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2)+'\n')
    temporary.replace(path)


def checkout(root, repository, pull):
    """Create only new disposable clones; never reset an existing checkout."""
    target = Path(tempfile.mkdtemp(prefix='candidate-', dir=root))
    env = environment()
    def git(*args):
        result = subprocess.run(['git', '-c', 'core.hooksPath=/dev/null',
            '-c', 'credential.helper=', '-c', 'credential.helper=!gh auth git-credential',
            *args], cwd=target, env=env, capture_output=True, text=True, timeout=120)
        if result.returncode:
            raise RuntimeError('Intake checkout failed; inspect repository access and candidate')
        return result.stdout.strip()
    git('clone', '--no-checkout', '--no-tags', '--single-branch', '--branch',
        pull['headRefName'], 'https://github.com/'+repository+'.git', '.')
    git('checkout', pull['headRefName'])
    if git('rev-parse', 'HEAD') != pull['headRefOid']:
        raise SubmissionBusy('PR changed during checkout; rediscover its current head')
    return target


def submit(config, key, root, pull, requirements, report):
    from .coordinator import Coordinator
    candidate = checkout(root, config['repos'][key]['github_repo'], pull)
    try:
        coordinator = Coordinator(config)
    except sqlite3.OperationalError as error:
        if 'locked' in str(error).lower():
            raise SubmissionBusy('Coordinator database is busy; retry later') from None
        raise
    try:
        return dict(submit_pr(coordinator, key, candidate, pull['url'],
                             pull['title'], requirements, report))
    finally:
        coordinator.store.db.close()


def scan(config, key):
    repo = config['repos'].get(key, {})
    settings = repo.get('pr_intake', {})
    steps = settings.get('test_steps') if isinstance(settings, dict) else None
    if (repo.get('enabled') is not True or not isinstance(settings, dict)
            or settings.get('enabled') is not True or not isinstance(steps, list)
            or not steps or any(not isinstance(s, str) or not s.strip() for s in steps)):
        raise ValueError('Intake requires an enabled repository and operator-configured manual test steps')
    # Use the configured repository identity, never a path/URL supplied by a PR.
    repository = repo['github_repo']
    if len(repository.split('/')) != 2 or any(
            not part or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-' for c in part)
            or part in ('.', '..') for part in repository.split('/')):
        raise ValueError('Invalid configured GitHub repository')
    root = Path(config['state_dir'])/'pr-intake'/repository
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    results = []
    with (root/'intake.lock').open('a') as lock:
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: return [{'status': 'busy'}]
        pulls = json.loads(github.gh('pr', 'list', '--repo', repository, '--state', 'open',
            '--base', 'main', '--limit', '100', '--json',
            'number,url,title,body,isDraft,headRefName,headRefOid,headRepository,headRepositoryOwner'))
        if len(pulls) >= 100:
            raise RuntimeError('Intake discovery limit reached; inspect open PR volume')
        for pull in pulls:
            owner, name = repository.split('/')
            if (pull['isDraft'] or (pull.get('headRepository') or {}).get('name') != name
                    or (pull.get('headRepositoryOwner') or {}).get('login', '').lower() != owner.lower()):
                continue
            number = pull['number']
            if type(number) is not int or number < 1 or pull['url'] != f'https://github.com/{repository}/pull/{number}':
                raise ValueError('Unexpected pull request identity')
            path = root/(str(number)+'.json')
            prior = json.loads(path.read_text()) if path.exists() else {}
            store = Store(Path(config['state_dir'])/'kodi.sqlite', read_only=True)
            try:
                row = store.db.execute('SELECT * FROM jobs WHERE pr_url=?', (pull['url'],)).fetchone()
                tracked = dict(row) if row else None
            finally: store.db.close()
            # Never adopt manually submitted/native deliveries or replace an
            # uncertain in-flight attempt after a process crash.
            if tracked and not prior:
                results.append({'pr': number, 'status': 'already-tracked'})
                continue
            if prior.get('status') == 'submitting':
                results.append({'pr': number, 'status': 'interrupted-needs-inspection'})
                continue
            # Publication rewrites the body and can rebase the head. Neither is
            # a new author submission. CI failures remain coordinator-owned.
            if prior.get('status') == 'submitted' and pull['headRefOid'] in (
                    prior.get('sha'), prior.get('accepted_sha')):
                results.append({'pr': number, 'status': 'submitted'})
                continue
            requirements = (pull.get('body') or '').strip()
            identity = hashlib.sha256(json.dumps([pull['headRefOid'], pull['title'], requirements]).encode()).hexdigest()
            if (prior.get('identity') == identity and prior.get('status') != 'deferred'):
                results.append({'pr': number, 'status': prior['status']})
                continue
            if tracked and (tracked['state'] in ('done', 'cancelled') or
                    tracked['state'] in ('coding', 'qa', 'review', 'review_wait', 'publishing', 'merge_pending', 'merge_updating') or
                    (tracked['commit_sha'] == pull['headRefOid'] and tracked['requirements'] == requirements
                     and tracked['title'] == pull['title'] and tracked['state'] in ('validated', 'ci_pending', 'human_review'))):
                results.append({'pr': number, 'status': 'coordinator-owned'})
                continue
            observation = {'identity': identity, 'sha': pull['headRefOid'], 'status': 'submitting'}
            if not requirements:
                observation['status'] = 'missing-requirements'
                save(path, observation)
                results.append({'pr': number, 'status': observation['status']})
                continue
            save(path, observation)
            report = {'summary': 'Automatically submitted external PR for independent validation.\n'+pull['title'],
                      'test_steps': steps}
            try:
                job = submit(config, key, root, pull, requirements, report)
                observation.update(status='submitted', job=job['id'], accepted_sha=job.get('commit_sha'))
            except SubmissionBusy:
                observation['status'] = 'deferred'
            except Exception:
                # Do not retry rejected QA/review, uncertain writes or arbitrary
                # operational errors. Preserve evidence and show a stable failure.
                observation['status'] = 'failed-needs-inspection'
            save(path, observation)
            results.append({'pr': number, 'status': observation['status']})
    return results
