from __future__ import annotations

import argparse
from email.utils import parseaddr
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from gmail_cli.auth import build_gmail_service
from gmail_cli.config import get_account, load_config, normalize_sender_list, update_account_sender_lists
from gmail_cli.errors import ConfigError, GmailCliError, UsageError
from gmail_cli.formatters import render_messages_table
from gmail_cli.formatters import summarize_message
from gmail_cli.gmail_api import (
    delete_message,
    get_thread_messages,
    list_all_messages,
    list_messages,
    mark_message_read,
    reply_to_message,
    reply_to_thread,
    send_email,
)
from gmail_cli.query_parser import parse_declarative_query
from gmail_cli.spam_flow import (
    make_identify_decision,
    parse_exclusion_indexes,
    run_cleanup_for_account,
    run_identify_for_account,
)

__version__ = "0.1.0"
_TRAILING_OPTIONS = {"-cc", "-bcc", "-atch"}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Declarative Gmail CLI",
        usage=(
            "gmail -v\n"
            "gmail -u\n"
            "gmail <preset> si\n"
            "gmail <preset> sc\n"
            "gmail <preset> -mr <message_id>\n"
            "gmail <preset> -d <message_id>\n"
            "gmail <preset> s <to> <subject> <body> [-cc <emails>] [-bcc <emails>] [-atch <path> [<path> ...]]\n"
            "gmail <preset> ls <query>\n"
            "gmail <preset> ls -ur [limit]\n"
            "gmail <preset> ls -ura [limit]\n"
            "gmail <preset> ls -t <thread_id>\n"
            "gmail <preset> r [-a] <message_id> <body> [-cc <emails>] [-bcc <emails>] [-atch <path> [<path> ...]]\n"
            "gmail <preset> r [-a] -t <thread_id> <body> [-cc <emails>] [-bcc <emails>] [-atch <path> [<path> ...]]"
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
    parser.add_argument("command", nargs="?", help="Command: s | -s | ls | r | -mr | -d | si | sc")
    parser.add_argument("params", nargs=argparse.REMAINDER, help="Command parameters")
    return parser


def _print_usage_guide() -> None:
    print(
        "\n".join(
            [
                "Gmail CLI Usage",
                "",
                "  gmail -v",
                "  gmail -u",
                "  gmail <preset> si",
                "  gmail <preset> sc",
                "  gmail <preset> -mr <message_id>",
                "  gmail <preset> -d <message_id>",
                "  gmail <preset> s <to> <subject> <body> [-cc <emails>] [-bcc <emails>] [-atch <path> [<path> ...]]",
                "  gmail <preset> ls <query>",
                "  gmail <preset> ls -ur [limit]",
                "  gmail <preset> ls -ura [limit]",
                "  gmail <preset> ls -t <thread_id>",
                "  gmail <preset> r [-a] <message_id> <body> [-cc <emails>] [-bcc <emails>] [-atch <path> [<path> ...]]",
                "  gmail <preset> r [-a] -t <thread_id> <body> [-cc <emails>] [-bcc <emails>] [-atch <path> [<path> ...]]",
                "",
                "Examples:",
                "  # Send email",
                "  gmail 1 s \"xyz@example.com\" \"Hello\" \"Body\"",
                "  gmail 1 s \"xyz@example.com\" \"Hello\" \"Body\" -cc \"cc1@example.com,cc2@example.com\" -bcc \"audit@example.com\"",
                "  gmail 1 s \"xyz@example.com\" \"Hello\" \"Body\" -atch \"/tmp/notes.txt\"",
                "  gmail 1 s \"xyz@example.com\" \"Hello\" \"Body\" -atch \"/tmp/notes.txt\" \"/tmp/project_dir\"",
                "",
                "  # List and audit messages",
                "  gmail 1 ls \"contains jake limit 1\"",
                "  gmail 1 ls -ur",
                "  gmail 1 ls -ur 1",
                "  # Audit unread emails",
                "  gmail 1 ls -ura 10",
                "  gmail 1 ls \"to silvia limit 1\"",
                "  gmail 1 ls -t \"19ca756c06a7ebcd\"",
                "",
                "  # Single-message utilities",
                "  gmail 1 -mr \"19caef2cd6494116\"",
                "  gmail 1 -d \"19caef2cd6494116\"",
                "",
                "  # Reply",
                "  gmail 1 r \"19caef2cd6494116\" \"Thanks for the update.\"",
                "  gmail 1 r -a \"19caef2cd6494116\" \"Thanks all.\"",
                "  gmail 1 r \"19caef2cd6494116\" \"Adding context.\" -cc \"manager@example.com\"",
                "  gmail 1 r \"19caef2cd6494116\" \"Please review.\" -atch \"/tmp/project_dir\"",
                "  gmail 1 r -a \"19caef2cd6494116\" \"Please review.\" -atch \"/tmp/notes.txt\" \"/tmp/project_dir\"",
                "  gmail 1 r -t \"19ca756c06a7ebcd\" \"Following up on this thread.\"",
                "  gmail 1 r -ta \"19ca756c06a7ebcd\" \"Thanks everyone.\"",
                "",
                "  # Spam flow",
                "  gmail 1 si",
                "  gmail 1 sc",
            ]
        )
    )


def _parse_recipient_csv(value: str, flag: str) -> list[str]:
    parsed = [item.strip() for item in value.split(",") if item.strip()]
    if not parsed:
        raise UsageError(f"{flag} requires at least one email address")
    return parsed


def _parse_attachment_path(value: str) -> Path:
    path = Path(value).expanduser()
    if not path.exists():
        raise UsageError(f"-atch path not found: {path}")
    if not (path.is_file() or path.is_dir()):
        raise UsageError(f"-atch must point to a file or directory: {path}")
    return path


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


def _handle_send(service, from_email: str, params: list[str], signature: str) -> int:
    to_email, subject, body, cc_emails, bcc_emails, attachment_paths = _parse_send_args(params)
    signed_body = _append_signature(body, signature)
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
    print(f"sent message_id={response.get('id')} thread_id={response.get('threadId')}")
    return 0


def _parse_optional_limit(flag: str, params: list[str], default_limit: int) -> int:
    if len(params) > 2:
        raise UsageError(f"{flag} accepts at most 1 optional param: [limit]")
    max_results = default_limit
    if len(params) == 2:
        try:
            max_results = int(params[1])
        except ValueError as exc:
            raise UsageError(f"{flag} limit must be a positive integer") from exc
        if max_results <= 0:
            raise UsageError(f"{flag} limit must be > 0")
    return max_results


def _handle_list(
    service,
    params: list[str],
    default_limit: int,
    my_email: str,
    config_path=None,
    account=None,
) -> int:
    if not params:
        raise UsageError("ls requires a query string, e.g. \"from maanas limit 1\"")

    if params[0] == "-ur":
        max_results = _parse_optional_limit("ls -ur", params, default_limit)
        messages = list_messages(service, "is:unread", max_results)
        print(render_messages_table(messages, my_email))
        return 0

    if params[0] == "-ura":
        if config_path is None or account is None:
            raise UsageError("Internal error: ls -ura requires account context")
        if len(params) == 1:
            messages = list_all_messages(service, "is:unread")
        else:
            max_results = _parse_optional_limit("ls -ura", params, default_limit)
            messages = list_messages(service, "is:unread", max_results)
        if not messages:
            print("No unread messages found.")
            return 0

        spam_senders = list(account.spam_senders)
        spam_set = set(spam_senders)
        trashed = 0
        audited = 0

        print(
            "Unread audit mode: enter 's' for spam (add sender to spam_senders + trash message), "
            "'t' for trash only, 'n' for not spam (leave unread), 'q' to stop."
        )
        for index, message in enumerate(messages, start=1):
            audited += 1
            row = summarize_message(message)
            sender = parseaddr(row.get("from", ""))[1].strip().lower() or row.get("from_email", "").strip().lower()
            message_id = str(message.get("id", ""))
            print(f"\n[{index}/{len(messages)}] message_id={message_id}")
            print(f"from    : {row.get('from', '')}")
            print(f"subject : {row.get('subject', '')}")
            print(f"date    : {row.get('date', '')}")
            print(f"snippet : {str(message.get('snippet', ''))}")

            while True:
                choice = input("action [s=spam, t=trash only, n=not spam, q=quit]: ").strip().lower()
                if choice not in {"s", "t", "n", "q"}:
                    print("Invalid input. Use s, t, n, or q.")
                    continue
                break

            if choice == "q":
                audited -= 1
                break
            if choice == "n":
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

        update_account_sender_lists(
            config_path,
            {
                account.preset: {
                    "spam_senders": spam_senders,
                    "not_spam_senders": account.not_spam_senders,
                }
            },
        )
        print(f"ls -ura complete: audited={audited} trashed={trashed}")
        return 0

    if params[0] == "-t":
        if len(params) != 2:
            raise UsageError("ls -t requires exactly 1 param: <thread_id>")
        thread_id = params[1]
        messages = get_thread_messages(service, thread_id)
        print(render_messages_table(messages, my_email))
        return 0

    query = " ".join(params)
    parsed = parse_declarative_query(query, default_limit)
    messages = list_messages(service, parsed.gmail_query, parsed.max_results)
    print(render_messages_table(messages, my_email))
    return 0


def _parse_reply_args(
    params: list[str],
) -> tuple[bool, bool, str, str, list[str], list[str], list[Path]]:
    if not params:
        raise UsageError(
            "Reply requires: [-a] <message_id> <body> [-cc <emails>] [-bcc <emails>] [-atch <path> [<path> ...]] "
            "or [-a] -t <thread_id> <body> [-cc <emails>] [-bcc <emails>] [-atch <path> [<path> ...]]"
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
        for flag in token[1:]:
            if flag not in {"a", "t"}:
                raise UsageError(
                    f"Unknown reply option '-{flag}'. Supported: -a, -t, -at, -ta, -cc, -bcc, -atch"
                )
            flags.add(flag)
        index += 1

    remaining = params[index:]
    target_name = "thread_id" if "t" in flags else "message_id"
    if len(remaining) < 2:
        raise UsageError(
            f"Reply requires: <{target_name}> <body> before trailing options"
        )

    target_id, body = remaining[:2]
    trailing = remaining[2:]
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

    return "t" in flags, "a" in flags, target_id, body, cc_emails, bcc_emails, attachment_paths


def _handle_reply(service, from_email: str, params: list[str], signature: str) -> int:
    use_thread, reply_all, target_id, body, cc_emails, bcc_emails, attachment_paths = _parse_reply_args(params)
    signed_body = _append_signature(body, signature)
    if use_thread:
        response = reply_to_thread(
            service,
            from_email,
            target_id,
            signed_body,
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
            signed_body,
            reply_all=reply_all,
            cc_emails=cc_emails,
            bcc_emails=bcc_emails,
            attachment_paths=attachment_paths,
        )
    print(f"replied message_id={response.get('id')} thread_id={response.get('threadId')}")
    return 0


def _merge_unique(existing: list[str], incoming: list[str]) -> list[str]:
    merged = existing + incoming
    return normalize_sender_list(merged)


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

    print("potential spam senders (>5 unread, non-gmail):")
    for index, item in enumerate(candidates, start=1):
        print(f"  {index}. {item.sender} (unread={item.unread_count})")

    exclusions_raw = input(
        "enter item numbers to exclude into not_spam (comma-separated, blank for none): "
    )
    try:
        excluded = parse_exclusion_indexes(exclusions_raw, len(candidates))
    except ValueError as exc:
        raise UsageError(str(exc)) from exc

    decision = make_identify_decision(candidates, excluded)
    if not decision.add_to_spam and not decision.add_to_not_spam:
        print("no list updates requested")
        return 0

    print(
        f"review: add_to_spam={len(decision.add_to_spam)} "
        f"add_to_not_spam={len(decision.add_to_not_spam)}"
    )
    confirm = input("confirm update config? [y/N]: ").strip().lower()
    if confirm != "y":
        print("skipped by user")
        return 0

    merged_spam = _merge_unique(account.spam_senders, decision.add_to_spam)
    merged_not_spam = _merge_unique(account.not_spam_senders, decision.add_to_not_spam)
    update_account_sender_lists(
        config.path,
        {
            preset: {
                "spam_senders": merged_spam,
                "not_spam_senders": merged_not_spam,
            }
        },
    )
    print(
        f"updated: +{len(decision.add_to_spam)} spam, +{len(decision.add_to_not_spam)} not_spam"
    )
    print(
        f"si complete: spam_added={len(decision.add_to_spam)} "
        f"not_spam_added={len(decision.add_to_not_spam)}"
    )
    return 0


def _handle_spam_clean(account, service) -> int:
    result = run_cleanup_for_account(service, account)
    print(
        f"trashed_spam={result.trashed_spam} "
        f"marked_not_spam_read={result.marked_not_spam_read}"
    )
    print(
        f"sc complete: trashed_spam={result.trashed_spam} "
        f"marked_not_spam_read={result.marked_not_spam_read}"
    )
    return 0


def _handle_mark_read(service, params: list[str]) -> int:
    if len(params) != 1:
        raise UsageError("-mr requires exactly 1 param: <message_id>")
    message_id = params[0]
    response = mark_message_read(service, message_id)
    print(f"marked_read message_id={response.get('id')} thread_id={response.get('threadId')}")
    return 0


def _handle_delete(service, params: list[str]) -> int:
    if len(params) != 1:
        raise UsageError("-d requires exactly 1 param: <message_id>")
    message_id = params[0]
    delete_message(service, message_id)
    print(f"deleted message_id={message_id}")
    return 0


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


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        _print_usage_guide()
        return 0
    first = argv[0].lower()
    preset_required_commands = {"s", "-s", "ls", "r", "si", "sc", "-mr", "-d"}
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
    service = build_gmail_service(account)
    signature = _read_signature(account.signature_file)

    command = args.command.lower()
    if command in {"s", "-s"}:
        return _handle_send(service, account.email, args.params, signature)

    if command == "ls":
        return _handle_list(
            service,
            args.params,
            config.default_list_limit,
            account.email,
            config_path=config.path,
            account=account,
        )

    if command == "r":
        return _handle_reply(service, account.email, args.params, signature)

    if command == "-mr":
        return _handle_mark_read(service, args.params)

    if command == "-d":
        return _handle_delete(service, args.params)

    if command == "si":
        if args.params:
            raise UsageError("si does not accept extra args. Use: gmail <preset> si")
        return _handle_spam_identify(config, account, service)

    if command == "sc":
        if args.params:
            raise UsageError("sc does not accept extra args. Use: gmail <preset> sc")
        return _handle_spam_clean(account, service)

    raise UsageError(f"Unknown command '{args.command}'. Use s, ls, r, -mr, -d, si, or sc.")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except GmailCliError as exc:
        print(f"error: {exc.message}", file=sys.stderr)
        raise SystemExit(exc.exit_code)
