"""Building the run a command sees, from the composition and today's machinery.

The root starts one run per invocation before anything else
(:func:`start_run`), and attaches the composition once it is built
(:func:`composition_of`); a declared command runs under it
(:func:`current_or_built`), and only code that runs one outside any
invocation (a library call, the testing kit) builds its own
(:func:`build_run`). A nested pick or a settings overlay is a child of
the current run for a block (:func:`child_run`). Each member reads what the
11.x runtime already provides: settings sections through
``get_config_section`` (each section validated once per run, on first read,
so after every root option applies), the install's plugins through
``installed_plugins()`` (read once, on first use), the home, the stderr sink
and the themed terminal (each resolved on first use: a home that doesn't
resolve fails the read that needs it, through the run's own sink).
"""

from __future__ import annotations

import functools
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from pydantic import BaseModel

from untaped.runtime.settings import get_config_section, get_settings
from untaped.runtime.sink import Diagnostics
from untaped.sdk._app_context import app_context
from untaped.sdk.plugin_records import installed_plugins
from untaped.sdk.projects import untaped_home
from untaped.sdk.run import (
    Composition,
    NoRunError,
    Run,
    SettingsOverlay,
    current_run,
    run_scope,
)

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
    """The run of one invocation, before anything is composed: :meth:`Run.attach` follows."""
    built: list[UiContext] = []

    def terminal() -> UiContext:
        if not built:
            built.append(app_context().ui())
        return built[0]

    return Run(
        locate_home=functools.cache(untaped_home) if home is None else _given(home),
        picks=_section_reader(),
        diag=Diagnostics(),
        terminal=terminal,
    )


def build_run(
    declaration: Declaration, settings: Mapping[str, type[BaseModel]], *, home: Path | None = None
) -> Run:
    """A run over ``declaration``'s plugins, for code running outside any invocation."""
    return start_run(home=home).attach(composition_of(declaration, settings))


@contextmanager
def current_or_built(
    declaration: Declaration, settings: Mapping[str, type[BaseModel]]
) -> Iterator[Run]:
    """The invocation's run; outside one (a library call), a run built over ``declaration``."""
    try:
        run = current_run()
    except NoRunError:
        with run_scope(build_run(declaration, settings)) as built:
            yield built
        return
    yield run


@contextmanager
def child_run(
    *, profiles: str | Sequence[str] | None = None, overlay: SettingsOverlay | None = None
) -> Iterator[Run]:
    """Run the block under a child of the current run: a nested pick or a settings overlay.

    The child has the parent's home, composition, rendering, diag, terminal,
    clock and notes, and its own sections, read under its own pick:
    ``profiles`` replaces the ``--profile`` pick (a string is one profile;
    ``UNTAPED_PROFILE``'s names still stack beneath it), ``overlay`` lays
    candidate values over what it loads, and either left out keeps the
    parent's. A new pick drops the settings cache on entry and exit so reads
    follow it; under an overlay the settings are never cached. A thread sees
    the child only when its context was copied inside the block.
    :class:`NoRunError` outside a run.
    """
    parent = current_run()
    child = replace(
        parent,
        picks=_section_reader(),
        profiles=parent.profiles if profiles is None else _names(profiles),
        overlay=parent.overlay if overlay is None else overlay,
    )
    if profiles is None:
        with run_scope(child):
            yield child
        return
    get_settings.cache_clear()
    try:
        with run_scope(child):
            yield child
    finally:
        get_settings.cache_clear()


def _names(profiles: str | Sequence[str]) -> tuple[str, ...]:
    return (profiles,) if isinstance(profiles, str) else tuple(profiles)
