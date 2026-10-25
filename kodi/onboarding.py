"""Prepare a reviewable onboarding branch; registration/activation stay explicit."""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from . import github_app
from .workspace import REPO, atomic, git

PROFILES = {'python', 'hardware-python', 'astro', 'static', 'inventory'}


def prepare(repository, profile, reviewer, root, publish=False):
    if not REPO.fullmatch(repository) or any(p in ('.', '..') for p in repository.split('/')):
        raise ValueError('Invalid repository')
    if profile not in PROFILES or not re.fullmatch(r'[A-Za-z0-9-]+', reviewer):
        raise ValueError('Explicit QA profile and reviewer required')
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    target = root/repository.split('/')[1]
    url = 'https://github.com/'+repository+'.git'
    if target.exists():
        raise ValueError('Onboarding directory already exists; inspect or resume it explicitly')
    result = subprocess.run(['git', '-c', 'core.hooksPath=/dev/null',
        '-c', 'credential.helper=', '-c', 'credential.helper=!gh auth git-credential',
        'clone', '--no-recurse-submodules', url, str(target)],
        env=github_app.environment(), capture_output=True, timeout=120)
    if result.returncode:
        raise RuntimeError('Clone failed; verify the existing App installation and operator repository allowlist')
    if git(target, 'branch', '--show-current') != 'main':
        raise ValueError('Repository default branch is not main; operator design required')
    branch = 'kodi/onboarding'
    git(target, 'switch', '-c', branch)
    if (target/'.kodi').exists() or (target/'.github/workflows/kodi-qa.yml').exists():
        raise ValueError('Existing Kodi onboarding files require reconciliation')
    for relative in ('.kodi', '.github', '.github/workflows', 'docs'):
        if (target/relative).is_symlink():
            raise ValueError('Onboarding directories must not be symlinks')
    if (target/'docs/kodi-context.md').exists():
        raise ValueError('Existing repository context requires reconciliation')
    template = Path(__file__).resolve().parents[1]/'examples/onboarding/qa.py'
    atomic(target/'.kodi/qa.py', template.read_text())
    atomic(target/'.kodi/config.json', json.dumps({'profile': profile,
        'reviewers': [reviewer], 'qa_scope': 'offline baseline; bench and production checks excluded'}, indent=2)+'\n')
    if profile == 'astro':
        setup = '''      - uses: actions/setup-node@v4
        with:
          node-version: '22'
          cache: npm
      - run: npm ci
'''
    else:
        setup = ''
    atomic(target/'.github/workflows/kodi-qa.yml', '''name: Kodi QA
on:
  pull_request:
    branches: [main]
  push:
    branches: [main]
permissions:
  contents: read
jobs:
  kodi-tests:
    runs-on: ubuntu-latest
    timeout-minutes: 10
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
      - run: python -m pip install pytest==9.1.1 PyYAML==6.0.3
'''+setup+'''      - run: python .kodi/qa.py
''')
    # Do not rewrite existing ownership rules or override a more specific rule.
    owners = target/'.github/CODEOWNERS'
    if not owners.exists():
        atomic(owners, '* @'+reviewer+'\n')
    atomic(target/'docs/kodi-context.md', '''# Kodi repository context

Read README.md and existing docs for architecture and product behavior.
Run `python .kodi/qa.py` for the declared offline baseline. This baseline does
not prove hardware behavior, external integrations or production readiness.

## Confirmed lessons

Add concise, verified lessons here with source PR/comment URLs and verification.
Keep this file under 12,000 UTF-8 bytes. Never include credentials or raw logs.
''')
    catalog = {repository.split('/')[1]: {'github_repo': repository,
        'qa_suite': 'astro' if profile == 'astro' else 'repository',
        'required_checks': ['kodi-tests', 'kodi/independent-acceptance'],
        'enabled': False, 'auto_merge': False}}
    atomic(root/(repository.split('/')[1]+'-catalog.json'), json.dumps(catalog, indent=2)+'\n')
    git(target, 'add', '.kodi', '.github/workflows/kodi-qa.yml', 'docs/kodi-context.md')
    if not git(target, 'ls-files', '.github/CODEOWNERS'):
        git(target, 'add', '.github/CODEOWNERS')
    git(target, '-c', 'user.name=Kodi', '-c', 'user.email=kodi@example.invalid',
        'commit', '-m', 'Prepare Kodi offline QA and reviewed repository context')
    body = ('Prepare Kodi onboarding with explicit '+profile+' baseline QA. '
        'Review existing CODEOWNERS and workflow compatibility before enabling dispatch.\n\n'
        'Manual validation: run python .kodi/qa.py in the offline QA image; inspect the scope '
        'of its checks and run any required bench tests. Astro requires an operator-built '
        'image matching package-lock.json.\n\n'
        'This PR is not independently accepted. Independent exact-commit QA, different-model '
        'review and GitHub CI are required before marking it ready. Owner approval, required '
        'check protections and registration of the disabled catalog precede activation. '
        'No App grant, protection or deployment was changed. Optional registration adds only a disabled registry entry.')
    atomic(root/(repository.split('/')[1]+'-pr.md'), body+'\n')
    pr = None
    if publish:
        # CI bootstrap changes workflows. The runtime App deliberately has no
        # workflows-write grant; use the operator's existing CLI login explicitly.
        env = dict(os.environ)
        env.pop('GH_TOKEN', None)
        env.pop('GITHUB_TOKEN', None)
        auth = subprocess.run(['gh', 'auth', 'status'], env=env, capture_output=True, timeout=30)
        if auth.returncode:
            raise RuntimeError('Onboarding branch prepared; operator gh login is required to publish workflow files')
        push = subprocess.run(['git', '-c', 'core.hooksPath=/dev/null',
            '-c', 'credential.helper=', '-c', 'credential.helper=!gh auth git-credential',
            'push', 'origin', branch], cwd=target, env=env, capture_output=True, timeout=120)
        if push.returncode:
            raise RuntimeError('Onboarding branch preserved; operator workflow publishing failed; inspect its existing access')
        result = subprocess.run(['gh', 'pr', 'create', '--repo', repository,
            '--head', branch, '--draft', '--title', 'Onboard repository to Kodi',
            '--body-file', '-'], input=body, cwd=target, env=env,
            text=True, capture_output=True, timeout=120)
        if result.returncode:
            raise RuntimeError('Onboarding branch pushed; inspect existing PR before retrying creation')
        pr = result.stdout.strip()
    return {'checkout': str(target), 'branch': branch, 'pr': pr,
        'catalog': str(root/(repository.split('/')[1]+'-catalog.json')),
        'enabled': False, 'auto_merge': False}


