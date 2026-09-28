"""Shared CLI helpers."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path

from untaped.capabilities.recipe.settings import RecipeSettings
from untaped.capability_api import (
    ConfigError,
    UiContext,
    UsageError,
    get_config_section,
    read_structured_file,
    report_errors,
)


def settings() -> RecipeSettings:
    """Read active recipe settings."""
    return get_config_section("recipe", RecipeSettings)


def library_root() -> Path:
    """Configured recipe library root."""
    return settings().library_root.expanduser()


def merge_vars(
    files: Sequence[Path], values: Mapping[str, object], *, file_flag: str
) -> dict[str, object]:
    """Merge YAML mapping ``files`` in order (later wins), then ``values`` on top.

    The ``--vars-file``/``--var`` precedence ``awx test`` suites use: CLI
    pairs win over every file, and a later file wins over an earlier one.
    """
    merged: dict[str, object] = {}
    for path in files:
        merged.update(read_structured_file(path, flag=file_flag))
    merged.update(values)
    return merged


def hook_timeout_seconds(override: float | None) -> float:
    """Resolve the effective hook timeout from the CLI override or settings."""
    timeout = settings().hook_timeout_seconds if override is None else override
    if timeout < 0:
        raise UsageError("--hook-timeout must be greater than or equal to 0")
    return timeout


def hook_startup_notice(ui: UiContext) -> Callable[[Path], None]:
    """Quiet-gated notice shown while a hook worker's uv environment starts."""

    def notice(project_root: Path) -> None:
        ui.message("info", f"preparing hook environment for {project_root}...")

    return notice


@contextmanager
def report_config_errors() -> Iterator[None]:
    """Report expected config/library errors without Python tracebacks."""
    with report_errors():
        try:
            yield
        except ValueError as exc:
            raise ConfigError(str(exc)) from exc
