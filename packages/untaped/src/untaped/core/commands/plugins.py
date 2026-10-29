"""``untaped plugin``: what is installed, and how contracts are filled.

``plugin list`` prints one ``untaped.installed_plugin`` record per candidate
plugin, in name order, from the composition: ready rows for committed
plugins plus quarantined rows carrying the entry-point name (or the
``unknown`` sentinels when the entry point never resolved). The listing never
touches settings, so invalid plugin values cannot block it. ``plugin
contracts`` lists every contract method instead: its owner, stability and
providers.

``plugin rank`` writes the order of a contract method's providers into the
owner's ``extensions`` settings; ``plugin schema`` prints a record kind's
JSON Schema. ``plugin new`` scaffolds a plugin filling a contract. These, and
``plugin contracts``, import ``untaped.sdk.contracts`` only when they run.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import BaseModel, Field

from untaped.core.commands.records import ScaffoldOutcome
from untaped.core.config.models import SettingOutcome
from untaped.core.settings import CoreSettings
from untaped.runtime.registry import CompositionResult
from untaped.sdk.errors import ConfigError, UsageError
from untaped.sdk.fields import Column, DryRun, Output, Positional
from untaped.sdk.plugin import Command, Group
from untaped.sdk.plugin_records import InstalledPlugin
from untaped.sdk.records import Record, record_model
from untaped.sdk.words import not_found, run_hint

if TYPE_CHECKING:
    from untaped.runtime.settings import ExtensionSettings
    from untaped.sdk._run import Diag
    from untaped.sdk.contracts._declare import ContractInfo


#: Shown by root ``--help`` and an empty ``plugin list`` when a bare
#: ``untaped`` install has no plugins.
INSTALL_HINT = (
    "No plugins are installed. Reinstall untaped with an extra: 'untaped[all]' "
    "for all of them, or one by its name, 'untaped[NAME]'."
)


class List(Command[CoreSettings]):
    """List installed and quarantined plugins, one row each."""

    output: Output[InstalledPlugin]

    def run(self) -> Iterator[InstalledPlugin]:
        rows = self.ctx.plugins
        if all(row.root for row in rows):
            self.ctx.diag.hint(INSTALL_HINT)
        yield from rows


class ContractMethod(Record):
    """One method of an installed contract and who fills it."""

    contract: Annotated[str, Column] = Field(description="`owner.contract`.")
    method: Annotated[str, Column]
    owner: str
    stability: Annotated[Literal["stable", "experimental", "deprecated"], Column] = Field(
        description="The method's own mark, else its contract's."
    )
    providers: Annotated[list[str], Column] = Field(
        description=(
            "The plugins whose usable providers fill the method, in the order they are asked."
        )
    )
    ranked: Annotated[list[str], Column] = Field(
        description="The profile's explicit ranking (`plugin rank`)."
    )


class Contracts(Command[CoreSettings]):
    """List each method of the installed contracts: its owner, stability and providers."""

    output: Output[ContractMethod]

    def run(self) -> Iterator[ContractMethod]:
        self.output.empty("No installed plugin declares a contract.")
        yield from _contract_methods(self.ctx.diag)


class Rank(Command[CoreSettings]):
    """Order the providers of a contract method in the profile (the active one or --profile).

    An unranked provider ranks below every ranked one; ranking none
    removes the method's ranking.
    """

    contract: Positional[str] = Field(description="The contract, as OWNER.CONTRACT.")
    method: Positional[str] = Field(description="The contract method whose providers to order.")
    plugins: Positional[list[str]] = Field(
        default_factory=list, description="Providers, first first; none removes the ranking."
    )
    dry_run: DryRun
    output: Output[SettingOutcome]

    def run(self) -> SettingOutcome:
        from untaped.core.commands.doctor import Check  # noqa: PLC0415 - a start's cost
        from untaped.core.commands.notes import print_notes  # noqa: PLC0415 - config's cost
        from untaped.core.config.repository import SettingsFileRepository  # noqa: PLC0415
        from untaped.core.config.use_cases import RankProviders  # noqa: PLC0415
        from untaped.runtime.registry import contract_owners  # noqa: PLC0415

        rank = RankProviders(
            SettingsFileRepository(),
            contract_owners(_composition()),
            contracts_hint=run_hint(Contracts.spelled()),
            doctor_hint=run_hint(Check.spelled()),
        )
        change = rank(self.contract, self.method, list(self.plugins), dry_run=bool(self.dry_run))
        print_notes(change.notes)
        return change.outcome


class New(Command[CoreSettings]):
    """Scaffold a plugin package that fills an installed owner's contract.

    The package composes and follows the conventions as written: fill the
    provider's stubs, add samples to its assert_fills test, then run
    untaped lint on it. A plugin with only commands starts from the
    repository's examples/untaped-hello instead.
    """

    name: Positional[str] = Field(description="The plugin name; the package is untaped-NAME.")
    fills: str = Field(
        description="The contract the plugin fills, as OWNER.CONTRACT (plugin contracts)."
    )
    path: Path = Field(Path(), description="Where to create untaped-NAME.")
    dry_run: DryRun
    output: Output[ScaffoldOutcome]

    def run(self) -> Iterator[ScaffoldOutcome]:
        from untaped.core.commands.plugin_new import scaffold  # noqa: PLC0415 - imports contracts

        result = _composition()
        yield from scaffold(
            result,
            result.candidates,
            self.name,
            self.fills,
            path=self.path,
            dry_run=bool(self.dry_run),
        )


class Schema(Command[CoreSettings]):
    """Print a record kind's JSON Schema."""

    kind: Positional[str] = Field(description="A record kind, such as workspace.repo.")

    def run(self) -> str:
        return json.dumps(_schema(self.kind, _composition()), indent=2)


