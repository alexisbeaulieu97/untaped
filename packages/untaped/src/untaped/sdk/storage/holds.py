"""Holds: a holder holds an item an owner declared, and core releases it through the owner.

An **owner** is a plugin that lets others hold its items: it declares an
:class:`ItemStore` on ``PluginSpec.stores`` and says how a holder lets go
(``release=``) and whether a holder's move needs repair (``moved=``). A
**holder** is a reference in core's ``refs`` (:mod:`untaped.sdk.storage._refs`) of
some kind: a project (:mod:`untaped.sdk.projects`) today. A **hold** is one
:class:`~untaped.sdk.storage._refs.RefTarget` with ``item`` set in the holder's reference.

Core never knows what an item is. It keeps track of who holds what, and
releases through one service: plan (ask the owner for each hold, with every
other holder of the item), preview, confirm, then apply each plan on a fresh
ask taken under the item store's lock (``refs/.<plugin>-<store>.lock``, per
item). One refusal applies nothing; an apply may do less than its preview,
never more. Lock order, always: the holder's lock, the item store's, the
owner's own, then the ``refs`` block's lock for the moment a reference is rewritten.

Holder kinds other than a project are designed but internal
(:class:`HolderKind`): a plugin's kind is spelled ``<plugin>_<word>``, and a
kind whose plugin isn't installed reads as ``unreadable``, so its holds stay.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Collection, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, ClassVar, Literal, Self, cast

from pydantic import BaseModel, ConfigDict, Field, RootModel, model_validator

from untaped.sdk.diagnostic import Failure, _Omitted
from untaped.sdk.errors import ConfigError, UntapedError
from untaped.sdk.marks import experimental
from untaped.sdk.records import OutcomeRecord
from untaped.sdk.storage._fs import file_lock
from untaped.sdk.storage._refs import (
    INSTALL,
    PROJECT,
    REFS,
    Reference,
    RefHolder,
    RefTarget,
    locked,
    read_reference,
    read_references,
    reference_file,
    write_reference,
)

if TYPE_CHECKING:
    from untaped.runtime.registry import CompositionResult

#: Seconds a holder's lock or an item store's lock is waited for, then exit 5.
LOCK_WAIT = 600.0

_NAME = re.compile(r"[a-z][a-z0-9_]*")
_WORD = re.compile(r"[a-z][a-z0-9]*")

#: Where a holder's anchor stands, as read now: ``unreadable`` and ``missing`` keep their holds.
type HolderState = Literal["present", "unreadable", "missing"]

_STRICT = ConfigDict(frozen=True, extra="forbid", strict=True)


@experimental
class HolderView(BaseModel):
    """A holder as the home sees it now: who, how a row names it, its anchor, its state."""

    model_config = _STRICT

    holder: RefHolder
    label: Annotated[str, Field(min_length=1)]
    anchor: Path
    state: HolderState


@experimental
class UnknownHolder(BaseModel):
    """A reference that can't be read: it holds whatever it may hold."""

    model_config = _STRICT

    label: Annotated[str, Field(min_length=1)]


type Seen = HolderView | UnknownHolder


@experimental
class Refused(BaseModel):
    """An owner's "not now": why the hold can't be released, and what would let it go."""

    model_config = _STRICT

    reason: Annotated[str, Field(min_length=1)]
    hint: str | None = None


@experimental
class ReleaseAsk[T: str | RootModel[str]](BaseModel):
    """What an owner is asked when ``holder`` lets go of ``item``.

    ``others`` is every other holder of the item, read now; ``force`` says
    the user named this item with ``--force``.
    """

    model_config = _STRICT

    item: T
    holder: HolderView
    others: tuple[Seen, ...]
    force: bool

    @model_validator(mode="after")
    def _checked(self) -> Self:
        if self.holder.state not in {"present", "missing"}:
            raise ValueError(f"a holder that is {self.holder.state} releases nothing")
        if any(_same_holder(other, self.holder) for other in self.others):
            raise ValueError("the holder letting go is not one of the others")
        return self


