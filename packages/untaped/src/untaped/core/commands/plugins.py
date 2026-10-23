"""Root ``untaped plugin`` group: what is installed, and how contracts are filled.

``plugin list`` reports one record per candidate
plugin — ``name/status/distribution/version``, in name order — from the
composition outcome: ready rows for committed plugins plus quarantined
rows carrying the entry-point name (or the ``unknown`` sentinels when the
entry point never resolved). The listing never touches settings, so invalid
plugin values cannot block it. ``plugin list --contracts`` lists every
contract method instead: its owner, stability and providers.

``plugin rank`` writes the order of a contract method's providers into the
owner's ``extensions`` settings; ``plugin schema`` prints a record kind's
JSON Schema. ``plugin new`` scaffolds a plugin filling a contract and
``plugin check`` checks an installed one. These, and ``--contracts``, import
``untaped.sdk.contracts`` only when they run.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Annotated, Any, ClassVar, Literal

from cyclopts import App, Parameter
from pydantic import Field

from untaped.core.commands._render import emit_isolated
from untaped.core.commands.notes import print_notes
from untaped.core.config.repository import SettingsFileRepository
from untaped.core.config.use_cases import RankProviders
from untaped.runtime.registry import (
    CompositionResult,
    PluginCandidate,
    contract_owners,
    owns_contracts,
    run_deferred_factory,
)
from untaped.runtime.settings import ExtensionSettings
from untaped.runtime.sink import Diagnostics
from untaped.runtime.theme import OutputFormat
from untaped.sdk._cli import (
    ColumnsOption,
    DryRunOption,
    FormatOption,
    create_app,
    echo,
    emit,
    report_errors,
    writes,
)
from untaped.sdk.diagnostic import WireModel
from untaped.sdk.errors import ConfigError, UsageError
from untaped.sdk.marks import deprecated
from untaped.sdk.plugin_records import Plugin
from untaped.sdk.records import Record, record_kinds, record_model
from untaped.sdk.words import not_found, run_hint

_diag = Diagnostics()


#: Shown by root ``--help`` and an empty ``plugin list`` when a bare
#: ``untaped`` install has no plugins.
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
        """List installed and quarantined plugins, one row each."""
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
            owners = contract_owners(result)
            rank = RankProviders(SettingsFileRepository(), owners)
            change = rank(contract, method, list(plugins), dry_run=dry_run)
            print_notes(change.notes)
            emit(change.outcome, fmt=fmt, columns=columns)

    @app.command(name="new")
    @writes
    def new_command(
        name: Annotated[str, Parameter(help="The plugin name; the package is untaped-NAME.")],
        /,
        *,
        fills: Annotated[
            str,
            Parameter(
                name="--fills",
                help="The contract the plugin fills, as OWNER.CONTRACT (plugin list --contracts).",
            ),
        ],
        path: Annotated[
            Path, Parameter(name="--path", help="Where to create untaped-NAME.")
        ] = Path(),
        dry_run: DryRunOption = False,
        fmt: FormatOption = "table",
        columns: ColumnsOption = None,
    ) -> None:
        """Scaffold a plugin package that fills an installed owner's contract.

        The package composes and follows the conventions as written: fill the
        provider's stubs, add samples to its assert_fills test, then run
        plugin check NAME on the installed package. A plugin with only
        commands starts from the repository's examples/untaped-hello instead.
        """
        from untaped.core.commands.plugin_new import scaffold  # noqa: PLC0415 - imports contracts

        with report_errors():
            outcomes = scaffold(result, candidates, name, fills, path=path, dry_run=dry_run)
            emit(outcomes, fmt=fmt, columns=columns)

    @app.command(name="check")
    @deprecated(removed_in=12, replacement="untaped lint, from untaped[lint]")
    def check_command(
        name: Annotated[
            str | None, Parameter(help="The plugin to check; every installed one when left out.")
        ] = None,
        /,
        *,
        fmt: FormatOption = "table",
        columns: ColumnsOption = None,
    ) -> None:
        """Check an installed plugin: conventions, and each contract it fills, live.

        The contract checks compose only the plugin and its owners. Exits 1
        when a check fails; owner-schema-drift only warns. The file conventions
        run only when untaped[lint], which now owns them, is installed.
        """
        from untaped.conventions import (  # noqa: PLC0415 - imports the root
            FILE_RULES_MISSING,
            file_rules_installed,
        )
        from untaped.core.commands.plugin_check import (  # noqa: PLC0415 - imports contracts
            check_plugins,
            report_check_rows,
        )

        with report_errors():
            if not file_rules_installed():
                _diag.warning(FILE_RULES_MISSING, hint="install untaped[lint] and run untaped lint")
            rows = check_plugins(result, candidates, name)
            report_check_rows(rows, fmt=fmt, columns=columns)

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

    contract: str = Field(description="`owner.contract`.")
    method: str
    owner: str
    stability: Literal["stable", "experimental", "deprecated"] = Field(
        description="The method's own mark, else its contract's."
    )
    providers: list[str] = Field(
        description=(
            "The plugins whose usable providers fill the method, in the order they are asked."
        )
    )
    ranked: list[str] = Field(description="The profile's explicit ranking (`plugin rank`).")


def _contract_rows() -> list[ContractRow]:
    from untaped.runtime.contracts.registry import (  # noqa: PLC0415 - only when listing contracts
        method_providers,
        owned_contracts,
        rankings,
        unreadable_owners,
    )
    from untaped.sdk.contracts._schema import method_stability  # noqa: PLC0415

    rows: list[ContractRow] = []
    owned = owned_contracts()
    for owner in unreadable_owners():
        _diag.warning(f"{owner}'s contracts couldn't be read", hint=run_hint("doctor"))
    for owner, infos in sorted(owned.items()):
        try:
            extensions: Mapping[str, ExtensionSettings] = rankings(owner)
        except ConfigError as exc:
            _diag.warning(f"rankings not shown: {exc}")
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


def _schema(kind: str, result: CompositionResult) -> dict[str, Any]:
    """``kind``'s JSON Schema, with ``$id`` and ``title`` the kind."""
    model = kind_model(kind, result)
    if model is None:
        raise UsageError(not_found("record kind", kind, known=_near(kind, record_kinds())))
    schema = model.model_json_schema()
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": kind,
        **schema,
        "title": kind,
    }


