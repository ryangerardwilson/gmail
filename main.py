from __future__ import annotations

import argparse
from email.utils import parseaddr
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile

from gmail_cli.auth import authorize_account, build_gmail_service
from gmail_cli.config import (
    get_account,
    load_config,
    normalize_spam_sender_list,
    resolve_config_path,
    upsert_authenticated_account,
    update_account_contacts,
    update_account_spam_excludes,
    update_account_sender_lists,
)
from gmail_cli.errors import ConfigError, GmailCliError, UsageError
from gmail_cli.formatters import render_message_open, render_messages_table
from gmail_cli.formatters import summarize_message
from gmail_cli.gmail_api import (
    batch_mark_messages_read,
    batch_delete_messages,
    delete_message,
    download_message_attachments,
    get_message,
    get_thread_messages,
    hydrate_message_text_from_raw,
    hydrate_message_text_bodies,
    list_all_messages,
    list_messages,
    list_messages_page,
    list_message_ids,
    mark_message_read,
    star_message,
    mark_message_unread,
    unstar_message,
    reply_to_message,
    reply_to_thread,
    send_email,
)
from gmail_cli.query_parser import parse_declarative_query
from gmail_cli.spam_flow import (
    make_identify_decision,
    run_cleanup_for_account,
    run_identify_for_account,
)

__version__ = "0.1.0"
_TRAILING_OPTIONS = {"-cc", "-bcc", "-atch"}
ANSI_RESET = "\033[0m"
ANSI_GRAY = "\033[38;5;245m"


def _muted_text(text: str) -> str:
    if not sys.stdout.isatty() or "NO_COLOR" in os.environ:
        return text
    return f"{ANSI_GRAY}{text}{ANSI_RESET}"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Declarative Gmail CLI",
        usage=(
            "gmail -v\n"
            "gmail -u\n"
            "gmail auth <client_secret_path>\n"
            "gmail <preset> si\n"
            "gmail <preset> sc\n"
            "gmail <preset> sa <spam_email1,spam_email2,...>\n"
            "gmail <preset> se <email1,email2,...>\n"
            "gmail <preset> sa -ur\n"
            "gmail <preset> cn\n"
            "gmail <preset> cn -a <alias> <email>\n"
            "gmail <preset> cn -d <alias>\n"
            "gmail <preset> cn -e\n"
            "gmail <preset> o <message_id>\n"
            "gmail <preset> o -t <thread_id>\n"
            "gmail <preset> mr <message_id>\n"
            "gmail <preset> mra\n"
            "gmail <preset> mur <message_id>\n"
            "gmail <preset> mstr <message_id>\n"
            "gmail <preset> mustr <message_id>\n"
            "gmail <preset> d <message_id>\n"
            "gmail <preset> ms <message_id>\n"
            "gmail <preset> s -e\n"
            "gmail <preset> s <to> <subject> <body> [-cc <emails>] [-bcc <emails>] [-atch <path> [<path> ...]]\n"
            "gmail <preset> ls [-o] <query>\n"
            "gmail <preset> ls [-o] -ur [limit]\n"
            "gmail <preset> ls [-o] -r [limit]\n"
            "gmail <preset> ls [-o] -str [limit]\n"
            "gmail <preset> ls [-o] -ext <limit>\n"
            "gmail <preset> ls [-o] -snt [limit|query]\n"
            "gmail <preset> ls -ura [limit]\n"
            "gmail <preset> ls -ra [limit]\n"
            "gmail <preset> ls [-o] -t <thread_id>\n"
            "gmail <preset> r [-a] [-e] <message_id> <body> [-cc <emails>] [-bcc <emails>] [-atch <path> [<path> ...]]\n"
            "gmail <preset> r [-a] [-e] -t <thread_id> <body> [-cc <emails>] [-bcc <emails>] [-atch <path> [<path> ...]]"
        ),
    )
    parser.add_argument(
        "-v",
        action="version",
        version=__version__,
        help="Show version and exit.",
    )
    parser.add_argument(
        "-u",
        dest="upgrade",
        action="store_true",
        help="Upgrade to latest release using install.sh.",
    )
    parser.add_argument(
        "preset", nargs="?", help="Account preset key from config.json, e.g. 1"
    )
    parser.add_argument("command", nargs="?", help="Command: s | -s | ls | r | o | mr | mra | mur | mstr | mustr | d | ms | si | sc | sa | se | cn")
    parser.add_argument("params", nargs=argparse.REMAINDER, help="Command parameters")
    return parser


