"""Guide and map-line rules on a composed plugin with a guides folder."""

from __future__ import annotations

import enum
from pathlib import Path

import pytest
from cyclopts import App
from pydantic import Field

from core_test_support.commands.support import make_plugin
from untaped.core.plugin_check.guides import MAP_LINE_LIMIT, guide_violations
from untaped.runtime import bootstrap
from untaped.runtime.command_lines import console_lines
from untaped.runtime.registry import PluginSpec
from untaped.sdk import Command, Plugin, Record, create_app
from untaped.sdk.contracts import Contract, Provider
from untaped.sdk.records import record_model
from untaped.sdk.testing import plugin_candidate


class Widget(Record, kind="svc.widget"):
    """A widget, for a guide's YAML block."""

    name: str
    size: int = 1


def _factory() -> App:
    app = create_app(name="svc", help="Service.")

    @app.command(name="show")
    def show() -> None:
        """Show it."""

    return app


def _violations(
    tmp_path: Path, help_text: str = "Service.", **files: str
) -> list[tuple[str, str, str]]:
    folder = tmp_path / "guides"
    folder.mkdir(exist_ok=True)
    for name, text in files.items():
        (folder / f"{name}.md").write_text(text, encoding="utf-8")
    spec = PluginSpec(name="svc", app_factory=_factory, help=help_text, guides=folder)
    root = bootstrap.build_root_app(candidates=[plugin_candidate(spec)])
    found = guide_violations(root, bootstrap.composition(), spec)
    return [tuple(line.split("::", 2)) for line in found]  # type: ignore[misc]


def _guide(body: str, description: str = "Read before showing anything.") -> str:
    return f"---\ndescription: {description}\n---\n\n{body}\n"


def _quoting(
    body: str, *others: type[Plugin], module: str = __name__, checked: str = "svc"
) -> list[tuple[str, ...]]:
    """The violations of a plugin class's guide saying ``body``: enum ``Finish`` of ``svc show``.

    ``module`` defines the enum: another plugin's (one of ``others``), which ``checked`` names.
    """
    finish = enum.Enum("Finish", {"MATTE": "matte"}, module=module)  # type: ignore[misc]
    finish.__doc__ = f"How it is finished.\n\n{body}"

    def run(self: Command) -> None:
        return None

    show = type(
        "Show",
        (Command,),
        {
            "__doc__": "Show it.",
            "__module__": __name__,
            "__annotations__": {"finish": finish},
            "finish": Field(finish.MATTE, description="The finish."),
            "run": run,
        },
    )
    svc = make_plugin("svc", commands=(show,), records=(Widget,))
    found = _map_violations(svc, *others, name=checked)
    return [tuple(line.split("::", 2)) for line in found]


def test_a_clean_guide_has_no_violation() -> None:
    body = (
        "Run `untaped svc show`, or read `untaped docs svc` and `untaped docs`.\n\n"
        "```console\n$ untaped svc show\n```\n"
    )
    assert _quoting(body) == []


def test_guide_names_are_kebab_case(tmp_path: Path) -> None:
    assert _violations(tmp_path, Life_Cycle=_guide("Text.")) == [
        ("guides/Life_Cycle.md", "guide-name", "'Life_Cycle' is not kebab-case")
    ]


def test_a_guide_may_share_a_command_s_name(tmp_path: Path) -> None:
    """A guide's address is under ``guides``, so no command shadows it."""
    assert _violations(tmp_path, show=_guide("Text.")) == []


@pytest.mark.parametrize(
    ("text", "detail"),
    [
        ("Text.\n", "has no front matter (a `---` block with a description)"),
        ("---\ndescription: x\nText.\n", "front matter has no closing `---`"),
        ("---\ntitle: x\n---\nText.\n", "front matter has no description"),
        ("---\ndescription: |\n  one\n  two\n---\nText.\n", "description is more than one line"),
        ("---\ndescription: [\n---\nText.\n", "front matter is not YAML: "),
    ],
)
def test_a_guide_says_when_to_read_it_in_one_line(tmp_path: Path, text: str, detail: str) -> None:
    [(where, rule, found)] = _violations(tmp_path, notes=text)
    assert (where, rule) == ("guides/notes.md", "guide-description")
    assert found.startswith(detail)


