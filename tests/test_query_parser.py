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

    def test_rejects_unknown_free_text(self) -> None:
        with self.assertRaises(UsageError):
            parse_list_query_args(["project-x"], default_limit=10)

    def test_invalid_limit_raises_usage_error(self) -> None:
        with self.assertRaises(UsageError):
            parse_list_query_args(["0"], default_limit=10)


if __name__ == "__main__":
    unittest.main()
