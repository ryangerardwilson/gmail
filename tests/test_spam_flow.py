import unittest
from unittest.mock import MagicMock, patch

from gmail_cli.config import AccountConfig
from gmail_cli.spam_flow import (
    make_identify_decision,
    parse_exclusion_indexes,
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
            existing_not_spam=["c@gmail.com"],
            threshold=5,
        )
        self.assertEqual([item.sender for item in selected], ["a@spam.com"])
        self.assertEqual(selected[0].unread_count, 7)

    def test_parse_exclusion_indexes(self) -> None:
        parsed = parse_exclusion_indexes("1, 3,3", 5)
        self.assertEqual(parsed, {1, 3})
        with self.assertRaises(ValueError):
            parse_exclusion_indexes("6", 5)

    def test_make_identify_decision(self) -> None:
        candidates = select_spam_candidates(
            counts={"z@spam.com": 8, "a@spam.com": 7},
            existing_spam=[],
            existing_not_spam=[],
            threshold=5,
        )
        decision = make_identify_decision(candidates, {1})
        self.assertEqual(decision.add_to_not_spam, ["z@spam.com"])
        self.assertEqual(decision.add_to_spam, ["a@spam.com"])

    def test_run_cleanup_for_account(self) -> None:
        account = AccountConfig(
            preset="1",
            email="me@example.com",
            client_secret_file=MagicMock(),
            signature_file=MagicMock(),
            spam_senders=["spam@x.com"],
            not_spam_senders=["friend@y.com"],
        )
        service = MagicMock()
        with patch("gmail_cli.spam_flow.list_message_ids") as list_ids, patch(
            "gmail_cli.spam_flow.batch_delete_messages"
        ) as delete_ids, patch("gmail_cli.spam_flow.batch_mark_messages_read") as mark_read:
            list_ids.side_effect = [["1", "2"], ["3"]]
            delete_ids.return_value = 2
            mark_read.return_value = 1

            result = run_cleanup_for_account(service, account)

        self.assertEqual(result.trashed_spam, 2)
        self.assertEqual(result.marked_not_spam_read, 1)
        self.assertEqual(list_ids.call_count, 2)


if __name__ == "__main__":
    unittest.main()
