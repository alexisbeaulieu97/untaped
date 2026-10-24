"""``install``: compose plugin classes into the hermetic home and run their commands.

A test names the plugins it needs and their settings as models, then runs a
built command (``.run(Say(names=["Ada"]))``) or a command line
(``.cli("hello say Ada --format json")``); both return one :class:`Ran`.
Nothing else is installed, so a test sees only what it named.
"""

from __future__ import annotations

import io
import json
import shlex
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import redirect_stdout
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from pydantic import BaseModel

from untaped.sdk.plugin import Command, Plugin

if TYPE_CHECKING:
    from untaped.runtime.declaration import CommandDecl, OptionDecl
    from untaped.runtime.mount import Mount
    from untaped.runtime.registry import PluginCandidate
    from untaped.sdk.diagnostic import Diagnostic
    from untaped.sdk.testing import CliResult


@dataclass(frozen=True)
class Ran:
    """What a command did: its exit code, what it printed, and its rows.

    ``rows`` are the records it printed: under ``.run`` the objects ``run``
    returned, under ``.cli`` the rows read back from ``--format json`` or
    ``pipe`` (none for a table). ``text`` is the line a command returning a
    string printed. ``stderr`` is what a user saw on stderr, line by line, and
    ``diagnostics`` the typed lines behind it.
    """

    exit: int
    stdout: str
    stderr: list[str]
    rows: list[BaseModel] = field(default_factory=list)
    text: str | None = None
    diagnostics: tuple[Diagnostic, ...] = ()
    #: The format a command line printed its rows in, when they could not be read back.
    unread: str | None = None

    def rows_as[M: BaseModel](self, model: type[M]) -> list[M]:
        """The rows, each checked to be a ``model``; a table's rows were never read back."""
        if self.unread is not None:
            raise TypeError(
                f"the rows were printed as --format {self.unread}, which is not read back; "
                "run it with --format json or pipe, or through .run"
            )
        for row in self.rows:
            if not isinstance(row, model):
                raise TypeError(f"a row is a {type(row).__qualname__}, not a {model.__qualname__}")
        return cast("list[M]", self.rows)


#: Each knob of :meth:`Install.run` and the core field it fills.
_KNOBS: Mapping[str, str] = {
    "answer": "confirm",
    "dry_run": "confirm",
    "refresh": "refresh",
    "stdin": "stdin",
    "format": "output",
}


