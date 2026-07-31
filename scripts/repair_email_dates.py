"""Clear implausible email_date values left by malformed Date headers.

Messages synced before plausibility checking could store any year a sender's
header claimed. Search sorts and paginates on email_date, so those rows sit at
the head of every date-ordered page and distort range filters.

    docker compose exec email-server python -m scripts.repair_email_dates
    docker compose exec email-server python -m scripts.repair_email_dates --apply

Without --apply this only reports. The column is nullable and a null date is
excluded from range filters, which is the honest representation of a date that
was never known.
"""

import argparse

from sqlalchemy import func, or_, select, update

from src.database.connection import SessionLocal
from src.email.message_dates import EARLIEST_PLAUSIBLE, FUTURE_TOLERANCE
from src.models.email import EmailLog


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write the change instead of reporting it")
    args = parser.parse_args()

    horizon = func.now() + FUTURE_TOLERANCE
    implausible = or_(EmailLog.email_date < EARLIEST_PLAUSIBLE, EmailLog.email_date > horizon)

    with SessionLocal() as db:
        rows = db.execute(
            select(EmailLog.id, EmailLog.email_date, EmailLog.subject)
            .where(EmailLog.email_date.is_not(None), implausible)
            .order_by(EmailLog.email_date)
        ).all()

        if not rows:
            print("No implausible email_date values found.")
            return

        print(f"{len(rows)} message(s) with an implausible date:")
        for message_id, date, subject in rows[:20]:
            print(f"  id={message_id:<8} {date}  {(subject or '')[:60]!r}")
        if len(rows) > 20:
            print(f"  ... and {len(rows) - 20} more")

        if not args.apply:
            print("\nRe-run with --apply to clear these dates.")
            return

        cleared = db.execute(
            update(EmailLog)
            .where(EmailLog.email_date.is_not(None), implausible)
            .values(email_date=None)
        ).rowcount
        db.commit()
        print(f"\nCleared {cleared} date(s).")


if __name__ == "__main__":
    main()
