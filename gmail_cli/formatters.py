from __future__ import annotations

import base64
from datetime import timedelta, timezone
import os
import re
from email.utils import parseaddr
from email.utils import parsedate_to_datetime
from typing import Any
ANSI_RESET = "\033[0m"
ANSI_GRAY = "\033[38;5;245m"
ANSI_WHITE = "\033[97m"


def _header_map(message: dict[str, Any]) -> dict[str, str]:
    headers = message.get("payload", {}).get("headers", [])
    result: dict[str, str] = {}
    for item in headers:
        name = item.get("name")
        value = item.get("value")
        if isinstance(name, str) and isinstance(value, str):
            result[name.lower()] = value
    return result


def _decode_base64_url(data: str) -> str:
    pad = "=" * ((4 - len(data) % 4) % 4)
    decoded = base64.urlsafe_b64decode(data + pad)
    return decoded.decode("utf-8", errors="replace")


def _extract_text_plain(payload: dict[str, Any]) -> str | None:
    mime_type = payload.get("mimeType")
    body_data = payload.get("body", {}).get("data")
    if mime_type == "text/plain" and isinstance(body_data, str):
        return _decode_base64_url(body_data)

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
        return _decode_base64_url(body_data)

    parts = payload.get("parts", [])
    if isinstance(parts, list):
        for part in parts:
            if not isinstance(part, dict):
                continue
            text = _extract_any_body(part)
            if text:
                return text
    return ""


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
    message: dict[str, Any], trim_body: bool = True, utc_offset: str = "+05:30"
) -> dict[str, str]:
    headers = _header_map(message)
    from_raw = headers.get("from", "")
    from_name, from_email = parseaddr(from_raw)
    if from_name and from_email:
        display_from = f"{from_name} <{from_email}>"
    else:
        display_from = from_email or from_raw

    payload = message.get("payload", {})
    if not isinstance(payload, dict):
        payload = {}
    body = _extract_text_plain(payload) or _extract_any_body(payload) or ""
    if trim_body:
        body = _strip_quoted_history(body)
        body = _trim_body(body)

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
        "body": body.strip() or str(message.get("snippet", "")).strip(),
        "snippet": str(message.get("snippet", "")).strip(),
    }


def _apply_color(block: str, is_from_me: bool) -> str:
    if os.getenv("NO_COLOR"):
        return block
    color = ANSI_GRAY if is_from_me else ANSI_WHITE
    return f"{color}{block}{ANSI_RESET}"


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
    row = summarize_message(message, trim_body=False, utc_offset=utc_offset)
    lines = [
        f"message_id: {row['message_id']}",
        f"thread_id : {row['thread_id']}",
        f"date      : {row['date']}",
        f"from      : {row['from']}",
        f"to        : {row['to']}",
    ]
    if row["cc"].strip():
        lines.append(f"cc        : {row['cc']}")
    if row["bcc"].strip():
        lines.append(f"bcc       : {row['bcc']}")
    lines.extend(
        [
            f"subject   : {row['subject']}",
            "body:",
            "",
            row["body"],
        ]
    )
    return _apply_color("\n".join(lines), row["from_email"] == my_email.strip().lower())