class PluginGroup(Group):
    """Inspect installed plugins and their contracts."""

    commands = (List, Contracts, Rank, New, Schema)


def _composition() -> CompositionResult:
    from untaped.runtime.bootstrap import composition  # noqa: PLC0415 - bootstrap is the root

    return composition()


def _contract_methods(diag: Diag) -> list[ContractMethod]:
    from untaped.core.commands.doctor import Check  # noqa: PLC0415 - a start's cost
    from untaped.runtime.contracts.registry import (  # noqa: PLC0415 - only when listing contracts
        method_providers,
        owned_contracts,
        rankings,
        unreadable_owners,
    )

    rows: list[ContractMethod] = []
    owned = owned_contracts()
    for owner in unreadable_owners():
        diag.warning(f"{owner}'s contracts couldn't be read", hint=run_hint(Check.spelled()))
    for owner, infos in sorted(owned.items()):
        try:
            extensions: Mapping[str, ExtensionSettings] = rankings(owner)
        except ConfigError as exc:
            diag.warning(f"rankings not shown: {exc}")
            extensions = {}
        for info in sorted(infos, key=lambda each: each.name):
            extension = extensions.get(info.name)
            for method in info.methods:
                order = [] if extension is None else list(extension.rank.get(method, ()))
                rows.append(
                    ContractMethod(
                        contract=f"{owner}.{info.name}",
                        method=method,
                        owner=owner,
                        stability=_stability(info, method),
                        providers=method_providers(info, method, order),
                        ranked=order,
                    )
                )
    return rows


def _stability(info: ContractInfo, name: str) -> Literal["stable", "experimental", "deprecated"]:
    """The method's own mark, else its contract's."""
    from untaped.sdk.marks import function_mark, stability_name  # noqa: PLC0415

    return stability_name(function_mark(info.methods[name].function) or function_mark(info.cls))


def _schema(kind: str, result: CompositionResult) -> dict[str, Any]:
    """``kind``'s JSON Schema, with ``$id`` and ``title`` the kind."""
    model = kind_model(kind, result)
    if model is None:
        known = result.mount.kinds
        raise UsageError(not_found("record kind", kind, known=_near(kind, known)))
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


def kind_model(kind: str, result: CompositionResult) -> type[BaseModel] | None:
    """The model declaring ``kind``: composition names every kind (``result``'s declaration).

    ``plugin schema`` and the ``guide-yaml`` convention find a kind's model here.
    """
    return record_model(kind) or result.mount.kinds.get(kind)


__all__ = ["INSTALL_HINT", "ContractMethod", "PluginGroup", "kind_model"]
