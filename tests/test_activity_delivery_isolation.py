import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock

from kodi.activity import ActivitySync
from kodi.coordinator import Coordinator
from kodi.store import Store


class ActivityDeliveryIsolationTests(unittest.TestCase):
    def test_delivery_and_progress_continue_while_export_request_is_blocked(self):
        entered, release = threading.Event(), threading.Event()
        callers, errors = [], []

        class SlowReceiver:
            description = Mock()

            def activity_supported(self, workspace, timeout):
                return True

            def external_activity(self, task, payload, timeout):
                callers.append(threading.get_ident())
                entered.set()
                if not release.wait(5):
                    raise TimeoutError()
                return {'id': 'receipt', 'created': True}

        with tempfile.TemporaryDirectory() as directory:
            coordinator = Coordinator({'state_dir': directory, 'board': {'workspace': 'workspace'}})
            store = coordinator.store
            job = store.enqueue('example:1', 'project', 'Example', 'Do work', 'task')
            store.usage(job['id'], 'codex', 'default', {'input_tokens': 1, 'output_tokens': 1}, 1)
            receiver = SlowReceiver()
            coordinator.board = receiver

            def export():
                worker_store = Store(Path(directory) / 'kodi.sqlite')
                try:
                    ActivitySync(worker_store, receiver).sync('workspace')
                except Exception as error:
                    errors.append(error)
                finally:
                    worker_store.db.close()

            worker = threading.Thread(target=export)
            worker.start()
            try:
                self.assertTrue(entered.wait(2), 'export did not reach the receiver')
                for name in ('finish_prs', 'sync', 'finish_checks', 'request_reviews', 'maintain_merges'):
                    setattr(coordinator, name, Mock())
                coordinator.process = Mock()
                coordinator._tick()
                coordinator.progress(store.get(job['id']))
                coordinator.process.assert_called_once()
                receiver.description.assert_called_once()
                self.assertEqual(callers, [worker.ident], 'delivery performed accounting HTTP')
                self.assertTrue(worker.is_alive(), 'delivery waited for the accounting request')
            finally:
                release.set()
                worker.join(6)
                store.db.close()
            self.assertFalse(worker.is_alive())
            self.assertFalse(errors)
