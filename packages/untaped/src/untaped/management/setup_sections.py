"""The setup screen's section pane: one section's settings as rows, their help, the open field.

Rows come from :class:`~untaped.config.use_cases.ListSettings` run under the
profile being edited (a profile that does not exist yet shows ``default``'s);
each shows its key within the section, its value, its marks and where the
value comes from. Under the rows is the focused row's help (description,
bounds, built-in default, variable) or, once enter opened it, the row's field,
picked from the field's shape by :mod:`untaped.config.fields`. A field writes
through :func:`~untaped.management.setup_write.write_setting`, the use case
``config set`` runs, so an edit fails with ``config set``'s message and a
value written here is the one ``config set`` would write. ``delete`` (or
``backspace``) asks to unset a key the profile sets, then unsets it.

Every read and write runs as a command (:func:`open_command`,
:func:`write_command`), which re-reads the rows after a write; this module
holds no reader or writer of its own.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass, replace
from functools import partial
from typing import Self

from rich.cells import cell_len
from rich.console import Group, RenderableType
from rich.text import Text

from untaped.config.fields import (
    editor,
    opened_value,
    ranking_editor,
    ranking_written,
    ranks,
    summary,
    written,
)
from untaped.config.models import SettingEntry, Source, setting_entry_row
from untaped.config.repository import SettingsFileRepository
from untaped.config.use_cases import ListRankings, ListSettings, Note, RankProviders, SetSetting
from untaped.config_schema import FieldDescriptor, bounds_text
from untaped.errors import ConfigError, UsageError
from untaped.management.setup_state import (
    ServiceState,
    connection_keys,
    profile_exists,
    read_states,
)
from untaped.management.setup_write import RankTarget, write_ranking, write_setting
from untaped.messages import command_line
from untaped.plugins.registry import PluginSpec
from untaped.profile_resolver import DEFAULT_PROFILE, profile_scope
from untaped.screen.components.draw import note_lines, option_row, role_style
from untaped.screen.components.fields import Field
from untaped.screen.components.lists import ListEditor, MapEditor
from untaped.screen.core import Cmd, Frame, Key
from untaped.screen.fit import fit_text
from untaped.setting_marks import SetupMarks
from untaped.settings import section_stabilities
from untaped.stability import stability_name

__all__ = [
    "CONNECTION",
    "FieldOpened",
    "Listing",
    "RankTarget",
    "SectionPane",
    "SettingRow",
    "UnsetAsked",
    "Written",
    "connection_row",
    "open_command",
    "read_listing",
    "write_command",
]

#: The label of a service's connection row (never a key: it has no dot and no section).
CONNECTION = "connection"
#: Lines kept under the rows for the help or the open field.
_BELOW = 6


@dataclass(frozen=True)
class SettingRow:
    """One row of a section: a setting, or a service's connection (``key`` empty)."""

    key: str
    label: str
    """The key within its section (``sweep.parallel``), or :data:`CONNECTION`."""
    value: str
    source: str
    """Where the value comes from: ``profile:work``, ``default``, a variable's name, ``unset``."""
    marks: tuple[str, ...] = ()
    help: tuple[str, ...] = ()
    own: bool = False
    """Whether the edited profile sets it, so ``delete`` can unset it."""
    descriptor: FieldDescriptor | None = None
    """Its field, when enter opens one (``None``: read-only, or the connection)."""
    raw: bool = False
    """Its section failed validation: its value is unvalidated and opens as text."""
    ranking: RankTarget | None = None
    """The ranking it edits (or adds), written through ``RankProviders``, not ``config set``."""


@dataclass(frozen=True)
class Listing:
    """What setup shows of one profile: every section's rows, and each service's state."""

    exists: bool
    """Whether the profile exists; the rows of one that doesn't are ``default``'s."""
    rows: Mapping[str, tuple[SettingRow, ...]]
    errors: Mapping[str, str]
    """Why each section that fails validation does."""
    states: Mapping[str, ServiceState]


@dataclass(frozen=True)
class FieldOpened:
    """A row's field is ready: its current value and the profile's own (``expected``)."""

    key: str
    value: object
    expected: object


