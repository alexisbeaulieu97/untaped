"""``settings-naming`` and ``settings-renames`` on a tmp plugin's settings."""

from __future__ import annotations

import importlib
from pathlib import Path
from textwrap import dedent, indent

import pytest
from cyclopts import App
from pydantic import BaseModel, create_model

from test_conventions.support import Install
from untaped.conventions.settings_names import name_problem, settings_name_violations
from untaped.conventions.source import source_files
from untaped.conventions.structure import structure_violations
from untaped.plugins.registry import PluginSpec

_ERRORS = dedent('''
    """Acme errors."""

    from untaped.sdk import UntapedError


    class AcmeError(UntapedError):
        """An acme failure."""

        system = "acme"
    ''')


_SETTINGS = dedent('''\
    """Acme settings."""

    from pathlib import Path
    from typing import ClassVar

    from pydantic import BaseModel, ConfigDict, Field


    class Sweep(BaseModel):
        model_config = ConfigDict(frozen=True)
        sync_workers: int = 4


    class AcmeSettings(BaseModel):
        model_config = ConfigDict(frozen=True)
    {fields}

    class AcmeState(BaseModel):
        model_config = ConfigDict(frozen=True)
        cache: Path = Path("x")
    ''')


def _settings(fields: str) -> str:
    """``acme/settings.py`` with ``fields`` as ``AcmeSettings``'s fields (line 16 on)."""
    return _SETTINGS.format(fields=indent(dedent(fields).strip(), "    "))


def _violations(install: Install, body: str) -> list[str]:
    site = install(
        {
            "acme/__init__.py": '"""Acme plugin."""\n',
            "acme/errors.py": _ERRORS,
            "acme/settings.py": _settings(body),
        }
    )
    settings = importlib.import_module("acme.settings")
    spec = PluginSpec(
        name="acme",
        app_factory=App,
        settings=settings.AcmeSettings,
        state=settings.AcmeState,
    )
    source_dir = site / "acme"
    return structure_violations(spec, "acme", source_dir, list(source_files(source_dir)))


PATH = "a path setting ends in _dir or _path"
PARALLEL = "a concurrency setting is named parallel"
DURATION = "a duration ends in its unit (_seconds, _minutes, _hours, _days, _ms)"


@pytest.mark.parametrize(
    ("name", "annotation", "problem"),
    [
        ("cache", Path, PATH),
        ("cache", Path | None, PATH),
        ("cache_dir", Path, None),
        ("index_path", Path, None),
        ("path", Path, None),
        ("probe_concurrency", int, PARALLEL),
        ("max_jobs", float, PARALLEL),
        ("workers", str, None),
        ("parallel", int, None),
        ("timeout", float, DURATION),
        ("stale_after", int, DURATION),
        ("timeout_seconds", float, None),
        ("backup_max_age_days", int, None),
        ("retry_delay_ms", int, None),
        ("page_size", int, None),
        ("git_fetch_depth", int, None),
    ],
)
def test_name_rules(name: str, annotation: object, problem: str | None) -> None:
    assert name_problem(name, annotation) == problem


def test_good_names_pass(install: Install) -> None:
    body = """
    cache_dir: Path = Path("c")
    parallel: int = 4
    timeout_seconds: float = 3.0
    """
    assert _violations(install, body) == []


def test_bad_names_are_flagged_at_their_line_with_the_full_key(install: Install) -> None:
    body = """
    cache: Path | None = None
    timeout: float = 3.0
    sweep: Sweep = Field(default_factory=Sweep)
    """
    assert _violations(install, body) == [
        "acme/settings.py:16::settings-naming::acme.cache: a path setting ends in _dir or _path",
        "acme/settings.py:17::settings-naming::acme.timeout: "
        "a duration ends in its unit (_seconds, _minutes, _hours, _days, _ms)",
        "acme/settings.py:11::settings-naming::acme.sweep.sync_workers: "
        "a concurrency setting is named parallel",
    ]


def test_the_allow_comment_and_class_vars_are_skipped(install: Install) -> None:
    body = """
    retry_after: ClassVar[int] = 3
    ca_bundle: Path | None = None  # untaped: allow settings-naming
    """
    assert _violations(install, body) == []


def test_broken_renames_are_flagged_at_the_class_line(install: Install) -> None:
    body = """
    renamed_keys: ClassVar[dict[str, str]] = {"old": "missing"}
    cache_dir: Path = Path("c")
    """
    assert _violations(install, body) == [
        "acme/settings.py:14::settings-renames::"
        "renamed key 'old' points at 'missing', which is not a setting"
    ]


def test_a_model_without_source_reports_renames_by_name() -> None:
    model = create_model("Made", cache=(Path, Path("c")))
    model.renamed_keys = {"old": "missing"}  # type: ignore[attr-defined]

    assert settings_name_violations("made", model, Path("/nowhere")) == [
        f"{model.__module__}.Made::settings-renames::"
        "renamed key 'old' points at 'missing', which is not a setting"
    ]


def test_a_model_outside_the_root_keeps_its_full_path() -> None:
    class Outside(BaseModel):
        timeout: float = 3.0

    assert settings_name_violations("out", Outside, Path("/nowhere")) == [
        f"{Path(__file__).as_posix()}:{Outside.__firstlineno__ + 1}::settings-naming::"
        f"out.timeout: {DURATION}"
    ]
