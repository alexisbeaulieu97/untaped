"""``untaped plugin check``: an installed plugin follows the conventions and fills its contracts.

Every check runs against the installed code (a developer's editable install
included). The conventions, the import lint among them, read the plugin's
package; the contract checks compose only the plugin and the owners it
provides for, so nothing else installed changes the verdict:

- ``registration``: an offer the registry quarantined fails (one waiting for
  an owner that isn't installed passes, skipped);
- ``conformance``: the owner's own checks, a ``conformance(provider)``
  function in its ``testing`` module, on each ready provider;
- ``live``: one live call of each filled method taking no required
  arguments, every item round-tripped through JSON;
- ``fills``: the ``assert_fills`` checks on the live items;
- ``schema``: the owner schema hash the provider recorded (``fills.json``)
  against the installed owner's; a difference is ``owner-schema-drift``,
  reported, never failed.

An owner gets a ``contracts`` row: its ``contracts`` function loads.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Iterator, Sequence
from importlib import import_module
from importlib.util import find_spec
from typing import TYPE_CHECKING, Any, ClassVar

from pydantic import BaseModel, ValidationError

from untaped.cli import echo
from untaped.errors import ExitCode, UsageError, first_validation_error
from untaped.management._render import emit_check_list, emit_isolated
from untaped.messages import not_found, summary
from untaped.plugins.registry import (
    CompositionResult,
    PluginCandidate,
    PluginSpec,
    owns_contracts,
)
from untaped.records import CheckRecord, kind_of
from untaped.theme import OutputFormat

if TYPE_CHECKING:
    from untaped.contracts._registry import Provider


class PluginCheckRow(CheckRecord, kind="untaped.plugin_check"):
    """One finding of ``untaped plugin check``."""

    table_columns: ClassVar[tuple[str, ...]] = ("plugin", "check", "title", "status", "detail")

    plugin: str
    check: str
    """``conventions``, ``registration``, ``conformance``, ``live``, ``fills``, ``schema`` or
    ``contracts``."""
    title: str
    """What it is about: a convention rule, ``owner.contract`` or ``owner.contract.method``."""
    detail: str = ""


def check_plugins(
    result: CompositionResult, candidates: Sequence[PluginCandidate], name: str | None
) -> list[PluginCheckRow]:
    """Every finding for plugin ``name``, or for every installed plugin when it is ``None``."""
    from untaped.bootstrap import kept_composition  # noqa: PLC0415 - bootstrap is the root

    installed = sorted({candidate.name for candidate in candidates})
    if name is not None and name not in installed:
        raise UsageError(not_found("plugin", name, known=installed))
    rows: list[PluginCheckRow] = []
    with kept_composition(quiet=True):
        for each in [name] if name is not None else installed:
            rows += _check(each, result, candidates)
    return rows


def report_check_rows(
    rows: Sequence[PluginCheckRow], *, fmt: OutputFormat, columns: list[str] | None
) -> None:
    """Show the findings (a checklist by plugin in a table) and a footer; exit 1 on a failure."""
    data: list[dict[str, object]] = [row.model_dump(mode="json") for row in rows]
    if fmt == "table" and columns is None:
        emit_check_list(data, run_line=" ".join)
        echo(err=True)
    else:
        emit_isolated(data, fmt=fmt, columns=columns, kind=kind_of(PluginCheckRow))
    counts = {status: sum(row.status == status for row in rows) for status in _STATUSES}
    echo(summary("plugin check", counts), err=True)
    if counts["fail"]:
        raise SystemExit(ExitCode.FAILURE)


_STATUSES = ("pass", "warn", "fail")


def _check(
    name: str, result: CompositionResult, candidates: Sequence[PluginCandidate]
) -> list[PluginCheckRow]:
    def row(check: str, title: str, status: str, detail: str = "") -> PluginCheckRow:
        return PluginCheckRow(plugin=name, check=check, title=title, status=status, detail=detail)

    quarantined = next((record for record in result.quarantine if record.name == name), None)
    if quarantined is not None:
        return [row("registration", quarantined.reason, "fail", quarantined.detail)]
    rows = _conventions(name, candidates, row)
    spec = next(plugin.spec for plugin in result.plugins if plugin.spec.name == name)
    if owns_contracts(spec):
        rows += _contracts(name, candidates, row)
    if spec.provides:
        rows += _offers(spec, candidates, row)
    return rows


type _Row = Callable[..., PluginCheckRow]


def _conventions(
    name: str, candidates: Sequence[PluginCandidate], row: _Row
) -> list[PluginCheckRow]:
    from untaped.conventions import plugin_violations  # noqa: PLC0415 - imports the root

    try:
        found = plugin_violations(name, candidates=candidates)
    except LookupError as exc:
        return [row("conventions", "plugin-name", "fail", str(exc))]
    if not found:
        return [row("conventions", "conventions", "pass", "no violations")]
    rows: list[PluginCheckRow] = []
    for line in found:
        where, rule, detail = [*line.split("::", 2), "", ""][:3]
        rows.append(row("conventions", rule, "fail", f"{where}: {detail}" if detail else where))
    return rows


def _hermetic(names: Sequence[str], candidates: Sequence[PluginCandidate]) -> list[PluginCandidate]:
    return [candidate for candidate in candidates if candidate.name in names]


def _contracts(name: str, candidates: Sequence[PluginCandidate], row: _Row) -> list[PluginCheckRow]:
    from untaped.bootstrap import composed_with  # noqa: PLC0415
    from untaped.contracts._registry import owned_contracts, unreadable_owners  # noqa: PLC0415

    with composed_with(_hermetic([name], candidates), quiet=True):
        if name in unreadable_owners():
            return [row("contracts", name, "fail", "its contracts function fails; see doctor")]
        names = sorted(info.name for info in owned_contracts().get(name, []))
    return [row("contracts", name, "pass", f"declares {', '.join(names) or 'no contract'}")]


def _offers(
    spec: PluginSpec, candidates: Sequence[PluginCandidate], row: _Row
) -> list[PluginCheckRow]:
    from untaped.bootstrap import composed_with  # noqa: PLC0415
    from untaped.contracts._registry import Provider, every_offer  # noqa: PLC0415

    installed = {candidate.name for candidate in candidates}
    owners = [owner for owner in spec.provides if owner in installed]
    rows: list[PluginCheckRow] = []
    with composed_with(_hermetic([spec.name, *owners], candidates), quiet=True) as composed:
        for entry in every_offer():
            if entry.plugin != spec.name:
                continue
            if not isinstance(entry, Provider):
                title = (
                    entry.owner if entry.contract is None else _what(entry.owner, entry.contract)
                )
                if entry.reason == "owner-not-installed":
                    rows.append(row("registration", title, "pass", f"skipped: {entry.detail}"))
                else:
                    rows.append(
                        row("registration", title, "fail", f"{entry.reason}: {entry.detail}")
                    )
                continue
            rows += _provider(entry, composed, candidates, row)
    return rows


def _what(owner: str, contract: type) -> str:
    from untaped.contracts._declare import contract_of  # noqa: PLC0415

    info = contract_of(contract)
    return f"{owner}.{'?' if info is None else info.name}"


def _provider(
    provider: Provider,
    composed: CompositionResult,
    candidates: Sequence[PluginCandidate],
    row: _Row,
) -> list[PluginCheckRow]:
    from untaped.contracts._registry import ready_of  # noqa: PLC0415

    binding = provider.binding
    what = f"{binding.owner}.{binding.contract.name}"
    ready = ready_of(provider)
    waiting = None
    if ready is not None:
        waiting = f"skipped: not ready: {ready.reason}" + (
            f" (set {ready.setting})" if ready.setting else ""
        )
    rows = list(_conformance(provider, composed, candidates, waiting, row))
    samples: list[object] = []
    for line in _live(provider, waiting, samples, row):
        rows.append(line)
    rows.append(_fills(provider, samples, row))
    rows.append(_schema(provider, what, row))
    return rows


def _conformance(
    provider: Provider,
    composed: CompositionResult,
    candidates: Sequence[PluginCandidate],
    waiting: str | None,
    row: _Row,
) -> Iterator[PluginCheckRow]:
    binding = provider.binding
    what = f"{binding.owner}.{binding.contract.name}"
    check = _owner_conformance(binding.owner, composed, candidates)
    if check is None:
        return
    if waiting is not None:
        yield row("conformance", what, "pass", waiting)
        return
    try:
        check(provider.instance)
    except Exception as exc:
        yield row("conformance", what, "fail", _message(exc))
        return
    yield row("conformance", what, "pass", f"{binding.owner}'s conformance checks pass")


def _owner_conformance(
    owner: str, composed: CompositionResult, candidates: Sequence[PluginCandidate]
) -> Callable[[object], object] | None:
    """``conformance`` from the owner package's ``testing`` module, when it has one."""
    from untaped.conventions import package_of  # noqa: PLC0415

    spec = next((plugin.spec for plugin in composed.plugins if plugin.spec.name == owner), None)
    if spec is None:
        return None
    target = next((c.target for c in candidates if c.name == owner), None)
    try:
        package = package_of(spec, target)[0]
        module_name = f"{package}.testing"
        if find_spec(module_name) is None:
            return None
        found = getattr(import_module(module_name), "conformance", None)
    except ImportError, LookupError, ValueError:
        return None
    return found if callable(found) else None