def register_disabled(repository, profile, reviewer, config_path, workspace):
    """Reuse operator catalog setup; preserve access scope and disable new targets."""
    if (not REPO.fullmatch(repository) or any(p in ('.', '..') for p in repository.split('/'))
            or profile not in PROFILES or not re.fullmatch(r'[A-Za-z0-9-]+', reviewer)):
        raise ValueError('Invalid explicit onboarding selection')
    config = json.loads(Path(config_path).read_text())
    for entry in config['repos'].values():
        if entry.get('github_repo', '').lower() == repository.lower():
            return  # Never overwrite an existing repository's activation/settings.
    key = repository.split('/')[1]
    if key in config['repos']:
        raise ValueError('Repository catalog key collision')
    catalog = {key: {'github_repo': repository,
        'qa_suite': 'astro' if profile == 'astro' else 'repository',
        'required_checks': ['kodi-tests', 'kodi/independent-acceptance'],
        'enabled': False, 'auto_merge': False}}
    with tempfile.TemporaryDirectory(prefix='kodi-onboarding-') as temporary:
        path = Path(temporary)/'catalog.json'
        path.write_text(json.dumps(catalog))
        script = Path(__file__).resolve().parents[1]/'scripts/onboard_repositories.py'
        result = subprocess.run([sys.executable, str(script), '--config', str(config_path),
            '--catalog', str(path), '--reviewer', reviewer, '--workspace', str(workspace)],
            text=True, capture_output=True, timeout=300)
        if result.returncode:
            raise RuntimeError('Disabled registration did not complete; inspect App installation access and canonical setup before retrying')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('repository', help='owner/repository')
    parser.add_argument('--profile', choices=sorted(PROFILES), required=True)
    parser.add_argument('--reviewer', required=True)
    parser.add_argument('--root', type=Path, default=Path.home()/'kodi-workspace/onboarding')
    parser.add_argument('--register', action='store_true', help='Register a new target disabled and sync the workspace before preparing its PR')
    parser.add_argument('--config', type=Path, default=Path.home()/'.config/omiro-dev/config.json')
    parser.add_argument('--workspace', type=Path, default=Path.home()/'kodi-workspace')
    parser.add_argument('--publish', action='store_true', help='Push workflow bootstrap with the operator gh login and open a draft PR')
    args = parser.parse_args()
    if args.register:
        register_disabled(args.repository, args.profile, args.reviewer, args.config, args.workspace)
    print(json.dumps(prepare(args.repository, args.profile, args.reviewer, args.root, args.publish), indent=2))


if __name__ == '__main__':
    main()
