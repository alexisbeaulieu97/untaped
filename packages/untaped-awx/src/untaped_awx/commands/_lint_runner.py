"""Running ``awx lint``: load the snapshot its origin calls for, then lint it.

FILES ``--offline`` never opens the awx context (it would build the HTTP
client and read the profile): the catalog alone resolves a batch. FILES
online and the controller origin open it once.
"""

from __future__ import annotations

import glob
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from urllib.parse import urlparse

from untaped.sdk import ConfigError, Filled, ProgressHandle, UsageError, plural
from untaped_awx.application.lint.controller import load_controller
from untaped_awx.application.lint.documents import load_documents
from untaped_awx.application.resources.ports import Catalog
from untaped_awx.application.resources.save_resources import (
    filter_field_not_on_spec,
    server_filters_for_spec,
)
from untaped_awx.commands.context import AwxContext
from untaped_awx.domain import ResourceSpec
from untaped_awx.domain.lint.resources import Resources
from untaped_awx.infrastructure.lint.documents import documents_of, file_texts
from untaped_awx.infrastructure.resources.catalog import AwxResourceCatalog
from untaped_awx.infrastructure.resources.spec import awx_api_path
from untaped_awx.settings import RESOURCES, TOKEN
from untaped_lint.api import Findings, Report, lint_config, lint_report


@dataclass(frozen=True)
class Query:
    """What a run reports: ``--kind``/``--organization`` as target globs, and the server query."""

    kind: str | None = None
    organization: str | None = None
    filters: dict[str, str] = field(default_factory=dict)
    search: str | None = None

    @property
    def queried(self) -> bool:
        """Whether ``--filter``/``--search`` ask the controller what to report."""
        return bool(self.filters) or self.search is not None


def run_lint(
    files: list[Path],
    *,
    offline: bool,
    refresh: bool | None,
    query: Query,
    findings: Findings,
    awx: Callable[[], AbstractContextManager[AwxContext]],
) -> Report | None:
    """Lint the origin ``files``/``offline`` name: the report, ``None`` when nothing matched.

    ``awx`` opens the awx context, which an offline run never does.
    """
    catalog = AwxResourceCatalog()
    spec = _kind(catalog, query.kind)
    _check_filters(catalog, spec, query.filters)
    locations: list[str] = []
    if files and offline:
        resources = load_documents(documents_of(file_texts(files)), catalog=catalog)
    else:
        with awx() as ctx:
            if files:
                texts = documents_of(file_texts(files))
                resources = load_documents(texts, catalog=catalog, live=ctx.fk)
            else:
                resources = _snapshot(ctx, refresh=refresh)
                _warn_unlisted(ctx, catalog, resources)
                if query.queried:
                    locations = _matching(ctx, resources, spec, query)
                    if not locations:
                        ctx.diag.warning("no resource matches --filter/--search; nothing to report")
                        return None
    return lint_report(
        [resources],
        config=lint_config(Path.cwd(), tree=False),
        select=findings.select,
        ignore=findings.ignore,
        only=_only(spec, query.organization, resources),
        locations=locations,
    )


def _kind(catalog: Catalog, kind: str | None) -> ResourceSpec | None:
    """The spec ``--kind`` names, by CLI name or kind, as ``export --kind`` reads it."""
    if kind is None:
        return None
    try:
        return catalog.by_cli_name(kind)
    except ConfigError:
        pass
    try:
        return catalog.get(kind)
    except ConfigError as exc:
        raise UsageError(f"--kind: {exc}") from exc


def _check_filters(catalog: Catalog, spec: ResourceSpec | None, filters: dict[str, str]) -> None:
    """Refuse a ``--filter`` field no kind it lists has, before anything is read."""
    specs = [spec] if spec is not None else [catalog.get(kind) for kind in catalog.kinds()]
    if filters and all(filter_field_not_on_spec(filters, each) is not None for each in specs):
        field = filter_field_not_on_spec(filters, specs[0])
        where = spec.kind if spec is not None else "any kind"
        raise UsageError(f"--filter: {field!r} is not a field of {where}")


def _only(spec: ResourceSpec | None, organization: str | None, resources: Resources) -> list[str]:
    """The target globs ``--kind``/``--organization`` select: ``Kind/Org/**`` with ``*`` unset.

    A document that could not be read has no kind or organization to narrow
    by (its target is its path), so it is always reported: a narrowed run
    never passes over a file it could not read.
    """
    if spec is None and organization is None:
        return []
    kind = glob.escape(spec.kind) if spec is not None else "*"
    scope = glob.escape(organization) if organization is not None else "*"
    unread = sorted({glob.escape(each.path) for each in resources.unreadable})
    return [f"{kind}/{scope}/**", *unread]


def _snapshot(ctx: AwxContext, *, refresh: bool | None) -> Resources:
    """The controller's snapshot, filled when ``awx.caches.resources`` says it is due.

    The token is the partition: hashed into the file's name, never shown.
    A profile with no token reads what the controller shows anyone, which
    is its own partition, ``""``.
    """
    server = ctx.settings.server
    # The URL as 11.0 keyed it: no slash after a bare host.
    base_url = "" if server is None else str(server.url).rstrip("/")
    token = TOKEN.find(server)
    served = RESOURCES.serve(
        {base_url: urlparse(base_url).netloc or base_url},
        refresh=refresh,
        fill=partial(_fill, ctx),
        partition=token.get_secret_value() if token is not None else "",
    )
    return served.value[base_url]


def _fill(ctx: AwxContext, _key: str, progress: ProgressHandle) -> Filled[Resources]:
    resources = load_controller(
        client=ctx.repo,
        fk=ctx.fk,
        catalog=ctx.catalog,
        nodes=ctx.workflow_nodes,
        playbooks=ctx.repo,
        path_of=_path_of,
        progress=progress,
    )
    return Filled(value=resources, summary=plural(len(resources.items), "resource"))


def _path_of(spec: ResourceSpec, id_: int) -> str:
    return f"{awx_api_path(spec)}/{id_}"


def _warn_unlisted(ctx: AwxContext, catalog: Catalog, resources: Resources) -> None:
    """Say which kinds the token could not list: references to them were not checked."""
    unlisted = [kind for kind in catalog.kinds() if kind not in resources.loaded]
    if unlisted:
        ctx.diag.warning(
            f"the token cannot list {', '.join(unlisted)}; references to them were not checked"
        )


def _matching(
    ctx: AwxContext, resources: Resources, spec: ResourceSpec | None, query: Query
) -> list[str]:
    """The locations of the snapshot's resources the controller lists for the query.

    One listing per kind: ``--kind``'s, else every kind that has the filtered
    fields, of those the token could list. ``--organization`` scopes each
    listing as ``export`` does. Locations, not targets: two schedules of one
    name under two templates share a target, never a location.
    """
    kinds = [spec.kind] if spec is not None else ctx.catalog.kinds()
    specs = [ctx.catalog.get(kind) for kind in kinds if kind in resources.loaded]
    paths: set[str] = set()
    for each in specs:
        params = server_filters_for_spec(
            each, filters=query.filters, organization=query.organization
        )
        if filter_field_not_on_spec(params, each) is not None:
            continue
        if query.search is not None:
            params["search"] = query.search
        paths.update(_path_of(each, int(row["id"])) for row in ctx.repo.list(each, params=params))
    held = {getattr(item.location, "id", None) for item in resources.items}
    return sorted(paths & held)


__all__ = ["Query", "run_lint"]
