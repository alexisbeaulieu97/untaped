"""Root ``untaped plugin`` group: what is installed, and how contracts are filled.

``plugin list`` reports one record per candidate
provider — ``name/status/distribution/version``, in name order — from the
composition outcome: ready rows for committed plugins plus quarantined
rows carrying the entry-point name (or the ``unknown`` sentinels when the
provider never resolved). The listing never touches settings, so invalid
plugin values cannot block it. ``plugin list --contracts`` lists every
contract method instead: its owner, stability and providers.

``plugin rank`` writes the order of a contract method's providers into the
owner's ``extensions`` settings; ``plugin schema`` prints a record kind's
JSON Schema. Both, and ``--contracts``, import ``untaped.contracts`` only
when they run.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Annotated, Any, ClassVar, Literal

from cyclopts import App, Parameter
from pydantic import TypeAdapter, ValidationError

from untaped.cli import (
    ColumnsOption,
    DryRunOption,
    FormatOption,
    create_app,
    echo,
    emit,
    report_errors,
    writes,
)
from untaped.config.models import SettingOutcome
from untaped.config.repository import SettingsFileRepository
from untaped.errors import ConfigError, UsageError, first_validation_error
from untaped.management._render import emit_isolated
from untaped.messages import command_line, not_found
from untaped.plugins.registry import (
    CompositionResult,
    PluginCandidate,
    candidate_distribution,
    owns_contracts,
    run_deferred_factory,
)
from untaped.records import Record, record_kinds, record_model
from untaped.settings import ContractName, ExtensionSettings
from untaped.theme import OutputFormat
from untaped.ui import ui_context

if TYPE_CHECKING:
    from untaped.contracts._declare import ContractInfo

_UNKNOWN = "unknown"

#: Shown by root ``--help`` and an empty ``plugin list`` when a bare
#: ``untaped`` install has no plugin providers.
INSTALL_HINT = (
    "No plugins are installed. Reinstall untaped with an extra: 'untaped[all]' "
    "for all of them, or one, e.g. 'untaped[awx]'."
)


def build_root_plugin_app(
    *,
    result: CompositionResult,
    candidates: Sequence[PluginCandidate],
) -> App:
    """Return the root ``plugin`` group for one composition."""
    app = create_app(name="plugin", help="Inspect installed plugins and their contracts.")

    @app.command(name="list")
    def list_command(
        *,
        contracts: Annotated[
            bool,
            Parameter(
                name="--contracts",
                negative="",
                help="List each contract method instead: its owner, stability and providers.",
            ),
        ] = False,
        fmt: FormatOption = "table",
        columns: ColumnsOption = None,
    ) -> None:
        """List installed plugins and quarantined providers, one row each."""
        with report_errors():
            if contracts:
                emit(
                    _contract_rows(),
                    fmt=fmt,
                    columns=columns,
                    empty="No installed plugin declares a contract.",
                )
            else:
                _show(result, candidates, fmt=fmt, columns=columns)

    @app.command(name="rank")
    @writes
    def rank_command(
        contract: Annotated[str, Parameter(help="The contract, as OWNER.CONTRACT.")],
        method: Annotated[str, Parameter(help="The contract method whose providers to order.")],
        /,
        *plugins: Annotated[
            str,
            Parameter(name="PLUGINS", help="Providers, first first; none removes the ranking."),
        ],
        dry_run: DryRunOption = False,
        fmt: FormatOption = "table",
        columns: ColumnsOption = None,
    ) -> None:
        """Order the providers of a contract method in the profile (the active one or --profile).

        An unranked provider ranks below every ranked one; ranking none
        removes the method's ranking.
        """
        with report_errors():
            outcome = _rank(result, contract, method, list(plugins), dry_run=dry_run)
            emit(outcome, fmt=fmt, columns=columns)

    @app.command(name="schema")
    def schema_command(
        kind: Annotated[str, Parameter(help="A record kind, such as workspace.repo.")],
        /,
    ) -> None:
        """Print a record kind's JSON Schema."""
        with report_errors():
            echo(json.dumps(_schema(kind, result), indent=2))

    return app


