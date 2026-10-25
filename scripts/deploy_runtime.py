"""Operator-only runtime updater; invoke from an external systemd timer."""
import argparse
import fcntl
import json
import subprocess
import sqlite3
from pathlib import Path


def command(*args, cwd=None):
    return subprocess.check_output(args, cwd=cwd, text=True).strip()


def deploy(runtime, config, repository, service='kodi.service'):
    runtime = Path(runtime).resolve()
    config = json.loads(Path(config).read_text())
    state = Path(config['state_dir'])
    # Share the coordinator's lock across the entire stop/update/start operation.
    with (state/'delivery.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 'deferred: coordinator busy'
        with sqlite3.connect(f'file:{state / "kodi.sqlite"}?mode=ro', uri=True) as db:
            if db.execute("SELECT 1 FROM jobs WHERE state IN "
                          "('coding','qa','review','publishing','merge_updating') LIMIT 1").fetchone():
                return 'deferred: interrupted job requires inspection'
        git = lambda *args: command('git', '-c', 'core.hooksPath=/dev/null',
                                   '-c', 'core.fsmonitor=false', *args, cwd=runtime)
        if git('status', '--porcelain') or git('branch', '--show-current') != 'main':
            raise RuntimeError('Runtime must be clean on main')
        if git('remote', 'get-url', 'origin') != f'https://github.com/{repository}.git':
            raise RuntimeError('Unexpected runtime origin')
        target = json.loads(command('gh', 'api', f'repos/{repository}/commits/main'))['sha']
        old = git('rev-parse', 'HEAD')
        if target == old:
            return 'already current: ' + old
        runs = json.loads(command('gh', 'api',
            f'repos/{repository}/actions/workflows/qa.yml/runs?branch=main&event=push&head_sha={target}'))['workflow_runs']
        if not runs or runs[0]['head_sha'] != target or runs[0]['conclusion'] != 'success':
            return 'deferred: main QA not successful'
        git('fetch', '--no-tags', 'origin', 'main')
        if git('rev-parse', 'FETCH_HEAD') != target:
            return 'deferred: main advanced during verification'
        git('merge-base', '--is-ancestor', old, target)
        subprocess.run(['systemctl', '--user', 'stop', service], check=True)
        try:
            git('merge', '--ff-only', target)
            subprocess.run(['systemctl', '--user', 'start', service], check=True)
            subprocess.run(['systemctl', '--user', 'is-active', '--quiet', service], check=True)
        except Exception:
            subprocess.run(['systemctl', '--user', 'stop', service], check=True)
            # Both endpoints were verified clean; preserve any unexpected local edits.
            git('reset', '--keep', old)
            subprocess.run(['systemctl', '--user', 'start', service], check=True)
            raise
        return 'deployed: ' + target


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--repository', required=True)
    parser.add_argument('--service', default='kodi.service')
    args = parser.parse_args()
    print(deploy(args.runtime, args.config, args.repository, args.service))
