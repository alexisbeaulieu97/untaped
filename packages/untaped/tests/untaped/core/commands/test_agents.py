"""Tests for the root ``untaped agents`` command: the map, a plugin's page, a command's page.

A fake plugin (``acme``) carries what the pages read: commands with write
marks, an experimental and a deprecated one, an option deprecated, settings
and a docstring ``Examples`` section; ``rack`` and ``bin`` (installed for the
test) are a contract owner and its filler. Each stage is compared with a
golden page in ``snapshots/agents/<case>.md``, whose second line (the
version and environment) is normalised; run with
``UNTAPED_UPDATE_SNAPSHOTS=1`` to rewrite them.
"""

from __future__ import annotations

import os
import re
import sys
import zipfile
from importlib import metadata
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Annotated

import pytest
from cyclopts import App, Parameter
from pydantic import BaseModel, Field

from core_test_support.commands.contract_plugins import candidates as contract_candidates
from core_test_support.runtime.plugin_harness import make_candidate
from untaped.runtime import bootstrap
from untaped.runtime.registry import PluginCandidate, PluginSpec
from untaped.sdk._cli import DryRunOption, FormatOption, YesOption, create_app, writes
from untaped.sdk._home import config_path
from untaped.sdk.marks import DEPRECATED_OPTIONS, deprecated, experimental
from untaped.sdk.testing import CliInvoker
from untaped.sdk.words import shown_path

pytestmark = pytest.mark.usefixtures("_isolated_config")

SNAPSHOTS = Path(__file__).parents[2] / "snapshots" / "agents"
#: Read at import: the hermetic test environment clears ``UNTAPED_*`` variables.
_UPDATE = bool(os.environ.get("UNTAPED_UPDATE_SNAPSHOTS"))


class AcmeSettings(BaseModel):
    """Settings of the fake plugin (section ``acme``)."""

    widgets_dir: Path = Field(Path("~/.acme/widgets"), description="Where widgets live.")
    colour: str = Field("blue", description="The colour of a new widget.")
    old_colour: Annotated[str | None, deprecated(removed_in=12, replacement="acme.colour")] = Field(
        None, description="Use colour."
    )


def _acme_app() -> App:
    app = create_app(name="acme", help="Manage widgets.")
    widgets = create_app(name="widgets", help="Widgets one by one.")
    app.command(widgets)

    @widgets.command(name="list")
    def list_command(*, fmt: FormatOption = "table") -> None:
        """List every widget.

        Examples
        --------
        Every widget, as JSON:

        ```console
        $ untaped acme widgets list -f json
        ```
        """

    @widgets.command(name="add")
    @writes
    def add_command(
        name: Annotated[str, Parameter(help="The widget's name.")],
        /,
        *,
        size: Annotated[int, Parameter(help="How big it is.")] = 3,
        fmt: FormatOption = "table",
    ) -> None:
        """Add a widget.

        A widget with that name already there is reported ``unchanged``.
        """

    @widgets.command(name="drop")
    @writes(destructive=True)
    def drop_command(
        names: Annotated[list[str], Parameter(help="The widgets to drop.")],
        /,
        *,
        yes: YesOption = False,
        dry_run: DryRunOption = False,
        fmt: FormatOption = "table",
        force: Annotated[
            bool,
            Parameter(group=DEPRECATED_OPTIONS, help="Drop it anyway.", negative=""),
            deprecated(removed_in=12, replacement="--yes"),
        ] = False,
    ) -> None:
        """Drop widgets for good."""

    @app.command(name="beta")
    @experimental
    def beta_command() -> None:
        """Try the next thing."""

    @app.command(name="old")
    @deprecated(removed_in=12, replacement=list_command)
    def old_command() -> None:
        """The old listing."""

    return app


ACME = PluginSpec(name="acme", app_factory=_acme_app, settings=AcmeSettings, help="Manage widgets.")


def _candidates() -> list[PluginCandidate]:
    return [make_candidate(ACME, "untaped-acme", distribution_version="2.3.4")]