@dataclass(frozen=True)
class Written:
    """A write finished (or was refused, ``error``); ``listing`` is the rows read after it.

    ``changed`` is false for an unset of a key the profile did not set.
    """

    key: str
    notes: tuple[Note, ...]
    listing: Listing | None
    error: str = ""
    changed: bool = True


@dataclass(frozen=True)
class UnsetAsked:
    """The unset of ``key`` was confirmed (``ranking``: a ranking's row)."""

    key: str
    ranking: RankTarget | None = None


def read_listing(profile: str, services: Mapping[str, PluginSpec]) -> Listing:
    """What setup shows of ``profile``: every section's rows, and each of ``services``' state.

    A service's connection keys are its connection row, not rows of their own;
    a deprecated setting is listed only while it is set.
    """
    exists = profile_exists(profile)
    hidden = frozenset().union(*(connection_keys(spec) for spec in services.values()))
    with profile_scope(profile if exists else DEFAULT_PROFILE):
        repo = SettingsFileRepository()
        listing = ListSettings(repo)
        entries = listing()
        descriptors = {descriptor.key: descriptor for descriptor in repo.descriptors()}
        rows: dict[str, list[SettingRow]] = {}
        for entry in entries:
            descriptor = descriptors[entry.key]
            set_here = entry.source.kind in ("profile", "env")
            if entry.key in hidden or (entry.stability == "deprecated" and not set_here):
                continue
            section = descriptor.path[0]
            raw = section in listing.errors
            if ranks(descriptor) and not raw:
                rows.setdefault(section, []).extend(_ranking_rows(repo, descriptor, profile))
                continue
            variable = repo.env_var_for(descriptor)
            # A variable hides whether the profile sets the key too; ask the profile.
            own = entry.source.profile == profile or (
                exists
                and entry.source.kind == "env"
                and repo.own_value(entry.key, profile=profile) is not None
            )
            # Read only for a shape that shows it; a missing profile owns nothing.
            mine = partial(repo.own_value, entry.key, profile=profile)
            shown_as = None if raw else summary(descriptor, mine)
            row = _row(entry, descriptor, variable, own=own, raw=raw, summary=shown_as)
            rows.setdefault(section, []).append(row)
    shown = {name: tuple(each) for name, each in rows.items()}
    return Listing(exists, shown, listing.errors, read_states(dict(services), profile))


def section_mark(section: str) -> str:
    """``experimental`` or ``deprecated`` for a marked plugin's section, else empty."""
    name = stability_name(section_stabilities().get(section))
    return "" if name == "stable" else name


def _row(
    entry: SettingEntry,
    descriptor: FieldDescriptor,
    variable: str,
    *,
    own: bool,
    raw: bool,
    summary: str | None = None,
) -> SettingRow:
    shown = setting_entry_row(entry, human=True)
    help: list[str] = []
    if descriptor.description:
        help.append(descriptor.description.replace("`", ""))
    if (bounds := bounds_text(descriptor)) is not None:
        help.append(bounds)
    default = "unset" if shown.default is None else str(shown.default)
    help.append(f"default {default} · {variable}")
    if entry.source.variable is not None:
        help.append(_variable_wins(entry.source.variable))
    if shown.note:
        help.append(shown.note)
    editable = shown.stability != "deprecated"
    if not editable:
        command = command_line(f"config set {entry.key}")
        help.append(
            f"Read-only here (deprecated); `{command}` sets it"
            + (", delete unsets it." if own else ".")
        )
    value = summary if summary is not None else "" if shown.value is None else str(shown.value)
    return SettingRow(
        key=entry.key,
        label=".".join(descriptor.path[1:]),
        value=value,
        source=entry.source.variable or shown.source,
        marks=() if shown.stability == "stable" else (shown.stability,),
        help=tuple(help),
        own=own,
        descriptor=descriptor if editable else None,
        raw=raw,
    )


