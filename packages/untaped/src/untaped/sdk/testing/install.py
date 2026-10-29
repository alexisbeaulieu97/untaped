"""``install``: compose plugin classes into the hermetic home and run their commands.

A test names the plugins it needs and their settings as models, then runs a
built command (``.run(Say(names=["Ada"]))``) or a command line, as a user
types it after ``untaped`` (``.cli("hello say Ada --format json")``); both
return one :class:`Ran`. Nothing else is installed, so a test sees only what
it named. A command's crash is the test's error, never an exit code.
"""

from __future__ import annotations

import io
import shlex
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager, nullcontext, redirect_stdout
from dataclasses import dataclass, field
from functools import cached_property
from importlib import metadata
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from pydantic import BaseModel, SecretBytes, SecretStr
from pydantic_core import to_jsonable_python

from untaped.sdk._names import kebab
from untaped.sdk.plugin import Command, Plugin
from untaped.sdk.testing.fixtures import ScriptedTerminal

if TYPE_CHECKING:
    from untaped.runtime.declaration import CommandDecl, OptionDecl
    from untaped.runtime.mount import Mount, Printed
    from untaped.runtime.registry import PluginCandidate
    from untaped.sdk.diagnostic import Diagnostic
    from untaped.sdk.network.http import HttpFactory
    from untaped.sdk.testing import CliResult


@dataclass(frozen=True)
class Ran:
    """What a command did: its exit code, what it printed, and its rows.

    ``rows`` are the records it printed: under ``.run`` the objects ``run``
    returned, under ``.cli`` those of ``--format json`` or ``pipe``, each
    read back through its JSON (none for a table). ``text`` is the line a
    command returning a string printed. ``stderr`` is what a user saw on
    stderr, line by line, and ``diagnostics`` the typed lines behind it.
    """

    exit: int
    stdout: str
    stderr: list[str]
    rows: list[BaseModel] = field(default_factory=list)
    text: str | None = None
    diagnostics: tuple[Diagnostic, ...] = ()
    #: Why a command line's rows were not read back, if they were not.
    _unread: str | None = field(default=None, repr=False)

    def rows_as[M: BaseModel](self, model: type[M]) -> list[M]:
        """The rows, each checked to be a ``model``; a table's rows were never read back."""
        if self._unread is not None:
            raise TypeError(f"the rows were not read back: {self._unread}")
        for row in self.rows:
            if not isinstance(row, model):
                raise TypeError(f"a row is a {type(row).__qualname__}, not a {model.__qualname__}")
        return cast("list[M]", self.rows)