@dataclass(frozen=True)
class Install:
    """Plugins composed into the hermetic home, ready to run commands."""

    candidates: tuple[PluginCandidate, ...]

    def run(
        self,
        command: Command[Any],
        *,
        raise_on_error: bool = False,
        **knobs: object,
    ) -> Ran:
        """Run the built ``command`` as core would, with the test's answers to its core fields.

        Core's fields default as a test runs them: ``Confirm`` answers yes,
        ``Output`` prints a table, ``Stdin`` reads nothing. ``answer=False``
        declines, ``dry_run=True`` previews, ``refresh=True``/``False`` is
        ``--refresh``/``--cached``, ``stdin=[row, "line"]`` pipes rows and
        bare lines, ``format="json"`` picks the format. A knob for a field
        the command does not declare is a :class:`TypeError`.
        """
        from untaped.sdk.testing import ScriptedPromptBackend, invoke_cli  # noqa: PLC0415

        mount = self._mount()
        decl = _decl(mount, command)
        kinds = {option.block for option in decl.options if option.block is not None}
        for knob in knobs:
            block = _KNOBS.get(knob)
            if block is None:
                raise TypeError(f"run() got an unexpected knob {knob!r}")
            if block not in kinds:
                raise TypeError(
                    f"{type(command).__qualname__} declares no {block} field, so {knob}= "
                    "does not apply"
                )
        answer = knobs.get("answer", True)
        flags = _flags(decl, knobs)
        piped = _piped(mount, knobs.get("stdin"))
        rows: list[BaseModel] = []
        texts: list[str] = []

        def go(_args: object, **_consoles: object) -> None:
            _dispatch(mount, decl, command, flags, rows=rows, texts=texts, raises=raise_on_error)

        declined = answer is False and "confirm" in kinds
        result = invoke_cli(
            go,
            [],
            input=piped,
            catch_exceptions=not raise_on_error,
            prompt_backend=ScriptedPromptBackend(confirms=[False]) if declined else None,
            terminal=declined,
        )
        if raise_on_error and result.exception is not None:
            raise result.exception
        return _ran(result, rows=rows, text=texts[0] if texts else None, unread=None)

    def cli(self, line: str | Sequence[str], *, input: str | None = None) -> Ran:
        """Run ``untaped <line>`` through the command line core mounts, as a user would.

        The terminal is absent unless the test gives one, so a command that
        asks first exits 2 without ``--yes``. ``input`` is what is piped on
        stdin. Rows are read back from ``--format json`` or ``pipe``.
        """
        from untaped.runtime.bootstrap import build_root_app  # noqa: PLC0415
        from untaped.sdk.testing import invoke_cli  # noqa: PLC0415

        args = shlex.split(line) if isinstance(line, str) else list(line)
        root = build_root_app(candidates=self.candidates)
        result = invoke_cli(root.meta, args, input=input)
        fmt = _given_format(args)
        readable = fmt in ("json", "pipe")
        rows = _read_back(self._mount(), args, result.stdout, fmt) if readable else []
        return _ran(result, rows=rows, text=None, unread=None if readable else fmt)

    def _mount(self) -> Mount:
        from untaped.runtime.bootstrap import compose_root  # noqa: PLC0415

        mount = compose_root(candidates=self.candidates).mount
        if mount is None:
            raise TypeError("install() composed no plugin class")
        return mount


def install(
    *plugins: type[Plugin],
    settings: BaseModel | Iterable[BaseModel] = (),
    names: Mapping[type[Plugin], str] | None = None,
) -> Install:
    """Compose ``plugins`` into the hermetic home, with ``settings`` written as their sections.

    A plugin installed in the environment keeps its entry point's name; a
    class defined in a test is named after itself (``Shop`` is ``shop``)
    unless ``names`` says otherwise. Each settings model is written, as set,
    to the section of the plugin whose ``settings`` it is; a plugin given
    none has no settings of its own, as a user who set nothing.
    """
    named = dict(names or {})
    candidates = tuple(_candidate(cls, named.get(cls)) for cls in plugins)
    models = [settings] if isinstance(settings, BaseModel) else list(settings)
    sections = {
        cls.settings: candidate.name for cls, candidate in zip(plugins, candidates, strict=True)
    }
    given: dict[str, BaseModel | None] = {candidate.name: None for candidate in candidates}
    for model in models:
        section = sections.get(type(model))
        if section is None:
            raise TypeError(f"{type(model).__qualname__} is the settings of no plugin installed")
        given[section] = model
    _write_sections(given)
    return Install(candidates)


def _candidate(cls: type[Plugin], name: str | None) -> PluginCandidate:
    from untaped.runtime.composition import kebab  # noqa: PLC0415
    from untaped.runtime.declaration import target_of  # noqa: PLC0415
    from untaped.runtime.registry import PluginCandidate, discover_candidates  # noqa: PLC0415

    target = target_of(cls)
    if name is None:
        found = next((c for c in discover_candidates() if c.target == target), None)
        if found is not None:
            return found
        name = kebab(cls.__name__)
    try:
        version = metadata.version(f"untaped-{name}")
    except metadata.PackageNotFoundError:
        version = ""
    return PluginCandidate(
        distribution=f"untaped-{name}", name=name, target=cls, distribution_version=version
    )


