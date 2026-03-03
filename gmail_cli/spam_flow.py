from __future__ import annotations

from dataclasses import dataclass

from .config import AccountConfig
from .gmail_api import (
    batch_delete_messages,
    batch_mark_messages_read,
    list_message_ids,
    unread_sender_counts_non_gmail,
)


@dataclass(frozen=True)
class SenderCount:
    sender: str
    unread_count: int


@dataclass(frozen=True)
class IdentifyDecision:
    add_to_spam: list[str]
    add_to_not_spam: list[str]


@dataclass(frozen=True)
class SpamCleanupResult:
    trashed_spam: int
    marked_not_spam_read: int


def select_spam_candidates(
    counts: dict[str, int],
    existing_spam: list[str],
    existing_not_spam: list[str],
    threshold: int = 5,
) -> list[SenderCount]:
    spam_set = {item.lower() for item in existing_spam}
    not_spam_set = {item.lower() for item in existing_not_spam}
    selected = [
        SenderCount(sender=sender, unread_count=count)
        for sender, count in counts.items()
        if count > threshold and sender.lower() not in spam_set and sender.lower() not in not_spam_set
    ]
    return sorted(selected, key=lambda item: (-item.unread_count, item.sender))


def parse_exclusion_indexes(raw: str, max_index: int) -> set[int]:
    text = raw.strip()
    if not text:
        return set()
    values: set[int] = set()
    for chunk in text.split(","):
        token = chunk.strip()
        if not token:
            continue
        parsed = int(token)
        if parsed < 1 or parsed > max_index:
            raise ValueError(f"Index out of range: {parsed}. Valid range is 1..{max_index}")
        values.add(parsed)
    return values


def run_identify_for_account(service, account: AccountConfig, progress_callback=None) -> list[SenderCount]:
    counts = unread_sender_counts_non_gmail(service, progress_callback=progress_callback)
    return select_spam_candidates(
        counts=counts,
        existing_spam=account.spam_senders,
        existing_not_spam=account.not_spam_senders,
        threshold=5,
    )


def make_identify_decision(candidates: list[SenderCount], excluded_indexes: set[int]) -> IdentifyDecision:
    excluded_senders = {
        item.sender for index, item in enumerate(candidates, start=1) if index in excluded_indexes
    }
    add_to_not_spam = sorted(excluded_senders)
    add_to_spam = sorted(item.sender for item in candidates if item.sender not in excluded_senders)
    return IdentifyDecision(add_to_spam=add_to_spam, add_to_not_spam=add_to_not_spam)


def run_cleanup_for_account(service, account: AccountConfig) -> SpamCleanupResult:
    trashed = 0
    for sender in account.spam_senders:
        query = f'is:unread from:{sender}'
        trashed += batch_delete_messages(service, list_message_ids(service, query))

    marked_read = 0
    for sender in account.not_spam_senders:
        query = f'is:unread from:{sender}'
        marked_read += batch_mark_messages_read(service, list_message_ids(service, query))

    return SpamCleanupResult(trashed_spam=trashed, marked_not_spam_read=marked_read)
