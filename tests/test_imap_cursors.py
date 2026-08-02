

def test_indexing_a_mailbox_never_marks_it_read():
    """RFC822 is an alias for BODY[], and a non-peek fetch sets \\Seen upstream."""
    import re
    from pathlib import Path

    source = Path("src/email/smtp_client.py").read_text()
    fetch_arguments = re.findall(r'"\((?:UID|BODY|FLAGS)[^"]*\)"', source)

    assert fetch_arguments, "no FETCH item lists found; has the client been restructured?"
    for arguments in fetch_arguments:
        body = arguments.replace("RFC822.SIZE", "")
        assert "RFC822" not in body, f"non-peek body fetch would mark mail read: {arguments}"
        assert "BODY[" not in body, f"non-peek body fetch would mark mail read: {arguments}"
