"""The rows an installed plugin is checked by: ``check(install)`` and doctor read them.

Every check runs against the installed code (a developer's editable install
included). The conventions read the plugin's package; the contract checks compose only the
plugin and the owners it provides for, so nothing else installed changes the
verdict:

- ``registration``: an offer the registry quarantined fails (one waiting for
  an owner that isn't installed passes, skipped);
- ``conformance``: the owner's own checks, a ``conformance(provider)``
  function in its ``testing`` module, on each ready provider;
- ``live``: one live call of each filled method taking no required
  arguments, every item round-tripped through JSON.

An owner gets a ``contracts`` row: its ``contracts`` function loads.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Iterator, Sequence
from importlib import import_module
from importlib.util import find_spec
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ValidationError

from untaped.core.commands.records import PluginCheck
from untaped.runtime.plugin_package import package_of
from untaped.runtime.registry import CompositionResult, PluginCandidate, PluginSpec, owns_contracts
from untaped.sdk.errors import UsageError, first_validation_error
from untaped.sdk.words import not_found, plural

if TYPE_CHECKING:
    from untaped.runtime.contracts.registry import BoundProvider


def check_plugins(
    result: CompositionResult,
    candidates: Sequence[PluginCandidate],
    name: str | None,
) -> list[PluginCheck]:
    """Every finding for plugin ``name``, or for every installed plugin when it is ``None``.

    The conventions that read only the plugin's files are lint's, not run here.
    """
    from untaped.runtime.bootstrap import kept_composition  # noqa: PLC0415 - bootstrap is the root

    installed = sorted({candidate.name for candidate in candidates})
    if name is not None and name not in installed:
        raise UsageError(not_found("plugin", name, known=installed))
    rows: list[PluginCheck] = []
    with kept_composition():
        for each in [name] if name is not None else installed:
            rows += _check(each, result, candidates)
    return rows


def _check(
    name: str,
    result: CompositionResult,
    candidates: Sequence[PluginCandidate],
) -> list[PluginCheck]:
    def row(check: str, title: str, status: str, detail: str = "") -> PluginCheck:
        return PluginCheck(plugin=name, check=check, title=title, status=status, detail=detail)

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


type _Row = Callable[..., PluginCheck]


def _conventions(name: str, candidates: Sequence[PluginCandidate], row: _Row) -> list[PluginCheck]:
    # rules import the root
    from untaped.core.plugin_check.rules import plugin_violations  # noqa: PLC0415

    try:
        found = plugin_violations(name, candidates=candidates)
    except LookupError as exc:
        return [row("conventions", "plugin-name", "fail", str(exc))]
    if not found:
        return [row("conventions", "conventions", "pass", "no violations")]
    rows: list[PluginCheck] = []
    for line in found:
        where, rule, detail = [*line.split("::", 2), "", ""][:3]
        rows.append(row("conventions", rule, "fail", f"{where}: {detail}" if detail else where))
    return rows


def _hermetic(names: Sequence[str], candidates: Sequence[PluginCandidate]) -> list[PluginCandidate]:
    return [candidate for candidate in candidates if candidate.name in names]


def _contracts(name: str, candidates: Sequence[PluginCandidate], row: _Row) -> list[PluginCheck]:
    from untaped.runtime.bootstrap import composed_with  # noqa: PLC0415
    from untaped.runtime.contracts.registry import (  # noqa: PLC0415
        owned_contracts,
        unreadable_owners,
    )

    with composed_with(_hermetic([name], candidates)):
        if name in unreadable_owners():
            return [row("contracts", name, "fail", "its contracts function fails; see doctor")]
        names = sorted(info.name for info in owned_contracts().get(name, []))
    return [row("contracts", name, "pass", f"declares {', '.join(names) or 'no contract'}")]


def _offers(
    spec: PluginSpec, candidates: Sequence[PluginCandidate], row: _Row
) -> list[PluginCheck]:
    from untaped.runtime.bootstrap import composed_with  # noqa: PLC0415
    from untaped.runtime.contracts.registry import BoundProvider, every_offer  # noqa: PLC0415
    from untaped.sdk.contracts import Reason  # noqa: PLC0415

    installed = {candidate.name for candidate in candidates}
    owners = [owner for owner in spec.provides if owner in installed]
    rows: list[PluginCheck] = []
    with composed_with(_hermetic([spec.name, *owners], candidates)) as composed:
        unusable = _unusable_owners(owners, composed)
        rows += [row("registration", owner, "fail", why) for owner, why in unusable.items()]
        for entry in every_offer():
            owner = entry.binding.owner if isinstance(entry, BoundProvider) else entry.owner
            if entry.plugin != spec.name or owner in unusable:
                continue
            if not isinstance(entry, BoundProvider):
                title = (
                    entry.owner if entry.contract is None else _what(entry.owner, entry.contract)
                )
                if entry.reason == Reason.owner_not_installed:
                    rows.append(row("registration", title, "pass", f"skipped: {entry.detail}"))
                else:
                    rows.append(
                        row("registration", title, "fail", f"{entry.reason}: {entry.detail}")
                    )
                continue
            rows += _provider(entry, composed, candidates, row)
    return rows


def _unusable_owners(owners: Sequence[str], composed: CompositionResult) -> dict[str, str]:
    """Installed owners whose contracts can't be read: quarantined, or ``contracts`` fails."""
    from untaped.runtime.contracts.registry import unreadable_owners  # noqa: PLC0415

    quarantined = {record.name: record for record in composed.quarantine}
    broken = set(unreadable_owners())
    found: dict[str, str] = {}
    for owner in owners:
        if owner in quarantined:
            record = quarantined[owner]
            found[owner] = f"{owner} is quarantined ({record.reason}): {record.detail}"
        elif owner in broken:
            found[owner] = f"{owner}'s contracts function fails; see doctor"
    return found


