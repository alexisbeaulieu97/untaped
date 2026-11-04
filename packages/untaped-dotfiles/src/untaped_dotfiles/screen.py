"""``show``'s screen: browse every package, turn packages and items on or off, pick
policies and answer plain inputs, then save once.

A screen from the screens kit (``docs/screens.md``): a tree of sources,
packages, their inputs and items on the left, the package under the cursor in
full on the right. ``space`` turns a package or item on or off, ``p`` cycles
the policy (the author's, ``sync``, ``once``, ``manual``), ``e`` edits a plain
input, ``ctrl-s`` saves every change in one write and closes, and esc closes
saving nothing. Nothing here runs a command from a source or places a file.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import cast

from rich.console import Group, RenderableType
from rich.text import Text

from untaped.screen.components.inputs import TextInput
from untaped.screen.components.layout import Panes
from untaped.screen.components.lists import Tree, TreeRow
from untaped.screen.core import (
    Back,
    Binding,
    Cmd,
    CmdError,
    Frame,
    Interrupt,
    KeyPress,
    Quit,
    Screen,
    Submit,
)
from untaped.sdk import plural
from untaped_dotfiles.policy import Policy
from untaped_dotfiles.records import ItemDetail, PackageDetail
from untaped_dotfiles.settings import InputValue, ItemChoice, PackageChoice
from untaped_values.api import Input, InputError, coerce

type At = tuple[str, str]
"""A package: ``(source, package)``."""
type Choices = Mapping[At, PackageChoice | None]

#: The rows an input being edited takes under the panes: its box and its help line.
_FIELD_ROWS = 4
#: The policies ``p`` steps through; ``None`` is the author's suggestion.
CYCLE: tuple[Policy | None, ...] = (None, Policy.SYNC, Policy.ONCE, Policy.MANUAL)


@dataclass(frozen=True)
class PackageView:
    """One package as the screen shows it: its detail when the screen opened, and its rules."""

    detail: PackageDetail
    spelled: str
    """How a command names it (``PKG``, or ``SOURCE/PKG`` when two sources share a name)."""
    refused: str | None = None
    """Why it can't be turned on here (its file doesn't load, or it doesn't apply)."""
    inputs: Mapping[str, Input] = field(default_factory=dict)

    @property
    def at(self) -> At:
        return (self.detail.source, self.detail.name)


@dataclass(frozen=True)
class Saved:
    """The write finished."""

    result: ShowResult


@dataclass(frozen=True)
class ShowResult:
    """What the screen saved: each package's new choice (``None``: off), and what to say."""

    changes: Choices = field(default_factory=dict)
    lines: tuple[str, ...] = ()
    turned_on: tuple[tuple[str, ...], ...] = ()
    """The packages or items turned on again: their entries are asked for, not new."""
    turned_off: tuple[tuple[str, ...], ...] = ()
    """The packages or items turned off: what they placed stays until ``remove``."""


@dataclass(frozen=True)
class Editing:
    at: At
    name: str
    field: TextInput


@dataclass(frozen=True)
class Model:
    packages: tuple[PackageView, ...]
    original: Choices
    choices: Choices
    expanded: frozenset[str]
    cursor: int = 0
    editing: Editing | None = None
    note: str = ""
    saving: bool = False

    def view_of(self, at: At) -> PackageView:
        return next(p for p in self.packages if p.at == at)


