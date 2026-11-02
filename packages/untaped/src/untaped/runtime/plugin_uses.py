"""What a plugin's recorded imports and fills find in this install: missing, deprecated or fine.

Composition reads a plugin's imports (:mod:`untaped.runtime.plugin_imports`)
before importing the plugin and looks each name of untaped or another plugin
up here (:func:`look_up`). A module's names are complete (its ``__all__``,
its attributes, its old names, its submodules), so a name it doesn't have is
missing for certain:

- missing and not guarded: the plugin can't run here, and the verdict says
  why by the plugin's floor on the owner (:func:`missing_words`): a major
  between the floor and the installed version means the plugin is out of
  date, none means it needs a newer owner than it declares;
- a deprecated name (its mark, or an old-names entry): a :class:`UseDecl`,
  which the declaration keeps for ``doctor`` and the composing run warns about;
- a name of a disabled plugin, or a module whose import raises: the cascade,
  as a disabled requirement is.

:func:`filled_uses` does the same for the contract methods a plugin's
providers fill.
"""

from __future__ import annotations

import importlib
import importlib.util
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from untaped.runtime.declaration import UseDecl
from untaped.runtime.plugin_imports import ImportRecord
from untaped.runtime.requirements import applies, floor_of, is_plain, parse_requirement
from untaped.sdk._lazy import old_names_of
from untaped.sdk.marks import Deprecated, class_mark, descriptor_function, function_mark


@dataclass(frozen=True)
class Owner:
    """Who a top-level package belongs to: untaped, or a plugin's distribution."""

    distribution: str
    #: The plugin's name; ``untaped`` for core.
    plugin: str
    #: Core's own: a deprecation names its major bare (``14.0``), not the distribution's.
    core: bool = False


@dataclass(frozen=True)
class Missing:
    """An import naming what its owner doesn't have."""

    record: ImportRecord
    owner: Owner


@dataclass(frozen=True)
class Cascade:
    """An import from a plugin that is disabled, or a module whose import raises."""

    record: ImportRecord
    owner: Owner
    #: What raised importing the module; ``None`` when its plugin is disabled.
    error: str | None = None


@dataclass
class Verdict:
    """What one plugin's imports find: the first problem, and every deprecated name."""

    missing: list[Missing] = field(default_factory=list)
    cascade: Cascade | None = None
    uses: list[UseDecl] = field(default_factory=list)


def look_up(
    records: Iterable[ImportRecord],
    owners: Mapping[str, Owner],
    disabled: Iterable[str] = (),
) -> Verdict:
    """Look each record of untaped or a plugin up; ``disabled`` names disabled distributions.

    A record of any other package (a library) is not untaped's business. The
    first cascade stops the lookup: the plugin is disabled with its owner.
    """
    verdict = Verdict()
    off = {str(canonicalize_name(each)) for each in disabled}
    for record in records:
        owner = owner_of(record.module, owners)
        if owner is None:
            continue
        found = _one(record, owner, off)
        if isinstance(found, Cascade):
            verdict.cascade = found
            return verdict
        if isinstance(found, Missing):
            verdict.missing.append(found)
        elif found is not None:
            verdict.uses.append(found)
    return verdict


def _one(record: ImportRecord, owner: Owner, off: set[str]) -> Missing | Cascade | UseDecl | None:
    """What one record finds; ``None`` when it is fine, or guarded and not there.

    A guarded import handles an ``ImportError`` of its own, so only a module
    failing otherwise disables the plugin then.
    """
    if str(canonicalize_name(owner.distribution)) in off:
        return None if record.guarded else Cascade(record, owner)
    try:
        module = importlib.import_module(record.module)
        if record.star or record.name is None:
            return None
        found, mark = _find(module, record.name)
    except ImportError as exc:
        if record.guarded:
            return None
        if isinstance(exc, ModuleNotFoundError) and _names(exc.name, record.module):
            return Missing(record, owner)
        return Cascade(record, owner, error=f"{type(exc).__name__}: {exc}")
    except Exception as exc:  # the owner's module fails to import or give the name: it is broken
        return Cascade(record, owner, error=f"{type(exc).__name__}: {exc}")
    if not found:
        return None if record.guarded else Missing(record, owner)
    if mark is None:
        return None
    return _use(f"sdk {record.module}.{record.name}", mark, owner, record=record)


