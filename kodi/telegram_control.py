"""Owner-only Telegram control; no model interprets or authorizes commands."""
import fcntl
import json
import re
import secrets
import threading
import time
from pathlib import Path

from . import github,owner_review
from .notify import TelegramAPI
from .telegram_state import State
from .whisper import transcribe

HELP=('Send a message or voice note to draft a ticket, choose its repository, then tap Create ticket.\n'
      'Example: project: Make the volume menu clearer\n\n'
      '/prs — ready PRs with approval buttons\n'
      '/approve project 42 — approve the current verified commit\n'
      '/drafts — unfinished ticket drafts\n'
      'Voice notes always become ticket drafts, never approval commands.')


def approval_item(config,state,job,chat,origin=None):
    options=config['telegram'];user=options['approver_user_id']
    # A fresh card may be requested after an old card expires or an uncertain action.
    prefix=f"approval:{job['id']}:{job['commit_sha']}:{user}:"
    if origin is None:
        pending=state.db.execute("SELECT * FROM items WHERE origin LIKE ? AND chat_id=? AND state='pending' AND expires>? ORDER BY created DESC LIMIT 1",(prefix+'%',chat,time.time())).fetchone()
        if pending:return pending
        origin=prefix+secrets.token_hex(6)
    return state.item(origin,'approval',user,chat,{'job':job['id'],'sha':job['commit_sha']})


def approval_markup(config,job):
    if config.get('telegram',{}).get('enabled') is not True:return None
    api=TelegramAPI(config['telegram_env']);state=State(config['state_dir'])
    try:item=approval_item(config,state,job,api.chat_id)
    finally:state.db.close()
    return {'inline_keyboard':[[{'text':'Approve '+job['commit_sha'][:8],
                                'callback_data':'approve:'+item['id']},
                               {'text':'Open PR','url':job['pr_url']}]]}


