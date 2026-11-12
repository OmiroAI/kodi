"""Visual review uses the existing same-task clarification continuation."""
import unittest
from unittest.mock import Mock, patch

from kodi.clarification import Intake, validate_coding_question
from kodi.design_preview import _NoRedirect, available, check_links, review_text
from tests import test_coding_clarification as fixtures


class DesignPreviewTests(unittest.TestCase):
    setUp = fixtures.CodingClarificationTests.setUp
    sync = fixtures.CodingClarificationTests.sync
    ask = fixtures.CodingClarificationTests.ask
    answer = fixtures.CodingClarificationTests.answer
    question = dict(status='needs_input', category='visual_review',
                    question='Which navigation should we implement?',
                    context='Navigation needs a visual decision.', previews=['mobile'])

    def prepare(self):
        job = fixtures.CodingClarificationTests.prepare(self)
        self.config['design_previews'] = {'mobile': dict(
            task=job['board_id'], private=True, retained_during_review=True,
            url='https://preview.example.test/artifacts/navigation', kind='mockup',
            viewport='390 x 844', state='navigation expanded',
            hardware='Touch behavior unverified on physical hardware',
            interaction='Navigation interaction included', sound='Not relevant',
            recovery='Reply on this task requesting a refreshed preview; work remains paused')}
        self.probe = patch('kodi.design_preview.check_links')
        self.probe.start(); self.addCleanup(self.probe.stop)
        return job

    def test_preview_links_and_metadata_project_to_both_channels_and_resume(self):
        job = self.prepare()
        self.config.update(telegram_env='fixture', telegram={'enabled':True})
        api = Mock(chat_id=42); api.send.return_value=100
        with patch('kodi.clarification.TelegramAPI', return_value=api):
            row = self.ask(job)
        for text in (self.task['description'], api.send.call_args.args[0]):
            self.assertIn('https://preview.example.test/artifacts/navigation', text)
            self.assertIn('390 x 844', text)
            self.assertIn('mockup', text)
            self.assertIn('unverified', text)
            self.assertIn('refreshed preview', text)
            self.assertIn('delivery acceptance', text)
        self.assertIn('mobile', available(self.config, job['board_id']))
        self.assertEqual(available(self.config, 'another-task'), '')
        current = self.answer(job, row, 'telegram')
        self.assertEqual(current['state'], 'queued')
        self.assertIn('https://preview.example.test/artifacts/navigation', current['requirements'])
        self.assertIsNotNone(Intake(self.c).continuation(current))
        self.assertFalse(self.c.store.gate(job['id'], 'a'*40))

    def test_updated_preview_round_retains_old_link_and_feedback(self):
        job = self.prepare(); row = self.ask(job)
        Intake(self.c).answer(job['id'],row['revision'],'Make the menu larger','telegram:42:1')
        self.sync()
        self.assertIn('https://preview.example.test/artifacts/navigation',
                      Intake(self.c).latest(job['id'])['result'])
        current = self.answer(job, Intake(self.c).latest(job['id']))
        self.c.store.update(job['id'], state='coding')
        self.config['design_previews']['mobile']['url'] += '-v2'
        row = self.ask(self.c.store.get(job['id']))
        self.assertIn('Make the menu larger', row['input_text'])
        self.assertIn('navigation-v2', row['input_text'])
        self.assertEqual(self.c.store.get(job['id'])['board_id'], job['board_id'])

    def test_registry_rejects_cross_task_public_unretained_and_incomplete_artifacts(self):
        job=self.prepare(); item=self.config['design_previews']['mobile']
        for field, value in [('task','another-task'), ('private',False),
                             ('retained_during_review',False), ('kind','unknown'),
                             ('hardware',''), ('url','http://preview.example.test/x'),
                             ('url','https://preview.example.test/x?token=secret')]:
            with self.subTest(field=field):
                original=item[field]; item[field]=value
                with self.assertRaises(ValueError):review_text(self.config,job['board_id'],['mobile'])
                item[field]=original
        for ids in ([], ['unknown'], ['mobile','mobile'], [{}]):
            with self.assertRaises(ValueError):review_text(self.config,job['board_id'],ids)

    def test_unavailable_link_stops_before_projection_and_retains_work(self):
        job=self.prepare()
        with patch('kodi.design_preview.check_links', side_effect=ValueError('Preview unavailable')):
            with self.assertRaisesRegex(ValueError,'unavailable'):self.ask(job)
        self.assertEqual(self.c.store.get(job['id'])['state'],'coding')
        self.assertEqual(self.dirty.read_text(),'partial implementation\n')

    def test_probe_rejects_non_success_and_suppresses_transport_details(self):
        self.prepare()
        response=Mock(status=200)
        response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
        with patch('kodi.design_preview.build_opener') as opener:
            opener.return_value.open.return_value=response
            check_links(self.config,['mobile'])
            response.status=302
            with self.assertRaisesRegex(ValueError,'recovery instructions'):check_links(self.config,['mobile'])
            opener.return_value.open.side_effect=OSError('private transport detail')
            with self.assertRaisesRegex(ValueError,'^Registered preview unavailable;'):check_links(self.config,['mobile'])

    def test_multiple_sizes_states_and_application_provenance(self):
        job=self.prepare()
        self.config['design_previews']['desktop']=dict(
            self.config['design_previews']['mobile'], kind='application render',
            viewport='1440 x 900', state='navigation collapsed',
            url='https://preview.example.test/artifacts/desktop')
        text=review_text(self.config,job['board_id'],['mobile','desktop'])
        for label in ('mockup','application render','390 x 844','1440 x 900',
                      'navigation expanded','navigation collapsed'):
            self.assertIn(label,text)
        self.assertIsNone(_NoRedirect().redirect_request(None,None,302,'',{},
                                                        'https://other.example.test/'))