@experimental
class MoveAsk[T: str | RootModel[str]](BaseModel):
    """What an owner is asked when ``holder`` moved from ``moved_from`` to its anchor."""

    model_config = _STRICT

    item: T
    holder: HolderView
    others: tuple[Seen, ...]
    moved_from: Path

    @model_validator(mode="after")
    def _checked(self) -> Self:
        if self.holder.state != "present":
            raise ValueError(f"a holder that is {self.holder.state} can't be repaired")
        if self.moved_from == self.holder.anchor:
            raise ValueError("moved_from is where the holder is now")
        if any(_same_holder(other, self.holder) for other in self.others):
            raise ValueError("the holder that moved is not one of the others")
        return self


@experimental
class LetGo[T: str | RootModel[str]](BaseModel):
    """An owner's plan to release a hold: what it will do, and the step that does it.

    ``apply`` gets a fresh :class:`ReleaseAsk`, taken under the item store's
    lock after the user confirmed, and returns the done row's text. It may
    do less than ``preview`` said (a delete may become a keep), never more:
    when the fresh ask needs more (it is now the last holder), it returns a
    :class:`Refused` and the hold stays. ``apply`` is never dumped, so plans
    compare by ``preview``.
    """

    model_config = _STRICT

    preview: Annotated[str, Field(min_length=1)]
    apply: Callable[[ReleaseAsk[T]], str | Refused] = Field(exclude=True, repr=False)


@experimental
class ItemStore[T: str | RootModel[str]]:
    """Items plugin ``plugin`` keeps for its holders, listed on ``PluginSpec.stores``.

    ``name`` is one settings segment, unique among the plugin's stores.
    ``item`` is the item's type: ``str``, or a ``RootModel[str]`` that
    normalises its text (``…/api`` and ``…/api.git`` are one hold).
    ``release`` plans how a holder lets go of an item (required: an owner
    that lets others hold its items says how they're released); ``moved``
    repairs what points into a holder that moved, or is ``None`` when
    nothing does (a required keyword: ``None`` is a choice). Both are module
    functions with a local import, as a migration's are. Two declarations
    with the same values are equal.
    """

    __slots__ = ("item", "moved", "name", "plugin", "release")

    def __init__(
        self,
        plugin: str,
        name: str,
        *,
        item: type[T] = str,  # type: ignore[assignment]
        release: Callable[[ReleaseAsk[T]], LetGo[T] | Refused],
        moved: Callable[[MoveAsk[T]], str | None] | None,
    ) -> None:
        from untaped.runtime.registry import check_plugin_name  # noqa: PLC0415 - no cycle

        check_plugin_name(plugin)
        if not _NAME.fullmatch(name):
            raise ConfigError(
                f"item store name {name!r} must be one settings segment (pattern {_NAME.pattern})"
            )
        where = f"item store {plugin}.{name}"
        if not (item is str or (isinstance(item, type) and issubclass(item, RootModel))):
            raise ConfigError(f"{where}: item must be str or a RootModel[str], not {item!r}")
        if not callable(release):
            raise ConfigError(f"{where}: release must be a function of a ReleaseAsk")
        if moved is not None and not callable(moved):
            raise ConfigError(f"{where}: moved must be a function of a MoveAsk, or None")
        self.plugin = plugin
        self.name = name
        self.item: type[T] = item
        self.release = release
        self.moved = moved

    def _identity(self) -> tuple[object, ...]:
        return (self.plugin, self.name, self.item, self.release, self.moved)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ItemStore):
            return NotImplemented
        return self._identity() == other._identity()

    def __hash__(self) -> int:
        return hash(self._identity())

    def __repr__(self) -> str:
        return f"ItemStore({self.plugin!r}, {self.name!r})"

    @property
    def qualified(self) -> str:
        """``<plugin>.<name>``: how a row or a message names the store."""
        return f"{self.plugin}.{self.name}"

    def _parse(self, value: T | str) -> T:
        """``value`` as this store's item, normalised; raises :class:`ValueError`."""
        text = value.root if isinstance(value, RootModel) else value
        if not isinstance(text, str) or not text:
            raise ValueError(f"an item of {self.qualified} is a non-empty string, not {value!r}")
        if self.item is str:
            return cast("T", text)
        parsed = cast("type[RootModel[str]]", self.item).model_validate(text)
        if not isinstance(parsed.root, str) or not parsed.root:
            raise ValueError(f"an item of {self.qualified} normalises to a non-empty string")
        return cast("T", parsed)

    def _text(self, item: T | str) -> str:
        """The item's normalised text, as a reference records it."""
        parsed = self._parse(item)
        return parsed.root if isinstance(parsed, RootModel) else parsed

    def target(self, item: T | str) -> RefTarget:
        """``item`` as a reference names it: ``{plugin, store, item}``."""
        return RefTarget(plugin=self.plugin, store=self.name, item=self._text(item))

    def holders(self, item: T | str) -> tuple[Seen, ...]:
        """Every holder of ``item``, read now; an unreadable reference is an UnknownHolder."""
        return holder_views(self.target(item))


