"""Tests for the root ``untaped docs`` command: every page an address names, and search.

A fake 12.0 plugin (``acme``) carries what the pages read: a group, a
command reading a file and one writing a starter, rows it prints, an options
bundle and an enum with docstrings (its guides), settings, an experimental
command and an extension point a provider (``store``) fills. Each page is
compared with a golden page in ``snapshots/docs/<case>.md``, whose second
line (the version and environment) is normalised; run with
``UNTAPED_UPDATE_SNAPSHOTS=1`` to rewrite them.
"""

from __future__ import annotations

import enum
import os
import re
import sys
from abc import abstractmethod
from collections.abc import Callable, Iterator
from importlib import metadata
from pathlib import Path
from typing import Annotated, Literal

import pytest
from pydantic import BaseModel, Field

from untaped.core.docs._docstring import _fenced
from untaped.sdk import (
    Column,
    Command,
    Confirm,
    File,
    Group,
    Key,
    Output,
    Plugin,
    Positional,
    Written,
    deprecated,
    experimental,
)
from untaped.sdk._home import config_path
from untaped.sdk.contracts import Contract, Provider
from untaped.sdk.testing import Install
from untaped.sdk.words import shown_path

pytestmark = pytest.mark.usefixtures("_isolated_config")

Installer = Callable[..., Install]

SNAPSHOTS = Path(__file__).parents[2] / "snapshots" / "docs"
#: Read at import: the hermetic test environment clears ``UNTAPED_*`` variables.
_UPDATE = bool(os.environ.get("UNTAPED_UPDATE_SNAPSHOTS"))


class AcmeSettings(BaseModel):
    """Settings of the fake plugin (section ``acme``)."""

    widgets_dir: Path = Field(Path("~/.acme/widgets"), description="Where widgets live.")
    colour: str = Field("blue", description="The colour of a new widget.")


class Size(BaseModel):
    """How big a widget is."""

    width: int = Field(1, description="Across, in slots.")
    height: int = Field(1, description="Up, in slots.")
    depth: int = Field(0, description="Back, in slots; 0 is flat.")


class Widget(BaseModel):
    """One widget on a rack."""

    name: Annotated[str, Key, Column] = Field(description="Its name, unique on the rack.")
    size: Size = Field(default_factory=Size, description="How much room it takes.")
    tags: list[str] = Field(default_factory=list, description="Labels to find it by.")


class Plan(BaseModel):
    """A plan of widgets to add to one rack.

    Each widget listed is added unless one by its name is there.
    """

    rack: str = Field(description="The rack the widgets go on.")
    widgets: list[Widget] = Field(default_factory=list, description="The widgets to add.")


class Selection(BaseModel):
    """Read before picking widgets: every one, or those named.

    A name matches in any case; `--every` wins over names.
    """

    every: bool = Field(False, description="Every widget on the rack.")
    names: list[str] = Field(default_factory=list, description="The widgets named.")


class Finish(enum.Enum):
    """Read before choosing a finish: what each one does to a widget.

    A gloss finish cannot be undone.
    """

    MATTE = "matte"
    GLOSS = "gloss"


class Rack(Contract):
    """Where the widgets you can name in acme commands come from.

    Fill it with a provider that lists the widgets it holds; acme asks every
    provider and merges what they answer.
    """

    @abstractmethod
    def widgets(self) -> list[Widget]:
        """Every widget the provider holds."""

    def find(self, name: str) -> Widget | None:
        """The widget called ``name``, if the provider holds one."""
        return next((each for each in self.widgets() if each.name == name), None)

    def placed(self, plan: Plan) -> list[Widget]:
        """The widgets ``plan`` adds that the provider does not hold yet."""
        held = {each.name for each in self.widgets()}
        return [each for each in plan.widgets if each.name not in held]


class Store(Rack, Provider):
    """The widgets the store has in stock."""

    def widgets(self) -> list[Widget]:
        return [Widget(name="bolt")]


class Show(Command[AcmeSettings]):
    """List the widgets picked.

    Examples
    --------
    Every widget, as JSON:

    ```console
    $ untaped acme widgets show --every -f json
    ```
    """

    selection: Selection
    limit: int = Field(10, description="Show at most this many.")
    output: Output[Widget]

    def run(self) -> Iterator[Widget]:
        yield from ()


