"""Adapter for the pinned Kandev HTTP API; the dashboard never starts agents."""
import json
from urllib.request import Request, urlopen


class Board:
    def __init__(self, url):
        self.url = url.rstrip('/')

    def request(self, path, data=None, method=None):
        request = Request(self.url+'/api/v1'+path,
                          data=json.dumps(data).encode() if data is not None else None,
                          headers={'Content-Type':'application/json'}, method=method)
        with urlopen(request, timeout=15) as response:
            return json.load(response)

    def bootstrap(self):
        workspaces = self.request('/workspaces')
        if isinstance(workspaces, dict): workspaces = workspaces.get('workspaces', [])
        workspace = next((w for w in workspaces if w['name']=='Kodi'), None)
        if not workspace:
            workspace = self.request('/workspaces', {'name':'Kodi',
                'description':'Kodi coordinates Docker workers. Move a task to Ready to queue it. Human approves every merge.'})
        workspace_id = workspace['id']
        workflows = self.request('/workspaces/'+workspace_id+'/workflows')
        if isinstance(workflows, dict): workflows = workflows.get('workflows', [])
        workflow = next((w for w in workflows if w['name']=='Kodi delivery'), None)
        if not workflow:
            workflow = self.request('/workflows', {'workspace_id':workspace_id,'name':'Kodi delivery',
                'description':'Backlog → Ready → Coding → QA → Review → Human review. Owner approval required before merging.'})
        workflow_id = workflow['id']
        steps = self.request('/workflows/'+workflow_id+'/workflow/steps')
        if isinstance(steps, dict): steps = steps.get('steps') or []
        steps = steps or []
        by_name = {s['name']:s['id'] for s in steps}
        for i, name in enumerate(['Backlog','Ready','Coding','QA','Review','Human review','Blocked','Done']):
            if name not in by_name:
                step = self.request('/workflow/steps', {'workflow_id':workflow_id, 'name':name,
                                                        'position':i, 'allow_manual_move':True})
                by_name[name] = step['id']
        return {'workspace':workspace_id, 'workflow':workflow_id, 'steps':by_name}

    def create(self, settings, title, description, external_id):
        return self.request('/tasks', {'workspace_id':settings['workspace'], 'workflow_id':settings['workflow'],
            'workflow_step_id':settings['steps']['Backlog'], 'title':title,'description':description,
            'external_id':external_id, 'start_agent':False, 'autopilot':False})

    def move(self, settings, task_id, name):
        return self.request('/tasks/'+task_id+'/move', {'workflow_id':settings['workflow'],
            'workflow_step_id':settings['steps'][name], 'position':0})

    def tasks(self, settings):
        result = self.request('/workflows/'+settings['workflow']+'/tasks')
        return (result.get('tasks') or []) if isinstance(result, dict) else (result or [])

    def description(self, task_id, text):
        return self.request('/tasks/'+task_id, {'description':text}, method='PATCH')
