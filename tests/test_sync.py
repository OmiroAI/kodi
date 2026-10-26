import tempfile
import unittest
from unittest.mock import Mock, patch
from kodi.coordinator import Coordinator
from kodi.board import Board


class SyncTests(unittest.TestCase):
    def test_organization_tasks_cannot_silently_fall_back_to_project(self):
        self.c.config['require_explicit_repo']=True
        self.task.pop('repositories')
        with patch('kodi.github.authenticated',return_value=True), \
                patch('kodi.github.issues',return_value=[]), \
                patch('kodi.github.create_issue') as create:
            self.c.sync()
        create.assert_not_called()
        self.c.board.move.assert_called_once_with(self.c.config['board'],'board-1','Blocked')
        self.assertIn('Repo:',self.c.board.description.call_args.args[1])

    def test_disabled_repository_is_never_dispatched(self):
        self.c.config['repos']['project']['enabled']=False
        with patch('kodi.github.authenticated',return_value=True), \
                patch('kodi.github.issues') as issues,patch('kodi.github.create_issue') as create:
            self.c.sync()
        issues.assert_not_called();create.assert_not_called()
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.c=Coordinator({'state_dir':self.directory.name,'default_repo':'project',
            'repos':{'project':{'github_repo':'example/project'}},
            'board':{'steps':{'Ready':'ready'}}})
        self.addCleanup(self.c.store.db.close)
        self.c.board=Mock()
        self.task={'id':'board-1','title':'Sound','description':'Improve sound',
                   'workflow_step_id':'ready','repositories':[{'id':'association-1','task_id':'board-1',
                                    'repository_id':'repo-1','base_branch':'main','position':0}]}
        self.c.board.repositories.return_value=[{'id':'repo-1','name':'project'}]
        self.c.board.tasks.return_value=[self.task]

    def test_workspace_repository_catalog_adapter(self):
        board=Board('https://board.example.com')
        for response in ([{'id':'repo-1'}], {'repositories':[{'id':'repo-1'}]}):
            with self.subTest(response=response), patch.object(
                    board,'request',return_value=response) as request:
                self.assertEqual(board.repositories({'workspace':'workspace-1'}),
                                 [{'id':'repo-1'}])
                request.assert_called_once_with('/workspaces/workspace-1/repositories')

    def test_native_selection_without_repo_line_dispatches(self):
        self.c.config['require_explicit_repo']=True
        with patch('kodi.github.authenticated',return_value=True), \
             patch('kodi.github.issues',return_value=[]), \
             patch('kodi.github.create_issue',return_value='issue-1') as create:
            self.c.sync()
        create.assert_called_once_with('example/project','Sound','Improve sound')
        job=self.c.store.db.execute('select * from jobs').fetchone()
        self.assertEqual(job['repo'],'project')
        self.c.board.move.assert_not_called()

    def test_catalog_github_clone_urls_resolve(self):
        for url in ('https://github.com/example/project.git',
                    'git@github.com:example/project.git'):
            with self.subTest(url=url):
                self.c.board.repositories.return_value=[
                    {'id':'repo-1','name':'other','remote_url':url}]
                self.assertEqual(self.c.task_repository(self.task,'',{}),'project')

    def test_missing_unknown_and_ambiguous_routes_block(self):
        for selected, catalog in (
                ([], []), (['unknown'], []), (['repo-1','repo-2'], []),
                (['repo-1'], [{'id':'repo-1','name':'unknown'}]),
                (['repo-1'], [{'id':'repo-1','name':'project'},
                             {'id':'repo-1','name':'project'}]),
                (['repo-1'], [{'id':'repo-1','name':'project',
                              'github_url':'https://github.com/example/other'}])):
            with self.subTest(selected=selected,catalog=catalog):
                self.c.config['repos']['other']={'github_repo':'example/other'}
                self.task['repositories']=[{'repository_id':key} for key in selected]
                self.c.board.repositories.return_value=catalog
                self.c.board.move.reset_mock()
                with patch('kodi.github.authenticated',return_value=False), \
                     patch('kodi.github.create_issue') as create:
                    self.c.sync()
                create.assert_not_called()
                self.c.board.move.assert_called_once_with(
                    self.c.config['board'],'board-1','Blocked')
        self.assertEqual(self.c.store.db.execute('select count(*) from jobs').fetchone()[0],0)

    def test_malformed_api_repository_selections_do_not_dispatch(self):
        for selected in ({'repository_id':'repo-1'}, ['repo-1'], [None],
                         [{}], [{'id':'repo-1'}], [{'repository_id':None}],
                         [{'repository_id':[]}], [{'repository_id':''}]):
            with self.subTest(selected=selected):
                self.task['repositories']=selected
                self.c.board.move.reset_mock()
                with patch('kodi.github.authenticated',return_value=False), \
                     patch('kodi.github.create_issue') as create:
                    self.c.sync()
                create.assert_not_called()
                self.c.board.move.assert_called_once_with(
                    self.c.config['board'],'board-1','Blocked')
        self.assertEqual(self.c.store.db.execute('select count(*) from jobs').fetchone()[0],0)

    def test_explicit_integration_route_precedes_selection(self):
        self.assertEqual(self.c.task_repository({},'Repo: project',{}),'project')
        self.assertIsNone(self.c.task_repository({},'Repo: project\nRepo: other',{}))
        self.c.board.repositories.assert_not_called()

    def test_repeated_ready_task_dispatches_once_without_external_id(self):
        with patch('kodi.github.authenticated',return_value=True), \
             patch('kodi.github.issues',return_value=[]), \
             patch('kodi.github.create_issue',return_value='https://github.com/example/project/issues/1') as create:
            self.c.sync()
            job=self.c.store.db.execute('select * from jobs').fetchone()
            self.c.store.update(job['id'],state='blocked',attempt=1,error='Provider unavailable')
            self.c.sync()
            self.c.sync()
        create.assert_called_once()
        self.assertEqual(self.c.store.db.execute('select count(*) from jobs').fetchone()[0],1)
        self.assertEqual(self.c.store.get(job['id'])['state'],'blocked')
        self.assertEqual(self.c.store.get(job['id'])['attempt'],1)

    def test_tracked_issue_does_not_create_another_board_mirror(self):
        url='https://github.com/example/project/issues/1'
        self.c.store.enqueue(url,'project','Sound','Improve sound','board-1')
        self.c.board.tasks.return_value=[]
        with patch('kodi.github.authenticated',return_value=True), \
             patch('kodi.github.issues',return_value=[{'url':url,'title':'Sound','body':'Improve sound'}]):
            self.c.sync()
        self.c.board.create.assert_not_called()
        self.c.board.move.assert_not_called()

    def test_distinct_tasks_with_identical_content_are_not_deduplicated(self):
        self.c.board.tasks.return_value=[self.task,{**self.task,'id':'board-2'}]
        with patch('kodi.github.authenticated',return_value=True), \
             patch('kodi.github.issues',return_value=[]), \
             patch('kodi.github.create_issue',side_effect=['issue-1','issue-2']) as create:
            self.c.sync()
        self.assertEqual(create.call_count,2)
        rows=self.c.store.db.execute('select board_id from jobs order by id').fetchall()
        self.assertEqual([r['board_id'] for r in rows],['board-1','board-2'])
