"""Bounded, read-only Codex intake using the existing worker/auth/proxy path."""
import json
import os
import stat
import subprocess
import tempfile
import time
from pathlib import Path

from .runner import docker_args


def options(config):
    value=config.get('clarification',{})
    if not isinstance(value,dict) or type(value.get('enabled',False)) is not bool:
        raise ValueError('clarification.enabled must be a boolean')
    if value.get('enabled') is not True:
        return None
    if value.get('model') != 'gpt-6-luna' or value.get('effort') not in ('low','medium','high'):
        raise ValueError('Configure clarification model gpt-6-luna and effort low, medium or high')
    for key,default,low,high in (('timeout',45,5,120),('context_chars',20000,1000,40000)):
        setting=value.get(key,default)
        if type(setting) is not int or not low <= setting <= high:
            raise ValueError('Invalid clarification '+key)
    return {**value,'timeout':value.get('timeout',45),'context_chars':value.get('context_chars',20000)}


def read_result(directory):
    # Open relative to the trusted output directory descriptor. O_NONBLOCK keeps
    # special files from stalling intake before fstat can reject them.
    root=os.open(directory,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try:
        fd=os.open('result.json',os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=root)
        with os.fdopen(fd,'rb') as stream:
            info=os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 16384:
                raise ValueError('Invalid clarification result file')
            raw=stream.read(16385)
            if len(raw)>16384:
                raise ValueError('Clarification result exceeds limit')
            return json.loads(raw)
    finally:
        os.close(root)


def run_clarifier(config,checkout,directory,prompt,on_usage):
    settings=options(config)
    if not settings:
        raise ValueError('Clarification is not enabled')
    if len(prompt)>settings['context_chars']:
        raise ValueError('Clarification input exceeds configured context budget')
    directory.mkdir(parents=True,exist_ok=True,mode=0o700)
    invocation=Path(tempfile.mkdtemp(prefix='intake-',dir=directory))
    output=invocation/'output';output.mkdir(mode=0o700)
    auth=Path(config['state_dir'])/'auth'/'codex'
    auth.mkdir(parents=True,exist_ok=True,mode=0o700)
    name='kodi-'+invocation.name
    args=docker_args(config,name,checkout,auth,output,read_only=True)
    command=['codex','exec','--ignore-user-config','--ignore-rules',
        '--dangerously-bypass-approvals-and-sandbox','--ephemeral','--json',
        '--model',settings['model'],'-c','model_reasoning_effort='+json.dumps(settings['effort']),
        '-C','/workspace','-o','/output/result.json','-']
    started=time.monotonic();usage={'usage_unknown':True};outcome='failed'
    try:
        result=subprocess.run(args+command,input=prompt,text=True,capture_output=True,timeout=settings['timeout'])
        # Host logs are siblings of the worker mount, never worker-selected paths.
        (invocation/'events.jsonl').write_text(result.stdout)
        (invocation/'stderr.log').write_text(result.stderr)
        for line in result.stdout.splitlines():
            try:
                event=json.loads(line)
                if isinstance(event,dict) and isinstance(event.get('usage'),dict):usage=event['usage']
            except ValueError:pass
        if result.returncode:
            raise RuntimeError('Clarification worker unavailable; inspect private invocation logs')
        value=read_result(output)
        outcome='succeeded'
        return value
    except subprocess.TimeoutExpired:
        outcome='timeout'
        raise RuntimeError('Clarification worker exceeded its time budget') from None
    finally:
        subprocess.run(['docker','rm','-f',name],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        on_usage(usage,time.monotonic()-started,{'phase':'clarification','outcome':outcome})
