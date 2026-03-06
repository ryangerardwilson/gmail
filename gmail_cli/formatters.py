from __future__ import annotations

import base64
from datetime import timedelta, timezone
from html import unescape
from html.parser import HTMLParser
import os
import quopri
import re
from email.utils import parseaddr
from email.utils import parsedate_to_datetime
from typing import Any
ANSI_RESET = "\033[0m"
ANSI_GRAY = "\033[38;5;245m"
ANSI_WHITE = "\033[97m"
_BLOCK_TAGS = {
    "address",
    "article",
    "aside",
    "blockquote",
    "div",
    "dl",
    "fieldset",
    "figcaption",
    "figure",
    "footer",
    "form",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "header",
    "li",
    "main",
    "nav",
    "ol",
    "p",
    "pre",
    "section",
    "table",
    "tr",
    "ul",
}
_BREAK_TAGS = {"br", "hr"}
_SKIP_CONTENT_TAGS = {"style", "script", "noscript", "head", "title"}
_COMMON_FOOTER_NOISE_PATTERNS = (
    "unsubscribe",
    "privacy policy",
    "terms of service",
    "manage your email preferences",
)
_LINKEDIN_FOOTER_NOISE_PATTERNS = (
    "unsubscribe",
    "you are receiving",
    "learn why we included this",
    "help:",
    "linkedin corporation",
    "notification emails",
)
_COMMON_FOOTER_CUT_MARKERS = (
    "unsubscribe:",
    "privacy policy",
    "terms of service",
    "manage your email preferences",
)
_LINKEDIN_FOOTER_CUT_MARKERS = (
    "this email was intended for",
    "you are receiving linkedin notification emails",
    "unsubscribe:",
    "help:",
    "linkedin corporation",
)
_LINKEDIN_PROMO_CUT_MARKERS = (
    "top jobs looking for your skills",
    "see more jobs",
    "get the new linkedin desktop app",
)


class _HtmlTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._anchor_stack: list[tuple[str | None, int]] = []
        self._visible_char_count = 0
        self._skip_depth = 0
        self._hidden_tag_stack: list[str] = []

    def _append(self, text: str, count_visible: bool = True) -> None:
        if not text:
            return
        self._parts.append(text)
        if count_visible:
            self._visible_char_count += len(re.sub(r"\s+", "", text))

    def _append_newline(self) -> None:
        if not self._parts:
            return
        if self._parts[-1].endswith("\n"):
            return
        self._parts.append("\n")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.lower()
        if lowered in _SKIP_CONTENT_TAGS:
            self._skip_depth += 1
            return
        if self._hidden_tag_stack:
            return
        attrs_map = {str(key).lower(): (value or "") for key, value in attrs}
        if _is_hidden_html_node(lowered, attrs_map):
            self._hidden_tag_stack.append(lowered)
            return
        if self._skip_depth:
            return
        if lowered in _BLOCK_TAGS or lowered in _BREAK_TAGS:
            self._append_newline()
        if lowered == "a":
            href = None
            for key, value in attrs:
                if key.lower() == "href" and isinstance(value, str) and value.strip():
                    href = value.strip()
                    break
            self._anchor_stack.append((href, self._visible_char_count))

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered in _SKIP_CONTENT_TAGS:
            if self._skip_depth:
                self._skip_depth -= 1
            return
        if self._hidden_tag_stack:
            if lowered == self._hidden_tag_stack[-1]:
                self._hidden_tag_stack.pop()
            return
        if self._skip_depth:
            return
        if lowered == "a" and self._anchor_stack:
            href, visible_at_open = self._anchor_stack.pop()
            if href and self._visible_char_count > visible_at_open:
                self._append(f" ({href})", count_visible=False)
        if lowered in _BLOCK_TAGS or lowered in _BREAK_TAGS:
            self._append_newline()

    def handle_data(self, data: str) -> None:
        if self._hidden_tag_stack:
            return
        if self._skip_depth:
            return
        self._append(data)

    def as_text(self) -> str:
        return "".join(self._parts)