def show_screen(
    packages: Sequence[PackageView],
    choices: Choices,
    *,
    save: Callable[[ShowResult], None],
    command: str,
    alternative: str,
) -> Screen[Model, ShowResult]:
    """The screen over ``packages``, starting from this machine's ``choices``; ``save``
    writes what changed (inside a ``Cmd(write=True)``, so no key can cut it short)."""
    sources = {p.detail.source for p in packages}
    start = Model(
        packages=tuple(packages),
        original=dict(choices),
        choices=dict(choices),
        expanded=frozenset(_source_id(s) for s in sources),
    )

    def update(model: Model, message: object) -> tuple[Model, list[Cmd]]:
        return _update(model, message, save)

    def editing(model: Model) -> bool:
        return model.editing is not None

    return Screen(
        init=lambda: (start, []),
        update=update,
        view=_view,
        title="Dotfiles packages",
        command=command,
        alternative=alternative,
        keys=(
            Binding(" ", "on/off", None, when=lambda m: not editing(m)),
            Binding("p", "policy", None, when=lambda m: not editing(m)),
            Binding("e", "edit input", None, when=lambda m: not editing(m)),
        ),
        shared_labels={
            "ctrl-s": "save",
            "esc": lambda m: "cancel edit" if editing(m) else "quit",
            "enter": lambda m: "keep" if editing(m) else "open",
        },
    )


# -- update ------------------------------------------------------------------


def _update(
    model: Model, message: object, save: Callable[[ShowResult], None]
) -> tuple[Model, list[Cmd]]:
    if isinstance(message, Saved):
        return model, [Cmd.send(Quit(message.result))]
    if isinstance(message, CmdError):
        return replace(model, saving=False, note=f"not saved: {message.error}"), []
    if model.saving:  # every key waits: the screen closes once the write is done
        if isinstance(message, KeyPress | Back | Interrupt):
            return replace(model, note="saving; it closes when done"), []
        return model, []
    if model.editing is not None:
        return _edit(model, model.editing, message, save)
    if isinstance(message, Submit):
        return _save(model, save)
    if isinstance(message, Back):
        return model, [Cmd.send(Quit(ShowResult()))]
    if not isinstance(message, KeyPress):
        return model, []
    return _key(model, message), []


def _key(model: Model, message: KeyPress) -> Model:
    """A key on the tree: an action on the row under the cursor, or a move."""
    match message.name:
        case " ":
            return _toggled(model)
        case "p":
            return _cycled(model)
        case "e":
            return _editing(model)
    tree, _ = _tree(model).update(message)
    if tree.cursor == model.cursor and tree.expanded == model.expanded:
        return model
    return replace(model, cursor=tree.cursor, expanded=tree.expanded, note="")


def _edit(
    model: Model, editing: Editing, message: object, save: Callable[[ShowResult], None]
) -> tuple[Model, list[Cmd]]:
    if isinstance(message, KeyPress) and message.name == "esc":
        return replace(model, editing=None, note=""), []
    if isinstance(message, KeyPress) and message.name == "enter":
        return _kept(model, editing), []
    if isinstance(message, Submit):
        kept = _kept(model, editing)
        return (kept, []) if kept.editing is not None else _save(kept, save)
    edited, cmds = editing.field.update(message)
    if edited is editing.field and not cmds:
        return model, []
    return replace(model, editing=replace(editing, field=edited)), list(cmds)


def _kept(model: Model, editing: Editing) -> Model:
    """``model`` with the edited input answered (an empty answer drops it), or the error."""
    view = model.view_of(editing.at)
    choice = model.choices.get(editing.at)
    assert choice is not None  # only an input of a package that is on is edited
    text = editing.field.value
    inputs = dict(choice.inputs)
    if not text.strip():  # an empty answer: the default again, or no value
        inputs.pop(editing.name, None)
    else:
        try:
            value = coerce(view.inputs[editing.name], text, name=editing.name)
        except InputError as exc:
            return replace(
                model, editing=replace(editing, field=editing.field.with_error(str(exc)))
            )
        inputs[editing.name] = cast("InputValue", value)
    updated = choice.model_copy(update={"inputs": inputs})
    return replace(model, choices={**model.choices, editing.at: updated}, editing=None, note="")


def _save(model: Model, save: Callable[[ShowResult], None]) -> tuple[Model, list[Cmd]]:
    result = _result(model)
    if not result.changes:
        return model, [Cmd.send(Quit(ShowResult()))]

    def write() -> Saved:
        save(result)
        return Saved(result)

    return replace(model, saving=True, note="saving"), [Cmd(write, write=True, name="save")]