@pytest.fixture
def contracts(contract_plugins_site: Path) -> list[PluginCandidate]:
    """``acme`` beside ``rack`` (an owner) and ``bin`` (its filler)."""
    return [*_candidates(), *contract_candidates()]


def _run(*words: str, candidates: list[PluginCandidate] | None = None) -> tuple[int, str, str]:
    root = bootstrap.build_root_app(candidates=_candidates() if candidates is None else candidates)
    invoked = CliInvoker().invoke(root.meta, ["agents", *words])
    return invoked.exit_code, invoked.stdout, invoked.stderr


def _normalised(page: str) -> str:
    head, second, rest = page.split("\n", 2)
    second = re.sub(r"^untaped \S+", "untaped VERSION", second)
    second = second.replace(_shown_prefix(), "ENV")
    return "\n".join([head, second, rest.replace(str(config_path()), "CONFIG")])


def _shown_prefix() -> str:
    return shown_path(sys.prefix)


def _assert_snapshot(case: str, page: str) -> None:
    path = SNAPSHOTS / f"{case}.md"
    text = _normalised(page)
    if _UPDATE:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    assert path.is_file(), f"no snapshot {path.name}; run with UNTAPED_UPDATE_SNAPSHOTS=1"
    assert text == path.read_text(encoding="utf-8")


# --- the stages ------------------------------------------------------------------


def test_the_map_lists_core_and_every_plugin_with_what_fills_its_extension_points(
    contracts: list[PluginCandidate],
) -> None:
    code, out, _ = _run(candidates=contracts)
    assert code == 0
    _assert_snapshot("map", out)


def test_a_plugin_page_lists_its_commands_with_their_marks_and_its_settings() -> None:
    code, out, _ = _run("acme")
    assert code == 0
    _assert_snapshot("plugin", out)


def test_an_owner_page_names_who_fills_its_extension_point(
    contracts: list[PluginCandidate],
) -> None:
    code, out, _ = _run("rack", candidates=contracts)
    assert code == 0
    _assert_snapshot("owner", out)
    code, out, _ = _run("bin", candidates=contracts)
    assert code == 0
    assert "## Fills rack: Where items come from" in out


def test_a_command_page_has_usage_options_and_behaviour() -> None:
    code, out, _ = _run("acme", "widgets", "add")
    assert code == 0
    _assert_snapshot("command", out)


def test_a_command_page_prints_its_docstring_examples() -> None:
    code, out, _ = _run("acme", "widgets", "list")
    assert code == 0
    assert "## Examples\n\nEvery widget, as JSON:\n\n```console\n$ untaped acme" in out


def _check_app() -> App:
    app = create_app(name="check")

    @app.default
    @writes
    def check_command(
        paths: Annotated[list[str] | None, Parameter(help="Only these files.")] = None,
        /,
        *,
        strict: Annotated[bool, Parameter(help="Fail on warnings too.", negative="")] = False,
    ) -> None:
        """Check the files you are in.

        Exit 3 when a check fails.

        Examples
        --------
        ```console
        $ untaped check --strict
        ```
        """

    @app.command(name="list")
    def list_command() -> None:
        """List every check."""

    return app


def test_a_plugin_that_runs_on_its_own_shows_its_usage_on_its_page() -> None:
    spec = PluginSpec(name="check", app_factory=_check_app, help="Check files.")
    code, out, _ = _run("check", candidates=[make_candidate(spec, "untaped-check")])
    assert code == 0
    _assert_snapshot("plugin-runs", out)


def test_a_plugin_that_only_runs_on_its_own_has_a_usage_and_no_command_list() -> None:
    def single() -> App:
        app = create_app(name="single")

        @app.default
        @writes
        def single_command() -> None:
            pass

        return app

    spec = PluginSpec(name="single", app_factory=single, help="One thing.")
    code, out, _ = _run("single", candidates=[make_candidate(spec, "untaped-single")])
    assert code == 0
    assert "Details of" not in out
    assert "## Commands" not in out
    assert "## Usage\n(changes things)\n\n`untaped single`" in out