class ContractRow(Record, kind="untaped.contract"):
    """One method of an installed contract and who fills it."""

    table_columns: ClassVar[tuple[str, ...]] = (
        "contract",
        "method",
        "stability",
        "providers",
        "ranked",
    )

    contract: str
    """``owner.contract``."""
    method: str
    owner: str
    stability: Literal["stable", "experimental", "deprecated"]
    """The method's own mark, else its contract's."""
    providers: list[str]
    """The plugins whose usable providers fill the method, in the order they are asked."""
    ranked: list[str]
    """The profile's explicit ranking (``plugin rank``)."""


def _contract_rows() -> list[ContractRow]:
    from untaped.contracts._registry import (  # noqa: PLC0415 - only when listing contracts
        method_providers,
        owned_contracts,
        rankings,
        unreadable_owners,
    )
    from untaped.contracts._schema import method_stability  # noqa: PLC0415

    rows: list[ContractRow] = []
    owned = owned_contracts()
    for owner in unreadable_owners():
        ui_context(strict=False).message(
            "warning", f"{owner}'s contracts couldn't be read; `{command_line('doctor')}` says why"
        )
    for owner, infos in sorted(owned.items()):
        try:
            extensions: Mapping[str, ExtensionSettings] = rankings(owner)
        except ConfigError as exc:
            ui_context(strict=False).message("warning", f"rankings not shown: {exc}")
            extensions = {}
        for info in sorted(infos, key=lambda each: each.name):
            extension = extensions.get(info.name)
            for method in info.methods:
                order = [] if extension is None else list(extension.rank.get(method, ()))
                rows.append(
                    ContractRow(
                        contract=f"{owner}.{info.name}",
                        method=method,
                        owner=owner,
                        stability=method_stability(info, method),
                        providers=method_providers(info, method, order),
                        ranked=order,
                    )
                )
    return rows


_EXTENSIONS = TypeAdapter(dict[ContractName, ExtensionSettings])


def _rank(
    result: CompositionResult, contract: str, method: str, plugins: list[str], *, dry_run: bool
) -> SettingOutcome:
    """Write ``plugins`` as the ranking of ``contract`` (``owner.contract``) ``method``.

    No plugins removes the ranking. Only the owner must exist then: removing
    is how doctor repairs a ranking of a contract or method that doesn't.
    """
    owner, _, name = contract.partition(".")
    if not owner or not name:
        raise UsageError(f"name the contract as OWNER.CONTRACT, not {contract!r}")
    try:
        _EXTENSIONS.validate_python({name: {"rank": {method: plugins}}})
    except ValidationError as exc:
        raise UsageError(f"invalid ranking: {first_validation_error(exc)}") from None
    owners = sorted(each.spec.name for each in result.plugins if owns_contracts(each.spec))
    if owner not in owners:
        raise UsageError(
            f"{owner!r} owns no contract (contract owners: {', '.join(owners) or 'none'})",
            hint=f"run `{command_line('plugin list --contracts')}`",
        )
    if plugins:
        _check_rankable(owner, name, method, plugins)
    changed = False

    def update(_target: str, current: Any) -> str | None:
        nonlocal changed
        extensions = _ranked(_mapping(current), name, method, plugins)
        changed = extensions != current and bool(extensions or current)
        return json.dumps(extensions) if extensions else None

    key = f"{owner}.extensions"
    path = f"{key}.{name}.rank.{method}"
    profile = SettingsFileRepository().update_value(key, update, dry_run=dry_run)
    ui = ui_context(strict=False)
    if not changed:
        ui.message("info", f"{path} unchanged in profile {profile}")
        action = "unchanged"
    elif dry_run:
        action = "planned"
    else:
        done = f"ranked {' > '.join(plugins)}" if plugins else "removed the ranking"
        ui.success(f"{done} for {owner}.{name} {method} (profile {profile})")
        action = "updated" if plugins else "deleted"
    return SettingOutcome(key=key, profile=profile, action=action)


def _mapping(value: Any) -> dict[str, Any]:
    """``value`` as a mapping to edit; anything else (``null``, a stray list) starts empty."""
    return dict(value) if isinstance(value, dict) else {}


def _ranked(
    extensions: dict[str, Any], name: str, method: str, plugins: list[str]
) -> dict[str, Any]:
    """``extensions`` with ``name``'s ``method`` ranked as ``plugins`` (none: unranked).

    A malformed contract or ``rank`` value is replaced, so ranking repairs it;
    an emptied contract entry is dropped.
    """
    entry = _mapping(extensions.get(name))
    ranks = _mapping(entry.get("rank"))
    if plugins:
        ranks[method] = plugins
    else:
        ranks.pop(method, None)
    if ranks:
        entry["rank"] = ranks
    else:
        entry.pop("rank", None)
    if entry:
        extensions[name] = entry
    else:
        extensions.pop(name, None)
    return extensions


