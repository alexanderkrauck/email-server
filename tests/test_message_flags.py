"""IMAP and Gmail disagree about how, and whether, read state is recorded."""

from src.email.message_flags import UNKNOWN, normalize_flags


def test_imap_seen_is_read():
    state = normalize_flags(r"\Seen")

    assert state.is_unread is False


def test_imap_empty_flag_list_is_unread_not_unknown():
    """FLAGS () is a positive statement that the message carries no flags."""
    state = normalize_flags("")

    assert state.is_unread is True
    assert state.is_flagged is False


def test_a_message_that_was_never_asked_about_stays_unknown():
    """None is what _extract_flags returns when the server said nothing."""
    assert normalize_flags(None) == UNKNOWN


def test_imap_custom_keywords_are_ignored():
    """Servers return NonJunk, $MDNSent and unknown-1; none carry read state."""
    state = normalize_flags(r"\Seen NonJunk unknown-1")

    assert state.is_unread is False
    assert state.is_flagged is False


def test_imap_flag_matching_is_case_insensitive_and_whole_token():
    assert normalize_flags(r"\SEEN").is_unread is False
    # A keyword that merely contains "seen" must not read as \Seen.
    assert normalize_flags(r"\Unseen").is_unread is True


def test_imap_answered_and_flagged():
    state = normalize_flags(r"\Answered \Seen \Flagged")

    assert (state.is_answered, state.is_flagged, state.is_unread) == (True, True, False)


def test_gmail_labels_invert_the_polarity():
    """Gmail records that a message is unread; IMAP records that it was read."""
    state = normalize_flags('["INBOX", "UNREAD"]')

    assert state.is_unread is True


def test_gmail_without_the_unread_label_is_read():
    assert normalize_flags('["CATEGORY_PROMOTIONS", "INBOX"]').is_unread is False


def test_gmail_starred_is_flagged():
    assert normalize_flags('["INBOX", "STARRED"]').is_flagged is True


def test_gmail_answered_is_unknown_not_false():
    """Gmail publishes no answered label, so claiming False would be a guess."""
    assert normalize_flags('["INBOX"]').is_answered is None


def test_unparseable_gmail_labels_are_unknown():
    """Better to say nothing than to report a truncated label list as read."""
    assert normalize_flags("[not json") == UNKNOWN
