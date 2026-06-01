from __future__ import annotations

from email.utils import parseaddr
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import urllib.request

from _version import __version__
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
    message_has_non_calendar_attachment,
    unstar_message,
    reply_to_message,
    reply_to_thread,
    send_email,
)
from gmail_cli.query_parser import parse_list_query_args
from gmail_cli.spam_flow import (
    make_identify_decision,
    run_cleanup_for_account,
    run_identify_for_account,
)

_TRAILING_OPTIONS = {"-cc", "-bcc", "-atch", "-dp"}
_DECLARATIVE_TRAILING_OPTIONS = {"cc", "bcc", "attach"}
ANSI_GRAY = "\033[38;5;245m"
ANSI_RESET = "\033[0m"
INSTALL_SCRIPT_URL = "https://raw.githubusercontent.com/ryangerardwilson/gmail/main/install.sh"
HELP_TEXT = """Gmail CLI

global actions:
  gmail help
    show this help
  gmail version
    print the installed version
  gmail upgrade
    upgrade to the latest release

features:
  authorize a Google account and save or refresh its preset
  # auth <client_secret_path>
  gmail auth ~/Documents/credentials/client_secret.json

  edit account config, including signature_file paths used for automatic send/reply signatures
  # config
  gmail config

  send a new email, with optional editor mode, cc, bcc, and attachments
  # <preset> send to <email|alias> subject <subject> body <body> [cc <emails>] [bcc <emails>] [attach <path> ...]
  gmail 1 send in editor
  gmail 1 send to person@example.com subject "Hello" body "Body"
  gmail 1 send to boss subject "Notes" body "Draft is attached" attach ~/notes.txt

  list and search messages using explicit filters
  # <preset> list [unread|read|sent|starred|external] [from <sender>] [containing <text>] [since <window>] [limit <count>] [with attachments] [open]
  gmail 1 list unread from geeta since 2w limit 10
  gmail 1 list sent containing proposal since 14d limit 10
  gmail 1 list with attachments from geeta limit 10

  open messages and reply to messages or threads
  # <preset> open message <id> | <preset> open thread <id>
  gmail 1 open message 19caef2cd6494116
  # <preset> reply to <message_id> [all] [in editor|body <body>]
  gmail 1 reply to 19caef2cd6494116 body "Thanks for the update."
  gmail 1 reply to thread 19ca756c06a7ebcd all body "Thanks everyone."

  clean spam, inspect spam candidates, and control the hourly timer
  # spam clean | timer install|disable|status | <preset> spam inspect|clean|add|allow
  gmail spam clean
  gmail timer install
  gmail 1 spam inspect
  gmail 1 spam add unread
  gmail 1 spam allow trusted@example.com

  manage saved contacts for a preset
  # <preset> contacts list|add|delete|edit
  gmail 1 contacts list
  gmail 1 contacts add boss boss@example.com
  gmail 1 contacts edit
"""


def _muted(text: str) -> str:
    if not sys.stdout.isatty() or "NO_COLOR" in os.environ:
        return text
    return f"{ANSI_GRAY}{text}{ANSI_RESET}"


def _print_help() -> None:
    print(_muted(HELP_TEXT.rstrip()))


def _upgrade_app() -> int:
    with urllib.request.urlopen(INSTALL_SCRIPT_URL) as response:
        script_body = response.read()
    with tempfile.NamedTemporaryFile(delete=False) as handle:
        handle.write(script_body)
        script_path = Path(handle.name)
    try:
        script_path.chmod(0o700)
        result = subprocess.run(
            ["/usr/bin/env", "bash", str(script_path), "upgrade"],
            check=False,
            text=True,
            env=os.environ.copy(),
        )
        return result.returncode
    finally:
        script_path.unlink(missing_ok=True)


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


def _parse_draft_path(value: str) -> Path:
    draft_path = Path(value).expanduser()
    if not draft_path.exists() or not draft_path.is_file():
        raise UsageError(f"-dp requires a readable draft file path: {value}")
    return draft_path


def _read_draft_body(path: Path) -> str:
    try:
        body = path.read_text(encoding="utf-8").rstrip()
    except OSError as exc:
        raise UsageError(f"Could not read draft file '{path}': {exc}") from exc
    if not body:
        raise UsageError(f"Draft file is empty: {path}")
    return body


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


