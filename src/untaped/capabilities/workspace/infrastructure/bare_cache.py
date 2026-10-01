"""Map repo URLs to local bare-cache paths.

The cache lives at ``<cache_dir>/<host>/<owner>/<name>.git``; the layout
(and its safety against hostile URLs) is :func:`repo_key`, which also
decides when two URLs name the same repo.
"""

from __future__ import annotations

from pathlib import Path

from untaped.capabilities.workspace.domain.naming import repo_key


def cache_path_for(url: str, *, cache_dir: Path) -> Path:
    """Return the bare-cache path for ``url``."""
    return cache_dir.expanduser().resolve().joinpath(*repo_key(url))
