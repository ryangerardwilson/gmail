from __future__ import annotations

import base64
from collections import defaultdict
from email import message_from_bytes
import io
import mimetypes
from email.message import EmailMessage
from email.message import Message
from email.utils import getaddresses
from email.utils import parseaddr
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
import zipfile

from .errors import ApiError
_TEXT_MIME_TYPES = {"text/plain", "text/html"}


def _encode_message(message: EmailMessage) -> str:
    raw = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
    return raw


def _decode_base64_url(data: str) -> bytes:
    pad = "=" * ((4 - len(data) % 4) % 4)
    return base64.urlsafe_b64decode(data + pad)


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


def _normalize_recipients(
    recipients: list[str], my_email: str, exclude: set[str] | None = None
) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    denied = {my_email.lower()}
    if exclude:
        denied.update(value.lower() for value in exclude)

    for _, candidate in getaddresses(recipients):
        candidate_clean = candidate.strip()
        candidate_key = candidate_clean.lower()
        if not candidate_clean or candidate_key in denied or candidate_key in seen:
            continue
        seen.add(candidate_key)
        out.append(candidate_clean)
    return out


def _path_to_attachment(path: Path) -> tuple[str, bytes, str, str]:
    if path.is_file():
        data = path.read_bytes()
        mime_type, _ = mimetypes.guess_type(path.name)
        if mime_type:
            maintype, subtype = mime_type.split("/", 1)
        else:
            maintype, subtype = "application", "octet-stream"
        return path.name, data, maintype, subtype

    if path.is_dir():
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            for child in sorted(path.rglob("*")):
                if child.is_file():
                    zf.write(child, arcname=child.relative_to(path))
        filename = f"{path.name or 'archive'}.zip"
        return filename, archive.getvalue(), "application", "zip"

    raise ApiError(f"Attachment path is neither file nor directory: {path}")


def _attach_files(msg: EmailMessage, attachment_paths: list[Path]) -> None:
    for path in attachment_paths:
        filename, data, maintype, subtype = _path_to_attachment(path)
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=filename)


def _build_reply_payload(
    original: dict[str, Any],
    from_email: str,
    body: str,
    source_label: str,
    reply_all: bool,
    cc_emails: list[str],
    bcc_emails: list[str],
    attachment_paths: list[Path],
) -> dict[str, Any]:
    headers = _headers_to_map(original)
    to_email, inherited_cc = _extract_reply_recipients(headers, from_email, reply_all)
    if not to_email:
        raise ApiError(f"Could not determine recipient for reply to {source_label}")
    my_email = from_email.strip().lower()
    cc_recipients = _normalize_recipients(
        inherited_cc + cc_emails,
        my_email=my_email,
        exclude={to_email},
    )
    bcc_recipients = _normalize_recipients(
        bcc_emails,
        my_email=my_email,
        exclude={to_email, *cc_recipients},
    )

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
    if bcc_recipients:
        reply["Bcc"] = ", ".join(bcc_recipients)
    if source_message_id:
        reply["In-Reply-To"] = source_message_id
    if references:
        reply["References"] = references
    reply.set_content(body)
    _attach_files(reply, attachment_paths)

    return {
        "raw": _encode_message(reply),
        "threadId": original.get("threadId"),
    }


def _append_signature_once(body: str, signature: str | None) -> str:
    body_clean = body.rstrip()
    if not signature:
        return body_clean
    sig_block = f"-- \n{signature.strip()}"
    if body_clean.endswith(sig_block):
        return body_clean
    return f"{body_clean}\n\n{sig_block}"


def send_email(
    service,
    from_email: str,
    to_email: str,
    subject: str,
    body: str,
    cc_emails: list[str] | None = None,
    bcc_emails: list[str] | None = None,
    attachment_paths: list[Path] | None = None,
) -> dict[str, Any]:
    cc_emails = cc_emails or []
    bcc_emails = bcc_emails or []
    attachment_paths = attachment_paths or []
    my_email = from_email.strip().lower()
    to_clean = parseaddr(to_email)[1].strip()
    cc_recipients = _normalize_recipients(cc_emails, my_email=my_email, exclude={to_clean})
    bcc_recipients = _normalize_recipients(
        bcc_emails, my_email=my_email, exclude={to_clean, *cc_recipients}
    )

    msg = EmailMessage()
    msg["To"] = to_email
    msg["From"] = from_email
    msg["Subject"] = subject
    if cc_recipients:
        msg["Cc"] = ", ".join(cc_recipients)
    if bcc_recipients:
        msg["Bcc"] = ", ".join(bcc_recipients)
    msg.set_content(body)
    _attach_files(msg, attachment_paths)

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