def _open_config_in_editor(config_path: Path | None = None) -> int:
    resolved_path = (config_path or resolve_config_path()).expanduser()
    if not resolved_path.exists():
        example_path = Path(__file__).with_name("example_config.json")
        resolved_path.parent.mkdir(parents=True, exist_ok=True)
        if example_path.exists():
            resolved_path.write_text(
                example_path.read_text(encoding="utf-8"),
                encoding="utf-8",
            )
        else:
            resolved_path.write_text(
                '{\n  "defaults": {\n    "list_limit": 10,\n    "timezone_offset": "+05:30"\n  },\n  "accounts": {}\n}\n',
                encoding="utf-8",
            )

    editor_cmd = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vim"
    editor_parts = shlex.split(editor_cmd)
    if not editor_parts:
        raise UsageError("Editor command is empty. Set VISUAL or EDITOR.")
    try:
        proc = subprocess.run([*editor_parts, str(resolved_path)], check=False)
    except FileNotFoundError as exc:
        raise UsageError(
            f"Editor not found: {editor_cmd}. Set VISUAL or EDITOR to a valid editor."
        ) from exc
    if proc.returncode != 0:
        raise UsageError(f"Editor exited with code {proc.returncode}")
    return 0


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
    if len(params) < 2:
        raise UsageError(
            "Send requires: <to> <subject> <body> or <to> <subject> -dp <draft_path> "
            "[-cc <emails>] [-bcc <emails>] [-atch <path> [<path> ...]]"
        )

    to_email, subject = params[:2]
    body = ""
    trailing_start = 2
    if len(params) > 2 and params[2] not in _TRAILING_OPTIONS:
        body = params[2]
        trailing_start = 3
    trailing = params[trailing_start:]
    cc_emails: list[str] = []
    bcc_emails: list[str] = []
    attachment_paths: list[Path] = []
    draft_path: Path | None = None
    index = 0
    while index < len(trailing):
        token = trailing[index]
        if token in _TRAILING_OPTIONS:
            if index + 1 >= len(trailing):
                if token == "-atch":
                    raise UsageError("-atch requires a path to a file or directory")
                if token == "-dp":
                    raise UsageError("-dp requires a path to a draft file")
                raise UsageError(f"{token} requires a comma-separated email list")
            if token == "-atch":
                parsed_paths, next_index = _consume_attachment_paths(trailing, index + 1)
                attachment_paths.extend(parsed_paths)
                index = next_index
            elif token == "-dp":
                if body:
                    raise UsageError("Use either <body> or -dp <draft_path>, not both")
                if draft_path is not None:
                    raise UsageError("-dp may only be provided once")
                draft_path = _parse_draft_path(trailing[index + 1])
                index += 2
            else:
                parsed = _parse_recipient_csv(trailing[index + 1], token)
                if token == "-cc":
                    cc_emails.extend(parsed)
                else:
                    bcc_emails.extend(parsed)
                index += 2
            continue
        raise UsageError(
            "Send options must appear at the end. Supported trailing options: -cc, -bcc, -atch, -dp"
        )

    if draft_path is not None:
        body = _read_draft_body(draft_path)
    if not body:
        raise UsageError("Send requires <body> or -dp <draft_path> before trailing options")

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
    if len(params) == 1:
        return None
    if len(params) != 3 or params[1] != "-l":
        raise UsageError(f"{flag} accepts only optional flag: -l <limit>")
    try:
        max_results = int(params[2])
    except ValueError as exc:
        raise UsageError(f"{flag} -l limit must be a positive integer") from exc
    if max_results <= 0:
        raise UsageError(f"{flag} -l limit must be > 0")
    return max_results


def _list_with_optional_limit(service, gmail_query: str, max_results: int | None) -> list[dict]:
    if max_results is None:
        return list_all_messages(service, gmail_query)
    return list_messages(service, gmail_query, max_results)


def _attachment_filtered_query(gmail_query: str) -> str:
    parts = [term for term in (gmail_query.strip(), "has:attachment") if term]
    return " ".join(parts)


def _filter_messages_with_attachments(messages: list[dict]) -> list[dict]:
    return [message for message in messages if message_has_non_calendar_attachment(message)]


def _list_with_attachment_filter(
    service,
    gmail_query: str,
    max_results: int | None,
) -> list[dict]:
    attachment_query = _attachment_filtered_query(gmail_query)
    if max_results is None:
        return _filter_messages_with_attachments(list_all_messages(service, attachment_query))

    matched: list[dict] = []
    page_token: str | None = None
    while len(matched) < max_results:
        page, page_token = list_messages_page(service, attachment_query, max_results, page_token=page_token)
        if not page:
            break
        for message in page:
            if message_has_non_calendar_attachment(message):
                matched.append(message)
                if len(matched) >= max_results:
                    break
        if page_token is None:
            break
    return matched


def _list_matching_messages(
    service,
    gmail_query: str,
    max_results: int | None,
    *,
    attachments_only: bool,
) -> list[dict]:
    if attachments_only:
        return _list_with_attachment_filter(service, gmail_query, max_results)
    return _list_with_optional_limit(service, gmail_query, max_results)


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