def _header_map(message: dict[str, Any]) -> dict[str, str]:
    headers = message.get("payload", {}).get("headers", [])
    result: dict[str, str] = {}
    for item in headers:
        name = item.get("name")
        value = item.get("value")
        if isinstance(name, str) and isinstance(value, str):
            result[name.lower()] = value
    return result


def _payload_header_map(payload: dict[str, Any]) -> dict[str, str]:
    headers = payload.get("headers", [])
    result: dict[str, str] = {}
    if not isinstance(headers, list):
        return result
    for item in headers:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        value = item.get("value")
        if isinstance(name, str) and isinstance(value, str):
            result[name.lower()] = value
    return result


def _decode_base64_url(data: str) -> str:
    pad = "=" * ((4 - len(data) % 4) % 4)
    decoded = base64.urlsafe_b64decode(data + pad)
    return decoded.decode("utf-8", errors="replace")


def _decode_payload_body_data(payload: dict[str, Any], data: str) -> str:
    pad = "=" * ((4 - len(data) % 4) % 4)
    raw = base64.urlsafe_b64decode(data + pad)

    encoding = _payload_header_map(payload).get("content-transfer-encoding", "").strip().lower()
    if encoding == "quoted-printable":
        raw = quopri.decodestring(raw)

    return raw.decode("utf-8", errors="replace")


def _extract_text_plain(payload: dict[str, Any]) -> str | None:
    mime_type = payload.get("mimeType")
    body_data = payload.get("body", {}).get("data")
    if mime_type == "text/plain" and isinstance(body_data, str):
        return _decode_payload_body_data(payload, body_data)

    parts = payload.get("parts", [])
    if isinstance(parts, list):
        for part in parts:
            if not isinstance(part, dict):
                continue
            text = _extract_text_plain(part)
            if text:
                return text
    return None


def _extract_any_body(payload: dict[str, Any]) -> str:
    body_data = payload.get("body", {}).get("data")
    if isinstance(body_data, str):
        return _decode_payload_body_data(payload, body_data)

    parts = payload.get("parts", [])
    if isinstance(parts, list):
        for part in parts:
            if not isinstance(part, dict):
                continue
            text = _extract_any_body(part)
            if text:
                return text
    return ""


def _extract_mime_body(payload: dict[str, Any], mime_type: str) -> str | None:
    payload_mime = payload.get("mimeType")
    body_data = payload.get("body", {}).get("data")
    if payload_mime == mime_type and isinstance(body_data, str):
        return _decode_payload_body_data(payload, body_data)

    parts = payload.get("parts", [])
    if isinstance(parts, list):
        for part in parts:
            if not isinstance(part, dict):
                continue
            text = _extract_mime_body(part, mime_type)
            if text:
                return text
    return None


def _html_to_text_preserve_links(html_body: str) -> str:
    if not html_body:
        return ""

    parser = _HtmlTextParser()
    parser.feed(html_body)
    parser.close()
    text = unescape(parser.as_text()).replace("\xa0", " ")
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    normalized_lines: list[str] = []
    for line in text.split("\n"):
        compact = re.sub(r"[ \t]+", " ", line).strip()
        if compact:
            normalized_lines.append(compact)
            continue
        if normalized_lines and normalized_lines[-1] != "":
            normalized_lines.append("")

    return "\n".join(normalized_lines).strip()


def _is_hidden_html_node(tag: str, attrs_map: dict[str, str]) -> bool:
    if attrs_map.get("hidden", "") != "":
        return True
    if attrs_map.get("aria-hidden", "").strip().lower() == "true":
        return True
    if attrs_map.get("data-email-preheader", "").strip().lower() in {"true", "1", "yes"}:
        return True
    if attrs_map.get("role", "").strip().lower() == "presentation" and tag == "img":
        return True

    style = attrs_map.get("style", "").lower()
    hidden_style_tokens = (
        "display:none",
        "display: none",
        "visibility:hidden",
        "visibility: hidden",
        "opacity:0",
        "opacity: 0",
        "max-height:0",
        "max-height: 0",
        "height:0",
        "height: 0",
        "mso-hide:all",
        "mso-hide: all",
    )
    if any(token in style for token in hidden_style_tokens):
        return True

    class_value = attrs_map.get("class", "").lower()
    class_tokens = set(re.split(r"\s+", class_value.strip())) if class_value.strip() else set()
    if class_tokens.intersection({"hidden", "invisible", "opacity-0", "text-transparent"}):
        return True
    return False


