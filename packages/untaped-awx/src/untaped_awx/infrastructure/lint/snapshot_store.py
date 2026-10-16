"""Where a controller snapshot is kept between runs: one JSON file per profile and controller.

The cache's sidecar says how fresh the snapshot is; this module holds the
data itself. The file name hashes the profile, the controller's URL and the
token (a digest, never the token), so two profiles on one controller, or a
profile whose token changed, never read another token's snapshot: what a
token may see differs. A changed token finds no file, and the snapshot is
read again.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from untaped.sdk import atomic_write
from untaped_awx.domain.lint.resources import AwxResources


def store_path(directory: Path, *, profile: str, base_url: str, token: str) -> Path:
    """The snapshot file of ``profile``'s ``token`` on ``base_url``, under ``directory``."""
    token_digest = hashlib.sha256(token.encode()).hexdigest()
    digest = hashlib.sha256(f"{profile}\0{base_url}\0{token_digest}".encode()).hexdigest()[:16]
    return directory / "resources" / f"{digest}.json"


def write(path: Path, resources: AwxResources) -> None:
    """Keep ``resources`` at ``path``, replacing what was there in one step.

    Owner-only: secrets are redacted, but the rest is the controller's
    configuration as the token sees it.
    """
    atomic_write(path, resources.model_dump_json(), mode=0o600)


def read(path: Path) -> AwxResources:
    """The snapshot kept at ``path``."""
    return AwxResources.model_validate_json(path.read_text(encoding="utf-8"))


__all__ = ["read", "store_path", "write"]
