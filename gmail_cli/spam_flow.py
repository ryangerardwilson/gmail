from __future__ import annotations

from dataclasses import dataclass

from .config import AccountConfig
from .gmail_api import (
    batch_delete_messages,
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


@dataclass(frozen=True)
class SpamCleanupResult:
    trashed_spam: int


def select_spam_candidates(
    counts: dict[str, int],
    existing_spam: list[str],
    threshold: int = 5,
) -> list[SenderCount]:
    spam_set = {item.lower() for item in existing_spam}
    selected = [
        SenderCount(sender=sender, unread_count=count)
        for sender, count in counts.items()
        if count > threshold and sender.lower() not in spam_set
    ]
    return sorted(selected, key=lambda item: (-item.unread_count, item.sender))


def run_identify_for_account(service, account: AccountConfig, progress_callback=None) -> list[SenderCount]:
    counts = unread_sender_counts_non_gmail(service, progress_callback=progress_callback)
    return select_spam_candidates(
        counts=counts,
        existing_spam=account.spam_senders,
        threshold=5,
    )


def make_identify_decision(candidates: list[SenderCount]) -> IdentifyDecision:
    return IdentifyDecision(add_to_spam=sorted(item.sender for item in candidates))


def run_cleanup_for_account(service, account: AccountConfig) -> SpamCleanupResult:
    trashed = 0
    for sender in account.spam_senders:
        query = f'is:unread from:{sender}'
        trashed += batch_delete_messages(service, list_message_ids(service, query))
    return SpamCleanupResult(trashed_spam=trashed)