def _near(kind: str, kinds: Iterable[str]) -> list[str]:
    """The kinds sharing ``kind``'s prefix, else every prefix as ``<prefix>.*``."""
    prefix = kind.partition(".")[0]
    kinds = sorted(kinds)
    same = [each for each in kinds if each.partition(".")[0] == prefix]
    return same or [f"{each}.*" for each in dict.fromkeys(k.partition(".")[0] for k in kinds)]


def kind_model(kind: str, result: CompositionResult) -> type[WireModel] | None:
    """The model declaring ``kind``, importing lazily what declares kinds (contracts, commands).

    ``plugin schema`` and the ``guide-yaml`` convention find a kind's model here.
    """
    if (model := record_model(kind)) is not None:
        return model
    if any(owns_contracts(each.spec) or each.spec.provides for each in result.plugins):
        from untaped.runtime.contracts.registry import every_offer, owned_contracts  # noqa: PLC0415

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
    emit_isolated(rows, fmt=fmt, columns=columns)
    if not rows:
        _diag.hint(INSTALL_HINT)


def _rows(
    result: CompositionResult,
    candidates: Sequence[PluginCandidate],
) -> list[Plugin]:
    from untaped.sdk.plugin_records import plugin_records  # noqa: PLC0415 - locates packages

    return plugin_records(result, candidates)


__all__ = ["INSTALL_HINT", "build_root_plugin_app"]
