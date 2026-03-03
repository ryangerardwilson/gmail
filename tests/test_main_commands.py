import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path

from gmail_cli.errors import UsageError
from main import _handle_delete, _handle_list, _handle_mark_read, main
from gmail_cli.config import AccountConfig


class MainCommandTests(unittest.TestCase):
    def test_handle_list_unread_default_limit(self) -> None:
        service = MagicMock()
        with patch("main.list_messages", return_value=[] ) as list_messages_mock, patch(
            "main.render_messages_table", return_value="table"
        ):
            code = _handle_list(service, ["-ur"], default_limit=10, my_email="me@example.com")
        self.assertEqual(code, 0)
        list_messages_mock.assert_called_once_with(service, "is:unread", 10)

    def test_handle_list_unread_custom_limit(self) -> None:
        service = MagicMock()
        with patch("main.list_messages", return_value=[] ) as list_messages_mock, patch(
            "main.render_messages_table", return_value="table"
        ):
            _handle_list(service, ["-ur", "1"], default_limit=10, my_email="me@example.com")
        list_messages_mock.assert_called_once_with(service, "is:unread", 1)

    def test_handle_list_unread_bad_limit(self) -> None:
        service = MagicMock()
        with self.assertRaises(UsageError):
            _handle_list(service, ["-ur", "0"], default_limit=10, my_email="me@example.com")

    def test_handle_mark_read(self) -> None:
        service = MagicMock()
        with patch("main.mark_message_read", return_value={"id": "m1", "threadId": "t1"}) as mark_mock:
            code = _handle_mark_read(service, ["m1"])
        self.assertEqual(code, 0)
        mark_mock.assert_called_once_with(service, "m1")

    def test_handle_delete(self) -> None:
        service = MagicMock()
        with patch("main.delete_message") as delete_mock:
            code = _handle_delete(service, ["m1"])
        self.assertEqual(code, 0)
        delete_mock.assert_called_once_with(service, "m1")

    def test_upgrade_rejects_extra_args(self) -> None:
        with self.assertRaises(UsageError):
            main(["-u", "3"])

    def test_handle_list_unread_audit_bad_limit(self) -> None:
        service = MagicMock()
        with self.assertRaises(UsageError):
            _handle_list(service, ["-ura", "0"], default_limit=10, my_email="me@example.com", config_path="/tmp/x", account=MagicMock())

    def test_handle_list_unread_audit_spam_path(self) -> None:
        service = MagicMock()
        account = AccountConfig(
            preset="1",
            email="me@example.com",
            client_secret_file=MagicMock(),
            signature_file=MagicMock(),
            spam_senders=[],
            not_spam_senders=[],
        )
        messages = [
            {
                "id": "m1",
                "threadId": "t1",
                "snippet": "buy now",
                "payload": {
                    "headers": [
                        {"name": "From", "value": "Spammer <spam@x.com>"},
                        {"name": "Subject", "value": "Sale"},
                        {"name": "Date", "value": "Mon, 1 Jan 2024 10:00:00 +0000"},
                    ]
                },
            }
        ]
        with patch("main.list_messages", return_value=messages), patch(
            "main.input", side_effect=["s"]
        ), patch("main.delete_message") as delete_mock, patch(
            "main.update_account_sender_lists"
        ) as update_mock:
            code = _handle_list(
                service,
                ["-ura", "1"],
                default_limit=10,
                my_email="me@example.com",
                config_path="/tmp/config.json",
                account=account,
            )
        self.assertEqual(code, 0)
        delete_mock.assert_called_once_with(service, "m1")
        update_payload = update_mock.call_args.args[1]
        self.assertIn("spam@x.com", update_payload["1"]["spam_senders"])

    def test_handle_list_unread_audit_no_limit_uses_batches(self) -> None:
        service = MagicMock()
        account = AccountConfig(
            preset="1",
            email="me@example.com",
            client_secret_file=MagicMock(),
            signature_file=MagicMock(),
            spam_senders=[],
            not_spam_senders=[],
        )
        with patch("main.list_messages_page", return_value=([], None) ) as list_page_mock, patch(
            "main.update_account_sender_lists"
        ):
            code = _handle_list(
                service,
                ["-ura"],
                default_limit=10,
                my_email="me@example.com",
                config_path=Path("/tmp/config.json"),
                account=account,
            )
        self.assertEqual(code, 0)
        list_page_mock.assert_called_once_with(service, "is:unread", max_results=10, page_token=None)

    def test_handle_list_unread_audit_trash_only(self) -> None:
        service = MagicMock()
        account = AccountConfig(
            preset="1",
            email="me@example.com",
            client_secret_file=MagicMock(),
            signature_file=MagicMock(),
            spam_senders=[],
            not_spam_senders=[],
        )
        messages = [
            {
                "id": "m1",
                "threadId": "t1",
                "snippet": "offer",
                "payload": {
                    "headers": [
                        {"name": "From", "value": "Sender <sender@x.com>"},
                        {"name": "Subject", "value": "Offer"},
                        {"name": "Date", "value": "Mon, 1 Jan 2024 10:00:00 +0000"},
                    ]
                },
            }
        ]
        with patch("main.list_messages", return_value=messages), patch(
            "main.input", side_effect=["t"]
        ), patch("main.delete_message") as delete_mock, patch(
            "main.update_account_sender_lists"
        ) as update_mock:
            code = _handle_list(
                service,
                ["-ura", "1"],
                default_limit=10,
                my_email="me@example.com",
                config_path="/tmp/config.json",
                account=account,
            )
        self.assertEqual(code, 0)
        delete_mock.assert_called_once_with(service, "m1")
        update_payload = update_mock.call_args.args[1]
        self.assertEqual(update_payload["1"]["spam_senders"], [])

    def test_handle_list_unread_audit_gmail_sender_protected(self) -> None:
        service = MagicMock()
        account = AccountConfig(
            preset="1",
            email="me@example.com",
            client_secret_file=MagicMock(),
            signature_file=MagicMock(),
            spam_senders=[],
            not_spam_senders=[],
        )
        messages = [
            {
                "id": "m1",
                "threadId": "t1",
                "snippet": "hello",
                "payload": {
                    "headers": [
                        {"name": "From", "value": "Person <person@gmail.com>"},
                        {"name": "Subject", "value": "Hi"},
                        {"name": "Date", "value": "Mon, 1 Jan 2024 10:00:00 +0000"},
                    ]
                },
            }
        ]
        with patch("main.list_messages", return_value=messages), patch(
            "main.input", side_effect=["t"]
        ), patch("main.delete_message") as delete_mock, patch(
            "main.update_account_sender_lists"
        ) as update_mock:
            code = _handle_list(
                service,
                ["-ura", "1"],
                default_limit=10,
                my_email="me@example.com",
                config_path="/tmp/config.json",
                account=account,
            )
        self.assertEqual(code, 0)
        delete_mock.assert_not_called()
        update_payload = update_mock.call_args.args[1]
        self.assertEqual(update_payload["1"]["spam_senders"], [])

    def test_handle_list_read_audit_custom_limit(self) -> None:
        service = MagicMock()
        account = AccountConfig(
            preset="1",
            email="me@example.com",
            client_secret_file=MagicMock(),
            signature_file=MagicMock(),
            spam_senders=[],
            not_spam_senders=[],
        )
        with patch("main.list_messages", return_value=[] ) as list_messages_mock:
            code = _handle_list(
                service,
                ["-ra", "5"],
                default_limit=10,
                my_email="me@example.com",
                config_path="/tmp/config.json",
                account=account,
            )
        self.assertEqual(code, 0)
        list_messages_mock.assert_called_once_with(service, "is:read", 5)


if __name__ == "__main__":
    unittest.main()