# --- holder kinds (internal until a first-party consumer: the projects design's D8) ---------


class Sighting(BaseModel):
    """What a holder kind's ``inspect`` saw at an anchor: its state and how a row names it."""

    model_config = _STRICT

    state: HolderState
    label: Annotated[str, Field(min_length=1)]


#: The plugin holder kinds this process declared, by kind.
_KINDS: dict[str, HolderKind] = {}


class HolderKind:
    """A plugin's kind of holder, ``<plugin>_<word>``: where its anchor is and whether it's there.

    ``inspect(holder, anchor)`` is cheap and never raises (a raise reads as
    ``unreadable``, which keeps the holds). Declaring one makes its holders
    readable in this process; a kind nobody declared reads as unreadable.
    """

    def __init__(
        self, plugin: str, word: str, *, inspect: Callable[[RefHolder, Path], Sighting]
    ) -> None:
        from untaped.runtime.registry import check_plugin_name  # noqa: PLC0415 - no cycle

        check_plugin_name(plugin)
        if not _WORD.fullmatch(word):
            raise ConfigError(f"holder kind word {word!r} must be one word ({_WORD.pattern})")
        if not callable(inspect):
            raise ConfigError(f"holder kind {plugin} {word}: inspect must be a function")
        self.plugin = plugin
        self.word = word
        self.inspect = inspect
        self.kind = f"{plugin.replace('-', '_')}_{word}"
        _KINDS[self.kind] = self

    def holder(self, name: str, *, anchor: Path) -> Holder:
        """One holder of this kind, named ``name``, anchored at ``anchor``."""
        return Holder(RefHolder(kind=self.kind, name=name), anchor)


@dataclass(frozen=True)
class Holder:
    """A holder of a plugin's kind (internal): the service, as a project has it."""

    ref: RefHolder
    anchor: Path

    def locked(self) -> AbstractContextManager[None]:
        """Hold this holder's lock for the block: re-entrant; waits 600 s, then exit 5."""
        return holder_lock(self.ref, self.ref.describe())

    def hold(self, store: ItemStore[Any], item: object) -> None:
        """Hold ``item`` of ``store``; idempotent."""
        with self.locked():
            add_hold(self.ref, store, item, anchor=self.anchor)

    def holds[T: str | RootModel[str]](self, store: ItemStore[T]) -> tuple[T, ...]:
        """The items of ``store`` this holder holds."""
        return holds_of(self.ref, store)

    def release(
        self,
        store: ItemStore[Any],
        *,
        items: Collection[object] | None = None,
        force: Collection[object] = (),
        dry_run: bool = False,
    ) -> list[OutcomeRecord]:
        """Release this holder's holds of ``store`` through the service; the rows."""
        with self.locked():
            return release_rows(self.ref, store, items=items, force=force, dry_run=dry_run)


# --- reading holders -----------------------------------------------------------------


