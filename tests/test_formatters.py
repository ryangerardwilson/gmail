import base64
import os
import unittest

from gmail_cli.formatters import (
    ANSI_GRAY,
    ANSI_RESET,
    ANSI_WHITE,
    render_message_open,
    summarize_message,
)


def _encode(data: str) -> str:
    return base64.urlsafe_b64encode(data.encode("utf-8")).decode("ascii").rstrip("=")


def _message_with_payload(payload: dict) -> dict:
    return {
        "id": "m1",
        "threadId": "t1",
        "snippet": "fallback",
        "payload": {
            "headers": [
                {"name": "From", "value": "Alice <alice@example.com>"},
                {"name": "To", "value": "me@example.com"},
                {"name": "Subject", "value": "Hello"},
                {"name": "Date", "value": "Fri, 01 Jan 2021 10:00:00 +0000"},
            ],
            **payload,
        },
    }


class FormatterTests(unittest.TestCase):
    def test_html_body_strips_tags(self) -> None:
        message = _message_with_payload(
            {"mimeType": "text/html", "body": {"data": _encode("<div>hello</div>")}}
        )
        row = summarize_message(message, trim_body=False)
        self.assertEqual(row["body"], "hello")

    def test_html_anchor_becomes_text_and_url(self) -> None:
        message = _message_with_payload(
            {
                "mimeType": "text/html",
                "body": {"data": _encode('<div>See <a href="https://example.com">Link</a></div>')},
            }
        )
        row = summarize_message(message, trim_body=False)
        self.assertEqual(row["body"], "See Link (https://example.com)")

    def test_html_nested_anchor_keeps_text(self) -> None:
        message = _message_with_payload(
            {
                "mimeType": "text/html",
                "body": {"data": _encode('<a href="https://example.com"><b>Link</b></a>')},
            }
        )
        row = summarize_message(message, trim_body=False)
        self.assertEqual(row["body"], "Link (https://example.com)")

    def test_prefers_text_plain_over_html(self) -> None:
        message = _message_with_payload(
            {
                "mimeType": "multipart/alternative",
                "parts": [
                    {"mimeType": "text/plain", "body": {"data": _encode("plain body")}},
                    {
                        "mimeType": "text/html",
                        "body": {"data": _encode('<div>html <a href="https://x.com">Link</a></div>')},
                    },
                ],
            }
        )
        row = summarize_message(message, trim_body=False)
        self.assertEqual(row["body"], "plain body")

    def test_html_entities_are_unescaped(self) -> None:
        message = _message_with_payload(
            {"mimeType": "text/html", "body": {"data": _encode("<div>a&nbsp;&amp;&nbsp;b</div>")}}
        )
        row = summarize_message(message, trim_body=False)
        self.assertEqual(row["body"], "a & b")

    def test_html_ignores_style_and_script_blocks(self) -> None:
        html = (
            "<html><head><style>body{font-family:Roboto;} .x{color:red;}</style></head>"
            "<body><div>Hello</div><script>console.log('x')</script>"
            '<a href="https://example.com">Join</a></body></html>'
        )
        message = _message_with_payload({"mimeType": "text/html", "body": {"data": _encode(html)}})
        row = summarize_message(message, trim_body=False)
        self.assertEqual(row["body"], "Hello\nJoin (https://example.com)")

    def test_strip_history_on_on_date_at_time_wrote_marker(self) -> None:
        body = (
            "Hi Ryan,\n"
            "Thanks for the call.\n"
            "On Mar 3, 2026, at 10:17 AM, Ryan Wilson <ryan@wilsonfamilyoffice.in> wrote:\n"
            "\n"
            "Earlier chain text"
        )
        message = _message_with_payload({"mimeType": "text/plain", "body": {"data": _encode(body)}})
        row = summarize_message(message, trim_body=False, strip_history=True)
        self.assertEqual(row["body"], "Hi Ryan,\nThanks for the call.")

    def test_render_open_header_gray_body_white_for_non_self(self) -> None:
        message = _message_with_payload({"mimeType": "text/plain", "body": {"data": _encode("hello")}})
        original = os.environ.get("NO_COLOR")
        if "NO_COLOR" in os.environ:
            del os.environ["NO_COLOR"]
        try:
            output = render_message_open(message, "me@example.com")
        finally:
            if original is not None:
                os.environ["NO_COLOR"] = original
        self.assertIn(f"{ANSI_GRAY}message_id: m1", output)
        self.assertIn(f"body:{ANSI_RESET}\n\n{ANSI_WHITE}hello{ANSI_RESET}", output)

    def test_render_open_no_color_has_no_ansi(self) -> None:
        message = _message_with_payload({"mimeType": "text/plain", "body": {"data": _encode("hello")}})
        original = os.environ.get("NO_COLOR")
        os.environ["NO_COLOR"] = "1"
        try:
            output = render_message_open(message, "me@example.com")
        finally:
            if original is None:
                del os.environ["NO_COLOR"]
            else:
                os.environ["NO_COLOR"] = original
        self.assertNotIn("\033[", output)


if __name__ == "__main__":
    unittest.main()
