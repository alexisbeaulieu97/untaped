"""Where a controller snapshot is kept between runs: one JSON file per profile and controller.

The cache's sidecar says how fresh the snapshot is; this module holds the
data itself. The file name hashes the profile with the controller's URL, so
two profiles on one controller never share a snapshot (their tokens may see
different resources).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from untaped.sdk import atomic_write
from untaped_awx.domain.lint.resources import AwxResources


def store_path(directory: Path, *, profile: str, base_url: str) -> Path:
    """The snapshot file of ``profile`` on ``base_url``, under the plugin's ``directory``."""
    digest = hashlib.sha256(f"{profile}\0{base_url}".encode()).hexdigest()[:16]
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