class Add(Command[AcmeSettings]):
    """Add the widgets of plans.

    A widget already there is reported ``unchanged``.
    """

    plans: Positional[list[File[Plan]]] = Field(
        default_factory=list, description="The plans to add."
    )
    finish: Finish = Field(Finish.MATTE, description="The finish each widget gets.")
    rack: Literal["default", "spare"] = Field("default", description="The rack to add to.")
    output: Output[Widget]
    confirm: Confirm

    def run(self) -> Iterator[Widget]:
        yield from ()


class Start(Command[AcmeSettings]):
    """Write a starter plan."""

    out: Annotated[File[Plan], Written] = Field(description="Where to write it.")

    def run(self) -> None:
        return None


class Drop(Command[AcmeSettings]):
    """Drop a widget for good."""

    stability = deprecated(removed_in=99, replacement=Add)

    def run(self) -> None:
        return None


class Widgets(Group):
    """Widgets one by one."""

    commands = (Show, Add, Start, Drop)


class Beta(Command[AcmeSettings]):
    """Try the next thing."""

    stability = experimental

    def run(self) -> str:
        return "beta"


class Acme(Plugin):
    """Manage widgets on racks.

    A rack's widgets come from the plugins that fill its extension point.
    """

    settings = AcmeSettings
    commands = (Widgets, Beta)
    offers = (Rack,)


@pytest.fixture
def acme(install: Installer) -> Install:
    return install(Acme, providers={"store": (Store,)})


def _docs(installed: Install, *words: str) -> tuple[int, str, str]:
    ran = installed.cli(["docs", *words])
    return ran.exit, ran.stdout, "\n".join(ran.stderr)


def _normalised(page: str) -> str:
    head, second, rest = page.split("\n", 2)
    second = re.sub(r"^untaped \S+", "untaped VERSION", second)
    second = second.replace(shown_path(sys.prefix), "ENV")
    return "\n".join([head, second, rest.replace(str(config_path()), "CONFIG")])


def _assert_snapshot(case: str, page: str) -> None:
    path = SNAPSHOTS / f"{case}.md"
    text = _normalised(page)
    if _UPDATE:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    assert path.is_file(), f"no snapshot {path.name}; run with UNTAPED_UPDATE_SNAPSHOTS=1"
    assert text == path.read_text(encoding="utf-8")


# --- the pages ---------------------------------------------------------------------

PAGES = {
    "overview": (),
    "plugin": ("acme",),
    "commands": ("acme", "commands"),
    "group": ("acme", "commands", "widgets"),
    "command": ("acme", "commands", "widgets", "add"),
    "command-examples": ("acme", "commands", "widgets", "show"),
    "inputs": ("acme", "inputs"),
    "input": ("acme", "inputs", "plan"),
    "outputs": ("acme", "outputs"),
    "output": ("acme", "outputs", "widget"),
    "settings": ("acme", "settings"),
    "guides": ("acme", "guides"),
    "guide-bundle": ("acme", "guides", "selection"),
    "guide-enum": ("acme", "guides", "finish"),
    "extensions": ("acme", "extensions"),
    "extension-point": ("acme", "extensions", "rack"),
}


@pytest.mark.parametrize("case", PAGES)
def test_each_page_renders_from_the_declaration(acme: Install, case: str) -> None:
    code, out, err = _docs(acme, *PAGES[case])
    assert (code, err) == (0, "")
    _assert_snapshot(case, out)


def test_every_title_is_its_address(acme: Install) -> None:
    for words in PAGES.values():
        _, out, _ = _docs(acme, *words)
        title = out.splitlines()[0].removesuffix(" [experimental]")
        assert title == "# " + " ".join(words or ("untaped",)), words


def test_every_page_but_the_overview_says_how_the_pages_are_organized(acme: Install) -> None:
    _, top, _ = _docs(acme)
    assert "How these pages are organized" not in top
    for words in list(PAGES.values())[1:]:
        _, out, _ = _docs(acme, *words)
        assert out.splitlines()[2] == "How these pages are organized: `untaped docs`.", words


def test_a_quoted_address_reads_as_the_same_words(acme: Install) -> None:
    assert _docs(acme, "acme", "commands widgets add") == _docs(
        acme, "acme", "commands", "widgets", "add"
    )