def _extract_list_flags(params: list[str]) -> tuple[bool, bool, list[str]]:
    open_mode = False
    attachments_only = False
    filtered: list[str] = []
    for token in params:
        if token == "-o":
            open_mode = True
            continue
        if token == "-wa":
            attachments_only = True
            continue
        filtered.append(token)
    return open_mode, attachments_only, filtered


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


def _exclude_sent_query(*terms: str) -> str:
    filtered = [term for term in terms if term.strip()]
    filtered.append("-in:sent")
    return " ".join(filtered)


def _attachment_download_dir(preset: str, message_id: str, cwd: Path | None = None) -> Path:
    root = cwd or Path.cwd()
    return root / f"atch_{preset}_{message_id}"


def _handle_list(
    service,
    params: list[str],
    default_limit: int,
    my_email: str,
    utc_offset: str = "+05:30",
    config_path=None,
    account=None,
) -> int:
    open_mode, attachments_only, filtered_params = _extract_list_flags(params)
    if not filtered_params:
        if attachments_only:
            messages = _list_matching_messages(
                service,
                "-in:sent",
                default_limit,
                attachments_only=True,
            )
            messages = _sort_messages_oldest_first(messages)
            _print_list_results(service, messages, my_email, utc_offset, open_mode)
            return 0
        raise UsageError(
            "ls requires -l <limit>, -wa, -f <from>, -c <contains>, -tl <time_limit>, or a mode flag like -ur"
        )
    if filtered_params[0] in {"-ura", "-ra"} and open_mode:
        raise UsageError("ls -o is not supported with -ura or -ra")
    if filtered_params[0] in {"-ura", "-ra"} and attachments_only:
        raise UsageError("ls -wa is not supported with -ura or -ra")

    if filtered_params[0] == "-ur":
        max_results = _parse_optional_limit("ls -ur", filtered_params)
        messages = _list_matching_messages(
            service,
            _exclude_sent_query("is:unread"),
            max_results,
            attachments_only=attachments_only,
        )
        messages = _sort_messages_oldest_first(messages)
        _print_list_results(service, messages, my_email, utc_offset, open_mode)
        return 0

    if filtered_params[0] == "-r":
        max_results = _parse_optional_limit("ls -r", filtered_params)
        messages = _list_matching_messages(
            service,
            _exclude_sent_query("is:read"),
            max_results,
            attachments_only=attachments_only,
        )
        messages = _sort_messages_oldest_first(messages)
        _print_list_results(service, messages, my_email, utc_offset, open_mode)
        return 0

    if filtered_params[0] == "-str":
        max_results = _parse_optional_limit("ls -str", filtered_params)
        messages = _list_matching_messages(
            service,
            _exclude_sent_query("is:starred"),
            max_results,
            attachments_only=attachments_only,
        )
        messages = _sort_messages_oldest_first(messages)
        _print_list_results(service, messages, my_email, utc_offset, open_mode)
        return 0

    if filtered_params[0] == "-ext":
        max_results = _parse_optional_limit("ls -ext", filtered_params)
        domain = my_email.split("@", 1)[1].strip().lower() if "@" in my_email else ""
        ext_query = _exclude_sent_query(f"-from:{my_email}")
        if domain:
            ext_query += f" -from:*@{domain}"
        messages = _list_matching_messages(
            service,
            ext_query,
            max_results,
            attachments_only=attachments_only,
        )
        messages = _sort_messages_oldest_first(messages)
        _print_list_results(service, messages, my_email, utc_offset, open_mode)
        return 0

    if filtered_params[0] == "-snt":
        if len(filtered_params) == 1:
            max_results = default_limit if attachments_only else None
            messages = _list_matching_messages(
                service,
                "in:sent",
                max_results,
                attachments_only=attachments_only,
            )
            messages = _sort_messages_oldest_first(messages)
            _print_list_results(service, messages, my_email, utc_offset, open_mode)
            return 0

        parsed = parse_list_query_args(
            filtered_params[1:],
            default_limit,
            base_terms=["in:sent"],
            require_filter_or_limit=True,
        )
        messages = _list_matching_messages(
            service,
            parsed.gmail_query,
            parsed.max_results,
            attachments_only=attachments_only,
        )
        messages = _sort_messages_oldest_first(messages)
        _print_list_results(service, messages, my_email, utc_offset, open_mode)
        return 0

    if filtered_params[0] == "-ura":
        if config_path is None or account is None:
            raise UsageError("Internal error: ls -ura requires account context")
        return _run_audit_mode(
            service,
            filtered_params,
            default_limit,
            config_path,
            account,
            _exclude_sent_query("is:unread"),
            "unread",
            "ura",
            utc_offset,
        )

    if filtered_params[0] == "-ra":
        if config_path is None or account is None:
            raise UsageError("Internal error: ls -ra requires account context")
        return _run_audit_mode(
            service,
            filtered_params,
            default_limit,
            config_path,
            account,
            _exclude_sent_query("is:read"),
            "read",
            "ra",
            utc_offset,
        )

    if filtered_params[0] == "-t":
        if len(filtered_params) != 2:
            raise UsageError("ls -t requires exactly 1 param: <thread_id>")
        thread_id = filtered_params[1]
        messages = get_thread_messages(service, thread_id)
        if attachments_only:
            messages = _filter_messages_with_attachments(messages)
        messages = _sort_messages_oldest_first(messages)
        _print_list_results(service, messages, my_email, utc_offset, open_mode)
        return 0

    parsed = parse_list_query_args(filtered_params, default_limit, base_terms=["-in:sent"])
    messages = _list_matching_messages(
        service,
        parsed.gmail_query,
        parsed.max_results,
        attachments_only=attachments_only,
    )
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
            "Reply requires: [-a] [-e] <message_id> <body> or [-a] [-e] <message_id> -dp <draft_path> "
            "[-cc <emails>] [-bcc <emails>] [-atch <path> [<path> ...]] or "
            "[-a] [-e] -t <thread_id> <body> or [-a] [-e] -t <thread_id> -dp <draft_path> "
            "[-cc <emails>] [-bcc <emails>] [-atch <path> [<path> ...]]"
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
            raise UsageError("Use declarative reply options, for example: reply to thread <id> all body <body>.")
        if token in _TRAILING_OPTIONS:
            break
        raise UsageError(
            f"Unknown reply option '{token}'. Supported: -a, -t, -e, -cc, -bcc, -atch, -dp"
        )

    remaining = params[index:]
    target_name = "thread_id" if "t" in flags else "message_id"
    use_editor = "e" in flags
    minimum_required = 1 if use_editor else 2
    if len(remaining) < minimum_required:
        raise UsageError(
            f"Reply requires: <{target_name}> {'<body> or -dp <draft_path> ' if not use_editor else ''}before trailing options"
        )

    target_id = remaining[0]
    body = ""
    trailing_start = 1
    if not use_editor:
        if len(remaining) > 1 and remaining[1] not in _TRAILING_OPTIONS:
            body = remaining[1]
            trailing_start = 2
    trailing = remaining[trailing_start:]
    cc_emails: list[str] = []
    bcc_emails: list[str] = []
    attachment_paths: list[Path] = []
    draft_path: Path | None = None
    tail_index = 0
    while tail_index < len(trailing):
        token = trailing[tail_index]
        if token in _TRAILING_OPTIONS:
            if tail_index + 1 >= len(trailing):
                if token == "-atch":
                    raise UsageError("-atch requires a path to a file or directory")
                if token == "-dp":
                    raise UsageError("-dp requires a path to a draft file")
                raise UsageError(f"{token} requires a comma-separated email list")
            if token == "-atch":
                parsed_paths, next_index = _consume_attachment_paths(trailing, tail_index + 1)
                attachment_paths.extend(parsed_paths)
                tail_index = next_index
            elif token == "-dp":
                if body:
                    raise UsageError("Use either <body> or -dp <draft_path>, not both")
                if draft_path is not None:
                    raise UsageError("-dp may only be provided once")
                draft_path = _parse_draft_path(trailing[tail_index + 1])
                tail_index += 2
            else:
                parsed = _parse_recipient_csv(trailing[tail_index + 1], token)
                if token == "-cc":
                    cc_emails.extend(parsed)
                else:
                    bcc_emails.extend(parsed)
                tail_index += 2
            continue
        raise UsageError(
            "Reply options must appear at the end. Supported trailing options: -cc, -bcc, -atch, -dp"
        )

    if draft_path is not None:
        body = _read_draft_body(draft_path)
    if not use_editor and not body:
        raise UsageError(f"Reply requires <{target_name}> <body> or <{target_name}> -dp <draft_path>")

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
                print("hint: if this id is a thread id, use: reply to thread <thread_id> in editor")
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
    print(f"spam inspect complete: spam_added={len(decision.add_to_spam)}")
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
    print(f"spam clean complete: trashed_spam={result.trashed_spam}")
    return 0


