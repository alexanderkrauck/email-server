"""One identity per message per account, independent of where it is filed.

An IMAP UID belongs to a folder, so using it as identity makes a moved message
look like a deletion followed by an unrelated arrival. The RFC Message-ID travels
with the message, which is what identity needs to do.
"""

import re

# Synthesised locally when a message arrives with no Message-ID header.
_SYNTHETIC_MESSAGE_ID = re.compile(r"^uid_\d+_\d+$")
_EDGE_WHITESPACE = re.compile(r"^\s+|\s+$")


def _usable(message_id: str | None) -> str | None:
    if not message_id:
        return None
    normalised = _EDGE_WHITESPACE.sub("", message_id).lower()
    if not normalised or _SYNTHETIC_MESSAGE_ID.match(normalised):
        return None
    return normalised


def stable_identity(
    *,
    uses_provider_ids: bool,
    provider_message_id: str | None,
    message_id: str | None,
    folder: str | None,
    uid: int | None,
    uid_validity: int | None,
) -> str:
    """Return an identity that survives the message being moved or copied.

    `uses_provider_ids` is true only for the Gmail API sync path, whose own message
    ID already survives a label change. It is deliberately not derived from the
    provider name: one account is provider='gmail' but syncs over IMAP.
    """
    if uses_provider_ids and provider_message_id:
        return provider_message_id

    usable = _usable(message_id)
    if usable:
        return usable

    # No trustworthy Message-ID. Fall back to location, marked so it is obvious this
    # message cannot be tracked across a move. Hashing the headers instead would
    # merge distinct messages that share sender, subject and timestamp.
    return f"loc:{folder or ''}:{uid_validity or 0}:{uid or 0}"
