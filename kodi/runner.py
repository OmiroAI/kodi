"""Fixed worker/QA capabilities. No shell interpolation of ticket content."""
import base64
import json
import os
import subprocess
import time
from pathlib import Path


REVIEW_SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'properties': {
        **{key: {'type': 'boolean'} for key in ('approved', 'criteria_met', 'simple')},
        'blocking_findings': {'type': 'array', 'items': {'type': 'string'}},
        'summary': {'type': 'string'},
    },
    'required': ['approved', 'criteria_met', 'simple', 'blocking_findings', 'summary'],
}


def git(checkout, *args):
    return subprocess.check_output(['git','-c','core.hooksPath=/dev/null',
                                    '-c','core.fsmonitor=false',*args],cwd=checkout,text=True).strip()


def docker_args(config, name, checkout, auth_dir, output_dir, read_only=False):
    args = ['docker','run','--rm','-i','--name',name,'--network','none',
            '--cpus','1.5','--memory','3g','--memory-swap','3g','--pids-limit','192',
            '--read-only','--cap-drop','ALL','--security-opt','no-new-privileges',
            '--user',str(os.getuid())+':'+str(os.getgid()),
            '--tmpfs','/tmp:rw,nosuid,nodev,size=512m',
            '--mount',f'type=bind,src={checkout},dst=/workspace'+(',readonly' if read_only else ''),
            '--mount',f'type=bind,src={checkout / ".git"},dst=/workspace/.git,readonly',
            '--mount',f'type=bind,src={auth_dir},dst=/home/worker',
            '--mount',f'type=bind,src={output_dir},dst=/output',
            '--mount',f'type=bind,src={config["proxy_dir"]},dst=/run/kodi-proxy,readonly',
            '-e','PYTHONDONTWRITEBYTECODE=1',config['worker_image']]
    return args


