# Email Package

import re


def sanitize_filename(filename: str) -> str:
    """Sanitize filename by removing invalid characters, spaces, and encoding issues."""
    if not filename:
        return "unknown"

    filename = filename.replace('=_utf-8_B_', '').replace('=_utf-8_Q_', '')
    filename = filename.replace('_utf-8_', '').replace('=C3=A4', 'ae').replace('=C3=BC', 'ue')
    filename = filename.replace('=C3=B6', 'oe').replace('=C3=9F', 'ss')
    filename = filename.replace(' ', '_')

    invalid_chars = '<>:"/\\|?*[](){}!@#$%^&+=`~;,\'\"'
    for char in invalid_chars:
        filename = filename.replace(char, '')

    filename = re.sub(r'_{2,}', '_', filename)
    filename = filename.strip('_.')
    
    if not filename:
        return "cleaned_subject"

    return filename[:100]