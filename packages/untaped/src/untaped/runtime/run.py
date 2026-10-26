"""Building the run a command sees, from the composition and today's machinery.

The root starts one run per invocation before anything else
(:func:`start_run`), and attaches the composition once it is built
(:func:`composition_of`); a declared command runs under it
(:func:`current_or_built`), and only code that runs one outside any
invocation (a library call, the testing kit) builds its own
(:func:`build_run`). A run is entered as its invocation's
(:func:`entered`): its output is open while it is current. A nested pick or a
settings overlay is a child of the current run for a block
(:func:`child_run`), and so is an invocation nested in a test's
(:func:`invocation`), with its own output. Each member reads what the
11.x runtime already provides: settings sections through
``get_config_section`` (each section validated once per run, on first read,
so after every root option applies), the install's plugins through
``installed_plugins()`` (read once, on first use), the home and the themed
terminal (each resolved on first use: a home that doesn't resolve fails the
read that needs it, through the run's own sink). The run's output
(``untaped.runtime.sink.RunOutput``) and its policy, ``UNTAPED_DIAGNOSTICS``
read once, are built with it.
"""

from __future__ import annotations

import functools
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel

from untaped.runtime.profile_resolver import pick_names
from untaped.runtime.settings import get_config_section
from untaped.runtime.sink import RunOutput, forced_diagnostics, opened
from untaped.sdk._app_context import app_context
from untaped.sdk._run import (
    Composition,
    OutputPolicy,
    Run,
    SettingsOverlay,
    current_run,
    run_or_none,
    run_scope,
)
from untaped.sdk.plugin_records import installed_plugins
from untaped.sdk.projects import untaped_home

if TYPE_CHECKING:
    from untaped.runtime.declaration import Declaration
    from untaped.sdk._ui import UiContext


def composition_of(
    declaration: Declaration, settings: Mapping[str, type[BaseModel]]
) -> Composition:
    """Who owns what among the composed plugins of ``declaration`` (``settings`` models by name)."""
    packages: dict[str, str] = {}
    for plugin in declaration.plugins:
        packages.setdefault(plugin.package, plugin.name)
    return Composition(
        owners={settings[plugin.name]: plugin.name for plugin in declaration.plugins},
        packages=packages,
        installed=functools.cache(lambda: tuple(installed_plugins())),
        spell=declaration.spell,
    )


def _given(home: Path) -> Callable[[], Path]:
    return lambda: home


def _section_reader() -> Callable[[str, type[BaseModel]], BaseModel]:
    """A run's ``picks``: each section validated once, on first read, under the run's pick."""
    picked: dict[str, BaseModel] = {}

    def picks(section: str, model: type[BaseModel]) -> BaseModel:
        if section not in picked:
            picked[section] = get_config_section(section, model)
        return picked[section]

    return picks


def start_run(*, home: Path | None = None) -> Run:
    """The run of one invocation, before anything is composed: :meth:`Run.attach` follows.

    Its output is its own, and ``UNTAPED_DIAGNOSTICS`` is read now, as the
    run's other inputs are.
    """
    built: list[UiContext] = []

    def terminal() -> UiContext:
        if not built:
            built.append(app_context().ui())
        return built[0]

    output = RunOutput()
    run = Run(
        locate_home=functools.cache(untaped_home) if home is None else _given(home),
        picks=_section_reader(),
        diag=output,
        terminal=terminal,
        policy=OutputPolicy(forced=forced_diagnostics()),
    )
    output.bind(run)
    return run


@contextmanager
def entered(run: Run) -> Iterator[Run]:
    """Make ``run`` current for the block as an invocation's: its output open while it is.

    Shown Python warnings reach its sink, and lines a screen held print when
    the block ends (:func:`untaped.runtime.sink.opened`).
    """
    output = run.diag
    if not isinstance(output, RunOutput):
        raise TypeError("an entered run has its own output: build it with start_run()")
    with run_scope(run), opened(output):
        yield run