def filled_uses(providers: Sequence[type], owners: Mapping[str, Owner]) -> list[UseDecl]:
    """Each deprecated contract method one of ``providers`` fills, as a use of its owner."""
    from untaped.sdk.contracts._declare import contract_of, fills  # noqa: PLC0415 - fills only

    uses: list[UseDecl] = []
    for provider in providers:
        info = contract_of(provider)
        if info is None:
            continue
        owner = owner_of(info.cls.__module__, owners)
        if owner is None:
            continue
        for name, method in info.methods.items():
            mark = function_mark(method.function)
            if isinstance(mark, Deprecated) and fills(provider, info, name):
                where = f"{provider.__module__}:{provider.__qualname__}.{name}"
                uses.append(
                    _use(f"contract {owner.plugin}.{info.name}.{name}", mark, owner, where=where)
                )
    return uses


def owner_of(module: str, owners: Mapping[str, Owner]) -> Owner | None:
    """Who owns ``module``: the owner of its longest package in ``owners`` (keyed by package).

    A plugin nested in ``untaped`` (``untaped.plugins.acme``) is its own owner, not core.
    """
    parts = module.split(".")
    for end in range(len(parts), 0, -1):
        found = owners.get(".".join(parts[:end]))
        if found is not None:
            return found
    return None


def missing_words(
    missing: Missing, *, distribution: str, requires_dist: Sequence[str], installed: str
) -> tuple[str, str]:
    """What a quarantine for ``missing`` says, and its fix, by the plugin's floor on the owner.

    ``installed`` is the owner's installed version (``""`` when unknown).

    Names plugins use go only at a major, so a major between the plugin's
    floor and the installed owner means the name went and the plugin is out
    of date; otherwise it is not there yet, and the plugin needs a newer
    owner than it declares. With no requirement on the owner both fixes are
    named.
    """
    record, owner = missing.record, missing.owner
    have = f"{owner.distribution} {installed}".strip()
    facts = f"{record.file}:{record.line} imports {record.spelled}, which {have} doesn't have"
    floor = _floor(requires_dist, owner.distribution)
    if floor is None:
        return (
            f"{facts}; {distribution} declares no requirement on {owner.distribution}",
            f"upgrade {owner.distribution}, or upgrade {distribution}",
        )
    if _major(installed) > _major(floor):
        return (
            f"{facts}: {distribution} is out of date (built for {owner.distribution} {floor})",
            f"upgrade {distribution}, or remove it",
        )
    return (
        f"{facts}: {distribution} may need a newer {owner.distribution} than it declares",
        f"upgrade {owner.distribution}",
    )


def notify_use(plugin: str, distribution: str, version: str, use: UseDecl) -> None:
    """Warn once that ``plugin`` (from ``distribution`` at ``version``) uses ``use``."""
    from untaped.sdk.marks import notify_deprecation  # noqa: PLC0415 - on a use only

    who = f"{distribution} {version}".strip()
    notify_deprecation(
        use.id,
        f"`{use_name(use)}`, which {who} {use.how} at {use.where},",
        removed_in=use.removed_in,
        owner=None if use.core else use.distribution,
        replacement=use.replacement,
        key=f"{plugin} {use.id}",
        hint=f"it keeps working until then; upgrade {distribution} once a release stops using it",
        plugin=plugin,
    )


