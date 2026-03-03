import base64
import unittest
from email import message_from_bytes
from unittest.mock import MagicMock

from main import _parse_reply_args
from gmail_cli.errors import UsageError
from gmail_cli.gmail_api import reply_to_message, reply_to_thread


def _decode_raw(raw: str):
    padding = "=" * (-len(raw) % 4)
    return message_from_bytes(base64.urlsafe_b64decode(raw + padding))


class ReplyParsingTests(unittest.TestCase):
    def test_parse_reply_args_clustered_flags(self) -> None:
        use_thread, reply_all, target_id, body = _parse_reply_args(["-ta", "thread123", "hello"])
        self.assertTrue(use_thread)
        self.assertTrue(reply_all)
        self.assertEqual(target_id, "thread123")
        self.assertEqual(body, "hello")

    def test_parse_reply_args_message_default(self) -> None:
        use_thread, reply_all, target_id, body = _parse_reply_args(["msg123", "hello"])
        self.assertFalse(use_thread)
        self.assertFalse(reply_all)
        self.assertEqual(target_id, "msg123")
        self.assertEqual(body, "hello")

    def test_parse_reply_args_invalid_flag(self) -> None:
        with self.assertRaises(UsageError):
            _parse_reply_args(["-x", "id", "body"])


class ReplyApiTests(unittest.TestCase):
    def test_reply_to_message_reply_all_preserves_cc(self) -> None:
        service = MagicMock()
        messages_api = service.users.return_value.messages.return_value
        messages_api.get.return_value.execute.return_value = {
            "threadId": "thr-1",
            "payload": {
                "headers": [
                    {"name": "From", "value": "Sender <sender@example.com>"},
                    {"name": "Subject", "value": "Status"},
                    {"name": "Message-ID", "value": "<msgid-1@example.com>"},
                    {"name": "References", "value": "<older@example.com>"},
                    {
                        "name": "Cc",
                        "value": "Teammate <cc1@example.com>, me@example.com, cc2@example.com",
                    },
                ]
            },
        }
        messages_api.send.return_value.execute.return_value = {"id": "new-id", "threadId": "thr-1"}

        result = reply_to_message(service, "me@example.com", "msg-1", "Thanks", reply_all=True)

        payload = messages_api.send.call_args.kwargs["body"]
        mime = _decode_raw(payload["raw"])
        self.assertEqual(result["threadId"], "thr-1")
        self.assertEqual(payload["threadId"], "thr-1")
        self.assertEqual(mime["To"], "sender@example.com")
        self.assertEqual(mime["Cc"], "cc1@example.com, cc2@example.com")
        self.assertEqual(mime["In-Reply-To"], "<msgid-1@example.com>")
        self.assertEqual(mime["References"], "<older@example.com> <msgid-1@example.com>")

    def test_reply_to_thread_uses_latest_non_self_message(self) -> None:
        service = MagicMock()
        threads_api = service.users.return_value.threads.return_value
        messages_api = service.users.return_value.messages.return_value

        threads_api.get.return_value.execute.return_value = {
            "messages": [
                {
                    "threadId": "thr-2",
                    "payload": {
                        "headers": [
                            {"name": "From", "value": "Peer <peer@example.com>"},
                            {"name": "Subject", "value": "Topic"},
                            {"name": "Message-ID", "value": "<peer-msg@example.com>"},
                            {"name": "Cc", "value": "cc@example.com"},
                        ]
                    },
                },
                {
                    "threadId": "thr-2",
                    "payload": {
                        "headers": [
                            {"name": "From", "value": "Me <me@example.com>"},
                            {"name": "Subject", "value": "Re: Topic"},
                            {"name": "Message-ID", "value": "<my-msg@example.com>"},
                        ]
                    },
                },
            ]
        }
        messages_api.send.return_value.execute.return_value = {"id": "new-id", "threadId": "thr-2"}

        reply_to_thread(service, "me@example.com", "thr-2", "Follow-up", reply_all=True)

        payload = messages_api.send.call_args.kwargs["body"]
        mime = _decode_raw(payload["raw"])
        self.assertEqual(payload["threadId"], "thr-2")
        self.assertEqual(mime["To"], "peer@example.com")
        self.assertEqual(mime["Cc"], "cc@example.com")
        self.assertEqual(mime["In-Reply-To"], "<peer-msg@example.com>")


if __name__ == "__main__":
    unittest.main()