def _toggled(model: Model) -> Model:
    row = _row_at(model)
    if row is None or row.kind not in ("package", "item"):
        return model
    view = model.view_of(row.at)
    choice = model.choices.get(row.at)
    if row.kind == "package":
        if choice is not None:
            return _with(model, row.at, None)
        if view.refused is not None:
            return replace(model, note=f"{view.spelled} can't be turned on here: {view.refused}")
        return _with(model, row.at, model.original.get(row.at) or PackageChoice())
    item = _item(view, row.name)
    if choice is None:
        if view.refused is not None or item.excluded is not None:
            reason = view.refused or item.excluded
            return replace(model, note=f"{view.spelled}/{row.name} can't be turned on: {reason}")
        others = {
            i.name: ItemChoice(enabled=False) for i in view.detail.items if i.name != row.name
        }
        return _with(model, row.at, PackageChoice(items=others))
    own = choice.items.get(row.name, ItemChoice())
    if not own.enabled and item.excluded is not None:
        return replace(model, note=f"{view.spelled}/{row.name} can't be turned on: {item.excluded}")
    return _with(model, row.at, _item_choice(choice, row.name, enabled=not own.enabled))


def _cycled(model: Model) -> Model:
    row = _row_at(model)
    if row is None or row.kind not in ("package", "item"):
        return model
    choice = model.choices.get(row.at)
    name = model.view_of(row.at).spelled
    if choice is None:
        return replace(model, note=f"turn {name} on to choose its policy")
    if row.kind == "package":
        return _with(model, row.at, choice.model_copy(update={"policy": _next(choice.policy)}))
    own = choice.items.get(row.name, ItemChoice())
    if not own.enabled:
        return replace(model, note=f"turn {name}/{row.name} on to choose its policy")
    return _with(model, row.at, _item_choice(choice, row.name, policy=_next(own.policy)))


def _editing(model: Model) -> Model:
    row = _row_at(model)
    if row is None or row.kind != "input":
        return replace(model, note="move to an input to edit it") if row is not None else model
    view = model.view_of(row.at)
    choice = model.choices.get(row.at)
    if choice is None:
        return replace(model, note=f"turn {view.spelled} on to answer its inputs")
    spec = view.inputs[row.name]
    if spec.secret:
        return replace(model, note=f"{row.name} is a secret: it is never kept in config.yml")
    current = choice.inputs.get(row.name)
    shown = "" if current is None else _text(current)
    field_ = TextInput(label=row.name, value=shown, help=spec.description)
    return replace(model, editing=Editing(row.at, row.name, field_), note="")


def _with(model: Model, at: At, choice: PackageChoice | None) -> Model:
    return replace(model, choices={**model.choices, at: choice}, note="")


def _item_choice(
    choice: PackageChoice, item: str, *, enabled: bool | None = None, policy: object = ...
) -> PackageChoice:
    """``choice`` with ``item``'s exception changed, dropped when it is the default."""
    items = dict(choice.items)
    own = items.get(item, ItemChoice())
    update: dict[str, object] = {}
    if enabled is not None:
        update["enabled"] = enabled
    if policy is not ...:
        update["policy"] = policy
    own = own.model_copy(update=update)
    if own.enabled and own.policy is None:
        items.pop(item, None)
    else:
        items[item] = own
    return choice.model_copy(update={"items": items})


def _next(policy: Policy | None) -> Policy | None:
    return CYCLE[(CYCLE.index(policy) + 1) % len(CYCLE)]


# -- what changed --------------------------------------------------------------


def _changes(model: Model) -> dict[At, PackageChoice | None]:
    return {at: choice for at, choice in model.choices.items() if choice != model.original.get(at)}


