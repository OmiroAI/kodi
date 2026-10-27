"""Trusted GitHub operations stay outside the agent container."""
import json
import re
import subprocess
import time
from pathlib import Path
from .github_app import environment, token


def gh(*args, cwd=None, stdin=None):
    # Read-only operations may be repeated; uncertain writes require reconciliation.
    read_only = (args[:2] in (('pr','view'),('pr','list'),('issue','list')) or
                 args[:1]==('api',) and '--method' not in args and '-X' not in args
                 and '--input' not in args and not any(a in args for a in ('-f','-F')))
    from .recovery import transient
    for attempt in range(3):
        try:
            result = subprocess.run(['gh', *args], cwd=cwd, input=stdin, text=True,
                                    capture_output=True, timeout=120, env=environment())
        except subprocess.TimeoutExpired:
            if not read_only or attempt==2:
                raise RuntimeError('GitHub operation timed out; inspect state before retrying') from None
            time.sleep((2,10)[attempt]); continue
        if result.returncode and read_only and transient(result.stderr) and attempt<2:
            time.sleep((2,10)[attempt]); continue
        break
    if result.returncode:
        if (any('statusCheckRollup' in arg or '/check-runs' in arg for arg in args) and
                'Resource not accessible by integration' in result.stderr):
            raise RuntimeError('Private repository CI could not be read. The GitHub App owner must '
                               'select Checks Read-only in Permissions & events and accept updated '
                               'installation permissions, then retry after the cached token expires.')
        raise RuntimeError('GitHub operation failed; run gh auth status and verify repository access')
    return result.stdout.strip()


def authenticated():
    try:
        token()
        return True
    except (RuntimeError,OSError,ValueError,KeyError):
        return False


def issues(repo):
    return json.loads(gh('issue','list','--repo',repo,'--label','kodi:ready','--state','open',
                         '--limit','100','--json','number,title,body,url'))


def create_issue(repo, title, body):
    return gh('issue','create','--repo',repo,'--title',title,'--body-file','-',stdin=body)


def publish(repo, checkout, branch, title, body):
    # gh's credential helper is host-only. It is never copied into workers.
    subprocess.run(['git','-c','credential.helper=!gh auth git-credential',
                    '-c','core.hooksPath=/dev/null','push','origin',branch],
                   cwd=checkout, check=True, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, timeout=120, env=environment())
    existing = json.loads(gh('pr','list','--repo',repo,'--head',branch,'--state','open','--json','url',cwd=checkout))
    if existing:
        return existing[0]['url']
    return gh('pr','create','--repo',repo,'--head',branch,'--draft','--title',title,
              '--body-file','-',cwd=checkout,stdin=body)


def api_pages(endpoint):
    # Older host gh versions emit consecutive JSON documents for --paginate.
    remaining=gh('api',endpoint,'--paginate'); decoder=json.JSONDecoder()
    while remaining.strip():
        remaining=remaining.lstrip(); page,end=decoder.raw_decode(remaining)
        yield page
        remaining=remaining[end:]


def _check_outcome(pr_url, sha, required, allowed_skipped=()):
    if set(required)&set(allowed_skipped):
        raise ValueError('Required checks cannot allow skipped conclusions')
    # Avoid gh's GraphQL rollup: it requests workflowRun (Actions permission).
    match=re.fullmatch(r'https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/pull/([0-9]+)',pr_url)
    if not match or not re.fullmatch(r'[0-9a-f]{40}',sha):
        raise ValueError('Invalid PR URL or commit SHA')
    owner,name,number=match.groups(); repo=owner+'/'+name
    pr=json.loads(gh('api','repos/'+repo+'/pulls/'+number))
    if pr['head']['sha']!=sha: return 'pending'
    pages=api_pages('repos/'+repo+'/commits/'+sha+'/check-runs?filter=latest&per_page=100')
    runs=[run for page in pages for run in page['check_runs']]
    pages=api_pages('repos/'+repo+'/commits/'+sha+'/status?per_page=100')
    statuses=[status for page in pages for status in page['statuses']]
    names={run['name'] for run in runs}|{status['context'] for status in statuses}
    failed=any(run.get('head_sha')==sha and run.get('status')=='completed'
               and run.get('conclusion') not in ('success', None)
               and not (run.get('name') in allowed_skipped and run.get('conclusion')=='skipped')
               for run in runs) or any(s.get('state') in ('failure','error') for s in statuses)
    if failed: return 'failed'
    passed=(set(required)<=names
            and all(run.get('head_sha')==sha and run.get('status')=='completed'
                    and (run.get('conclusion')=='success' or
                         run.get('name') in allowed_skipped and run.get('conclusion')=='skipped')
                    for run in runs)
            and all(status.get('state')=='success' for status in statuses))
    return 'passed' if passed else 'pending'


def check_outcome(pr_url, sha, required, allowed_skipped=()):
    """Inspect CI without treating an unavailable API as acceptance."""
    try:
        return _check_outcome(pr_url,sha,required,allowed_skipped)
    except (RuntimeError, OSError, KeyError, TypeError, json.JSONDecodeError):
        return 'unavailable'


