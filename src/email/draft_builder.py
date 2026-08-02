"""Build the RFC 5322 bytes of a draft."""

from email.message import EmailMessage
from email.utils import formatdate, make_msgid


def build_draft(
    *,
    sender: str,
    to_addresses: list[str],
    cc_addresses: list[str],
    subject: str,
    body_text: str,
    body_html: str,
    headers: dict[str, str],
) -> bytes:
    """A draft is an ordinary message; only the folder and the \\Draft flag differ."""
    message = EmailMessage()
    message["From"] = sender
    if to_addresses:
        message["To"] = ", ".join(to_addresses)
    if cc_addresses:
        message["Cc"] = ", ".join(cc_addresses)
    message["Subject"] = subject
    message["Date"] = formatdate(localtime=True)
    # Give it an identity now, so the draft the sync pass indexes back is
    # recognisable as this one rather than arriving with a synthetic id.
    message["Message-ID"] = make_msgid()
    for name, value in headers.items():
        message[name] = value

    message.set_content(body_text or "")
    if body_html:
        message.add_alternative(body_html, subtype="html")
    return message.as_bytes()
