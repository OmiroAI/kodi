"""An explicit human Telegram action may use the owner's host GitHub login.

This identity is never a publisher, model credential, or worker mount.
"""
import json
import os
import subprocess


def owner_gh(*args,stdin=None):
    env=dict(os.environ)
    for key in ('GH_TOKEN','GITHUB_TOKEN','GH_ENTERPRISE_TOKEN','GITHUB_ENTERPRISE_TOKEN'):
        env.pop(key,None)
    try:
        result=subprocess.run(['gh',*args,'--hostname','github.com'],input=stdin,text=True,capture_output=True,timeout=60,env=env)
    except (OSError,subprocess.TimeoutExpired):
        raise RuntimeError('GitHub owner approval could not be confirmed') from None
    if result.returncode:raise RuntimeError('GitHub owner login is unavailable or lacks review access')
    return result.stdout.strip()


def approve(repo,number,sha,login,action):
    identity=json.loads(owner_gh('api','user'))
    if identity.get('login','').lower()!=login.lower():
        raise RuntimeError('The host GitHub login does not match the configured human approver')
    return json.loads(owner_gh('api','--method','POST',f'repos/{repo}/pulls/{number}/reviews',
        '--input','-',stdin=json.dumps({'event':'APPROVE','commit_id':sha,
            'body':'Human approval submitted through the authorized Kodi Telegram interface.\n\n'
                   'Exact commit: '+sha+'\nAction: '+action})))
