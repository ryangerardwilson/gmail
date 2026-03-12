from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from datetime import timedelta
import re

from .errors import UsageError


_RELATIVE_TIME_RE = re.compile(r"^(?P<amount>\d+)(?P<unit>[dwmy])$", re.IGNORECASE)
_ISO_MONTH_RE = re.compile(r"^(?P<year>\d{4})-(?P<month>\d{2})$")
_ISO_DATE_RE = re.compile(r"^(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})$")
_NAMED_MONTH_RE = re.compile(r"^(?P<month>[A-Za-z]+)[ -]+(?P<year>\d{4})$", re.IGNORECASE)
_MONTH_NAMES = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}
_TIME_LIMIT_SHAPE = '2w | 14d | 3m | 1y | 2025-01 | "jan 2025" | 2025-01-10 | 2025-01-10..2025-01-20'


@dataclass(frozen=True)
class ParsedListQuery:
    gmail_query: str
    max_results: int | None


def _month_bounds(year: int, month: int) -> tuple[date, date]:
    start = date(year, month, 1)
    if month == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month + 1, 1)
    return start, next_month - timedelta(days=1)


def _format_gmail_date(value: date) -> str:
    return value.strftime("%Y/%m/%d")


def _gmail_inclusive_range(start: date, end: date) -> str:
    if end < start:
        raise UsageError("ls -tl range end must be on or after start")
    return (
        f"after:{_format_gmail_date(start - timedelta(days=1))} "
        f"before:{_format_gmail_date(end + timedelta(days=1))}"
    )


def _parse_iso_date(value: str) -> date | None:
    match = _ISO_DATE_RE.match(value)
    if not match:
        return None
    year = int(match.group("year"))
    month = int(match.group("month"))
    day = int(match.group("day"))
    try:
        return date(year, month, day)
    except ValueError as exc:
        raise UsageError(f"Invalid ls -tl date '{value}'") from exc


def _parse_iso_month(value: str) -> tuple[date, date] | None:
    match = _ISO_MONTH_RE.match(value)
    if not match:
        return None
    year = int(match.group("year"))
    month = int(match.group("month"))
    try:
        return _month_bounds(year, month)
    except ValueError as exc:
        raise UsageError(f"Invalid ls -tl month '{value}'") from exc


def _parse_named_month(value: str) -> tuple[date, date] | None:
    match = _NAMED_MONTH_RE.match(value.strip())
    if not match:
        return None
    month_name = match.group("month").lower()
    month = _MONTH_NAMES.get(month_name)
    if month is None:
        raise UsageError(f"Invalid ls -tl month '{value}'")
    year = int(match.group("year"))
    return _month_bounds(year, month)


def parse_time_limit_expr(value: str) -> str:
    expr = value.strip()
    if not expr:
        raise UsageError("ls -tl requires: <time_limit>")

    if ".." in expr:
        start_raw, sep, end_raw = expr.partition("..")
        if not sep or not start_raw.strip() or not end_raw.strip():
            raise UsageError(f"ls -tl supports: {_TIME_LIMIT_SHAPE}")
        start = _parse_iso_date(start_raw.strip())
        end = _parse_iso_date(end_raw.strip())
        if start is None or end is None:
            raise UsageError("ls -tl date ranges must use: YYYY-MM-DD..YYYY-MM-DD")
        return _gmail_inclusive_range(start, end)

    relative_match = _RELATIVE_TIME_RE.match(expr)
    if relative_match:
        amount = int(relative_match.group("amount"))
        unit = relative_match.group("unit").lower()
        if amount <= 0:
            raise UsageError("ls -tl duration must be > 0")
        if unit == "w":
            return f"newer_than:{amount * 7}d"
        return f"newer_than:{amount}{unit}"

    month_bounds = _parse_iso_month(expr)
    if month_bounds is not None:
        start, end = month_bounds
        return _gmail_inclusive_range(start, end)

    named_month_bounds = _parse_named_month(expr)
    if named_month_bounds is not None:
        start, end = named_month_bounds
        return _gmail_inclusive_range(start, end)

    exact_date = _parse_iso_date(expr)
    if exact_date is not None:
        return _gmail_inclusive_range(exact_date, exact_date)

    raise UsageError(f"ls -tl supports: {_TIME_LIMIT_SHAPE}")


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

        if token == "-tl":
            if i + 1 >= len(params):
                raise UsageError("ls -tl requires: <time_limit>")
            terms.append(parse_time_limit_expr(params[i + 1]))
            saw_user_filter = True
            i += 2
            continue

        if token.startswith("-"):
            raise UsageError(
                f"Unknown ls option '{token}'. Supported: [limit], -f <from>, -c <contains>, -tl <time_limit>"
            )

        try:
            parsed_limit = int(token)
        except ValueError as exc:
            raise UsageError(
                "ls supports only [limit], -f <from>, -c <contains>, and -tl <time_limit>"
            ) from exc
        if parsed_limit <= 0:
            raise UsageError(f"ls limit must be > 0, got {parsed_limit}")
        if max_results is not None:
            raise UsageError("ls accepts only one positional [limit]")
        max_results = parsed_limit
        saw_user_filter = True
        i += 1

    if require_filter_or_limit and not saw_user_filter:
        raise UsageError("ls requires [limit], -f <from>, -c <contains>, or -tl <time_limit>")

    if max_results is None and saw_user_filter:
        max_results = default_limit

    return ParsedListQuery(gmail_query=" ".join(terms), max_results=max_results)
