import tempfile
import unittest
from pathlib import Path

from kodi.egress import allowed
from kodi.env import read_env
from kodi.policy import changed_paths_safe, validate_review
from kodi.runner import docker_args
from kodi.store import Store


class AcceptanceGateTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory()
        self.store=Store(Path(self.temporary.name)/'jobs.sqlite')
        self.job=self.store.enqueue('issue:1','system','Fix reconnect','Preserve messages')['id']
        self.sha='a'*40

    def tearDown(self):
        self.store.db.close(); self.temporary.cleanup()

    def evidence(self, sha=None):
        sha=sha or self.sha
        self.store.evidence(self.job,sha,'author',{'test_steps':['Reconnect and verify no duplicate']})
        self.store.evidence(self.job,sha,'qa',{'passed':True})
        self.store.evidence(self.job,sha,'review',{'approved':True,'criteria_met':True,'simple':True,
            'blocking_findings':[],'author_family':'openai','reviewer_family':'google'})

    def test_missing_evidence_blocks(self):
        self.assertFalse(self.store.gate(self.job,self.sha))

    def test_exact_commit_independent_evidence_passes(self):
        self.evidence(); self.assertTrue(self.store.gate(self.job,self.sha))

    def test_stale_evidence_cannot_approve_new_commit(self):
        self.evidence(); self.assertFalse(self.store.gate(self.job,'b'*40))

    def test_failed_rerun_overrides_previous_success(self):
        self.evidence(); self.store.evidence(self.job,self.sha,'qa',{'passed':False})
        self.assertFalse(self.store.gate(self.job,self.sha))

    def test_self_review_cannot_pass(self):
        self.evidence(); self.store.evidence(self.job,self.sha,'review',{'approved':True,
            'criteria_met':True,'simple':True,'blocking_findings':[],
            'author_family':'openai','reviewer_family':'openai'})
        self.assertFalse(self.store.gate(self.job,self.sha))

    def test_only_one_worker_can_claim_and_enqueue_is_idempotent(self):
        self.store.enqueue('issue:2','system','Another task','Requirements')
        self.assertEqual(self.store.enqueue('issue:1','system','Retry','Retry')['id'],self.job)
        other=Store(Path(self.temporary.name)/'jobs.sqlite')
        try:
            self.assertEqual(self.store.claim()['id'],self.job)
            self.assertIsNone(other.claim())
        finally: other.db.close()

    def test_notifications_deduplicate_only_after_success(self):
        calls=[]
        def fail(): raise RuntimeError('delivery failure')
        with self.assertRaises(RuntimeError): self.store.notify_once('event',fail)
        self.assertTrue(self.store.notify_once('event',lambda:calls.append(1)))
        self.assertFalse(self.store.notify_once('event',lambda:calls.append(2)))
        self.assertEqual(calls,[1])


class PolicyTests(unittest.TestCase):
    def test_secrets_and_privilege_changes_require_operator_review(self):
        for path in ['.env','config/private.key','.github/workflows/ci.yml','src/auth/session.py',
                     '../outside.py','/etc/passwd','AGENTS.md']:
            with self.subTest(path=path), self.assertRaises(ValueError): changed_paths_safe([path])
        changed_paths_safe(['src/reconnect.py','tests/test_reconnect.py','.env.example'])

    def test_string_true_does_not_count_as_review_approval(self):
        with self.assertRaises(ValueError):
            validate_review({'approved':'true','criteria_met':True,'simple':True,
                'blocking_findings':[],'summary':'fine'},'openai','google')

    def test_proxy_rejects_private_hosts_and_domain_suffix_tricks(self):
        for host in ['localhost','127.0.0.1','192.0.2.20','qa-node.example.test','chatgpt.com.evil.test','evilopenai.com']:
            self.assertFalse(allowed(host,443))
        self.assertTrue(allowed('auth.openai.com',443))
        for host in ['lh3.googleusercontent.com','www.googleapis.com','daily-cloudcode-pa.googleapis.com','aicode.googleapis.com','antigravity.google.com','antigravity-unleash.goog']:
            self.assertTrue(allowed(host,443))
            self.assertFalse(allowed(host+'.evil.test',443))
        self.assertFalse(allowed('auth.openai.com',80))

    def test_worker_has_no_host_network_socket_or_notification_credentials(self):
        args=docker_args({'worker_image':'worker','proxy_dir':'/tmp/proxy'},'job',
                        Path('/tmp/checkout'),Path('/tmp/auth'),Path('/tmp/output'))
        self.assertEqual(args[args.index('--network')+1],'none')
        self.assertIn('--read-only',args)
        self.assertNotIn('/var/run/docker.sock',' '.join(args))
        self.assertNotIn('TELEGRAM',' '.join(args))

    def test_environment_values_are_never_shell_evaluated(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'.env'; path.write_text('TOKEN="$(touch /tmp/should-not-exist)"\n')
            self.assertEqual(read_env(path)['TOKEN'],'$(touch /tmp/should-not-exist)')


if __name__=='__main__': unittest.main()