def view(ref: Reference) -> Seen:
    """How one reference reads now, as a holder."""
    if ref.opaque is not None or ref.holder is None or ref.anchor is None:
        return UnknownHolder(label=_opaque_label(ref))
    label = ref.describe()
    if ref.kind == PROJECT:
        from untaped.sdk.projects import (  # noqa: PLC0415 - projects imports this module
            anchor_state,
        )

        return HolderView(
            holder=ref.holder, label=label, anchor=ref.anchor, state=anchor_state(ref)
        )
    if ref.kind == INSTALL:
        state: HolderState = "present" if ref.live else "missing"
        return HolderView(holder=ref.holder, label=label, anchor=ref.anchor, state=state)
    kind = _KINDS.get(ref.kind)
    if kind is None:  # its plugin isn't installed: it holds what it holds
        return HolderView(
            holder=ref.holder,
            label=f"{label} (its plugin isn't installed)",
            anchor=ref.anchor,
            state="unreadable",
        )
    try:
        seen = kind.inspect(ref.holder, ref.anchor)
        if not isinstance(seen, Sighting):
            raise TypeError(f"inspect returned {type(seen).__name__}, not a Sighting")
    except Exception:  # a kind that can't tell keeps its holds
        return HolderView(holder=ref.holder, label=label, anchor=ref.anchor, state="unreadable")
    return HolderView(holder=ref.holder, label=seen.label, anchor=ref.anchor, state=seen.state)


def holder_views(target: RefTarget) -> tuple[Seen, ...]:
    """Every holder of ``target`` now, and every reference that can't be read."""
    found: list[Seen] = []
    for ref in read_references():
        if ref.opaque is not None:
            found.append(UnknownHolder(label=_opaque_label(ref)))
        elif target in ref.targets and (ref.kind != INSTALL or ref.live):
            found.append(view(ref))
    return tuple(found)


def holder_view(holder: RefHolder) -> Seen | None:
    """``holder``'s own reference as it reads now; ``None`` when it has none."""
    file = reference_file(holder)
    if not file.exists():
        return None
    return view(read_reference(file))


def holds_of[T: str | RootModel[str]](holder: RefHolder, store: ItemStore[T]) -> tuple[T, ...]:
    """The items of ``store`` ``holder``'s reference names, in its order."""
    return tuple(store._parse(target.item or "") for target in _held(holder, store))


def _held(holder: RefHolder, store: ItemStore[Any]) -> list[RefTarget]:
    file = reference_file(holder)
    if not file.exists():
        return []
    ref = read_reference(file)
    if ref.opaque is not None:
        raise ConfigError(
            f"cannot read what {holder.describe()} holds: {file} is {ref.opaque}",
            hint="upgrade untaped, or fix or remove that file",
        )
    return [
        target
        for target in ref.targets
        if (target.plugin, target.store) == (store.plugin, store.name) and target.item is not None
    ]


def _opaque_label(ref: Reference) -> str:
    from untaped.sdk.words import shown_path  # noqa: PLC0415 - no cycle

    return f"{shown_path(ref.file)} ({ref.opaque or 'unreadable'})"


def _same_holder(other: Seen, holder: HolderView) -> bool:
    return isinstance(other, HolderView) and other.holder == holder.holder


# --- locks and writes ------------------------------------------------------------------


def holder_lock(holder: RefHolder, name: str) -> AbstractContextManager[None]:
    """A holder's lock, ``refs/.<kind>-<name>.lock``: re-entrant; 600 s, then exit 5."""
    return _lock(holder_lock_file(holder).name, f"{holder.kind} {name}")


def holder_lock_file(holder: RefHolder) -> Path:
    """The file a holder's lock takes, ``refs/.<kind>-<name>.lock``."""
    return REFS.place / f".{holder.kind}-{holder.name}.lock"


def store_lock(store: ItemStore[Any]) -> AbstractContextManager[None]:
    """An item store's lock, ``refs/.<plugin>-<store>.lock``: re-entrant; 600 s, then exit 5."""
    return _target_lock(store.plugin, store.name)


def _target_lock(plugin: str, store: str) -> AbstractContextManager[None]:
    """The lock of item store ``<plugin>.<store>``, declared here or not (a forgotten hold's)."""
    return _lock(f".{plugin}-{store}.lock", f"the items of {plugin}.{store}")


