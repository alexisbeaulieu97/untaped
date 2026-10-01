"""Shared CLI helpers."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path

from untaped.capabilities.recipe.errors import RecipeError
from untaped.capabilities.recipe.settings import RecipeSettings
from untaped.sdk import (
    ErrorCategory,
    UiContext,
    UntapedError,
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
    """Report expected library and input errors without Python tracebacks.

    An :class:`UntapedError` (typed recipe errors included) keeps its own
    category. Any other ``ValueError`` (the remaining plain validation raises
    of recipe, pack and hook files, and library errors such as a pydantic
    ``ValidationError`` or a YAML error) is invalid local input: it becomes a
    :class:`RecipeError` with the same message (exit ``1``).
    """
    with report_errors():
        try:
            yield
        except UntapedError:
            raise
        except ValueError as exc:
            raise RecipeError(str(exc)) from exc


def as_recipe_error[T, R](action: Callable[[T], R]) -> Callable[[T], R]:
    """Wrap ``action`` so its expected library errors are per-item ``UntapedError``s.

    Typed errors keep their category; a plain ``ValueError`` is invalid input
    and an ``OSError`` a failed file operation (both in ``local``).
    """

    def wrapped(item: T) -> R:
        try:
            return action(item)
        except UntapedError:
            raise
        except ValueError as exc:
            raise RecipeError(str(exc)) from exc
        except OSError as exc:
            raise RecipeError(str(exc), category=ErrorCategory.FAILED) from exc

    return wrapped
