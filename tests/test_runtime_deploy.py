import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('deploy_runtime',
    Path(__file__).resolve().parents[1]/'scripts/deploy_runtime.py')
updater = importlib.util.module_from_spec(spec)
spec.loader.exec_module(updater)


class RuntimeDeployTests(unittest.TestCase):
    def test_busy_lock_never_stops_service(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root/'config.json'
            config.write_text(json.dumps({'state_dir': directory}))
            with patch.object(updater.fcntl, 'flock', side_effect=BlockingIOError), \
                    patch.object(updater, 'command') as command:
                self.assertIn('busy', updater.deploy(root, config, 'ExampleOrg/kodi'))
                command.assert_not_called()

    def test_interrupted_job_never_updates_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root/'config.json'
            config.write_text(json.dumps({'state_dir': directory}))
            with sqlite3.connect(root/'kodi.sqlite') as db:
                db.execute('CREATE TABLE jobs (state TEXT)')
                db.execute("INSERT INTO jobs VALUES ('qa')")
            with patch.object(updater, 'command') as command:
                self.assertIn('inspection', updater.deploy(root, config, 'ExampleOrg/kodi'))
                command.assert_not_called()