def _live(
    provider: Provider, waiting: str | None, samples: list[object], row: _Row
) -> Iterator[PluginCheckRow]:
    from untaped.contracts._declare import fills  # noqa: PLC0415

    binding = provider.binding
    info = binding.contract
    for name, method in info.methods.items():
        if method.bridge or not fills(type(provider.instance), info, name):
            continue
        title = f"{binding.owner}.{info.name}.{name}"
        parameters = list(inspect.signature(method.function).parameters.values())[1:]
        if any(parameter.default is parameter.empty for parameter in parameters):
            yield row("live", title, "pass", "skipped: it needs arguments")
        elif waiting is not None:
            yield row("live", title, "pass", waiting)
        else:
            yield _call(provider, name, title, samples, row)


def _call(
    provider: Provider, name: str, title: str, samples: list[object], row: _Row
) -> PluginCheckRow:
    from untaped.contracts import Failed, Ok, gather  # noqa: PLC0415

    binding = provider.binding
    try:
        answers = gather(getattr(binding.contract.cls, name), refresh=True)()
    except Exception as exc:
        return row("live", title, "fail", _message(exc))
    answer = next((each for each in answers if each.plugin == binding.plugin), None)
    if isinstance(answer, Failed):
        return row("live", title, "fail", _message(answer.error))
    if not isinstance(answer, Ok):
        reason = "not asked" if answer is None else f"{answer.reason}: {answer.detail}"
        return row("live", title, "fail", reason)
    if answer.stale is not None:
        return row("live", title, "fail", f"served a cached answer: {_message(answer.stale.error)}")
    items = answer.value if isinstance(answer.value, list) else [answer.value]
    problems = [*answer.invalid, *_round_trips(items)]
    if problems:
        return row("live", title, "fail", "; ".join(problems))
    samples += _samples(provider, items)
    return row("live", title, "pass", f"{len(items)} item{'' if len(items) == 1 else 's'}")