def test_the_page_writes_the_guide_s_heading(tmp_path: Path) -> None:
    fenced = "```bash\n# a comment, not a heading\n```\n"
    assert _violations(tmp_path, ok=_guide(fenced)) == []
    assert _violations(tmp_path, bad=_guide("# Notes\n\nText.")) == [
        (
            "guides/bad.md",
            "guide-heading",
            "the page writes the heading; start the guide's own at ##",
        )
    ]


def test_a_quoted_docs_page_must_exist() -> None:
    body = (
        "See `untaped docs svc guides nothing`, `untaped docs jira` and "
        "`untaped docs svc guides NAME`."
    )
    [(_, rule, detail)] = _quoting(body)
    assert rule == "guide-link"
    assert detail.startswith('untaped docs svc guides nothing: no page "svc guides nothing"')


def test_a_quoted_docs_address_is_checked_as_its_words() -> None:
    body = 'See `untaped docs "svc guides nothing"`.'
    [(_, rule, detail)] = _quoting(body)
    assert rule == "guide-link"
    assert 'no page "svc guides nothing"' in detail


def test_a_quoted_address_naming_a_placeholder_is_no_link() -> None:
    body = (
        'See `untaped docs "svc guides NAME"`, `untaped docs "svc guides <name>"`, '
        "`untaped docs svc [SECTION]`, `untaped docs svc [SECTION]...` and "
        "`untaped docs svc SECTION...`."
    )
    assert _quoting(body) == []


def test_root_options_and_redirects_leave_a_broken_link_flagged() -> None:
    body = (
        "`untaped --profile PROD docs svc guides nothing` and "
        '`untaped docs "svc guides nothing" < IN`'
    )
    found = _quoting(body)
    assert [rule for _, rule, _ in found] == ["guide-link", "guide-link"]


def test_a_console_block_with_a_title_is_still_checked() -> None:
    [(_, rule, _)] = _quoting("```console title\n$ untaped svc nope\n```")
    assert rule == "guide-command"


def test_a_guide_may_link_a_guide() -> None:
    assert _quoting("Then `untaped docs svc guides finish`.") == []


def test_quoted_commands_parse() -> None:
    body = "```console\n$ untaped svc show --nope\n```\n\nAnd `untaped svc missing`."
    found = _quoting(body)
    assert [(rule, detail.split(":", 1)[0]) for _, rule, detail in found] == [
        ("guide-command", "untaped svc show --nope"),
        ("guide-command", "untaped svc missing"),
    ]


def test_a_console_line_ending_in_a_backslash_goes_on() -> None:
    body = "```console\n$ untaped svc show \\\n    --nope\n$ untaped svc show\n```"
    found = _quoting(body)
    assert [(rule, detail.split(":", 1)[0]) for _, rule, detail in found] == [
        ("guide-command", "untaped svc show --nope"),
    ]


def test_a_continuation_prompt_is_cut_and_a_last_backslash_goes_nowhere() -> None:
    examples = "```console\n$ untaped svc show \\\n> --nope\n$ untaped svc show \\\n```"
    assert console_lines(examples) == ["untaped svc show --nope", "untaped svc show"]


def test_a_guide_is_checked_under_the_plugin_whose_package_defines_it() -> None:
    """``atlas`` defines the enum and only ``svc`` uses it: atlas's check reads it."""
    atlas = make_plugin("atlas")
    body = "Run `untaped svc missing`."
    found = _quoting(body, atlas, module=atlas.__module__, checked="atlas")
    assert [(rule, detail.split(":", 1)[0]) for _, rule, detail in found] == [
        ("guide-command", "untaped svc missing"),
    ]


def test_commands_in_yaml_comments_parse() -> None:
    body = "```yaml\n#   untaped svc show --nope\nkey: value\n```"
    found = _quoting(body)
    assert [(rule, detail.split(":", 1)[0]) for _, rule, detail in found] == [
        ("guide-command", "untaped svc show --nope"),
    ]