def _print_usage_guide(show_examples: bool = True, show_usage: bool = True) -> None:
    lines: list[str] = []
    if show_usage:
        lines.extend(
            [
                "",
                "  gmail -h",
                "  gmail -v",
                "  gmail -u",
                "  gmail auth <client_secret_path>",
                "  gmail <preset> si",
                "  gmail <preset> sc",
                "  gmail <preset> sa <spam_email1,spam_email2,...>",
                "  gmail <preset> se <email1,email2,...>",
                "  gmail <preset> sa -ur",
                "  gmail <preset> cn",
                "  gmail <preset> cn -a <alias> <email>",
                "  gmail <preset> cn -d <alias>",
                "  gmail <preset> cn -e",
                "  gmail <preset> o <message_id>",
                "  gmail <preset> o -t <thread_id>",
                "  gmail <preset> mr <message_id>",
                "  gmail <preset> mra",
                "  gmail <preset> mur <message_id>",
                "  gmail <preset> mstr <message_id>",
                "  gmail <preset> mustr <message_id>",
                "  gmail <preset> d <message_id>",
                "  gmail <preset> ms <message_id>",
                "  gmail <preset> s -e",
                "  gmail <preset> s <to> <subject> <body> [-cc <emails>] [-bcc <emails>] [-atch <path> [<path> ...]]",
                "  gmail <preset> ls [-o] <query>",
                "  gmail <preset> ls [-o] -ur [limit]",
                "  gmail <preset> ls [-o] -r [limit]",
                "  gmail <preset> ls [-o] -str [limit]",
                "  gmail <preset> ls [-o] -ext <limit>",
                "  gmail <preset> ls [-o] -snt [limit|query]",
                "  gmail <preset> ls -ura [limit]",
                "  gmail <preset> ls -ra [limit]",
                "  gmail <preset> ls [-o] -t <thread_id>",
                "  gmail <preset> r [-a] [-e] <message_id> <body> [-cc <emails>] [-bcc <emails>] [-atch <path> [<path> ...]]",
                "  gmail <preset> r [-a] [-e] -t <thread_id> <body> [-cc <emails>] [-bcc <emails>] [-atch <path> [<path> ...]]"
            ]
        )
    if show_examples:
        lines.extend(
            [
                "",
                "  # Send email",
                "  gmail 1 s -e",
                "  gmail 1 s \"xyz@example.com\" \"Hello\" \"Body\"",
                "  gmail 1 s \"xyz@example.com\" \"Hello\" \"Body\" -cc \"cc1@example.com,cc2@example.com\" -bcc \"audit@example.com\"",
                "  gmail 1 s \"xyz@example.com\" \"Hello\" \"Body\" -atch \"/tmp/notes.txt\"",
                "  gmail 1 s \"xyz@example.com\" \"Hello\" \"Body\" -atch \"/tmp/notes.txt\" \"/tmp/project_dir\"",
                "",
                "  # List and audit messages",
                "  gmail 1 ls \"contains jake limit 1\"",
                "  gmail 1 ls \"from xyz limit 5\"",
                "  gmail 1 ls -ur",
                "  gmail 1 ls -ur 1",
                "  gmail 1 ls -r",
                "  gmail 1 ls -r 1",
                "  gmail 1 ls -str",
                "  gmail 1 ls -str 5",
                "  gmail 1 ls -ext 10",
                "  gmail 1 ls -snt 10",
                "  gmail 1 ls -snt \"silvia\"",
                "  gmail 1 ls -o \"from xyz limit 1\"",
                "  gmail 1 ls -o -ur 1",
                "  # Audit unread emails",
                "  gmail 1 ls -ura 10",
                "  # Audit read emails",
                "  gmail 1 ls -ra 10",
                "  gmail 1 ls \"to silvia limit 1\"",
                "  gmail 1 ls -t \"19ca756c06a7ebcd\"",
                "",
                "  # Single-message utilities",
                "  gmail 1 o \"19caef2cd6494116\"",
                "  gmail 1 o -t \"19ca756c06a7ebcd\"",
                "  gmail 1 mr \"19caef2cd6494116\"",
                "  gmail 1 mra",
                "  gmail 1 mur \"19caef2cd6494116\"",
                "  gmail 1 mstr \"19caef2cd6494116\"",
                "  gmail 1 mustr \"19caef2cd6494116\"",
                "  gmail 1 d \"19caef2cd6494116\"",
                "  gmail 1 ms \"19caef2cd6494116\"",
                "",
                "  # Reply",
                "  gmail 1 r \"19caef2cd6494116\" \"Thanks for the update.\"",
                "  gmail 1 r -e \"19caef2cd6494116\"",
                "  gmail 1 r -a \"19caef2cd6494116\" \"Thanks all.\"",
                "  gmail 1 r \"19caef2cd6494116\" \"Adding context.\" -cc \"manager@example.com\"",
                "  gmail 1 r \"19caef2cd6494116\" \"Please review.\" -atch \"/tmp/project_dir\"",
                "  gmail 1 r -a \"19caef2cd6494116\" \"Please review.\" -atch \"/tmp/notes.txt\" \"/tmp/project_dir\"",
                "  gmail 1 r -t \"19ca756c06a7ebcd\" \"Following up on this thread.\"",
                "  gmail 1 r -t -a \"19ca756c06a7ebcd\" \"Thanks everyone.\"",
                "",
                "  # Spam flow",
                "  gmail 1 si",
                "  gmail 1 sc",
                "  gmail 1 sa \"spam1@example.com,spam2@example.com\"",
                "  gmail 1 sa \"@domain1.com,@domain2.com\"",
                "  gmail 1 se \"trusted1@example.com,trusted2@example.com\"",
                "  gmail 1 se \"@trusted-domain.com\"",
                "  gmail 1 sa -ur",
                "",
                "  # Contacts",
                "  gmail 1 cn",
                "  gmail 1 cn -a \"silvia\" \"xyz@hbc.com\"",
                "  gmail 1 cn -d \"silvia\"",
                "  gmail 1 cn -e",
                ""
            ]
        )
    print(_muted_text("\n".join(lines)))


def _parse_recipient_csv(value: str, flag: str) -> list[str]:
    parsed = [item.strip() for item in value.split(",") if item.strip()]
    if not parsed:
        raise UsageError(f"{flag} requires at least one email address")
    return parsed


def _parse_recipient_csv_optional(value: str) -> list[str]:
    stripped = value.strip()
    if not stripped:
        return []
    return [item.strip() for item in stripped.split(",") if item.strip()]


def _strip_outer_quotes(value: str) -> str:
    stripped = value.strip()
    if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in {'"', "'"}:
        return stripped[1:-1].strip()
    return stripped


def _parse_attachment_csv_optional(value: str) -> list[Path]:
    stripped = _strip_outer_quotes(value)
    if not stripped:
        return []
    raw_items = [item.strip() for item in stripped.split(",") if item.strip()]
    if not raw_items:
        return []
    return [_parse_attachment_path(item) for item in raw_items]


def _compose_editor_template(from_email: str, signature: str, include_to_subject: bool) -> str:
    header_lines = [f"From: {from_email}"]
    if include_to_subject:
        header_lines.extend(["To:", "Subject:"])
    header_lines.extend(["CC:", "BCC:", 'Attachments: ""', "Body:", ""])
    return "\n".join(
        header_lines + [f"-- \n{signature.strip()}", ""]
    )


def _parse_editor_template(
    content: str,
) -> tuple[str, str, str, list[str], list[str], list[Path]]:
    lines = content.splitlines()
    to_email = ""
    subject = ""
    cc_emails: list[str] = []
    bcc_emails: list[str] = []
    attachment_paths: list[Path] = []
    body_lines: list[str] = []
    in_body = False

    for line in lines:
        if in_body:
            body_lines.append(line)
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key_norm = key.strip().lower()
        value = value.strip()
        if key_norm == "body":
            in_body = True
            if value:
                body_lines.append(value)
            continue
        if key_norm == "to":
            to_email = value
        elif key_norm == "subject":
            subject = value
        elif key_norm == "cc":
            cc_emails = _parse_recipient_csv_optional(value)
        elif key_norm == "bcc":
            bcc_emails = _parse_recipient_csv_optional(value)
        elif key_norm == "attachments":
            attachment_paths = _parse_attachment_csv_optional(value)

    body = "\n".join(body_lines).strip()
    return to_email, subject, body, cc_emails, bcc_emails, attachment_paths