def test_a_group_page_lists_its_subcommands() -> None:
    code, out, _ = _run("acme", "widgets")
    assert code == 0
    _assert_snapshot("group", out)


def test_core_answers_as_untaped() -> None:
    code, out, _ = _run("untaped")
    assert code == 0
    assert out.startswith("# untaped\n")
    assert "- `config set`: " in out
    assert "## Settings (`untaped`)" in out
    assert "- `http.timeout_seconds` (number" in out
    code, out, _ = _run("untaped", "config", "set")
    assert code == 0
    assert "`untaped config set " in out


def test_core_page_never_lists_a_plugin_as_a_core_command() -> None:
    code, _, err = _run("untaped", "acme")
    assert code == 2
    assert 'no topic "acme" in untaped' in err


# --- marks ---------------------------------------------------------------------


def test_deprecated_commands_options_and_settings_are_left_out_as_help_leaves_them() -> None:
    _, plugin, _ = _run("acme")
    _, drop, _ = _run("acme", "widgets", "drop")
    assert "`old`" not in plugin
    assert "old_colour" not in plugin
    assert "--force" not in drop


def test_a_deprecated_command_asked_by_name_still_answers() -> None:
    code, out, _ = _run("acme", "old")
    assert code == 0
    assert out.startswith("# acme old\n")


def test_an_experimental_plugin_marks_its_heading_and_its_commands_pages() -> None:
    spec = PluginSpec(
        name="acme", app_factory=_acme_app, help="Manage widgets.", stability=experimental
    )
    candidates = [make_candidate(spec, "untaped-acme")]
    _, top, _ = _run(candidates=candidates)
    _, page, _ = _run("acme", "widgets", "add", candidates=candidates)
    assert "## acme [experimental] · details: `untaped agents acme`" in top
    assert page.startswith("# acme widgets add [experimental]\n")


def test_write_marks_follow_the_declared_writes() -> None:
    _, out, _ = _run("acme")
    assert "- `widgets add`: Add a widget. (changes things)" in out
    assert "- `widgets drop`: Drop widgets for good. (destructive)" in out
    assert "- `widgets list`: List every widget.\n" in out


def test_a_command_taking_confirm_asks_first() -> None:
    _, out, _ = _run("untaped")
    assert "- `project remove`: Remove a project: release what it holds, then delete what " in out
    assert "untaped wrote in it. (asks first)\n" in out
    assert "- `project list`: List the projects this home records, with each one's state.\n" in out


# --- errors and paths ------------------------------------------------------------


def test_a_plugin_this_untaped_lacks_is_not_installed_here() -> None:
    code, out, err = _run("jira")
    assert (code, out) == (2, "")
    assert "jira is not installed in this untaped (" in err
    assert "`untaped agents` lists what is" in err


def test_a_quarantined_plugin_says_why_and_where_to_look() -> None:
    reserved = PluginSpec(name="agents", app_factory=_acme_app, help="Takes a reserved name.")
    code, _, err = _run("agents", candidates=[make_candidate(reserved, "untaped-agents")])
    assert code == 2
    assert "agents is installed but was quarantined [duplicate-name]" in err
    assert "`untaped plugin list` shows it" in err


def _broken_app() -> App:
    raise RuntimeError("cli import failed")


BROKEN = PluginSpec(name="broke", app_factory=_broken_app, help="Never builds.")


def test_a_plugin_that_cannot_build_its_commands_fails_as_they_do() -> None:
    candidates = [make_candidate(BROKEN, "untaped-broke")]
    for words in (("broke",), ("broke", "list")):
        code, out, err = _run(*words, candidates=candidates)
        assert (code, out) == (4, ""), words
        assert "could not build its commands: " in err
        assert "cli import failed" in err


