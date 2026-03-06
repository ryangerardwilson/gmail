from __future__ import annotations

import shlex
from dataclasses import dataclass

from .errors import UsageError


@dataclass(frozen=True)
class ParsedQuery:
    gmail_query: str
    max_results: int | None


def parse_declarative_query(query: str, default_limit: int) -> ParsedQuery:
    if not query.strip():
        raise UsageError("Search query cannot be empty")

    tokens = shlex.split(query)
    terms: list[str] = []
    max_results: int | None = None

    i = 0
    while i < len(tokens):
        token = tokens[i].lower()

        if token == "unread":
            terms.append("is:unread")
            i += 1
            continue

        if token == "contains":
            # Declarative alias for a broad term search across Gmail indexed fields.
            if i + 1 < len(tokens):
                terms.append(tokens[i + 1])
                i += 2
            else:
                i += 1
            continue

        if token in {"from", "to", "subject", "after", "before", "limit"}:
            if i + 1 >= len(tokens):
                raise UsageError(f"Missing value for '{token}' in query: {query}")
            value = tokens[i + 1]
            if token == "limit":
                try:
                    parsed = int(value)
                except ValueError as exc:
                    raise UsageError(f"Invalid limit '{value}' in query: {query}") from exc
                if parsed <= 0:
                    raise UsageError(f"Limit must be > 0, got {parsed}")
                max_results = parsed
            elif token == "subject":
                terms.append(f"subject:({value})")
            else:
                terms.append(f"{token}:{value}")
            i += 2
            continue

        terms.append(tokens[i])
        i += 1

    return ParsedQuery(gmail_query=" ".join(terms), max_results=max_results)
