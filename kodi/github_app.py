"""Short-lived publisher credentials belonging to a GitHub App, not a human."""
import base64
import json
import os
import subprocess
import time
from pathlib import Path
from urllib.request import Request, urlopen

CONFIG = Path(os.environ.get('GITHUB_APP_CONFIG',str(Path.home()/'.config/omiro-dev/github/github-app.json')))
_cached = None


def repository_scope(config):
    """An operator-owned, non-secret allowlist can extend an existing installation."""
    path=CONFIG.parent/'repositories.json'
    names=json.loads(path.read_text()) if path.exists() else config['repositories']
    if not isinstance(names,list) or not names or any(
            not isinstance(n,str) or not n or any(c not in
                'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-' for c in n)
            or n in ('.','..') for n in names):
        raise RuntimeError('Invalid operator repository allowlist')
    return list(dict.fromkeys(names))


def b64(value):
    return base64.urlsafe_b64encode(value).decode().rstrip('=')


def token():
    global _cached
    if _cached and _cached[0]>time.time(): return _cached[1]
    if not CONFIG.is_file(): raise RuntimeError('Dedicated GitHub App setup is required')
    config=json.loads(CONFIG.read_text())
    if not config.get('installation_id') or not config.get('app_id'):
        raise RuntimeError('GitHub App must be installed on the development repository')
    key=Path(config['private_key'])
    if key.stat().st_mode & 0o077: raise RuntimeError('GitHub App private key must have owner-only permissions')
    issued=int(time.time())
    claims={'iat':issued-60,'exp':issued+540,'iss':str(config['app_id'])}
    unsigned=b64(b'{"alg":"RS256","typ":"JWT"}')+'.'+b64(json.dumps(claims,separators=(',',':')).encode())
    result=subprocess.run(['openssl','dgst','-sha256','-sign',str(key)],input=unsigned.encode(),capture_output=True,check=True)
    jwt=unsigned+'.'+b64(result.stdout)
    installation_url='https://api.github.com/app/installations/'+str(int(config['installation_id']))
    headers={'Authorization':'Bearer '+jwt,'Accept':'application/vnd.github+json',
             'Content-Type':'application/json','User-Agent':'kodi-coordinator'}
    try:
        with urlopen(Request(installation_url,headers=headers),timeout=15) as response:
            installation=json.load(response)
        permissions={'contents':'write','issues':'write','pull_requests':'write','statuses':'write'}
        if installation.get('permissions',{}).get('checks') in ('read','write'):
            permissions['checks']='read'
        request=Request(installation_url+'/access_tokens',
            data=json.dumps({'repositories':repository_scope(config),'permissions':permissions}).encode(),
            headers=headers)
        with urlopen(request,timeout=15) as response: data=json.load(response)
    except Exception:
        raise RuntimeError('GitHub App token request failed; inspect its installation and repository scope') from None
    _cached=(time.time()+3000,data['token'])
    return data['token']


def environment():
    env=dict(os.environ)
    env['GH_TOKEN']=token()
    return env
