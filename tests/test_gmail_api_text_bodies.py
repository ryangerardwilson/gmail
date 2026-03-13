import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock
import base64

from gmail_cli.gmail_api import (
    download_message_attachments,
    hydrate_message_text_bodies,
    hydrate_message_text_from_raw,
    message_has_non_calendar_attachment,
)


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

    def test_download_message_attachments_skips_ics_files(self) -> None:
        service = MagicMock()
        attachments_api = service.users.return_value.messages.return_value.attachments.return_value
        attachments_api.get.return_value.execute.return_value = {"data": "aGVsbG8"}
        message = {
            "id": "m1",
            "payload": {
                "mimeType": "multipart/mixed",
                "parts": [
                    {
                        "filename": "invite.ics",
                        "body": {"attachmentId": "att-ics"},
                    },
                    {
                        "filename": "notes.txt",
                        "body": {"attachmentId": "att-txt"},
                    },
                    {
                        "filename": "MEETING.ICS",
                        "body": {"data": "aGVsbG8"},
                    },
                ],
            },
        }

        with TemporaryDirectory() as tmp_dir:
            downloaded = download_message_attachments(service, message, Path(tmp_dir))

        self.assertEqual(downloaded, [Path(tmp_dir) / "notes.txt"])
        attachments_api.get.assert_called_once_with(userId="me", messageId="m1", id="att-txt")

    def test_message_has_non_calendar_attachment_ignores_ics_only_invites(self) -> None:
        message = {
            "payload": {
                "parts": [
                    {"filename": "invite.ics", "body": {"attachmentId": "att-1"}},
                    {"filename": "MEETING.ICS", "body": {"data": "aGVsbG8"}},
                ]
            }
        }

        self.assertFalse(message_has_non_calendar_attachment(message))

    def test_message_has_non_calendar_attachment_accepts_real_files(self) -> None:
        message = {
            "payload": {
                "parts": [
                    {"filename": "invite.ics", "body": {"attachmentId": "att-1"}},
                    {"filename": "notes.pdf", "body": {"attachmentId": "att-2"}},
                ]
            }
        }

        self.assertTrue(message_has_non_calendar_attachment(message))


if __name__ == "__main__":
    unittest.main()