@dataclass(frozen=True)
class Running:
    """A block running under an install (:meth:`Install.running`): what it showed so far.

    ``diagnostics`` are the typed stderr lines the block itself shows, as
    :attr:`Ran.diagnostics` are a command's; a ``.run()``, ``.cli()`` or
    nested ``running()`` inside it reports on its own result. ``http`` is
    the run's HTTP factory, which a client class takes.
    """

    _shown: list[Diagnostic] = field(repr=False)

    @property
    def diagnostics(self) -> tuple[Diagnostic, ...]:
        """The typed lines the block itself showed so far, oldest first."""
        return tuple(self._shown)

    @property
    def http(self) -> HttpFactory:
        """HTTP clients as a command's ``self.ctx.http`` builds them, for a client class's tests."""
        from untaped.sdk._run import current_run  # noqa: PLC0415

        return current_run().http


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
        ``--refresh``/``--cached``, ``stdin=[...]`` pipes rows (or bare
        lines), ``format="json"`` picks the format. A knob for a field the
        command does not declare is a :class:`TypeError`. A command's error
        is the exit code and its message, unless ``raise_on_error``.
        """
        from untaped.runtime.mount import watching  # noqa: PLC0415
        from untaped.sdk.testing import ScriptedTerminal, invoke_cli  # noqa: PLC0415
        from untaped.sdk.testing.fixtures import current_terminal  # noqa: PLC0415

        mount = self._mount
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
        flags = _flags(decl, knobs)
        piped = _piped(mount, knobs.get("stdin"))

        def go(_args: object, **_consoles: object) -> None:
            _dispatch(mount, decl, command, flags, raises=raise_on_error)

        declined = knobs.get("answer") is False
        scripted = current_terminal()
        if not declined:
            asking: AbstractContextManager[ScriptedTerminal | None] = nullcontext(_terminal())
        elif scripted is not None:
            asking = scripted._declining()  # the test's terminal, its next answer no
        else:
            asking = nullcontext(ScriptedTerminal().answers(False))
        with asking as backend, watching() as printed:
            result = invoke_cli(
                go,
                [],
                input=piped,
                catch_exceptions=False,
                prompt_backend=backend,
                terminal=backend is not None,
                interactive=backend is not None and piped is None,
            )
        return _ran(result, printed[0] if printed else None, read_back=False)

    def cli(self, line: str | Sequence[str], *, input: str | None = None) -> Ran:
        """Run ``untaped <line>`` through the command line core mounts, as a user would.

        The terminal is the ``terminal`` fixture's: absent unless the test
        scripted it, so a command that asks first exits 2 without ``--yes``.
        ``input`` is what is piped on stdin. The rows of ``--format json`` or
        ``pipe`` are read back through their JSON.
        """
        from untaped.runtime.bootstrap import build_root_app  # noqa: PLC0415
        from untaped.runtime.mount import watching  # noqa: PLC0415
        from untaped.sdk.testing import invoke_cli  # noqa: PLC0415

        args = shlex.split(line) if isinstance(line, str) else list(line)
        root = build_root_app(candidates=self.candidates)
        backend = _terminal()
        with watching() as printed:
            result = invoke_cli(
                root.meta,
                args,
                input=input,
                catch_exceptions=False,
                prompt_backend=backend,
                terminal=backend is not None,
                interactive=backend is not None and input is None,
            )
        return _ran(result, printed[-1] if printed else None, read_back=True)

    @contextmanager
    def running(self) -> Iterator[Running]:
        """Run the block as a command of this install runs: under its run, its plugins composed.

        The block's run is built when it opens, as a command's is (the
        environment, ``UNTAPED_DIAGNOSTICS``, read then), and what it yields
        records the typed lines the block itself shows (``run.diagnostics``);
        a command run inside reports on its own result.
        """
        from untaped.runtime.run import build_run, entered  # noqa: PLC0415
        from untaped.sdk._run import recording  # noqa: PLC0415

        with (
            recording() as shown,
            entered(build_run(self._mount.declaration, self._mount.settings)),
        ):
            yield Running(shown)

    @cached_property
    def _mount(self) -> Mount:
        from untaped.runtime.bootstrap import compose_root  # noqa: PLC0415

        return compose_root(candidates=self.candidates).mount


def _terminal() -> ScriptedTerminal | None:
    """The test's ``terminal`` when it is present; ``None`` is no terminal."""
    from untaped.sdk.testing.fixtures import current_terminal  # noqa: PLC0415

    terminal = current_terminal()
    return terminal if terminal is not None and terminal.is_present else None


