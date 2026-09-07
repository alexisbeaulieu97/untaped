from __future__ import annotations

from pathlib import Path

from orchestration.builders import STORE_ID
from untaped.capabilities.orchestration.application.bootstrap import InitializeStore, InitRequest
from untaped.capabilities.orchestration.infrastructure.filesystem import location_from_root
from untaped.capabilities.orchestration.infrastructure.locking import FileLockManager
from untaped.capabilities.orchestration.infrastructure.repository import FilesystemStoreRepository
from untaped.capabilities.orchestration.infrastructure.views import MarkdownViewRenderer


def initialized_repository(tmp_path: Path) -> Path:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    repository = FilesystemStoreRepository()
    InitializeStore(
        repository,
        repository,
        FileLockManager(),
        MarkdownViewRenderer(),
    ).execute(InitRequest(repository_root, STORE_ID, "CLI fixture", "UTC"))
    return location_from_root(repository_root / ".untaped" / "orchestration").root
