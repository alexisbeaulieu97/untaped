"""Adapter interfaces for the ``awx test`` use cases.

Concrete implementations live in :mod:`untaped.capabilities.awx.infrastructure.suites`,
except ``Launcher`` / ``Watcher`` (which reuse the existing
:class:`RunAction` / :class:`WatchJob` use cases), ``Canceller`` (the
job record repository's ``cancel``), ``LogReader`` / ``EventReader`` (the
job monitor's ``fetch_stdout`` / ``stream_events``), ``LaunchCheck``
(:class:`PreflightLaunch`) and ``FkPrefetcher`` /
``FkLookup`` (narrow views of :class:`FkResolver`, implemented by
:mod:`untaped.capabilities.awx.infrastructure.fk_resolver`).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from untaped.capabilities.awx.domain import Job, JobEvent, ResourceSpec
from untaped.capabilities.awx.domain.suite import VariableSpec


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
        spec: ResourceSpec,
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
class Canceller(Protocol):
    """Ask AWX to cancel a running execution (raises when AWX refuses)."""

    def __call__(self, *, kind: str, job_id: int) -> None: ...


@runtime_checkable
class LogReader(Protocol):
    """Return a job's full stdout, one string per line."""

    def __call__(self, job: Job, /) -> list[str]: ...


@runtime_checkable
class EventReader(Protocol):
    """Read a job's events; ``params`` filter server-side, ``follow=False`` reads once."""

    def __call__(
        self, job: Job, *, params: dict[str, str] | None = None, follow: bool = True
    ) -> Iterable[JobEvent]: ...


@runtime_checkable
class LaunchCheck(Protocol):
    """Raise when AWX would reject or ignore a launch, before anything runs."""

    def __call__(
        self,
        spec: ResourceSpec,
        *,
        name: str,
        scope: dict[str, str] | None,
        payload: dict[str, Any],
    ) -> None: ...


@runtime_checkable
class Parser(Protocol):
    """Splits frontmatter, parses YAML (with ``!ref``), renders Jinja2.

    The concrete implementation lives in
    :mod:`untaped.capabilities.awx.infrastructure.suites.parser`; the loader takes this
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


class FkPrefetcher(Protocol):
    """Subset of :class:`FkResolver` the runner uses to warm caches upfront."""

    def prefetch(self, plan: dict[str, list[dict[str, str] | None]]) -> None: ...


class FkLookup(Protocol):
    """Subset of :class:`FkResolver` the case-payload resolver needs."""

    def name_to_id(self, kind: str, name: str, *, scope: dict[str, str] | None = None) -> int: ...