def _is_ranking(value: object) -> bool:
    """Whether raw ``value`` is a non-empty list of names, so it can open as a ranking."""
    return isinstance(value, list) and bool(value) and all(isinstance(each, str) for each in value)


def _variable_wins(variable: str) -> str:
    return f"{variable} is set: it wins over the profile until it is unset."


def _ranking_rows(
    repo: SettingsFileRepository, descriptor: FieldDescriptor, profile: str
) -> list[SettingRow]:
    """A row per ranking the owner's ``extensions`` holds, then the row that adds one."""
    owner, label = descriptor.path[0], ".".join(descriptor.path[1:])
    help_tail = f"`{command_line('plugin list --contracts')}` lists contracts and providers."
    rows = [
        SettingRow(
            key=f"{descriptor.key}.{each.contract}.rank.{each.method}",
            label=f"{label}.{each.contract}.rank.{each.method}",
            value=", ".join(each.providers),
            source=each.source,
            help=(
                f"The providers of {owner}.{each.contract} {each.method}, first first.",
                help_tail,
                *((_variable_wins(each.variable),) if each.variable else ()),
            ),
            own=each.profile == profile,
            descriptor=descriptor,
            ranking=RankTarget(owner, each.contract, each.method, each.providers),
        )
        for each in ListRankings(repo)(descriptor.key)
    ]
    add = SettingRow(
        key=descriptor.key,
        label=".".join(descriptor.path[1:]),
        value="+ add a ranking",
        source="",
        help=("Order a contract method's providers, as plugin rank does.", help_tail),
        descriptor=descriptor,
        ranking=RankTarget(owner),
    )
    return [*rows, add]


def connection_row(state: ServiceState, marks: SetupMarks) -> SettingRow:
    """A plugin's setup row: what its marked settings hold, and where each secret comes from."""
    parts = [
        state.shown.get(field.key)
        or (f"no {field.key}" if field.key in state.missing else f"{field.key} set")
        for field in marks.required
        if not field.is_secret
    ]
    parts += [
        f"{each.key} from {source}"
        if (source := state.secret_sources.get(each.key))
        else f"no {each.key}"
        for each in marks.secrets
    ]
    help = (
        "The settings the plugin needs, checked online before they are saved.",
        "enter opens them.",
    )
    return SettingRow(key="", label=CONNECTION, value=", ".join(parts), source="", help=help)


def open_command(profile: str, row: SettingRow) -> Cmd:
    """Read ``row``'s value and the profile's own value of it, for its field."""
    key, raw, value = row.key, row.raw, row.value
    ranking = row.ranking

    def open_field() -> object:
        exists = profile_exists(profile)
        with profile_scope(profile if exists else DEFAULT_PROFILE):
            repo = SettingsFileRepository()
            if ranking is not None:
                if not ranking.method:
                    return FieldOpened(key, (), None)  # the row adding one
                # The profile's own ranking is what a write replaces; else what it shows.
                contract = f"{ranking.owner}.{ranking.contract}"
                own = RankProviders(repo, ()).current(contract, ranking.method, profile=profile)
                shown = own if _is_ranking(own) else ranking.providers
                return FieldOpened(key, tuple(shown), own)
            expected = SetSetting(repo).current(key, profile=profile)
            if raw:
                return FieldOpened(key, value, expected)
            current = opened_value(repo, repo.descriptor(key), profile=profile)
            return FieldOpened(key, current, expected)

    return Cmd(open_field, name="open_field")


def write_command(
    profile: str,
    key: str,
    value: str | None,
    expected: object,
    services: Mapping[str, PluginSpec],
    *,
    ranking: RankTarget | None = None,
    owners: Collection[str] = (),
) -> Cmd:
    """Write ``value`` to ``key`` in ``profile`` (``None`` unsets it), then re-read the rows.

    A ``ranking``'s row writes through ``RankProviders`` instead (``owners``
    own contracts): ``value`` is its providers as JSON, or, on the row adding
    one, ``CONTRACT METHOD PROVIDER...`` as ``plugin rank`` takes them. A
    refused write comes back with its message; on a conflict the rows are
    re-read too, so the screen shows what changed.
    """

    def write() -> object:
        try:
            if ranking is None:
                change = write_setting(key, value, profile=profile, expected=expected)
            else:
                change = write_ranking(
                    ranking, value, profile=profile, owners=owners, expected=expected
                )
        except UsageError as exc:
            hint = f" ({exc.hint})" if exc.hint else ""
            return Written(key, (), None, error=f"{exc}{hint}")
        except ConfigError as exc:
            listing = read_listing(profile, services) if exc.category == "conflict" else None
            return Written(key, (), listing, error=str(exc))
        changed = change.outcome.action != "unchanged"
        return Written(key, change.notes, read_listing(profile, services), changed=changed)

    return Cmd(write, write=True, name="write_setting")


