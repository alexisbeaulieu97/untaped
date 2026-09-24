"""Map repo URLs to local bare-cache paths.

The cache lives at ``<cache_dir>/<host>/<owner>/<name>.git``. URLs that
can't be parsed cleanly fall back to a hashed leaf so we still get a
deterministic path. Every path component is sanitised to a single safe
segment so a hostile URL (``https://evil/../../tmp/pwn.git``) can never
place a cache repo outside ``cache_dir``.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from urllib.parse import urlparse

from untaped.capability_api import safe_path_segment


def cache_path_for(url: str, *, cache_dir: Path) -> Path:
    """Return the bare-cache path for ``url``."""
    base = cache_dir.expanduser().resolve()
    host, segments = _parse(url)
    if host is None or not segments:
        digest = hashlib.sha256(url.encode()).hexdigest()[:16]
        return base / "_unknown" / f"{digest}.git"
    leaf = safe_path_segment(segments[-1]) + ".git"
    return base.joinpath(
        safe_path_segment(host), *(safe_path_segment(s) for s in segments[:-1]), leaf
    )


_SSH_RE = re.compile(r"^(?P<user>[^@]+)@(?P<host>[^:]+):(?P<path>.+)$")


def _parse(url: str) -> tuple[str | None, list[str]]:
    """Extract (host, [path-segments without .git]) from ``url``."""
    if "://" in url:
        parsed = urlparse(url)
        host = parsed.hostname
        segments = [s for s in (parsed.path or "").replace("\\", "/").split("/") if s]
    else:
        match = _SSH_RE.match(url)
        if not match:
            return None, []
        host = match.group("host")
        segments = [s for s in match.group("path").replace("\\", "/").split("/") if s]
    if segments and segments[-1].endswith(".git"):
        segments[-1] = segments[-1][:-4]
    return host, segments
