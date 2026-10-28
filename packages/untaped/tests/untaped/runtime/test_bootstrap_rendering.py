"""One stderr rendering per run: the format and theme the root fixes before anything is emitted.

For a declared command the root decides them from the declaration, as the
command's parse decides its ``--format`` (the flag, then ``UNTAPED_FORMAT``
for a ``table`` default, then ``untaped.ui.format``), so a quarantine warning,
a dispatch-time notice and the command's own diagnostics share one format.
A line naming no declared command takes the flag, then ``UNTAPED_FORMAT``,
then ``untaped.ui.format``; a root option's error, read before any of them,
only the first two. An 11.x seam command keeps the two phases until it moves.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import pytest
from cyclopts import App
from pydantic import BaseModel

from untaped.runtime import bootstrap
from untaped.runtime.registry import PluginCandidate, PluginSpec
from untaped.sdk import Command, Output, Plugin, deprecated
from untaped.sdk._cli import FormatOption, create_app, emit
from untaped.sdk._run import Rendering, current_run
from untaped.sdk.storage import _refs as refs
from untaped.sdk.testing import CliInvoker

pytestmark = pytest.mark.usefixtures("fresh_composition")


class Chord(BaseModel):
    """One chord."""

    name: str


class Strum(Command):
    """Warn, then print the chord as YAML unless asked otherwise."""

    output: Output[Chord] = Output(format="yaml")

    def run(self) -> Chord:
        self.ctx.diag.warning("strummed")
        return Chord(name="c")


class Chords(Command):
    """Warn, then list the chords as a table unless asked otherwise."""

    output: Output[Chord]

    def run(self) -> Iterator[Chord]:
        self.ctx.diag.warning("listed")
        yield Chord(name="c")


class Old(Command):
    """List the chords the old way."""

    stability = deprecated(removed_in=99, replacement=Chords)
    output: Output[Chord]

    def run(self) -> Iterator[Chord]:
        self.ctx.diag.warning("listed")
        yield Chord(name="c")


class Tune(Command):
    """Repair the chords, under any UNTAPED_FORMAT."""

    repairs = True
    output: Output[Chord]

    def run(self) -> Iterator[Chord]:
        self.ctx.diag.warning("tuned")
        yield Chord(name="c")


class ChimeSettings(BaseModel):
    """Nothing to configure."""


class Chime(Plugin):
    """Chords (a 12.0 plugin)."""

    settings = ChimeSettings
    commands = (Strum, Chords, Old, Tune)


#: The rendering each run of ``bell ring`` saw.
RANG: list[Rendering | None] = []


def _old_app() -> App:
    app = create_app(name="bell", help="An 11.x plugin.")

    @app.command(name="ring")
    def ring(*, fmt: FormatOption = "table") -> None:
        """Warn, then list one row."""
        from untaped.runtime.sink import Diagnostics

        RANG.append(current_run().rendering)
        Diagnostics().warning("rang")
        emit([{"name": "c"}], fmt=fmt)

    return app


CHIME = PluginCandidate(distribution="untaped-chime", name="chime", target=Chime)
BELL = PluginCandidate(
    distribution="untaped-bell", name="bell", target=PluginSpec(name="bell", app_factory=_old_app)
)
BROKEN = PluginCandidate(distribution="broken-dist", name="broken", target="no_such_mod:SPEC")


def _config(cfg: Path, ui: str) -> None:
    cfg.write_text(f"format_version: 2\nuntaped:\n  ui:\n{ui}")


def _formats(stderr: str) -> list[str]:
    """Each stderr line's format: ``json`` or ``text`` (a hint line belongs to its message)."""
    found: list[str] = []
    for line in stderr.splitlines():
        if line.startswith("hint: "):
            continue
        try:
            json.loads(line)
        except ValueError:
            found.append("text")
        else:
            found.append("json")
    return found


def _run(
    argv: list[str],
    *,
    kept: bool,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    candidates: tuple[PluginCandidate, ...] = (CHIME, BROKEN),
) -> tuple[object, str, str]:
    """Run ``argv`` from the start, read with or without a kept declaration."""
    declaration = bootstrap.compose_root(candidates=candidates).mount.declaration if kept else None
    bootstrap._clear_for_tests()
    monkeypatch.setattr(bootstrap, "kept_declaration", lambda _candidates: declaration)
    with pytest.raises(SystemExit) as exit_info:
        bootstrap.run_root(argv, candidates=candidates)
    captured = capsys.readouterr()
    return exit_info.value.code or 0, captured.out, captured.err


