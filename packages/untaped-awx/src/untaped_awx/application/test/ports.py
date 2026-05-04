"""Adapter interfaces for the ``awx test`` use cases.

Concrete implementations live in :mod:`untaped_awx.infrastructure.test`
and (for ``Launcher`` / ``Watcher``) reuse the existing
:class:`RunAction` / :class:`WatchJob` use cases.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from untaped_awx.domain import Job
from untaped_awx.domain.test_suite import VariableSpec

if TYPE_CHECKING:
    # AwxResourceSpec lives in infrastructure but is the concrete spec type
    # accepted by transport-aware ports below. Type-only import keeps
    # application → infrastructure decoupled at runtime (see test_layering).
    from untaped_awx.infrastructure.spec import AwxResourceSpec


@runtime_checkable
class Filesystem(Protocol):
    """Read text files. Decoupled so unit tests can stub it."""

    def read_text(self, path: Path) -> str: ...


@runtime_checkable
class Prompt(Protocol):
    """Interactive prompt for variable values. Stubbed in unit tests."""

    def is_interactive(self) -> bool: ...

    def ask(self, spec: VariableSpec) -> str: ...


@runtime_checkable
class Launcher(Protocol):
    """POST a launch payload, return the resulting :class:`Job` record."""

    def __call__(
        self,
        spec: AwxResourceSpec,
        *,
        name: str,
        action: str,
        scope: dict[str, str] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> Job: ...


@runtime_checkable
class Watcher(Protocol):
    """Poll a :class:`Job` until it reaches a terminal state."""

    def __call__(self, job: Job, *, timeout: float | None = None) -> Job: ...


@runtime_checkable
class Parser(Protocol):
    """Splits frontmatter, parses YAML (with ``!ref``), renders Jinja2.

    The concrete implementation lives in
    :mod:`untaped_awx.infrastructure.test.parser`; the loader takes this
    via injection so the application layer never imports YAML or Jinja2
    directly.
    """

    def split_frontmatter(self, text: str) -> tuple[str, str]: ...

    def parse_yaml(self, text: str) -> Any: ...

    def render_body(self, body: str, values: Mapping[str, Any]) -> str: ...


@runtime_checkable
class VarsResolver(Protocol):
    """Resolve declared variables → typed values from CLI / files / prompt."""

    def __call__(
        self,
        specs: Mapping[str, VariableSpec],
        *,
        cli: Mapping[str, str],
        files: Iterable[Path],
        prompt: Prompt,
        extra_known_names: Iterable[str] = (),
    ) -> dict[str, Any]: ...