def _result(model: Model) -> ShowResult:
    changes = _changes(model)
    lines: list[str] = []
    turned: list[tuple[str, ...]] = []
    off: list[tuple[str, ...]] = []
    for at, choice in changes.items():
        view = model.view_of(at)
        name = view.spelled
        before = model.original.get(at)
        if choice is None:
            lines.append(f"turned off {name}")
            off.append(at)
            continue
        if before is None:
            lines.append(f"turned on {name}" + _policy_note(choice.policy))
            turned.append(at)
            before = PackageChoice()
        elif choice.policy != before.policy:
            lines.append(f"policy of {name}: {_policy_name(choice.policy)}")
        for item in view.detail.items:
            was = before.items.get(item.name, ItemChoice())
            now = choice.items.get(item.name, ItemChoice())
            if now.enabled != was.enabled:
                lines.append(f"turned {'on' if now.enabled else 'off'} {name}/{item.name}")
                (turned if now.enabled else off).append((*at, item.name))
            if now.policy != was.policy:
                lines.append(f"policy of {name}/{item.name}: {_policy_name(now.policy, item=True)}")
        for input_name in view.inputs:
            if choice.inputs.get(input_name) != before.inputs.get(input_name):
                verb = "set" if input_name in choice.inputs else "cleared"
                lines.append(f"{verb} {name} input {input_name}")
    return ShowResult(
        changes=changes, lines=tuple(lines), turned_on=tuple(turned), turned_off=tuple(off)
    )


def _policy_note(policy: Policy | None) -> str:
    return f" ({policy})" if policy is not None else ""


def _policy_name(policy: Policy | None, *, item: bool = False) -> str:
    if policy is not None:
        return str(policy)
    return "the package's" if item else "the author's"


# -- the tree ----------------------------------------------------------------


@dataclass(frozen=True)
class _Row:
    kind: str
    at: At
    name: str = ""


def _source_id(source: str) -> str:
    return f"source\t{source}"


def _parse(row_id: str) -> _Row | None:
    kind, *rest = row_id.split("\t")
    if kind == "source" or len(rest) < 2:
        return None
    at = (rest[0], rest[1])
    return _Row(kind, at, rest[2] if len(rest) > 2 else "")


def _row_at(model: Model) -> _Row | None:
    return _parse(_tree(model).value)


def _tree(model: Model, frame: Frame | None = None) -> Tree:
    return Tree(
        label="",
        rows=_rows(model, frame),
        expanded=model.expanded,
        cursor=model.cursor,
    )


def _rows(model: Model, frame: Frame | None) -> tuple[TreeRow, ...]:
    def mark(on: bool) -> str:
        return frame.symbol("on" if on else "off") if frame is not None else ""

    by_source: dict[str, list[TreeRow]] = {}
    for view in model.packages:
        detail = view.detail
        choice = model.choices.get(view.at)
        on = choice is not None
        ident = f"{detail.source}\t{detail.name}"
        children: list[TreeRow] = []
        for name, spec in view.inputs.items():
            value = _mask(frame) if spec.secret else _input_value(view, choice, name)
            children.append(TreeRow(f"input\t{ident}\t{name}", f"{name} = {value}", "input"))
        for item in detail.items:
            own = choice.items.get(item.name, ItemChoice()) if choice is not None else None
            item_on = own is not None and own.enabled
            policy = (own.policy if own is not None else None) or None
            summary = item.excluded or (str(policy) if item_on and policy else "")
            label = f"{mark(item_on)} {item.name}".strip()
            children.append(TreeRow(f"item\t{ident}\t{item.name}", label, summary))
        summary = _package_summary(view, choice)
        label = f"{mark(on)} {detail.name}".strip()
        by_source.setdefault(detail.source, []).append(
            TreeRow(f"package\t{ident}", label, summary, tuple(children))
        )
    return tuple(
        TreeRow(_source_id(source), source, plural(len(rows), "package"), tuple(rows))
        for source, rows in by_source.items()
    )


