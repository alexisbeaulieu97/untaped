"""A cache's setting ``<plugin>.caches.<cache>.max_age_seconds``, generated from the spec.

A ``hello`` fixture plugin keeps the ``motd`` cache over a fake upstream; its
``show`` command serves it, so the setting is read the way a command reads it.
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Mapping, Sequence
from datetime import timedelta
from pathlib import Path
from typing import Annotated, ClassVar

import pytest
from cyclopts import App
from pydantic import BaseModel, Field

from untaped.core.plugin_check.settings_names import name_problem, settings_name_violations
from untaped.runtime.bootstrap import build_root_app
from untaped.runtime.config_file import read_config_dict
from untaped.runtime.registry import PluginSpec, settings_model
from untaped.runtime.settings import get_settings
from untaped.sdk._cli import create_app, echo, report_errors
from untaped.sdk._home import config_path
from untaped.sdk.diagnostic import Deprecation, Message
from untaped.sdk.health.deprecated_keys import Retired, key_mappings, mapping_errors
from untaped.sdk.progress import ProgressHandle
from untaped.sdk.storage.cache import Cache, Filled, refresh_option
from untaped.sdk.testing import CliInvoker, CliResult, cache_clock, plugin_candidate

MOTD = Cache("hello", "motd", label="message of the day", max_age=timedelta(hours=1))
SETTING = "hello.caches.motd.max_age_seconds"
FILLS: list[str] = []


class HelloSettings(BaseModel):
    greeting: str = "hi"


def _fill(key: str, _task: ProgressHandle) -> Filled:
    FILLS.append(key)
    return Filled()


def _app() -> App:
    app = create_app(name="hello", help="Say hello.")

    @app.command(name="show")
    def show(*, refresh: Annotated[bool | None, refresh_option(MOTD)] = None) -> None:
        """Print the message of the day."""
        with report_errors():
            served = MOTD.serve({"motd": "the message"}, refresh=refresh, fill=_fill, read=str)
        echo(json.dumps(served.value))

    return app


SPEC = PluginSpec(name="hello", app_factory=_app, settings=HelloSettings, caches=(MOTD,))


@pytest.fixture(autouse=True)
def _no_fills() -> None:
    FILLS.clear()


def _run(*argv: str, spec: PluginSpec = SPEC) -> CliResult:
    app = build_root_app(candidates=[plugin_candidate(spec)])
    return CliInvoker().invoke(app.meta, list(argv))


def _write(text: str) -> Path:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    get_settings.cache_clear()
    return path


def _hello(body: str, profile: str = "default") -> str:
    """A config with ``body`` as ``profile``'s ``hello`` section (``default`` is the base)."""
    at = "" if profile == "default" else "    "
    lines = "".join(f"{at}  {line}\n" for line in body.splitlines())
    named = f"profiles:\n  {profile}:\n" if at else ""
    return f"format_version: 2\n{named}{at}hello:\n{lines}"


def _warnings(result: CliResult) -> list[str]:
    return [
        d.message
        for d in result.diagnostics
        if isinstance(d, Deprecation) or (isinstance(d, Message) and d.level == "warning")
    ]


# --- the setting ------------------------------------------------------------


def test_the_setting_defaults_to_the_declared_max_age() -> None:
    result = _run("config", "get", SETTING, "--format", "json")
    assert result.exit_code == 0, result.output
    got = json.loads(result.stdout)
    assert (got["value"], got["default"], got["source"]) == (3600, 3600, "default")
    listed = json.loads(_run("config", "list", "--format", "json").stdout)
    assert SETTING in {row["key"] for row in listed}


def test_config_set_tunes_the_max_age_read_at_call_time() -> None:
    _run("config", "list")  # composes hello in this process
    assert MOTD.max_age() == timedelta(hours=1)
    assert _run("config", "set", SETTING, "60").exit_code == 0
    assert MOTD.max_age() == timedelta(seconds=60)
    assert json.loads(_run("config", "get", SETTING, "--format", "json").stdout)["value"] == 60


def test_the_setting_is_per_profile() -> None:
    _write(_hello("caches:\n  motd:\n    max_age_seconds: 5", profile="fast"))
    assert _run("--profile", "fast", "config", "get", SETTING).stdout.strip() == "5"
    assert _run("config", "get", SETTING).stdout.strip() == "3600"


def test_the_setting_has_its_environment_variable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNTAPED_HELLO__CACHES__MOTD__MAX_AGE_SECONDS", "7")
    _run("config", "list")
    assert MOTD.max_age() == timedelta(seconds=7)


