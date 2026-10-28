import argparse
import json
import subprocess
import time
from pathlib import Path

from .coordinator import Coordinator
from .egress import serve
from .notify import send
from . import github
from .usage import usage_summary, usage_report

DEFAULT_CONFIG=Path.home()/'.config/omiro-dev/config.json'


def main():
    parser=argparse.ArgumentParser(description='Kodi: sandboxed development, human-approved merges')
    parser.add_argument('--config',type=Path,default=DEFAULT_CONFIG)
    sub=parser.add_subparsers(dest='command',required=True)
    for name in ('doctor','bootstrap','tick','serve','list','sync'): sub.add_parser(name)
    activity=sub.add_parser('sync-usage', help='Bounded dashboard usage backfill and retry')
    activity.add_argument('--limit', type=int, choices=range(1,101), default=100, metavar='1..100')
    activity.add_argument('--status', action='store_true')
    usage=sub.add_parser('usage')
    grouping=usage.add_mutually_exclusive_group()
    grouping.add_argument('--summary',action='store_true')
    grouping.add_argument('--daily',action='store_true')
    grouping.add_argument('--tasks',action='store_true')
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
    recovery=sub.add_parser('recover', help='Host-only scoped retained-candidate recovery')
    recovery.add_argument('action', choices=('preview','authorize','apply','track'))
    recovery.add_argument('job', type=int)
    recovery.add_argument('--stage', choices=('qa','review'), default='review')
    recovery.add_argument('--digest', default='')
    recovery.add_argument('--owner-reference', default='')
    args=parser.parse_args()
    if args.command=='proxy': serve(args.socket); return
    config=json.loads(args.config.read_text())
    if args.command=='recover' and args.action=='preview':
        # Opening a preview must not initialize/migrate state or create WAL files.
        from types import SimpleNamespace
        from .store import Store
        from .host_recovery import preview
        coordinator=SimpleNamespace(
            config=config,
            store=Store(Path(config['state_dir'])/'kodi.sqlite', read_only=True),
            job_branch=Coordinator.job_branch)
        try: print(json.dumps(preview(coordinator,args.job,args.stage)))
        finally: coordinator.store.db.close()
        return
    coordinator=Coordinator(config)
    if args.command=='recover':
        from .host_recovery import authorize, apply, track
        if args.action=='authorize':
            authorize(coordinator,args.job,args.stage,args.digest,args.owner_reference)
            print(json.dumps({'authorized':args.job}))
        elif args.action=='track':
            print(json.dumps({'board_id':track(coordinator,args.job)}))
        else: print(json.dumps(apply(coordinator,args.job,args.stage,args.digest)))
    elif args.command=='doctor':
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
            record = dict(row)
            record['invocation'] = coordinator.store.invocation_status(row['id'])
            print(json.dumps(record))
    elif args.command=='sync-usage':
        if args.status:
            from .activity import ActivitySync
            print(json.dumps(ActivitySync(coordinator.store, coordinator.board).status()))
        else:
            print(json.dumps(coordinator.sync_activity(args.limit)))
    elif args.command=='usage':
        rows=coordinator.store.db.execute('SELECT job,provider,model,data,seconds FROM usage ORDER BY id')
        if args.daily or args.tasks:
            print(json.dumps(usage_report(coordinator.store, 'daily' if args.daily else 'tasks')))
        elif args.summary:
            print(json.dumps(usage_summary(rows)))
        else:
            for row in rows:
                print(json.dumps(dict(row)))
    elif args.command=='notify': send(config['telegram_env'],args.text); print('Notification delivered')
    elif args.command=='publish': coordinator.publish_control(args.job)
    elif args.command=='submit-pr':
        from .submission import submit_pr
        job=submit_pr(coordinator,args.repo,args.checkout,args.pr_url,args.title,
                      args.requirements_file.read_text(),json.loads(args.report_file.read_text()))
        print(json.dumps({'job':job['id'],'state':job['state'],'commit':job['commit_sha'],'pr_url':job['pr_url']}))
    elif args.command=='sync': coordinator.sync()
    elif args.command=='tick': coordinator.tick()
    elif args.command=='serve':
        # Durable invocations retain ownership across restart. Unknown liveness
        # requires inspection, never replacement or implicit budget reset.
        from .telegram_control import start
        start(config)
        while True:
            try: coordinator.tick()
            except Exception as error: print('Coordinator cycle failed:',type(error).__name__,flush=True)
            time.sleep(30)


if __name__=='__main__': main()