def test_the_map_builds_no_plugin_s_commands() -> None:
    built: list[str] = []

    def factory() -> App:
        built.append("acme")
        return _acme_app()

    spec = PluginSpec(name="acme", app_factory=factory, help="Manage widgets.")
    candidates = [make_candidate(spec, "untaped-acme"), make_candidate(BROKEN, "untaped-broke")]
    code, out, _ = _run(candidates=candidates)
    assert code == 0
    assert "## broke · details: `untaped agents broke`\nNever builds." in out
    assert built == []


def test_a_mistyped_topic_points_at_the_plugin_page() -> None:
    code, _, err = _run("acme", "widgets", "new")
    assert code == 2
    assert 'no topic "widgets new" in acme; `untaped agents acme` lists them' in err


def test_a_quoted_path_reads_as_the_same_words() -> None:
    assert _run("acme", "widgets add") == _run("acme", "widgets", "add")


def test_every_page_but_the_map_ends_by_pointing_back_at_it() -> None:
    _, top, _ = _run()
    assert not top.rstrip().endswith("`untaped agents`")
    for words in (("acme",), ("acme", "widgets"), ("acme", "beta"), ("untaped",)):
        _, out, _ = _run(*words)
        assert out.endswith("The map of this untaped: `untaped agents`\n"), words


# --- who answered ------------------------------------------------------------------


def test_the_second_line_names_the_version_and_environment() -> None:
    _, top, _ = _run()
    _, page, _ = _run("acme", "beta")
    shown = _shown_prefix()
    version = metadata.version("untaped")
    assert top.splitlines()[1] == f"untaped {version} · {shown}"
    assert page.splitlines()[1] == f"untaped {version} · untaped-acme 2.3.4 · {shown}"


