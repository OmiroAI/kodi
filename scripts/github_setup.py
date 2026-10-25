#!/usr/bin/env python3
"""Authenticated-by-proxy GitHub App manifest setup. Never logs auth codes."""
import html
import json
import os
import secrets
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

DATA=Path('/data')
BASE=os.environ['PUBLIC_URL'].rstrip('/')
ORG=os.environ.get('GITHUB_ORG','')
REPOSITORY=os.environ.get('KODI_SETUP_REPOSITORY','dev-system')
STATE_FILE=DATA/'registration-state'


def build_manifest(base, organization, repository='dev-system'):
    return {'name':'Kodi Development','url':'https://github.com/'+organization+'/'+repository,
        'description':'Isolated development and independent QA. Human approves all merges.',
        'public':False,
        # GitHub requires this nested URL even when delivery is disabled.
        'hook_attributes':{'url':base+'/setup/github/events','active':False},
        'redirect_url':base+'/setup/github/callback',
        'default_permissions':{'contents':'write','issues':'write','pull_requests':'write','statuses':'write','checks':'read'},
        'default_events':[]}


def save(path, text):
    path.write_text(text); path.chmod(0o600)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_): pass

    def page(self, body, status=200):
        encoded=('<!doctype html><html lang="en"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>Kodi GitHub setup</title><style>body{font:18px system-ui;max-width:640px;margin:40px auto;padding:20px;line-height:1.5}button{font:inherit;padding:12px}code{overflow-wrap:anywhere}</style>'
            '<h1>Kodi GitHub setup</h1>'+body+'</html>').encode()
        self.send_response(status); self.send_header('Content-Type','text/html; charset=utf-8')
        self.send_header('Cache-Control','no-store'); self.end_headers(); self.wfile.write(encoded)

    def do_GET(self):
        url=urlparse(self.path); query=parse_qs(url.query)
        if url.path in ('/setup/github','/setup/github/'):
            config_file=DATA/'github-app.json'
            if config_file.exists():
                config=json.loads(config_file.read_text())
                link='https://github.com/apps/'+config['slug']+'/installations/new'
                self.page('<p>Kodi’s app is registered. Install it on <strong>'+html.escape(ORG)+'</strong>, selecting only the development repositories.</p>'
                          '<p>For private CI, the App owner must select Checks Read-only in Permissions &amp; events and accept updated installation permissions. This is provider permission setup, not a new paid subscription.</p>'
                          '<p><a href="'+html.escape(link)+'">Install Kodi’s GitHub App</a></p><p>After installation, return here to confirm its installation ID.</p>'
                          '<form action="/setup/github/installed" method="post"><label>Installation ID from the GitHub installation URL <input name="installation_id" type="number" required></label><input type="hidden" name="state" value="'+html.escape(STATE_FILE.read_text())+'"><button>Save installation</button></form>')
                return
            state=secrets.token_urlsafe(32); save(STATE_FILE,state)
            manifest=build_manifest(BASE,ORG,REPOSITORY)
            action='https://github.com/organizations/'+ORG+'/settings/apps/new?state='+state
            self.page('<p>Register a private GitHub App owned by '+html.escape(ORG)+'. It can create branches, issues, PRs and acceptance statuses. It gets no administration, workflow-editing or production credentials.</p>'
                '<p>The manifest includes <code>checks: read</code> (Checks Read-only) to read CI on private repositories.</p>'
                '<p>Create the public development-system repository first; your product repository can stay private.</p>'
                '<form method="post" action="'+html.escape(action)+'"><input type="hidden" name="manifest" value="'+html.escape(json.dumps(manifest),quote=True)+'"><button>Register Kodi on GitHub</button></form>')
        elif url.path=='/setup/github/callback':
            state=query.get('state',[''])[0]; code=query.get('code',[''])[0]
            if not STATE_FILE.exists() or not secrets.compare_digest(state,STATE_FILE.read_text()) or not code:
                self.page('<p>Invalid or expired registration state. Start registration again.</p>',400); return
            request=Request('https://api.github.com/app-manifests/'+code+'/conversions',data=b'{}',
                headers={'Accept':'application/vnd.github+json','User-Agent':'kodi-coordinator','Content-Type':'application/json'})
            try:
                with urlopen(request,timeout=20) as response: app=json.load(response)
                save(DATA/'github-app.pem',app['pem'])
                save(DATA/'github-app.json',json.dumps({'app_id':app['id'],'installation_id':None,
                    'slug':app['slug'],'private_key':os.environ['HOST_PRIVATE_KEY_PATH'],
                    'repositories':[REPOSITORY]},indent=2)+'\n')
                self.send_response(303); self.send_header('Location',BASE+'/setup/github'); self.end_headers()
            except Exception:
                self.page('<p>GitHub registration failed. Retry from the setup page; secret-bearing diagnostics are suppressed.</p>',502)
        else: self.page('<p>Not found.</p>',404)

    def do_POST(self):
        if self.path!='/setup/github/installed': self.page('<p>Not found.</p>',404); return
        size=int(self.headers.get('Content-Length','0'))
        if size<1 or size>4096: self.page('<p>Invalid form.</p>',400); return
        form=parse_qs(self.rfile.read(size).decode())
        state=form.get('state',[''])[0]
        if not STATE_FILE.exists() or not secrets.compare_digest(state,STATE_FILE.read_text()):
            self.page('<p>Invalid registration state.</p>',400); return
        try:
            installation=int(form.get('installation_id',[''])[0])
            if installation<=0: raise ValueError()
            path=DATA/'github-app.json'; config=json.loads(path.read_text()); config['installation_id']=installation
            save(path,json.dumps(config,indent=2)+'\n')
            self.page('<p>Installation saved. Kodi will validate it with GitHub before using it. You remain the human PR approver.</p>')
        except (ValueError,OSError): self.page('<p>Enter the numeric installation ID from GitHub.</p>',400)


if __name__=='__main__':
    if not ORG:raise RuntimeError('Set GITHUB_ORG to the operator organization')
    DATA.mkdir(parents=True,exist_ok=True); HTTPServer(('0.0.0.0',38530),Handler).serve_forever()
