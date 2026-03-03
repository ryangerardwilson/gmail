import unittest
from unittest.mock import MagicMock, patch

from gmail_cli.config import AccountConfig
from gmail_cli.spam_flow import (
    make_identify_decision,
    run_cleanup_for_account,
    select_spam_candidates,
)


class SpamFlowTests(unittest.TestCase):
    def test_select_spam_candidates_threshold_and_filters(self) -> None:
        counts = {
            "a@spam.com": 7,
            "b@spam.com": 6,
            "c@gmail.com": 9,
            "d@spam.com": 5,
        }
        selected = select_spam_candidates(
            counts=counts,
            existing_spam=["b@spam.com"],
            threshold=5,
        )
        self.assertEqual([item.sender for item in selected], ["c@gmail.com", "a@spam.com"])

    def test_make_identify_decision(self) -> None:
        candidates = select_spam_candidates(
            counts={"z@spam.com": 8, "a@spam.com": 7},
            existing_spam=[],
            threshold=5,
        )
        decision = make_identify_decision(candidates)
        self.assertEqual(decision.add_to_spam, ["a@spam.com", "z@spam.com"])

    def test_run_cleanup_for_account(self) -> None:
        account = AccountConfig(
            preset="1",
            email="me@example.com",
            client_secret_file=MagicMock(),
            signature_file=MagicMock(),
            spam_senders=["spam@x.com"],
        )
        service = MagicMock()
        with patch("gmail_cli.spam_flow.list_message_ids") as list_ids, patch(
            "gmail_cli.spam_flow.batch_delete_messages"
        ) as delete_ids:
            list_ids.return_value = ["1", "2"]
            delete_ids.return_value = 2
            result = run_cleanup_for_account(service, account)

        self.assertEqual(result.trashed_spam, 2)
        self.assertEqual(list_ids.call_count, 1)


if __name__ == "__main__":
    unittest.main()
