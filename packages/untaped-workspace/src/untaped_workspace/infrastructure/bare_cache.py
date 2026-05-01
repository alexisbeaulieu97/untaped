"""Map repo URLs to local bare-cache paths.

The cache lives at ``<cache_dir>/<host>/<owner>/<name>.git``. URLs that
can't be parsed cleanly fall back to a hashed leaf so we still get a
deterministic path.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from urllib.parse import urlparse

from untaped_core import get_settings


def cache_path_for(url: str, *, cache_dir: Path | None = None) -> Path:
    """Return the bare-cache path for ``url``."""
    base = cache_dir or get_settings().workspace.cache_dir
    base = base.expanduser().resolve()
    host, segments = _parse(url)
    if host is None or not segments:
        digest = hashlib.sha256(url.encode()).hexdigest()[:16]
        return base / "_unknown" / f"{digest}.git"
    leaf = segments[-1]
    if not leaf.endswith(".git"):
        leaf = leaf + ".git"
    return base.joinpath(host, *segments[:-1], leaf)


_SSH_RE = re.compile(r"^(?P<user>[^@]+)@(?P<host>[^:]+):(?P<path>.+)$")


def _parse(url: str) -> tuple[str | None, list[str]]:
    """Extract (host, [path-segments without .git]) from ``url``."""
    if "://" in url:
        parsed = urlparse(url)
        host = parsed.hostname
        segments = [s for s in (parsed.path or "").split("/") if s]
    else:
        match = _SSH_RE.match(url)
        if not match:
            return None, []
        host = match.group("host")
        segments = [s for s in match.group("path").split("/") if s]
    if not segments:
        return host, []
    if segments[-1].endswith(".git"):
        segments[-1] = segments[-1][:-4]
    return host, segments
