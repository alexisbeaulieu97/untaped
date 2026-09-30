"""Adapter interfaces for the ``awx test`` use cases.

Concrete implementations live in :mod:`untaped.capabilities.awx.infrastructure.suites`,
except ``Launcher`` / ``Watcher`` (which reuse the existing
:class:`RunAction` / :class:`WatchJob` use cases), ``LogReader`` / ``EventReader`` /
``TailReader`` (the job monitor's ``fetch_stdout`` / ``stream_events`` /
``tail_stdout``), ``JobReader`` (the job monitor itself), ``HostReader``,
``NodeReader`` and ``ApprovalDecider`` (the job repository's ``host_summaries``,
``workflow_nodes`` and ``decide_approval``), ``LaunchCheck``
(:class:`PreflightLaunch`) and ``FkPrefetcher`` /
``FkLookup`` (narrow views of :class:`FkResolver`, implemented by
:mod:`untaped.capabilities.awx.infrastructure.fk_resolver`).
"""

from __future__ import annotations

from collections.abc import Callable, Collection, Iterable, Mapping
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
    """Poll a :class:`Job` until it reaches a terminal state.

    ``on_state`` is called with each polled state that is not terminal yet
    (not with ``job`` itself), before the next poll; a workflow's pending
    approvals are answered there. An exception it raises ends the watch and
    propagates to the caller.
    """

    def __call__(
        self,
        job: Job,
        *,
        timeout: float | None = None,
        on_state: Callable[[Job], None] | None = None,
    ) -> Job: ...


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
class TailReader(Protocol):
    """Return the last ``lines`` log lines of a job, from its newest events only."""

    def __call__(self, job: Job, lines: int, /) -> list[str]: ...


@runtime_checkable
class HostReader(Protocol):
    """Read a job's host summary records (``job_host_summaries``), lazily, failed hosts first.

    ``params`` filter server-side (``changed__gt=0``, ``host_name__in=a,b``).
    """

    def __call__(
        self, job: Job, params: Mapping[str, str] | None = None, /
    ) -> Iterable[Mapping[str, Any]]: ...


@runtime_checkable
class NodeReader(Protocol):
    """Read a workflow job's nodes (``workflow_jobs/<id>/workflow_nodes/``), every page."""

    def __call__(self, job: Job, /) -> Iterable[Mapping[str, Any]]: ...


@runtime_checkable
class ApprovalDecider(Protocol):
    """Approve or deny a pending workflow approval (``workflow_approvals/<id>/approve/``)."""

    def __call__(self, approval_id: int, *, approve: bool) -> None: ...


@runtime_checkable
class JobReader(Protocol):
    """Re-read an execution: as it is now, or once AWX has saved its events."""

    def fetch(self, job: Job) -> Job: ...

    def settled(self, job: Job) -> Job: ...


@runtime_checkable
class LaunchCheck(Protocol):
    """Raise when AWX would reject or ignore a launch, before anything runs.

    ``nodes`` are node ids a workflow case checks, which the workflow must have.
    Returns the payload to launch: the fields that change nothing dropped.
    """

    def __call__(
        self,
        spec: ResourceSpec,
        *,
        name: str,
        scope: dict[str, str] | None,
        payload: dict[str, Any],
        nodes: Collection[str] = (),
    ) -> dict[str, Any]: ...

    def approval_nodes(
        self, spec: ResourceSpec, *, name: str, scope: dict[str, str] | None
    ) -> list[str]:
        """The paths of a workflow's approval nodes, nested workflows' included."""
        ...


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
