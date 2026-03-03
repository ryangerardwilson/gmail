from __future__ import annotations

import base64
from email.message import EmailMessage
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
) -> dict[str, Any]:
    try:
        original = (
            service.users()
            .messages()
            .get(
                userId="me",
                id=message_id,
                format="metadata",
                metadataHeaders=["From", "Reply-To", "Subject", "Message-ID", "References"],
            )
            .execute()
        )
    except Exception as exc:  # pragma: no cover
        raise ApiError(f"Failed to load message '{message_id}' for reply: {exc}") from exc

    headers = _headers_to_map(original)

    to_raw = headers.get("reply-to") or headers.get("from", "")
    to_email = parseaddr(to_raw)[1]
    if not to_email:
        raise ApiError(f"Could not determine recipient for reply to message '{message_id}'")

    subject = _reply_subject(headers.get("subject", ""))
    source_message_id = headers.get("message-id", "")
    references = headers.get("references", "").strip()
    if source_message_id:
        references = f"{references} {source_message_id}".strip()

    reply = EmailMessage()
    reply["To"] = to_email
    reply["From"] = from_email
    reply["Subject"] = subject
    if source_message_id:
        reply["In-Reply-To"] = source_message_id
    if references:
        reply["References"] = references
    reply.set_content(body)

    payload: dict[str, Any] = {
        "raw": _encode_message(reply),
        "threadId": original.get("threadId"),
    }

    try:
        return service.users().messages().send(userId="me", body=payload).execute()
    except Exception as exc:  # pragma: no cover
        raise ApiError(f"Gmail reply failed: {exc}") from exc