def _write_sections(sections: Mapping[str, BaseModel | None]) -> None:
    """Write each plugin's settings as set, or leave it with none (``None``)."""
    from untaped.runtime.config_file import read_config_dict, write_config_dict  # noqa: PLC0415
    from untaped.runtime.settings import active_settings_layout  # noqa: PLC0415

    raw = read_config_dict()
    target, _ = active_settings_layout().write_profile(raw, None)
    for section, model in sections.items():
        if model is None:
            target.pop(section, None)
        else:
            target[section] = model.model_dump(mode="json", exclude_unset=True)
    write_config_dict(raw)


def _decl(mount: Mount, command: Command[Any]) -> CommandDecl:
    from untaped.runtime.declaration import target_of  # noqa: PLC0415

    target = target_of(type(command))
    for decl in mount.declaration.commands():
        if decl.target == target and mount.command(decl) is type(command):
            return decl
    raise TypeError(f"{type(command).__qualname__} is a command of no plugin installed")


def _flags(decl: CommandDecl, knobs: Mapping[str, object]) -> dict[str, dict[str, object]]:
    """Each core field's flags (by field path), as a user would pass them for ``knobs``."""
    flags: dict[str, dict[str, object]] = {}
    for option in decl.options:
        if option.block is None:
            continue
        given = flags.setdefault(option.field, {})
        value = _flag(option, knobs)
        if value is not None:
            given[option.flag or ""] = value
    return flags


def _flag(option: OptionDecl, knobs: Mapping[str, object]) -> object:
    match option.flag:
        case "--yes":
            return knobs.get("answer", True) is True and not knobs.get("dry_run")
        case "--dry-run":
            return knobs.get("dry_run") is True
        case "--refresh":
            return knobs.get("refresh")
        case "--stdin":
            return "stdin" in knobs
        case "--format":
            return _format(knobs)
    return None


def _format(knobs: Mapping[str, object]) -> str:
    fmt = knobs.get("format", "table")
    return str(getattr(fmt, "value", fmt))


def _piped(mount: Mount, values: object) -> str | None:
    """``values`` (rows and bare lines) as another command's ``--format pipe`` would print them."""
    if values is None:
        return None
    from untaped.sdk._cli import emit  # noqa: PLC0415

    lines: list[str] = []
    for value in cast("Iterable[object]", values):
        if isinstance(value, BaseModel):
            out = io.StringIO()
            with redirect_stdout(out):
                emit([value], fmt="pipe", kind=mount.kind_of(type(value)))
            lines.append(out.getvalue().rstrip("\n"))
        else:
            lines.append(str(value))
    return "\n".join(lines) + "\n"


def _dispatch(
    mount: Mount,
    decl: CommandDecl,
    command: Command[Any],
    flags: Mapping[str, Mapping[str, object]],
    *,
    rows: list[BaseModel],
    texts: list[str],
    raises: bool,
) -> None:
    """Fill ``command``'s core fields, run it under a run, print what it returns, keep its rows.

    As the root runs a command: one diagnostics scope, errors reported as their
    exit codes, and a run that emitted a failure fails.
    """
    from contextlib import nullcontext  # noqa: PLC0415

    from untaped.runtime.mount import _print  # noqa: PLC0415
    from untaped.runtime.run import build_run  # noqa: PLC0415
    from untaped.runtime.sink import (  # noqa: PLC0415
        derived_exit_code,
        diagnostics_scope,
        exit_on_counted_failure,
    )
    from untaped.sdk._cli import report_errors  # noqa: PLC0415
    from untaped.sdk.run import run_scope  # noqa: PLC0415

    with diagnostics_scope():
        try:
            with nullcontext() if raises else report_errors():
                filled = _filled(mount, decl, command, flags)
                with run_scope(build_run(mount.declaration, mount.settings)):
                    result = filled.run()
                    if decl.returns in ("line", "line-or-none"):
                        if result is not None:
                            texts.append(str(result))
                    elif isinstance(result, BaseModel):
                        rows.append(result)
                    elif result is not None:
                        result = _kept(cast("Iterable[BaseModel]", result), rows)
                    _print(mount, decl, filled, result)
                exit_on_counted_failure()
        except SystemExit as exc:
            exc.code = derived_exit_code(exc.code)
            raise


