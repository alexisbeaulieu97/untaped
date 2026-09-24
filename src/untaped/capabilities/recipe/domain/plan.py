"""Pure result models for recipe planning and application."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from untaped.capabilities.recipe.domain.paths import safe_relative_path

ApplyStatus = Literal["planned", "applied", "dry-run", "skipped", "error"]

# File content is handled as ``str``. Bytes that are not UTF-8 decode to lone
# surrogates under this error handler and encode back unchanged, so ``copy``,
# ``remove``, backups and restores keep binary files byte-exact.
CONTENT_ERRORS = "surrogateescape"


def is_binary_content(content: str | None) -> bool:
    """Whether ``content`` holds non-UTF-8 bytes or NULs (not diffable text)."""
    return content is not None and (
        "\x00" in content or any("\udc80" <= char <= "\udcff" for char in content)
    )


class Verdict(BaseModel):
    """Read-only hook verdict."""

    model_config = ConfigDict(frozen=True)

    status: Literal["pass", "fail", "skip"]
    message: str = ""

    @property
    def failed(self) -> bool:
        """Whether this verdict must abort the target plan."""
        return self.status == "fail"

    @property
    def skipped(self) -> bool:
        """Whether this verdict marks the target not applicable."""
        return self.status == "skip"


class FileChange(BaseModel):
    """One planned file mutation for one target directory."""

    model_config = ConfigDict(frozen=True)

    target: Path
    relative_path: Path
    before: str | None
    after: str | None

    @field_validator("relative_path")
    @classmethod
    def _safe_relative_path(cls, value: Path) -> Path:
        return safe_relative_path(value, field="relative_path")

    @property
    def path(self) -> Path:
        """Absolute path of the changed file."""
        return self.target / self.relative_path

    @property
    def kind(self) -> str:
        """Human-readable mutation kind."""
        if self.before is None and self.after is not None:
            return "create"
        if self.before is not None and self.after is None:
            return "remove"
        return "modify"


class TargetPlan(BaseModel):
    """Planned changes for one target directory."""

    model_config = ConfigDict(frozen=True)

    target: Path
    status: ApplyStatus
    changes: tuple[FileChange, ...] = ()
    warnings: tuple[str, ...] = ()
    error: str = ""
    display_inputs: dict[str, object] = Field(default_factory=dict)

    @property
    def files_changed(self) -> int:
        """Count of files whose content or existence changes."""
        return len(self.changes)


@dataclass(frozen=True)
class HookDebugResult[T]:
    """Hook result plus diagnostics and accumulated warnings for one invocation."""

    result: T
    diagnostics: str
    warnings: tuple[str, ...] = ()
