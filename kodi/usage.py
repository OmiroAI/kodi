"""Read-only reports over Kodi usage and acceptance evidence."""
import json
from datetime import datetime, timezone


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



def usage_report(store, grouping):
    if grouping not in ('daily','tasks'):
        raise ValueError('Unsupported usage grouping')
    groups={}
    jobs={row['id']:dict(row) for row in store.db.execute('SELECT * FROM jobs ORDER BY id')}

    def key_for(job, created):
        if grouping=='tasks': return str(job)
        return datetime.fromisoformat(created).astimezone(timezone.utc).date().isoformat()

    def bucket(job, created):
        key=key_for(job,created)
        if key not in groups:
            groups[key]={'providers':{}, 'review_iterations':0, 'qa_runs':0}
            if grouping=='tasks':
                metadata=jobs.get(job,{})
                groups[key].update({field:metadata.get(field) for field in
                                   ('title','repo','state','board_id','pr_url')})
                groups[key]['coding_attempts']=metadata.get('attempt',0)
                groups[key]['delivery_timing']=store.delivery_timing(job)
        return groups[key]

    if grouping=='tasks':
        for job in jobs.values(): bucket(job['id'],job['created'])
    runs={}
    for row in store.db.execute('SELECT * FROM usage ORDER BY id'):
        group=bucket(row['job'],row['created'])
        data=json.loads(row['data'])
        provenance=data.get('author_provenance') if isinstance(data,dict) else None
        if provenance:
            group.setdefault('author_runs',[]).append(dict(provenance, phase=row['phase'], outcome=row['outcome']))
        runs.setdefault(key_for(row['job'],row['created']),[]).append(row)
    for key, rows in runs.items(): groups[key]['providers']=usage_summary(rows)
    for row in store.db.execute("SELECT job,kind,created FROM evidence WHERE kind IN ('review','qa')"):
        group=bucket(row['job'],row['created'])
        group['review_iterations' if row['kind']=='review' else 'qa_runs']+=1
    return dict(sorted(groups.items()))