def _run_spam_clean_all_presets() -> int:
    config = load_config()
    total_trashed = 0
    ran = False
    for preset, account in config.accounts.items():
        service = build_gmail_service(account)
        ran = True
        print(f"preset={preset} email={account.email}")
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
        total_trashed += result.trashed_spam
        print(f"{preset}\ttrashed_spam={result.trashed_spam}")
    if not ran:
        raise UsageError("No configured presets found.")
    print(f"spam clean complete: presets={len(config.accounts)} trashed_spam={total_trashed}")
    return 0


def _gmail_unit_name() -> str:
    return "gmail"


def _build_runtime_command(*args: str) -> str:
    command_parts = [shlex.quote(str(Path(sys.executable).resolve()))]
    if not getattr(sys, "frozen", False):
        command_parts.append(shlex.quote(str(Path(__file__).resolve())))
    command_parts.extend(shlex.quote(arg) for arg in args)
    return " ".join(command_parts)


def _build_notification_command(summary: str, body: str, urgency: str = "normal") -> str:
    notify_function = " ".join(
        [
            "notify() {",
            'summary="$1";',
            'body="${2:-}";',
            'urgency="${3:-normal}";',
            'qs="${XDG_CONFIG_HOME:-$HOME/.config}/quickshell/omarchy-bar";',
            'if command -v quickshell >/dev/null 2>&1 && quickshell ipc -p "$qs" call bar notify "$summary" "$body" "$urgency" >/dev/null 2>&1; then return 0; fi;',
            'if command -v notify-send >/dev/null 2>&1; then notify-send -a "$summary" -u "$urgency" "$summary" "$body" || true; fi;',
            "};",
        ]
    )
    return " ".join(
        [
            notify_function,
            "notify",
            shlex.quote(summary),
            shlex.quote(body),
            shlex.quote(urgency),
        ]
    )