def test_core_answers_as_untaped(acme: Install) -> None:
    code, out, _ = _docs(acme, "untaped", "commands", "config", "set")
    assert code == 0
    assert out.startswith("# untaped commands config set\n")
    _, settings, _ = _docs(acme, "untaped", "settings")
    assert "`http.timeout_seconds`" in settings


def test_an_experimental_command_is_marked_on_its_page_and_in_lists(acme: Install) -> None:
    _, page, _ = _docs(acme, "acme", "commands", "beta")
    _, listed, _ = _docs(acme, "acme", "commands")
    assert page.startswith("# acme commands beta [experimental]\n")
    assert "`beta` [experimental]" in listed


def test_a_command_taking_confirm_asks_first_on_its_page_only(acme: Install) -> None:
    _, page, _ = _docs(acme, "acme", "commands", "widgets", "add")
    _, listed, _ = _docs(acme, "acme", "commands")
    assert "(asks first)" in page
    assert "(asks first)" not in listed


def test_a_command_page_shows_every_option_help_shows_in_its_order(acme: Install) -> None:
    _, page, _ = _docs(acme, "acme", "commands", "widgets", "show")
    usage = page.split("## Usage", 1)[1]
    shown = re.findall(r"^- `(--[\w-]+)", usage, re.MULTILINE)
    assert shown == ["--every", "--names", "--limit", "--format", "--columns"]


def test_the_options_panel_is_headed_after_another_panel(acme: Install) -> None:
    _, page, _ = _docs(acme, "acme", "commands", "widgets", "show")
    assert "### Selection\n- `--every`" in page
    assert "### Options\n- `--limit`: Show at most this many. Default: `10`." in page


def test_a_default_that_is_a_choice_still_shows(acme: Install) -> None:
    _, page, _ = _docs(acme, "acme", "commands", "widgets", "add")
    assert "One of `default`, `spare`. Default: `default`." in page


def test_a_deprecated_command_is_left_out_of_lists_and_its_page_says_so(acme: Install) -> None:
    _, listed, _ = _docs(acme, "acme", "commands")
    code, page, _ = _docs(acme, "acme", "commands", "widgets", "drop")
    assert "drop" not in listed
    assert code == 0
    assert page.startswith("# acme commands widgets drop [deprecated]\n")
    assert "\nDeprecated: removed in untaped-acme 99.0; use untaped acme widgets add.\n" in page


def test_bare_example_lines_are_fenced(acme: Install) -> None:
    _, page, _ = _docs(acme, "untaped", "commands", "config", "get")
    examples = page.split("## Examples\n", 1)[1]
    assert examples.startswith("\n```console\n$ untaped config get ")


def test_an_example_s_output_stays_in_its_fence() -> None:
    examples = "Show one:\n\n$ untaped acme widgets show w1\nw1  2x1\n\nThat is all."
    assert _fenced(examples).splitlines() == [
        "Show one:",
        "",
        "```console",
        "$ untaped acme widgets show w1",
        "w1  2x1",
        "```",
        "",
        "That is all.",
    ]


# --- search ----------------------------------------------------------------------


def test_search_prints_each_matching_line_after_its_page_s_address(acme: Install) -> None:
    code, out, _ = _docs(acme, "acme", "--search", "GLOSS")
    assert code == 0
    lines = out.splitlines()
    assert lines
    assert all(line.startswith("untaped docs acme") for line in lines)
    assert "untaped docs acme guides finish: A gloss finish cannot be undone." in lines


def test_search_covers_only_the_pages_under_the_address(acme: Install) -> None:
    _, under, _ = _docs(acme, "acme", "outputs", "--search", "rack")
    assert under
    assert all(line.startswith("untaped docs acme outputs") for line in under.splitlines())


def test_search_from_the_overview_covers_every_page(acme: Install) -> None:
    _, out, _ = _docs(acme, "--search", "Labels to find it by")
    assert "untaped docs acme outputs widget: " in out
    assert "untaped docs acme inputs plan: " in out


def test_search_finding_nothing_says_so_on_stderr_only(acme: Install) -> None:
    code, out, err = _docs(acme, "acme", "--search", "no such text anywhere")
    assert (code, out) == (0, "")
    assert "no line under `untaped docs acme` contains 'no such text anywhere'" in err


