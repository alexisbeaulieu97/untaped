"""Guide and map-line rules on a composed plugin with a guides folder."""

from __future__ import annotations

from pathlib import Path

import pytest
from cyclopts import App

from test_plugins.plugin_harness import make_spec
from untaped import bootstrap
from untaped.conventions.guides import MAP_LINE_LIMIT, guide_violations
from untaped.plugins.registry import PluginSpec
from untaped.records import record_model
from untaped.sdk import Record, create_app
from untaped.testing import plugin_candidate


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


def test_a_clean_guide_has_no_violation(tmp_path: Path) -> None:
    body = (
        "Run `untaped svc show`, or read `untaped agents svc show` and `untaped agents`.\n\n"
        "```console\n$ untaped svc show\n```\n"
    )
    assert _violations(tmp_path, lifecycle=_guide(body)) == []


@pytest.mark.parametrize(
    ("name", "detail"),
    [
        ("Life_Cycle", "'Life_Cycle' is not kebab-case"),
        ("show", "the command `show` shadows it; rename the guide"),
    ],
)
def test_guide_names_are_kebab_case_and_shadow_no_command(
    tmp_path: Path, name: str, detail: str
) -> None:
    assert _violations(tmp_path, **{name: _guide("Text.")}) == [
        (f"guides/{name}.md", "guide-name", detail)
    ]


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


def test_a_quoted_agents_page_must_exist(tmp_path: Path) -> None:
    body = "See `untaped agents svc nothing`, `untaped agents jira` and `untaped agents svc NAME`."
    [(_, rule, detail)] = _violations(tmp_path, links=_guide(body))
    assert rule == "guide-link"
    assert detail.startswith('untaped agents svc nothing: no topic "nothing" in svc')


def test_a_quoted_agents_path_is_checked_as_its_words(tmp_path: Path) -> None:
    [(_, rule, detail)] = _violations(tmp_path, links=_guide('See `untaped agents "svc nothing"`.'))
    assert rule == "guide-link"
    assert 'no topic "nothing" in svc' in detail


def test_a_quoted_path_naming_a_placeholder_is_no_link(tmp_path: Path) -> None:
    body = (
        'See `untaped agents "svc TOPIC"`, `untaped agents "svc <topic>"`, '
        "`untaped agents svc [TOPIC]`, `untaped agents svc [TOPIC]...` and "
        "`untaped agents svc TOPIC...`."
    )
    assert _violations(tmp_path, links=_guide(body)) == []


def test_root_options_and_redirects_leave_a_broken_link_flagged(tmp_path: Path) -> None:
    body = '`untaped --profile PROD agents svc nothing` and `untaped agents "svc nothing" < IN`'
    found = _violations(tmp_path, links=_guide(body))
    assert [rule for _, rule, _ in found] == ["guide-link", "guide-link"]


def test_a_console_block_with_a_title_is_still_checked(tmp_path: Path) -> None:
    [(_, rule, _)] = _violations(
        tmp_path, titled=_guide("```console title\n$ untaped svc nope\n```")
    )
    assert rule == "guide-command"


def test_a_guide_may_link_another_guide(tmp_path: Path) -> None:
    assert (
        _violations(
            tmp_path, first=_guide("Then `untaped agents svc second`."), second=_guide("Done.")
        )
        == []
    )


def test_quoted_commands_parse(tmp_path: Path) -> None:
    body = "```console\n$ untaped svc show --nope\n```\n\nAnd `untaped svc missing`."
    found = _violations(tmp_path, commands=_guide(body))
    assert [(rule, detail.split(":", 1)[0]) for _, rule, detail in found] == [
        ("guide-command", "untaped svc show --nope"),
        ("guide-command", "untaped svc missing"),
    ]



def test_commands_in_yaml_comments_parse(tmp_path: Path) -> None:
    body = "```yaml\n#   untaped svc show --nope\nkey: value\n```"
    found = _violations(tmp_path, commands=_guide(body))
    assert [(rule, detail.split(":", 1)[0]) for _, rule, detail in found] == [
        ("guide-command", "untaped svc show --nope"),
    ]

def test_commands_after_a_kind_tagged_block_are_still_checked(tmp_path: Path) -> None:
    body = (
        "```yaml svc.widget\nname: w\n```\n\nRun `untaped svc missing`.\n\n"
        "```bash\nuntaped svc show --nope\n```"
    )
    found = _violations(tmp_path, after=_guide(body))
    assert sorted(detail.split(":", 1)[0] for _, _, detail in found) == [
        "untaped svc missing",
        "untaped svc show --nope",
    ]


def test_a_kind_tagged_yaml_block_validates_against_its_model(tmp_path: Path) -> None:
    assert record_model("svc.widget") is Widget
    good = "```yaml svc.widget\nname: w\nsize: 2\n```"
    bad = "```yaml svc.widget\nsize: big\n```"
    unknown = "```yaml svc.gadget\nname: g\n```"
    plain = "```yaml\nnot: checked\n```"
    assert _violations(tmp_path, good=_guide(good), plain=_guide(plain)) == []
    found = _violations(tmp_path, bad=_guide(bad), unknown=_guide(unknown))
    assert [(where, rule) for where, rule, _ in found] == [
        ("guides/bad.md", "guide-yaml"),
        ("guides/unknown.md", "guide-yaml"),
    ]
    assert found[0][2].startswith("svc.widget: name: ")
    assert found[1][2] == "svc.gadget: no record kind by that name"


def test_map_lines_are_capped(tmp_path: Path) -> None:
    long_help = "x" * (MAP_LINE_LIMIT + 1)
    assert _violations(tmp_path, help_text=long_help) == [
        (
            "svc",
            "map-line",
            f"its summary line is {MAP_LINE_LIMIT + 1} characters, over {MAP_LINE_LIMIT}: "
            "every agent session reads the map",
        )
    ]
    assert _violations(tmp_path, help_text="x" * MAP_LINE_LIMIT) == []


def test_a_plugin_without_guides_checks_only_its_map_lines() -> None:
    spec = make_spec(name="svc", factory=_factory)
    root = bootstrap.build_root_app(candidates=[plugin_candidate(spec)])
    assert guide_violations(root, bootstrap.composition(), spec) == []