def _write_timer_units() -> None:
    systemd_dir = Path.home() / ".config" / "systemd" / "user"
    systemd_dir.mkdir(parents=True, exist_ok=True)
    service_path = systemd_dir / f"{_gmail_unit_name()}.service"
    timer_path = systemd_dir / f"{_gmail_unit_name()}.timer"
    entrypoint = Path(__file__).resolve()
    run_command = _build_runtime_command("spam", "clean")
    notify_command = _build_notification_command(
        "gmail",
        "Hourly spam clean finished successfully",
    )
    service_body = "\n".join(
        [
            "[Unit]",
            "Description=gmail spam clean all presets",
            "",
            "[Service]",
            "Type=oneshot",
            f"WorkingDirectory={entrypoint.parent}",
            f"ExecStart=/usr/bin/env bash -lc {shlex.quote(f'{run_command} && {notify_command}')}",
            "",
        ]
    )
    timer_body = "\n".join(
        [
            "[Unit]",
            "Description=Run gmail spam clean hourly",
            "",
            "[Timer]",
            "OnBootSec=5m",
            "OnActiveSec=5m",
            "OnUnitActiveSec=1h",
            "Persistent=true",
            "",
            "[Install]",
            "WantedBy=timers.target",
            "",
        ]
    )
    service_path.write_text(service_body, encoding="utf-8")
    timer_path.write_text(timer_body, encoding="utf-8")


def _systemctl_user(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["systemctl", "--user", *args],
        check=True,
        text=True,
        capture_output=True,
    )


def _install_timer() -> int:
    _write_timer_units()
    _systemctl_user("daemon-reload")
    _systemctl_user("enable", f"{_gmail_unit_name()}.timer")
    _systemctl_user("restart", f"{_gmail_unit_name()}.timer")
    print(f"timer enabled: {_gmail_unit_name()}.timer")
    return 0


def _disable_timer() -> int:
    _write_timer_units()
    _systemctl_user("disable", "--now", f"{_gmail_unit_name()}.timer")
    print(f"timer disabled: {_gmail_unit_name()}.timer")
    return 0


def _timer_status() -> int:
    result = _systemctl_user("status", f"{_gmail_unit_name()}.timer")
    print(result.stdout.strip())
    return 0


def _handle_spam_add(config, account, service, params: list[str]) -> int:
    if len(params) != 1:
        raise UsageError("spam add requires exactly one value: <email_or_domain_csv> or unread")

    if params[0] == "-ur":
        print("spam add unread: scanning unread messages in batches...")
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
            print("spam add unread complete: no unread messages found")
            return 0

        candidate_senders = normalize_spam_sender_list(sorted(sender_set))
        merged = _merge_unique(account.spam_senders, candidate_senders)
        update_account_sender_lists(config.path, {account.preset: merged})
        trashed = batch_delete_messages(service, message_ids)
        print(
            f"spam add unread complete: added_senders={len(candidate_senders)} "
            f"total_spam_senders={len(merged)} trashed_unread={trashed}"
        )
        return 0

    new_items = normalize_spam_sender_list([item.strip() for item in params[0].split(",")])
    if not new_items:
        raise UsageError("spam add requires at least one valid email in comma-separated input")
    merged = _merge_unique(account.spam_senders, new_items)
    update_account_sender_lists(config.path, {account.preset: merged})
    print(f"spam add complete: added={len(new_items)} total_spam_senders={len(merged)}")
    return 0


