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
            preset_email="me@example.com",
            spam_excludes=[],
            threshold=5,
        )
        self.assertEqual([item.sender for item in selected], ["a@spam.com"])

    def test_make_identify_decision(self) -> None:
        candidates = select_spam_candidates(
            counts={"z@spam.com": 8, "a@spam.com": 7},
            existing_spam=[],
            preset_email="me@example.com",
            spam_excludes=[],
            threshold=5,
        )
        decision = make_identify_decision(candidates)
        self.assertEqual(decision.add_to_spam, ["a@spam.com", "z@spam.com"])

    def test_select_spam_candidates_excludes_same_domain_as_preset(self) -> None:
        counts = {
            "promo@example.com": 20,
            "spam@other.com": 9,
        }
        selected = select_spam_candidates(
            counts=counts,
            existing_spam=[],
            preset_email="me@example.com",
            spam_excludes=[],
            threshold=5,
        )
        self.assertEqual([item.sender for item in selected], ["spam@other.com"])

    def test_select_spam_candidates_respects_spam_excludes(self) -> None:
        counts = {"keep@safe.com": 9, "spam@x.com": 8}
        selected = select_spam_candidates(
            counts=counts,
            existing_spam=[],
            preset_email="me@example.com",
            spam_excludes=["keep@safe.com"],
            threshold=5,
        )
        self.assertEqual([item.sender for item in selected], ["spam@x.com"])

    def test_select_spam_candidates_respects_domain_targets(self) -> None:
        counts = {"a@blocked.com": 10, "b@safe.com": 8}
        selected = select_spam_candidates(
            counts=counts,
            existing_spam=["@blocked.com"],
            preset_email="me@example.com",
            spam_excludes=[],
            threshold=5,
        )
        self.assertEqual([item.sender for item in selected], ["b@safe.com"])

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

    def test_run_cleanup_for_account_skips_spam_excludes(self) -> None:
        account = AccountConfig(
            preset="1",
            email="me@example.com",
            client_secret_file=MagicMock(),
            signature_file=MagicMock(),
            spam_senders=["spam@x.com", "trusted@x.com"],
            spam_excludes=["trusted@x.com"],
        )
        service = MagicMock()
        with patch("gmail_cli.spam_flow.list_message_ids") as list_ids, patch(
            "gmail_cli.spam_flow.batch_delete_messages"
        ) as delete_ids:
            list_ids.return_value = ["1"]
            delete_ids.return_value = 1
            result = run_cleanup_for_account(service, account)

        self.assertEqual(result.trashed_spam, 1)
        self.assertEqual(list_ids.call_args.args[1], "from:spam@x.com")

    def test_run_cleanup_for_account_skips_spam_exclude_domains(self) -> None:
        account = AccountConfig(
            preset="1",
            email="me@example.com",
            client_secret_file=MagicMock(),
            signature_file=MagicMock(),
            spam_senders=["@blocked.com", "spam@x.com"],
            spam_excludes=["@blocked.com"],
        )
        service = MagicMock()
        with patch("gmail_cli.spam_flow.list_message_ids") as list_ids, patch(
            "gmail_cli.spam_flow.batch_delete_messages"
        ) as delete_ids:
            list_ids.return_value = ["1"]
            delete_ids.return_value = 1
            result = run_cleanup_for_account(service, account)

        self.assertEqual(result.trashed_spam, 1)
        self.assertEqual(list_ids.call_args.args[1], "from:spam@x.com")


if __name__ == "__main__":
    unittest.main()
