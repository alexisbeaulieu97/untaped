"""What the ansible tests share: ``untaped ansible`` run under an install, and its home.

The conftest points ``ansible.index_dir`` at the test's ``tmp_path`` (the
index there is :mod:`ansible_index_support`'s); :func:`configure` sets the
sections the next run writes, and :func:`write_state` the plugin's
``state.yml``.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import BaseModel

from untaped.sdk import Command, Diagnostic, Output, Plugin, Record
from untaped.sdk.testing import Home, Install, Ran, Running, install
from untaped_ansible.plugin import Ansible
from untaped_ansible.settings import AnsibleSettings
from untaped_git.settings import GitSettings
from untaped_github.settings import GithubSettings

#: The token a test that reads GitHub runs with, from the GitHub CLI's variable.
TOKEN = "ghp_test"


@dataclass
class Sections:
    """The test's home, and the sections its next run writes: ``ansible``, ``github``, ``git``."""

    home: Home
    ansible: dict[str, Any] = field(default_factory=dict)
    github: dict[str, Any] = field(default_factory=dict)
    git: dict[str, Any] = field(default_factory=dict)


_SECTIONS: ContextVar[Sections | None] = ContextVar("ansible_test_sections", default=None)


@dataclass(frozen=True)
class Result:
    """What one ``untaped ansible`` run did, its stderr as one text."""

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
def using(sections: Sections) -> Iterator[None]:
    """Runs in the block write ``sections``."""
    token = _SECTIONS.set(sections)
    try:
        yield
    finally:
        _SECTIONS.reset(token)


def sections() -> Sections:
    """The sections the next run writes (the conftest's, for the test)."""
    current = _SECTIONS.get()
    if current is None:
        raise RuntimeError("no ansible sections: the conftest sets them for each test")
    return current


def configure(
    *,
    ansible: Mapping[str, Any] | None = None,
    github: Mapping[str, Any] | None = None,
    git: Mapping[str, Any] | None = None,
) -> None:
    """Add fields to the next run's ``ansible``, ``github`` and ``git`` sections."""
    current = sections()
    current.ansible.update(ansible or {})
    current.github.update(github or {})
    current.git.update(git or {})


def use_config(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    state: Mapping[str, Any] | None = None,
    *,
    token: bool = False,
    ansible: Mapping[str, Any] | None = None,
    github: Mapping[str, Any] | None = None,
    ui: Mapping[str, Any] | None = None,
) -> None:
    """Set the next run's sections and write ``state``; ``token`` keeps ``GH_TOKEN`` set.

    An ``ansible`` key the settings model has no field for (``caches``, a
    retired key) is written to ``config.yml`` as it is; ``ui`` is core's
    ``untaped.ui``, set through its variables.
    """
    del tmp_path  # the conftest's ``ansible.index_dir``
    if not token:
        monkeypatch.delenv("GH_TOKEN", raising=False)
    for name, value in (ui or {}).items():
        monkeypatch.setenv(f"UNTAPED_UNTAPED__UI__{name.upper()}", str(value))
    given = dict(ansible or {})
    raw = {key: given.pop(key) for key in list(given) if key not in AnsibleSettings.model_fields}
    if raw:
        write_config({"ansible": raw})
    configure(ansible=given, github=github)
    if state is not None:
        write_state(state)


class JobTemplate(Record):
    """An awx job template as ``find`` reads it from a pipe: its identity and its SCM."""

    id: int
    name: str | None = None
    scm_url: str | None = None
    effective_scm_ref: str | None = None


class AwxSettings(BaseModel):
    """Nothing to configure."""


class JobTemplates(Command[AwxSettings]):
    """List no job templates: the command that declares the ``awx.job_template`` kind."""

    output: Output[JobTemplate]

    def run(self) -> Iterator[JobTemplate]:
        yield from ()


class Awx(Plugin):
    """A stand-in for awx, whose ``awx.job_template`` records ``find`` reads."""

    settings = AwxSettings
    commands = (JobTemplates,)


def installed(*also: type[Plugin]) -> Install:
    """ansible (with github and git) installed with the test's sections, and ``also``."""
    current = sections()
    return install(
        Ansible,
        *also,
        settings=[
            AnsibleSettings(**current.ansible),
            GithubSettings(**current.github),
            GitSettings(**current.git),
        ],
    )


def run(*args: str, input: str | None = None, also: tuple[type[Plugin], ...] = ()) -> Result:
    """``untaped ansible ARGS``, with ``input`` piped on stdin and ``also`` installed."""
    return _result(installed(*also).cli(["ansible", *args], input=input))


def run_root(*args: str, input: str | None = None) -> Result:
    """``untaped ARGS``, ansible installed: a root command such as ``config`` or ``doctor``."""
    return _result(installed().cli(list(args), input=input))


@contextmanager
def running() -> Iterator[Running]:
    """The block runs as an ansible command does: under its install's run."""
    with installed().running() as current:
        yield current


def _result(ran: Ran) -> Result:
    return Result(int(ran.exit), ran.stdout, "".join(f"{line}\n" for line in ran.stderr), ran)


def write_config(data: Mapping[str, Any]) -> None:
    """Merge ``data``'s sections into ``config.yml`` as written; an install keeps what it does
    not set."""
    path = sections().home.path / "config.yml"
    config = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else None
    config = config or {"format_version": 2}
    for section, values in data.items():
        current = config.get(section)
        config[section] = {**current, **values} if isinstance(current, dict) else values
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")


def state_path() -> Path:
    """ansible's ``state.yml`` in the hermetic home."""
    return sections().home.plugin("ansible") / "state.yml"


def write_state(state: Mapping[str, Any]) -> None:
    """Write ansible's ``state.yml``: ``sources`` rows, and ``aliases`` as rows or a name map."""
    written = dict(state)
    aliases = written.get("aliases")
    if isinstance(aliases, Mapping) and all(isinstance(v, str) for v in aliases.values()):
        written["aliases"] = [{"alias": name, "repo": repo} for name, repo in aliases.items()]
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(written, sort_keys=False), encoding="utf-8")


def read_state() -> dict[str, Any]:
    """ansible's ``state.yml`` without core's header, ``aliases`` as a name map (``{}`` unset)."""
    path = state_path()
    if not path.exists():
        return {}
    state = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    state.pop("untaped", None)
    if isinstance(state.get("aliases"), list):
        state["aliases"] = {row["alias"]: row["repo"] for row in state["aliases"]}
    return state


__all__ = [
    "TOKEN",
    "Awx",
    "JobTemplate",
    "Result",
    "Sections",
    "configure",
    "installed",
    "read_state",
    "run",
    "run_root",
    "running",
    "sections",
    "state_path",
    "use_config",
    "using",
    "write_config",
    "write_state",
]