@contextmanager
def _lock(file: str, what: str) -> Iterator[None]:
    folder = REFS.place
    try:
        folder.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ConfigError(f"could not create {folder}: {exc.strerror or exc}") from exc
    with file_lock(
        folder / file,
        timeout=LOCK_WAIT,
        error=ConfigError,
        busy=f"another untaped is changing {what}; try again",
        failed=f"could not lock {what}",
    ):
        yield


def add_hold(
    holder: RefHolder, store: ItemStore[Any], item: object, *, anchor: Path | None = None
) -> None:
    """Record that ``holder`` holds ``item`` of ``store`` (idempotent), under the store's lock.

    Refuses an item store no composed plugin lists (:class:`ConfigError`):
    core could never release that hold. ``anchor`` is where a holder with no
    reference yet is anchored (a project is always recorded first).
    """
    owners = Owners.current()
    if owners.stores.get((store.plugin, store.name)) != store:
        raise ConfigError(
            f"{store.qualified} is not an item store a composed plugin lists, "
            "so nothing could release a hold on it",
            hint=f"list it on plugin {store.plugin}'s PluginSpec.stores",
        )
    try:
        target = store.target(item)
    except ValueError as exc:
        raise ConfigError(f"cannot hold {item!r} of {store.qualified}: {exc}") from exc
    with store_lock(store):
        _rewrite(
            holder, lambda targets: targets if target in targets else (*targets, target), anchor
        )


def drop_hold(holder: RefHolder, target: RefTarget) -> None:
    """Remove ``target`` from ``holder``'s reference (only the service calls this)."""
    _rewrite(holder, lambda targets: tuple(t for t in targets if t != target), None)


def _rewrite(
    holder: RefHolder,
    change: Callable[[tuple[RefTarget, ...]], tuple[RefTarget, ...]],
    anchor: Path | None,
) -> None:
    file = reference_file(holder)
    with locked():
        if file.exists():
            ref = read_reference(file)
            if ref.opaque is not None:
                raise ConfigError(
                    f"cannot change what {holder.describe()} holds: {file} is {ref.opaque}",
                    hint="upgrade untaped, or fix or remove that file",
                )
        elif anchor is not None:
            ref = Reference(file, holder=holder, anchor=anchor)
        else:
            raise ConfigError(f"{holder.describe()} has no reference in {REFS.place}")
        targets = change(ref.targets)
        if targets == ref.targets and file.exists():
            return
        try:
            write_reference(
                holder,
                targets,
                anchor=ref.anchor,
                anchor_id=ref.anchor_id,
                moved_from=ref.moved_from,
                plugin=ref.plugin,
                version=ref.version,
            )
        except OSError as exc:
            raise ConfigError(f"could not write {file}: {exc.strerror or exc}") from exc


# --- the service -----------------------------------------------------------------------


@dataclass(frozen=True)
class Owners:
    """Who decides about each hold: the composed item stores, and the plugins that can't."""

    stores: Mapping[tuple[str, str], ItemStore[Any]] = field(default_factory=dict)
    composed: frozenset[str] = frozenset()
    quarantined: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def of(cls, result: CompositionResult) -> Owners:
        """The owners a composition offers."""
        stores = {
            (store.plugin, store.name): store
            for registered in result.plugins
            for store in registered.spec.stores
            if isinstance(store, ItemStore)
        }
        return cls(
            stores=stores,
            composed=frozenset(registered.spec.name for registered in result.plugins),
            quarantined={record.name: record.detail for record in result.quarantine},
        )

    @classmethod
    def current(cls) -> Owners:
        """The owners this run composed; none outside the CLI."""
        from untaped.runtime.bootstrap import composition  # noqa: PLC0415 - bootstrap is the root

        try:
            return cls.of(composition())
        except RuntimeError:
            return cls()

    def why_not(self, target: RefTarget) -> tuple[str, bool]:
        """Why no composed owner decides about ``target``, and whether ``--force`` may forget it."""
        plugin = target.plugin
        if plugin in self.quarantined:
            return f"{plugin} is broken ({self.quarantined[plugin]}): the hold stays", False
        if plugin in self.composed:
            return f"{plugin} declares no item store {target.store!r} any more", True
        return f"{plugin} is not installed: the hold stays", True


