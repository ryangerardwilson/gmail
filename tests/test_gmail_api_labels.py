import unittest
from unittest.mock import MagicMock

from gmail_cli.gmail_api import star_message, unstar_message


class GmailApiLabelTests(unittest.TestCase):
    def test_star_message_marks_read(self) -> None:
        service = MagicMock()
        messages_api = service.users.return_value.messages.return_value
        messages_api.modify.return_value.execute.return_value = {"id": "m1", "threadId": "t1"}

        star_message(service, "m1")

        messages_api.modify.assert_called_once_with(
            userId="me",
            id="m1",
            body={"addLabelIds": ["STARRED"], "removeLabelIds": ["UNREAD"]},
        )

    def test_unstar_message_does_not_mark_unread(self) -> None:
        service = MagicMock()
        messages_api = service.users.return_value.messages.return_value
        messages_api.modify.return_value.execute.return_value = {"id": "m1", "threadId": "t1"}

        unstar_message(service, "m1")

        messages_api.modify.assert_called_once_with(
            userId="me",
            id="m1",
            body={"removeLabelIds": ["STARRED"]},
        )


if __name__ == "__main__":
    unittest.main()
