"""A plugin plans writes to its own settings: ``PluginHandle.plan_settings`` (#1081).

The plan is checked when it is made and writes nothing; ``preview()`` is what
``Confirm(preview=)`` prints; ``write()`` writes where ``config set`` writes,
a ``HomeOnly`` key to the base, validated again against the file as it is then.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import Annotated, Literal

import pytest
from pydantic import BaseModel, Field, SecretStr

from core_test_support.commands.support import compose, make_plugin
from core_test_support.runtime.run import holding_runs
from untaped.runtime.bootstrap import build_root_app
from untaped.runtime.config_file import read_config_dict
from untaped.runtime.folder_layer import forget_folder_layer
from untaped.runtime.settings import get_settings
from untaped.sdk import (
    Command,
    Confirm,
    DryRun,
    SettingKey,
    SettingsWritten,
    key,
    plugin_of,
)
from untaped.sdk.errors import ConfigError
from untaped.sdk.health import HomeOnly
from untaped.sdk.testing import CliResult, invoke_cli, plugin_candidate

pytestmark = pytest.mark.usefixtures("_isolated_config")


class Item(BaseModel):
    enabled: bool = True
    policy: Literal["sync", "once", "manual"] | None = None


class Source(BaseModel):
    url: str
    packages: dict[str, Item] = {}


class ShelfSettings(BaseModel):
    sources: Annotated[dict[str, Source], HomeOnly()] = {}
    greeting: str = "hello"
    retries: int = Field(3, gt=0)
    token: SecretStr | None = None


class OtherSettings(BaseModel):
    level: int = 1


SHELF = key(ShelfSettings)

HOME = """\
format_version: 2
# kept: a comment above the section
shelf:
  greeting: hey  # kept: a comment on a value
  sources:
    main:
      url: https://git.example.com/main.git
      packages:
        git: {policy: once}
profiles:
  work: {}
  calm: {}