@dataclass(frozen=True)
class Plan:
    """One hold and its owner's answer: a :class:`LetGo`, a :class:`Refused`, or a failure.

    ``forget`` is a hold with no owner the user named with ``--force``: it
    is dropped without asking anyone. ``forced`` says the user named the
    item with ``--force``; ``forceable`` says naming it would turn a
    refusal into a plan.
    """

    target: RefTarget
    holder: HolderView
    owner: ItemStore[Any] | None = None
    answer: LetGo[Any] | Refused | None = None
    failure: Failure | None = None
    forceable: bool = False
    forced: bool = False
    forget: bool = False

    @property
    def item(self) -> str:
        return self.target.item or ""

    @property
    def blocked(self) -> bool:
        """Whether this hold stops the whole release (a refusal, or a plan that failed)."""
        return not self.forget and not isinstance(self.answer, LetGo)


class HoldOutcome(OutcomeRecord, kind="untaped.hold_outcome"):
    """One hold, or the holder itself, in a release, a move or a removal.

    ``holder`` names who lets go (``project PROJ-123``); ``plugin`` and
    ``item`` the hold (``untaped`` and a folder for the holder's own row).
    ``action`` is ``planned``, ``refused``, ``failed``, ``released``,
    ``forgotten``, ``repaired``, ``moved``, ``removed`` or ``skipped``.
    """

    table_columns: ClassVar[tuple[str, ...]] = ("plugin", "item", "action", "detail", "hint")

    holder: str
    plugin: str
    item: str
    detail: str
    hint: _Omitted = None


def plan_holds(
    holder: HolderView,
    targets: Sequence[RefTarget],
    *,
    force: Collection[str] = (),
    owners: Owners | None = None,
) -> list[Plan]:
    """Ask each hold's owner how ``holder`` lets go of it; never changes anything."""
    owners = Owners.current() if owners is None else owners
    plans: list[Plan] = []
    for target in targets:
        if target.item is None:
            continue
        store = owners.stores.get((target.plugin, target.store))
        named = any(names_item(name, target, store) for name in force)
        if store is None:
            reason, forgettable = owners.why_not(target)
            if forgettable and named:
                plans.append(Plan(target, holder, forget=True))
                continue
            hint = f"install {target.plugin}, or --force {target.item} forgets it"
            refusal = Refused(reason=reason, hint=hint if forgettable else None)
            plans.append(Plan(target, holder, answer=refusal, forceable=forgettable))
            continue
        plans.append(_plan_one(store, target, holder, force=named))
    return plans


def names_item(name: str, target: RefTarget, store: ItemStore[Any] | None) -> bool:
    """Whether ``--force NAME`` names ``target``'s item: as its owner normalises it, if composed."""
    if name == target.item:
        return True
    if store is None:
        return False
    try:
        return store._text(name) == target.item
    except ValueError:
        return False


def unnamed(force: Collection[str], plans: Sequence[Plan]) -> list[str]:
    """The ``--force`` names that name none of the planned holds."""
    return [
        name
        for name in force
        if not any(names_item(name, plan.target, plan.owner) for plan in plans)
    ]


def _plan_one(store: ItemStore[Any], target: RefTarget, holder: HolderView, *, force: bool) -> Plan:
    try:
        ask = release_ask(store, target.item or "", holder, force=force)
    except (ValueError, UntapedError) as exc:
        return Plan(target, holder, store, failure=_raised(exc, f"cannot read the hold: {exc}"))
    answer, failure = _ask(store, ask)
    if failure is not None:
        return Plan(target, holder, store, failure=failure)
    forceable = False
    if isinstance(answer, Refused) and not force:
        again, _ = _ask(store, ask.model_copy(update={"force": True}))
        forceable = isinstance(again, LetGo)
    return Plan(target, holder, store, answer=answer, forceable=forceable, forced=force)


def release_ask(
    store: ItemStore[Any], item: object, holder: HolderView, *, force: bool
) -> ReleaseAsk[Any]:
    """A :class:`ReleaseAsk` for ``item``, with every other holder read now."""
    parsed = store._parse(item)
    others = tuple(
        other for other in holder_views(store.target(parsed)) if not _same_holder(other, holder)
    )
    return ReleaseAsk(item=parsed, holder=holder, others=others, force=force)


