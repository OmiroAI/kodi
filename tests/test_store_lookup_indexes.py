"""Historical evidence must not require full-ledger scans per dashboard task."""
import tempfile
import unittest
from pathlib import Path

from kodi.store import Store


class LookupIndexesTests(unittest.TestCase):
    def test_existing_ledger_is_indexed_without_changing_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'ledger.sqlite'
            store = Store(path)
            store.db.executescript('DROP INDEX evidence_job; DROP INDEX evidence_job_kind; DROP INDEX usage_job;')
            store.db.execute("INSERT INTO evidence(job,sha,kind,data,created) VALUES (1,'abc','review','{}','now')")
            store.db.commit()
            store.db.close()
            store = Store(path)
            queries = [
                "SELECT data FROM evidence WHERE job=1 AND kind IN ('coding_profile','recovery_profile') ORDER BY id",
                'SELECT id,kind,sha,data FROM evidence WHERE job=1 ORDER BY id DESC LIMIT 128',
                "SELECT 1 FROM evidence WHERE job=1 AND kind='merge_maintenance' "
                "AND id>COALESCE((SELECT MAX(id) FROM evidence WHERE job=1 AND kind='merge_released'),0)",
                'SELECT provider,model,data,seconds FROM usage WHERE job=1',
            ]
            for query in queries:
                with self.subTest(query=query):
                    plan = ' '.join(row[3] for row in store.db.execute('EXPLAIN QUERY PLAN '+query))
                    self.assertIn('SEARCH', plan)
                    self.assertNotIn('SCAN', plan)
            self.assertEqual(store.db.execute('SELECT COUNT(*) FROM evidence').fetchone()[0], 1)
            store.db.close()
            # Reopening is idempotent, and read-only inspection still works.
            Store(path).db.close()
            Store(path, read_only=True).db.close()
