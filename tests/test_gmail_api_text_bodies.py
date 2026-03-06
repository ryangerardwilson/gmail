import unittest
from unittest.mock import MagicMock
import base64

from gmail_cli.gmail_api import hydrate_message_text_bodies, hydrate_message_text_from_raw


class GmailApiTextBodiesTests(unittest.TestCase):
    def test_hydrates_text_html_data_from_attachment_id(self) -> None:
        service = MagicMock()
        attachments_api = service.users.return_value.messages.return_value.attachments.return_value
        attachments_api.get.return_value.execute.return_value = {"data": "aGVsbG8"}

        message = {
            "id": "m1",
            "payload": {
                "mimeType": "multipart/alternative",
                "parts": [
                    {
                        "mimeType": "text/html",
                        "body": {"attachmentId": "att-1"},
                    }
                ],
            },
        }

        hydrated = hydrate_message_text_bodies(service, message)
        html_part = hydrated["payload"]["parts"][0]
        self.assertEqual(html_part["body"]["data"], "aGVsbG8")
        attachments_api.get.assert_called_once_with(userId="me", messageId="m1", id="att-1")

    def test_does_not_fetch_for_non_text_parts(self) -> None:
        service = MagicMock()
        attachments_api = service.users.return_value.messages.return_value.attachments.return_value

        message = {
            "id": "m1",
            "payload": {
                "mimeType": "multipart/mixed",
                "parts": [
                    {
                        "mimeType": "application/pdf",
                        "body": {"attachmentId": "att-2"},
                    }
                ],
            },
        }

        hydrate_message_text_bodies(service, message)
        attachments_api.get.assert_not_called()

    def test_hydrates_from_raw_mime_parts(self) -> None:
        service = MagicMock()
        raw_mime = (
            "MIME-Version: 1.0\r\n"
            "Content-Type: multipart/alternative; boundary=abc\r\n"
            "\r\n"
            "--abc\r\n"
            "Content-Type: text/plain; charset=UTF-8\r\n"
            "\r\n"
            "plain text\r\n"
            "--abc\r\n"
            "Content-Type: text/html; charset=UTF-8\r\n"
            "\r\n"
            "<html><body><p>html text</p></body></html>\r\n"
            "--abc--\r\n"
        ).encode("utf-8")
        raw = base64.urlsafe_b64encode(raw_mime).decode("ascii").rstrip("=")
        service.users.return_value.messages.return_value.get.return_value.execute.return_value = {"raw": raw}

        message = {"id": "m1", "payload": {}}
        hydrated = hydrate_message_text_from_raw(service, message)
        self.assertEqual(hydrated["_raw_plain_body"].strip(), "plain text")
        self.assertIn("<p>html text</p>", hydrated["_raw_html_body"])


if __name__ == "__main__":
    unittest.main()
