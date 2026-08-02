"""Where a Gmail message lives, derived from its labels.

Gmail has no folders. A message carries a set of labels and may hold several at
once, so there is no location to read off. But everything above this layer --
folder-scoped search, Trash exclusion, and every write, which needs somewhere to
address -- is built on a message being *somewhere*.

So one location is projected from the labels, by precedence. It is deliberately
exclusive: a message that reported two locations would be counted twice by
list_mail_folders, and would give writable_placement two answers about where to
write. The ladder puts the states that remove a message from view first, because
a message in Trash is in Trash whatever else is true of it.

The names are chosen to match settings.excluded_folder_suffixes: "TRASH" and
"SPAM" lowercase to entries in that list, so Gmail's Trash is excluded from
search by exactly the same rule as an IMAP server's.
"""

import json

# In precedence order. ARCHIVE is not a Gmail label: it is the name for having
# none of the others, which Gmail's own interface calls archived.
TRASH = "TRASH"
SPAM = "SPAM"
DRAFT = "DRAFT"
INBOX = "INBOX"
SENT = "SENT"
ARCHIVE = "ARCHIVE"

LOCATIONS = (TRASH, SPAM, DRAFT, INBOX, SENT)

# Labels that describe a state rather than a place. STARRED and UNREAD are read
# by message_flags; CATEGORY_* are Gmail's inbox tabs, which are not locations.
STATE_LABELS = frozenset({"STARRED", "UNREAD", "IMPORTANT", "CHAT"})


def parse_labels(raw: str | None) -> list[str]:
    """The label ids stored on a message, or [] when there are none."""
    if not raw:
        return []
    text = raw.strip()
    if not text.startswith("["):
        return []
    try:
        labels = json.loads(text)
    except ValueError:
        return []
    return [str(label) for label in labels] if isinstance(labels, list) else []


def location_for(labels: list[str]) -> str:
    """The single folder-like location a set of labels implies."""
    present = set(labels)
    for candidate in LOCATIONS:
        if candidate in present:
            return candidate
    return ARCHIVE


def location_for_stored(raw: str | None) -> str:
    """The location implied by a stored flags column."""
    return location_for(parse_labels(raw))


def labels_for_move(labels: list[str], destination: str) -> tuple[list[str], list[str]]:
    """The label changes that move a message to a location.

    Returns (add, remove). Moving on Gmail means rewriting labels, so this both
    adds the destination and clears whichever other location the message held --
    otherwise "move to Archive" would leave it in the Inbox as well.
    """
    if destination not in (*LOCATIONS, ARCHIVE):
        raise ValueError(f"Not a Gmail location: {destination}")
    present = set(labels)
    # Nothing to add if it is already there: an empty change is how the caller
    # tells a no-op move from one that costs a request.
    add = [] if destination == ARCHIVE or destination in present else [destination]
    # SENT and DRAFT are assigned by Gmail and cannot be removed by a client;
    # leaving them alone is why a sent message keeps showing under Sent after
    # being filed elsewhere, which is also what Gmail's own interface does.
    removable = {TRASH, SPAM, INBOX}
    remove = sorted((present & removable) - {destination})
    return add, remove
