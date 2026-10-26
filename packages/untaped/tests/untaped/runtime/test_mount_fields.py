"""The mount fills Refresh, Confirm, Parallel and Limit from their flags for ``run`` to read."""

from __future__ import annotations

import pytest
from pydantic import BaseModel, Field

from untaped.runtime.bootstrap import build_root_app
from untaped.runtime.registry import PluginCandidate
from untaped.sdk import Command, Confirm, Limit, Parallel, Plugin, Positional, Refresh, key
from untaped.sdk.testing import CliInvoker, CliResult, ScriptedPromptBackend

#: A stand-in for a cache the plugin class holds: what Refresh's flags are about.
MOTD = object()


class ToolSettings(BaseModel):
    workers: int = Field(3, description="How many workers run at once.")


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


class Tool(Plugin):
    """Do things (a 12.0 plugin)."""

    settings = ToolSettings
    commands = (Delete, Tidy, Sweep, Fetch, Work)
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


def test_parallel_and_limit_below_one_are_usage_errors() -> None:
    assert _run("tool", "work", "-j", "0").exit_code == 2
    limited = _run("tool", "work", "--limit", "0")
    assert limited.exit_code == 2
    assert "--limit: must be at least 1" in limited.stderr
