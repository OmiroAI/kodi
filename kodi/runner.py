"""Fixed worker/QA capabilities. No shell interpolation of ticket content."""
import base64
import json
import os
import subprocess
import stat
import tempfile
import time
from pathlib import Path


class QuotaExhausted(RuntimeError):
    """Provider explicitly reports exhausted credits or subscription quota."""


def quota_exhausted(message):
    text = message.lower()
    return any(marker in text for marker in (
        'individual quota reached', 'insufficient_quota', 'credit balance is too low',
        'insufficient credits', 'credits exhausted', 'usage limit reached',
        'exceeded your current quota',
    ))


REVIEW_SCHEMA = {
    'type': 'object', 'additionalProperties': False,
    'properties': {
        **{key: {'type': 'boolean'} for key in ('approved', 'criteria_met', 'simple', 'delivery_met')},
        'blocking_findings': {'type': 'array', 'items': {'type': 'string'}},
        'summary': {'type': 'string'},
    },
    'required': ['approved', 'criteria_met', 'simple', 'delivery_met', 'blocking_findings', 'summary'],
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


class WorkerTimeout(RuntimeError):
    pass


def run_worker(config, checkout, job_dir, prompt, role, provider, on_usage=None,
               on_usage_event=None, usage_phase=None, context_revision=None):
    selection=config.get('selected_coding_profile', {'profile':'legacy'}) if role=='author' and provider=='codex' else {}
    provenance=None
    if role=='author' and provider=='codex':
        from .profiles import validate_profile
        if selection.get('profile') != 'legacy':
            validate_profile(selection)
        provenance={key:selection.get(key) for key in ('profile','model','effort')}
        provenance['requested_model']=provenance.pop('model')
        provenance.update(resolved_model=None, harness_version=None,
                          available_harness_version=config.get('codex_harness_version'),
                          family='openai')
        config=dict(config, author_provenance=provenance)
    observation = []
    started = time.monotonic()
    outcome = 'failed'
    def record(data, seconds):
        observation[:] = [data, seconds]
        if on_usage:
            on_usage(data, seconds)
    try:
        if context_revision is not None and (
                role != 'author' or provider != 'codex' or usage_phase not in ('recovery', 'rebase_conflict_repair')):
            raise ValueError('Committed context is only available for conflict recovery')
        result = _run_worker(config, checkout, job_dir, prompt, role, provider, record,
                             context_revision=context_revision)
        outcome = 'succeeded'
        return result
    except WorkerTimeout:
        outcome = 'timeout'
        raise
    finally:
        if on_usage_event:
            data, seconds = observation or [{'usage_unknown':True}, time.monotonic()-started]
            if provenance is not None:
                data=dict(data, author_provenance=dict(provenance))
            phase = usage_phase or ('review' if role == 'review' else 'coding')
            on_usage_event(data, seconds, {'phase':phase, 'outcome':outcome})


RESULT_MAX_BYTES = 1024 * 1024


def read_worker_result(directory_fd):
    """Open relative to the pinned mount root, never follow worker links."""
    try:
        fd = os.open('result.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                     dir_fd=directory_fd)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > RESULT_MAX_BYTES:
                raise ValueError('Unexpected result object or size')
            data = stream.read(RESULT_MAX_BYTES + 1)
            if len(data) > RESULT_MAX_BYTES:
                raise ValueError('Result exceeds size limit')
            return data.decode('utf-8')
    except (OSError, ValueError):
        raise RuntimeError('Worker returned a missing, unsafe or oversized result') from None


def _run_worker(config, checkout, job_dir, prompt, role, provider, on_usage=None,
                context_revision=None):
    # Only this fresh directory is writable through /output. Its parent and all
    # host evidence remain outside the worker's mounts, including on retries.
    output = Path(tempfile.mkdtemp(prefix=role+'-worker-', dir=job_dir))
    evidence_root = job_dir/(role+'-evidence')
    evidence_root.mkdir(exist_ok=True, mode=0o700)
    evidence = Path(tempfile.mkdtemp(prefix='attempt-', dir=evidence_root))
    (evidence/'worker-output.txt').write_text(output.name)
    directory_fd = os.open(output, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        return _invoke_worker(config, checkout, job_dir, prompt, role, provider,
                              output, evidence, directory_fd, on_usage,
                              context_revision=context_revision)
    finally:
        os.close(directory_fd)


def _invoke_worker(config, checkout, job_dir, prompt, role, provider,
                   output, evidence, directory_fd, on_usage=None, context_revision=None):
    from .workspace import context_text
    prompt += context_text(checkout, revision=context_revision)
    # Dedicated auth directories contain only this provider's selected login.
    auth_dir = Path(config['state_dir'])/'auth'/provider
    auth_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    name = 'kodi-'+role+'-'+job_dir.name
    args = docker_args(config,name,checkout,auth_dir,output,role=='review')
    if provider=='codex':
        selection=config.get('selected_coding_profile',{'profile':'legacy'})
        provenance=config.get('author_provenance')
        version_path=output/'codex-version.txt'
        # Reused attempt directories must never supply a previous run's version.
        version_path.unlink(missing_ok=True)
        command = ['codex','exec','--ignore-user-config','--ignore-rules',
                   '--dangerously-bypass-approvals-and-sandbox','--ephemeral','--json',
                   '-o','/output/result.json','-']
        if selection.get('model'):
            command[2:2]=['--model',selection['model'],'--config',
                          'model_reasoning_effort='+json.dumps(selection['effort'])]
        if provenance is not None:
            # Probe the executable in the actual worker container, then replace
            # the shell with that same executable. Ticket content stays on stdin.
            command=['sh','-c',
                     'codex --version > /output/codex-version.txt && exec "$@"',
                     'kodi-codex',*command]
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
    except subprocess.TimeoutExpired as error:
        for filename, data in (('events.jsonl', error.stdout), ('stderr.log', error.stderr)):
            (evidence/filename).write_bytes(data.encode() if isinstance(data, str) else data or b'')
        if on_usage: on_usage({'usage_unknown':True,'error':'timeout'},time.monotonic()-started)
        raise WorkerTimeout(provider+' worker exceeded its time budget') from None
    finally:
        subprocess.run(['docker','rm','-f',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        if provider=='codex' and provenance is not None:
            try:
                fd = os.open('codex-version.txt', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                             dir_fd=directory_fd)
                with os.fdopen(fd, 'rb') as stream:
                    info = os.fstat(stream.fileno())
                    if stat.S_ISREG(info.st_mode) and info.st_size <= 100:
                        version = stream.read(101).decode('utf-8', errors='replace').strip()
                        if len(version) <= 100 and version.startswith('codex-cli '):
                            provenance['harness_version'] = version
            except OSError:
                pass
    (evidence/'events.jsonl').write_text(result.stdout)
    (evidence/'stderr.log').write_text(result.stderr)
    usage={}
    if provider=='codex':
        for line in result.stdout.splitlines():
            try:
                event=json.loads(line)
                if event.get('usage'): usage=event['usage']
                metadata=(event.get('payload',{}) if event.get('type')=='session_meta' else
                          event if event.get('type') in ('thread.started','turn.started','turn_context') else {})
                if provenance is not None and isinstance(metadata,dict) and isinstance(metadata.get('model'),str):
                    provenance['resolved_model']=metadata['model']
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
        final=read_worker_result(directory_fd)
    elif provider=='antigravity':
        if len(result.stdout.encode('utf-8')) > RESULT_MAX_BYTES:
            if on_usage: on_usage({'usage_unknown':True},time.monotonic()-started)
            raise RuntimeError('Worker result exceeds size limit')
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
            if quota_exhausted(result.stderr):
                raise QuotaExhausted('Primary reviewer quota exhausted')
            if transient(result.stderr+'\n'+result.stdout):
                raise TransientError('Antigravity reviewer temporarily unavailable')
            raise RuntimeError('Antigravity returned an unreadable result') from None
        usage=envelope.get('usage',{})
        if on_usage: on_usage(usage or {'usage_unknown':True},time.monotonic()-started)
        if result.returncode or envelope.get('status')!='SUCCESS' or envelope.get('denied_actions'):
            from .recovery import TransientError, transient
            if not envelope.get('denied_actions') and quota_exhausted(result.stderr):
                raise QuotaExhausted('Primary reviewer quota exhausted')
            if not envelope.get('denied_actions') and transient(result.stderr+'\n'+result.stdout):
                raise TransientError('Antigravity reviewer temporarily unavailable')
            raise RuntimeError('Antigravity review failed; inspect its local logs')
        value=envelope.get('structured_output')
        if not isinstance(value,dict):
            raise RuntimeError('Antigravity returned no structured review verdict')
        return value,usage,time.monotonic()-started
    else:
        if len(result.stdout.encode('utf-8')) > RESULT_MAX_BYTES:
            if on_usage: on_usage({'usage_unknown':True},time.monotonic()-started)
            raise RuntimeError('Worker result exceeds size limit')
        try: envelope=json.loads(result.stdout)
        except json.JSONDecodeError:
            if on_usage: on_usage({'usage_unknown':True},time.monotonic()-started)
            raise RuntimeError('Gemini returned an unreadable result') from None
        final=envelope.get('response',''); usage=envelope.get('stats',{})
        if on_usage: on_usage(usage or {'usage_unknown':True},time.monotonic()-started)
        if result.returncode: raise RuntimeError(provider+' worker failed; inspect its local logs')
    final=final.strip()
    if final.startswith('```'): final='\n'.join(final.splitlines()[1:-1])
    value = json.loads(final)
    (evidence/'result.json').write_text(json.dumps(value))
    return value,usage,time.monotonic()-started


def qa(config, checkout, sha, suite):
    archive = checkout.parent/(sha+'.tar')
    with archive.open('wb') as stream:
        subprocess.run(['git','archive','--format=tar',sha],cwd=checkout,stdout=stream,check=True)
    limit=config.get('qa_archive_max_bytes',50*1024*1024)
    if type(limit) is not int or not 1 <= limit <= 512*1024*1024:
        archive.unlink(missing_ok=True)
        raise ValueError('Invalid operator QA archive bound')
    if archive.stat().st_size>limit:
        archive.unlink(missing_ok=True)
        raise RuntimeError('Source archive exceeds operator QA limit')
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