def _open_editor_template(
    from_email: str,
    signature: str,
    include_to_subject: bool,
) -> tuple[str, str, str, list[str], list[str], list[Path]]:
    editor_cmd = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vim"
    with tempfile.NamedTemporaryFile(mode="w+", suffix=".txt", delete=False) as tmp:
        tmp_path = Path(tmp.name)
        tmp.write(_compose_editor_template(from_email, signature, include_to_subject))
        tmp.flush()
    try:
        editor_parts = shlex.split(editor_cmd)
        if not editor_parts:
            raise UsageError("Editor command is empty. Set VISUAL or EDITOR.")
        proc = subprocess.run([*editor_parts, str(tmp_path)], check=False)
        if proc.returncode != 0:
            raise UsageError(
                f"Editor exited with code {proc.returncode}. Message not sent."
            )
        content = tmp_path.read_text(encoding="utf-8")
        return _parse_editor_template(content)
    except FileNotFoundError as exc:
        raise UsageError(
            f"Editor not found: {editor_cmd}. Set VISUAL or EDITOR to a valid editor."
        ) from exc
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass


def _parse_attachment_path(value: str) -> Path:
    path = Path(value).expanduser()
    if not path.exists():
        raise UsageError(f"-atch path not found: {path}")
    if not (path.is_file() or path.is_dir()):
        raise UsageError(f"-atch must point to a file or directory: {path}")
    return path


def _resolve_contact(value: str, contacts: dict[str, str]) -> str:
    candidate = value.strip()
    if not candidate:
        return candidate
    if "@" in candidate:
        return candidate
    return contacts.get(candidate.lower(), candidate)


def _resolve_recipient_list(values: list[str], contacts: dict[str, str]) -> list[str]:
    return [_resolve_contact(value, contacts) for value in values]


def _consume_attachment_paths(tokens: list[str], start_index: int) -> tuple[list[Path], int]:
    if start_index >= len(tokens) or tokens[start_index] in _TRAILING_OPTIONS:
        raise UsageError("-atch requires at least one path to a file or directory")

    out: list[Path] = []
    index = start_index
    while index < len(tokens) and tokens[index] not in _TRAILING_OPTIONS:
        out.append(_parse_attachment_path(tokens[index]))
        index += 1
    return out, index


def _parse_send_args(
    params: list[str],
) -> tuple[str, str, str, list[str], list[str], list[Path]]:
    if len(params) < 3:
        raise UsageError(
            "Send requires: <to> <subject> <body> [-cc <emails>] [-bcc <emails>] [-atch <path> [<path> ...]]"
        )

    to_email, subject, body = params[:3]
    trailing = params[3:]
    cc_emails: list[str] = []
    bcc_emails: list[str] = []
    attachment_paths: list[Path] = []
    index = 0
    while index < len(trailing):
        token = trailing[index]
        if token in _TRAILING_OPTIONS:
            if index + 1 >= len(trailing):
                if token == "-atch":
                    raise UsageError("-atch requires a path to a file or directory")
                raise UsageError(f"{token} requires a comma-separated email list")
            if token == "-atch":
                parsed_paths, next_index = _consume_attachment_paths(trailing, index + 1)
                attachment_paths.extend(parsed_paths)
                index = next_index
            else:
                parsed = _parse_recipient_csv(trailing[index + 1], token)
                if token == "-cc":
                    cc_emails.extend(parsed)
                else:
                    bcc_emails.extend(parsed)
                index += 2
            continue
        raise UsageError(
            "Send options must appear at the end. Supported trailing options: -cc, -bcc, -atch"
        )

    return to_email, subject, body, cc_emails, bcc_emails, attachment_paths


def _handle_send(
    service,
    from_email: str,
    params: list[str],
    signature: str,
    contacts: dict[str, str],
) -> int:
    editor_mode = False
    if params == ["-e"]:
        editor_mode = True
        to_email, subject, body, cc_emails, bcc_emails, attachment_paths = _open_editor_template(
            from_email, signature, include_to_subject=True
        )
        if not to_email or not subject or not body:
            return 0
    else:
        to_email, subject, body, cc_emails, bcc_emails, attachment_paths = _parse_send_args(params)
    to_email = _resolve_contact(to_email, contacts)
    cc_emails = _resolve_recipient_list(cc_emails, contacts)
    bcc_emails = _resolve_recipient_list(bcc_emails, contacts)
    signed_body = _append_signature(body, signature)
    try:
        response = send_email(
            service,
            from_email,
            to_email,
            subject,
            signed_body,
            cc_emails=cc_emails,
            bcc_emails=bcc_emails,
            attachment_paths=attachment_paths,
        )
    except GmailCliError:
        if editor_mode:
            print("editor_draft_recovery:")
            print(f"to: {to_email}")
            print(f"subject: {subject}")
            print("body:")
            print(body)
        raise
    print(f"sent message_id={response.get('id')} thread_id={response.get('threadId')}")
    return 0


def _parse_optional_limit(flag: str, params: list[str]) -> int | None:
    if len(params) > 2:
        raise UsageError(f"{flag} accepts at most 1 optional param: [limit]")
    if len(params) == 1:
        return None
    max_results: int | None = None
    if len(params) == 2:
        try:
            max_results = int(params[1])
        except ValueError as exc:
            raise UsageError(f"{flag} limit must be a positive integer") from exc
        if max_results <= 0:
            raise UsageError(f"{flag} limit must be > 0")
    return max_results


def _list_with_optional_limit(service, gmail_query: str, max_results: int | None) -> list[dict]:
    if max_results is None:
        return list_all_messages(service, gmail_query)
    return list_messages(service, gmail_query, max_results)


def _is_gmail_sender(sender_email: str) -> bool:
    return sender_email.strip().lower().endswith("@gmail.com")