def get_message(
    service,
    message_id: str,
    format_type: str = "full",
    metadata_headers: list[str] | None = None,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {"userId": "me", "id": message_id, "format": format_type}
    if metadata_headers:
        kwargs["metadataHeaders"] = metadata_headers
    try:
        return service.users().messages().get(**kwargs).execute()
    except Exception as exc:  # pragma: no cover
        raise ApiError(f"Failed to fetch message '{message_id}': {exc}") from exc


def _hydrate_text_parts_payload(service, message_id: str, payload: dict[str, Any]) -> None:
    mime_type = str(payload.get("mimeType", "")).strip().lower()
    body = payload.get("body", {})
    if not isinstance(body, dict):
        body = {}
        payload["body"] = body

    if mime_type in _TEXT_MIME_TYPES:
        has_data = isinstance(body.get("data"), str)
        attachment_id = body.get("attachmentId")
        if not has_data and isinstance(attachment_id, str):
            try:
                response = (
                    service.users()
                    .messages()
                    .attachments()
                    .get(userId="me", messageId=message_id, id=attachment_id)
                    .execute()
                )
            except Exception:
                response = {}
            raw_data = response.get("data")
            if isinstance(raw_data, str):
                body["data"] = raw_data

    parts = payload.get("parts", [])
    if not isinstance(parts, list):
        return
    for part in parts:
        if isinstance(part, dict):
            _hydrate_text_parts_payload(service, message_id, part)


def hydrate_message_text_bodies(service, message: dict[str, Any]) -> dict[str, Any]:
    message_id = str(message.get("id", "")).strip()
    payload = message.get("payload", {})
    if not message_id or not isinstance(payload, dict):
        return message
    _hydrate_text_parts_payload(service, message_id, payload)
    return message


def _extract_best_text_parts_from_raw(raw_bytes: bytes) -> tuple[str | None, str | None]:
    msg: Message = message_from_bytes(raw_bytes)
    plain_candidates: list[str] = []
    html_candidates: list[str] = []

    for part in msg.walk():
        content_type = part.get_content_type().lower()
        if content_type not in {"text/plain", "text/html"}:
            continue
        disposition = (part.get_content_disposition() or "").lower()
        if disposition == "attachment":
            continue
        payload = part.get_payload(decode=True)
        if payload is None:
            continue
        charset = part.get_content_charset() or "utf-8"
        try:
            text = payload.decode(charset, errors="replace")
        except LookupError:
            text = payload.decode("utf-8", errors="replace")
        if not text.strip():
            continue
        if content_type == "text/plain":
            plain_candidates.append(text)
        else:
            html_candidates.append(text)

    plain = max(plain_candidates, key=len) if plain_candidates else None
    html = max(html_candidates, key=len) if html_candidates else None
    return plain, html


def hydrate_message_text_from_raw(service, message: dict[str, Any]) -> dict[str, Any]:
    message_id = str(message.get("id", "")).strip()
    if not message_id:
        return message
    try:
        response = service.users().messages().get(userId="me", id=message_id, format="raw").execute()
    except Exception:
        return message

    raw = response.get("raw")
    if not isinstance(raw, str) or not raw:
        return message
    try:
        raw_bytes = _decode_base64_url(raw)
    except Exception:
        return message
    plain, html = _extract_best_text_parts_from_raw(raw_bytes)
    if plain:
        message["_raw_plain_body"] = plain
    if html:
        message["_raw_html_body"] = html
    return message


def _attachment_parts(payload: dict[str, Any]) -> list[tuple[str, str | None, str | None]]:
    out: list[tuple[str, str | None, str | None]] = []
    filename = str(payload.get("filename", "")).strip()
    body = payload.get("body", {})
    if not isinstance(body, dict):
        body = {}
    attachment_id = body.get("attachmentId")
    inline_data = body.get("data")
    if filename and (isinstance(attachment_id, str) or isinstance(inline_data, str)):
        out.append(
            (
                Path(filename).name or "attachment",
                attachment_id if isinstance(attachment_id, str) else None,
                inline_data if isinstance(inline_data, str) else None,
            )
        )

    parts = payload.get("parts", [])
    if isinstance(parts, list):
        for part in parts:
            if isinstance(part, dict):
                out.extend(_attachment_parts(part))
    return out


def _unique_attachment_path(target_dir: Path, filename: str) -> Path:
    clean_name = Path(filename).name or "attachment"
    candidate = target_dir / clean_name
    if not candidate.exists():
        return candidate

    stem = Path(clean_name).stem or "attachment"
    suffix = Path(clean_name).suffix
    index = 1
    while True:
        candidate = target_dir / f"{stem}_{index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def download_message_attachments(
    service,
    message: dict[str, Any],
    target_dir: Path,
) -> list[Path]:
    message_id = str(message.get("id", "")).strip()
    if not message_id:
        return []
    payload = message.get("payload", {})
    if not isinstance(payload, dict):
        return []

    attachment_specs = _attachment_parts(payload)
    if not attachment_specs:
        return []

    target_dir.mkdir(parents=True, exist_ok=True)
    downloaded: list[Path] = []
    for filename, attachment_id, inline_data in attachment_specs:
        if attachment_id:
            try:
                response = (
                    service.users()
                    .messages()
                    .attachments()
                    .get(userId="me", messageId=message_id, id=attachment_id)
                    .execute()
                )
            except Exception as exc:  # pragma: no cover
                raise ApiError(
                    f"Failed to download attachment '{filename}' from message '{message_id}': {exc}"
                ) from exc
            raw_data = response.get("data")
            if not isinstance(raw_data, str):
                continue
            data = _decode_base64_url(raw_data)
        elif inline_data:
            data = _decode_base64_url(inline_data)
        else:
            continue

        out_path = _unique_attachment_path(target_dir, filename)
        out_path.write_bytes(data)
        downloaded.append(out_path)
    return downloaded


