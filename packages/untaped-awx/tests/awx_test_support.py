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
from pathlib import Path
from typing import Any

from untaped.runtime.bootstrap import build_root_app
from untaped.sdk.network import ServerVersion
from untaped.sdk.storage import safe_path_segment
from untaped.sdk.testing import CliResult, Install, PromptBackend, install, invoke_cli
from untaped.sdk.testing.fixtures import current_terminal
from untaped_awx.commands.context import AwxContext
from untaped_awx.commands.controller import Ping
from untaped_awx.infrastructure import AwxClient
from untaped_awx.infrastructure.controller.jobs import Jobs
from untaped_awx.infrastructure.controller.resources import ResourceRepository
from untaped_awx.infrastructure.controller.unified_templates import UnifiedTemplates
from untaped_awx.infrastructure.controller.workflow_nodes import WorkflowNodes
from untaped_awx.plugin import Awx
from untaped_awx.settings import AwxSettings

#: What each recorded controller version answered, one folder per version.
RECORDED = Path(__file__).parent / "untaped_awx" / "recorded"

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
    if prompt_backend is None and scripted is not None and scripted.is_present:
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


def recorded_answer(version: ServerVersion, name: str) -> Any:
    """What ``version``'s controller answered, recorded as ``recorded/<version>/<name>.json``."""
    folder = RECORDED / safe_path_segment(str(version))
    return json.loads((folder / f"{name}.json").read_text(encoding="utf-8"))


class ClientRepository(ResourceRepository, UnifiedTemplates):
    """The resource reads and writes over a given client: :class:`ResourceLookups`' own tests."""

    def __init__(self, client: AwxClient, *, page_size: int = 200) -> None:
        self._given = client
        self._page_size = page_size

    @property
    def _client(self) -> AwxClient:
        return self._given


type Call = tuple[str, str, dict[str, str]]


class RecordingController(Jobs, WorkflowNodes, UnifiedTemplates):
    """The controller's parts, recording each call instead of sending it.

    ``answers`` are the JSON answers, in order (an empty page once they run
    out); a listing reads its pages from them, following each page's
    ``next``. ``texts`` are the text answers, in order.
    """

    _page_size = 200

    def __init__(
        self,
        *,
        answers: Sequence[dict[str, Any]] = (),
        texts: Sequence[str] = (),
    ) -> None:
        self._answers = list(answers)
        self._texts = list(texts)
        #: Every JSON request: method, path, params.
        self.json_calls: list[Call] = []
        #: The body of each JSON request, in order (``None``: none sent).
        self.bodies: list[dict[str, Any] | None] = []
        #: Every listing: path, params, limit.
        self.listings: list[tuple[str, dict[str, str], int | None]] = []
        #: Every text request: method, path, params.
        self.text_calls: list[Call] = []

    @property
    def _client(self) -> AwxClient:
        raise AssertionError("a recording controller sends nothing")

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        json: dict[str, Any] | None = None,
        collection: bool = False,
    ) -> dict[str, Any]:
        self.json_calls.append((method, path, dict(params or {})))
        self.bodies.append(json)
        return self._answers.pop(0) if self._answers else {"results": [], "next": None}

    def _paginate(
        self,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        limit: int | None = None,
        collection: bool = False,
    ) -> Iterator[dict[str, Any]]:
        self.listings.append((path, dict(params or {}), limit))
        page = self._request("GET", path, params=params)
        while True:
            yield from page.get("results") or []
            if not page.get("next"):
                return
            page = self._request("GET", page["next"])

    def _text(self, path: str, *, params: Mapping[str, str] | None = None) -> str:
        self.text_calls.append(("GET", path, dict(params or {})))
        return self._texts.pop(0) if self._texts else ""