def _what(owner: str, contract: type) -> str:
    from untaped.sdk.contracts._declare import contract_of  # noqa: PLC0415

    info = contract_of(contract)
    return f"{owner}.{'?' if info is None else info.name}"


def _provider(
    provider: BoundProvider,
    composed: CompositionResult,
    candidates: Sequence[PluginCandidate],
    row: _Row,
) -> list[PluginCheck]:
    from untaped.runtime.contracts.registry import ready_of  # noqa: PLC0415

    ready = ready_of(provider)
    waiting = None
    if ready is not None:
        waiting = f"skipped: not ready: {ready.reason}" + (
            f" (set {ready.spelled})" if ready.spelled else ""
        )
    conformance = _conformance(provider, composed, candidates, waiting, row)
    return [*conformance, *_live(provider, waiting, row)]


def _conformance(
    provider: BoundProvider,
    composed: CompositionResult,
    candidates: Sequence[PluginCandidate],
    waiting: str | None,
    row: _Row,
) -> Iterator[PluginCheck]:
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


def _live(provider: BoundProvider, waiting: str | None, row: _Row) -> Iterator[PluginCheck]:
    from untaped.sdk.contracts._declare import fills  # noqa: PLC0415

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
            yield _call(provider, name, title, row)


def _call(provider: BoundProvider, name: str, title: str, row: _Row) -> PluginCheck:
    from untaped.sdk.contracts import Errored, Ok, gather  # noqa: PLC0415

    binding = provider.binding
    try:
        method = getattr(binding.contract.cls, name)
        answers = gather(method, plugins={binding.plugin}, refresh=True)()
    except Exception as exc:
        return row("live", title, "fail", _message(exc))
    answers.take_stale()  # a stale answer is this row's failure, not a warning
    answer = next((each for each in answers if each.plugin == binding.plugin), None)
    if isinstance(answer, Errored):
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
    return row("live", title, "pass", plural(len(items), "item"))


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


def _message(exc: object) -> str:
    text = str(exc).rstrip(": ")
    return text if text else type(exc).__name__
