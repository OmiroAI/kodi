"""Host-only intake for a committed workspace PR; workers never call this path."""
import fcntl
from contextlib import contextmanager
import json
from pathlib import Path
import re
import subprocess

from . import github
from .policy import changed_paths_safe
from .recovery import retry
from .runner import git, qa
from .store import Store, StaleClaim


def validate_pull(repository, pull, branch, sha):
    owner, name = repository.split('/')
    if (pull['state'] != 'OPEN' or pull['baseRefName'] != 'main'
            or pull['headRefName'] != branch or pull['headRefOid'] != sha
            or (pull.get('headRepository') or {}).get('name') != name
            or (pull.get('headRepositoryOwner') or {}).get('login', '').lower() != owner.lower()):
        raise RuntimeError('PR must be open, target main, and match the configured repository and workspace head')
    if pull.get('autoMergeRequest'):
        raise RuntimeError('Disable the existing auto-merge request before submitting the PR')


def submit_pr(coordinator, repo_key, checkout, pr_url, title, requirements, author):
    """Validate an existing PR under the same delivery lock as daemon ticks."""
    root = Path(coordinator.config['state_dir'])
    with (root / 'delivery.lock').open('a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Coordinator is busy; retry submission after the current delivery') from None
        return _submit(coordinator, repo_key, Path(checkout).resolve(), pr_url,
                       title, requirements, author, handle)


@contextmanager
def independent_work(c, job, lock):
    """Retain durable ownership while the submission CLI releases delivery.lock."""
    original = c.store
    token = original.begin_invocation(job, 'submission')
    if not token: raise RuntimeError('Execution slot is already owned')
    worker = Store(Path(c.config['state_dir'])/'kodi.sqlite', token)
    c.store = worker
    status = 'failed'
    fcntl.flock(lock, fcntl.LOCK_UN)
    try:
        yield
        with worker.write(job): pass
        status = 'complete'
    except StaleClaim:
        status = 'stale'
        raise
    finally:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            if status == 'complete':
                with worker.write(job): pass
        except StaleClaim:
            status = 'stale'
            raise
        finally:
            with worker.db:
                worker.db.execute('UPDATE invocations SET status=? WHERE id=?', (status, token))
            c.store = original
            worker.db.close()


