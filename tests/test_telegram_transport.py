import json
import tempfile
import unittest
from pathlib import Path
from urllib.error import URLError
from unittest.mock import MagicMock,patch

from kodi.notify import TelegramAPI
from kodi.owner_review import approve,owner_gh
from kodi.whisper import transcribe
from kodi.github import approved_at

SHA='a'*40


class TransportTests(unittest.TestCase):
    def test_telegram_token_is_suppressed_in_transport_errors(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'telegram.env';p.write_text('TELEGRAM_BOT_TOKEN=test-private-token\nTELEGRAM_CHAT_ID=42\n')
            api=TelegramAPI(p)
            with patch('kodi.notify.urlopen',side_effect=URLError('https://host/test-private-token')):
                with self.assertRaises(RuntimeError) as failure:api.send('test')
            self.assertNotIn('test-private-token',str(failure.exception))

    def test_voice_file_path_and_size_are_bounded(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'telegram.env';p.write_text('TELEGRAM_BOT_TOKEN=test\nTELEGRAM_CHAT_ID=42\n');api=TelegramAPI(p)
            for result in ({'file_path':'../private'},{'file_path':'voice/file.ogg','file_size':200}):
                with patch.object(api,'call',return_value=result),patch('kodi.notify.urlopen') as opening:
                    with self.assertRaises((RuntimeError,ValueError)):api.download_voice('id',100)
                    opening.assert_not_called()

    def test_whisper_uses_only_configured_host_and_multipart_audio(self):
        response=MagicMock();response.__enter__.return_value.read.return_value=b'{"text":"project: fix volume"}'
        with patch('kodi.whisper.urlopen',return_value=response) as opening:
            text=transcribe({'whisper_url':'http://whisper-host:8008/v1/audio/transcriptions'},b'audio bytes')
        self.assertEqual(text,'project: fix volume')
        request=opening.call_args.args[0]
        self.assertEqual(request.full_url,'http://whisper-host:8008/v1/audio/transcriptions')
        self.assertIn(b'audio bytes',request.data);self.assertIn(b'name="file"',request.data)

    def test_owner_review_uses_host_login_without_app_token(self):
        with patch.dict('os.environ',{'GH_TOKEN':'app-token','GITHUB_TOKEN':'also-app'}), \
                patch('kodi.owner_review.subprocess.run',return_value=MagicMock(returncode=0,stdout='{}')) as run:
            owner_gh('api','user')
        self.assertNotIn('GH_TOKEN',run.call_args.kwargs['env'])
        self.assertNotIn('GITHUB_TOKEN',run.call_args.kwargs['env'])

    def test_mismatched_host_identity_never_submits_review(self):
        with patch('kodi.owner_review.owner_gh',return_value='{"login":"wrong-user"}') as gh:
            with self.assertRaises(RuntimeError):approve('owner/repo',1,SHA,'maintainer','action')
        self.assertEqual(gh.call_count,1)

    def test_owner_review_sends_explicit_commit_id(self):
        with patch('kodi.owner_review.owner_gh',side_effect=['{"login":"maintainer"}','{}']) as gh:
            approve('owner/repo',1,SHA,'maintainer','action')
        payload=json.loads(gh.call_args.kwargs['stdin'])
        self.assertEqual(payload['commit_id'],SHA);self.assertEqual(payload['event'],'APPROVE')

    def test_old_commit_or_dismissed_review_is_not_current_human_approval(self):
        url='https://github.com/owner/repo/pull/1'
        for reviews in ([{'state':'APPROVED','commit_id':'b'*40,'user':{'login':'maintainer'}}],
                        [{'state':'APPROVED','commit_id':SHA,'user':{'login':'maintainer'}},
                         {'state':'DISMISSED','commit_id':SHA,'user':{'login':'maintainer'}}]):
            with patch('kodi.github.api_pages',return_value=[reviews]):self.assertFalse(approved_at(url,SHA,['maintainer']))
