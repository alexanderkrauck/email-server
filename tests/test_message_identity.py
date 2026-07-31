"""A message keeps one identity per account no matter which folder holds it."""

from src.email.message_identity import stable_identity


def _imap(**over):
    base = dict(
        uses_provider_ids=False,
        provider_message_id="INBOX:12345:678",
        message_id="<abc@example.com>",
        folder="INBOX",
        uid=678,
        uid_validity=12345,
    )
    base.update(over)
    return stable_identity(**base)


def test_identity_is_independent_of_folder_and_uid():
    assert _imap() == _imap(folder="Archive", uid=12, uid_validity=98765)


def test_message_id_is_normalised_including_stray_whitespace():
    assert _imap(message_id="  <ABC@Example.COM>\t\n") == _imap()


def test_gmail_api_messages_keep_their_provider_id():
    assert (
        stable_identity(
            uses_provider_ids=True,
            provider_message_id="18f2c0a1b",
            message_id="<abc@example.com>",
            folder="gmail",
            uid=None,
            uid_validity=None,
        )
        == "18f2c0a1b"
    )


def test_messages_without_a_usable_message_id_keep_a_location_identity():
    """No hash: a content hash would merge 67 genuinely distinct messages."""
    for absent in (None, "", "   ", "uid_678_3"):
        assert _imap(message_id=absent) == "loc:INBOX:12345:678"


def test_a_location_identity_is_distinct_per_location():
    assert _imap(message_id=None) != _imap(message_id=None, uid=679)
