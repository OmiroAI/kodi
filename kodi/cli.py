import argparse
import json
import subprocess
import time
from pathlib import Path

from .coordinator import Coordinator
from .egress import serve
from .notify import send
from . import github

DEFAULT_CONFIG=Path.home()/'.config/omiro-dev/config.json'


def usage_summary(rows):
    summary={}
    for row in rows:
        provider=row['provider']
        totals=summary.setdefault(provider, {'run_count':0, 'seconds':0,
                                             'input_tokens':0, 'output_tokens':0})
        totals['run_count']+=1
        totals['seconds']+=row['seconds']
        data=json.loads(row['data'])
        keys=('prompt_tokens','completion_tokens') if provider=='qwen' else ('input_tokens','output_tokens')
        for field, key in zip(('input_tokens','output_tokens'), keys):
            value=data.get(key) if isinstance(data,dict) else None
            if type(value) is not int or value < 0:
                totals[field]=None
            elif totals[field] is not None:
                totals[field]+=value
    for totals in summary.values():
        totals['total_tokens']=(totals['input_tokens']+totals['output_tokens']
                                if totals['input_tokens'] is not None and totals['output_tokens'] is not None else None)
    return summary


def main():
    parser=argparse.ArgumentParser(description='Kodi: sandboxed development, human-approved merges')
    parser.add_argument('--config',type=Path,default=DEFAULT_CONFIG)
    sub=parser.add_subparsers(dest='command',required=True)
    for name in ('doctor','bootstrap','tick','serve','list','sync'): sub.add_parser(name)
    usage=sub.add_parser('usage'); usage.add_argument('--summary',action='store_true')
    proxy=sub.add_parser('proxy'); proxy.add_argument('socket')
    enqueue=sub.add_parser('enqueue'); enqueue.add_argument('--repo',required=True)
    enqueue.add_argument('--title',required=True); enqueue.add_argument('--requirements-file',type=Path,required=True)
    enqueue.add_argument('--external-id',required=True)
    publish=sub.add_parser('publish'); publish.add_argument('job',type=int)
    submit=sub.add_parser('submit-pr',help='Run independent acceptance for an existing workspace PR')
    submit.add_argument('--repo',required=True)
    submit.add_argument('--checkout',type=Path,required=True)
    submit.add_argument('--pr-url',required=True)
    submit.add_argument('--title',required=True)
    submit.add_argument('--requirements-file',type=Path,required=True)
    submit.add_argument('--report-file',type=Path,required=True)
    notify=sub.add_parser('notify'); notify.add_argument('text')
    args=parser.parse_args()
    if args.command=='proxy': serve(args.socket); return
    config=json.loads(args.config.read_text()); coordinator=Coordinator(config)
    if args.command=='doctor':
        print('GitHub publisher:', 'App authenticated' if github.authenticated() else 'dedicated App required')
        for provider in ['codex','gemini']:
            root=Path(config['state_dir'])/'auth'/provider
            found=bool(list(root.glob('.codex/auth.json'))) if provider=='codex' else bool(list(root.glob('.gemini/oauth_creds.json')))
            print(provider+':','dedicated login present' if found else 'dedicated login required')
        print('Antigravity: dedicated account login must be verified with a sandbox review pilot')
        print('Reviewer:',config.get('reviewer','antigravity'))
        print('QA host:',config['qa_host'])
        print('Dashboard:',config.get('public_url','localhost only'))
        print('Paid API fallback: disabled')
    elif args.command=='bootstrap':
        config['board']=coordinator.board.bootstrap()
        args.config.write_text(json.dumps(config,indent=2)+'\n'); args.config.chmod(0o600)
        print('Development workspace and delivery workflow configured')
    elif args.command=='enqueue':
        job=coordinator.store.enqueue(args.external_id,args.repo,args.title,args.requirements_file.read_text())
        print('Queued job',job['id'])
    elif args.command=='list':
        for row in coordinator.store.db.execute('SELECT id,title,state,attempt,pr_url,error FROM jobs ORDER BY id'):
            print(json.dumps(dict(row)))
    elif args.command=='usage':
        rows=coordinator.store.db.execute('SELECT job,provider,model,data,seconds FROM usage ORDER BY id')
        if args.summary:
            print(json.dumps(usage_summary(rows)))
        else:
            for row in rows:
                print(json.dumps(dict(row)))
    elif args.command=='notify': send(config['telegram_env'],args.text); print('Notification delivered')
    elif args.command=='publish': coordinator.publish(args.job)
    elif args.command=='submit-pr':
        from .submission import submit_pr
        job=submit_pr(coordinator,args.repo,args.checkout,args.pr_url,args.title,
                      args.requirements_file.read_text(),json.loads(args.report_file.read_text()))
        print(json.dumps({'job':job['id'],'state':job['state'],'commit':job['commit_sha'],'pr_url':job['pr_url']}))
    elif args.command=='sync': coordinator.sync()
    elif args.command=='tick': coordinator.tick()
    elif args.command=='serve':
        # An interrupted run needs inspection rather than implicit duplicate execution.
        with coordinator.store.db:
            coordinator.store.db.execute("UPDATE jobs SET state='blocked',error='Coordinator restarted during execution; inspect before retry' WHERE state IN ('coding','qa','review','publishing')")
        from .telegram_control import start
        start(config)
        while True:
            try: coordinator.tick()
            except Exception as error: print('Coordinator cycle failed:',type(error).__name__,flush=True)
            time.sleep(30)


if __name__=='__main__': main()