@contextmanager
def invocation() -> Iterator[Run]:
    """Run the block as an invocation nested in a test's: a child of the current run.

    The child keeps the parent's home, composition, rendering, policy,
    picks, terminal, clock and notes, with an output of its own, so what it
    counts and holds is its own; with no run current it is a bare run
    (:func:`start_run`), whose composition a declared command attaches
    (:func:`current_or_built`). The testing kit's ``invoke_cli`` runs each
    invocation under it.
    """
    parent = run_or_none()
    if parent is None:
        run = start_run()
    else:
        output = RunOutput()
        run = replace(parent, diag=output)
        output.bind(run)
    with entered(run):
        yield run


type FlagToken = tuple[Run, OutputPolicy]
"""What a flag handler returns: the run it changed and its policy before."""


def set_flag(name: Literal["quiet", "verbose"], value: bool) -> FlagToken:
    """Set ``--quiet`` or ``--verbose`` on the current run's policy (:class:`NoRunError` off it)."""
    run = current_run()
    previous = run.policy
    run.set_policy(
        replace(previous, quiet=value) if name == "quiet" else replace(previous, verbose=value)
    )
    return run, previous


def reset_flag(name: Literal["quiet", "verbose"], token: FlagToken) -> None:
    """Restore the flag ``token`` saved on the run it changed."""
    run, previous = token
    run.set_policy(
        replace(run.policy, quiet=previous.quiet)
        if name == "quiet"
        else replace(run.policy, verbose=previous.verbose)
    )


def enable_quiet(_value: str = "") -> FlagToken:
    """Root-option handler for ``--quiet``/``-q``, on the run of the invocation.

    Mutes commentary: lines the user did not ask for and need not act on
    (progress, ``success`` and ``info``). A preview before a prompt, a stream
    the user asked to watch, and output a flag exists to produce are not
    commentary and print (``info(…, unmuted=True)``). Warnings, failures and
    hints always print, as do prompts and stdout. Takes no value; the
    dispatcher passes a placeholder string that is ignored. Nothing undoes
    it: the run ends with its invocation.
    """
    return set_flag("quiet", True)


def build_run(
    declaration: Declaration, settings: Mapping[str, type[BaseModel]], *, home: Path | None = None
) -> Run:
    """A run over ``declaration``'s plugins, for code running outside any invocation."""
    return start_run(home=home).attach(composition_of(declaration, settings))


@contextmanager
def current_or_built(
    declaration: Declaration, settings: Mapping[str, type[BaseModel]]
) -> Iterator[Run]:
    """The invocation's run; outside one (a library call), a run built over ``declaration``.

    A run not yet composed (a test's bare invocation, :func:`invocation`) is
    attached the composition of ``declaration``.
    """
    run = run_or_none()
    if run is None:
        with entered(build_run(declaration, settings)) as built:
            yield built
        return
    if not run.attached:
        run.attach(composition_of(declaration, settings))
    yield run


@contextmanager
def child_run(
    *, profiles: str | Sequence[str] | None = None, overlay: SettingsOverlay | None = None
) -> Iterator[Run]:
    """Run the block under a child of the current run: a nested pick or a settings overlay.

    The child has the parent's home, composition, rendering, diag (its sink
    and count, following the parent's policy), terminal, clock and notes,
    and its own sections, read under its own pick:
    ``profiles`` replaces the ``--profile`` pick (a string is one profile;
    ``UNTAPED_PROFILE``'s names still stack beneath it; ``"default"`` reads
    the base alone), ``overlay`` lays candidate values over what it loads,
    and either left out keeps the parent's. An empty pick raises
    :class:`ValueError`: leave ``profiles`` out to keep the parent's. The
    aggregate settings are cached per pick and never under an overlay, so
    nothing is dropped on entry or exit. A thread sees the child only when
    its context was copied inside the block. :class:`NoRunError` outside a run.
    """
    names = None if profiles is None else pick_names(profiles)
    if names == ():
        raise ValueError(
            "a child run's pick names a profile; leave `profiles` out to keep the parent's"
        )
    parent = current_run()
    child = replace(
        parent,
        picks=_section_reader(),
        profiles=parent.profiles if names is None else names,
        overlay=parent.overlay if overlay is None else overlay,
    )
    with run_scope(child):
        yield child