def _handle_spam_exclude(config, account, params: list[str]) -> int:
    if len(params) != 1:
        raise UsageError("spam allow requires exactly 1 param: <email1,email2,...>")
    new_items = [item.strip().lower() for item in params[0].split(",") if item.strip()]
    if not new_items:
        raise UsageError("spam allow requires at least one valid email in comma-separated input")
    merged = sorted(set(account.spam_excludes + new_items))
    update_account_spam_excludes(config.path, account.preset, merged)
    print(f"spam allow complete: added={len(new_items)} total_spam_excludes={len(merged)}")
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
    service, params: list[str], my_email: str, preset: str, utc_offset: str = "+05:30"
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
        target_dir = _attachment_download_dir(preset, target_id)
        downloaded = download_message_attachments(service, message, target_dir)
        response = mark_message_read(service, target_id)
        print(render_message_open(message, my_email, utc_offset=utc_offset))
        if downloaded:
            print(f"attachments_downloaded={len(downloaded)} dir={target_dir}")
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
    download_dirs: list[Path] = []
    message_ids: list[str] = []
    for idx, message in enumerate(messages, start=1):
        message = hydrate_message_text_bodies(service, message)
        message = hydrate_message_text_from_raw(service, message)
        message_id = str(message.get("id", "")).strip()
        if message_id:
            message_ids.append(message_id)
            target_dir = _attachment_download_dir(preset, message_id)
            downloaded = download_message_attachments(service, message, target_dir)
            if downloaded:
                download_dirs.append(target_dir)
                all_downloaded.extend(downloaded)
        print(f"[{idx}/{len(messages)}]")
        print(render_message_open(message, my_email, utc_offset=utc_offset))
        if idx < len(messages):
            print("")

    marked_read = batch_mark_messages_read(service, message_ids)
    if all_downloaded:
        print(f"attachments_downloaded={len(all_downloaded)} dirs={len(download_dirs)}")
        for directory in download_dirs:
            print(f"attachment_dir: {directory}")
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
            raise UsageError("contacts edit does not accept extra args")
        return _open_config_in_editor(config.path)

    if action == "-a":
        if len(params) != 3:
            raise UsageError("contacts add requires: <alias> <email>")
        alias = params[1].strip().lower()
        email = params[2].strip()
        if not alias:
            raise UsageError("contacts add requires a non-empty alias")
        if "@" not in email:
            email = _resolve_contact(email, contacts)
        if "@" not in email:
            raise UsageError("contacts add requires an email address")
        contacts[alias] = email
        update_account_contacts(config.path, account.preset, contacts)
        print(f"contact added: {alias} -> {email}")
        return 0

    if action == "-d":
        if len(params) != 2:
            raise UsageError("contacts delete requires: <alias>")
        alias = params[1].strip().lower()
        if not alias:
            raise UsageError("contacts delete requires a non-empty alias")
        if alias not in contacts:
            print(f"contact not found: {alias}")
            return 0
        del contacts[alias]
        update_account_contacts(config.path, account.preset, contacts)
        print(f"contact deleted: {alias}")
        return 0

    raise UsageError("contacts supports: list, add <alias> <email>, delete <alias>, edit")


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


def _take_value(tokens: list[str], index: int, keyword: str, shape: str) -> tuple[str, int]:
    if index >= len(tokens) or tokens[index] != keyword or index + 1 >= len(tokens):
        raise UsageError(shape)
    return tokens[index + 1], index + 2


def _append_declarative_tail(out: list[str], tokens: list[str], index: int, shape: str) -> list[str]:
    while index < len(tokens):
        token = tokens[index]
        if token in {"cc", "bcc"}:
            if index + 1 >= len(tokens):
                raise UsageError(f"{token} requires: <emails>")
            out.extend([f"-{token}", tokens[index + 1]])
            index += 2
            continue
        if token == "attach":
            index += 1
            if index >= len(tokens):
                raise UsageError("attach requires at least one path")
            paths: list[str] = []
            while index < len(tokens) and tokens[index] not in _DECLARATIVE_TRAILING_OPTIONS:
                paths.append(tokens[index])
                index += 1
            if not paths:
                raise UsageError("attach requires at least one path")
            out.append("-atch")
            out.extend(paths)
            continue
        raise UsageError(shape)
    return out


def _parse_send_declarative(params: list[str]) -> list[str]:
    if params == ["in", "editor"]:
        return ["-e"]
    shape = (
        "Use: gmail <preset> send to <email|alias> subject <subject> "
        "body <body>|body from <draft_path> [cc <emails>] [bcc <emails>] [attach <path> ...]"
    )
    index = 0
    to_email, index = _take_value(params, index, "to", shape)
    subject, index = _take_value(params, index, "subject", shape)
    if index >= len(params) or params[index] != "body" or index + 1 >= len(params):
        raise UsageError(shape)
    index += 1
    out = [to_email, subject]
    if params[index] == "from":
        if index + 1 >= len(params):
            raise UsageError("body from requires: <draft_path>")
        out.extend(["-dp", params[index + 1]])
        index += 2
    else:
        out.append(params[index])
        index += 1
    return _append_declarative_tail(out, params, index, shape)