def install(
    *plugins: type[Plugin],
    settings: BaseModel | Iterable[BaseModel] = (),
    names: Mapping[type[Plugin], str] | None = None,
    providers: Mapping[str, Sequence[type]] | None = None,
) -> Install:
    """Compose ``plugins`` into the hermetic home, with ``settings`` written as their sections.

    A plugin installed in the environment keeps its entry point's name and
    brings the plugins its distribution requires (the owner a provider
    fills contracts of); a class defined in a test is named after
    itself (``Shop`` is ``shop``) unless ``names`` says otherwise.
    ``providers={"forge": (Forge,)}`` composes the provider classes a test
    defines as one plugin per key, filling the contracts they subclass, so
    ``gather`` asks them; its settings are their ``Provider[S]``. Each
    settings model is written as set (secrets in clear, by alias) to the
    section of the plugin whose ``settings`` it is, whether the test named
    it, made it from ``providers`` or ``install`` brought it; a plugin given
    none has none of its own, as a user who set nothing.
    What core keeps in a section (a cache's max age) stays.
    """
    from untaped.runtime.registry import discover_candidates  # noqa: PLC0415
    from untaped.sdk.contracts._providing import providers_plugin  # noqa: PLC0415

    named = dict(names or {})
    discovered = discover_candidates()
    entries = [(cls, named.get(cls)) for cls in plugins]
    entries += [(providers_plugin(key, each), key) for key, each in (providers or {}).items()]
    candidates = tuple(candidate_of(cls, name, discovered) for cls, name in entries)
    candidates += _dependencies(candidates, discovered)
    models = [settings] if isinstance(settings, BaseModel) else list(settings)
    given: dict[str, tuple[type[BaseModel], BaseModel | None]] = {}
    for candidate in candidates:
        model = _settings_model(candidate)
        if model is not None:
            given[candidate.name] = (model, None)
    for each in models:
        section = next((name for name, (m, _) in given.items() if isinstance(each, m)), None)
        if section is None:
            raise TypeError(f"{type(each).__qualname__} is the settings of no plugin installed")
        given[section] = (given[section][0], each)
    _write_sections(given)
    installed = Install(candidates)
    _remember(installed)
    return installed


def _settings_model(candidate: PluginCandidate) -> type[BaseModel] | None:
    """The settings model of the plugin class ``candidate`` installs, if it loads."""
    from untaped.runtime.registry import resolve_target  # noqa: PLC0415

    try:
        target = resolve_target(candidate.target)
    except Exception:  # composition quarantines a target that does not load
        return None
    model = getattr(target, "settings", None)
    return model if isinstance(model, type) and issubclass(model, BaseModel) else None


def _dependencies(
    candidates: Sequence[PluginCandidate], discovered: Sequence[PluginCandidate]
) -> tuple[PluginCandidate, ...]:
    """The installed plugins ``candidates`` declare, and theirs, not already among them.

    A provider requires the owner it fills contracts of in its plain
    dependencies, so the owner comes along.
    """
    from packaging.utils import canonicalize_name  # noqa: PLC0415

    from untaped.runtime.registry import declared_distributions  # noqa: PLC0415

    by_distribution: dict[str, PluginCandidate] = {
        canonicalize_name(each.distribution): each for each in discovered
    }
    held = {each.name for each in candidates}
    added: list[PluginCandidate] = []
    waiting = list(candidates)
    while waiting:
        for name in sorted(declared_distributions(waiting.pop().requires_dist)):
            found = by_distribution.get(name)
            if found is not None and found.name not in held:
                held.add(found.name)
                added.append(found)
                waiting.append(found)
    return tuple(added)


def _remember(installed: Install) -> None:
    """Let the test's credentials file secrets against ``installed``'s plugins."""
    from untaped.sdk.network._credential_store import _TEST_STORE  # noqa: PLC0415
    from untaped.sdk.testing.fixtures import MemoryCredentials  # noqa: PLC0415

    store = _TEST_STORE.get()
    if isinstance(store, MemoryCredentials):
        store.installs.append(installed)


def candidate_of(
    cls: type[Plugin], name: str | None, discovered: Sequence[PluginCandidate]
) -> PluginCandidate:
    """``cls`` as installed (its entry point), else named ``name`` or after its class."""
    from untaped.runtime.declaration import target_of  # noqa: PLC0415
    from untaped.runtime.registry import PluginCandidate  # noqa: PLC0415

    target = target_of(cls)
    found = next((c for c in discovered if c.target == target), None)
    if found is not None and name in (None, found.name):
        return found
    name = name or kebab(cls.__name__)
    try:
        version = metadata.version(f"untaped-{name}")
    except metadata.PackageNotFoundError:
        version = ""
    return PluginCandidate(
        distribution=f"untaped-{name}", name=name, target=cls, distribution_version=version
    )