def run_worker(config, checkout, job_dir, prompt, role, provider, on_usage=None):
    from .workspace import context_text
    prompt += context_text(checkout)
    output = job_dir/(role+'-output'); output.mkdir(exist_ok=True)
    # Dedicated auth directories contain only this provider's selected login.
    auth_dir = Path(config['state_dir'])/'auth'/provider
    auth_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    name = 'kodi-'+role+'-'+job_dir.name
    args = docker_args(config,name,checkout,auth_dir,output,role=='review')
    if provider=='codex':
        command = ['codex','exec','--ignore-user-config','--ignore-rules',
                   '--dangerously-bypass-approvals-and-sandbox','--ephemeral','--json',
                   '-o','/output/result.json','-']
    elif provider=='gemini':
        command = ['gemini','--approval-mode','yolo','--output-format','json','-p',prompt]
    elif provider=='antigravity':
        model=config.get('antigravity_model','gemini-4-argon-high')
        if role!='review' or not isinstance(model,str) or not model.startswith('gemini-'):
            raise ValueError('Antigravity reviewer requires a pinned Gemini model')
        command=['agy','--input-format','stream-json','--output-format','stream-json',
                 '--disable-slash-commands','--dangerously-skip-permissions','--model',model,
                 '--print-timeout',str(config.get('worker_timeout',900))+'s',
                 '--json-schema',json.dumps(REVIEW_SCHEMA)]
    else:
        raise ValueError('Unknown coding harness')
    started = time.monotonic()
    try:
        result = subprocess.run(args+command,input=(json.dumps({'event':'user','message':{'content':prompt}})+'\n'
                   if provider=='antigravity' else prompt if provider=='codex' else None),
            text=True,capture_output=True,timeout=config.get('worker_timeout',900))
    except subprocess.TimeoutExpired:
        if on_usage: on_usage({'usage_unknown':True,'error':'timeout'},time.monotonic()-started)
        raise RuntimeError(provider+' worker exceeded its time budget') from None
    finally:
        subprocess.run(['docker','rm','-f',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    (output/'events.jsonl').write_text(result.stdout)
    (output/'stderr.log').write_text(result.stderr)
    usage={}
    if provider=='codex':
        for line in result.stdout.splitlines():
            try:
                event=json.loads(line)
                if event.get('usage'): usage=event['usage']
            except json.JSONDecodeError: pass
        if on_usage: on_usage(usage or {'usage_unknown':True},time.monotonic()-started)
        if result.returncode:
            from .recovery import TransientError, transient
            if any(code in result.stderr+result.stdout for code in
                   ('refresh_token_reused','token_expired','workspace routing discovery unauthorized (401)')):
                raise RuntimeError('Codex sign-in required; renew Kodi dedicated login')
            if transient(result.stderr+'\n'+result.stdout):
                raise TransientError(provider+' worker temporarily unavailable')
            raise RuntimeError(provider+' worker failed; inspect its local logs')
        final=(output/'result.json').read_text()
    elif provider=='antigravity':
        envelope=None
        try:
            for line in result.stdout.splitlines():
                event=json.loads(line)
                if not isinstance(event,dict): raise ValueError('Invalid stream event')
                if event.get('event')=='result': envelope=event['result']
            if not isinstance(envelope,dict): raise ValueError('Missing result')
        except (ValueError,KeyError,TypeError):
            if on_usage: on_usage({'usage_unknown':True},time.monotonic()-started)
            from .recovery import TransientError, transient
            if transient(result.stderr+'\n'+result.stdout):
                raise TransientError('Antigravity reviewer temporarily unavailable')
            raise RuntimeError('Antigravity returned an unreadable result') from None
        usage=envelope.get('usage',{})
        if on_usage: on_usage(usage or {'usage_unknown':True},time.monotonic()-started)
        if result.returncode or envelope.get('status')!='SUCCESS' or envelope.get('denied_actions'):
            from .recovery import TransientError, transient
            if not envelope.get('denied_actions') and transient(result.stderr+'\n'+result.stdout):
                raise TransientError('Antigravity reviewer temporarily unavailable')
            raise RuntimeError('Antigravity review failed; inspect its local logs')
        value=envelope.get('structured_output')
        if not isinstance(value,dict):
            raise RuntimeError('Antigravity returned no structured review verdict')
        return value,usage,time.monotonic()-started
    else:
        try: envelope=json.loads(result.stdout)
        except json.JSONDecodeError:
            if on_usage: on_usage({'usage_unknown':True},time.monotonic()-started)
            raise RuntimeError('Gemini returned an unreadable result') from None
        final=envelope.get('response',''); usage=envelope.get('stats',{})
        if on_usage: on_usage(usage or {'usage_unknown':True},time.monotonic()-started)
        if result.returncode: raise RuntimeError(provider+' worker failed; inspect its local logs')
    final=final.strip()
    if final.startswith('```'): final='\n'.join(final.splitlines()[1:-1])
    return json.loads(final),usage,time.monotonic()-started


def qa(config, checkout, sha, suite):
    archive = checkout.parent/(sha+'.tar')
    with archive.open('wb') as stream:
        subprocess.run(['git','archive','--format=tar',sha],cwd=checkout,stdout=stream,check=True)
    if archive.stat().st_size>50*1024*1024: raise RuntimeError('Source archive exceeds 50 MB')
    host=config['qa_host']
    transfer=subprocess.run(['scp','-q','-o','BatchMode=yes','-o','ConnectTimeout=10',str(archive),
                    host+':.local/state/omiro-qa/incoming/'+sha+'.tar'],capture_output=True,timeout=60)
    if transfer.returncode:
        from .recovery import TransientError, transient
        if transient(transfer.stderr.decode(errors='replace')):
            raise TransientError('Independent QA transfer temporarily unavailable')
        raise RuntimeError('Independent QA transfer failed; inspect remote access')
    payload=base64.b64encode(json.dumps({'sha':sha,'suite':suite}).encode()).decode()
    result=subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=10',host,
                           'python3 .local/lib/omiro-dev/qa_remote.py '+payload],
                          capture_output=True,text=True,timeout=660)
    archive.unlink(missing_ok=True)
    if result.returncode:
        from .recovery import TransientError, transient
        if transient(result.stderr): raise TransientError('Independent QA host temporarily unavailable')
        raise RuntimeError('Independent QA host failed; inspect remote setup')
    evidence=json.loads(result.stdout)
    if evidence.get('sha')!=sha: raise RuntimeError('QA evidence commit mismatch')
    return evidence