def _check_rankable(owner: str, name: str, method: str, plugins: list[str]) -> None:
    """Refuse a contract or method that doesn't exist; warn about a plugin that doesn't fill it."""
    from untaped.contracts._registry import (  # noqa: PLC0415 - only when ranking
        method_providers,
        owned_contracts,
    )

    owned = owned_contracts()
    if owner not in owned:
        raise UsageError(
            f"{owner}'s contracts couldn't be read", hint=f"run `{command_line('doctor')}`"
        )
    info: ContractInfo | None = next((each for each in owned[owner] if each.name == name), None)
    if info is None:
        known = ", ".join(sorted(each.name for each in owned[owner]))
        raise UsageError(f"{owner} declares no contract {name!r} (it declares {known})")
    rankable = [each for each, declared in info.methods.items() if not declared.bridge]
    if method not in rankable:
        what = "is a bridge, which is never ranked" if method in info.methods else "isn't a method"
        raise UsageError(f"{owner}.{name} {method} {what} (ranked methods: {', '.join(rankable)})")
    filling = set(method_providers(info, method, ()))
    for plugin in plugins:
        if plugin not in filling:
            ui_context(strict=False).message(
                "warning",
                f"{plugin} doesn't fill {owner}.{name} {method} here; the ranking keeps it "
                "for when it does",
            )


def _schema(kind: str, result: CompositionResult) -> dict[str, Any]:
    """``kind``'s JSON Schema, with ``$id`` and ``title`` the kind."""
    model = record_model(kind) or _load_kind(kind, result)
    if model is None:
        prefix = kind.partition(".")[0]
        known = sorted(each for each in record_kinds() if each.partition(".")[0] == prefix)
        raise UsageError(not_found("record kind", kind, known=known))
    schema = model.model_json_schema()
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": kind,
        **schema,
        "title": kind,
    }


def _load_kind(kind: str, result: CompositionResult) -> type[Record] | None:
    """Import what declares record kinds lazily (contracts, then commands), then look again."""
    if any(owns_contracts(each.spec) or each.spec.provides for each in result.plugins):
        from untaped.contracts._registry import every_offer, owned_contracts  # noqa: PLC0415

        owned_contracts()
        every_offer()
        if (model := record_model(kind)) is not None:
            return model
    for registered in result.plugins:
        if registered.spec.app_factory is not None:
            run_deferred_factory(registered)
    return record_model(kind)


def _show(
    result: CompositionResult,
    candidates: Sequence[PluginCandidate],
    *,
    fmt: OutputFormat,
    columns: list[str] | None,
) -> None:
    rows = _rows(result, candidates)
    emit_isolated(
        rows,
        fmt=fmt,
        columns=columns,
        kind="untaped.plugin",
        table_columns=["name", "status", "distribution", "version"],
    )
    if not rows:
        # ``sdk.hint`` only renders ``hint: run `<command>```, so print the prefix directly.
        echo(f"hint: {INSTALL_HINT}", err=True)


def _rows(
    result: CompositionResult,
    candidates: Sequence[PluginCandidate],
) -> list[dict[str, object]]:
    # A distribution declares each entry-point name once, and a provider whose
    # spec name differs from it is quarantined, so the reported (distribution,
    # name) finds the candidate of every row, a provider that never resolved
    # and a blank distribution (reported as ``unknown``) included.
    versions = {
        (candidate_distribution(item), item.name): item.distribution_version or _UNKNOWN
        for item in candidates
    }
    ready = [
        (registered.spec.name, "ready", registered.plugin_ref.distribution)
        for registered in result.plugins
    ]
    quarantined = [
        (record.name, "quarantined", record.distribution) for record in result.quarantine
    ]
    return [
        {
            "name": name,
            "status": status,
            "distribution": distribution,
            "version": versions.get((distribution, name), _UNKNOWN),
        }
        for name, status, distribution in sorted(
            ready + quarantined, key=lambda row: (row[0], row[2])
        )
    ]


__all__ = ["INSTALL_HINT", "build_root_plugin_app"]