def list_all_messages(service, gmail_query: str) -> list[dict[str, Any]]:
    try:
        response = (
            service.users()
            .messages()
            .list(userId="me", q=gmail_query, maxResults=500)
            .execute()
        )
    except Exception as exc:  # pragma: no cover
        raise ApiError(f"Gmail search failed: {exc}") from exc

    ids = response.get("messages", [])
    page_token = response.get("nextPageToken")
    while page_token:
        try:
            page = (
                service.users()
                .messages()
                .list(userId="me", q=gmail_query, maxResults=500, pageToken=page_token)
                .execute()
            )
        except Exception as exc:  # pragma: no cover
            raise ApiError(f"Gmail search failed: {exc}") from exc
        ids.extend(page.get("messages", []))
        page_token = page.get("nextPageToken")

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


def list_messages_page(
    service,
    gmail_query: str,
    max_results: int,
    page_token: str | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    try:
        response = (
            service.users()
            .messages()
            .list(userId="me", q=gmail_query, maxResults=max_results, pageToken=page_token)
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

    return results, response.get("nextPageToken")


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
    signature: str | None = None,
    reply_all: bool = False,
    cc_emails: list[str] | None = None,
    bcc_emails: list[str] | None = None,
    attachment_paths: list[Path] | None = None,
) -> dict[str, Any]:
    cc_emails = cc_emails or []
    bcc_emails = bcc_emails or []
    attachment_paths = attachment_paths or []
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
        _append_signature_once(body, signature),
        source_label=f"message '{message_id}'",
        reply_all=reply_all,
        cc_emails=cc_emails,
        bcc_emails=bcc_emails,
        attachment_paths=attachment_paths,
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
    signature: str | None = None,
    reply_all: bool = False,
    cc_emails: list[str] | None = None,
    bcc_emails: list[str] | None = None,
    attachment_paths: list[Path] | None = None,
) -> dict[str, Any]:
    cc_emails = cc_emails or []
    bcc_emails = bcc_emails or []
    attachment_paths = attachment_paths or []
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
        _append_signature_once(body, signature),
        source_label=f"thread '{thread_id}'",
        reply_all=reply_all,
        cc_emails=cc_emails,
        bcc_emails=bcc_emails,
        attachment_paths=attachment_paths,
    )
    payload["threadId"] = thread_id

    try:
        return service.users().messages().send(userId="me", body=payload).execute()
    except Exception as exc:  # pragma: no cover
        raise ApiError(f"Gmail thread reply failed: {exc}") from exc


def list_message_ids(service, gmail_query: str) -> list[str]:
    message_ids: list[str] = []
    page_token: str | None = None
    try:
        while True:
            response = (
                service.users()
                .messages()
                .list(
                    userId="me",
                    q=gmail_query,
                    maxResults=500,
                    pageToken=page_token,
                )
                .execute()
            )
            for item in response.get("messages", []):
                message_id = item.get("id")
                if isinstance(message_id, str) and message_id:
                    message_ids.append(message_id)
            page_token = response.get("nextPageToken")
            if not page_token:
                break
    except Exception as exc:  # pragma: no cover
        raise ApiError(f"Gmail list message ids failed for query '{gmail_query}': {exc}") from exc
    return message_ids


