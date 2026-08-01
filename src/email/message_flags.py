"""Provider flag strings, normalised to a small tri-state vocabulary.

The two sync paths disagree about almost everything. IMAP stores a
space-separated system-flag list (``\\Seen \\Answered``) and records that a
message *was read*; the Gmail API stores a JSON array of label ids
(``["INBOX", "UNREAD"]``) and records that a message *was not*. Neither form is
queryable, so search cannot filter on either without a normalised column.

"Unknown" is a first-class answer here. Messages synced before flags were
captured have no flag string at all, and Gmail exposes no label for "answered".
Returning False for those would let the model state, with confidence, that a
message was read when nothing was ever observed about it.
"""

import json
from dataclasses import dataclass

# IMAP system flags, lowercased. Servers also return custom keywords
# ("NonJunk", "$MDNSent"), which carry no portable meaning and are ignored.
SEEN = "\\seen"
FLAGGED = "\\flagged"
ANSWERED = "\\answered"

# Gmail label ids. There is deliberately no answered equivalent: Gmail does not
# publish one, and inferring it from thread shape would be a guess.
GMAIL_UNREAD = "UNREAD"
GMAIL_STARRED = "STARRED"


@dataclass(frozen=True)
class FlagState:
    """Tri-state read/flag status. None means nothing was ever observed."""

    is_unread: bool | None = None
    is_flagged: bool | None = None
    is_answered: bool | None = None


UNKNOWN = FlagState()


def normalize_flags(raw: str | None) -> FlagState:
    """Map a stored provider flag string onto the tri-state vocabulary.

    None means the message was stored without flags ever being fetched. An empty
    string is different: it is an IMAP ``FLAGS ()`` response, which positively
    states that the message carries no flags and is therefore unread.
    """
    if raw is None:
        return UNKNOWN
    text = raw.strip()
    if text.startswith("["):
        return _from_gmail_labels(text)
    return _from_imap_flags(text)


def _from_imap_flags(text: str) -> FlagState:
    tokens = {token.lower() for token in text.split()}
    return FlagState(
        is_unread=SEEN not in tokens,
        is_flagged=FLAGGED in tokens,
        is_answered=ANSWERED in tokens,
    )


def _from_gmail_labels(text: str) -> FlagState:
    try:
        labels = json.loads(text)
    except ValueError:
        return UNKNOWN
    if not isinstance(labels, list):
        return UNKNOWN
    names = {str(label) for label in labels}
    return FlagState(
        is_unread=GMAIL_UNREAD in names,
        is_flagged=GMAIL_STARRED in names,
        # Gmail publishes no answered label, so this stays unknown rather than
        # reporting every Gmail message as unanswered.
        is_answered=None,
    )


def apply_flag_state(message, raw: str | None) -> None:
    """Store a provider flag string and its normalised form on a message."""
    from src.email import sanitize_db_text

    state = normalize_flags(raw)
    message.flags = None if raw is None else sanitize_db_text(raw)
    message.is_unread = state.is_unread
    message.is_flagged = state.is_flagged
    message.is_answered = state.is_answered
