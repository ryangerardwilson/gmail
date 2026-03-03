from __future__ import annotations

import argparse
import sys

from gmail_cli.auth import build_gmail_service
from gmail_cli.config import get_account, load_config
from gmail_cli.errors import ConfigError, GmailCliError, UsageError
from gmail_cli.formatters import render_messages_table
from gmail_cli.gmail_api import (
    get_thread_messages,
    list_messages,
    reply_to_message,
    send_email,
)
from gmail_cli.query_parser import parse_declarative_query


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Declarative Gmail CLI",
        usage=(
            "python main.py <preset> s <to> <subject> <body>\n"
            "python main.py <preset> ls <query>\n"
            "python main.py <preset> ls -t <thread_id>\n"
            "python main.py <preset> r <message_id> <body>"
        ),
    )
    parser.add_argument("preset", help="Account preset key from config.json, e.g. 1")
    parser.add_argument("command", help="Command: s | -s | ls | r")
    parser.add_argument("params", nargs=argparse.REMAINDER, help="Command parameters")
    return parser


def _print_usage_guide() -> None:
    print(
        "\n".join(
            [
                "Gmail CLI Usage",
                "",
                "  python main.py <preset> s <to> <subject> <body>",
                "  python main.py <preset> ls <query>",
                "  python main.py <preset> ls -t <thread_id>",
                "  python main.py <preset> r <message_id> <body>",
                "",
                "Examples:",
                "  python main.py 1 s \"xyz@example.com\" \"Hello\" \"Body\"",
                "  python main.py 1 ls \"contains jake limit 1\"",
                "  python main.py 1 ls \"to silvia limit 1\"",
                "  python main.py 1 ls -t \"19ca756c06a7ebcd\"",
                "  python main.py 1 r \"19caef2cd6494116\" \"Thanks for the update.\"",
            ]
        )
    )


def _handle_send(service, from_email: str, params: list[str]) -> int:
    if len(params) != 3:
        raise UsageError("Send requires 3 params: <to> <subject> <body>")

    to_email, subject, body = params
    response = send_email(service, from_email, to_email, subject, body)
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


def _handle_reply(service, from_email: str, params: list[str]) -> int:
    if len(params) != 2:
        raise UsageError("Reply requires 2 params: <message_id> <body>")

    message_id, body = params
    response = reply_to_message(service, from_email, message_id, body)
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


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if not argv:
        _print_usage_guide()
        return 0

    parser = _build_parser()
    args = parser.parse_args(argv)

    config = load_config()
    account = get_account(config, args.preset)
    service = build_gmail_service(account)
    signature = _read_signature(account.signature_file)

    command = args.command.lower()
    if command in {"s", "-s"}:
        if len(args.params) == 3:
            args.params[2] = _append_signature(args.params[2], signature)
        return _handle_send(service, account.email, args.params)

    if command == "ls":
        return _handle_list(service, args.params, config.default_list_limit, account.email)

    if command == "r":
        if len(args.params) == 2:
            args.params[1] = _append_signature(args.params[1], signature)
        return _handle_reply(service, account.email, args.params)

    raise UsageError(f"Unknown command '{args.command}'. Use s, ls, or r.")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except GmailCliError as exc:
        print(f"error: {exc.message}", file=sys.stderr)
        raise SystemExit(exc.exit_code)
