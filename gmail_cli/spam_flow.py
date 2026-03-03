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
        if count > threshold and sender.lower() not in spam_set and not sender.lower().endswith("@gmail.com")
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


def run_cleanup_for_account(service, account: AccountConfig, progress_callback=None) -> SpamCleanupResult:
    sender_groups = _chunk_senders(account.spam_senders, 25)
    if not sender_groups:
        return SpamCleanupResult(trashed_spam=0)

    all_ids: set[str] = set()
    total_groups = len(sender_groups)
    if progress_callback is not None:
        progress_callback("groups_total", total_groups)
    for index, group in enumerate(sender_groups, start=1):
        query = _spam_group_query(group)
        ids = list_message_ids(service, query)
        all_ids.update(ids)
        if progress_callback is not None:
            progress_callback("group_processed", index, total_groups, len(ids), len(all_ids))

    trashed = batch_delete_messages(service, sorted(all_ids))
    if progress_callback is not None:
        progress_callback("trashed_total", trashed)
    return SpamCleanupResult(trashed_spam=trashed)


def _chunk_senders(senders: list[str], size: int) -> list[list[str]]:
    if size <= 0:
        return [senders]
    return [senders[i : i + size] for i in range(0, len(senders), size)]


def _spam_group_query(senders: list[str]) -> str:
    if len(senders) == 1:
        return f"from:{senders[0]}"
    sender_terms = " OR ".join(f"from:{sender}" for sender in senders)
    return f"({sender_terms})"