def _audit_message_batch(
    messages: list[dict],
    spam_senders: list[str],
    spam_set: set[str],
    service,
    utc_offset: str,
) -> tuple[int, int, bool]:
    audited = 0
    trashed = 0
    stopped = False

    for index, message in enumerate(messages, start=1):
        audited += 1
        row = summarize_message(message, utc_offset=utc_offset)
        sender = parseaddr(row.get("from", ""))[1].strip().lower() or row.get("from_email", "").strip().lower()
        message_id = str(message.get("id", ""))
        print(f"\n[{index}/{len(messages)}] message_id={message_id}")
        print(f"from    : {row.get('from', '')}")
        print(f"subject : {row.get('subject', '')}")
        print(f"date    : {row.get('date', '')}")
        body_preview = row.get("body", "").strip() or str(message.get("snippet", ""))
        print("body:")
        print(body_preview)

        while True:
            choice = input("action [s=spam, t=trash only, n=not spam, q=quit]: ").strip().lower()
            if choice not in {"s", "t", "n", "q"}:
                print("Invalid input. Use s, t, n, or q.")
                continue
            break

        if choice == "q":
            audited -= 1
            stopped = True
            break
        if choice == "n":
            continue

        if _is_gmail_sender(sender):
            print("gmail sender protected: message not trashed")
            continue

        delete_message(service, message_id)
        trashed += 1
        if choice == "t":
            print(f"trashed message_id={message_id}")
            continue

        if not sender:
            print("trashed message, but could not parse sender email for spam_senders update")
            continue
        if sender not in spam_set:
            spam_set.add(sender)
            spam_senders.append(sender)
        print(f"trashed message_id={message_id} and added sender to spam_senders: {sender}")

    return audited, trashed, stopped


def _extract_list_open_flag(params: list[str]) -> tuple[bool, list[str]]:
    open_mode = False
    filtered: list[str] = []
    for token in params:
        if token == "-o":
            open_mode = True
            continue
        filtered.append(token)
    return open_mode, filtered


def _print_list_results(
    service,
    messages: list[dict],
    my_email: str,
    utc_offset: str,
    open_mode: bool,
) -> None:
    if not open_mode:
        print(render_messages_table(messages, my_email, utc_offset=utc_offset))
        return
    if not messages:
        print("No messages found.")
        return

    message_ids: list[str] = []
    for index, message in enumerate(messages, start=1):
        message_id = str(message.get("id", "")).strip()
        if message_id:
            message_ids.append(message_id)
        print(f"[{index}/{len(messages)}]")
        print(render_message_open(message, my_email, utc_offset=utc_offset))
        if index < len(messages):
            print("")

    marked_read = batch_mark_messages_read(service, message_ids)
    print(f"ls_opened messages={len(messages)} marked_read={marked_read}")


def _sort_messages_oldest_first(messages: list[dict]) -> list[dict]:
    def _internal_date_key(message: dict) -> int:
        raw = message.get("internalDate", "0")
        try:
            return int(raw)
        except (TypeError, ValueError):
            return 0

    return sorted(messages, key=_internal_date_key)


def _handle_list(
    service,
    params: list[str],
    default_limit: int,
    my_email: str,
    utc_offset: str = "+05:30",
    config_path=None,
    account=None,
) -> int:
    open_mode, filtered_params = _extract_list_open_flag(params)
    if not filtered_params:
        raise UsageError("ls requires a query string, e.g. \"from maanas limit 1\"")
    if filtered_params[0] in {"-ura", "-ra"} and open_mode:
        raise UsageError("ls -o is not supported with -ura or -ra")

    if filtered_params[0] == "-ur":
        max_results = _parse_optional_limit("ls -ur", filtered_params)
        messages = _list_with_optional_limit(service, "is:unread", max_results)
        messages = _sort_messages_oldest_first(messages)
        _print_list_results(service, messages, my_email, utc_offset, open_mode)
        return 0

    if filtered_params[0] == "-r":
        max_results = _parse_optional_limit("ls -r", filtered_params)
        messages = _list_with_optional_limit(service, f"is:read -from:{my_email}", max_results)
        messages = _sort_messages_oldest_first(messages)
        _print_list_results(service, messages, my_email, utc_offset, open_mode)
        return 0

    if filtered_params[0] == "-str":
        max_results = _parse_optional_limit("ls -str", filtered_params)
        messages = _list_with_optional_limit(service, "is:starred", max_results)
        messages = _sort_messages_oldest_first(messages)
        _print_list_results(service, messages, my_email, utc_offset, open_mode)
        return 0

    if filtered_params[0] == "-ext":
        if len(filtered_params) > 2:
            raise UsageError("ls -ext accepts at most 1 optional param: [limit]")
        max_results: int | None = None
        if len(filtered_params) == 2:
            try:
                max_results = int(filtered_params[1])
            except ValueError as exc:
                raise UsageError("ls -ext limit must be a positive integer") from exc
            if max_results <= 0:
                raise UsageError("ls -ext limit must be > 0")
        domain = my_email.split("@", 1)[1].strip().lower() if "@" in my_email else ""
        ext_query = f"-from:{my_email}"
        if domain:
            ext_query += f" -from:*@{domain}"
        messages = _list_with_optional_limit(service, ext_query, max_results)
        messages = _sort_messages_oldest_first(messages)
        _print_list_results(service, messages, my_email, utc_offset, open_mode)
        return 0

    if filtered_params[0] == "-snt":
        if len(filtered_params) == 1:
            messages = list_all_messages(service, "in:sent")
            messages = _sort_messages_oldest_first(messages)
            _print_list_results(service, messages, my_email, utc_offset, open_mode)
            return 0
        if len(filtered_params) == 2:
            try:
                max_results = int(filtered_params[1])
            except ValueError:
                max_results = -1
            if max_results > 0:
                messages = list_messages(service, "in:sent", max_results)
                messages = _sort_messages_oldest_first(messages)
                _print_list_results(service, messages, my_email, utc_offset, open_mode)
                return 0
            if max_results == 0:
                raise UsageError("ls -snt limit must be > 0")

        sent_query = "in:sent " + " ".join(filtered_params[1:])
        parsed = parse_declarative_query(sent_query, default_limit)
        messages = _list_with_optional_limit(service, parsed.gmail_query, parsed.max_results)
        messages = _sort_messages_oldest_first(messages)
        _print_list_results(service, messages, my_email, utc_offset, open_mode)
        return 0

    if filtered_params[0] == "-ura":
        if config_path is None or account is None:
            raise UsageError("Internal error: ls -ura requires account context")
        return _run_audit_mode(
            service, filtered_params, default_limit, config_path, account, "is:unread", "unread", "ura", utc_offset
        )

    if filtered_params[0] == "-ra":
        if config_path is None or account is None:
            raise UsageError("Internal error: ls -ra requires account context")
        return _run_audit_mode(
            service, filtered_params, default_limit, config_path, account, "is:read", "read", "ra", utc_offset
        )

    if filtered_params[0] == "-t":
        if len(filtered_params) != 2:
            raise UsageError("ls -t requires exactly 1 param: <thread_id>")
        thread_id = filtered_params[1]
        messages = get_thread_messages(service, thread_id)
        messages = _sort_messages_oldest_first(messages)
        _print_list_results(service, messages, my_email, utc_offset, open_mode)
        return 0

    query = " ".join(filtered_params)
    parsed = parse_declarative_query(query, default_limit)
    messages = _list_with_optional_limit(service, parsed.gmail_query, parsed.max_results)
    messages = _sort_messages_oldest_first(messages)
    _print_list_results(service, messages, my_email, utc_offset, open_mode)
    return 0