def unread_sender_counts_non_gmail(service, progress_callback=None) -> dict[str, int]:
    # Exact one-pass count across unread non-gmail messages.
    message_ids: list[str] = []
    page_token: str | None = None
    list_query = "is:unread -from:*@gmail.com"
    try:
        while True:
            response = (
                service.users()
                .messages()
                .list(
                    userId="me",
                    q=list_query,
                    maxResults=500,
                    pageToken=page_token,
                )
                .execute()
            )
            for item in response.get("messages", []):
                message_id = item.get("id")
                if isinstance(message_id, str) and message_id:
                    message_ids.append(message_id)
            if progress_callback is not None:
                progress_callback("listed_unread_messages", len(message_ids))
            page_token = response.get("nextPageToken")
            if not page_token:
                break

        counts: dict[str, int] = defaultdict(int)
        total = len(message_ids)
        for index, message_id in enumerate(message_ids, start=1):
            details = (
                service.users()
                .messages()
                .get(
                    userId="me",
                    id=message_id,
                    format="metadata",
                    metadataHeaders=["From"],
                )
                .execute()
            )
            headers = _headers_to_map(details)
            sender = parseaddr(headers.get("from", ""))[1].strip().lower()
            if not sender or sender.endswith("@gmail.com"):
                continue
            counts[sender] += 1
            if progress_callback is not None:
                progress_callback("counted_sender_messages", index, total)
        if progress_callback is not None:
            progress_callback("unique_senders", len(counts))
    except Exception as exc:  # pragma: no cover
        raise ApiError(f"Gmail unread sender scan failed: {exc}") from exc
    return dict(counts)


def batch_delete_messages(service, message_ids: list[str]) -> int:
    if not message_ids:
        return 0
    trashed = 0
    try:
        for i in range(0, len(message_ids), 1000):
            chunk = message_ids[i : i + 1000]
            service.users().messages().batchModify(
                userId="me",
                body={"ids": chunk, "addLabelIds": ["TRASH"]},
            ).execute()
            trashed += len(chunk)
    except Exception as exc:  # pragma: no cover
        raise ApiError(f"Gmail batch trash failed: {exc}") from exc
    return trashed


def batch_mark_messages_read(service, message_ids: list[str]) -> int:
    if not message_ids:
        return 0
    updated = 0
    try:
        for i in range(0, len(message_ids), 1000):
            chunk = message_ids[i : i + 1000]
            service.users().messages().batchModify(
                userId="me",
                body={"ids": chunk, "removeLabelIds": ["UNREAD"]},
            ).execute()
            updated += len(chunk)
    except Exception as exc:  # pragma: no cover
        raise ApiError(f"Gmail batch mark-read failed: {exc}") from exc
    return updated


def mark_message_read(service, message_id: str) -> dict[str, Any]:
    try:
        return (
            service.users()
            .messages()
            .modify(
                userId="me",
                id=message_id,
                body={"removeLabelIds": ["UNREAD"]},
            )
            .execute()
        )
    except Exception as exc:  # pragma: no cover
        raise ApiError(f"Failed to mark message '{message_id}' as read: {exc}") from exc


def mark_message_unread(service, message_id: str) -> dict[str, Any]:
    try:
        return (
            service.users()
            .messages()
            .modify(
                userId="me",
                id=message_id,
                body={"addLabelIds": ["UNREAD"]},
            )
            .execute()
        )
    except Exception as exc:  # pragma: no cover
        raise ApiError(f"Failed to mark message '{message_id}' as unread: {exc}") from exc


def star_message(service, message_id: str) -> dict[str, Any]:
    try:
        return (
            service.users()
            .messages()
            .modify(
                userId="me",
                id=message_id,
                body={"addLabelIds": ["STARRED"], "removeLabelIds": ["UNREAD"]},
            )
            .execute()
        )
    except Exception as exc:  # pragma: no cover
        raise ApiError(f"Failed to star message '{message_id}': {exc}") from exc


def unstar_message(service, message_id: str) -> dict[str, Any]:
    try:
        return (
            service.users()
            .messages()
            .modify(
                userId="me",
                id=message_id,
                body={"removeLabelIds": ["STARRED"]},
            )
            .execute()
        )
    except Exception as exc:  # pragma: no cover
        raise ApiError(f"Failed to unstar message '{message_id}': {exc}") from exc


def delete_message(service, message_id: str) -> None:
    try:
        service.users().messages().trash(userId="me", id=message_id).execute()
    except Exception as exc:  # pragma: no cover
        raise ApiError(f"Failed to trash message '{message_id}': {exc}") from exc
