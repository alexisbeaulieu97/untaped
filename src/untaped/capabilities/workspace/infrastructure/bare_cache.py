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


def cache_path_for(url: str, *, cache_dir: Path) -> Path:
    """Return the bare-cache path for ``url``."""
    base = cache_dir.expanduser().resolve()
    host, segments = _parse(url)
    if host is None or not segments:
        digest = hashlib.sha256(url.encode()).hexdigest()[:16]
        return base / "_unknown" / f"{digest}.git"
    leaf = _safe_path_part(segments[-1]) + ".git"
    return base.joinpath(_safe_path_part(host), *(_safe_path_part(s) for s in segments[:-1]), leaf)


def _safe_path_part(value: str) -> str:
    """Map ``value`` to one filesystem-safe segment (never ``.``/``..``)."""
    safe = "".join(char if char.isalnum() or char in "._-" else "_" for char in value)
    return "_" if safe in {"", ".", ".."} else safe


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
    if not segments:
        return host, []
    if segments[-1].endswith(".git"):
        segments[-1] = segments[-1][:-4]
    return host, segments