def use_line(plugin: str, distribution: str, version: str, use: UseDecl) -> str:
    """Doctor's words for ``use``: what ``plugin`` uses, and what the release removing it does."""
    from untaped.sdk.marks import removal_text  # noqa: PLC0415 - doctor only
    from untaped.sdk.words import removal_phrase  # noqa: PLC0415

    owner = "untaped" if use.core else use.distribution
    removal = removal_text(use.removed_in, None if use.core else use.distribution)
    head = f"{distribution} {version}".strip()
    where = f"in `{use.where}`" if use.how == "calls" else use.where
    head = f"{head} {use.how} {use_name(use)} ({where}), {removal_phrase(removal)}"
    if use.removed_in is None:
        return f"{head}: upgrade {distribution} once a release stops using it"
    # A call is seen while a command runs; an import or a fill is read before the plugin loads.
    effect = f"makes `{use.where}` fail" if use.how == "calls" else f"disables {plugin}"
    return (
        f"{head}: upgrading {owner} to {use.removed_in}.0 {effect} "
        f"unless a newer {distribution} stops using it"
    )


def use_name(use: UseDecl) -> str:
    """The name ``use`` spells, without its kind: ``untaped.sdk.human_size``."""
    return use.id.partition(" ")[2]


def _find(module: object, name: str) -> tuple[bool, Deprecated | None]:
    """Whether ``module`` has ``name``, and the deprecation it carries when it does.

    The name is looked up as ``from module import name`` would: an attribute
    (a module ``__getattr__`` included), else a submodule. An old name is read
    from the module's table, never looked up, so finding it doesn't warn as a
    use would. Anything but ``AttributeError`` raised giving the name is the
    owner's failure, and propagates.
    """
    old = old_names_of(module)  # type: ignore[arg-type]
    if name in old:
        return True, old[name]
    try:
        return True, _mark(getattr(module, name))
    except AttributeError:
        pass
    if hasattr(module, "__path__"):  # a package: the name may be a submodule
        spec = importlib.util.find_spec(f"{getattr(module, '__name__', '')}.{name}")
        if spec is not None:
            return True, None
    return False, None


def _mark(obj: object) -> Deprecated | None:
    target = descriptor_function(obj)
    mark = class_mark(target) if isinstance(target, type) else function_mark(target)
    return mark if isinstance(mark, Deprecated) else None


def _use(
    identity: str,
    mark: Deprecated,
    owner: Owner,
    *,
    record: ImportRecord | None = None,
    where: str = "",
) -> UseDecl:
    from untaped.sdk._replacement import sdk_replacement_text  # noqa: PLC0415 - on a use only

    replacement = sdk_replacement_text(mark)
    return UseDecl(
        id=identity,
        how="fills" if record is None else "imports",
        distribution=owner.distribution,
        core=owner.core,
        removed_in=mark.removed_in,
        replacement=replacement,
        where=where if record is None else f"{record.file}:{record.line}",
    )


def _names(missing: str | None, module: str) -> bool:
    """Whether a missing module ``missing`` is ``module`` or a package on its way."""
    return missing is not None and (module == missing or module.startswith(f"{missing}."))


def _floor(requires_dist: Sequence[str], distribution: str) -> str | None:
    """The plugin's floor on ``distribution``: its highest lower bound; ``None`` without one."""
    wanted = canonicalize_name(distribution)
    floors: list[str] = []
    for line in requires_dist:
        parsed = parse_requirement(line)
        if parsed is None or not is_plain(parsed) or canonicalize_name(parsed.name) != wanted:
            continue
        try:
            if not applies(parsed):
                continue
        except Exception:
            continue
        floors.extend(floor for spec in parsed.specifier if (floor := floor_of(spec)))
    return max(floors, key=_version, default=None)


def _version(text: str) -> Version:
    try:
        return Version(text)
    except InvalidVersion:
        return Version("0")


def _major(text: str) -> int:
    return _version(text).major


__all__ = [
    "Cascade",
    "Missing",
    "Owner",
    "Verdict",
    "filled_uses",
    "look_up",
    "missing_words",
    "notify_use",
    "owner_of",
    "use_line",
    "use_name",
]