def test_an_uncomposed_cache_reads_its_declared_default() -> None:
    _write(_hello("caches:\n  motd:\n    max_age_seconds: 5"))
    assert MOTD.max_age() == timedelta(hours=1)


def test_a_cache_left_off_the_spec_reads_its_declared_default() -> None:
    _write(_hello("caches:\n  motd:\n    max_age_seconds: 5"))
    news = Cache("hello", "news", label="news", max_age=timedelta(minutes=2))
    _run("config", "list")
    assert news.max_age() == timedelta(minutes=2)


def test_zero_checks_upstream_on_every_read() -> None:
    _write(_hello("caches:\n  motd:\n    max_age_seconds: 0"))
    with cache_clock() as clock:
        assert _run("hello", "show").exit_code == 0
        clock.advance(timedelta(seconds=1))
        assert _run("hello", "show").exit_code == 0
    assert FILLS == ["motd", "motd"]


def test_within_the_max_age_a_read_is_served_from_the_cache() -> None:
    with cache_clock() as clock:
        assert _run("hello", "show").exit_code == 0
        clock.advance(timedelta(minutes=59))
        assert _run("hello", "show").exit_code == 0
    assert FILLS == ["motd"]


def test_a_negative_max_age_is_a_settings_error() -> None:
    _write(_hello("caches:\n  motd:\n    max_age_seconds: -1"))
    result = _run("hello", "show")
    assert result.exit_code == 4
    assert SETTING in result.stderr
    assert FILLS == []
    refused = _run("config", "set", SETTING, "-1")
    assert refused.exit_code != 0
    assert "greater than or equal to 0" in refused.stderr


def test_a_typo_under_a_cache_loads_and_doctor_names_it() -> None:
    _write(_hello("caches:\n  motd:\n    max_age: 5"))
    with cache_clock():
        assert _run("hello", "show").exit_code == 0
    assert MOTD.max_age() == timedelta(hours=1)
    rows = json.loads(_run("doctor", "--format", "json").stdout)
    (row,) = [row for row in rows if row["check"] == "untaped.unknown-keys"]
    assert row["status"] == "warn"
    assert "hello.caches.motd.max_age" in row["detail"]


def test_the_refresh_option_names_a_setting_config_get_accepts() -> None:
    help_text = _run("hello", "show", "--help").stdout
    assert SETTING in help_text.replace("\n", "").replace(" ", "")
    assert _run("config", "get", SETTING).exit_code == 0


def test_an_unknown_cache_loads_and_doctor_names_it() -> None:
    _write(_hello("caches:\n  typo:\n    max_age_seconds: 5"))
    assert _run("config", "get", SETTING).stdout.strip() == "3600"
    rows = json.loads(_run("doctor", "--format", "json").stdout)
    (row,) = [row for row in rows if row["check"] == "untaped.unknown-keys"]
    assert row["status"] == "warn"
    assert "hello.caches.typo" in row["detail"]


def test_the_generated_setting_follows_the_naming_conventions() -> None:
    caches = settings_model(SPEC).model_fields["caches"].annotation
    assert isinstance(caches, type) and issubclass(caches, BaseModel)
    (motd,) = caches.model_fields.values()
    assert isinstance(motd.annotation, type) and issubclass(motd.annotation, BaseModel)
    assert list(motd.annotation.model_fields) == ["max_age_seconds"]
    assert name_problem("max_age_seconds", int) is None


# --- renamed_from -----------------------------------------------------------


MOTD_RENAMED = Cache(
    "hello",
    "motd",
    label="message of the day",
    max_age=timedelta(hours=1),
    renamed_from=("motd_seconds",),
)
RENAMED = PluginSpec(name="hello", app_factory=_app, settings=HelloSettings, caches=(MOTD_RENAMED,))


def test_an_old_key_is_read_as_the_setting_with_a_warning() -> None:
    _write(_hello("motd_seconds: 9"))
    result = _run("config", "get", SETTING, spec=RENAMED)
    assert result.stdout.strip() == "9"
    (warning,) = _warnings(result)
    assert warning.startswith(
        f"hello.motd_seconds is deprecated and will be removed in a future major release; "
        f"use {SETTING}"
    )
    assert MOTD_RENAMED.max_age() == timedelta(seconds=9)


def test_the_old_environment_variable_is_read_too(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNTAPED_HELLO__MOTD_SECONDS", "11")
    result = _run("config", "get", SETTING, spec=RENAMED)
    assert result.stdout.strip() == "11"
    assert any("UNTAPED_HELLO__MOTD_SECONDS" in line for line in _warnings(result))


def test_config_migrate_moves_the_old_key_into_caches() -> None:
    path = _write(_hello("greeting: hey\nmotd_seconds: 9"))
    result = _run("config", "migrate", "--format", "json", spec=RENAMED)
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)[0]["to"] == SETTING
    assert read_config_dict(path)["hello"] == {
        "greeting": "hey",
        "caches": {"motd": {"max_age_seconds": 9}},
    }