def _count_footer_noise_hits(text: str, linkedin_mode: bool = False) -> int:
    lowered = text.lower()
    patterns = _COMMON_FOOTER_NOISE_PATTERNS
    if linkedin_mode:
        patterns = patterns + _LINKEDIN_FOOTER_NOISE_PATTERNS
    return sum(1 for pattern in patterns if pattern in lowered)


def _body_quality_score(text: str, linkedin_mode: bool = False) -> float:
    if not text:
        return -100.0

    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lowered = normalized.lower()
    non_empty_lines = [line.strip() for line in normalized.split("\n") if line.strip()]
    word_count = len(re.findall(r"[a-zA-Z]{2,}", normalized))
    sentence_count = len(re.findall(r"[.!?](?:\s|$)", normalized))
    url_count = len(re.findall(r"https?://|www\.", lowered))
    long_token_count = len(re.findall(r"\S{70,}", normalized))
    footer_hits = _count_footer_noise_hits(normalized, linkedin_mode=linkedin_mode)

    score = 0.0
    score += min(word_count, 220) * 0.22
    score += min(sentence_count, 12) * 1.8
    score -= url_count * 2.8
    score -= long_token_count * 0.75
    score -= footer_hits * 7.0

    if word_count > 0 and len(non_empty_lines) > 0:
        avg_words_per_line = word_count / len(non_empty_lines)
        if avg_words_per_line < 2.0:
            score -= 4.0

    if "this email was intended for" in lowered:
        score -= 10.0

    return score


def _strip_footer_sections(text: str, linkedin_mode: bool = False) -> str:
    if not text:
        return ""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")

    cutoff = len(lines)
    footer_markers = _COMMON_FOOTER_CUT_MARKERS
    promo_markers: tuple[str, ...] = ()
    if linkedin_mode:
        footer_markers = footer_markers + _LINKEDIN_FOOTER_CUT_MARKERS
        promo_markers = _LINKEDIN_PROMO_CUT_MARKERS

    for i, line in enumerate(lines):
        lowered = line.strip().lower()
        if not lowered:
            continue
        if any(marker in lowered for marker in footer_markers):
            cutoff = i
            break
    if promo_markers:
        for i, line in enumerate(lines):
            lowered = line.strip().lower()
            if not lowered:
                continue
            if any(marker in lowered for marker in promo_markers):
                cutoff = min(cutoff, i)
                break

    cleaned = "\n".join(lines[:cutoff]).strip()
    return cleaned if cleaned else text.strip()


def _prefer_html_over_plain(plain_body: str, html_as_text: str, linkedin_mode: bool = False) -> bool:
    plain_lower = plain_body.lower()
    if linkedin_mode:
        html_word_count = len(re.findall(r"[a-zA-Z]{2,}", html_as_text))
        plain_footer_heavy = (
            "this email was intended for" in plain_lower
            or "you are receiving linkedin notification emails" in plain_lower
            or _count_footer_noise_hits(plain_body, linkedin_mode=True) >= 2
        )
        html_has_substantive_sentence = bool(
            re.search(r"\b(thank you for your interest|unfortunately, we will not)\b", html_as_text.lower())
        )
        if plain_footer_heavy and html_word_count >= 35 and html_has_substantive_sentence:
            return True

    plain_score = _body_quality_score(plain_body, linkedin_mode=linkedin_mode)
    html_score = _body_quality_score(html_as_text, linkedin_mode=linkedin_mode)
    footer_heavy_plain = _count_footer_noise_hits(plain_body, linkedin_mode=linkedin_mode) >= 2
    threshold = 2.0 if (linkedin_mode and footer_heavy_plain) else 8.0
    return html_score >= plain_score + threshold