def _round_trips(items: Sequence[Any]) -> Iterator[str]:
    for index, item in enumerate(items, 1):
        if not isinstance(item, BaseModel):
            continue
        dumped = item.model_dump_json(by_alias=True, round_trip=True)
        try:
            back = type(item).model_validate_json(dumped, strict=True)
        except ValidationError as exc:
            yield f"item {index} doesn't read back from JSON: {first_validation_error(exc)}"
            continue
        if back != item:
            yield f"item {index} changes in a JSON round trip"


def _samples(provider: Provider, items: Sequence[Any]) -> list[object]:
    """The provider's own records behind ``items``: what its bridge turned into them."""
    binding = provider.binding
    if binding.owns_item:
        return [item for item in items if isinstance(item, BaseModel)]
    found: list[object] = []
    for item in items:
        source = getattr(item, "source", None)
        if source is not None and source.record:
            found.append(dict(source.record))
    return found


def _fills(provider: Provider, samples: Sequence[object], row: _Row) -> PluginCheckRow:
    from untaped.contracts._fills import fills_problems  # noqa: PLC0415

    what = f"{provider.binding.owner}.{provider.binding.contract.name}"
    if not samples:
        return row("fills", what, "pass", "skipped: no live items to check")
    problems = fills_problems(provider, samples)
    if problems:
        return row("fills", what, "fail", "; ".join(problems))
    return row("fills", what, "pass", f"{len(samples)} live item{'' if len(samples) == 1 else 's'}")


def _schema(provider: Provider, what: str, row: _Row) -> PluginCheckRow:
    from untaped.contracts._fills import current_hash, recorded_hash  # noqa: PLC0415

    recorded = recorded_hash(provider)
    if recorded is None:
        return row("schema", what, "warn", "no recorded owner schema hash; assert_fills records it")
    if recorded != current_hash(provider):
        return row(
            "schema",
            what,
            "warn",
            f"owner-schema-drift: tested against another {what} schema; "
            "rerun its tests against this owner",
        )
    return row("schema", what, "pass", "tested against this owner's schema")


def _message(exc: BaseException) -> str:
    text = str(exc)
    return text if text else type(exc).__name__