def _ask(
    store: ItemStore[Any], ask: ReleaseAsk[Any]
) -> tuple[LetGo[Any] | Refused | None, Failure | None]:
    try:
        answer = store.release(ask)
    except Exception as exc:  # a plugin's bug is its row's failure, never the command's
        return None, _raised(exc)
    if not isinstance(answer, LetGo | Refused):  # a plugin's bug, whatever its annotation says
        return None, Failure.plain(  # type: ignore[unreachable]
            f"{store.qualified}'s release returned {type(answer).__name__}, not LetGo or Refused"
        )
    return answer, None


def preview_rows(plans: Sequence[Plan]) -> list[HoldOutcome]:
    """The plans as rows, before anything is applied."""
    rows = []
    for plan in plans:
        row = _row(plan, "planned", "")
        if plan.forget:
            rows.append(
                row.model_copy(
                    update={
                        "detail": "forgets the hold without releasing it: "
                        f"{plan.target.plugin}'s data stays where it keeps it"
                    }
                )
            )
        elif plan.failure is not None:
            rows.append(
                row.model_copy(
                    update={
                        "action": "failed",
                        "detail": plan.failure.message,
                        "error": plan.failure,
                    }
                )
            )
        elif isinstance(plan.answer, Refused):
            hint = plan.answer.hint
            if plan.forceable and plan.owner is not None:
                hint = f"{hint}, or --force {plan.item}" if hint else f"--force {plan.item}"
            rows.append(
                row.model_copy(
                    update={"action": "refused", "detail": plan.answer.reason, "hint": hint}
                )
            )
        elif isinstance(plan.answer, LetGo):
            rows.append(row.model_copy(update={"detail": plan.answer.preview}))
    return rows


def apply_plans(
    holder: RefHolder, plans: Sequence[Plan], *, now: Callable[[], Seen | None]
) -> list[HoldOutcome]:
    """Apply every plan, each on a fresh ask under its item store's lock; the done rows.

    ``now`` reads the holder as it is at that moment. A ``str`` from
    ``apply`` drops the hold; a :class:`Refused` (something changed since the
    preview) or a raise keeps it, and the rest still run.
    """
    rows: list[HoldOutcome] = []
    for plan in plans:
        if plan.forget:
            with _target_lock(plan.target.plugin, plan.target.store):
                drop_hold(holder, plan.target)
            rows.append(_row(plan, "forgotten", "forgot the hold without releasing it"))
            continue
        if plan.owner is None or not isinstance(plan.answer, LetGo):
            continue  # a blocked plan never gets here: the caller applies nothing
        rows.append(_apply_one(holder, plan, plan.owner, plan.answer, now))
    return rows


def _apply_one(
    holder: RefHolder,
    plan: Plan,
    store: ItemStore[Any],
    answer: LetGo[Any],
    now: Callable[[], Seen | None],
) -> HoldOutcome:
    with store_lock(store):
        seen = now()
        if not isinstance(seen, HolderView) or seen.state not in {"present", "missing"}:
            state = "gone" if seen is None else getattr(seen, "state", "unreadable")
            return _changed(plan, Refused(reason=f"{plan.holder.label} is {state} now"))
        try:
            fresh = release_ask(store, plan.item, seen, force=plan.forced)
            done = answer.apply(fresh)
        except Exception as exc:  # the hold stays; the other plans still run
            failure = _raised(exc)
            return _row(plan, "failed", failure.message).model_copy(update={"error": failure})
        if isinstance(done, Refused):
            return _changed(plan, done)
        if not isinstance(done, str) or not done:
            failure = Failure.plain(
                f"{store.qualified}'s apply returned {type(done).__name__}, not a row's text"
            )
            return _row(plan, "failed", failure.message).model_copy(update={"error": failure})
        drop_hold(holder, plan.target)
    return _row(plan, "released", done)


