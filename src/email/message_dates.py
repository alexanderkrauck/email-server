"""Plausibility-checked parsing of RFC 5322 date headers.

`parsedate_to_datetime` raises only on syntactically invalid input. A header
that is well formed but absurd, such as a year of 2611, parses successfully and
would otherwise be stored as-is. Search sorts and paginates on this column, so a
single bad value sits at the head of every date-ordered page and silently
distorts range filters.
"""

from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

# Predates SMTP itself, so anything earlier is a parsing artifact rather than mail.
EARLIEST_PLAUSIBLE = datetime(1980, 1, 1, tzinfo=timezone.utc)
# Senders with a skewed clock are common; a runaway year is not.
FUTURE_TOLERANCE = timedelta(days=2)


def is_plausible(value: datetime, *, now: datetime | None = None) -> bool:
    """Report whether a parsed date could belong to a real message."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    reference = now or datetime.now(tz=timezone.utc)
    return EARLIEST_PLAUSIBLE <= value <= reference + FUTURE_TOLERANCE


def parse_header_date(raw: str | None, *, now: datetime | None = None) -> datetime | None:
    """Parse a Date header, returning None when it is missing or implausible."""
    if not raw:
        return None
    try:
        parsed = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed if is_plausible(parsed, now=now) else None
