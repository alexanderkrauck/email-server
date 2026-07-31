"""Read-only PostgreSQL integration checks for the mail search service."""

import re
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import func

from src.database.connection import SessionLocal
from src.models.attachment import EmailAttachment
from src.models.email import EmailLog
from src.models.participant import MailParticipant
from src.models.smtp_config import SMTPConfig
from src.models.user import User
from src.services.mail_service import (
    get_thread,
    mail_account_summary,
    search_mail,
    search_mail_regex,
)


def _attachment_phrase(text: str) -> tuple[str, str]:
    words = re.findall(r"[^\W\d_]+", text, flags=re.UNICODE)
    for index in range(len(words) - 2):
        selected = words[index : index + 3]
        if min(map(len, selected)) >= 4 and len(
            {word.lower() for word in selected}
        ) > 1:
            regex = r"\b" + r"\s+".join(re.escape(word) for word in selected) + r"\b"
            lexical = " ".join(selected)
            return regex, lexical
    raise AssertionError("No useful attachment phrase was available")


def main() -> None:
    db = SessionLocal()
    try:
        user = db.query(User).order_by(User.id).first()
        assert user is not None, "No testable user exists"
        inventory = mail_account_summary(db, user)

        # Trash is excluded by default; compare against the unfiltered inventory.
        first = search_mail(db, user, limit=37, deduplicate="none", exclude_folders=[])
        assert first["raw_count"] == inventory["total_message_count"]
        assert first["total_count"] == first["raw_count"]
        assert first["next_cursor"] and first["has_more"]
        second = search_mail(
            db,
            user,
            limit=37,
            cursor=first["next_cursor"],
            deduplicate="none",
            exclude_folders=[],
        )
        assert not {
            item["id"] for item in first["items"]
        }.intersection(item["id"] for item in second["items"])

        exact = search_mail(db, user, limit=5, deduplicate="exact")
        mirror = search_mail(db, user, limit=5, deduplicate="mirror")
        assert mirror["total_count"] <= exact["total_count"] <= first["raw_count"]

        participant = (
            db.query(MailParticipant)
            .join(EmailLog)
            .join(SMTPConfig)
            .filter(SMTPConfig.owner_user_id == user.id)
            .order_by(MailParticipant.id)
            .first()
        )
        assert participant is not None
        participant_page = search_mail(
            db,
            user,
            participants=[participant.domain],
            limit=5,
            deduplicate="none",
        )
        assert participant_page["total_count"] > 0
        assert participant_page["facets"]["participant_domains"]

        attachments = (
            db.query(EmailAttachment)
            .join(EmailLog)
            .join(SMTPConfig)
            .filter(
                SMTPConfig.owner_user_id == user.id,
                EmailLog.deleted_at.is_(None),
                EmailAttachment.text_content.is_not(None),
                func.length(EmailAttachment.text_content) > 200,
            )
            .order_by(EmailAttachment.id)
            .limit(250)
            .all()
        )
        attachment = None
        phrase_pattern = lexical_query = ""
        for candidate in attachments:
            try:
                phrase_pattern, lexical_query = _attachment_phrase(
                    candidate.text_content
                )
            except AssertionError:
                continue
            attachment = candidate
            break
        assert attachment is not None
        message_date = attachment.email_log.email_date
        assert message_date is not None
        date_from = message_date - timedelta(seconds=1)
        date_to = message_date + timedelta(seconds=1)
        attachment_page = search_mail(
            db,
            user,
            query=lexical_query,
            account_id=attachment.email_log.smtp_config_id,
            date_from=date_from,
            date_to=date_to,
            search_attachments=True,
            limit=100,
            deduplicate="none",
        )
        assert any(
            match["field"] == "attachment"
            for item in attachment_page["items"]
            for match in item["matches"]
        )

        regex_page = search_mail_regex(
            db,
            user,
            pattern=phrase_pattern,
            fields=["body", "attachment"],
            account_id=attachment.email_log.smtp_config_id,
            date_from=date_from,
            date_to=date_to,
            limit=50,
            deduplicate="none",
        )
        assert "attachment" in regex_page["fields"]
        assert any(
            match["field"] == "attachment"
            for item in regex_page["items"]
            for match in item["matches"]
        )
        try:
            search_mail_regex(
                db,
                user,
                pattern="[",
                account_id=attachment.email_log.smtp_config_id,
            )
        except HTTPException as exc:
            assert exc.status_code == 400
            assert str(exc.detail).startswith("Invalid regex")
        else:
            raise AssertionError("Invalid regex was accepted")

        reply = (
            db.query(EmailLog)
            .join(SMTPConfig)
            .filter(
                SMTPConfig.owner_user_id == user.id,
                EmailLog.in_reply_to.is_not(None),
                EmailLog.in_reply_to != "",
            )
            .order_by(EmailLog.id.desc())
            .first()
        )
        if reply is not None:
            thread = get_thread(db, user, reply.id, max_body_chars=500)
            assert thread["messages"]
            assert all(
                len(message.get("body_plain", "")) <= 500
                for message in thread["messages"]
            )

        print(
            "PostgreSQL search verification passed:",
            {
                "messages": inventory["total_message_count"],
                "exact": exact["total_count"],
                "mirror": mirror["total_count"],
                "accounts": inventory["account_count"],
            },
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