def _write_sections(sections: Mapping[str, tuple[type[BaseModel], BaseModel | None]]) -> None:
    """Write each plugin's settings model's fields as set, leaving core's keys in the section."""
    from untaped.runtime.config_file import read_config_dict, write_config_dict  # noqa: PLC0415
    from untaped.runtime.settings import active_settings_layout  # noqa: PLC0415

    raw = read_config_dict()
    target, _ = active_settings_layout().write_profile(raw, None)
    for section, (model, value) in sections.items():
        node = target.get(section)
        own = _keys(model)
        kept = {k: v for k, v in node.items() if k not in own} if isinstance(node, dict) else {}
        written = {**kept, **(_dumped(value) if value is not None else {})}
        if written:
            target[section] = written
        else:
            target.pop(section, None)
    write_config_dict(raw)


def _keys(model: type[BaseModel]) -> set[str]:
    """The keys a section may spell ``model``'s fields with: their names and aliases."""
    keys: set[str] = set()
    for name, info in model.model_fields.items():
        keys.update(k for k in (name, info.alias, info.validation_alias) if isinstance(k, str))
    return keys


def _dumped(model: BaseModel) -> dict[str, Any]:
    """``model``'s fields as set, by alias, as JSON values, with secrets in clear."""
    data = model.model_dump(exclude_unset=True, by_alias=True)
    return cast("dict[str, Any]", to_jsonable_python(_revealed(data)))


def _revealed(value: object) -> object:
    if isinstance(value, SecretStr | SecretBytes):
        return value.get_secret_value()
    if isinstance(value, dict):
        return {key: _revealed(each) for key, each in value.items()}
    if isinstance(value, list | tuple):
        return [_revealed(each) for each in value]
    return value


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
            fmt = knobs.get("format", "table")
            return str(getattr(fmt, "value", fmt))
    return None


def _piped(mount: Mount, values: object) -> str | None:
    """``values`` (rows, or bare lines) as another command's ``--format pipe`` would print them."""
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
    raises: bool,
) -> None:
    """Run ``command`` as the root runs one, under the invocation's run: errors as exit codes.

    A run that emitted a failure fails, as at the root.
    """
    from untaped.runtime.mount import run_built  # noqa: PLC0415
    from untaped.runtime.sink import derived_exit_code, exit_on_counted_failure  # noqa: PLC0415
    from untaped.sdk._cli import report_errors  # noqa: PLC0415

    try:
        with nullcontext() if raises else report_errors():
            run_built(mount, decl, command, flags)
            exit_on_counted_failure()
    except SystemExit as exc:
        exc.code = derived_exit_code(exc.code)
        raise


def _ran(result: CliResult, printed: Printed | None, *, read_back: bool) -> Ran:
    """``result`` as a :class:`Ran`, with what the command printed (read back, under ``.cli``)."""
    rows: list[BaseModel] = []
    unread: str | None = None
    if printed is not None and not read_back:
        rows = printed.rows
    elif printed is None:
        if read_back and result.exit_code != 0:
            unread = f"nothing was printed (exit {result.exit_code})"
        elif read_back:
            unread = "the line ran no command of a plugin installed (core's own print text)"
    elif printed.format in ("json", "pipe"):
        rows = [type(row).model_validate_json(row.model_dump_json()) for row in printed.rows]
    elif printed.format is not None:
        unread = (
            f"they were printed as --format {printed.format}; "
            "run it with --format json or pipe, or through .run"
        )
    return Ran(
        exit=result.exit_code,
        stdout=result.stdout,
        stderr=result.stderr.splitlines(),
        rows=rows,
        text=None if printed is None else printed.text,
        diagnostics=result.diagnostics,
        _unread=unread,
    )


@dataclass(frozen=True)
class Home:
    """The hermetic untaped home every test runs in: ``config.yml``, ``plugins/<name>/``."""

    path: Path

    def plugin(self, plugin: str | type[Plugin]) -> Path:
        """A plugin's own directory, ``plugins/<name>/``, by its name or its class."""
        from untaped.runtime.registry import discover_candidates  # noqa: PLC0415
        from untaped.runtime.settings import plugins_root  # noqa: PLC0415

        if isinstance(plugin, str):
            return plugins_root() / plugin
        return plugins_root() / candidate_of(plugin, None, discover_candidates()).name


__all__ = ["Home", "Install", "Ran", "Running", "install"]
