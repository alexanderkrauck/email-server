"""Write flags back to an IMAP mailbox.

Kept apart from SMTPClient, which is a read path: everything here mutates a
mailbox the user owns, so it fails loudly rather than falling back.
"""

import logging
import re

logger = logging.getLogger(__name__)

# One response line, carrying both the UID and the flags that resulted.
UID_AND_FLAGS = re.compile(rb"\bUID\s+(\d+)\b.*?\bFLAGS\s+\(([^)]*)\)", re.DOTALL)
FLAGS_AND_UID = re.compile(rb"\bFLAGS\s+\(([^)]*)\).*?\bUID\s+(\d+)\b", re.DOTALL)


class ImapWriteError(RuntimeError):
    """The server refused, or could not be shown to have applied, a write."""


def parse_store_response(lines) -> dict[int, str]:
    """Pair each UID with the flags reported for that same UID.

    Not SMTPClient._extract_flags: that returns the first FLAGS across every
    line, so a batched STORE would write one message's resulting flags onto
    every message in the batch.
    """
    result: dict[int, str] = {}
    for line in lines:
        if not isinstance(line, (bytes, bytearray)):
            continue
        raw = bytes(line)
        match = UID_AND_FLAGS.search(raw)
        if match:
            result[int(match.group(1))] = match.group(2).decode("ascii", errors="ignore")
            continue
        # RFC 3501 says a UID FETCH response SHOULD carry UID, not MUST, and the
        # order of data items is not fixed.
        match = FLAGS_AND_UID.search(raw)
        if match:
            result[int(match.group(2))] = match.group(1).decode("ascii", errors="ignore")
    return result


async def store_flags(client, folder: str, uids: list[int], *, add: list[str], remove: list[str]) -> dict[int, str]:
    """Add and/or remove flags on messages in one folder.

    Returns the flags the server reports afterwards, per UID. A UID the server
    did not confirm is absent from the result rather than assumed applied.
    """
    if not uids or (not add and not remove):
        return {}

    selected = await client.client.select(f'"{folder}"')
    if selected.result != "OK":
        raise ImapWriteError(f"cannot select {folder}")

    confirmed: dict[int, str] = {}
    sequence = ",".join(str(uid) for uid in uids)
    for operation, flags in (("+FLAGS", add), ("-FLAGS", remove)):
        if not flags:
            continue
        # Not .SILENT: the untagged FETCH it suppresses is the only evidence the
        # write landed.
        response = await client.client.uid("store", sequence, operation, f"({' '.join(flags)})")
        if response.result != "OK":
            raise ImapWriteError(f"{operation} failed in {folder}: {response.result}")
        confirmed.update(parse_store_response(response.lines))

    missing = set(uids) - set(confirmed)
    if missing:
        # Some servers answer a STORE without untagged FETCH data. Ask directly
        # rather than reporting a state nothing confirmed.
        response = await client.client.uid("fetch", ",".join(str(uid) for uid in sorted(missing)), "(UID FLAGS)")
        if response.result == "OK":
            confirmed.update(parse_store_response(response.lines))
    return confirmed