def test_commands_after_a_kind_tagged_block_are_still_checked() -> None:
    body = (
        "```yaml svc.widget\nname: w\n```\n\nRun `untaped svc missing`.\n\n"
        "```bash\nuntaped svc show --nope\n```"
    )
    found = _quoting(body)
    assert sorted(detail.split(":", 1)[0] for _, _, detail in found) == [
        "untaped svc missing",
        "untaped svc show --nope",
    ]


def test_a_kind_tagged_yaml_block_validates_against_its_model() -> None:
    assert record_model("svc.widget") is Widget
    good = "```yaml svc.widget\nname: w\nsize: 2\n```"
    bad = "```yaml svc.widget\nsize: big\n```"
    unknown = "```yaml svc.gadget\nname: g\n```"
    plain = "```yaml\nnot: checked\n```"
    assert _quoting(f"{good}\n\n{plain}") == []
    found = _quoting(f"{bad}\n\n{unknown}")
    assert [rule for _, rule, _ in found] == ["guide-yaml", "guide-yaml"]
    assert found[0][2].startswith("svc.widget: name: ")
    assert found[1][2] == "svc.gadget: no record kind by that name"


def _map_violations(*plugins: type[Plugin], name: str = "svc") -> list[str]:
    root = bootstrap.build_root_app(candidates=[plugin_candidate(each) for each in plugins])
    result = bootstrap.composition()
    assert not result.quarantine
    [spec] = [plugin.spec for plugin in result.plugins if plugin.spec.name == name]
    return guide_violations(root, result, spec)


_OVER = (
    f"{MAP_LINE_LIMIT + 1} characters, over {MAP_LINE_LIMIT}: "
    "every agent session reads the overview"
)


@pytest.mark.parametrize(("length", "found"), [(MAP_LINE_LIMIT + 1, True), (MAP_LINE_LIMIT, False)])
def test_map_lines_are_capped(length: int, found: bool) -> None:
    svc = make_plugin("svc")
    svc.__doc__ = "x" * length
    assert _map_violations(svc) == (
        [f"svc::map-line::its summary line is {_OVER}"] if found else []
    )


class Long(Contract):
    """Where items come from, said at far too great a length for the overview to carry them."""

    def items(self) -> list[str]:
        """Every item."""
        raise NotImplementedError


class LongFill(Long, Provider):
    """Items from svc, said at far too great a length for the line under the extension point."""

    def items(self) -> list[str]:
        return []


def test_an_extension_point_s_line_and_a_fill_s_line_are_capped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("untaped.core.plugin_check.guides.MAP_LINE_LIMIT", 80)
    owner = make_plugin("svc", offers=(Long,))
    assert [line.split(" is ")[0] for line in _map_violations(owner)] == [
        "svc::map-line::an extension point's line"
    ]
    filler = make_plugin("bin", fills=(LongFill,))
    assert [
        line.split(" is ")[0]
        for line in _map_violations(make_plugin("rack", offers=(Long,)), filler, name="bin")
    ] == ["bin::map-line::its line under rack's extension point"]


def test_a_plugin_without_guides_checks_only_its_map_lines() -> None:
    assert _map_violations(make_plugin("svc")) == []


class Finish(enum.Enum):
    """How a widget is finished.

    Read `untaped docs svc nowhere` first, then run `untaped svc paint --nope`.

    ```yaml svc.widget
    name: w
    size: big
    ```
    """

    MATTE = "matte"
    GLOSS = "gloss"


class Paint(Command):
    """Paint a widget."""

    finish: Finish = Field(Finish.MATTE, description="The finish.")

    def run(self) -> None:
        return None


def test_a_plugin_class_s_guides_quote_pages_commands_and_records_that_resolve() -> None:
    svc = make_plugin("svc", commands=(Paint,), records=(Widget,))
    found = _map_violations(svc)
    where = f"{__name__}:Finish"
    assert [line.partition(": ")[0] for line in found] == [
        f"{where}::guide-link::untaped docs svc nowhere",
        f"{where}::guide-command::untaped svc paint --nope",
        f"{where}::guide-yaml::svc.widget",
    ]
