import io
import json
import unittest
from email.message import Message
from unittest.mock import patch

from kodi.board import Board


class Response(io.BytesIO):
    def __init__(self, data, status=200, content_type='application/json'):
        super().__init__(json.dumps(data).encode())
        self.status = status
        self.headers = Message()
        self.headers['Content-Type'] = content_type


class ActivityProtocolTests(unittest.TestCase):
    def test_probe_and_encoded_write_use_bounded_protocol(self):
        board = Board('https://example.invalid')
        with patch('kodi.board.urlopen', return_value=Response({'totals':{},'daily':[]})) as request:
            self.assertTrue(board.activity_supported('space/a', 1))
            self.assertIn('/workspaces/space%2Fa/stats/usage?', request.call_args.args[0].full_url)
            self.assertEqual(request.call_args.kwargs['timeout'], 1)
        with patch('kodi.board.urlopen', return_value=Response({'id':'receipt','created':False})) as request:
            self.assertEqual(board.external_activity('task/a', {'event_id':'usage:1'}, 2)['id'], 'receipt')
            self.assertIn('/tasks/task%2Fa/external-activity', request.call_args.args[0].full_url)
            self.assertEqual(request.call_args.args[0].get_method(), 'POST')

    def test_non_json_oversized_or_invalid_receipts_are_never_acknowledged(self):
        fixtures = [Response({},204), Response({'id':'x','created':True},200,'text/html'),
                    Response({'id':'x'*17000,'created':True}), Response({'id':'x','created':1}),
                    Response({'id':'','created':True})]
        for response in fixtures:
            with self.subTest(response=response), patch('kodi.board.urlopen',return_value=response):
                with self.assertRaises(ValueError):
                    Board('https://example.invalid').external_activity('task',{},1)