"""

#: What the commands below plan, set by each test.
_changes: list[Callable[[], Mapping[SettingKey, object]]] = []
#: What they wrote.
_written: list[SettingsWritten] = []


class Write(Command[ShelfSettings]):
    """Write what the test planned, after asking."""

    confirm: Confirm

    def run(self) -> None:
        plan = plugin_of(ShelfSettings).plan_settings(_changes[-1]())
        if self.confirm("Write the shelf's settings?", preview=plan.preview):
            _written.append(plan.write())


class Add(Command[ShelfSettings]):
    """Write what the test planned, without asking."""

    dry_run: DryRun

    def run(self) -> None:
        plan = plugin_of(ShelfSettings).plan_settings(_changes[-1]())
        if self.dry_run.go_ahead(preview=plan.preview):
            _written.append(plan.write())


@pytest.fixture(autouse=True)
def _home(_isolated_config: Path) -> Iterator[None]:
    _isolated_config.write_text(HOME, encoding="utf-8")
    yield
    _changes.clear()
    _written.clear()


def _shelf() -> type:
    return make_plugin("shelf", settings=ShelfSettings, commands=(Write, Add))


def _untaped(changes: Mapping[SettingKey, object], *args: str) -> CliResult:
    _changes.append(lambda: changes)
    get_settings.cache_clear()
    forget_folder_layer()
    root = build_root_app(candidates=[plugin_candidate(_shelf())])
    return invoke_cli(root.meta, list(args))


def _file(path: Path) -> dict[str, object]:
    return read_config_dict(path)


def _outcomes() -> list[tuple[str, str, str]]:
    [written] = _written
    return [(each.key, each.profile, each.action) for each in written.outcomes]  # type: ignore[attr-defined]


# ── where it lands ───────────────────────────────────────────────────────────


def test_a_write_lands_where_config_set_writes(_isolated_config: Path) -> None:
    result = _untaped({SHELF.greeting: "hi"}, "shelf", "write", "--yes")

    assert result.exit_code == 0, result.output
    assert _file(_isolated_config)["shelf"]["greeting"] == "hi"  # type: ignore[index]
    assert _outcomes() == [("shelf.greeting", "default", "updated")]
    assert _written[0].target == "default"


def test_under_profile_it_lands_in_that_profile(_isolated_config: Path) -> None:
    result = _untaped({SHELF.greeting: "hi"}, "--profile", "work", "shelf", "write", "--yes")

    assert result.exit_code == 0, result.output
    data = _file(_isolated_config)
    assert data["profiles"]["work"] == {"shelf": {"greeting": "hi"}}  # type: ignore[index]
    assert data["shelf"]["greeting"] == "hey"  # type: ignore[index]
    assert _written[0].target == "work"


def test_a_home_only_key_lands_in_the_base_under_profile(_isolated_config: Path) -> None:
    package = SHELF.sources["main"].packages["fish"]
    changes = {package: Item(policy="sync"), SHELF.greeting: "hi"}

    result = _untaped(changes, "--profile", "work", "shelf", "write", "--yes")

    assert result.exit_code == 0, result.output
    data = _file(_isolated_config)
    packages = data["shelf"]["sources"]["main"]["packages"]  # type: ignore[index]
    assert packages == {"git": {"policy": "once"}, "fish": {"policy": "sync"}}
    assert data["profiles"]["work"] == {"shelf": {"greeting": "hi"}}  # type: ignore[index]
    assert _outcomes() == [
        ("shelf.sources.main.packages.fish", "default", "updated"),
        ("shelf.greeting", "work", "updated"),
    ]
    assert _written[0].target == "work"


def test_two_profiles_name_no_place_to_write(_isolated_config: Path) -> None:
    before = _isolated_config.read_bytes()

    result = _untaped({SHELF.greeting: "hi"}, "--profile", "work,calm", "shelf", "write", "--yes")

    assert result.exit_code == 2, result.output
    assert "a write needs one" in result.stderr
    assert _isolated_config.read_bytes() == before


# ── what it takes ────────────────────────────────────────────────────────────


def test_a_model_writes_only_the_fields_it_set(_isolated_config: Path) -> None:
    entry = SHELF.sources["main"].packages["git"]

    result = _untaped({entry: Item(enabled=False)}, "shelf", "write", "--yes")

    assert result.exit_code == 0, result.output
    packages = _file(_isolated_config)["shelf"]["sources"]["main"]["packages"]  # type: ignore[index]
    assert packages == {"git": {"enabled": False}}


def test_none_unsets_an_entry_and_a_key_not_set_is_unchanged(_isolated_config: Path) -> None:
    changes = {SHELF.sources["main"].packages["git"]: None, SHELF.retries: None}

    previewed = _untaped(changes, "shelf", "write", "--dry-run")
    assert "unset shelf.sources.main.packages.git in profile default" in previewed.stderr
    assert "shelf.retries is not set in profile default" in previewed.stderr

    result = _untaped(changes, "shelf", "write", "--yes")

    assert result.exit_code == 0, result.output
    shelf = _file(_isolated_config)["shelf"]
    assert "packages" not in shelf["sources"]["main"]  # type: ignore[index]
    assert _outcomes() == [
        ("shelf.sources.main.packages.git", "default", "deleted"),
        ("shelf.retries", "default", "unchanged"),
    ]


@pytest.fixture
def _held() -> Iterator[None]:
    """A run of what the test composes, current for the test: plugin code called directly."""
    with holding_runs():
        yield


@pytest.mark.usefixtures("_held")
def test_another_plugins_key_is_a_type_error() -> None:
    compose(_shelf(), make_plugin("other", settings=OtherSettings))
    handle = plugin_of(ShelfSettings)

    for wrong in (key(OtherSettings).level, "shelf.greeting", SHELF):
        with pytest.raises(TypeError):
            handle.plan_settings({wrong: 1})  # type: ignore[dict-item]


@pytest.mark.usefixtures("_held")
def test_a_model_no_plugin_owns_is_a_type_error() -> None:
    compose(_shelf())

    with pytest.raises(TypeError, match="not a setting of shelf"):
        plugin_of(ShelfSettings).plan_settings({key(OtherSettings).level: 2})


# ── checked before anything is written ───────────────────────────────────────


def test_keys_are_validated_together(_isolated_config: Path) -> None:
    new = SHELF.sources["new"]
    together = {new.url: "https://git.example.com/new.git", new.packages["vim"]: {}}

    result = _untaped(together, "shelf", "write", "--yes")

    assert result.exit_code == 0, result.output
    sources = _file(_isolated_config)["shelf"]["sources"]  # type: ignore[index]
    assert sources["new"] == {"url": "https://git.example.com/new.git", "packages": {"vim": {}}}


def test_one_bad_value_fails_the_plan_and_writes_nothing(_isolated_config: Path) -> None:
    before = _isolated_config.read_bytes()

    result = _untaped({SHELF.greeting: "hi", SHELF.retries: 0}, "shelf", "write", "--yes")

    assert result.exit_code == 1, result.output
    assert "invalid value for 'shelf.retries'" in result.stderr
    assert "Write the shelf's settings?" not in result.stderr  # failed before asking
    assert _isolated_config.read_bytes() == before


def test_an_entry_its_section_refuses_fails_the_plan(_isolated_config: Path) -> None:
    before = _isolated_config.read_bytes()

    result = _untaped({SHELF.sources["new"].packages["vim"]: {}}, "shelf", "write", "--yes")

    assert result.exit_code == 1, result.output
    assert "url" in result.stderr
    assert _isolated_config.read_bytes() == before


# ── preview and write ────────────────────────────────────────────────────────


def test_the_preview_prints_through_confirm_and_dry_run_writes_nothing(
    _isolated_config: Path,
) -> None:
    before = _isolated_config.read_bytes()
    changes = {SHELF.greeting: "hi", SHELF.sources["main"].packages["fish"]: Item(policy="sync")}

    result = _untaped(changes, "shelf", "write", "--dry-run")

    assert result.exit_code == 0, result.output
    assert "set shelf.greeting to hi in profile default" in result.stderr
    assert (
        'set shelf.sources.main.packages.fish to {"policy":"sync"} in profile default'
        in result.stderr
    )
    assert _isolated_config.read_bytes() == before
    assert _written == []


def test_a_dry_run_block_prints_the_preview_and_writes_nothing(_isolated_config: Path) -> None:
    before = _isolated_config.read_bytes()

    result = _untaped({SHELF.greeting: "hi"}, "shelf", "add", "--dry-run")

    assert result.exit_code == 0, result.output
    assert "set shelf.greeting to hi in profile default" in result.stderr
    assert _isolated_config.read_bytes() == before


def test_a_secret_is_masked_in_the_preview_and_written_as_given(_isolated_config: Path) -> None:
    result = _untaped({SHELF.token: "s3cret"}, "shelf", "add")

    assert result.exit_code == 0, result.output
    assert _file(_isolated_config)["shelf"]["token"] == "s3cret"  # type: ignore[index]

    previewed = _untaped({SHELF.token: "other"}, "shelf", "add", "--dry-run")
    assert "set shelf.token to *** in profile default" in previewed.stderr
    assert "other" not in previewed.stderr


def test_comments_are_kept(_isolated_config: Path) -> None:
    result = _untaped({SHELF.greeting: "hi"}, "shelf", "add")

    assert result.exit_code == 0, result.output
    text = _isolated_config.read_text(encoding="utf-8")
    assert "# kept: a comment above the section" in text
    assert "# kept: a comment on a value" in text


@pytest.mark.usefixtures("_held")
def test_the_file_is_validated_again_when_written(_isolated_config: Path) -> None:
    compose(_shelf())
    plan = plugin_of(ShelfSettings).plan_settings(
        {SHELF.sources["main"].packages["vim"]: Item(policy="manual")}
    )
    changed = HOME.replace("      url: https://git.example.com/main.git\n", "")
    _isolated_config.write_text(changed, encoding="utf-8")
    get_settings.cache_clear()

    with pytest.raises(ConfigError, match="url"):
        plan.write()
    assert _isolated_config.read_text(encoding="utf-8") == changed


def test_a_write_hidden_by_a_picked_profile_says_so(
    _isolated_config: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolated_config.write_text(
        HOME.replace("  work: {}", "  work: {shelf: {greeting: yo}}"), encoding="utf-8"
    )
    monkeypatch.setenv("UNTAPED_PROFILE", "work")  # picks what runs read, never where they write

    result = _untaped({SHELF.greeting: "hi"}, "shelf", "add")

    assert result.exit_code == 0, result.output
    assert "this run still reads shelf.greeting from profile work, picked over the base" in (
        result.stderr
    )
    assert _outcomes() == [("shelf.greeting", "default", "updated")]