class Control:
    def __init__(self,config,api=None):
        from .coordinator import Coordinator
        self.config=config;self.options=config['telegram']
        self.api=api or TelegramAPI(config['telegram_env'])
        self.state=State(config['state_dir']);self.coordinator=Coordinator(config)
        allowed=self.options.get('allowed_user_ids',[])
        approver=self.options.get('approver_user_id')
        if not allowed or any(type(x) is not int or x<=0 for x in allowed) or approver not in allowed:
            raise ValueError('Configure explicit Telegram users and a human approver')
        if type(approver) is not int or self.api.chat_id<=0:
            raise ValueError('Telegram control requires the configured private owner chat')
        if not re.fullmatch('[A-Za-z0-9][A-Za-z0-9-]{0,38}',self.options.get('github_approver','')):
            raise ValueError('Configure the GitHub human approver login')
        self.allowed=set(allowed)

    def close(self):
        self.state.db.close();self.coordinator.store.db.close()

    def authorized(self,update):
        callback=update.get('callback_query');message=(callback or {}).get('message') if callback else update.get('message')
        if not isinstance(message,dict):return None
        sender=(callback or {}).get('from') if callback else message.get('from')
        chat=message.get('chat',{})
        if not isinstance(sender,dict) or sender.get('is_bot') or sender.get('id') not in self.allowed:
            return None
        if type(sender.get('id')) is not int:return None
        if chat.get('id')!=self.api.chat_id or chat.get('type')!='private':return None
        if message.get('forward_origin') or message.get('forward_date') or message.get('via_bot') or message.get('sender_chat'):
            return None
        return sender['id'],chat['id'],message

    def handle(self,update):
        identity=self.authorized(update)
        if not identity:return
        user,chat,message=identity
        try:
            if 'callback_query' in update:
                callback=update['callback_query']
                try:self.api.call('answerCallbackQuery',{'callback_query_id':callback['id']})
                except RuntimeError:pass
                self.callback(user,chat,callback.get('data',''))
                return
            if self.clarification_reply(chat,message):return
            if 'voice' in message:
                voice=message['voice']
                if voice.get('duration',0)>self.options.get('max_voice_seconds',300):
                    raise ValueError('Please split voice notes longer than five minutes.')
                limit=self.options.get('max_voice_bytes',10*1024*1024)
                if voice.get('file_size',0)>limit:raise ValueError('Voice note is too large.')
                audio=self.api.download_voice(voice['file_id'],limit)
                text=transcribe(self.options,audio,voice.get('mime_type','audio/ogg'))
                # Never route a transcript through the command parser.
                self.draft(user,chat,message['message_id'],text,voice=True)
                return
            text=message.get('text','').strip()
            if not text:return
            if text.startswith('/'):
                command,*rest=text.split(maxsplit=1);command=command.split('@')[0].lower()
                args=rest[0] if rest else ''
                if command in ('/start','/help'):
                    self.api.send(HELP);return
                if command=='/prs':self.prs(user,chat);return
                if command=='/drafts':
                    rows=self.state.db.execute("SELECT * FROM items WHERE kind='ticket' AND user_id=? AND chat_id=? AND state IN ('pending','creating','uncertain') AND expires>? ORDER BY created DESC LIMIT 5",(user,chat,time.time())).fetchall()
                    for row in rows:self.show_draft(row)
                    if not rows:self.api.send('No unfinished ticket drafts.')
                    return
                if command=='/approve':
                    parts=args.split()
                    if len(parts)!=2 or not parts[1].lstrip('#').isdigit():
                        self.api.send('Use /approve <repo> <PR number>, or /prs for approval buttons.');return
                    key=self.repo_key(parts[0]);job=self.job_for_pr(key,int(parts[1].lstrip('#')))
                    item=approval_item(self.config,self.state,job,chat,origin=f"approval-message:{chat}:{message['message_id']}")
                    self.approve(user,chat,item['id']);return
                if command=='/ticket':
                    self.draft(user,chat,message['message_id'],args);return
                self.api.send(HELP);return
            self.draft(user,chat,message['message_id'],text)
        except ValueError as error:
            self.api.send(str(error))
        except Exception:
            # Provider/subprocess errors can contain credentials or token-bearing URLs.
            self.api.send('That action could not be completed. Use /prs or /drafts to check its status before retrying.')

    def clarification_reply(self,chat,message):
        reply=message.get('reply_to_message')
        if not isinstance(reply,dict) or type(reply.get('message_id')) is not int:return False
        row=self.coordinator.store.db.execute('SELECT c.*,j.state AS job_state FROM clarifications c '
            'JOIN jobs j ON j.id=c.job WHERE telegram_chat=? AND telegram_message=?',
            (chat,reply['message_id'])).fetchone()
        if not row:
            text=reply.get('text','')
            reference=re.match(r'^\[Kodi input: ([0-9a-f]{32})\](?:\n|$)',text) if isinstance(text,str) else None
            if reference:
                row=self.coordinator.store.db.execute('SELECT c.*,j.state AS job_state FROM clarifications c '
                    'JOIN jobs j ON j.id=c.job WHERE telegram_chat=? AND question_token=?',
                    (chat,reference[1])).fetchone()
                if not row:
                    self.api.send('That question is no longer available. Check the task in KanDev; this reply was not treated as a command.')
                    return True
        if not row:
            text=reply.get('text','')
            legacy=re.fullmatch(r'Kodi needs input: [^\n]+\nTask: ([^\n]+)\n\n(.+)\n\nReply directly to this message with your answer\.',text,re.S) if isinstance(text,str) else None
            if legacy:
                # Older notifications had no token. Recover only an exact question
                # on the named task; never let an obsolete question create a ticket.
                rows=self.coordinator.store.db.execute('SELECT c.*,j.state AS job_state FROM clarifications c '
                    'JOIN jobs j ON j.id=c.job WHERE telegram_chat=? AND j.board_id=?',
                    (chat,legacy[1])).fetchall()
                matches=[candidate for candidate in rows if candidate['result'] and
                    json.loads(candidate['result']).get('question')==legacy[2]]
                if len(matches)==1:row=matches[0]
                else:
                    self.api.send('That question is no longer available. Check the task in KanDev; this reply was not treated as a command.')
                    return True
        if not row:return False
        from .clarification import Intake
        from .clarification_worker import options
        intake=Intake(self.coordinator)
        latest=intake.latest(row['job'])
        if not options(self.config):
            self.api.send('Ticket clarification is currently disabled. Your task is still saved.');return True
        if row['state']!='waiting' or row['job_state']!='needs_input' or latest['revision']!=row['revision'] or row['answer'] is not None:
            self.api.send('That question has already been answered or superseded. Use the latest question or update the task in KanDev.');return True
        text=message.get('text','').strip()
        if not text:
            self.api.send('Reply with a text answer, or add it in KanDev and move the task to Ready.');return True
        accepted=intake.answer(row['job'],row['revision'],text,f"telegram:{chat}:{message['message_id']}")
        self.api.send('Answer saved on the same task. Kodi will recheck it before coding.' if accepted else 'This question already has an answer.')
        return True

    def repo_key(self,name):
        for key,repo in self.config['repos'].items():
            if name.lower() in (key.lower(),repo['github_repo'].split('/')[1].lower()) and repo.get('enabled',True) is True:
                return key
        raise ValueError('Unknown or disabled repository. Choose one of the configured repositories.')

    def draft(self,user,chat,message_id,text,voice=False):
        if not text or len(text)>20000:raise ValueError('Send ticket details between 1 and 20,000 characters.')
        repo=None
        for key,entry in self.config['repos'].items():
            if entry.get('enabled',True) is not True:continue
            aliases={key,entry['github_repo'].split('/')[1]}
            for alias in sorted(aliases,key=len,reverse=True):
                match=re.match(r'^(?:create\s+(?:a\s+)?ticket\s+(?:for|in)\s+)?'+re.escape(alias)+r'(?:\s*[:,]\s*|\s+)(.+)$',text,re.I|re.S)
                if match:repo=key;text=match.group(1).strip();break
            if repo:break
        data={'repo':repo,'title':text.splitlines()[0][:120],'body':text,'voice':voice}
        item=self.state.item(f'message:{chat}:{message_id}','ticket',user,chat,data)
        self.show_draft(item)

    def show_draft(self,item):
        if item['state']!='pending':
            data=json.loads(item['data']);result=json.loads(item['result'] or '{}')
            if item['state']=='creating':
                label='Ticket creation needs a status check.'
                if result.get('issue_url'):label='Ticket created; queue delivery needs a status check.\n'+result['issue_url']
                self.api.send(label,{'inline_keyboard':[[{'text':'Check / resume','callback_data':f"create:{item['id']}:{data['repo']}"}]]})
            elif item['state']=='uncertain':
                self.api.send('Creation could not be confirmed. Check the repository before sending a new draft.')
            elif result.get('issue_url'):self.api.send('Already created: '+result['issue_url'])
            else:self.api.send('This draft has already been handled.')
            return
        data=json.loads(item['data']);repo=data.get('repo')
        text=('Voice ticket draft — transcript:\n' if data.get('voice') else 'Ticket draft:\n')+data['body'][:3100]
        if len(data['body'])>3100:text+='\n[Preview shortened; the full text will be used.]'
        text+='\n\nRepository: '+(self.config['repos'][repo]['github_repo'] if repo else 'choose below')
        rows=[]
        choices=[(key,r) for key,r in self.config['repos'].items() if r.get('enabled',True) is True]
        for start in range(0,len(choices),2):
            rows.append([{'text':r['github_repo'].split('/')[1], 'callback_data':f"repo:{item['id']}:{key}"} for key,r in choices[start:start+2]])
        if repo:rows.append([{'text':'Create ticket','callback_data':f"create:{item['id']}:{repo}"}])
        rows.append([{'text':'Cancel','callback_data':'cancel:'+item['id']}])
        self.api.send(text,{'inline_keyboard':rows})

    def callback(self,user,chat,data):
        parts=data.split(':')
        if len(parts) not in (2,3):raise ValueError('Unknown action.')
        action,id=parts[:2];item=self.state.get(id,user,chat)
        if action=='approve' and item['kind']=='approval':self.approve(user,chat,id);return
        if item['kind']!='ticket':raise ValueError('Unknown action.')
        if action=='create':
            if len(parts)!=3 or json.loads(item['data']).get('repo')!=parts[2]:
                raise ValueError('The draft repository changed. Use its latest card before creating the ticket.')
            self.create_ticket(item);return
        if item['state']!='pending':raise ValueError('This ticket draft has already been handled.')
        if action=='cancel':self.state.update(id,state='cancelled');self.api.send('Draft cancelled.');return
        if action=='repo' and len(parts)==3:
            payload=json.loads(item['data']);payload['repo']=self.repo_key(parts[2]);self.state.update(id,data=payload)
            self.show_draft(self.state.get(id,user,chat));return
        raise ValueError('Unknown action.')

    def find_ticket(self,repo,marker):
        for i,page in enumerate(github.api_pages('repos/'+repo+'/issues?state=all&sort=created&direction=desc&per_page=100')):
            for issue in page:
                if marker in (issue.get('body') or '') and 'pull_request' not in issue:return issue['html_url']
            if i>=4:break
        return None

    def create_ticket(self,item):
        data=json.loads(item['data']);key=data.get('repo')
        if key is None:raise ValueError('Choose a repository first.')
        self.repo_key(key);repo=self.config['repos'][key]['github_repo'];id=item['id']
        result=json.loads(item['result'] or '{}');url=result.get('issue_url')
        if item['state']=='done':self.api.send('Already created: '+url);return
        if item['state'] in ('cancelled','uncertain'):raise ValueError('This draft cannot be submitted again. Check the repository before making a new draft.')
        marker='<!-- kodi-telegram-ticket:'+id+' -->'
        if item['state']=='creating' and not url:
            url=self.find_ticket(repo,marker)
            if not url:
                self.state.update(id,state='uncertain')
                raise ValueError('The previous creation could not be confirmed. Check the repository before making a new draft.')
        if not url:
            if not self.state.claim(id,'pending','creating'):raise ValueError('This ticket is already being created.')
            body=data['body']+'\n\n'+marker
            url=github.create_issue(repo,data['title'],body)
        prefix='https://github.com/'+repo+'/issues/'
        if not url.startswith(prefix) or not url[len(prefix):].isdigit():raise RuntimeError('Unexpected issue destination')
        self.state.update(id,state='creating',result={'issue_url':url})
        number=url[len(prefix):]
        issue=json.loads(github.gh('api','repos/'+repo+'/issues/'+number))
        if 'kodi:ready' not in {label['name'] for label in issue.get('labels',[])}:
            github.gh('api','--method','POST','repos/'+repo+'/issues/'+number+'/labels','--input','-',stdin=json.dumps({'labels':['kodi:ready']}))
        self.state.update(id,state='done',result={'issue_url':url})
        self.api.send('Created and queued for Kodi: '+url)

    def job_for_pr(self,key,number):
        repo=self.config['repos'][key]['github_repo'];url=f'https://github.com/{repo}/pull/{number}'
        job=self.coordinator.store.db.execute('SELECT * FROM jobs WHERE repo=? AND pr_url=?',(key,url)).fetchone()
        if not job:raise ValueError('This PR is not tracked as a ready Kodi task.')
        return job

    def validate_approval(self,job,sha):
        if job['state']!='human_review' or job['commit_sha']!=sha:
            raise ValueError('This PR changed or is not ready. Use /prs for its current version.')
        if self.config['repos'][job['repo']].get('enabled',True) is not True:
            raise ValueError('This repository is disabled.')
        if not self.coordinator.store.gate(job['id'],sha):raise ValueError('Independent validation is not complete.')
        pr=github.pull(job['pr_url'])
        if pr['state']!='OPEN' or pr['isDraft'] or pr['headRefOid']!=sha:
            raise ValueError('This PR changed or closed. Use /prs for its current version.')
        if not self.coordinator.checks_pass(job):raise ValueError('GitHub checks are not passing yet.')
        return pr

    def approve(self,user,chat,id):
        if user!=self.options['approver_user_id']:raise ValueError('Only the configured human approver may approve PRs.')
        item=self.state.get(id,user,chat)
        if item['kind']!='approval':raise ValueError('Unknown approval action.')
        data=json.loads(item['data']);job=self.coordinator.store.get(data['job']);sha=data['sha']
        if job is None:raise ValueError('This Kodi task no longer exists.')
        pr=self.validate_approval(job,sha);repo=self.config['repos'][job['repo']]['github_repo']
        login=self.options['github_approver']
        if github.approved_at(job['pr_url'],sha,[login]):
            self.state.update(id,state='done',result={'sha':sha});self.api.send('Already approved on GitHub: '+job['pr_url']);return
        if item['state']!='pending':
            raise ValueError('The previous approval could not be confirmed. Check GitHub before requesting another approval.')
        if not self.state.claim(id,'pending','approving'):raise ValueError('Approval is already being submitted.')
        review=owner_review.approve(repo,pr['number'],sha,login,id)
        if review.get('state')!='APPROVED' or review.get('commit_id')!=sha or review.get('user',{}).get('login','').lower()!=login.lower():
            raise RuntimeError('Owner approval did not match its target')
        # If the PR moves concurrently, never claim that the new head is approved.
        current=github.pull(job['pr_url'])
        if current['headRefOid']!=sha:
            self.state.update(id,state='stale',result={'review_id':review['id'],'sha':sha})
            raise ValueError('The PR changed during approval. Your approval applies only to '+sha[:8]+'. Use /prs for the new version.')
        self.state.update(id,state='done',result={'review_id':review['id'],'sha':sha})
        suffix='Kodi will follow the protected merge process.' if self.config['repos'][job['repo']].get('auto_merge') is True else 'This repository still uses manual merging.'
        self.api.send('Approved '+sha[:8]+' on GitHub: '+job['pr_url']+'\n'+suffix)

    def prs(self,user,chat):
        if user!=self.options['approver_user_id']:raise ValueError('Only the configured approver can request approval cards.')
        rows=self.coordinator.store.db.execute("SELECT * FROM jobs WHERE pr_url IS NOT NULL AND state NOT IN ('merged','closed','cancelled') ORDER BY id").fetchall();count=0
        for job in rows:
            count+=1
            try:
                self.validate_approval(job,job['commit_sha'])
            except ValueError as error:
                self.api.send(job['pr_url']+'\nCommit: '+str(job['commit_sha'])+'\n'+str(error)+
                              '\nWait for current QA, review, CI or refresh; inspect the task if blocked.')
                continue
            except Exception:
                self.api.send(job['pr_url']+'\nGitHub status is unavailable. Retry /prs before approving.')
                continue
            text=self.coordinator.ready_text(job)
            if github.approved_at(job['pr_url'],job['commit_sha'],[self.options['github_approver']]):
                self.api.send(text+'\nAlready approved. '+job['pr_url'])
            else:
                item=approval_item(self.config,self.state,job,chat)
                self.api.send(text,{'inline_keyboard':[[{'text':'Approve '+job['commit_sha'][:8],'callback_data':'approve:'+item['id']},{'text':'Open PR','url':job['pr_url']}]]})
        if not count:self.api.send('No Kodi PRs are ready for approval.')

    def poll(self):
        offset=self.state.setting('offset')
        if offset is None:
            # Notification-only historical messages never become new commands.
            old=self.api.call('getUpdates',{'offset':-1,'timeout':0,'limit':1,'allowed_updates':['message','callback_query']})
            offset=(old[-1]['update_id']+1) if old else 0
            self.state.setting('offset',offset)
        updates=self.api.call('getUpdates',{'offset':int(offset),'timeout':20,'allowed_updates':['message','callback_query']},timeout=30)
        for update in updates:
            self.handle(update)
            self.state.setting('offset',update['update_id']+1)