def _kept(rows: Iterable[BaseModel], into: list[BaseModel]) -> Iterator[BaseModel]:
    for row in rows:
        into.append(row)
        yield row


def _filled(
    mount: Mount,
    decl: CommandDecl,
    command: Command[Any],
    flags: Mapping[str, Mapping[str, object]],
) -> Command[Any]:
    """``command`` with each core field filled from its flags, as the mount fills them."""
    from untaped.sdk._block import FieldBlock, Invocation  # noqa: PLC0415

    filled: BaseModel = command
    for path, given in flags.items():
        parts = path.split(".")
        holders: list[BaseModel] = [filled]
        for part in parts[:-1]:
            holders.append(getattr(holders[-1], part))
        block = getattr(holders[-1], parts[-1])
        if not isinstance(block, FieldBlock):
            continue
        info = type(holders[-1]).model_fields[parts[-1]]
        call = Invocation(
            words=("untaped", *decl.path),
            annotation=info.annotation,
            kinds=lambda: mount.kinds,
        )
        value: object = block._fill(given, call)
        for holder, part in zip(reversed(holders), reversed(parts), strict=True):
            value = holder.model_copy(update={part: value})
        filled = cast("BaseModel", value)
    return cast("Command[Any]", filled)


def _given_format(args: Sequence[str]) -> str:
    for index, arg in enumerate(args):
        if arg in ("--format", "-f") and index + 1 < len(args):
            return args[index + 1]
        if arg.startswith("--format="):
            return arg.partition("=")[2]
    return "table"


def _read_back(mount: Mount, args: Sequence[str], stdout: str, fmt: str) -> list[BaseModel]:
    """The rows ``stdout`` holds in ``fmt``, each validated as the command's ``Output[R]``."""
    decl = _decl_of_line(mount, args)
    model = None if decl is None or decl.output is None else mount.kinds.get(decl.output)
    if model is None:
        return []
    if fmt == "json":
        loaded = json.loads(stdout or "[]")
        records = loaded if isinstance(loaded, list) else [loaded]
    else:
        from untaped.sdk._pipe import parse_envelope_line  # noqa: PLC0415

        records = [
            parse_envelope_line(lineno, text).record
            for lineno, text in enumerate(stdout.splitlines(), start=1)
            if text.strip()
        ]
    return [model.model_validate(record) for record in records]


def _decl_of_line(mount: Mount, args: Sequence[str]) -> CommandDecl | None:
    words = [arg for arg in args if not arg.startswith("-")]
    best: CommandDecl | None = None
    for decl in mount.declaration.commands():
        if tuple(words[: len(decl.path)]) == decl.path and (
            best is None or len(decl.path) > len(best.path)
        ):
            best = decl
    return best


def _ran(result: CliResult, *, rows: list[BaseModel], text: str | None, unread: str | None) -> Ran:
    return Ran(
        exit=result.exit_code,
        stdout=result.stdout,
        stderr=result.stderr.splitlines(),
        rows=rows,
        text=text,
        diagnostics=result.diagnostics,
        unread=unread,
    )


@dataclass(frozen=True)
class Home:
    """The hermetic home every test of an opted-in suite runs in."""

    path: Path

    def plugin(self, plugin: str | type[Plugin]) -> Path:
        """A plugin's own directory, ``plugins/<name>/``, by its name or its class."""
        from untaped.runtime.settings import plugins_root  # noqa: PLC0415

        name = plugin if isinstance(plugin, str) else _candidate(plugin, None).name
        return plugins_root() / name


__all__ = ["Home", "Install", "Ran", "install"]
