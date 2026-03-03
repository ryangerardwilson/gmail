from __future__ import annotations

import base64
from email.message import EmailMessage
from email.utils import getaddresses
from email.utils import parseaddr
from email.utils import parsedate_to_datetime
from typing import Any

from .errors import ApiError


def _encode_message(message: EmailMessage) -> str:
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
    return raw


def _headers_to_map(message: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in message.get("payload", {}).get("headers", []):
        name = item.get("name")
        value = item.get("value")
        if isinstance(name, str) and isinstance(value, str):
            out[name.lower()] = value
    return out


def _reply_subject(subject: str) -> str:
    if subject.lower().startswith("re:"):
        return subject
    return f"Re: {subject}" if subject else "Re:"


def _extract_reply_recipients(
    headers: dict[str, str], from_email: str, reply_all: bool
) -> tuple[str, list[str]]:
    my_email = from_email.strip().lower()
    to_candidates = getaddresses([headers.get("reply-to") or headers.get("from", "")])
    to_email = ""
    for _, candidate in to_candidates:
        candidate_clean = candidate.strip()
        if candidate_clean and candidate_clean.lower() != my_email:
            to_email = candidate_clean
            break
    if not to_email:
        return "", []

    if not reply_all:
        return to_email, []

    cc_seen: set[str] = set()
    cc_recipients: list[str] = []
    for _, candidate in getaddresses([headers.get("cc", "")]):
        candidate_clean = candidate.strip()
        candidate_key = candidate_clean.lower()
        if (
            not candidate_clean
            or candidate_key == my_email
            or candidate_key == to_email.lower()
            or candidate_key in cc_seen
        ):
            continue
        cc_seen.add(candidate_key)
        cc_recipients.append(candidate_clean)

    return to_email, cc_recipients


def _build_reply_payload(
    original: dict[str, Any],
    from_email: str,
    body: str,
    source_label: str,
    reply_all: bool,
) -> dict[str, Any]:
    headers = _headers_to_map(original)
    to_email, cc_recipients = _extract_reply_recipients(headers, from_email, reply_all)
    if not to_email:
        raise ApiError(f"Could not determine recipient for reply to {source_label}")

    subject = _reply_subject(headers.get("subject", ""))
    source_message_id = headers.get("message-id", "")
    references = headers.get("references", "").strip()
    if source_message_id:
        references = f"{references} {source_message_id}".strip()

    reply = EmailMessage()
    reply["To"] = to_email
    reply["From"] = from_email
    reply["Subject"] = subject
    if cc_recipients:
        reply["Cc"] = ", ".join(cc_recipients)
    if source_message_id:
        reply["In-Reply-To"] = source_message_id
    if references:
        reply["References"] = references
    reply.set_content(body)

    return {
        "raw": _encode_message(reply),
        "threadId": original.get("threadId"),
    }


def send_email(service, from_email: str, to_email: str, subject: str, body: str) -> dict[str, Any]:
    msg = EmailMessage()
    msg["To"] = to_email
    msg["From"] = from_email
    msg["Subject"] = subject
    msg.set_content(body)

    payload = {"raw": _encode_message(msg)}

    try:
        return service.users().messages().send(userId="me", body=payload).execute()
    except Exception as exc:  # pragma: no cover - network/runtime dependent
        raise ApiError(f"Gmail send failed: {exc}") from exc


def list_messages(service, gmail_query: str, max_results: int) -> list[dict[str, Any]]:
    try:
        response = (
            service.users()
            .messages()
            .list(userId="me", q=gmail_query, maxResults=max_results)
            .execute()
        )
    except Exception as exc:  # pragma: no cover
        raise ApiError(f"Gmail search failed: {exc}") from exc

    ids = response.get("messages", [])
    results: list[dict[str, Any]] = []
    for item in ids:
        message_id = item.get("id")
        if not isinstance(message_id, str):
            continue
        try:
            details = (
                service.users()
                .messages()
                .get(
                    userId="me",
                    id=message_id,
                    format="full",
                )
                .execute()
            )
        except Exception as exc:  # pragma: no cover
            raise ApiError(f"Failed to fetch message details for {message_id}: {exc}") from exc
        results.append(details)

    return results


def get_thread_messages(service, thread_id: str) -> list[dict[str, Any]]:
    try:
        response = (
            service.users()
            .threads()
            .get(userId="me", id=thread_id, format="full")
            .execute()
        )
    except Exception as exc:  # pragma: no cover
        raise ApiError(f"Failed to fetch thread '{thread_id}': {exc}") from exc

    messages = response.get("messages", [])
    if not isinstance(messages, list):
        return []

    def _header_date_key(message: dict[str, Any]) -> int:
        headers = _headers_to_map(message)
        raw_date = headers.get("date", "")
        if not raw_date:
            return 0
        try:
            dt = parsedate_to_datetime(raw_date)
            return int(dt.timestamp() * 1000)
        except Exception:
            return 0

    def _internal_date_key(message: dict[str, Any]) -> int:
        raw = message.get("internalDate", "0")
        try:
            return int(raw)
        except (TypeError, ValueError):
            return _header_date_key(message)

    # Ascending order: oldest first, newest last.
    return sorted(messages, key=_internal_date_key)


def reply_to_message(
    service,
    from_email: str,
    message_id: str,
    body: str,
    reply_all: bool = False,
) -> dict[str, Any]:
    try:
        original = (
            service.users()
            .messages()
            .get(
                userId="me",
                id=message_id,
                format="metadata",
                metadataHeaders=["From", "Reply-To", "To", "Cc", "Subject", "Message-ID", "References"],
            )
            .execute()
        )
    except Exception as exc:  # pragma: no cover
        raise ApiError(f"Failed to load message '{message_id}' for reply: {exc}") from exc

    payload = _build_reply_payload(
        original,
        from_email,
        body,
        source_label=f"message '{message_id}'",
        reply_all=reply_all,
    )

    try:
        return service.users().messages().send(userId="me", body=payload).execute()
    except Exception as exc:  # pragma: no cover
        raise ApiError(f"Gmail reply failed: {exc}") from exc


def reply_to_thread(
    service,
    from_email: str,
    thread_id: str,
    body: str,
    reply_all: bool = False,
) -> dict[str, Any]:
    try:
        thread = (
            service.users()
            .threads()
            .get(
                userId="me",
                id=thread_id,
                format="metadata",
                metadataHeaders=["From", "Reply-To", "To", "Cc", "Subject", "Message-ID", "References"],
            )
            .execute()
        )
    except Exception as exc:  # pragma: no cover
        raise ApiError(f"Failed to load thread '{thread_id}' for reply: {exc}") from exc

    messages = thread.get("messages", [])
    if not isinstance(messages, list) or not messages:
        raise ApiError(f"Thread '{thread_id}' has no messages")

    my_email = from_email.strip().lower()
    anchor: dict[str, Any] | None = None
    for candidate in reversed(messages):
        headers = _headers_to_map(candidate)
        sender = parseaddr(headers.get("from", ""))[1].strip().lower()
        if sender and sender != my_email:
            anchor = candidate
            break
    if anchor is None:
        anchor = messages[-1]

    payload = _build_reply_payload(
        anchor,
        from_email,
        body,
        source_label=f"thread '{thread_id}'",
        reply_all=reply_all,
    )
    payload["threadId"] = thread_id

    try:
        return service.users().messages().send(userId="me", body=payload).execute()
    except Exception as exc:  # pragma: no cover
        raise ApiError(f"Gmail thread reply failed: {exc}") from exc
