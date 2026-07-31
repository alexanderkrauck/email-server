"""A malformed Date header must not reach the column search sorts on."""

from datetime import datetime, timezone

from src.email.message_dates import EARLIEST_PLAUSIBLE, is_plausible, parse_header_date

NOW = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)


def test_ordinary_headers_are_parsed_with_their_offset():
    parsed = parse_header_date("Wed, 15 Jan 2025 10:30:00 +0000", now=NOW)

    assert parsed == datetime(2025, 1, 15, 10, 30, tzinfo=timezone.utc)


def test_implausible_years_are_discarded_rather_than_stored():
    # This is the shape that put a year of 2611 into the live index. It is a
    # perfectly valid RFC 5322 date, so exception handling alone never saw it.
    assert parse_header_date("Fri, 24 Sep 2611 09:12:00 +0200", now=NOW) is None
    assert parse_header_date("Mon, 01 Jan 1900 00:00:00 +0000", now=NOW) is None
    assert parse_header_date("Sat, 12 Mar 2124 08:00:00 +0000", now=NOW) is None


def test_missing_and_unparseable_headers_are_discarded():
    for raw in (None, "", "not a date", "Wed, 99 Foo 2025 10:30:00 +0000"):
        assert parse_header_date(raw, now=NOW) is None


def test_a_slightly_fast_sender_clock_is_still_accepted():
    """Skewed clocks are ordinary; a runaway year is not."""
    assert parse_header_date("Thu, 31 Jul 2026 12:00:00 +0000", now=NOW) is not None
    assert parse_header_date("Mon, 10 Aug 2026 12:00:00 +0000", now=NOW) is None


def test_naive_headers_are_treated_as_utc_rather_than_rejected():
    parsed = parse_header_date("Wed, 15 Jan 2025 10:30:00", now=NOW)

    assert parsed is not None
    assert parsed.tzinfo is not None


def test_plausibility_boundaries():
    assert not is_plausible(EARLIEST_PLAUSIBLE.replace(year=1979), now=NOW)
    assert is_plausible(EARLIEST_PLAUSIBLE, now=NOW)
    assert is_plausible(NOW, now=NOW)