def _run_audit_mode(
    service,
    params,
    default_limit,
    config_path,
    account,
    gmail_query: str,
    mode_label: str,
    mode_flag: str,
    utc_offset: str,
) -> int:
    if config_path is None or account is None:
        raise UsageError("Internal error: audit mode requires account context")

    spam_senders = list(account.spam_senders)
    spam_set = set(spam_senders)
    trashed = 0
    audited = 0

    print(
        "Audit mode: enter 's' for spam (add sender to spam_senders + trash message), "
        "'t' for trash only, 'n' for not spam (leave unchanged), 'q' to stop."
    )

    if len(params) == 1:
        page_token: str | None = None
        batch_index = 0
        while True:
            messages, next_page = list_messages_page(
                service, gmail_query, max_results=10, page_token=page_token
            )
            messages = _sort_messages_oldest_first(messages)
            if not messages:
                if audited == 0:
                    print(f"No {mode_label} messages found.")
                break

            batch_index += 1
            print(f"\nProcessing {mode_label} batch {batch_index} ({len(messages)} messages)")
            audited_delta, trashed_delta, stopped = _audit_message_batch(
                messages, spam_senders, spam_set, service, utc_offset
            )
            audited += audited_delta
            trashed += trashed_delta
            if stopped:
                break
            if not next_page:
                break
            page_token = next_page
    else:
        max_results = _parse_optional_limit(f"ls -{mode_flag}", params)
        if max_results is None:
            max_results = default_limit
        messages = list_messages(service, gmail_query, max_results)
        messages = _sort_messages_oldest_first(messages)
        if not messages:
            print(f"No {mode_label} messages found.")
            return 0
        audited_delta, trashed_delta, _ = _audit_message_batch(
            messages, spam_senders, spam_set, service, utc_offset
        )
        audited += audited_delta
        trashed += trashed_delta

    update_account_sender_lists(
        config_path,
        {account.preset: spam_senders},
    )
    print(f"ls -{mode_flag} complete: audited={audited} trashed={trashed}")
    return 0


def _parse_reply_args(
    params: list[str],
) -> tuple[bool, bool, bool, str, str, list[str], list[str], list[Path]]:
    if not params:
        raise UsageError(
            "Reply requires: [-a] [-e] <message_id> <body> [-cc <emails>] [-bcc <emails>] [-atch <path> [<path> ...]] "
            "or [-a] [-e] -t <thread_id> <body> [-cc <emails>] [-bcc <emails>] [-atch <path> [<path> ...]]"
        )

    flags: set[str] = set()
    index = 0
    while index < len(params):
        token = params[index]
        if token == "--":
            index += 1
            break
        if not token.startswith("-") or token == "-":
            break
        if token in {"-a", "-t", "-e"}:
            flags.add(token[1:])
            index += 1
            continue
        if len(token) > 2 and token.startswith("-") and set(token[1:]).issubset({"a", "t", "e"}):
            raise UsageError("Do not combine reply flags. Use separate flags, e.g. r -t -a or r -a -t.")
        if token in _TRAILING_OPTIONS:
            break
        raise UsageError(
            f"Unknown reply option '{token}'. Supported: -a, -t, -e, -cc, -bcc, -atch"
        )

    remaining = params[index:]
    target_name = "thread_id" if "t" in flags else "message_id"
    use_editor = "e" in flags
    minimum_required = 1 if use_editor else 2
    if len(remaining) < minimum_required:
        raise UsageError(
            f"Reply requires: <{target_name}> {'<body> ' if not use_editor else ''}before trailing options"
        )

    target_id = remaining[0]
    body = ""
    trailing_start = 1
    if not use_editor:
        body = remaining[1]
        trailing_start = 2
    trailing = remaining[trailing_start:]
    cc_emails: list[str] = []
    bcc_emails: list[str] = []
    attachment_paths: list[Path] = []
    tail_index = 0
    while tail_index < len(trailing):
        token = trailing[tail_index]
        if token in _TRAILING_OPTIONS:
            if tail_index + 1 >= len(trailing):
                if token == "-atch":
                    raise UsageError("-atch requires a path to a file or directory")
                raise UsageError(f"{token} requires a comma-separated email list")
            if token == "-atch":
                parsed_paths, next_index = _consume_attachment_paths(trailing, tail_index + 1)
                attachment_paths.extend(parsed_paths)
                tail_index = next_index
            else:
                parsed = _parse_recipient_csv(trailing[tail_index + 1], token)
                if token == "-cc":
                    cc_emails.extend(parsed)
                else:
                    bcc_emails.extend(parsed)
                tail_index += 2
            continue
        raise UsageError(
            "Reply options must appear at the end. Supported trailing options: -cc, -bcc, -atch"
        )

    return "t" in flags, "a" in flags, use_editor, target_id, body, cc_emails, bcc_emails, attachment_paths