def _should_prefer_snippet(body: str, snippet: str, linkedin_mode: bool = False) -> bool:
    if not body or not snippet:
        return False

    body_hits = _count_footer_noise_hits(body, linkedin_mode=linkedin_mode)
    min_hits = 2 if linkedin_mode else 3
    if body_hits < min_hits:
        return False

    snippet_clean = re.sub(r"\s+", " ", snippet).strip()
    if len(snippet_clean) < 30:
        return False

    snippet_words = len(re.findall(r"[a-zA-Z]{2,}", snippet_clean))
    if snippet_words < 7:
        return False
    if len(re.findall(r"[\u034f\u00ad\u200b\u200c\u200d]", snippet_clean)) >= 3:
        return False
    if len(re.findall(r"https?://|www\.", snippet_clean.lower())) > 1:
        return False

    if snippet_clean.lower() in body.lower():
        return False

    return True


def _is_linkedin_sender(from_email: str) -> bool:
    domain = from_email.strip().lower().split("@")[-1] if from_email else ""
    return domain.endswith("linkedin.com")


def _timezone_from_offset(utc_offset: str) -> timezone:
    sign = 1 if utc_offset.startswith("+") else -1
    hours = int(utc_offset[1:3])
    minutes = int(utc_offset[4:6])
    return timezone(sign * timedelta(hours=hours, minutes=minutes))


def _to_local_date(raw_date: str, utc_offset: str) -> str:
    if not raw_date:
        return ""
    try:
        dt = parsedate_to_datetime(raw_date)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        local_dt = dt.astimezone(_timezone_from_offset(utc_offset))
        return local_dt.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return raw_date


def _strip_quoted_history(body: str) -> str:
    if not body:
        return ""

    lines = body.splitlines()
    output: list[str] = []
    quote_markers = (
        r"^On .+wrote:$",
        r"^On .+,\s*at .+wrote:$",
        r"^From: .+",
        r"^-{2,}\s*Original Message\s*-{2,}$",
    )

    prev_stripped = ""
    for line in lines:
        stripped = line.strip()
        lowered = stripped.lower()

        # Handles quote headers that may appear without trailing "wrote:"
        # e.g. "On Sat, ... Ryan Wilson <ryan@example.com>"
        if lowered.startswith("on ") and "<" in stripped and "@" in stripped and ">" in stripped:
            break

        # Handles multi-line quote headers:
        # "On ... <email>" followed by "wrote:" on the next line.
        if lowered == "wrote:" and prev_stripped.lower().startswith("on "):
            break
        if lowered.startswith("on ") and lowered.endswith("wrote:"):
            break
        if stripped.startswith(">"):
            break
        if any(re.match(pattern, stripped) for pattern in quote_markers):
            break
        output.append(line)
        prev_stripped = stripped

    cleaned = "\n".join(output).strip()
    return cleaned if cleaned else body.strip()


def _trim_body(body: str, max_lines: int = 24, max_chars: int = 2000) -> str:
    if not body:
        return ""

    lines = body.splitlines()
    condensed: list[str] = []
    blank_count = 0
    for line in lines:
        if line.strip():
            blank_count = 0
            condensed.append(line.rstrip())
            continue

        blank_count += 1
        if blank_count <= 1:
            condensed.append("")

    body_text = "\n".join(condensed).strip()

    was_trimmed = False
    if len(body_text) > max_chars:
        body_text = body_text[:max_chars].rstrip()
        was_trimmed = True

    split_lines = body_text.splitlines()
    if len(split_lines) > max_lines:
        body_text = "\n".join(split_lines[:max_lines]).rstrip()
        was_trimmed = True

    return body_text


