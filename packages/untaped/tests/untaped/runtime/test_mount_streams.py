"""What the mount does with streams: rows printed as ``run`` yields them, ``-`` read from stdin,
and a ``bool | None`` flag said either way."""

from __future__ import annotations

import json
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml
from pydantic import BaseModel, Field

from untaped.runtime.bootstrap import build_root_app
from untaped.runtime.registry import PluginCandidate
from untaped.sdk import (
    Command,
    Output,
    PathOrStdin,
    Plugin,
    Positional,
    Record,
    TextOrStdin,
    UntapedError,
    read_stdin_text,
)
from untaped.sdk.testing import CliInvoker, CliResult


class Line(Record):
    """One line of a log."""

    text: str
    n: int


def _print_line(row: Line | list[Line]) -> None:
    for each in [row] if isinstance(row, Line) else row:
        print(f"> {each.text}")


#: What stdout held each time ``run`` went on after a row.
PRINTED: list[str] = []


def _went_on() -> None:
    PRINTED.append(sys.stdout.getvalue())  # type: ignore[attr-defined] - the captured stream


class Follow(Command):
    """Print each line as it comes; --fail stops after the first."""

    lines: int = Field(2, description="How many lines.")
    fail: bool = Field(False, description="Fail after the first line.")
    prefix: str = Field("line", description="What each line's text starts with.")
    output: Output[Line] = Output(stream=True, render={"quoted": _print_line})

    def run(self) -> Iterator[Line]:
        for n in range(self.lines):
            yield Line(text=f"{self.prefix}-{n}", n=n)
            _went_on()
            if self.fail:
                raise UntapedError("the log went away")


class Batch(Command):
    """Print every line once the run ends."""

    output: Output[Line]

    def run(self) -> Iterator[Line]:
        yield Line(text="line-0", n=0)
        _went_on()


class Cat(Command):
    """Print the files' text; - reads stdin."""

    files: Positional[list[PathOrStdin]] = Field(description="Files, or - for stdin.")

    def run(self) -> str:
        return "".join(
            read_stdin_text() if file == Path("-") else file.read_text() for file in self.files
        )


class Echo(Command):
    """Print the text; - reads it from stdin."""

    text: Positional[TextOrStdin] = Field(description="The text, or - for stdin.")

    def run(self) -> str:
        return self.text


class Toggle(Command):
    """Print what --color said: true, false or unset."""

    color: bool | None = Field(None, description="Colour the output (default: when a TTY).")

    def run(self) -> str:
        return str(self.color)


class ToolSettings(BaseModel):
    """Nothing to configure."""


class Tool(Plugin):
    """Do things (a 12.0 plugin)."""

    settings = ToolSettings
    commands = (Follow, Batch, Cat, Echo, Toggle)


TOOL = PluginCandidate(distribution="untaped-tool", name="tool", target=Tool)


def _run(*argv: str, input: str | None = None, interactive: bool = False) -> CliResult:
    root = build_root_app(candidates=[TOOL])
    return CliInvoker().invoke(root.meta, list(argv), input=input, interactive=interactive)


# ---- Output(stream=True) ------------------------------------------------------


@pytest.fixture(autouse=True)
def _printed() -> Iterator[None]:
    PRINTED.clear()
    yield


def test_a_stream_prints_each_row_before_run_goes_on() -> None:
    streamed = _run("tool", "follow", "--lines", "1", "-f", "json")
    batched = _run("tool", "batch", "-f", "json")

    assert PRINTED == ['{"text": "line-0", "n": 0}\n', ""]
    assert batched.stdout == '[{"text": "line-0", "n": 0}]\n'
    assert streamed.stdout == '{"text": "line-0", "n": 0}\n'


def test_a_failed_stream_keeps_what_it_printed() -> None:
    result = _run("tool", "follow", "--fail", "-f", "json")
    assert result.exit_code == 1
    assert result.stdout == '{"text": "line-0", "n": 0}\n'
    assert "the log went away" in result.stderr


@pytest.mark.parametrize(
    ("fmt", "expected"),
    [
        ("json", '{"text": "line-0", "n": 0}\n{"text": "line-1", "n": 1}\n'),
        ("raw", "line-0\nline-1\n"),
        ("quoted", "> line-0\n> line-1\n"),
    ],
)
def test_a_stream_prints_one_line_per_row(fmt: str, expected: str) -> None:
    result = _run("tool", "follow", "-f", fmt)
    assert result.exit_code == 0, result.output
    assert result.stdout == expected


def test_a_streamed_yaml_is_one_document_per_row() -> None:
    result = _run("tool", "follow", "-f", "yaml")
    assert list(yaml.safe_load_all(result.stdout)) == [
        {"text": "line-0", "n": 0},
        {"text": "line-1", "n": 1},
    ]


def test_a_streamed_pipe_is_one_envelope_per_row() -> None:
    result = _run("tool", "follow", "-f", "pipe")
    records = [json.loads(line)["record"] for line in result.stdout.splitlines()]
    assert records == [{"text": "line-0", "n": 0}, {"text": "line-1", "n": 1}]


def test_a_streamed_table_prints_its_header_once() -> None:
    result = _run("tool", "follow", "--lines", "3")
    assert result.stdout.splitlines() == ["text    n", "line-0  0", "line-1  1", "line-2  2"]


def test_a_streamed_table_pads_wide_characters_by_the_cells_they_take() -> None:
    result = _run("tool", "follow", "--prefix", "行")
    assert result.stdout.splitlines() == ["text  n", "行-0  0", "行-1  1"]


def test_a_stream_with_no_row_prints_nothing() -> None:
    result = _run("tool", "follow", "--lines", "0", "-f", "json")
    assert (result.exit_code, result.stdout) == (0, "")


# ---- - reads stdin ------------------------------------------------------------


def test_a_dash_among_paths_is_left_for_the_command_to_read(tmp_path: Path) -> None:
    file = tmp_path / "a.txt"
    file.write_text("from a file\n")

    result = _run("tool", "cat", str(file), "-", input="from stdin\n")

    assert result.exit_code == 0, result.output
    assert result.stdout == "from a file\nfrom stdin\n"


def test_a_dash_for_text_is_the_text_piped() -> None:
    result = _run("tool", "echo", "-", input="piped")
    assert (result.exit_code, result.stdout) == (0, "piped\n")


@pytest.mark.parametrize("command", [["cat", "-"], ["echo", "-"]])
def test_a_dash_from_a_terminal_is_refused_before_waiting(command: list[str]) -> None:
    result = _run("tool", *command, interactive=True)
    assert result.exit_code == 2
    assert "- reads stdin; pipe it" in result.stderr


# ---- bool | None --------------------------------------------------------------


@pytest.mark.parametrize(
    ("flags", "said"), [([], "None"), (["--color"], "True"), (["--no-color"], "False")]
)
def test_an_optional_flag_is_said_either_way_or_left_unset(flags: list[str], said: str) -> None:
    result = _run("tool", "toggle", *flags)
    assert (result.exit_code, result.stdout) == (0, f"{said}\n")