@pytest.mark.parametrize("text", ["", "  "])
def test_a_blank_search_is_a_usage_error(acme: Install, text: str) -> None:
    code, out, err = _docs(acme, "--search", text)
    assert (code, out) == (2, "")
    assert "--search needs some text to look for" in err


# --- errors ----------------------------------------------------------------------


def test_a_plugin_this_untaped_lacks_is_not_installed_here(acme: Install) -> None:
    code, out, err = _docs(acme, "jira")
    assert (code, out) == (2, "")
    assert "jira is not installed in this untaped (" in err
    assert "`untaped docs` lists what is" in err


@pytest.mark.parametrize(
    ("words", "listed"),
    [
        (("acme", "widgets"), "untaped docs acme"),
        (("acme", "commands", "widgets", "new"), "untaped docs acme commands"),
        (("acme", "outputs", "gadget"), "untaped docs acme outputs"),
        (("acme", "guides", "lifecycle"), "untaped docs acme guides"),
        (("acme", "settings", "colour"), "untaped docs acme settings"),
    ],
)
def test_an_unknown_address_is_exit_2_naming_the_list_page(
    acme: Install, words: tuple[str, ...], listed: str
) -> None:
    code, out, err = _docs(acme, *words)
    assert (code, out) == (2, "")
    assert f'no page "{" ".join(words)}"; `{listed}` lists what acme has' in err


def test_a_search_under_an_unknown_address_fails_as_its_page_would(acme: Install) -> None:
    code, _, err = _docs(acme, "acme", "gadgets", "--search", "x")
    assert code == 2
    assert 'no page "acme gadgets"' in err


def test_an_empty_section_is_hidden_and_no_address(install: Installer) -> None:
    class Plain(Plugin):
        """A plugin with commands only."""

        settings = AcmeSettings
        commands = (Beta,)

    installed = install(Plain)
    _, page, _ = _docs(installed, "plain")
    for section in ("inputs", "outputs", "extensions", "guides"):
        assert f"`{section}`" not in page
        code, _, err = _docs(installed, "plain", section)
        assert code == 2, section
        assert f'no page "plain {section}"; `untaped docs plain` lists what plain has' in err


# --- who answered ------------------------------------------------------------------


def test_the_second_line_names_the_version_and_environment(acme: Install) -> None:
    _, top, _ = _docs(acme)
    version = metadata.version("untaped")
    assert top.splitlines()[1] == f"untaped {version} · {shown_path(sys.prefix)}"


def test_the_shared_section_names_the_config_file_in_use(acme: Install) -> None:
    _, out, _ = _docs(acme)
    assert f"- Settings live in `{config_path()}`" in out


def test_a_project_asking_for_another_untaped_adds_a_line(
    acme: Install, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "app"
    project.mkdir()
    (project / ".git").mkdir()
    (project / "pyproject.toml").write_text(
        '[project]\nname = "app"\ndependencies = ["untaped[jira]==1.0.*"]\n', encoding="utf-8"
    )
    monkeypatch.chdir(project)
    version = metadata.version("untaped")
    for words in ((), ("acme",), ("acme", "commands", "beta")):
        _, out, _ = _docs(acme, *words)
        third = out.splitlines()[2]
        assert third.startswith("This project ("), words
        assert f"requires untaped[jira]==1.0.*; this untaped is {version}. Run the" in third


# --- how it prints -------------------------------------------------------------------


def test_piped_pages_are_raw_markdown_and_a_terminal_gets_them_rendered(
    acme: Install, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, piped, _ = _docs(acme, "acme")
    assert piped.startswith("# acme\n")
    monkeypatch.setattr("untaped.runtime.render.stream_is_tty", lambda _stream: True)
    _, shown, _ = _docs(acme, "acme")
    assert not shown.startswith("# ")
    assert "Manage widgets on racks." in shown


def test_every_help_screen_ends_with_its_page_s_address(acme: Install) -> None:
    for line, address in (
        ("--help", "untaped docs"),
        ("config set --help", "untaped docs untaped commands config set"),
        ("acme --help", "untaped docs acme"),
        ("acme widgets --help", "untaped docs acme commands widgets"),
        ("acme widgets add --help", "untaped docs acme commands widgets add"),
        ("acme beta --help", "untaped docs acme commands beta"),
    ):
        out = acme.cli(line).stdout.rstrip()
        assert out.splitlines()[-1].strip() == f"Full documentation: {address}", line