def _package_summary(view: PackageView, choice: PackageChoice | None) -> str:
    if view.detail.error is not None:
        return "doesn't load"
    if choice is None:
        return view.refused or ""
    return str(choice.policy or view.detail.suggested or Policy.MANUAL)


def _input_value(view: PackageView, choice: PackageChoice | None, name: str) -> str:
    if choice is not None and name in choice.inputs:
        return _text(choice.inputs[name])
    spec = view.inputs[name]
    return _text(spec.default) if spec.default is not None else "(no value)"


def _mask(frame: Frame | None) -> str:
    return frame.symbol("mask") * 3 if frame is not None else ""


def _text(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list | tuple):
        return ", ".join(_text(v) for v in value)
    return str(value)


def _item(view: PackageView, name: str) -> ItemDetail:
    return next(i for i in view.detail.items if i.name == name)


# -- view ----------------------------------------------------------------------


def _view(model: Model, frame: Frame) -> RenderableType:
    row = _row_at(model)
    at = row.at if row is not None else None
    title = model.view_of(at).spelled if at is not None else "Packages"

    def detail(pane: Frame, focused: bool) -> RenderableType:
        return _detail(model, at, pane)

    below: list[RenderableType] = []  # under the panes, so a narrow terminal still shows them
    if model.editing is not None:
        below.append(model.editing.field.view(frame, focused=True, width=frame.width))
    below.append(Text(model.note, style=frame.style("screen.muted")))
    used = (_FIELD_ROWS if model.editing is not None else 0) + 1
    rest = replace(frame, height=max(6, frame.height - used))
    panes = Panes(
        left=_tree(model, rest),
        right=detail,
        left_title="Sources",
        right_title=title,
        split=0.4,
    )
    return Group(panes.view(rest, focused=True), *below)


def _detail(model: Model, at: At | None, frame: Frame) -> RenderableType:
    muted, value = frame.style("screen.muted"), frame.style("screen.value")
    if at is None:
        return Text("Pick a package to see it in full.", style=muted)
    view = model.view_of(at)
    detail = view.detail
    choice = model.choices.get(at)
    lines: list[RenderableType] = []
    if detail.description:
        lines.append(Text(detail.description, style=value))
    if detail.error is not None:
        lines.append(
            Text(f"doesn't load: {detail.error.message}", style=frame.style("screen.error"))
        )
        return Group(*lines)
    lines.append(Text(f"policy   {_policy_line(view, choice)}", style=value))
    if view.refused is not None:
        lines.append(Text(f"not here: {view.refused}", style=muted))
    for name, spec in view.inputs.items():
        shown = f"{_mask(frame)} (secret)" if spec.secret else _input_value(view, choice, name)
        lines.append(Text(f"input    {name}  {shown}", style=value))
    for item in detail.items:
        own = choice.items.get(item.name, ItemChoice()) if choice is not None else None
        on = own is not None and own.enabled
        lines.append(Text(f"{frame.symbol('on' if on else 'off')} {item.name}", style=value))
        for file in item.files:
            state = file.state or ""
            lines.append(Text(f"    {file.mode:<6}{file.target}  {state}", style=muted))
        for step in item.steps:
            run = " ".join(step.run) if step.run else f"script {step.script}"
            lines.append(Text(f"    step  {step.name}  {run}", style=muted))
        for repo in item.repos:
            lines.append(Text(f"    repo  {repo.url} into {repo.target}", style=muted))
    room = max(1, frame.height)
    if len(lines) > room:
        lines = [*lines[: room - 1], Text(frame.ellipsis(), style=muted)]
    return Group(*lines)


def _policy_line(view: PackageView, choice: PackageChoice | None) -> str:
    if choice is None:
        return "off"
    if choice.policy is not None:
        return f"{choice.policy}  (this machine's; steps follow it)"
    author = view.detail.suggested or Policy.MANUAL
    return f"{author}  (the author's; steps: manual)"
