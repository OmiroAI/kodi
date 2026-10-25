"""Maintain operator-configured canonical clones using host-only App access."""
import re
import subprocess
from pathlib import Path

from .github_app import environment


def refresh_source(repo):
    name = repo.get('github_repo')
    if not name:
        return  # Explicitly local-only repositories retain their existing behavior.
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', name) or any(
            part in ('.', '..') for part in name.split('/')):
        raise RuntimeError('Canonical source: invalid configured GitHub repository')
    source = Path(repo['source']).resolve()

    def git(*args, env=None):
        try:
            result = subprocess.run(
                ['git', '-c', 'core.hooksPath=/dev/null', '-c', 'core.fsmonitor=false',
                 *args], cwd=source, text=True, capture_output=True, timeout=120, env=env)
        except (OSError, subprocess.TimeoutExpired):
            raise RuntimeError('Canonical source: Git operation failed') from None
        if result.returncode:
            operation = 'fetch' if 'fetch' in args else args[0]
            from .recovery import TransientError, transient
            if operation=='fetch' and transient(result.stderr):
                raise TransientError('Canonical source temporarily unavailable')
            raise RuntimeError('Canonical source: Git operation failed ('+operation+')')
        return result.stdout.strip()

    if not (source / '.git').exists() or git('rev-parse', '--show-toplevel') != str(source):
        raise RuntimeError('Canonical source must be a separate Git checkout, never production')
    if git('symbolic-ref', '--short', 'HEAD') != 'main':
        raise RuntimeError('Canonical source must be checked out on main')
    if git('status', '--porcelain', '--untracked-files=all'):
        raise RuntimeError('Canonical source has dirty or untracked files; operator maintenance required')
    origins = git('config', '--get-all', 'remote.origin.url').splitlines()
    allowed = {prefix+name+suffix for prefix in (
        'https://github.com/', 'git@github.com:', 'ssh://git@github.com/')
        for suffix in ('', '.git')}
    if len(origins) != 1 or origins[0] not in allowed:
        raise RuntimeError('Canonical source origin does not match configured GitHub repository')
    # Even an SSH origin uses HTTPS for this fetch, with the existing scoped App
    # environment and host credential helper. No credentials enter URLs or clones.
    try:
        env = environment()
    except Exception:
        raise RuntimeError('Canonical source fetch requires configured GitHub App access') from None
    # Do not let checkout-specific fetch/prune settings update local refs before
    # the ancestry check, or leave an old FETCH_HEAD as the comparison target.
    git('-c', 'credential.helper=', '-c', 'credential.helper=!gh auth git-credential',
        'fetch', '--no-tags', '--no-recurse-submodules', '--no-prune',
        '--refmap=', '--write-fetch-head', '--no-auto-maintenance',
        'https://github.com/'+name+'.git', 'refs/heads/main', env=env)
    upstream = git('rev-parse', 'FETCH_HEAD')
    counts = git('rev-list', '--left-right', '--count', 'main...'+upstream).split()
    if int(counts[0]):
        raise RuntimeError('Canonical source main is ahead of or diverged from GitHub main')
    git('merge', '--ff-only', upstream)