@dataclass(frozen=True)
class SectionPane:
    """One section's rows, the cursor, and the open field or the unset question.

    Keys move the cursor; ``delete`` or ``backspace`` asks to unset the row the
    profile sets, ``y`` confirms it (sending :class:`UnsetAsked`) and any other
    key keeps it. With a field open, every key goes to the field. Opening and
    writing are the screen's (enter, ctrl-s, esc).
    """

    section: str
    profile: str
    rows: tuple[SettingRow, ...]
    cursor: int = 0
    editing: Field | None = None
    """The open field."""
    expected: object = None
    """The profile's own value of the open field's key when it opened."""
    initial: str | None = None
    """What the open field would write as it opened: writing it back changes nothing."""
    confirm: bool = False
    error: str = ""
    """The section's own error (it fails validation)."""

    def __post_init__(self) -> None:
        object.__setattr__(self, "cursor", max(0, min(self.cursor, len(self.rows) - 1)))

    @property
    def row(self) -> SettingRow | None:
        """The row under the cursor."""
        return self.rows[self.cursor] if self.rows else None

    @property
    def value(self) -> str:
        """The key under the cursor."""
        row = self.row
        return "" if row is None else row.key

    def with_error(self, text: str) -> Self:
        """This pane showing ``text`` as the section's error."""
        return replace(self, error=text)

    def validate(self) -> str:
        """Always fine: each field is checked when it is written."""
        return ""

    def opened(self, component: Field, expected: object) -> Self:
        """This pane with ``component`` open under the rows."""
        opened = replace(self, editing=component, expected=expected, confirm=False)
        return replace(opened, initial=opened.to_write())

    def closed(self) -> Self:
        """This pane with no field open and no question asked."""
        return replace(self, editing=None, expected=None, initial=None, confirm=False)

    def with_rows(self, rows: tuple[SettingRow, ...], error: str) -> Self:
        """This pane over re-read ``rows``, its cursor kept on the same key."""
        key = self.value
        cursor = next((index for index, row in enumerate(rows) if row.key == key), self.cursor)
        return replace(self, rows=rows, cursor=cursor, error=error)

    def checked(self) -> Self | None:
        """This pane with the open field's own error shown, or ``None`` when it may be written."""
        component = self.editing
        error = "" if component is None else component.validate()
        return replace(self, editing=component.with_error(error)) if component and error else None

    def to_write(self) -> str | None:
        """The open field's value as ``config set`` takes it; ``None`` unsets the key."""
        row, component = self.row, self.editing
        assert row is not None and row.descriptor is not None and component is not None
        if row.ranking is not None:
            return ranking_written(component)
        return written(row.descriptor, component, raw=row.raw)

    def update(self, message: object) -> tuple[Self, list[Cmd]]:
        """Give ``message`` to the open field, answer the question, or move the cursor."""
        if self.editing is not None:
            updated, cmds = self.editing.update(message)
            if updated is self.editing and not cmds:
                return self, []
            return replace(self, editing=updated), list(cmds)
        if not isinstance(message, Key):
            return self, []
        row = self.row
        if self.confirm:
            if message.name == "y" and row is not None:
                return replace(self, confirm=False), [Cmd.send(UnsetAsked(row.key, row.ranking))]
            return replace(self, confirm=False), []
        match message.name:
            case "up" | "down" | "home" | "end":
                moved = _moved(message.name, self.cursor, len(self.rows))
                return (self if moved == self.cursor else replace(self, cursor=moved)), []
            case "delete" | "backspace" if row is not None and row.own and row.key:
                return replace(self, confirm=True), []
        return self, []

    def view(
        self, frame: Frame, *, focused: bool = False, width: int | None = None
    ) -> RenderableType:
        """The rows (a window around the cursor), then the field, the question or the help."""
        inner = width or frame.width  # the pane's own border already frames the rows
        editing = self.editing
        below = _BELOW
        if isinstance(editing, ListEditor | MapEditor):
            # The editor takes the room it needs, down to one row of settings above it.
            chrome = (2 if frame.box() is None else 3) + note_lines(
                editing.error or editing.help, inner
            )
            chrome += note_lines(self.error, inner)
            items = min(editing.rows, max(3, frame.height - 1 - chrome))
            editing = replace(editing, window_rows=items)
            below = items + chrome
        size = max(1 if below > _BELOW else 3, frame.height - below)
        start = max(0, min(self.cursor - size // 2, len(self.rows) - size))
        shown = self.rows[start : start + size]
        key_width = min(
            max((cell_len(row.label) for row in self.rows), default=0), max(8, inner * 2 // 5)
        )
        lines: list[RenderableType] = [
            self._line(
                frame, inner, row, key_width, cursor=focused and start + index == self.cursor
            )
            for index, row in enumerate(shown)
        ]
        if self.error:
            lines.append(Text(self.error, style=role_style(frame, "screen.error")))
        lines.append(Text())
        row = self.row
        if editing is not None:
            lines.append(editing.view(frame, focused=focused, width=width))
        elif self.confirm and row is not None:
            question = (
                f"Unset {row.label} in profile {self.profile}? y unsets it, any key keeps it."
            )
            lines.append(Text(question, style=role_style(frame, "screen.accent")))
        elif row is not None:
            lines.extend(self._help(frame, inner, row, key_width))
        return Group(*lines)

    def _line(
        self, frame: Frame, inner: int, row: SettingRow, key_width: int, *, cursor: bool
    ) -> Text:
        """A row: the key and the value (the value is cut first), then marks and source."""
        label = f"{fit_text(Text(row.label), key_width, frame.ellipsis()).plain:<{key_width}}"
        style = role_style(frame, "screen.highlight" if cursor else "screen.value")
        return option_row(
            frame,
            inner,
            label=f"{label}  {row.value}",
            label_style=style,
            trail="  ".join((*row.marks, row.source)),
            trail_style=style if cursor else role_style(frame, "screen.muted"),
            base=role_style(frame, "screen.highlight") if cursor else None,
        )

    def _help(self, frame: Frame, inner: int, row: SettingRow, key_width: int) -> list[Text]:
        muted = role_style(frame, "screen.muted")
        lines = [Text(line, style=muted) for line in row.help]
        trail = cell_len("  ".join((*row.marks, row.source))) + 1
        if cell_len(row.value) > inner - key_width - 2 - trail:
            lines.insert(0, Text(row.value, style=role_style(frame, "screen.value")))
        return lines


def _moved(name: str, cursor: int, count: int) -> int:
    match name:
        case "up":
            return max(cursor - 1, 0)
        case "down":
            return min(cursor + 1, count - 1)
        case "home":
            return 0
    return count - 1


def field_for(pane: SectionPane, message: FieldOpened) -> SectionPane:
    """``pane`` with the field of ``message``'s row open (unchanged when the row moved on)."""
    row = pane.row
    if row is None or row.key != message.key or row.descriptor is None:
        return pane
    if row.ranking is not None:
        providers = message.value if isinstance(message.value, tuple) else ()
        component = ranking_editor(row.label, providers, adds=not row.ranking.method)
        return pane.opened(component, message.expected)
    component = editor(
        row.descriptor,
        message.value,
        label=row.label,
        raw=row.raw,
        own=message.expected,
        own_source=Source(kind="profile", profile=pane.profile).label,
    )
    return pane.opened(component, message.expected)
