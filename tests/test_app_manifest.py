import importlib.util
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch


class AppManifestTests(unittest.TestCase):
    def test_disabled_webhooks_still_have_the_required_nested_url(self):
        path=Path(__file__).resolve().parents[1]/'scripts/github_setup.py'
        spec=importlib.util.spec_from_file_location('github_setup',path)
        module=importlib.util.module_from_spec(spec)
        with patch.dict(os.environ,{'PUBLIC_URL':'https://dev.example.com'}):
            spec.loader.exec_module(module)
        manifest=module.build_manifest('https://dev.example.com','ExampleOrg')
        self.assertEqual(manifest['url'],'https://github.com/ExampleOrg/dev-system')
        self.assertEqual(manifest['hook_attributes']['url'],'https://dev.example.com/setup/github/events')
        self.assertIs(manifest['hook_attributes']['active'],False)
        self.assertEqual(manifest['redirect_url'],'https://dev.example.com/setup/github/callback')
        self.assertNotIn('administration',manifest['default_permissions'])
        expected={'contents':'write','issues':'write','pull_requests':'write','statuses':'write','checks':'read'}
        self.assertEqual(manifest['default_permissions'],expected)
        example=json.loads((path.parents[1]/'examples/github-app-manifest.json').read_text())
        self.assertEqual(example['default_permissions'],expected)
