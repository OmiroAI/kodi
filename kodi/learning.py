"""Deterministic learning admission. Feedback never grants scheduling authority."""
import json
from datetime import datetime, timezone


def policy(config):
    value = config.get('learning_schedule', {})
    if not isinstance(value, dict):
        raise ValueError('learning_schedule must be a mapping')
    bound = value.get('max_product_claims', 8)
    if type(bound) is not int or not 1 <= bound <= 1000:
        raise ValueError('max_product_claims must be an integer from 1 to 1000')
    return value.get('authorized') is True, bound


def timestamp(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result


def waiting_reason(config, job):
    if not job['external_id'].startswith('learning:'):
        return None
    authorized, _ = policy(config)
    repo = config.get('repos', {}).get(job['repo'])
    if not repo or repo.get('enabled', True) is not True:
        return 'repository disabled or absent'
    if not authorized:
        return 'waiting for owner learning scheduling authorization'
    if job['state'] != 'queued':
        return 'learning is '+job['state']
    try:
        source = json.loads(job['requirements'].split('Untrusted feedback data:\n', 1)[1])['pr']
        expected = 'https://github.com/'+repo['github_repo']+'/pull/'
        if source['url'] != expected+str(source['number']):
            return 'source repository identity changed'
        if timestamp(source['mergedAt']) < timestamp(config['learning_since']):
            return 'source predates learning cutoff'
    except (KeyError, ValueError, TypeError, IndexError, AttributeError):
        return 'missing source provenance or learning cutoff'
    return None


def scheduler_state(store):
    row = store.db.execute("SELECT data FROM evidence WHERE job=0 AND kind='learning_scheduler' ORDER BY id LIMIT 1").fetchone()
    return json.loads(row['data']) if row else {'product_claims': 0, 'learning_claims': 0}


def record_claim(store, learning):
    state = scheduler_state(store)
    state['product_claims'] = 0 if learning else min(1000, state['product_claims']+1)
    state['learning_claims'] = min(2147483647, state['learning_claims']+int(learning))
    # A single bounded record is updated in the same transaction as the claim.
    row = store.db.execute("SELECT id FROM evidence WHERE job=0 AND kind='learning_scheduler' ORDER BY id LIMIT 1").fetchone()
    if row:
        store.db.execute('UPDATE evidence SET data=? WHERE id=?', (json.dumps(state), row['id']))
    else:
        from .store import now
        store.db.execute("INSERT INTO evidence(job,sha,kind,data,created) VALUES (0,'','learning_scheduler',?,?)", (json.dumps(state), now()))


def model_feedback(store, repo, source_url, accepted_sha=None):
    """Read accepted finding dispositions from the original task's private ledger.

    Review prose remains untrusted. Only independently source/test-resolved fixes
    on the final accepted candidate are eligible for lesson inspection.
    """
    results = []
    for job in store.db.execute("SELECT * FROM jobs WHERE repo=? AND pr_url=? AND state='done' ORDER BY id",
                                (repo, source_url)).fetchall():
        if job['external_id'].startswith('learning:') or not job['commit_sha']:
            continue
        sha = job['commit_sha']
        if accepted_sha is not None and sha != accepted_sha:
            continue
        if not store.gate(job['id'], sha):
            continue
        review = store.db.execute("SELECT data FROM evidence WHERE job=? AND sha=? AND kind='review' ORDER BY id DESC LIMIT 1",
                                  (job['id'], sha)).fetchone()
        if not review:
            continue
        verdict = json.loads(review['data'])
        for finding in store.findings(job['id']):
            dispositions = [d for d in verdict.get('dispositions', []) if isinstance(d, dict) and d.get('finding_id') == finding['id']]
            if len(dispositions) != 1 or dispositions[0].get('status') != 'fixed with source/test evidence':
                continue
            if store.finding_blockers(job['id'], sha, verdict):
                continue
            results.append({'id': 'model:'+str(job['id'])+':'+finding['id'],
                'original_task': job['external_id'], 'reviewed_sha': finding['sha'],
                'accepted_sha': sha, 'reviewer': {'provider': finding['provider'], 'model': finding['model'],
                    'family': finding['reviewer_family']},
                'finding': finding['finding'], 'accepted_fix': dispositions[0], 'html_url': source_url})
    return results


def observe_collection(store, job):
    """Bounded per-source counters; recollection never changes scheduling state."""
    with store.write(job):
        row = store.db.execute("SELECT id,data FROM evidence WHERE job=? AND kind='learning_tracking' ORDER BY id LIMIT 1", (job,)).fetchone()
        value = json.loads(row['data']) if row else {'collections': 0, 'duplicates_avoided': 0}
        value['collections'] = min(2147483647, value['collections']+1)
        value['duplicates_avoided'] = min(2147483647, value['duplicates_avoided']+int(row is not None))
        if row:
            store.db.execute('UPDATE evidence SET data=? WHERE id=?', (json.dumps(value), row['id']))
        else:
            from .store import now
            store.db.execute("INSERT INTO evidence(job,sha,kind,data,created) VALUES (?,'','learning_tracking',?,?)", (job, json.dumps(value), now()))


def validate_context_edit(checkout, paths):
    from pathlib import Path
    root = Path(checkout)
    path = root/'docs/kodi-context.md'
    if paths != ['docs/kodi-context.md']:
        raise ValueError('Learning may change only curated repository context')
    if (root/'docs').is_symlink() or path.is_symlink() or not path.is_file():
        raise ValueError('Learning context must be an ordinary file')
    with path.open('rb') as stream:
        raw = stream.read(12001)
    if len(raw) > 12000:
        raise ValueError('Learning context exceeds 12000-byte budget')
    raw.decode('utf-8')


def metrics(store, job, **values):
    """One bounded observation per job, durable across collection and retries."""
    with store.write(job):
        row = store.db.execute("SELECT id,data FROM evidence WHERE job=? AND kind='learning_metrics' LIMIT 1", (job,)).fetchone()
        data = json.loads(row['data']) if row else {}
        if not values:
            return data
        for key, value in values.items():
            if type(value) is not int:
                raise ValueError('Learning metrics require integer observations')
            data[key] = max(-12000, min(2147483647, value))
        if row:
            store.db.execute('UPDATE evidence SET data=? WHERE id=?', (json.dumps(data), row['id']))
        else:
            from .store import now
            store.db.execute("INSERT INTO evidence(job,sha,kind,data,created) VALUES (?,'','learning_metrics',?,?)", (job, json.dumps(data), now()))
    return data


def promotion_prompt():
    return ('\nFor any context edit return lesson_promotions: a list of {source_id, lesson, '
            'replaces, source_path, source_excerpt, test_path, test_excerpt}. '
            'lesson is the exact new source-linked guidance; replaces is exact superseded '
            'guidance or empty. Use only a verified repository-specific failure with an accepted '
            'fix, never approval, speculation or a summary. Source and test excerpts must occur '
            'in the accepted source. Preserve unrelated guidance, replace superseded text, '
            'target 10000 UTF-8 bytes and never exceed 12000. Keep raw feedback private. '
            'Normal independent acceptance and human approval apply; no documentation exception.')


def validate_promotions(checkout, job, author, baseline='HEAD'):
    """Verify mechanical provenance and exact replacement; semantic QA remains independent."""
    from pathlib import Path, PurePosixPath
    from .workspace import git
    import subprocess
    import re
    record = json.loads(job['requirements'].split('Untrusted feedback data:\n', 1)[1])
    sha = record['pr']['headRefOid']
    if not re.fullmatch('[0-9a-f]{40}', sha or ''):
        raise ValueError('Missing accepted source identity')
    sources = {str(c['id']): c for c in record['comments']}
    promotions = author.get('lesson_promotions')
    if not isinstance(promotions, list) or not promotions:
        raise ValueError('Context edit requires verified lesson promotions')
    try:
        before = subprocess.check_output(['git', 'show', baseline+':docs/kodi-context.md'],
                                         cwd=checkout, stderr=subprocess.DEVNULL, timeout=60).decode('utf-8')
    except subprocess.CalledProcessError:
        before = ''
    after = (Path(checkout)/'docs/kodi-context.md').read_text(encoding='utf-8')
    expected = before
    seen = set()
    for item in promotions:
        identity = str(item['source_id'])
        source = sources.get(identity)
        if not source or identity in seen:
            raise ValueError('Unknown or duplicate finding identity')
        seen.add(identity)
        lesson, old = item['lesson'], item['replaces']
        if not isinstance(lesson, str) or not lesson.strip() or source.get('html_url', '') not in lesson or not source.get('html_url'):
            raise ValueError('Guidance requires source-linked text')
        if re.search(r'(?i)(?:-----BEGIN|api[_-]?key\s*[:=]|password\s*[:=]|token\s*[:=]|/(?:home|Users)/|(?:\d{1,3}\.){3}\d{1,3}|https?://[^\s/]+\.(?:internal|local)\b)', lesson):
            raise ValueError('Potential private data in guidance')
        if item['source_path'] == item['test_path']:
            raise ValueError('Source and regression evidence must be distinct')
        for kind in ('source', 'test'):
            path, excerpt = item[kind+'_path'], item[kind+'_excerpt']
            parts = PurePosixPath(path)
            if (parts.is_absolute() or '..' in parts.parts or path.startswith('-')
                    or re.search(r'(?i)(?:secret|credential|\.env|(?:^|/)auth(?:/|\.)|private[_-]?key|\.(?:pem|key)$)', path)
                    or not excerpt or len(excerpt) > 4000):
                raise ValueError('Invalid verification reference')
            if excerpt not in git(checkout, 'show', sha+':'+path):
                raise ValueError('Unsupported source/test claim')
        if source.get('accepted_sha') and source['accepted_sha'] != sha:
            raise ValueError('Accepted finding candidate changed')
        if old:
            if expected.count(old) != 1:
                raise ValueError('Superseded guidance must match once')
            expected = expected.replace(old, lesson, 1)
        else:
            if lesson in expected:
                raise ValueError('Guidance already promoted')
            expected = expected.rstrip()+'\n\n'+lesson
    if expected.strip() != after.strip():
        raise ValueError('Context changes exceed verified promotions')
    return {'promoted': len(seen), 'skipped': len(sources)-len(seen),
            'context_bytes': len(after.encode('utf-8')),
            'context_growth': len(after.encode('utf-8'))-len(before.encode('utf-8'))}


def preapproval_feedback(store, job, feedback):
    """At most one optional lesson pass, piggybacking on an existing repair."""
    if job['external_id'].startswith('learning:'):
        return feedback
    with store.write(job['id']):
        if store.db.execute("SELECT 1 FROM evidence WHERE job=? AND kind='learning_preapproval'", (job['id'],)).fetchone():
            return feedback
        from .store import now
        store.db.execute("INSERT INTO evidence(job,sha,kind,data,created) VALUES (?,?, 'learning_preapproval',?,?)",
                         (job['id'], job['commit_sha'] or '', json.dumps({'passes': 1, 'additional_review_cycles': 0}), now()))
    metrics(store, job['id'], lesson_passes=1, additional_review_cycles=0)
    return feedback+ ('\nDuring this required repair only, optionally update existing docs/kodi-context.md '
        'with a concise repository-specific lesson if the finding proves a reusable failure and '
        'the fix and regression verify it. Replace superseded guidance; keep source references '
        'and the 12000-byte hard bound. Never edit AGENTS.md or policy for learning. '
        'Do not create a separate commit/review loop for optional lessons. Unsupported or late '
        'feedback is deferred to separately authorized learning; all normal gates remain required.')


def filtered_outcome(store, config, repo, name, pr, reason):
    with store.write(0):
        return _filtered_outcome(store, config, repo, name, pr, reason)


def _filtered_outcome(store, config, repo, name, pr, reason):
    """Successful collection no-op in common job tracking, never a runnable task."""
    import hashlib
    if not policy(config)[0] or not pr.get('mergedAt'):
        return
    record = {'pr': {k: pr.get(k) for k in ('number', 'url', 'mergedAt', 'headRefOid')}, 'comments': []}
    requirements = 'Untrusted feedback data:\n'+json.dumps(record, sort_keys=True)
    identity = 'learning:'+repo+':'+name.lower()+':filtered:'+str(pr['number'])+':'+hashlib.sha256(reason.encode()).hexdigest()[:16]
    probe = {'external_id': identity, 'repo': repo, 'state': 'queued', 'requirements': requirements}
    if waiting_reason(config, probe):
        return
    job = store.reconcile_learning(identity, repo, 'Learning collection no-op #'+str(pr['number'])+' '+reason,
                                   requirements, source_url=pr['url'])
    if job['state'] == 'queued':
        store.evidence(job['id'], '', 'learning_no_change', {'outcome': 'no_change', 'reason': reason})
        store.update(job['id'], state='done')
    observe_collection(store, job['id'])
    metrics(store, job['id'], promoted=0, skipped=1, additional_review_cycles=0, delivery_latency_seconds=0)


def reconcile_collection(store, identity, repo, title, requirements, source_url):
    with store.write(0):
        return _reconcile_collection(store, identity, repo, title, requirements, source_url)


def _reconcile_collection(store, identity, repo, title, requirements, source_url):
    """Late new findings get a separate low-priority job; terminal work stays terminal."""
    import hashlib
    existing = store.db.execute('SELECT * FROM jobs WHERE external_id=?', (identity,)).fetchone()
    if existing and existing['state'] == 'done':
        prefix, raw = requirements.split('Untrusted feedback data:\n', 1)
        record = json.loads(raw)
        seen = set()
        for row in store.db.execute("SELECT requirements FROM jobs WHERE repo=? AND external_id LIKE 'learning:%'", (repo,)):
            try:
                old = json.loads(row['requirements'].split('Untrusted feedback data:\n', 1)[1])
                if old['pr']['url'] == source_url:
                    seen.update(str(c['id']) for c in old['comments'])
            except (ValueError, KeyError, IndexError, TypeError):
                continue
        new = [c for c in record['comments'] if str(c['id']) not in seen]
        if not new:
            return existing
        record['comments'] = new
        suffix = hashlib.sha256(json.dumps(sorted(str(c['id']) for c in new)).encode()).hexdigest()[:16]
        identity += ':findings:'+suffix
        title += ' findings '+suffix
        requirements = prefix+'Untrusted feedback data:\n'+json.dumps(record, indent=2)
    return store.reconcile_learning(identity, repo, title, requirements, source_url=source_url)


def elapsed(job):
    end = timestamp(job['updated']) if job['state'] in ('done', 'cancelled') else datetime.now(timezone.utc)
    return max(0, int((end-timestamp(job['created'])).total_seconds()))


def tracking_requirements(job):
    """Kandev gets identity and a digest; private feedback stays in local evidence."""
    import hashlib
    if not job['external_id'].startswith('learning:'):
        return job['requirements']
    return ('Repository learning proposal\nSource identity: '+job['external_id']+
            '\nPrivate evidence digest: '+hashlib.sha256(job['requirements'].encode()).hexdigest()+
            '\nScheduling requires owner authorization and authoritative Ready state. '
            'Normal exact-candidate QA, review, CI and human approval apply.')
