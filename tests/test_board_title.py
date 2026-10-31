import unittest
from unittest.mock import Mock
from kodi.board import Board


class BoardTitleTests(unittest.TestCase):
    def test_create_bounds_title_without_changing_authoritative_requirements(self):
        board = Board("http://example.invalid")
        board.request = Mock(return_value={"id": "task"})
        settings = {"workspace": "workspace", "workflow": "workflow", "steps": {"Backlog": "backlog"}}
        for title in ("Short title", "A" * 61, "界" * 70):
            with self.subTest(title=title):
                board.create(settings, title, "Exact authoritative requirements", "stable-identity")
                payload = board.request.call_args.args[1]
                self.assertEqual(payload["title"], title[:60])
                self.assertEqual(payload["description"], "Exact authoritative requirements")
                self.assertEqual(payload["external_id"], "stable-identity")
                self.assertFalse(payload["start_agent"])
