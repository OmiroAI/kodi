import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from kodi import github, github_app


class PublisherIdentityTests(unittest.TestCase):
    def test_tokens_request_only_approved_checks_read_and_existing_repository_scope(self):
        for approved in (None,'read','write'):
            with self.subTest(approved=approved):
                config=MagicMock()
                config.read_text.return_value=json.dumps({'app_id':1,'installation_id':2,
                    'private_key':'unused','repositories':['dev-system','Project']})
                (config.parent/'repositories.json').exists.return_value=False
                key=MagicMock()
                key.stat.return_value.st_mode=0o600
                installation=MagicMock()
                installation.__enter__.return_value.read.return_value=json.dumps(
                    {'permissions':{'checks':approved,'administration':'write'}}).encode()
                response=MagicMock()
                response.__enter__.return_value.read.return_value=b'{"token":"test-token"}'
                with patch.object(github_app,'CONFIG',config), patch.object(github_app,'_cached',None), \
                        patch.object(github_app,'Path',return_value=key), \
                        patch.object(github_app.subprocess,'run',return_value=MagicMock(stdout=b'signature')), \
                        patch.object(github_app,'urlopen',side_effect=[installation,response]) as opening:
                    self.assertTrue(github.authenticated())
                    self.assertEqual(github_app.token(),'test-token')
                    self.assertEqual(opening.call_count,2)
                    discovery=opening.call_args_list[0].args[0]
                    request=opening.call_args_list[1].args[0]
                    self.assertEqual(discovery.get_method(),'GET')
                    self.assertEqual(discovery.full_url,'https://api.github.com/app/installations/2')
                    self.assertEqual(discovery.get_header('Authorization'),request.get_header('Authorization'))
                    payload=json.loads(request.data)
                    expected={'contents':'write','issues':'write','pull_requests':'write','statuses':'write'}
                    if approved: expected['checks']='read'
                    self.assertEqual(payload,{'repositories':['dev-system','Project'],'permissions':expected})

    def test_operator_allowlist_extends_scope_without_changing_permissions(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'github-app.json'
            (p.parent/'repositories.json').write_text(json.dumps(['project','plugin-project']))
            with patch.object(github_app,'CONFIG',p):
                self.assertEqual(github_app.repository_scope({'repositories':['project']}),['project','plugin-project'])
                (p.parent/'repositories.json').write_text('["../other"]')
                with self.assertRaises(RuntimeError):github_app.repository_scope({})

    def test_operator_cli_login_is_not_a_publisher_identity(self):
        with patch('kodi.github.token',side_effect=RuntimeError('App missing')):
            self.assertFalse(github.authenticated())

    def test_incomplete_installation_fails_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'config.json'
            p.write_text(json.dumps({'app_id':1,'installation_id':None}))
            with patch.object(github_app,'CONFIG',p), patch.object(github_app,'_cached',None):
                with self.assertRaises(RuntimeError): github_app.token()

    def test_private_key_cannot_be_group_or_world_readable(self):
        with tempfile.TemporaryDirectory() as folder:
            key=Path(folder)/'key.pem'; key.write_text('placeholder'); key.chmod(0o644)
            p=Path(folder)/'config.json'; p.write_text(json.dumps({'app_id':1,'installation_id':2,'private_key':str(key)}))
            with patch.object(github_app,'CONFIG',p), patch.object(github_app,'_cached',None):
                with self.assertRaises(RuntimeError): github_app.token()