def _handle_reply(
    service,
    from_email: str,
    params: list[str],
    signature: str,
    contacts: dict[str, str],
) -> int:
    (
        use_thread,
        reply_all,
        use_editor,
        target_id,
        body,
        cc_emails,
        bcc_emails,
        attachment_paths,
    ) = _parse_reply_args(params)
    if use_editor:
        _, _, body_from_editor, cc_from_editor, bcc_from_editor, attachment_from_editor = _open_editor_template(
            from_email, signature, include_to_subject=False
        )
        if not body_from_editor:
            return 0
        body = body_from_editor
        cc_emails = cc_from_editor + cc_emails
        bcc_emails = bcc_from_editor + bcc_emails
        attachment_paths = attachment_from_editor + attachment_paths
    cc_emails = _resolve_recipient_list(cc_emails, contacts)
    bcc_emails = _resolve_recipient_list(bcc_emails, contacts)
    try:
        if use_thread:
            response = reply_to_thread(
                service,
                from_email,
                target_id,
                body,
                signature=signature,
                reply_all=reply_all,
                cc_emails=cc_emails,
                bcc_emails=bcc_emails,
                attachment_paths=attachment_paths,
            )
        else:
            response = reply_to_message(
                service,
                from_email,
                target_id,
                body,
                signature=signature,
                reply_all=reply_all,
                cc_emails=cc_emails,
                bcc_emails=bcc_emails,
                attachment_paths=attachment_paths,
            )
    except GmailCliError:
        if use_editor:
            print("editor_draft_recovery:")
            if use_thread:
                print(f"thread_id: {target_id}")
            else:
                print(f"message_id: {target_id}")
                print("hint: if this id is a thread id, use: r -e -t <thread_id>")
            print("body:")
            print(body)
        raise
    print(f"replied message_id={response.get('id')} thread_id={response.get('threadId')}")
    return 0


def _merge_unique(existing: list[str], incoming: list[str]) -> list[str]:
    merged = existing + incoming
    return normalize_spam_sender_list(merged)


def _handle_spam_identify(config, account, service) -> int:
    print("scanning unread non-gmail sender sample...")

    def _progress(event: str, value: int, total: int | None = None) -> None:
        if event == "listed_unread_messages":
            print(f"unread non-gmail messages listed: {value}")
        elif event == "counted_sender_messages" and total is not None:
            print(f"counting sender occurrences: {value}/{total}")
        elif event == "unique_senders":
            print(f"unique non-gmail senders found: {value}")

    candidates = run_identify_for_account(service, account, progress_callback=_progress)
    print(f"potential spammers identified: {len(candidates)}")
    if not candidates:
        print("no potential spam senders found")
        return 0

    print("potential spam senders (>5 unread, excluding gmail + preset domain + spam_excludes):")
    for index, item in enumerate(candidates, start=1):
        print(f"  {index}. {item.sender} (unread={item.unread_count})")
    decision = make_identify_decision(candidates)
    if not decision.add_to_spam:
        print("no list updates requested")
        return 0

    print(f"review: add_to_spam={len(decision.add_to_spam)}")
    confirm = input("confirm update config? [y/N]: ").strip().lower()
    if confirm != "y":
        print("skipped by user")
        return 0

    merged_spam = _merge_unique(account.spam_senders, decision.add_to_spam)
    update_account_sender_lists(config.path, {account.preset: merged_spam})
    print(f"updated: +{len(decision.add_to_spam)} spam")
    print(f"si complete: spam_added={len(decision.add_to_spam)}")
    return 0


def _handle_spam_clean(account, service) -> int:
    def _progress(event: str, value: int, total: int | None = None, group_ids: int | None = None, unique_ids: int | None = None) -> None:
        if event == "groups_total":
            print(f"spam sender groups to process: {value}")
        elif event == "group_processed" and total is not None:
            ids_found = group_ids if group_ids is not None else 0
            unique_found = unique_ids if unique_ids is not None else 0
            print(
                f"processed group {value}/{total}: ids_found={ids_found} unique_ids_collected={unique_found}"
            )
        elif event == "trashed_total":
            print(f"trashed so far: {value}")

    result = run_cleanup_for_account(service, account, progress_callback=_progress)
    print(f"trashed_spam={result.trashed_spam}")
    print(f"sc complete: trashed_spam={result.trashed_spam}")
    return 0


def _handle_spam_add(config, account, service, params: list[str]) -> int:
    if len(params) != 1:
        raise UsageError("sa requires exactly 1 param: <spam_email1,spam_email2,...> or -ur")

    if params[0] == "-ur":
        print("sa -ur: scanning unread messages in batches...")
        page_token: str | None = None
        sender_set: set[str] = set()
        message_ids: list[str] = []
        batch_index = 0
        while True:
            messages, next_page = list_messages_page(
                service,
                "is:unread",
                max_results=100,
                page_token=page_token,
            )
            if not messages:
                break
            batch_index += 1
            for message in messages:
                row = summarize_message(message)
                sender = parseaddr(row.get("from", ""))[1].strip().lower() or row.get("from_email", "").strip().lower()
                if sender:
                    sender_set.add(sender)
                message_id = str(message.get("id", "")).strip()
                if message_id:
                    message_ids.append(message_id)
            print(
                f"processed unread batch {batch_index}: senders_collected={len(sender_set)} "
                f"messages_collected={len(message_ids)}"
            )
            if not next_page:
                break
            page_token = next_page

        if not message_ids:
            print("sa -ur complete: no unread messages found")
            return 0

        candidate_senders = normalize_spam_sender_list(sorted(sender_set))
        merged = _merge_unique(account.spam_senders, candidate_senders)
        update_account_sender_lists(config.path, {account.preset: merged})
        trashed = batch_delete_messages(service, message_ids)
        print(
            f"sa -ur complete: added_senders={len(candidate_senders)} "
            f"total_spam_senders={len(merged)} trashed_unread={trashed}"
        )
        return 0

    new_items = normalize_spam_sender_list([item.strip() for item in params[0].split(",")])
    if not new_items:
        raise UsageError("sa requires at least one valid email in comma-separated input")
    merged = _merge_unique(account.spam_senders, new_items)
    update_account_sender_lists(config.path, {account.preset: merged})
    print(f"sa complete: added={len(new_items)} total_spam_senders={len(merged)}")
    return 0


def _handle_spam_exclude(config, account, params: list[str]) -> int:
    if len(params) != 1:
        raise UsageError("se requires exactly 1 param: <email1,email2,...>")
    new_items = [item.strip().lower() for item in params[0].split(",") if item.strip()]
    if not new_items:
        raise UsageError("se requires at least one valid email in comma-separated input")
    merged = sorted(set(account.spam_excludes + new_items))
    update_account_spam_excludes(config.path, account.preset, merged)
    print(f"se complete: added={len(new_items)} total_spam_excludes={len(merged)}")
    return 0


