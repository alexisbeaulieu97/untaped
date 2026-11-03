"""What the github tests share: ``untaped github`` run under an install, and its result.

The ``settings`` fixture is the ``github`` section the next run writes, by
field (``settings["server"] = {"url": ...}``); the conftest makes it the one
:func:`run` and :func:`installed` use, with a token from ``GH_TOKEN``.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import respx

from untaped.sdk import Diagnostic
from untaped.sdk.storage import safe_path_segment
from untaped.sdk.testing import Install, Ran, install
from untaped_github.plugin import Github
from untaped_github.server import SERVER_API
from untaped_github.settings import GithubSettings
from untaped_github.version import GithubVersion, Product

#: Each recorded version's answers, one folder per version: ``recorded/<version>/<name>.json``.
RECORDED = Path(__file__).parent / "untaped_github" / "recorded"

#: The ``github`` settings the next run uses (the ``settings`` fixture), by field.
type Settings = dict[str, Any]

_SETTINGS: ContextVar[Settings | None] = ContextVar("github_test_settings", default=None)


@dataclass(frozen=True)
class Result:
    """What one ``untaped github`` run did, its stderr as one text."""

    exit_code: int
    stdout: str
    stderr: str
    ran: Ran

    @property
    def output(self) -> str:
        return self.stdout + self.stderr

    @property
    def diagnostics(self) -> tuple[Diagnostic, ...]:
        return self.ran.diagnostics


@contextmanager
def using(settings: Settings) -> Iterator[None]:
    """Runs in the block write ``settings`` as the ``github`` section."""
    token = _SETTINGS.set(settings)
    try:
        yield
    finally:
        _SETTINGS.reset(token)


def installed() -> Install:
    """github (and git) installed with the test's ``github`` settings."""
    return install(Github, settings=GithubSettings(**(_SETTINGS.get() or {})))


def run(args: Sequence[str], *, input: str | None = None) -> Result:
    """``untaped github ARGS``, with ``input`` piped on stdin."""
    ran = installed().cli(["github", *args], input=input)
    return Result(int(ran.exit), ran.stdout, "".join(f"{line}\n" for line in ran.stderr), ran)


@dataclass(frozen=True)
class Answer:
    """One recorded answer: what was asked (``GET /user``) and how the server answered."""

    request: str
    status: int
    headers: dict[str, str]
    body: Any

    @property
    def path(self) -> str:
        return self.request.split(" ", 1)[1]

    def response(self) -> httpx.Response:
        return httpx.Response(self.status, json=self.body, headers=self.headers)


@dataclass(frozen=True)
class Recorded:
    """One adapter at one version it records: where its answers live and how its server reads."""

    adapter: type
    version: GithubVersion

    @property
    def id(self) -> str:
        return f"{self.adapter.__name__}-{self.version}"

    @property
    def folder(self) -> Path:
        return RECORDED / safe_path_segment(str(self.version))

    @property
    def api(self) -> str:
        """The API URL a server of this version answers at (GHES's is synthetic)."""
        if self.version.product is Product.DOTCOM:
            return "https://api.github.com"
        return "https://ghe.example.com/api/v3"

    def answer(self, name: str) -> Answer:
        data = json.loads((self.folder / f"{name}.json").read_text(encoding="utf-8"))
        return Answer(**data)

    def answers(self) -> tuple[Answer, ...]:
        return tuple(self.answer(path.stem) for path in sorted(self.folder.glob("*.json")))

    @contextmanager
    def served(self) -> Iterator[respx.MockRouter]:
        """Every recorded answer served at :attr:`api`, by method and path."""
        origin = self.api.removesuffix("/api/v3")
        prefix = self.api.removeprefix(origin)
        with respx.mock(base_url=origin, assert_all_called=False) as mock:
            for each in self.answers():
                method = each.request.split(" ", 1)[0]
                mock.route(method=method, path=prefix + each.path).mock(
                    return_value=each.response()
                )
            yield mock


#: Every adapter of github's server API at each version it records.
RECORDINGS = tuple(
    Recorded(adapter, version) for adapter in SERVER_API.adapters for version in adapter.recorded
)
