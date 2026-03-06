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
    def _set_linkedin_sender(self, message: dict) -> None:
        headers = message.get("payload", {}).get("headers", [])
        for item in headers:
            if item.get("name", "").lower() == "from":
                item["value"] = "LinkedIn <jobs-noreply@linkedin.com>"
                return

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

    def test_prefers_html_when_plaintext_is_footer_noise(self) -> None:
        plain = (
            "This email was intended for Ryan.\n"
            "Learn why we included this: https://www.linkedin.com/help/x\n"
            "Unsubscribe: https://www.linkedin.com/unsub/very/long/tracking/token\n"
            "You are receiving LinkedIn notification emails.\n"
            "LinkedIn Corporation."
        )
        html = (
            "<div>Thank you for your interest in the role.</div>"
            "<div>Unfortunately, we will not move forward with your application.</div>"
        )
        message = _message_with_payload(
            {
                "mimeType": "multipart/alternative",
                "parts": [
                    {"mimeType": "text/plain", "body": {"data": _encode(plain)}},
                    {"mimeType": "text/html", "body": {"data": _encode(html)}},
                ],
            }
        )
        row = summarize_message(message, trim_body=False)
        self.assertEqual(
            row["body"],
            "Thank you for your interest in the role.\nUnfortunately, we will not move forward with your application.",
        )

    def test_strips_footer_noise_before_snippet_fallback(self) -> None:
        noisy_body = (
            "Your update from Company\n\n"
            "This email was intended for Ryan.\n"
            "Learn why we included this: https://linkedin.example/help\n"
            "You are receiving LinkedIn notification emails.\n"
            "Unsubscribe: https://linkedin.example/unsub\n"
            "Help: https://linkedin.example/help2\n"
            "LinkedIn Corporation."
        )
        snippet = (
            "Thank you for your interest in the role. "
            "Unfortunately, we will not move forward with your application."
        )
        message = _message_with_payload(
            {"mimeType": "text/plain", "body": {"data": _encode(noisy_body)}}
        )
        self._set_linkedin_sender(message)
        message["snippet"] = snippet
        row = summarize_message(message, trim_body=False)
        self.assertEqual(row["body"], "Your update from Company")

    def test_html_quoted_printable_payload_decodes_before_parse(self) -> None:
        html_qp = (
            "<html><body>"
            "<p>Thank you for your interest in the role.</p>=0A"
            "<p>Visit <a href=3D\"https://example.com\">Link</a></p>"
            "</body></html>"
        )
        message = _message_with_payload(
            {
                "mimeType": "text/html",
                "headers": [{"name": "Content-Transfer-Encoding", "value": "quoted-printable"}],
                "body": {"data": _encode(html_qp)},
            }
        )
        row = summarize_message(message, trim_body=False)
        self.assertEqual(
            row["body"],
            "Thank you for your interest in the role.\n\nVisit Link (https://example.com)",
        )

    def test_html_hides_preheader_block(self) -> None:
        html = (
            '<html><body><div data-email-preheader="true">Preheader noise</div>'
            "<p>Real body line.</p></body></html>"
        )
        message = _message_with_payload({"mimeType": "text/html", "body": {"data": _encode(html)}})
        row = summarize_message(message, trim_body=False)
        self.assertEqual(row["body"], "Real body line.")

    def test_snippet_not_used_when_it_contains_preheader_noise_chars(self) -> None:
        body = (
            "This email was intended for Ryan.\n"
            "You are receiving LinkedIn notification emails.\n"
            "Unsubscribe: https://linkedin.example/unsub\n"
            "Help: https://linkedin.example/help\n"
            "LinkedIn Corporation."
        )
        noisy_snippet = (
            "Your application to Senior Software Engineer Team Lead at FindYou Consulting GmbH "
            "\u034f \u034f \u034f \u034f"
        )
        message = _message_with_payload(
            {"mimeType": "text/plain", "body": {"data": _encode(body)}}
        )
        self._set_linkedin_sender(message)
        message["snippet"] = noisy_snippet
        row = summarize_message(message, trim_body=False)
        self.assertEqual(row["body"], body)

    def test_linkedin_plain_footer_heavy_prefers_html_with_decision_text(self) -> None:
        plain = (
            "Your update from FindYou Consulting GmbH\n\n"
            "----------------------------------------\n\n"
            "This email was intended for Ryan Gerard Wilson.\n"
            "You are receiving LinkedIn notification emails.\n"
            "Unsubscribe: https://linkedin.example/unsub\n"
            "Help: https://linkedin.example/help\n"
            "LinkedIn Corporation."
        )
        html = (
            "<html><body>"
            "<h2>Your update from FindYou Consulting GmbH</h2>"
            "<p>Senior Software Engineer Team Lead</p>"
            "<p>FindYou Consulting GmbH · Hamburg, Hamburg, Germany</p>"
            "<p>Applied on Mar 3</p>"
            "<p>Thank you for your interest in the Senior Software Engineer Team Lead position at "
            "FindYou Consulting GmbH in Hamburg, Hamburg, Germany. Unfortunately, we will not be moving "
            "forward with your application, but we appreciate your time and interest in FindYou Consulting "
            "GmbH.<br><br>Regards,<br>FindYou Consulting GmbH</p>"
            "<h2>Top jobs looking for your skills</h2>"
            "</body></html>"
        )
        message = _message_with_payload(
            {
                "mimeType": "multipart/alternative",
                "parts": [
                    {"mimeType": "text/plain", "body": {"data": _encode(plain)}},
                    {"mimeType": "text/html", "body": {"data": _encode(html)}},
                ],
            }
        )
        self._set_linkedin_sender(message)
        row = summarize_message(message, trim_body=False)
        self.assertIn("Thank you for your interest in the Senior Software Engineer Team Lead position", row["body"])
        self.assertNotIn("Top jobs looking for your skills", row["body"])

    def test_non_linkedin_does_not_apply_linkedin_promo_cut(self) -> None:
        message = {
            "id": "m2",
            "threadId": "t2",
            "snippet": "",
            "payload": {
                "headers": [
                    {"name": "From", "value": "News <digest@example.com>"},
                    {"name": "To", "value": "me@example.com"},
                    {"name": "Subject", "value": "Digest"},
                    {"name": "Date", "value": "Fri, 01 Jan 2021 10:00:00 +0000"},
                ],
                "mimeType": "text/plain",
                "body": {
                    "data": _encode(
                        "Intro paragraph\nTop jobs looking for your skills\nThis line should remain for non-linkedin"
                    )
                },
            },
        }
        row = summarize_message(message, trim_body=False)
        self.assertIn("Top jobs looking for your skills", row["body"])

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