both = pytest.mark.parametrize("kept", [True, False], ids=["kept", "cold"])


@pytest.mark.usefixtures("_isolated_config")
@both
@pytest.mark.parametrize("argv", [["profile", "show"], ["chime", "strum"]])
def test_a_yaml_default_command_keeps_text_stderr_under_untaped_format(
    argv: list[str],
    kept: bool,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("UNTAPED_FORMAT", "json")  # a default for table commands only

    code, out, err = _run(argv, kept=kept, monkeypatch=monkeypatch, capsys=capsys)

    assert code == 0, err
    assert out.startswith("name: ")
    assert "quarantined" in err  # the first line, from the root's composition
    assert set(_formats(err)) == {"text"}, err


@both
@pytest.mark.parametrize("argv", [["chime", "chords"], ["chime", "old"]])
def test_the_ui_format_setting_reaches_every_line_of_a_table_command(
    argv: list[str],
    kept: bool,
    _isolated_config: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _config(_isolated_config, "    format: json\n")

    code, out, err = _run(argv, kept=kept, monkeypatch=monkeypatch, capsys=capsys)

    assert code == 0, err
    assert json.loads(out) == [{"name": "c"}]
    lines = [json.loads(line) for line in err.splitlines()]
    assert "quarantined" in lines[0]["message"]
    assert lines[-1]["message"] == "listed"
    if argv[-1] == "old":  # the deprecated command's notice, emitted at dispatch
        assert any("is deprecated" in line["message"] for line in lines[1:-1])


@pytest.mark.usefixtures("home_run")  # the test reads the references' lock path
@both
def test_the_busy_lock_warning_follows_the_ui_format_setting(
    kept: bool,
    _isolated_config: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from filelock import FileLock

    assert _run(["setup", "register"], kept=False, monkeypatch=monkeypatch, capsys=capsys)[0] == 0
    _config(_isolated_config, "    format: json\n")
    monkeypatch.setattr(refs.REFS, "wait", timedelta(seconds=0.01))
    with FileLock(str(refs.REFS.lock_path())):  # chime is new to the reference: it rewrites
        code, _, err = _run(
            ["chime", "chords"],
            kept=kept,
            monkeypatch=monkeypatch,
            capsys=capsys,
            candidates=(CHIME,),
        )

    assert code == 0, err
    lines = [json.loads(line) for line in err.splitlines()]
    assert "could not record this install's plugins" in lines[0]["message"]
    assert lines[-1]["message"] == "listed"


@pytest.mark.usefixtures("_isolated_config")
@both
@pytest.mark.parametrize(
    ("argv", "env", "expected"),
    [
        (["chime", "chords", "-f", "table"], "json", "text"),
        (["chime", "chords", "--format=yaml"], "table", "json"),
        (["chime", "strum", "-fjson"], "table", "json"),
        (["chime", "strum", "-q", "--format", "pipe"], "table", "json"),
    ],
)
def test_the_command_s_format_flag_wins(
    argv: list[str],
    env: str,
    expected: str,
    kept: bool,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("UNTAPED_FORMAT", env)

    code, _, err = _run(argv, kept=kept, monkeypatch=monkeypatch, capsys=capsys)

    assert code == 0, err
    assert set(_formats(err)) == {expected}, err


@pytest.mark.usefixtures("_isolated_config")
@both
def test_a_repairs_command_runs_under_a_bad_untaped_format(
    kept: bool, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("UNTAPED_FORMAT", "bogus")

    repaired = _run(["chime", "tune"], kept=kept, monkeypatch=monkeypatch, capsys=capsys)
    helped = _run(["chime", "chords", "--help"], kept=kept, monkeypatch=monkeypatch, capsys=capsys)
    refused = _run(["chime", "chords"], kept=kept, monkeypatch=monkeypatch, capsys=capsys)

    assert repaired[0] == 0, repaired[2]
    assert "warning: tuned" in repaired[2]
    assert helped[0] == 0, helped[2]
    assert refused[0] == 2
    assert "error: UNTAPED_FORMAT must be one of" in refused[2]
    assert "listed" not in refused[2]


@both
def test_the_user_s_theme_renders_every_text_line(
    kept: bool,
    _isolated_config: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _config(_isolated_config, "    symbols:\n      warning: '!'\n")

    code, _, err = _run(["chime", "old"], kept=kept, monkeypatch=monkeypatch, capsys=capsys)

    assert code == 0, err
    warnings = [line for line in err.splitlines() if not line.startswith("hint: ")]
    assert len(warnings) == 3  # quarantined, deprecated, listed
    assert all(line.startswith("! ") for line in warnings), err


@pytest.mark.usefixtures("_isolated_config")
@both
def test_an_11x_command_keeps_its_two_phases(
    kept: bool, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("UNTAPED_FORMAT", "json")
    RANG.clear()

    code, out, err = _run(
        ["bell", "ring"], kept=kept, monkeypatch=monkeypatch, capsys=capsys, candidates=(BELL,)
    )

    assert code == 0, err
    assert json.loads(out) == [{"name": "c"}]
    assert json.loads(err)["message"] == "rang"
    assert RANG == [None]  # its parse decides how stderr renders, as before


@pytest.mark.usefixtures("_isolated_config")
def test_the_decision_reads_the_declaration_without_importing_the_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("UNTAPED_FORMAT", "pipe")
    declaration = bootstrap.compose_root(candidates=[CHIME]).mount.declaration
    chime = declaration.plugin("chime")
    absent = "untaped_absent_chime.commands"
    moved = declaration.model_copy(
        update={
            "plugins": tuple(
                plugin.model_copy(
                    update={
                        "commands": tuple(
                            command.model_copy(update={"target": f"{absent}:{command.name}"})
                            for command in plugin.commands
                        )
                    }
                )
                if plugin is chime
                else plugin
                for plugin in declaration.plugins
            )
        }
    )

    with bootstrap._started(["chime", "chords"], moved) as (run, line):
        assert current_run() is run

    assert line.placed
    assert run.rendering == Rendering(format="pipe", source="UNTAPED_FORMAT", theme=None)
    assert absent not in sys.modules


@both
@pytest.mark.parametrize(
    "argv",
    [
        ["--help"],
        ["--version"],
        [],
        ["nope"],
        ["chime"],
        ["chime", "nope"],
        ["chime", "--help"],
    ],
    ids=["root-help", "version", "bare", "unknown", "group", "unknown-in-group", "group-help"],
)
def test_a_line_naming_no_command_follows_the_ui_format_setting(
    argv: list[str],
    kept: bool,
    _isolated_config: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _config(_isolated_config, "    format: json\n")

    _, _, err = _run(argv, kept=kept, monkeypatch=monkeypatch, capsys=capsys)

    lines = [json.loads(line) for line in err.splitlines()]  # every line, the error's too
    assert "quarantined" in lines[0]["message"]
    if "nope" in argv:
        assert lines[-1]["category"] == "usage"


@both
@pytest.mark.parametrize(
    ("argv", "env"),
    [(["nope", "--format", "table"], "json"), (["nope"], "bogus")],
    ids=["flag-wins", "invalid-env-ignored"],
)
def test_a_line_naming_no_command_takes_the_flag_then_untaped_format(
    argv: list[str],
    env: str,
    kept: bool,
    _isolated_config: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _config(_isolated_config, "    format: yaml\n")
    monkeypatch.setenv("UNTAPED_FORMAT", env)

    code, _, err = _run(argv, kept=kept, monkeypatch=monkeypatch, capsys=capsys)

    assert code == 2
    expected = "text" if "--format" in argv else "json"  # bogus falls through to the setting
    assert set(_formats(err)) == {expected}, err
    assert "Unknown command" in err


@both
@pytest.mark.parametrize(
    "root", [["--profile", "a,,b"], ["-v", "-q"]], ids=["malformed-profile", "verbose-quiet"]
)
def test_a_root_option_error_ignores_the_ui_format_setting(
    root: list[str],
    kept: bool,
    _isolated_config: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _config(_isolated_config, "    format: json\n")

    code, _, err = _run(
        ["chime", "chords", *root], kept=kept, monkeypatch=monkeypatch, capsys=capsys
    )

    assert code == 2
    assert err.startswith("error: "), err  # before the composition, so no quarantine line
    assert set(_formats(err)) == {"text"}, err


@both
def test_a_trailing_profile_picks_the_ui_format_and_theme(
    kept: bool,
    _isolated_config: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _isolated_config.write_text(
        "format_version: 2\n"
        "profiles:\n"
        "  loud:\n    untaped:\n      ui:\n        format: json\n"
        "  bang:\n    untaped:\n      ui:\n        symbols:\n          warning: '!'\n"
    )

    loud = _run(
        ["chime", "chords", "--profile", "loud"], kept=kept, monkeypatch=monkeypatch, capsys=capsys
    )
    bang = _run(
        ["chime", "old", "--profile", "bang"], kept=kept, monkeypatch=monkeypatch, capsys=capsys
    )

    assert loud[0] == 0, loud[2]
    assert json.loads(loud[1]) == [{"name": "c"}]
    assert set(_formats(loud[2])) == {"json"}, loud[2]
    assert bang[0] == 0, bang[2]
    warnings = [line for line in bang[2].splitlines() if not line.startswith("hint: ")]
    assert len(warnings) == 3  # quarantined, deprecated, listed
    assert all(line.startswith("! ") for line in warnings), bang[2]


@both
def test_a_broken_ui_section_renders_text_with_the_default_theme(
    kept: bool,
    _isolated_config: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _config(_isolated_config, "    format: nonsense\n    symbols:\n      warning: '!'\n")

    code, out, err = _run(["chime", "chords"], kept=kept, monkeypatch=monkeypatch, capsys=capsys)

    assert code == 0, err
    assert "name" in out and not out.startswith("[")  # the table, not the setting's choice
    warnings = [line for line in err.splitlines() if not line.startswith("hint: ")]
    assert warnings and all(line.startswith("warning: ") for line in warnings), err


@both
@pytest.mark.parametrize(
    ("argv", "ui", "forced"),
    [(["chime", "chords"], "    format: json\n", "text"), (["chime", "strum"], "", "json")],
)
def test_untaped_diagnostics_overrides_the_fixed_rendering(
    argv: list[str],
    ui: str,
    forced: str,
    kept: bool,
    _isolated_config: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _config(_isolated_config, ui or "    {}\n")
    monkeypatch.setenv("UNTAPED_DIAGNOSTICS", forced)

    code, out, err = _run(argv, kept=kept, monkeypatch=monkeypatch, capsys=capsys)

    assert code == 0, err
    assert out.startswith("[" if ui else "name: ")  # stdout keeps the format chosen
    assert set(_formats(err)) == {forced}, err


def test_a_fixed_rendering_is_fixed_once_and_notes_change_nothing(
    _isolated_config: Path,
) -> None:
    from untaped.runtime.theme import BUILTIN_THEMES
    from untaped.sdk._cli import _note_theme, note_requested_format

    _config(_isolated_config, "    format: json\n")
    declaration = bootstrap.compose_root(candidates=[CHIME]).mount.declaration

    with bootstrap._started(["chime", "chords"], declaration) as (run, _):
        fixed = run.policy
        note_requested_format(["--format", "table"])
        _note_theme(BUILTIN_THEMES["default"])  # the seam's phase 2
        assert run.policy == fixed
        assert (fixed.format, fixed.theme) == ("json", None)
        with pytest.raises(RuntimeError, match="fixed once"):
            run.render_with(Rendering())

    assert run.rendering == Rendering(format="json", source="untaped.ui.format", theme=None)


@pytest.mark.usefixtures("_isolated_config")
def test_the_root_driven_directly_tells_an_unknown_command_from_an_11x_one(
    _isolated_config: Path,
) -> None:
    _config(_isolated_config, "    format: json\n")
    root = bootstrap.build_root_app(candidates=[CHIME, BELL]).meta

    unknown = CliInvoker().invoke(root, ["nope"])
    RANG.clear()
    rang = CliInvoker().invoke(root, ["bell", "ring"])

    assert unknown.exit_code == 2
    assert json.loads(unknown.stderr)["category"] == "usage"
    assert rang.exit_code == 0, rang.stderr
    assert RANG == [None]  # the root left the 11.x command's rendering to its parse