def test_the_composed_model_carries_the_rename_and_the_owner_s_own() -> None:
    class Owner(BaseModel):
        renamed_keys: ClassVar[Mapping[str, str]] = {"salutation": "greeting"}

        greeting: str = "hi"

    spec = PluginSpec(name="hello", settings=Owner, caches=(MOTD_RENAMED,))
    composed = settings_model(spec)
    assert mapping_errors(composed) == []
    assert dict(key_mappings(composed).readable) == {
        "salutation": "greeting",
        "motd_seconds": "caches.motd.max_age_seconds",
    }
    assert "renamed_keys" not in composed.model_fields
    assert dict(Owner.renamed_keys) == {"salutation": "greeting"}


class Sweep(BaseModel):
    parallel: int = 4


class GithubLike(BaseModel):
    """D's shape: a dotted old key beside a setting that stays in its model."""

    sweep: Sweep = Field(default_factory=Sweep)


def test_a_dotted_old_key_moves_beside_a_surviving_sibling() -> None:
    swept = Cache(
        "hello",
        "sweep",
        label="sweep results",
        max_age=timedelta(minutes=10),
        renamed_from=("sweep.max_age_seconds",),
    )
    spec = PluginSpec(name="hello", settings=GithubLike, caches=(swept,))
    path = _write(_hello("sweep:\n  parallel: 2\n  max_age_seconds: 30"))
    got = _run("config", "get", "hello.caches.sweep.max_age_seconds", spec=spec)
    assert got.stdout.strip() == "30"
    assert _run("config", "migrate", spec=spec).exit_code == 0
    assert read_config_dict(path)["hello"] == {
        "sweep": {"parallel": 2},
        "caches": {"sweep": {"max_age_seconds": 30}},
    }


class AnsibleAfterC(BaseModel):
    """Decision 6: the owner retires its own old key; the cache renames the newer one."""

    retired_keys: ClassVar[Mapping[str, str | Retired]] = {
        "stale_after": Retired(note="use hello.caches.index.max_age_seconds"),
    }

    greeting: str = "hi"


INDEX = Cache(
    "hello",
    "index",
    label="source data",
    max_age=timedelta(days=1),
    renamed_from=("stale_after_seconds",),
)
AFTER_C = PluginSpec(name="hello", settings=AnsibleAfterC, caches=(INDEX,))


def test_an_owner_retires_its_key_while_the_cache_renames_the_next_one() -> None:
    assert mapping_errors(AnsibleAfterC) == []
    assert settings_name_violations("hello", AnsibleAfterC, Path.cwd()) == []
    path = _write(_hello("stale_after: 60\nstale_after_seconds: 120"))
    result = _run("config", "migrate", "--format", "json", spec=AFTER_C)
    assert result.exit_code == 0, result.output
    rows = {row["from"]: row for row in json.loads(result.stdout)}
    assert rows["hello.stale_after_seconds"]["to"] == "hello.caches.index.max_age_seconds"
    assert rows["hello.stale_after"]["action"] == "deleted"
    assert "use hello.caches.index.max_age_seconds" in rows["hello.stale_after"]["detail"]
    assert read_config_dict(path)["hello"] == {"caches": {"index": {"max_age_seconds": 120}}}


def _section(spec: PluginSpec) -> Sequence[str]:
    return list(settings_model(spec).model_fields)


def test_the_renamed_spec_composes_like_any_other() -> None:
    assert _section(RENAMED) == ["greeting", "caches"]
    assert _section(AFTER_C) == ["greeting", "caches"]


class Badly(BaseModel):
    timeout: int = 5


@pytest.mark.parametrize("cache", [MOTD, MOTD_RENAMED], ids=["plain", "renamed_from"])
def test_the_owner_s_fields_are_still_checked_once_caches_are_composed(cache: Cache) -> None:
    composed = settings_model(PluginSpec(name="hello", settings=Badly, caches=(cache,)))
    assert composed is not Badly
    line = inspect.getsourcelines(Badly)[1] + 1
    assert settings_name_violations("hello", composed, Path(__file__).parent) == [
        f"{Path(__file__).name}:{line}::settings-naming::hello.timeout: a duration ends in its "
        "unit (_seconds, _minutes, _hours, _days, _ms)"
    ]