def _handle_mark_read(service, params: list[str]) -> int:
    if len(params) != 1:
        raise UsageError("mr requires exactly 1 param: <message_id>")
    message_id = params[0]
    response = mark_message_read(service, message_id)
    print(f"marked_read message_id={response.get('id')} thread_id={response.get('threadId')}")
    return 0


def _handle_mark_read_all(service, params: list[str]) -> int:
    if params:
        raise UsageError("mra does not accept params. Use: gmail <preset> mra")
    message_ids = list_message_ids(service, "is:unread")
    updated = batch_mark_messages_read(service, message_ids)
    print(f"marked_read_all={updated}")
    return 0


def _handle_open_message(
    service, params: list[str], my_email: str, utc_offset: str = "+05:30"
) -> int:
    if not params:
        raise UsageError("o requires: <message_id> or -t <thread_id>")

    use_thread = False
    index = 0
    if params[0].startswith("-"):
        if params[0] != "-t":
            raise UsageError("o supports only optional flag: -t")
        use_thread = True
        index = 1

    if len(params[index:]) != 1:
        raise UsageError("o requires exactly one id: <message_id> or -t <thread_id>")
    target_id = params[index]

    if not use_thread:
        message = get_message(service, target_id, format_type="full")
        message = hydrate_message_text_bodies(service, message)
        message = hydrate_message_text_from_raw(service, message)
        downloaded = download_message_attachments(service, message, Path.cwd())
        response = mark_message_read(service, target_id)
        print(render_message_open(message, my_email, utc_offset=utc_offset))
        if downloaded:
            print(f"attachments_downloaded={len(downloaded)} cwd={Path.cwd()}")
            for path in downloaded:
                print(f"attachment: {path.name}")
        print(
            f"opened_and_marked_read message_id={response.get('id')} thread_id={response.get('threadId')}"
        )
        return 0

    messages = get_thread_messages(service, target_id)
    if not messages:
        print(f"no messages found in thread_id={target_id}")
        return 0

    all_downloaded: list[Path] = []
    message_ids: list[str] = []
    for idx, message in enumerate(messages, start=1):
        message = hydrate_message_text_bodies(service, message)
        message = hydrate_message_text_from_raw(service, message)
        message_id = str(message.get("id", "")).strip()
        if message_id:
            message_ids.append(message_id)
        all_downloaded.extend(download_message_attachments(service, message, Path.cwd()))
        print(f"[{idx}/{len(messages)}]")
        print(render_message_open(message, my_email, utc_offset=utc_offset))
        if idx < len(messages):
            print("")

    marked_read = batch_mark_messages_read(service, message_ids)
    if all_downloaded:
        print(f"attachments_downloaded={len(all_downloaded)} cwd={Path.cwd()}")
        for path in all_downloaded:
            print(f"attachment: {path.name}")
    print(
        f"opened_thread thread_id={target_id} messages={len(messages)} marked_read={marked_read}"
    )
    return 0


def _handle_mark_unread(service, params: list[str]) -> int:
    if len(params) != 1:
        raise UsageError("mur requires exactly 1 param: <message_id>")
    message_id = params[0]
    response = mark_message_unread(service, message_id)
    print(f"marked_unread message_id={response.get('id')} thread_id={response.get('threadId')}")
    return 0


def _handle_mark_star(service, params: list[str]) -> int:
    if len(params) != 1:
        raise UsageError("mstr requires exactly 1 param: <message_id>")
    message_id = params[0]
    response = star_message(service, message_id)
    print(f"starred message_id={response.get('id')} thread_id={response.get('threadId')}")
    return 0


def _handle_mark_unstar(service, params: list[str]) -> int:
    if len(params) != 1:
        raise UsageError("mustr requires exactly 1 param: <message_id>")
    message_id = params[0]
    response = unstar_message(service, message_id)
    print(f"unstarred message_id={response.get('id')} thread_id={response.get('threadId')}")
    return 0


def _handle_delete(service, params: list[str]) -> int:
    if len(params) != 1:
        raise UsageError("d requires exactly 1 param: <message_id>")
    message_id = params[0]
    delete_message(service, message_id)
    print(f"deleted message_id={message_id}")
    return 0


def _handle_mark_spammer(config, account, service, params: list[str]) -> int:
    if len(params) != 1:
        raise UsageError("ms requires exactly 1 param: <message_id>")
    message_id = params[0]
    message = get_message(service, message_id, format_type="metadata", metadata_headers=["From"])
    row = summarize_message(message)
    sender = parseaddr(row.get("from", ""))[1].strip().lower() or row.get("from_email", "").strip().lower()
    if sender:
        merged = _merge_unique(account.spam_senders, [sender])
        update_account_sender_lists(config.path, {account.preset: merged})
    delete_message(service, message_id)
    if sender:
        print(f"marked_spammer sender={sender} trashed_message_id={message_id}")
    else:
        print(f"trashed_message_id={message_id} (sender not detected)")
    return 0


def _handle_contacts(config, account, params: list[str]) -> int:
    contacts = dict(account.contacts)
    if not params:
        if not contacts:
            print("no contacts configured")
            return 0
        print("contacts:")
        for index, alias in enumerate(sorted(contacts.keys()), start=1):
            print(f"  {index}. {alias} -> {contacts[alias]}")
        return 0

    action = params[0]
    if action == "-e":
        if len(params) != 1:
            raise UsageError("cn -e does not accept extra args")
        editor_cmd = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vim"
        editor_parts = shlex.split(editor_cmd)
        if not editor_parts:
            raise UsageError("Editor command is empty. Set VISUAL or EDITOR.")
        try:
            proc = subprocess.run([*editor_parts, str(config.path)], check=False)
        except FileNotFoundError as exc:
            raise UsageError(
                f"Editor not found: {editor_cmd}. Set VISUAL or EDITOR to a valid editor."
            ) from exc
        if proc.returncode != 0:
            raise UsageError(f"Editor exited with code {proc.returncode}")
        return 0

    if action == "-a":
        if len(params) != 3:
            raise UsageError("cn -a requires: <alias> <email>")
        alias = params[1].strip().lower()
        email = params[2].strip()
        if not alias:
            raise UsageError("cn -a requires non-empty alias")
        if "@" not in email:
            email = _resolve_contact(email, contacts)
        if "@" not in email:
            raise UsageError("cn -a requires an email address")
        contacts[alias] = email
        update_account_contacts(config.path, account.preset, contacts)
        print(f"contact added: {alias} -> {email}")
        return 0

    if action == "-d":
        if len(params) != 2:
            raise UsageError("cn -d requires: <alias>")
        alias = params[1].strip().lower()
        if not alias:
            raise UsageError("cn -d requires non-empty alias")
        if alias not in contacts:
            print(f"contact not found: {alias}")
            return 0
        del contacts[alias]
        update_account_contacts(config.path, account.preset, contacts)
        print(f"contact deleted: {alias}")
        return 0

    raise UsageError("cn supports: (no args), -a <alias> <email>, -d <alias>, -e")


