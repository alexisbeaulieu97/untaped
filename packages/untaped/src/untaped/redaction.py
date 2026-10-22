"""Credential redaction for text that leaves the process.

Settings values, error messages, diagnostics and records may carry a URL
with a password in its userinfo (``https://user:s3cret@host``);
:func:`redact_url_password` masks it wherever it appears in a string.
Log lines get :func:`redact_credentials`, which masks more.
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


_URL_USERINFO = re.compile(r"(?P<prefix>[A-Za-z][A-Za-z0-9+.\-]*://)[^/?#@\s]+@")
_AUTHORIZATION = re.compile(
    r"(?P<name>\b(?:proxy-)?authorization\b['\"]?\s*[:=]\s*['\"]?)"
    r"(?P<scheme>[A-Za-z]+\s+)?[^\s'\",;}]+",
    re.IGNORECASE,
)


def redact_credentials(value: str, *, placeholder: str = "***") -> str:
    """Mask every URL's whole userinfo and every ``Authorization`` value in ``value``.

    For log lines, where a user name may itself be the secret (a proxy URL
    ``https://<token>@proxy``): ``https://***@host``. An ``Authorization``
    or ``Proxy-Authorization`` value keeps its scheme (``Bearer ***``).
    """
    value = _URL_USERINFO.sub(rf"\g<prefix>{placeholder}@", value)
    return _AUTHORIZATION.sub(rf"\g<name>\g<scheme>{placeholder}", value)


__all__ = ["redact_credentials", "redact_url_password"]
