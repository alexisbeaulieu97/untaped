"""Running ``awx lint``: load the snapshot its origin calls for, then lint it.

FILES ``--offline`` never opens the awx context (it would build the HTTP
client and read the profile): the catalog alone resolves a batch. FILES
online and the controller origin open it once.
"""

from __future__ import annotations

import glob
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from urllib.parse import urlparse

from untaped.sdk import (
    ConfigError,
    Filled,
    OutputFormat,
    ProgressHandle,
    plugin_dir,
    plural,
    raise_usage,
    report_errors,
)
from untaped_awx import SPEC
from untaped_awx.application.lint.controller import load_controller
from untaped_awx.application.lint.documents import load_documents
from untaped_awx.application.resources.ports import Catalog
from untaped_awx.application.resources.save_resources import (
    filter_field_not_on_spec,
    server_filters_for_spec,
)
from untaped_awx.cli.context import AwxContext, open_context
from untaped_awx.domain import ResourceSpec
from untaped_awx.domain.lint.resources import AwxResources, subject_of
from untaped_awx.infrastructure.lint import snapshot_store
from untaped_awx.infrastructure.lint.documents import documents_of, file_texts
from untaped_awx.infrastructure.resources.catalog import AwxResourceCatalog
from untaped_awx.infrastructure.resources.spec import awx_api_path
from untaped_awx.settings import RESOURCES
from untaped_lint.api import lint, lint_config


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
    select: list[str],
    ignore: list[str],
    strict: bool,
    fmt: OutputFormat,
    columns: list[str] | None,
) -> None:
    """Lint the origin ``files``/``offline`` name; prints the rows and exits (lint's codes)."""
    catalog = AwxResourceCatalog()
    spec = _kind(catalog, query.kind)
    _check_filters(catalog, spec, query.filters)
    locations: list[str] = []
    with report_errors():
        if files and offline:
            resources = load_documents(documents_of(file_texts(files)), catalog=catalog)
        else:
            with open_context() as ctx:
                if files:
                    texts = documents_of(file_texts(files))
                    resources = load_documents(texts, catalog=catalog, live=ctx.fk)
                else:
                    resources = _snapshot(ctx, refresh=refresh)
                    _warn_unlisted(ctx, catalog, resources)
                    if query.queried:
                        locations = _matching(ctx, resources, spec, query)
                        if not locations:
                            ctx.diag.warning(
                                "no resource matches --filter/--search; nothing to report"
                            )
                            return
        lint(
            [subject_of(resources)],
            config=lint_config(Path.cwd(), tree=False),
            select=select,
            ignore=ignore,
            only=_only(spec, query.organization, resources),
            locations=locations,
            strict=strict,
            fmt=fmt,
            columns=columns,
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
        raise_usage(f"--kind: {exc}")


def _check_filters(catalog: Catalog, spec: ResourceSpec | None, filters: dict[str, str]) -> None:
    """Refuse a ``--filter`` field no kind it lists has, before anything is read."""
    specs = [spec] if spec is not None else [catalog.get(kind) for kind in catalog.kinds()]
    if filters and all(filter_field_not_on_spec(filters, each) is not None for each in specs):
        field = filter_field_not_on_spec(filters, specs[0])
        where = spec.kind if spec is not None else "any kind"
        raise_usage(f"--filter: {field!r} is not a field of {where}")


def _only(
    spec: ResourceSpec | None, organization: str | None, resources: AwxResources
) -> list[str]:
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


def _snapshot(ctx: AwxContext, *, refresh: bool | None) -> AwxResources:
    """The controller's snapshot, filled when ``awx.caches.resources`` says it is due."""
    base_url = str(ctx.settings.base_url)
    token = ctx.settings.token
    path = snapshot_store.store_path(
        plugin_dir(SPEC),
        profile=ctx.profile,
        base_url=base_url,
        token=token.get_secret_value() if token is not None else "",
    )
    if not path.exists() and RESOURCES.freshness(base_url).refreshed_at is not None:
        RESOURCES.forget(base_url)  # the data went away: what the sidecar says no longer holds
    served = RESOURCES.serve(
        {base_url: urlparse(base_url).netloc or base_url},
        refresh=refresh,
        fill=partial(_fill, ctx, path),
        read=partial(snapshot_store.read, path),
    )
    return served.value


def _fill(ctx: AwxContext, path: Path, _key: str, progress: ProgressHandle) -> Filled:
    resources = load_controller(
        client=ctx.repo,
        fk=ctx.fk,
        catalog=ctx.catalog,
        nodes=ctx.workflow_nodes,
        playbooks=ctx.repo,
        path_of=_path_of,
        progress=progress,
    )
    snapshot_store.write(path, resources)
    return Filled(summary=plural(len(resources.items), "resource"))


def _path_of(spec: ResourceSpec, id_: int) -> str:
    return f"{awx_api_path(spec)}/{id_}"


def _warn_unlisted(ctx: AwxContext, catalog: Catalog, resources: AwxResources) -> None:
    """Say which kinds the token could not list: references to them were not checked."""
    unlisted = [kind for kind in catalog.kinds() if kind not in resources.loaded]
    if unlisted:
        ctx.diag.warning(
            f"the token cannot list {', '.join(unlisted)}; references to them were not checked"
        )


def _matching(
    ctx: AwxContext, resources: AwxResources, spec: ResourceSpec | None, query: Query
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