def _read_signature(signature_file) -> str:
    signature = signature_file.read_text(encoding="utf-8").strip()
    if not signature:
        raise ConfigError(f"Signature file is empty: {signature_file}")
    return signature


def _append_signature(body: str, signature: str) -> str:
    body_clean = body.rstrip()
    sig_block = f"-- \n{signature.strip()}"
    if body_clean.endswith(sig_block):
        return body_clean
    return f"{body_clean}\n\n{sig_block}"


def _upgrade_to_latest() -> int:
    curl = shutil.which("curl")
    bash = shutil.which("bash")
    if not curl:
        print("curl not found in PATH.", file=sys.stderr)
        return 1
    if not bash:
        print("bash not found in PATH.", file=sys.stderr)
        return 1

    url = "https://raw.githubusercontent.com/ryangerardwilson/gmail/main/install.sh"
    with tempfile.NamedTemporaryFile(suffix=".sh", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    try:
        fetch = subprocess.run([curl, "-fsSL", url, "-o", str(tmp_path)], check=False)
        if fetch.returncode != 0:
            return fetch.returncode
        run = subprocess.run([bash, str(tmp_path)], check=False)
        return run.returncode
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass


def _default_signature_path(email: str) -> Path:
    return Path("~/.config/gmail/signatures").expanduser() / f"{email}.txt"


def _prompt_signature_file(email: str) -> Path:
    default_path = _default_signature_path(email)
    value = input(f"Signature file path [{default_path}]: ").strip()
    path = Path(value).expanduser() if value else default_path
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(email, encoding="utf-8")
    return path.resolve()


def _handle_auth(params: list[str]) -> int:
    if len(params) != 1:
        raise UsageError("Use: gmail auth <client_secret_path>")
    client_secret = Path(params[0]).expanduser()
    if not client_secret.exists() or not client_secret.is_file():
        raise UsageError(f"Missing client secret file: {client_secret}")
    authorized = authorize_account(client_secret)
    signature_file = _prompt_signature_file(authorized.email)
    account = upsert_authenticated_account(
        resolve_config_path(),
        client_secret,
        authorized.email,
        signature_file,
    )
    print(
        f"authorized preset={account.preset} email={account.email} signature_file={account.signature_file}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if argv and argv[0] == "-h":
        _print_usage_guide(show_examples=True, show_usage=True)
        return 0
    if not argv:
        _print_usage_guide(show_examples=True, show_usage=True)
        return 0
    first = argv[0].lower()
    if first == "auth":
        return _handle_auth(argv[1:])
    preset_required_commands = {"s", "-s", "ls", "r", "o", "si", "sc", "sa", "se", "mr", "mra", "mur", "mstr", "mustr", "d", "ms", "cn"}
    if first in preset_required_commands:
        hint = " ".join(argv)
        raise UsageError(
            f"Missing preset before command '{argv[0]}'. Example: gmail <preset> {hint}"
        )

    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.upgrade:
        if args.preset or args.command or args.params:
            raise UsageError("-u does not accept extra args. Use: gmail -u")
        return _upgrade_to_latest()
    if not args.preset or not args.command:
        raise UsageError("Expected: <preset> <command>. Use -h for usage.")

    config = load_config()
    account = get_account(config, args.preset)

    command = args.command.lower()
    if command == "cn":
        return _handle_contacts(config, account, args.params)

    service = build_gmail_service(account)
    if command in {"s", "-s"}:
        signature = _read_signature(account.signature_file)
        return _handle_send(service, account.email, args.params, signature, account.contacts)

    if command == "ls":
        return _handle_list(
            service,
            args.params,
            config.default_list_limit,
            account.email,
            utc_offset=config.timezone_offset,
            config_path=config.path,
            account=account,
        )

    if command == "r":
        signature = _read_signature(account.signature_file)
        return _handle_reply(service, account.email, args.params, signature, account.contacts)

    if command == "o":
        return _handle_open_message(
            service, args.params, account.email, utc_offset=config.timezone_offset
        )

    if command == "mr":
        return _handle_mark_read(service, args.params)

    if command == "mra":
        return _handle_mark_read_all(service, args.params)

    if command == "mur":
        return _handle_mark_unread(service, args.params)

    if command == "mstr":
        return _handle_mark_star(service, args.params)

    if command == "mustr":
        return _handle_mark_unstar(service, args.params)

    if command == "d":
        return _handle_delete(service, args.params)

    if command == "ms":
        return _handle_mark_spammer(config, account, service, args.params)

    if command == "si":
        if args.params:
            raise UsageError("si does not accept extra args. Use: gmail <preset> si")
        return _handle_spam_identify(config, account, service)

    if command == "sc":
        if args.params:
            raise UsageError("sc does not accept extra args. Use: gmail <preset> sc")
        return _handle_spam_clean(account, service)

    if command == "sa":
        return _handle_spam_add(config, account, service, args.params)

    if command == "se":
        return _handle_spam_exclude(config, account, args.params)

    if command == "cn":
        return _handle_contacts(config, account, args.params)

    raise UsageError(
        f"Unknown command '{args.command}'. Use s, ls, r, o, mr, mra, mur, mstr, mustr, d, ms, si, sc, sa, se, or cn."
    )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except GmailCliError as exc:
        print(f"error: {exc.message}", file=sys.stderr)
        raise SystemExit(exc.exit_code)