def serve(config):
    lock_path=Path(config['state_dir'])/'telegram.lock'
    with lock_path.open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            print('Telegram control already running',flush=True);return
        control=Control(config)
        try:
            info=control.api.call('getWebhookInfo')
            if info.get('url'):raise RuntimeError('Telegram webhook is already configured; control polling not started')
            chat=control.api.call('getChat',{'chat_id':control.api.chat_id})
            if chat.get('type')!='private':raise RuntimeError('Telegram control requires a private owner chat')
            try:
                control.api.call('setMyCommands',{'commands':[
                    {'command':'start','description':'How to create tickets and approve PRs'},
                    {'command':'prs','description':'Ready PRs and approval buttons'},
                    {'command':'ticket','description':'Draft a ticket: repo followed by details'},
                    {'command':'drafts','description':'Unfinished ticket drafts and status'},
                    {'command':'approve','description':'Approve a verified PR: repo and PR number'}]})
            except RuntimeError:print('Telegram command menu setup could not be confirmed',flush=True)
            print('Telegram control listening in the configured private owner chat',flush=True)
            while True:
                try:control.poll()
                except Exception as error:
                    print('Telegram control cycle failed:',type(error).__name__,flush=True);time.sleep(5)
        finally:control.close()


def start(config):
    if config.get('telegram',{}).get('enabled') is not True:return
    thread=threading.Thread(target=serve,args=(config,),name='kodi-telegram',daemon=True)
    thread.start()
    return thread