def _submit(c, repo_key, workspace, pr_url, title, requirements, author, delivery_lock):
    managed = (Path(c.config['state_dir']) / 'jobs').resolve()
    if workspace == managed or managed in workspace.parents:
        raise RuntimeError('Submit the development workspace, not a coordinator-owned checkout')
    repo = c.config['repos'].get(repo_key)
    if not repo or repo.get('enabled') is not True or not repo.get('github_repo'):
        raise ValueError('Select an explicitly enabled repository from the operator catalog')
    match = re.fullmatch(r'https://github\.com/([^/]+/[^/]+)/pull/([1-9][0-9]*)', pr_url)
    if not match or match[1].lower() != repo['github_repo'].lower():
        raise ValueError('PR URL does not match the selected repository')
    if (not isinstance(author, dict) or not isinstance(author.get('summary'), str)
            or not author['summary'].strip()
            or not isinstance(author.get('test_steps'), list) or not author['test_steps']
            or any(not isinstance(step, str) or not step.strip() for step in author['test_steps'])
            or not title.strip() or not requirements.strip()):
        raise ValueError('Supply a summary, nonempty manual test steps, title and requirements')
    branch = git(workspace, 'symbolic-ref', '--short', 'HEAD')
    if branch == 'main' or git(workspace, 'status', '--porcelain', '--untracked-files=all'):
        raise RuntimeError('Submit a clean, committed task branch rather than main')
    sha = git(workspace, 'rev-parse', 'HEAD')
    if not github.authenticated():
        raise RuntimeError('Dedicated GitHub App authentication is required')
    pull = github.pull(pr_url)
    validate_pull(repo['github_repo'], pull, branch, sha)
    if github.remote_head(repo['github_repo'], branch) != sha:
        raise RuntimeError('Push the workspace commit to the PR branch before submitting')
    active = c.store.db.execute("""SELECT 1 FROM jobs WHERE state IN
        ('coding','qa','review','publishing','merge_pending','merge_updating') OR
        (state NOT IN ('done','cancelled') AND EXISTS
         (SELECT 1 FROM evidence WHERE job=jobs.id AND kind='merge_maintenance'))""").fetchone()
    if active:
        raise RuntimeError('An active delivery or merge owns the serialized slot; retry later')
    for tracked in c.store.db.execute("SELECT * FROM jobs WHERE repo=? AND state NOT IN ('done','cancelled')",
                                     (repo_key,)):
        if c.job_branch(tracked) == branch and tracked['pr_url'] != pr_url:
            raise RuntimeError('This branch is already owned by another delivery; use that job')
    existing = c.store.db.execute('SELECT * FROM jobs WHERE pr_url=? OR external_id=?',
                                (pr_url, pr_url)).fetchone()
    if existing:
        if existing['repo'] != repo_key or existing['branch'] != branch:
            raise RuntimeError('PR is already tracked by another delivery; use that job')
        if existing['state'] in ('done', 'cancelled'):
            raise RuntimeError('This submission is terminal; inspect its existing job')
        prior = c.store.db.execute("SELECT data FROM evidence WHERE job=? AND sha=? AND kind='author' ORDER BY id DESC LIMIT 1",
                                   (existing['id'], sha)).fetchone()
        if (existing['commit_sha'] == sha and existing['state'] in ('ci_pending', 'human_review')
                and existing['requirements'] == requirements and existing['title'] == title
                and prior and json.loads(prior['data']) == author and c.store.gate(existing['id'], sha)):
            return existing
    job = existing or c.store.enqueue(pr_url, repo_key, title, requirements)
    job_id = job['id']
    directory = Path(c.config['state_dir']) / 'jobs' / str(job_id)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    checkout = directory / 'checkout'
    c.store.update(job_id, state='qa', branch=branch, commit_sha=sha, pr_url=pr_url,
                   title=title, requirements=requirements, error=None)
    try:
        github.submission_status(repo['github_repo'], sha, pr_url)
        if checkout.exists():
            number = 1
            while (directory / ('submission-' + str(number))).exists():
                number += 1
            checkout.rename(directory / ('submission-' + str(number)))
        c.refresh_source(c.store.get(job_id), repo)
        subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', 'clone', '--no-local',
                        '--no-hardlinks', str(workspace), str(checkout)],
                       check=True, capture_output=True, timeout=60)
        git(checkout, 'checkout', branch)
        if git(checkout, 'rev-parse', 'HEAD') != sha:
            raise RuntimeError('Workspace changed while cloning; resubmit its committed head')
        git(checkout, 'remote', 'set-url', 'origin', 'https://github.com/' + repo['github_repo'] + '.git')
        git(checkout, 'fetch', '--no-tags', repo['source'], 'main')
        git(checkout, 'update-ref', 'refs/heads/main', git(checkout, 'rev-parse', 'FETCH_HEAD'))
        base = git(checkout, 'merge-base', 'main', sha)
        paths = git(checkout, 'diff', '--name-only', '-z', base, sha).split('\0')
        changed_paths_safe([path for path in paths if path])
        if any((checkout / path).is_symlink() for path in paths if path):
            raise RuntimeError('Symlink changes need operator review')
        c.store.evidence(job_id, sha, 'submission', {'head': sha, 'branch': branch, 'pr_url': pr_url})
        c.store.evidence(job_id, sha, 'author', author)
        # A retry or changed requirements cannot reuse a previous successful
        # verdict at this SHA. Only the idempotent return above reuses evidence.
        c.store.evidence(job_id, sha, 'qa', {'passed': False, 'invalidation': True})
        c.store.evidence(job_id, sha, 'review', {'approved': False, 'invalidation': True})
        # Rebase only the disposable checkout. Any new SHA receives new QA/review
        # and a leased PR update, which dismisses the old GitHub approval.
        with independent_work(c, job_id, delivery_lock):
            job = c.refresh_base(job_id)
            sha = job['commit_sha']
            if not c.store.gate(job_id, sha):
                evidence = retry(c.store, job_id, 'qa', sha,
                                 lambda: qa(c.config, checkout, sha, repo['qa_suite']))
                c.store.evidence(job_id, sha, 'qa', evidence)
                (directory / 'qa.json').write_text(json.dumps(evidence, indent=2))
                if not evidence.get('passed'):
                    raise RuntimeError('Independent QA failed; inspect the preserved QA evidence')
                c.store.update(job_id, state='review')
                review = c.review(c.store.get(job_id), checkout, sha, author)
                (directory / 'review.json').write_text(json.dumps(review, indent=2))
            if not c.store.gate(job_id, sha):
                raise RuntimeError('Independent review did not approve; inspect the preserved review evidence')
            c.store.update(job_id, state='validated')
        from .coordinator import WorkPending
        c._control_cycle = True
        try: c.publish(job_id)
        except WorkPending: pass
        finally: c._control_cycle = False
        return c.store.get(job_id)
    except Exception:
        c.store.update(job_id, state='blocked', error='Workspace PR validation failed; inspect preserved evidence and resubmit')
        try:
            github.submission_status(repo['github_repo'], pull['headRefOid'], pr_url, failed=True)
        except Exception:
            pass
        raise
