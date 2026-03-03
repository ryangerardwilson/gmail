from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from gmail_cli.auth import build_gmail_service
from gmail_cli.config import get_account, load_config
from gmail_cli.errors import ConfigError, GmailCliError, UsageError
from gmail_cli.formatters import render_messages_table
from gmail_cli.gmail_api import (
    get_thread_messages,
    list_messages,
    reply_to_message,
    reply_to_thread,
    send_email,
)
from gmail_cli.query_parser import parse_declarative_query

__version__ = "0.1.0"
_TRAILING_OPTIONS = {"-cc", "-bcc", "-atch"}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Declarative Gmail CLI",
        usage=(
            "python main.py -v\n"
            "python main.py -u\n"
            "python main.py <preset> s <to> <subject> <body> [-cc <emails>] [-bcc <emails>] [-atch <path> [<path> ...]]\n"
            "python main.py <preset> ls <query>\n"
            "python main.py <preset> ls -t <thread_id>\n"
            "python main.py <preset> r [-a] <message_id> <body> [-cc <emails>] [-bcc <emails>] [-atch <path> [<path> ...]]\n"
            "python main.py <preset> r [-a] -t <thread_id> <body> [-cc <emails>] [-bcc <emails>] [-atch <path> [<path> ...]]"
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
    parser.add_argument("command", nargs="?", help="Command: s | -s | ls | r")
    parser.add_argument("params", nargs=argparse.REMAINDER, help="Command parameters")
    return parser


def _print_usage_guide() -> None:
    print(
        "\n".join(
            [
                "Gmail CLI Usage",
                "",
                "  python main.py -v",
                "  python main.py -u",
                "  python main.py <preset> s <to> <subject> <body> [-cc <emails>] [-bcc <emails>] [-atch <path> [<path> ...]]",
                "  python main.py <preset> ls <query>",
                "  python main.py <preset> ls -t <thread_id>",
                "  python main.py <preset> r [-a] <message_id> <body> [-cc <emails>] [-bcc <emails>] [-atch <path> [<path> ...]]",
                "  python main.py <preset> r [-a] -t <thread_id> <body> [-cc <emails>] [-bcc <emails>] [-atch <path> [<path> ...]]",
                "",
                "Examples:",
                "  python main.py 1 s \"xyz@example.com\" \"Hello\" \"Body\"",
                "  python main.py 1 s \"xyz@example.com\" \"Hello\" \"Body\" -cc \"cc1@example.com,cc2@example.com\" -bcc \"audit@example.com\"",
                "  python main.py 1 s \"xyz@example.com\" \"Hello\" \"Body\" -atch \"/tmp/notes.txt\"",
                "  python main.py 1 s \"xyz@example.com\" \"Hello\" \"Body\" -atch \"/tmp/notes.txt\" \"/tmp/project_dir\"",
                "  python main.py 1 ls \"contains jake limit 1\"",
                "  python main.py 1 ls \"to silvia limit 1\"",
                "  python main.py 1 ls -t \"19ca756c06a7ebcd\"",
                "  python main.py 1 r \"19caef2cd6494116\" \"Thanks for the update.\"",
                "  python main.py 1 r -a \"19caef2cd6494116\" \"Thanks all.\"",
                "  python main.py 1 r \"19caef2cd6494116\" \"Adding context.\" -cc \"manager@example.com\"",
                "  python main.py 1 r \"19caef2cd6494116\" \"Please review.\" -atch \"/tmp/project_dir\"",
                "  python main.py 1 r -a \"19caef2cd6494116\" \"Please review.\" -atch \"/tmp/notes.txt\" \"/tmp/project_dir\"",
                "  python main.py 1 r -t \"19ca756c06a7ebcd\" \"Following up on this thread.\"",
                "  python main.py 1 r -ta \"19ca756c06a7ebcd\" \"Thanks everyone.\"",
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


def _handle_list(service, params: list[str], default_limit: int, my_email: str) -> int:
    if not params:
        raise UsageError("ls requires a query string, e.g. \"from maanas limit 1\"")

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

    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.upgrade:
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
        return _handle_list(service, args.params, config.default_list_limit, account.email)

    if command == "r":
        return _handle_reply(service, account.email, args.params, signature)

    raise UsageError(f"Unknown command '{args.command}'. Use s, ls, or r.")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except GmailCliError as exc:
        print(f"error: {exc.message}", file=sys.stderr)
        raise SystemExit(exc.exit_code)
