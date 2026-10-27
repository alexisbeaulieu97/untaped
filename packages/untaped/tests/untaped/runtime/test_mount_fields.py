"""The mount fills Refresh, Confirm, Parallel and Limit from their flags for ``run`` to read,
and refuses a ``File[M]`` it reads that names nothing."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Annotated

import pytest
from pydantic import BaseModel, Field

from untaped.runtime.bootstrap import build_root_app
from untaped.runtime.registry import PluginCandidate
from untaped.sdk import (
    Cache,
    Command,
    Confirm,
    File,
    Limit,
    Parallel,
    Plugin,
    Positional,
    Refresh,
    Written,
    key,
)
from untaped.sdk.testing import CliInvoker, CliResult, Install, ScriptedPromptBackend

#: A cache the plugin class holds: what Refresh's flags are about.
MOTD = Cache(label="message of the day", max_age=timedelta(hours=1))


class ToolSettings(BaseModel):
    workers: int = Field(3, description="How many workers run at once.")
    spread: int | None = Field(None, description="How many spread at once; unset: twelve.")


class Delete(Command):
    """Delete a thing, asking first."""

    name: Positional[str] = Field(description="The thing to delete.")
    confirm: Confirm

    def run(self) -> str:
        go = self.confirm(f"Delete {self.name}?", preview=lambda: [f"would delete {self.name}"])
        return "deleted" if go else "planned"


class Tidy(Command):
    """Tidy a thing, asking only when there is something to tidy."""

    name: Positional[str] = Field(description="The thing to tidy.")
    confirm: Confirm

    def run(self) -> str:
        return "tidied" if self.confirm(f"Tidy {self.name}?", needed=False) else "planned"


class Sweep(Command):
    """Sweep a thing; ``--check`` previews it, as ``--dry-run`` does."""

    name: Positional[str] = Field(description="The thing to sweep.")
    check: bool = Field(False, description="Preview only.")
    confirm: Confirm

    def run(self) -> str:
        confirm = self.confirm.as_dry_run() if self.check else self.confirm
        go = confirm(f"Sweep {self.name}?", preview=lambda: [f"would sweep {self.name}"])
        return f"{'swept' if go else 'planned'} {self.confirm.dry_run}"


class Fetch(Command):
    """Print what the refresh flags said."""

    refresh: Refresh = Refresh(MOTD)

    def run(self) -> str:
        return repr(self.refresh.directive)


class Work(Command[ToolSettings]):
    """Print how many run at once and how many results at most."""

    parallel: Parallel = Parallel(default=key(ToolSettings).workers, cap=8)
    limit: Limit = Limit()

    def run(self) -> str:
        return f"{self.parallel.value} {self.limit.value}"


class Spread(Command[ToolSettings]):
    """Print how many spread at once: an optional setting, twelve when unset."""

    parallel: Parallel = Parallel(default=key(ToolSettings).spread, unset=12, cap=8)

    def run(self) -> str:
        return str(self.parallel.value)


class Page(Command):
    """Print how many results at most, fifty unless asked."""

    limit: Limit = Limit(default=50)

    def run(self) -> str:
        return str(self.limit.value)


class Suite(BaseModel):
    """A suite of checks."""

    name: str = "suite"


class Check(Command):
    """Read suites; write a report and copies, which need not exist yet."""

    suite: File[Suite] | None = Field(None, description="The suite to read.")
    more: list[File[Suite]] = Field(default_factory=list, description="More suites.")
    report: Annotated[File[Suite] | None, Written] = Field(None, description="Write here.")
    copies: Annotated[list[File[Suite]], Written] = Field(
        default_factory=list, description="Copy here."
    )

    def run(self) -> str:
        return "checked"


class Tool(Plugin):
    """Do things (a 12.0 plugin)."""

    settings = ToolSettings
    commands = (Delete, Tidy, Sweep, Fetch, Work, Spread, Page, Check)
    motd = MOTD


TOOL = PluginCandidate(distribution="untaped-tool", name="tool", target=Tool)


def _run(
    *argv: str, terminal: bool = False, prompt_backend: ScriptedPromptBackend | None = None
) -> CliResult:
    root = build_root_app(candidates=[TOOL])
    return CliInvoker().invoke(
        root.meta, list(argv), terminal=terminal, prompt_backend=prompt_backend
    )


def test_yes_goes_ahead_without_asking_or_previewing() -> None:
    result = _run("tool", "delete", "x", "--yes")
    assert (result.exit_code, result.stdout, result.stderr) == (0, "deleted\n", "")


def test_dry_run_previews_and_returns_false_even_with_yes() -> None:
    result = _run("tool", "delete", "x", "--dry-run", "-y")
    assert result.exit_code == 0
    assert result.stdout == "planned\n"
    assert "would delete x" in result.stderr


def test_without_a_terminal_it_refuses_naming_the_command_and_question() -> None:
    result = _run("tool", "delete", "x")
    assert result.exit_code == 2
    assert 'untaped tool delete requires --yes when not interactive: it asks "Delete x?"' in (
        result.stderr
    )
    assert "pass --yes to go ahead, or --dry-run to preview" in result.stderr
    assert "would delete x" not in result.stderr


def test_as_dry_run_previews_without_asking_and_leaves_the_field_as_filled() -> None:
    checked = _run("tool", "sweep", "x", "--check")
    assert (checked.exit_code, checked.stdout) == (0, "planned False\n")
    assert "would sweep x" in checked.stderr
    assert _run("tool", "sweep", "x", "--yes").stdout == "swept False\n"


def test_a_question_not_needed_goes_ahead_without_a_terminal() -> None:
    assert _run("tool", "tidy", "x").stdout == "tidied\n"


def test_at_a_terminal_it_previews_asks_and_a_no_cancels() -> None:
    yes = ScriptedPromptBackend(confirms=[True])
    asked = _run("tool", "delete", "x", terminal=True, prompt_backend=yes)
    assert (asked.exit_code, asked.stdout) == (0, "deleted\n")
    assert "would delete x" in asked.stderr
    assert yes.calls == [("confirm", "Delete x?")]

    declined = _run(
        "tool", "delete", "x", terminal=True, prompt_backend=ScriptedPromptBackend(confirms=[False])
    )
    assert declined.exit_code == 1
    assert "cancelled; no changes made" in declined.stderr


def test_refresh_and_cached_set_the_directive() -> None:
    assert _run("tool", "fetch").stdout == "None\n"
    assert _run("tool", "fetch", "--refresh").stdout == "True\n"
    assert _run("tool", "fetch", "--cached").stdout == "False\n"


def test_refresh_help_names_the_cache_and_its_setting() -> None:
    shown = " ".join(_run("tool", "fetch", "--help").stdout.replace("│", " ").split())
    assert "--refresh checks upstream for message of the day now" in shown
    assert "older than tool.caches.motd.max_age_seconds (1 h)." in shown


def test_parallel_comes_from_the_flag_or_its_setting_and_is_capped() -> None:
    assert _run("tool", "work").stdout == "3 None\n"
    assert _run("tool", "work", "-j", "5", "--limit", "2").stdout == "5 2\n"
    capped = _run("tool", "work", "-j", "20")
    assert capped.stdout == "8 None\n"
    assert "--parallel 20 clamped to 8" in capped.stderr


def test_a_setting_above_the_cap_is_clamped_naming_the_setting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("UNTAPED_TOOL__WORKERS", "20")
    capped = _run("tool", "work")
    assert capped.stdout == "8 None\n"
    assert "tool.workers 20 clamped to 8" in capped.stderr


def test_a_setting_is_not_read_when_j_is_given(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UNTAPED_TOOL__WORKERS", "0")
    assert _run("tool", "work", "-j", "2").stdout == "2 None\n"
    refused = _run("tool", "work")
    assert refused.exit_code != 0
    assert "setting tool.workers is 0; it must be a number of at least 1" in refused.stderr


def test_an_unmounted_command_reads_its_setting_afresh(monkeypatch: pytest.MonkeyPatch) -> None:
    from untaped.runtime.composition import PluginEntry
    from untaped.runtime.mount import compose_plugins
    from untaped.runtime.run import build_run
    from untaped.sdk._run import run_scope

    mount = compose_plugins([PluginEntry("tool", f"{__name__}:Tool", Tool)])
    seen = []
    for workers in ("3", "5"):
        monkeypatch.setenv("UNTAPED_TOOL__WORKERS", workers)
        with run_scope(build_run(mount.declaration, mount.settings)):
            seen.append(Work().parallel.value)
    assert seen == [3, 5]


def test_an_unset_setting_takes_unset_capped_without_a_warning() -> None:
    unset = _run("tool", "spread")
    assert (unset.exit_code, unset.stdout, unset.stderr) == (0, "8\n", "")


def test_a_set_setting_or_j_above_the_cap_still_warns(monkeypatch: pytest.MonkeyPatch) -> None:
    assert "--parallel 20 clamped to 8" in _run("tool", "spread", "-j", "20").stderr
    monkeypatch.setenv("UNTAPED_TOOL__SPREAD", "20")
    capped = _run("tool", "spread")
    assert capped.stdout == "8\n"
    assert "tool.spread 20 clamped to 8" in capped.stderr
    monkeypatch.setenv("UNTAPED_TOOL__SPREAD", "5")
    assert _run("tool", "spread").stdout == "5\n"


def test_an_unfilled_block_reads_its_default_under_a_run(install: Callable[..., Install]) -> None:
    # what code outside a command field reads: the same number, no -j
    with install(Tool).running():
        assert (Spread().parallel.value, Work().parallel.value) == (8, 3)
        # with no cap, an unset setting is unset itself
        assert Parallel(default=key(ToolSettings).spread, unset=12).value == 12


def test_parallel_and_limit_below_one_are_usage_errors() -> None:
    assert _run("tool", "work", "-j", "0").exit_code == 2
    limited = _run("tool", "work", "--limit", "0")
    assert limited.exit_code == 2
    assert "--limit: must be at least 1" in limited.stderr


def test_a_limit_a_command_sets_is_its_default_in_help_and_on_the_surface() -> None:
    from untaped.core.surface import plugin_lines

    assert _run("tool", "page").stdout.strip() == "50"
    assert _run("tool", "page", "--limit", "3").stdout.strip() == "3"
    assert "[default: 50]" in _run("tool", "page", "--help").stdout
    lines = {line.text for line in plugin_lines(TOOL)}
    assert "cli untaped tool page --limit VALUE default=50" in lines
    assert "cli untaped tool work --limit VALUE default=unset" in lines


@pytest.mark.parametrize(("flag", "repeated"), [("--suite", False), ("--more", True)])
def test_a_file_read_must_exist(tmp_path: Path, flag: str, repeated: bool) -> None:
    present, missing = tmp_path / "a.yml", tmp_path / "missing.yml"
    present.write_text("name: a\n", encoding="utf-8")
    given = [flag, str(present), flag, str(missing)] if repeated else [flag, str(missing)]
    result = _run("tool", "check", *given)
    assert result.exit_code == 2
    assert f"error: {flag}: file not found: {missing}" in result.stderr
    found = _run("tool", "check", flag, str(tmp_path))  # a directory is searched
    assert (found.exit_code, found.stdout) == (0, "checked\n")


def test_a_written_file_need_not_exist(tmp_path: Path) -> None:
    missing = str(tmp_path / "missing.yml")
    result = _run("tool", "check", "--report", missing, "--copies", missing)
    assert (result.exit_code, result.stdout) == (0, "checked\n")
