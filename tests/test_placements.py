"""A message may sit in several folders at once and must survive moving."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from src.models.base import Base
from src.models.email import EmailLog
from src.models.placement import MessagePlacement
from src.models.smtp_config import SMTPConfig
from src.models.user import User


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")

    # SQLite ignores ON DELETE CASCADE unless foreign keys are switched on.
    @event.listens_for(engine, "connect")
    def _foreign_keys(dbapi_connection, _record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


def make_message(db, identity="<abc@example.com>"):
    if not db.query(User).first():
        db.add(User(id=1, google_sub="owner", email="owner@example.com"))
        db.add(
            SMTPConfig(
                id=1,
                owner_user_id=1,
                provider="imap",
                auth_type="password",
                name="Personal",
                account_name="a@example.com",
                host="imap.example.com",
                port=993,
                smtp_host="smtp.example.com",
                smtp_port=465,
                username="a@example.com",
            )
        )
    message = EmailLog(
        smtp_config_id=1,
        sender="a@example.com",
        recipient="b@example.com",
        subject="Invoice",
        provider_message_id=identity,
        message_id=identity,
        email_date=datetime(2025, 3, 4, tzinfo=timezone.utc),
    )
    db.add(message)
    db.commit()
    return message


def test_a_message_can_be_placed_in_several_folders(db):
    message = make_message(db)
    db.add_all(
        [
            MessagePlacement(email_log_id=message.id, folder="INBOX", uid=7, uid_validity=1),
            MessagePlacement(email_log_id=message.id, folder="INBOX.Sent", uid=9, uid_validity=1),
        ]
    )
    db.commit()

    assert {p.folder for p in db.query(MessagePlacement).all()} == {"INBOX", "INBOX.Sent"}


def test_one_placement_per_folder(db):
    message = make_message(db)
    db.add(MessagePlacement(email_log_id=message.id, folder="INBOX", uid=7, uid_validity=1))
    db.commit()
    db.add(MessagePlacement(email_log_id=message.id, folder="INBOX", uid=8, uid_validity=1))

    with pytest.raises(IntegrityError):
        db.commit()


def test_deleting_a_message_removes_its_placements(db):
    message = make_message(db)
    db.add(MessagePlacement(email_log_id=message.id, folder="INBOX", uid=7, uid_validity=1))
    db.commit()

    db.delete(message)
    db.commit()

    assert db.query(MessagePlacement).count() == 0


def test_the_same_message_seen_in_a_second_folder_adds_a_placement(db):
    from src.email.email_processor import upsert_placement

    message = make_message(db)
    upsert_placement(db, message.id, "INBOX", 7, 1)
    upsert_placement(db, message.id, "INBOX.Archive", 12, 1)
    db.commit()

    assert db.query(MessagePlacement).count() == 2


def test_reseeing_a_message_in_the_same_folder_updates_the_uid(db):
    from src.email.email_processor import upsert_placement

    message = make_message(db)
    upsert_placement(db, message.id, "INBOX", 7, 1)
    db.commit()
    upsert_placement(db, message.id, "INBOX", 99, 2)
    db.commit()

    placement = db.query(MessagePlacement).one()
    assert (placement.uid, placement.uid_validity) == (99, 2)


def test_a_move_keeps_the_row_and_relocates_the_placement(db):
    """The old model deleted the row and re-created it, losing extracted text."""
    from src.email.email_processor import apply_folder_snapshots

    message = make_message(db)
    db.add(MessagePlacement(email_log_id=message.id, folder="INBOX", uid=7, uid_validity=1))
    db.commit()
    original_id = message.id

    apply_folder_snapshots(
        db,
        config_id=1,
        snapshots={"INBOX": {"uid_validity": 1, "uids": set(), "flags": {}}},
    )
    db.commit()

    assert db.query(EmailLog).count() == 1
    surviving = db.query(EmailLog).one()
    assert surviving.id == original_id
    assert surviving.deleted_at is not None  # tombstoned, not destroyed


def test_a_message_that_never_had_a_placement_is_never_touched(db):
    """22,686 production rows predate the folder column and have no placement."""
    from src.email.email_processor import apply_folder_snapshots

    make_message(db)
    db.commit()

    apply_folder_snapshots(
        db,
        config_id=1,
        snapshots={"INBOX": {"uid_validity": 1, "uids": set(), "flags": {}}},
    )
    db.commit()

    assert db.query(EmailLog).count() == 1
    assert db.query(EmailLog).one().deleted_at is None


def test_a_uidvalidity_change_does_not_wipe_the_folder(db):
    """Renumbering is not deletion; the current reconciler already guards this."""
    from src.email.email_processor import apply_folder_snapshots

    message = make_message(db)
    db.add(MessagePlacement(email_log_id=message.id, folder="INBOX", uid=7, uid_validity=1))
    db.commit()

    apply_folder_snapshots(
        db,
        config_id=1,
        snapshots={"INBOX": {"uid_validity": 2, "uids": {99}, "flags": {}}},
    )
    db.commit()

    assert db.query(EmailLog).one().deleted_at is None


def test_a_message_still_present_upstream_is_not_tombstoned(db):
    from src.email.email_processor import apply_folder_snapshots

    message = make_message(db)
    db.add(MessagePlacement(email_log_id=message.id, folder="INBOX", uid=7, uid_validity=1))
    db.commit()

    apply_folder_snapshots(
        db,
        config_id=1,
        snapshots={"INBOX": {"uid_validity": 1, "uids": {7}, "flags": {7: "\\Seen"}}},
    )
    db.commit()

    assert db.query(EmailLog).one().deleted_at is None
    assert db.query(EmailLog).one().flags == "\\Seen"


def _tombstone(db, age_days: int):
    from src.config import settings

    message = make_message(db)
    now = datetime.now(tz=timezone.utc)
    message.deleted_at = now - timedelta(days=settings.tombstone_grace_days + age_days)
    account = db.query(SMTPConfig).one()
    account.sync_state, account.backfill_complete = "healthy", True
    db.commit()
    return now


def test_a_tombstone_is_reaped_after_the_grace_period(db):
    from src.email.email_processor import reap_tombstoned_messages

    now = _tombstone(db, age_days=1)

    assert reap_tombstoned_messages(db, config_id=1, now=now) == 1
    db.commit()
    assert db.query(EmailLog).count() == 0


def test_a_fresh_tombstone_is_kept_so_a_wrong_inference_is_recoverable(db):
    from src.email.email_processor import reap_tombstoned_messages

    message = make_message(db)
    now = datetime.now(tz=timezone.utc)
    message.deleted_at = now
    account = db.query(SMTPConfig).one()
    account.sync_state, account.backfill_complete = "healthy", True
    db.commit()

    assert reap_tombstoned_messages(db, config_id=1, now=now) == 0
    assert db.query(EmailLog).count() == 1


def test_an_unhealthy_account_is_never_reaped(db):
    """A partial view of a mailbox is what produces a wrong inference."""
    from src.email.email_processor import reap_tombstoned_messages

    now = _tombstone(db, age_days=1)
    account = db.query(SMTPConfig).one()
    account.sync_state, account.backfill_complete = "error", False
    db.commit()

    assert reap_tombstoned_messages(db, config_id=1, now=now) == 0
    assert db.query(EmailLog).count() == 1


def test_a_short_census_is_refused_rather_than_tombstoning_the_account(db):
    """An incomplete folder listing looks exactly like an emptied mailbox."""
    from src.email.email_processor import apply_folder_snapshots

    for index in range(25):
        message = make_message(db, identity=f"<bulk{index}@example.com>")
        db.add(
            MessagePlacement(
                email_log_id=message.id, folder="INBOX", uid=index + 1, uid_validity=1
            )
        )
    db.commit()

    # Every placement is still there, but the census came back empty.
    apply_folder_snapshots(
        db,
        config_id=1,
        snapshots={"INBOX": {"uid_validity": 1, "uids": set(), "flags": {}}},
    )

    assert db.query(EmailLog).filter(EmailLog.deleted_at.is_not(None)).count() == 0


def test_a_handful_of_real_deletions_still_tombstones(db):
    """The circuit breaker must not disable ordinary deletion mirroring."""
    from src.email.email_processor import apply_folder_snapshots

    for index in range(25):
        message = make_message(db, identity=f"<bulk{index}@example.com>")
        db.add(
            MessagePlacement(
                email_log_id=message.id, folder="INBOX", uid=index + 1, uid_validity=1
            )
        )
    db.commit()

    # All but two are still upstream.
    apply_folder_snapshots(
        db,
        config_id=1,
        snapshots={
            "INBOX": {"uid_validity": 1, "uids": set(range(1, 24)), "flags": {}}
        },
    )

    assert db.query(EmailLog).filter(EmailLog.deleted_at.is_not(None)).count() == 2


def test_a_stale_uidvalidity_placement_is_retired_when_the_message_is_elsewhere(db):
    """A renumbered folder leaves placements naming UIDs that no longer exist."""
    from src.email.email_processor import apply_folder_snapshots

    message = make_message(db)
    db.add_all(
        [
            MessagePlacement(email_log_id=message.id, folder="INBOX", uid=7, uid_validity=1),
            MessagePlacement(
                email_log_id=message.id, folder="INBOX.Trash", uid=3, uid_validity=9
            ),
        ]
    )
    db.commit()

    apply_folder_snapshots(
        db,
        config_id=1,
        snapshots={
            "INBOX": {"uid_validity": 9, "uids": set(), "flags": {}},
            "INBOX.Trash": {"uid_validity": 9, "uids": {3}, "flags": {}},
        },
    )

    assert {p.folder for p in db.query(MessagePlacement).all()} == {"INBOX.Trash"}
    assert db.query(EmailLog).one().deleted_at is None


def test_a_stale_placement_is_kept_when_it_is_the_only_one(db):
    """Retiring it would unplace the message and make it look deleted."""
    from src.email.email_processor import apply_folder_snapshots

    message = make_message(db)
    db.add(MessagePlacement(email_log_id=message.id, folder="INBOX", uid=7, uid_validity=1))
    db.commit()

    apply_folder_snapshots(
        db,
        config_id=1,
        snapshots={"INBOX": {"uid_validity": 9, "uids": {4}, "flags": {}}},
    )

    assert db.query(MessagePlacement).count() == 1
    assert db.query(EmailLog).one().deleted_at is None
