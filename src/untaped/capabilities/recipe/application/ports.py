"""Application-layer ports for filesystem and hook adapters."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from untaped.capabilities.recipe.domain.plan import Verdict


@dataclass(frozen=True)
class HookDebugResult[T]:
    """Hook result plus diagnostics and accumulated warnings for one invocation."""

    result: T
    diagnostics: str
    warnings: tuple[str, ...] = ()


class HookExecutorPort(Protocol):
    """Execute trusted hooks through their resolved runtime."""

    def transform(
        self,
        hook: str,
        content: str,
        *,
        local_hook_project: Path | None,
        target: Path,
        file: Path,
        inputs: dict[str, object],
        args: dict[str, object],
        capture_diagnostics: bool = False,
    ) -> HookDebugResult[str]:
        """Run a transform hook and return replacement content plus diagnostics."""

    def validate(
        self,
        hook: str,
        *,
        local_hook_project: Path | None,
        target: Path,
        inputs: dict[str, object],
        args: dict[str, object],
        capture_diagnostics: bool = False,
    ) -> HookDebugResult[Verdict]:
        """Run a validate hook and return its coerced verdict plus diagnostics."""
