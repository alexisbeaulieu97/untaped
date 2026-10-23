"""The mount fills Refresh, Confirm, Parallel and Limit from their flags for ``run`` to read."""

from __future__ import annotations

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
    commands = (Delete, Fetch, Work)
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


def test_parallel_and_limit_below_one_are_usage_errors() -> None:
    assert _run("tool", "work", "-j", "0").exit_code == 2
    limited = _run("tool", "work", "--limit", "0")
    assert limited.exit_code == 2
    assert "--limit: must be at least 1" in limited.stderr