def test_the_shared_section_names_the_config_file_in_use(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("UNTAPED_HOME", str(tmp_path / "elsewhere"))
    _, out, _ = _run()
    assert f"- Settings live in `{tmp_path / 'elsewhere' / 'config.yml'}`" in out


def test_a_project_asking_for_another_untaped_adds_a_third_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "app"
    project.mkdir()
    (project / ".git").mkdir()
    (project / "pyproject.toml").write_text(
        '[project]\nname = "app"\ndependencies = ["untaped[jira]==1.0.*"]\n', encoding="utf-8"
    )
    monkeypatch.chdir(project)
    version = metadata.version("untaped")
    for words in ((), ("acme",), ("acme", "beta")):
        _, out, _ = _run(*words)
        third = out.splitlines()[2]
        assert third.startswith("This project (")
        assert f"requires untaped[jira]==1.0.*; this untaped is {version}. Run the" in third


def test_a_project_the_running_untaped_satisfies_adds_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        f'[dependency-groups]\ndev = ["untaped=={metadata.version("untaped")}"]\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    _, out, _ = _run()
    assert out.splitlines()[2] == ""


# --- guides ----------------------------------------------------------------------

_LIFECYCLE = """\
---
description: "Read before adding or dropping widgets: how a widget's life runs."
---

A widget is added once and dropped for good: `untaped acme widgets drop` keeps nothing.

## Sizes

See `untaped agents acme widgets add` for the size option.
"""


def _guides(tmp_path: Path, **files: str) -> Path:
    folder = tmp_path / "guides"
    folder.mkdir()
    for name, text in files.items():
        (folder / f"{name.replace('_', '-')}.md").write_text(text, encoding="utf-8")
    return folder


def _with_guides(folder: Traversable, *, app: bool = True) -> list[PluginCandidate]:
    spec = PluginSpec(
        name="acme",
        app_factory=_acme_app if app else None,
        settings=AcmeSettings,
        help="Manage widgets." if app else None,
        guides=folder,
    )
    return [make_candidate(spec, "untaped-acme", distribution_version="2.3.4")]


def test_a_plugin_page_lists_its_guides_with_when_to_read_them(tmp_path: Path) -> None:
    candidates = _with_guides(_guides(tmp_path, lifecycle=_LIFECYCLE))
    code, out, _ = _run("acme", candidates=candidates)
    assert code == 0
    _assert_snapshot("plugin-guides", out)


def test_a_guide_page_prints_its_text_under_the_heading_the_page_writes(tmp_path: Path) -> None:
    candidates = _with_guides(_guides(tmp_path, lifecycle=_LIFECYCLE))
    code, out, _ = _run("acme", "lifecycle", candidates=candidates)
    assert code == 0
    _assert_snapshot("guide", out)
    assert _run("acme", " lifecycle ", candidates=candidates)[1] == out


def test_a_command_by_the_guide_s_name_wins_and_the_guide_shows_as_shadowed(
    tmp_path: Path,
) -> None:
    candidates = _with_guides(_guides(tmp_path, beta=_LIFECYCLE))
    _, plugin, _ = _run("acme", candidates=candidates)
    _, beta, _ = _run("acme", "beta", candidates=candidates)
    assert "- `beta` (shadowed by the command `beta`): Read before adding" in plugin
    assert beta.startswith("# acme beta [experimental]\n")
    assert "Try the next thing." in beta


def test_a_plugin_with_guides_and_no_commands_has_topics(tmp_path: Path) -> None:
    candidates = _with_guides(_guides(tmp_path, lifecycle=_LIFECYCLE), app=False)
    code, plugin, _ = _run("acme", candidates=candidates)
    assert code == 0
    assert "## Commands" not in plugin
    assert "## Guides\n- `lifecycle`: Read before" in plugin
    assert _run("acme", "lifecycle", candidates=candidates)[0] == 0


def test_only_one_word_names_a_guide(tmp_path: Path) -> None:
    candidates = _with_guides(_guides(tmp_path, lifecycle=_LIFECYCLE))
    code, _, err = _run("acme", "lifecycle", "more", candidates=candidates)
    assert code == 2
    assert 'no topic "lifecycle more" in acme' in err


def test_a_guide_whose_front_matter_is_broken_still_prints(tmp_path: Path) -> None:
    candidates = _with_guides(_guides(tmp_path, notes="No front matter here.\n"))
    _, plugin, _ = _run("acme", candidates=candidates)
    code, out, _ = _run("acme", "notes", candidates=candidates)
    assert "- `notes`\n" in plugin
    assert code == 0
    assert "No front matter here." in out


def test_files_beside_the_guides_are_not_guides(tmp_path: Path) -> None:
    folder = _guides(tmp_path, lifecycle=_LIFECYCLE)
    (folder / "suite.yml").write_text("a: 1\n", encoding="utf-8")
    (folder / "drafts").mkdir()
    (folder / "drafts" / "later.md").write_text(_LIFECYCLE, encoding="utf-8")
    _, plugin, _ = _run("acme", candidates=_with_guides(folder))
    guides = plugin.split("## Guides\n", 1)[1].split("\n\n", 1)[0]
    assert (
        guides == "- `lifecycle`: Read before adding or dropping widgets: how a widget's life runs."
    )


def test_a_plugin_without_guides_keeps_the_command_pointer() -> None:
    _, out, _ = _run("acme")
    assert "Details of a command: `untaped agents acme COMMAND`" in out
    assert "## Guides" not in out


def test_guides_read_from_a_zip_import(tmp_path: Path) -> None:
    archive = tmp_path / "acme.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("untaped_acme/guides/lifecycle.md", _LIFECYCLE)
    folder = zipfile.Path(archive, "untaped_acme/guides/")
    code, out, _ = _run("acme", "lifecycle", candidates=_with_guides(folder))  # type: ignore[arg-type]
    assert code == 0
    assert "A widget is added once" in out


def test_a_byte_order_mark_does_not_hide_the_front_matter(tmp_path: Path) -> None:
    folder = _guides(tmp_path, lifecycle="\ufeff" + _LIFECYCLE)
    _, plugin, _ = _run("acme", candidates=_with_guides(folder))
    assert "- `lifecycle`: Read before adding" in plugin
