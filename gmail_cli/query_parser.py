from __future__ import annotations

from dataclasses import dataclass

from .errors import UsageError


@dataclass(frozen=True)
class ParsedListQuery:
    gmail_query: str
    max_results: int | None


def parse_list_query_args(
    params: list[str],
    default_limit: int,
    *,
    base_terms: list[str] | None = None,
    require_filter_or_limit: bool = True,
) -> ParsedListQuery:
    terms = list(base_terms or [])
    max_results: int | None = None
    saw_user_filter = False

    i = 0
    while i < len(params):
        token = params[i]

        if token == "-f":
            if i + 1 >= len(params):
                raise UsageError("ls -f requires: <from>")
            terms.append(f"from:{params[i + 1]}")
            saw_user_filter = True
            i += 2
            continue

        if token == "-c":
            if i + 1 >= len(params):
                raise UsageError("ls -c requires: <contains>")
            terms.append(params[i + 1])
            saw_user_filter = True
            i += 2
            continue

        if token.startswith("-"):
            raise UsageError(f"Unknown ls option '{token}'. Supported: [limit], -f <from>, -c <contains>")

        try:
            parsed_limit = int(token)
        except ValueError as exc:
            raise UsageError(
                "ls supports only [limit], -f <from>, and -c <contains>"
            ) from exc
        if parsed_limit <= 0:
            raise UsageError(f"ls limit must be > 0, got {parsed_limit}")
        if max_results is not None:
            raise UsageError("ls accepts only one positional [limit]")
        max_results = parsed_limit
        saw_user_filter = True
        i += 1

    if require_filter_or_limit and not saw_user_filter:
        raise UsageError("ls requires [limit], -f <from>, or -c <contains>")

    if max_results is None and saw_user_filter:
        max_results = default_limit

    return ParsedListQuery(gmail_query=" ".join(terms), max_results=max_results)