def _parse_reply_declarative(params: list[str]) -> list[str]:
    shape = (
        "Use: gmail <preset> reply to <message_id|thread <thread_id>> "
        "[all] [in editor|body <body>|body from <draft_path>] "
        "[cc <emails>] [bcc <emails>] [attach <path> ...]"
    )
    if len(params) < 2 or params[0] != "to":
        raise UsageError(shape)
    index = 1
    out: list[str] = []
    if params[index] == "thread":
        if index + 1 >= len(params):
            raise UsageError(shape)
        out.append("-t")
        target_id = params[index + 1]
        index += 2
    else:
        target_id = params[index]
        index += 1
    if index < len(params) and params[index] == "all":
        out.append("-a")
        index += 1
    if index + 1 < len(params) and params[index : index + 2] == ["in", "editor"]:
        out.append("-e")
        index += 2
        out.append(target_id)
        return _append_declarative_tail(out, params, index, shape)
    if index >= len(params) or params[index] != "body" or index + 1 >= len(params):
        raise UsageError(shape)
    index += 1
    out.append(target_id)
    if params[index] == "from":
        if index + 1 >= len(params):
            raise UsageError("body from requires: <draft_path>")
        out.extend(["-dp", params[index + 1]])
        index += 2
    else:
        out.append(params[index])
        index += 1
    return _append_declarative_tail(out, params, index, shape)


def _parse_list_declarative(params: list[str]) -> list[str]:
    shape = (
        "Use: gmail <preset> list [unread|read|sent|starred|external|thread <id>] "
        "[from <sender>] [containing <text>] [since <window>] [limit <count>] "
        "[with attachments] [open]"
    )
    out: list[str] = []
    index = 0
    mode = None
    if index < len(params):
        first = params[index]
        if first in {"unread", "read", "sent", "starred", "external"}:
            mode = first
            index += 1
        elif first == "thread":
            if index + 1 >= len(params):
                raise UsageError(shape)
            out.extend(["-t", params[index + 1]])
            index += 2
        elif first in {"audit-unread", "audit-read"}:
            mode = first
            index += 1

    if mode == "unread":
        out.append("-ur")
    elif mode == "read":
        out.append("-r")
    elif mode == "sent":
        out.append("-snt")
    elif mode == "starred":
        out.append("-str")
    elif mode == "external":
        out.append("-ext")
    elif mode == "audit-unread":
        out.append("-ura")
    elif mode == "audit-read":
        out.append("-ra")

    while index < len(params):
        token = params[index]
        if token == "from":
            if index + 1 >= len(params):
                raise UsageError("from requires: <sender>")
            out.extend(["-f", params[index + 1]])
            index += 2
            continue
        if token == "containing":
            if index + 1 >= len(params):
                raise UsageError("containing requires: <text>")
            out.extend(["-c", params[index + 1]])
            index += 2
            continue
        if token == "since":
            if index + 1 >= len(params):
                raise UsageError("since requires: <window>")
            out.extend(["-tl", params[index + 1]])
            index += 2
            continue
        if token == "limit":
            if index + 1 >= len(params):
                raise UsageError("limit requires: <count>")
            out.extend(["-l", params[index + 1]])
            index += 2
            continue
        if token == "with" and index + 1 < len(params) and params[index + 1] == "attachments":
            out.append("-wa")
            index += 2
            continue
        if token == "open":
            out.append("-o")
            index += 1
            continue
        raise UsageError(shape)
    return out


def _parse_contacts_declarative(params: list[str]) -> list[str]:
    if not params or params == ["list"]:
        return []
    action = params[0]
    if action == "add" and len(params) == 3:
        return ["-a", params[1], params[2]]
    if action == "delete" and len(params) == 2:
        return ["-d", params[1]]
    if action == "edit" and len(params) == 1:
        return ["-e"]
    raise UsageError("Use: gmail <preset> contacts list|add <alias> <email>|delete <alias>|edit")


def _is_legacy_gmail_command(command: str) -> bool:
    return command in {
        "s",
        "-s",
        "ls",
        "r",
        "o",
        "si",
        "sc",
        "sa",
        "se",
        "mr",
        "mra",
        "mur",
        "mstr",
        "mustr",
        "d",
        "ms",
        "cn",
    }


