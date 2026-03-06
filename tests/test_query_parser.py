import unittest

from gmail_cli.errors import UsageError
from gmail_cli.query_parser import parse_declarative_query


class QueryParserTests(unittest.TestCase):
    def test_parses_example_query(self) -> None:
        parsed = parse_declarative_query("from maanas limit 1", default_limit=10)
        self.assertEqual(parsed.gmail_query, "from:maanas")
        self.assertEqual(parsed.max_results, 1)

    def test_unknown_tokens_fallback_to_generic_terms(self) -> None:
        parsed = parse_declarative_query("project-x unread", default_limit=10)
        self.assertEqual(parsed.gmail_query, "project-x is:unread")
        self.assertIsNone(parsed.max_results)

    def test_invalid_limit_raises_usage_error(self) -> None:
        with self.assertRaises(UsageError):
            parse_declarative_query("from maanas limit nope", default_limit=10)


if __name__ == "__main__":
    unittest.main()
