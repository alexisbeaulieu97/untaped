"""What the awx tests share: ``untaped awx`` run under an install, and its result.

The ``settings`` fixture is the ``awx`` section the next run writes, by field
(``settings["default_organization"] = "Default"``); the conftest makes it the
one :func:`run` and :func:`installed` use, with a token from
``CONTROLLER_OAUTH_TOKEN``.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from untaped.runtime.bootstrap import build_root_app
from untaped.sdk.testing import CliResult, Install, PromptBackend, install, invoke_cli
from untaped.sdk.testing.fixtures import current_terminal
from untaped_awx.commands.context import AwxContext
from untaped_awx.commands.controller import Ping
from untaped_awx.plugin import Awx
from untaped_awx.settings import AwxSettings

#: The ``awx`` settings the next run uses (the ``settings`` fixture), by field.
type Settings = dict[str, Any]

_SETTINGS: ContextVar[Settings | None] = ContextVar("awx_test_settings", default=None)


@contextmanager
def using(settings: Settings) -> Iterator[None]:
    """Runs in the block write ``settings`` as the ``awx`` section."""
    token = _SETTINGS.set(settings)
    try:
        yield
    finally:
        _SETTINGS.reset(token)


def installed(*, providers: Mapping[str, Sequence[type]] | None = None) -> Install:
    """awx (and lint) installed with the test's ``awx`` settings, and ``providers``' plugins."""
    return install(Awx, settings=AwxSettings(**(_SETTINGS.get() or {})), providers=providers)


@contextmanager
def context() -> Iterator[AwxContext]:
    """The controller's context as a command's run opens it, for a test of the use cases."""
    with installed().running(), Ping().awx() as awx:
        yield awx


def run(
    args: Sequence[str],
    *,
    input: str | None = None,
    interactive: bool = False,
    prompt_backend: PromptBackend | None = None,
    terminal: bool = False,
    on: Install | None = None,
    before: Sequence[str] = (),
) -> CliResult:
    """``untaped BEFORE awx ARGS`` under ``on`` (default :func:`installed`), ``input`` on stdin.

    ``prompt_backend`` answers the prompts (``terminal=True`` gives the
    command a terminal; ``interactive=True`` makes stdin one); without it, the
    ``terminal`` fixture's terminal is the one, when the test scripted it.
    """
    root = build_root_app(candidates=(on or installed()).candidates)
    scripted = current_terminal()
    if prompt_backend is None and scripted is not None and scripted.present:
        prompt_backend, terminal = scripted, True
        interactive = interactive or input is None
    return invoke_cli(
        root.meta,
        [*before, "awx", *args],
        input=input,
        catch_exceptions=False,
        interactive=interactive,
        prompt_backend=prompt_backend,
        terminal=terminal,
    )


def human(stderr: str) -> str:
    """The JSON diagnostics of a ``--format json`` run, as the lines a terminal shows.

    ``{"level": "error", "item": "x", "message": "m"}`` reads ``error: x: m``.
    """
    lines = []
    for line in stderr.splitlines():
        record = json.loads(line)
        item = f"{record['item']}: " if record.get("item") else ""
        lines.append(f"{record['level']}: {item}{record['message']}")
    return "".join(f"{line}\n" for line in lines)
