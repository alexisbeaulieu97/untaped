"""Git remote URLs: what untaped accepts as a remote, and its store key.

``store_key`` is the repo store's directory key: ``(<host>, <segment>...,
<name>.git)``, host and path lowercased and NFC-normalised, so the https and
ssh URLs of one repo, and two spellings of its path, are one directory.
Without a host (a local path, ``file://``) it is ``("_unknown",
"<sha256[:16]>.git")``. Every part is one filesystem-safe segment, so a hostile
URL never leaves the store root.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Annotated
from urllib.parse import urlparse

from pydantic import AfterValidator

from untaped.sdk import safe_path_segment

_UNKNOWN = "_unknown"
_SCP = re.compile(r"^[A-Za-z0-9._-]+@(?P<host>[A-Za-z0-9.-]+):(?P<path>[^/].*)$")
#: Any ``user@host:path``, for splitting a URL that is already in use.
_SCP_PARTS = re.compile(r"^(?P<user>[^@]+)@(?P<host>[^:]+):(?P<path>.+)$")


def validate_git_url(value: str) -> str:
    """``value`` when it is an ``https://``, ``ssh://`` or ``user@host:path`` remote.

    Refused: userinfo in an https URL (a token in a URL ends up in logs and
    configs), ``file://`` and bare paths, ``ext::`` and every other scheme.
    """
    if "://" not in value:
        if _SCP.match(value):
            return value
        raise ValueError(f"{value!r} is not an https://, ssh:// or user@host:path Git URL")
    parsed = urlparse(value)
    if parsed.scheme == "https":
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("a Git URL must not carry credentials; use `untaped auth set`")
    elif parsed.scheme != "ssh":
        raise ValueError(f"{parsed.scheme}:// is not an accepted Git URL scheme")
    if not parsed.hostname or parsed.path.strip("/") == "":
        raise ValueError(f"{value!r} has no host or no repository path")
    return value


#: A remote URL, validated by :func:`validate_git_url`.
type GitUrl = Annotated[str, AfterValidator(validate_git_url)]


def repo_url_parts(url: str) -> tuple[str | None, list[str]]:
    """``(host, path segments with the last .git removed)`` of a URL, ``user@host:path`` or path.

    The host is lowercased, as ``urlparse`` does. A plain path or ``file://``
    URL has no host.
    """
    match = _SCP_PARTS.match(url)
    if "://" in url:
        parsed = urlparse(url)
        host, path = parsed.hostname, parsed.path or ""
    elif match:
        host, path = match.group("host").lower(), match.group("path")
    else:
        host, path = None, url
    segments = [s for s in path.replace("\\", "/").split("/") if s]
    if segments:
        segments[-1] = segments[-1].removesuffix(".git")
    return host, segments


def url_host(url: str) -> str | None:
    """The lowercased host of ``url``; ``None`` for a local path or ``file://``."""
    host, _ = repo_url_parts(url)
    return host


def https_origin(url: str) -> str | None:
    """``https://<host>`` (port kept) for an https URL, else ``None``."""
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        return None
    netloc = parsed.hostname if parsed.port is None else f"{parsed.hostname}:{parsed.port}"
    return f"https://{netloc}"


def store_key(url: str) -> tuple[str, ...]:
    """The repo store directory key of ``url``; see the module docstring."""
    host, segments = repo_url_parts(url)
    if host is None or not segments:
        return (_UNKNOWN, f"{hashlib.sha256(url.encode()).hexdigest()[:16]}.git")
    *parents, leaf = (safe_path_segment(_fold(part)) for part in segments)
    return (safe_path_segment(_fold(host)), *parents, f"{leaf}.git")


def _fold(part: str) -> str:
    return unicodedata.normalize("NFC", part).lower()
