"""Credential redaction for text that leaves the process.

Settings values, error messages, diagnostics and records may carry a URL
with a password in its userinfo (``https://user:s3cret@host``);
:func:`redact_url_password` masks it wherever it appears in a string.
"""

from __future__ import annotations

import re

_URL_PASSWORD = re.compile(r"(?P<prefix>[A-Za-z][A-Za-z0-9+.\-]*://[^/@:\s]*):[^/@\s]*@")


def redact_url_password(value: str, *, placeholder: str = "***") -> str:
    """Mask the password of every ``scheme://user:password@host`` URL in ``value``.

    The user name stays visible so the value remains recognizable. Text
    without such a URL is returned unchanged.
    """
    return _URL_PASSWORD.sub(rf"\g<prefix>:{placeholder}@", value)


__all__ = ["redact_url_password"]