def summarize_message(
    message: dict[str, Any],
    trim_body: bool = True,
    utc_offset: str = "+05:30",
    strip_history: bool = True,
) -> dict[str, str]:
    headers = _header_map(message)
    from_raw = headers.get("from", "")
    from_name, from_email = parseaddr(from_raw)
    if from_name and from_email:
        display_from = f"{from_name} <{from_email}>"
    else:
        display_from = from_email or from_raw
    linkedin_mode = _is_linkedin_sender(from_email)

    payload = message.get("payload", {})
    if not isinstance(payload, dict):
        payload = {}
    raw_plain = message.get("_raw_plain_body")
    raw_html = message.get("_raw_html_body")
    plain_body_raw = raw_plain if isinstance(raw_plain, str) and raw_plain.strip() else _extract_text_plain(payload)
    html_body = raw_html if isinstance(raw_html, str) and raw_html.strip() else _extract_mime_body(payload, "text/html")
    html_as_text_raw = _html_to_text_preserve_links(html_body) if html_body else ""
    if plain_body_raw and html_as_text_raw:
        selected = (
            html_as_text_raw
            if _prefer_html_over_plain(plain_body_raw, html_as_text_raw, linkedin_mode=linkedin_mode)
            else plain_body_raw
        )
    elif plain_body_raw:
        selected = plain_body_raw
    elif html_as_text_raw:
        selected = html_as_text_raw
    else:
        selected = _extract_any_body(payload) or ""
    body = _strip_footer_sections(selected, linkedin_mode=linkedin_mode) if selected else ""
    if strip_history:
        body = _strip_quoted_history(body)
    if trim_body:
        body = _trim_body(body)
    snippet = str(message.get("snippet", "")).strip()
    if _should_prefer_snippet(body, snippet, linkedin_mode=linkedin_mode):
        body = snippet

    return {
        "message_id": str(message.get("id", "")),
        "thread_id": str(message.get("threadId", "")),
        "from": display_from,
        "from_email": from_email.lower(),
        "to": headers.get("to", ""),
        "cc": headers.get("cc", ""),
        "bcc": headers.get("bcc", ""),
        "subject": headers.get("subject", ""),
        "date": _to_local_date(headers.get("date", ""), utc_offset),
        "body": body.strip() or snippet,
        "snippet": snippet,
    }


def _apply_color(block: str, is_from_me: bool) -> str:
    if os.getenv("NO_COLOR"):
        return block
    color = ANSI_GRAY if is_from_me else ANSI_WHITE
    return f"{color}{block}{ANSI_RESET}"


def _apply_gray(block: str) -> str:
    if os.getenv("NO_COLOR"):
        return block
    return f"{ANSI_GRAY}{block}{ANSI_RESET}"


def render_messages_table(
    messages: list[dict[str, Any]], my_email: str, utc_offset: str = "+05:30"
) -> str:
    if not messages:
        return "No messages found."

    my_email_normalized = my_email.strip().lower()
    sections: list[str] = []
    for i, msg in enumerate(messages, start=1):
        row = summarize_message(msg, utc_offset=utc_offset)
        prefix = f"[{i}]"
        header = prefix + ("-" * max(1, 79 - len(prefix)))
        lines = [
            header,
            f"message_id: {row['message_id']}",
            f"thread_id : {row['thread_id']}",
            f"date      : {row['date']}",
            f"from      : {row['from']}",
            f"subject   : {row['subject']}",
        ]
        section = "\n".join(lines)
        sections.append(_apply_color(section, row["from_email"] == my_email_normalized))

    return "\n".join(sections)


def render_message_open(
    message: dict[str, Any], my_email: str, utc_offset: str = "+05:30"
) -> str:
    row = summarize_message(message, trim_body=False, utc_offset=utc_offset, strip_history=True)
    header_lines = [
        f"message_id: {row['message_id']}",
        f"thread_id : {row['thread_id']}",
        f"date      : {row['date']}",
        f"from      : {row['from']}",
        f"to        : {row['to']}",
    ]
    if row["cc"].strip():
        header_lines.append(f"cc        : {row['cc']}")
    if row["bcc"].strip():
        header_lines.append(f"bcc       : {row['bcc']}")
    header_lines.extend(
        [
            f"subject   : {row['subject']}",
            "body:",
        ]
    )
    header_block = _apply_gray("\n".join(header_lines))
    body_block = _apply_color(row["body"], row["from_email"] == my_email.strip().lower())
    return "\n".join([header_block, "", body_block, ""])
