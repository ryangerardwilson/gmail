import unittest

from gmail_cli.errors import UsageError
from gmail_cli.query_parser import parse_list_query_args


class QueryParserTests(unittest.TestCase):
    def test_parses_from_and_limit_args(self) -> None:
        parsed = parse_list_query_args(["-f", "maanas", "1"], default_limit=10)
        self.assertEqual(parsed.gmail_query, "from:maanas")
        self.assertEqual(parsed.max_results, 1)

    def test_parses_contains_with_default_limit(self) -> None:
        parsed = parse_list_query_args(["-c", "project-x"], default_limit=10)
        self.assertEqual(parsed.gmail_query, "project-x")
        self.assertEqual(parsed.max_results, 10)

    def test_parses_time_limit_relative_window(self) -> None:
        parsed = parse_list_query_args(["-f", "geeta", "-tl", "2w"], default_limit=10)
        self.assertEqual(parsed.gmail_query, "from:geeta newer_than:14d")
        self.assertEqual(parsed.max_results, 10)

    def test_parses_time_limit_named_month(self) -> None:
        parsed = parse_list_query_args(["-tl", "jan 2025", "20"], default_limit=10)
        self.assertEqual(parsed.gmail_query, "after:2024/12/31 before:2025/02/01")
        self.assertEqual(parsed.max_results, 20)

    def test_parses_time_limit_explicit_date_range(self) -> None:
        parsed = parse_list_query_args(["-tl", "2025-01-10..2025-01-20"], default_limit=10)
        self.assertEqual(parsed.gmail_query, "after:2025/01/09 before:2025/01/21")
        self.assertEqual(parsed.max_results, 10)

    def test_rejects_unknown_free_text(self) -> None:
        with self.assertRaises(UsageError):
            parse_list_query_args(["project-x"], default_limit=10)

    def test_invalid_limit_raises_usage_error(self) -> None:
        with self.assertRaises(UsageError):
            parse_list_query_args(["0"], default_limit=10)

    def test_invalid_time_limit_raises_usage_error(self) -> None:
        with self.assertRaises(UsageError):
            parse_list_query_args(["-tl", "jan-25"], default_limit=10)


if __name__ == "__main__":
    unittest.main()