def checks_pass(pr_url, sha, required, allowed_skipped=()):
    # Compatibility for callers that require a boolean and actionable API errors.
    return _check_outcome(pr_url,sha,required,allowed_skipped)=='passed'


def ready(pr_url):
    gh('pr','ready',pr_url)


def request_reviewers(pr_url, sha, reviewers):
    match=re.fullmatch(r'https://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/pull/([0-9]+)',pr_url)
    if not match or not re.fullmatch('[a-f0-9]{40}',sha):
        raise ValueError('Invalid review request target')
    if not isinstance(reviewers,list) or not reviewers or any(
            not isinstance(r,str) or not re.fullmatch('[A-Za-z0-9][A-Za-z0-9-]{0,38}',r)
            for r in reviewers):
        raise ValueError('Invalid configured reviewers')
    repo,number=match.groups(); endpoint='repos/'+repo+'/pulls/'+number
    pr=json.loads(gh('api',endpoint))
    if pr['state']!='open' or pr['head']['sha']!=sha or pr.get('draft'):
        raise RuntimeError('PR changed before requesting review')
    # Never re-request an already approving reviewer on the same commit.
    approved=set()
    latest={}
    for page in api_pages(endpoint+'/reviews?per_page=100'):
        for review in page:
            if review['state']!='COMMENTED': latest[review['user']['login'].lower()]=review
    for login,review in latest.items():
        if review['state']=='APPROVED' and review['commit_id']==sha: approved.add(login)
    pending={r['login'].lower() for r in pr.get('requested_reviewers',[])}
    missing=[r for r in dict.fromkeys(reviewers) if r.lower() not in pending|approved
             and r.lower()!=pr['user']['login'].lower()]
    if missing:
        gh('api','--method','POST',endpoint+'/requested_reviewers','--input','-',
           stdin=json.dumps({'reviewers':missing}))


def approved_at(pr_url,sha,logins):
    match=re.fullmatch(r'https://github\.com/([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)/pull/([0-9]+)',pr_url)
    if not match or not re.fullmatch('[a-f0-9]{40}',sha):raise ValueError('Invalid review target')
    repo,number=match.groups();latest={}
    for page in api_pages(f'repos/{repo}/pulls/{number}/reviews?per_page=100'):
        for review in page:
            if review['state']!='COMMENTED':latest[review['user']['login'].lower()]=review
    return any(login.lower() in latest and latest[login.lower()]['state']=='APPROVED'
        and latest[login.lower()]['commit_id']==sha
        and latest[login.lower()]['user'].get('type','User')=='User' for login in logins)


def pr_state(pr_url):
    """Read lifecycle state; approval is deliberately not a terminal event."""
    value = json.loads(gh('pr','view',pr_url,'--json','state'))
    if not isinstance(value,dict) or value.get('state') not in ('OPEN','MERGED','CLOSED'):
        raise ValueError('Invalid GitHub PR state')
    return value['state']


def independent_status(repo, sha, pr_url):
    gh('api','--method','POST','repos/'+repo+'/statuses/'+sha,
       '-f','state=success','-f','context=kodi/independent-acceptance',
       '-f','description=Independent QA and different-model review passed',
       '-f','target_url='+pr_url)


def submission_status(repo, sha, pr_url, failed=False):
    gh('api','--method','POST','repos/'+repo+'/statuses/'+sha,
       '-f','state='+('failure' if failed else 'pending'),
       '-f','context=kodi/independent-acceptance',
       '-f','description='+('Independent validation failed; inspect submission evidence' if failed
                          else 'Independent workspace PR validation in progress'),
       '-f','target_url='+pr_url)


def pull(pr_url):
    return json.loads(gh('pr','view',pr_url,'--json',
        'state,isDraft,reviewDecision,headRefName,headRefOid,baseRefName,headRepository,headRepositoryOwner,autoMergeRequest,number'))


def remote_head(repo, branch):
    result = subprocess.run(['git','-c','credential.helper=',
        '-c','credential.helper=!gh auth git-credential','-c','core.hooksPath=/dev/null',
        'ls-remote','https://github.com/'+repo+'.git','refs/heads/'+branch],
        capture_output=True,text=True,timeout=120,env=environment())
    if result.returncode:
        raise RuntimeError('Could not verify remote PR branch')
    rows = result.stdout.splitlines()
    return rows[0].split()[0] if len(rows)==1 else None


def refresh_pull(repo, checkout, branch, old_sha, sha, number, body):
    # Explicit lease and destination: never trust checkout push configuration.
    result = subprocess.run(['git','-c','credential.helper=',
        '-c','credential.helper=!gh auth git-credential','-c','core.hooksPath=/dev/null',
        'push','--force-with-lease=refs/heads/'+branch+':'+old_sha,
        'https://github.com/'+repo+'.git',sha+':refs/heads/'+branch],
        cwd=checkout,capture_output=True,timeout=120,env=environment())
    if result.returncode:
        raise RuntimeError('PR refresh failed; expected remote head lease was not changed')
    gh('api','--method','PATCH','repos/'+repo+'/pulls/'+str(number),
       '--input','-',stdin=json.dumps({'body':body}))


def auto_merge(pr_url, sha):
    gh('pr','merge',pr_url,'--auto','--squash','--match-head-commit',sha)