def _dispatch(argv: list[str]) -> int:
    first = argv[0].lower()
    if first == "auth":
        return _handle_auth(argv[1:])
    if first == "config":
        if len(argv) != 1:
            raise UsageError("Use: gmail config")
        return _open_config_in_editor()
    if first == "spam":
        if argv[1:] != ["clean"]:
            raise UsageError("Use: gmail spam clean")
        return _run_spam_clean_all_presets()
    if first == "timer":
        if len(argv) != 2 or argv[1] not in {"install", "disable", "status"}:
            raise UsageError("Use: gmail timer install|disable|status")
        if argv[1] == "install":
            return _install_timer()
        if argv[1] == "disable":
            return _disable_timer()
        return _timer_status()
    if first in {"conf", "sc", "ti", "td", "st"}:
        raise UsageError("Use declarative commands: gmail config, gmail spam clean, or gmail timer install|disable|status")
    if not first.isdigit():
        raise UsageError("Expected: gmail <preset> <command>. Use gmail help for examples.")
    if len(argv) < 2:
        raise UsageError("Expected: gmail <preset> <command>. Use gmail help for examples.")

    config = load_config()
    account = get_account(config, first)

    command = argv[1].lower()
    params = argv[2:]
    if _is_legacy_gmail_command(command):
        raise UsageError("Use declarative commands. Run: gmail help")
    if command == "contacts":
        return _handle_contacts(config, account, _parse_contacts_declarative(params))

    service = build_gmail_service(account)
    if command == "send":
        signature = _read_signature(account.signature_file)
        return _handle_send(service, account.email, _parse_send_declarative(params), signature, account.contacts)

    if command == "list":
        return _handle_list(
            service,
            _parse_list_declarative(params),
            config.default_list_limit,
            account.email,
            utc_offset=config.timezone_offset,
            config_path=config.path,
            account=account,
        )

    if command == "reply":
        signature = _read_signature(account.signature_file)
        return _handle_reply(service, account.email, _parse_reply_declarative(params), signature, account.contacts)

    if command == "open":
        if len(params) != 2 or params[0] not in {"message", "thread"}:
            raise UsageError("Use: gmail <preset> open message <id> | open thread <id>")
        open_params = [params[1]] if params[0] == "message" else ["-t", params[1]]
        return _handle_open_message(
            service, open_params, account.email, account.preset, utc_offset=config.timezone_offset
        )

    if command == "mark":
        if len(params) == 3 and params[0] == "message":
            message_id, state = params[1], params[2]
            if state == "read":
                return _handle_mark_read(service, [message_id])
            if state == "unread":
                return _handle_mark_unread(service, [message_id])
            if state == "starred":
                return _handle_mark_star(service, [message_id])
            if state == "unstarred":
                return _handle_mark_unstar(service, [message_id])
        if params == ["all", "unread", "read"]:
            return _handle_mark_read_all(service, [])
        raise UsageError("Use: gmail <preset> mark message <id> read|unread|starred|unstarred")

    if command == "delete":
        if len(params) != 2 or params[0] != "message":
            raise UsageError("Use: gmail <preset> delete message <id>")
        return _handle_delete(service, [params[1]])

    if command == "spam":
        if not params:
            raise UsageError("Use: gmail <preset> spam inspect|clean|add|allow")
        action = params[0]
        rest = params[1:]
        if action == "inspect":
            if rest:
                raise UsageError("Use: gmail <preset> spam inspect")
            return _handle_spam_identify(config, account, service)
        if action == "clean":
            if rest:
                raise UsageError("Use: gmail <preset> spam clean")
            return _handle_spam_clean(account, service)
        if action == "add":
            if rest == ["unread"]:
                return _handle_spam_add(config, account, service, ["-ur"])
            if len(rest) != 1:
                raise UsageError("Use: gmail <preset> spam add <email_or_domain_csv>|unread")
            return _handle_spam_add(config, account, service, rest)
        if action == "allow":
            if len(rest) != 1:
                raise UsageError("Use: gmail <preset> spam allow <email_or_domain_csv>")
            return _handle_spam_exclude(config, account, rest)
        if action == "mark" and len(rest) == 2 and rest[0] == "message":
            return _handle_mark_spammer(config, account, service, [rest[1]])
        raise UsageError("Use: gmail <preset> spam inspect|clean|add|allow")

    if command == "inspect-spam":
        if params:
            raise UsageError("Use: gmail <preset> spam inspect")
        return _handle_spam_identify(config, account, service)

    raise UsageError(
        "Unknown command. Use: send, list, open, reply, mark, delete, spam, or contacts."
    )


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        _print_help()
        return 0
    if args == ["help"]:
        _print_help()
        return 0
    if args == ["version"]:
        print(__version__)
        return 0
    if args == ["upgrade"]:
        return _upgrade_app()
    if args[0] in {"help", "version", "upgrade"}:
        raise UsageError(f"Use: gmail {args[0]}")
    return _dispatch(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except GmailCliError as exc:
        print(f"error: {exc.message}", file=sys.stderr)
        raise SystemExit(exc.exit_code)
    except subprocess.CalledProcessError as exc:
        message = exc.stderr.strip() or exc.stdout.strip() or str(exc)
        print(f"systemctl failed: {message}", file=sys.stderr)
        raise SystemExit(2)
