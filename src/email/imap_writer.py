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


# [COPYUID <uidvalidity> <source set> <destination set>], RFC 4315. Without it a
# move cannot report where the message landed.
COPYUID = re.compile(rb"\[COPYUID\s+(\d+)\s+([\d,:]+)\s+([\d,:]+)\]", re.IGNORECASE)

# IMAP special-use attributes, RFC 6154. A mailbox that names its own Trash is
# far more reliable than guessing from the folder's name.
SPECIAL_USE = ("\\all", "\\archive", "\\drafts", "\\flagged", "\\junk", "\\sent", "\\trash")


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


def parse_copyuid(lines) -> int | None:
    """The destination UID a MOVE or COPY reported, when the server supports UIDPLUS."""
    for line in lines:
        if not isinstance(line, (bytes, bytearray)):
            continue
        match = COPYUID.search(bytes(line))
        if match:
            destination = match.group(3).decode("ascii", errors="ignore")
            # A single message, so the set is one UID; take the first regardless.
            first = destination.replace(":", ",").split(",")[0]
            return int(first) if first.isdigit() else None
    return None


async def move_message(client, source: str, uid: int, destination: str) -> int | None:
    """Move one message between folders. Returns the new UID when the server says.

    Prefers UID MOVE (RFC 6851). The COPY/STORE/EXPUNGE fallback deliberately does
    not EXPUNGE unless the server supports UID EXPUNGE: a bare EXPUNGE removes
    every \\Deleted message in the mailbox, including ones this call never touched.
    """
    selected = await client.client.select(f'"{source}"')
    if selected.result != "OK":
        raise ImapWriteError(f"cannot select {source}")

    capabilities = {item.upper() for item in getattr(client.client.protocol, "capabilities", set())}
    if "MOVE" in capabilities:
        response = await client.client.uid("move", str(uid), f'"{destination}"')
        if response.result != "OK":
            raise ImapWriteError(f"MOVE to {destination} failed: {response.result}")
        return parse_copyuid(response.lines)

    response = await client.client.uid("copy", str(uid), f'"{destination}"')
    if response.result != "OK":
        raise ImapWriteError(f"COPY to {destination} failed: {response.result}")
    new_uid = parse_copyuid(response.lines)

    marked = await client.client.uid("store", str(uid), "+FLAGS", "(\\Deleted)")
    if marked.result != "OK":
        raise ImapWriteError(f"could not mark the original deleted in {source}")
    if "UIDPLUS" in capabilities:
        await client.client.uid("expunge", str(uid))
    else:
        logger.info(
            "No UIDPLUS on this server; leaving the original in %s flagged \\Deleted "
            "rather than expunging the whole folder",
            source,
        )
    return new_uid


async def append_message(client, folder: str, raw: bytes, *, flags: list[str]) -> int | None:
    """Append a message to a folder, e.g. a draft. Returns its UID when reported."""
    response = await client.client.append(
        raw, mailbox=f'"{folder}"', flags=f"({' '.join(flags)})" if flags else None
    )
    if response.result != "OK":
        raise ImapWriteError(f"APPEND to {folder} failed: {response.result}")
    # APPENDUID has the same shape as COPYUID, minus the source set.
    for line in response.lines:
        if isinstance(line, (bytes, bytearray)):
            match = re.search(rb"\[APPENDUID\s+\d+\s+(\d+)\]", bytes(line), re.IGNORECASE)
            if match:
                return int(match.group(1))
    return None


async def list_folders(client) -> list[dict]:
    """Every selectable folder, with its special-use role where the server names one."""
    response = await client.client.list('""', "*")
    if response.result != "OK":
        raise ImapWriteError("cannot list folders")
    folders = []
    for line in response.lines:
        parsed = client._parse_list_response(line)
        if not parsed:
            continue
        flags, name = parsed
        if "\\noselect" in flags:
            continue
        role = next((flag.lstrip("\\") for flag in flags if flag in SPECIAL_USE), None)
        folders.append({"name": name, "special_use": role})
    return folders