def _changed(plan: Plan, refusal: Refused) -> HoldOutcome:
    return _row(plan, "refused", f"changed since the preview: {refusal.reason}").model_copy(
        update={"hint": refusal.hint}
    )


def _row(plan: Plan, action: str, detail: str) -> HoldOutcome:
    return HoldOutcome(
        holder=plan.holder.label,
        plugin=plan.target.plugin,
        item=plan.item,
        action=action,
        detail=detail,
    )


def release_rows(
    holder: RefHolder,
    store: ItemStore[Any],
    *,
    items: Collection[object] | None,
    force: Collection[object],
    dry_run: bool,
    owners: Owners | None = None,
) -> list[OutcomeRecord]:
    """The service for one owner's holds, without a prompt: ``Project.release`` and its kin.

    Refused or failed plans apply nothing and come back as their rows; a
    dry run returns the preview.
    """
    seen = holder_view(holder)
    if not isinstance(seen, HolderView):
        raise ConfigError(
            f"cannot release what {holder.describe()} holds: its record can't be read"
        )
    wanted = None if items is None else {store._text(item) for item in items}
    targets = [t for t in _held(holder, store) if wanted is None or t.item in wanted]
    named = {store._text(item) for item in force}
    owners = owners if owners is not None else Owners.current()
    plans = plan_holds(seen, targets, force=named, owners=owners)
    if dry_run or any(plan.blocked for plan in plans):
        return list(preview_rows(plans))
    return list(apply_plans(holder, plans, now=lambda: holder_view(holder)))


def repair_holds(
    holder: RefHolder, targets: Collection[RefTarget], *, moved_from: Path
) -> tuple[dict[RefTarget | None, HoldOutcome], bool]:
    """Ask each hold's owner to repair a holder that moved from ``moved_from``.

    The rows, by hold (``None`` when the holder isn't there), say what each
    owner did; the flag is whether nothing is left to retry (no repair
    failed, and no owner is broken: once fixed, it repairs).
    """
    seen = holder_view(holder)
    if not isinstance(seen, HolderView) or seen.state != "present":
        why = "its record can't be read" if not isinstance(seen, HolderView) else seen.state
        failure = Failure.plain(f"cannot repair {holder.describe()}'s holds: {why}")
        label = seen.label if isinstance(seen, HolderView) else holder.describe()
        row = HoldOutcome(
            holder=label,
            plugin="untaped",
            item=holder.name,
            action="failed",
            detail=failure.message,
            error=failure,
        )
        return {None: row}, False
    owners = Owners.current()
    rows: dict[RefTarget | None, HoldOutcome] = {}
    done = True
    for target in targets:
        if target.item is None:
            continue
        store = owners.stores.get((target.plugin, target.store))
        row = HoldOutcome(
            holder=seen.label, plugin=target.plugin, item=target.item, action="skipped", detail=""
        )
        if store is None:
            reason, forgettable = owners.why_not(target)
            done = done and forgettable
            rows[target] = row.model_copy(update={"detail": f"{reason}; nothing repaired"})
            continue
        if store.moved is None:
            continue
        try:
            others = tuple(o for o in holder_views(target) if not _same_holder(o, seen))
            ask: MoveAsk[Any] = MoveAsk(
                item=store._parse(target.item), holder=seen, others=others, moved_from=moved_from
            )
            text = store.moved(ask)
        except Exception as exc:  # a plugin's failure is its row's; the move itself stands
            failure = _raised(exc)
            rows[target] = row.model_copy(
                update={"action": "failed", "detail": failure.message, "error": failure}
            )
            done = False
            continue
        if isinstance(text, str) and text:
            rows[target] = row.model_copy(update={"action": "repaired", "detail": text})
    return rows, done


def _raised(exc: BaseException, message: str | None = None) -> Failure:
    if isinstance(exc, UntapedError):
        return Failure.from_exception(exc, message=message)
    text = message or f"raised {type(exc).__name__}: {exc}"
    return Failure.from_exception(exc, message=text)


__all__ = [
    "HolderState",
    "HolderView",
    "ItemStore",
    "LetGo",
    "MoveAsk",
    "Refused",
    "ReleaseAsk",
    "UnknownHolder",
]
