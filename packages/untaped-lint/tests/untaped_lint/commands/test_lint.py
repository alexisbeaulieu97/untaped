"""``untaped lint``, ``rules`` and ``new``: subjects in, findings out, exit by the table."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from lint_fakes import (
    Acme,
    Gizmo,
    NotAProvider,
    Placing,
    PluginReader,
    Raising,
    Sprocket,
    WarnOnly,
    Widget,
    pipe,
)
from lint_test_support import Result, run
from untaped.sdk import InstalledPlugin, Plugin
from untaped.sdk.contracts import NotReady
from untaped.sdk.health.doctor import PluginContext
from untaped.sdk.testing import PluginCheckRow, install
from untaped_git.plugin import Git
from untaped_lint.api import Rule
from untaped_lint.plugin import LintPlugin
from untaped_lint.settings import LintSettings

WIDGET = pipe(Widget(name="w"))


@pytest.fixture(autouse=True)
def _outside_this_repository(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run where no ``.untaped/lint.yml`` is found: this repository's would add its rules."""
    monkeypatch.chdir(tmp_path)


def _lint(
    *args: str,
    input: str = WIDGET,
    providers: dict[str, list[type]] | None = None,
    settings: LintSettings | None = None,
) -> Result:
    return run(
        ["--stdin", "-f", "json", *args],
        input=input,
        providers=providers if providers is not None else {"acme": [Acme]},
        settings=() if settings is None else settings,
    )


def _rules(result: Result) -> list[str]:
    return sorted(row["rule"] for row in json.loads(result.stdout))


def _warnings(result: Result) -> list[str]:
    return [
        f"warning: {each.message}"
        for each in result.ran.diagnostics
        if getattr(each, "level", None) == "warning"
    ]


# --- exit codes -------------------------------------------------------------


def test_an_error_finding_exits_3() -> None:
    result = _lint()
    assert result.exit_code == 3
    assert _rules(result) == ["acme.tall", "acme.wide"]
    assert "lint: 1 error, 1 warning" in result.stderr


def test_a_table_shows_each_target_and_its_location_as_a_file_line() -> None:
    result = run(
        ["--stdin", "-c", "rule,target,location"], input=WIDGET, providers={"acme": [Placing]}
    )
    rows = [[cell.strip() for cell in line.split("│")[1:-1]] for line in result.stdout.splitlines()]
    assert [row for row in rows if row[:1] == ["acme.placed"]] == [
        ["acme.placed", "w", "src/a.py:1"],
        ["acme.placed", "w", "src/a.py:2"],
    ]


def test_warnings_only_exit_0_and_3_with_strict() -> None:
    assert _lint(providers={"acme": [WarnOnly]}).exit_code == 0
    assert _lint("--strict", providers={"acme": [WarnOnly]}).exit_code == 3


def test_a_failure_row_exits_1_even_with_error_findings() -> None:
    result = _lint(providers={"acme": [Raising]})
    assert result.exit_code == 1
    assert _rules(result) == ["acme.wide"]
    assert "acme.boom" in result.stderr


@pytest.mark.parametrize(
    ("args", "input"),
    [
        (["src/*.py"], WIDGET),
        ([], pipe(Widget(name="w"), kind=None)),
        (["--select", "Not An Id"], WIDGET),
        (["--ignore", "acme.wide:"], WIDGET),
    ],
    ids=["stdin-with-paths", "kind-less-record", "bad-select", "bad-ignore"],
)
def test_usage_mistakes_exit_2(args: list[str], input: str) -> None:
    result = _lint(*args, input=input)
    assert result.exit_code == 2, result.stderr
    assert not result.stdout


def test_bare_values_on_stdin_are_a_usage_mistake() -> None:
    result = _lint(input="w\n")
    assert result.exit_code == 2, result.stderr
    assert not result.stdout
    assert "--stdin reads records: pipe untaped's --format pipe output" in result.stderr


class ShopSettings(BaseModel):
    """Nothing to configure."""


class Shop(Plugin):
    """Fills lint's ``Linter`` with a class that is no ``Provider``: composition quarantines it."""

    settings = ShopSettings
    fills = (NotAProvider,)


def test_a_quarantined_plugin_offers_no_rules_and_composition_says_why() -> None:
    result = run(["--stdin", "-f", "json"], input=WIDGET, plugins=[Shop])
    assert result.exit_code == 0, result.stderr
    assert any("plugin 'shop'" in each and "quarantined" in each for each in _warnings(result))
    assert _warnings(result)[-1] == (
        "warning: no installed rule reads linttest.widget records; see `untaped lint rules`"
    )


def test_a_provider_that_is_not_ready_says_so_when_it_is_the_only_one() -> None:
    class Lazy(Acme):
        def ready(self) -> NotReady:
            return NotReady("needs a token")

    result = _lint(providers={"acme": [Lazy]})
    assert result.exit_code == 0
    assert "warning: acme's rules did not run: needs a token" in _warnings(result)


# --- suppression ------------------------------------------------------------


def test_the_profile_ignore_flags_and_select_add_up() -> None:
    widgets = pipe(Widget(name="legacy-a"), Widget(name="b"))
    result = _lint(
        "--ignore",
        "acme.wide:legacy-*",
        "--ignore",
        "nope",
        "--select",
        "acme.wide",
        "--select",
        "acme.tall",
        input=widgets,
        providers={"acme": [Acme], "gizmo": [Gizmo]},
        settings=LintSettings(ignore=["acme.tall", "nope"]),
    )
    assert [(row["rule"], row["target"]) for row in json.loads(result.stdout)] == [
        ("acme.wide", "b")
    ]
    assert _warnings(result) == ["warning: `nope`: no installed rule; is its plugin installed?"]


def test_doctor_warns_about_an_ignored_id_no_rule_declares() -> None:
    check = LintPlugin.ignore_ids
    with install(LintPlugin, providers={"acme": [Acme]}).running():
        known = check.run(PluginContext(settings=LintSettings(ignore=["acme.wide"])))
        unknown = check.run(PluginContext(settings=LintSettings(ignore=["acme.wide", "nope"])))
    assert (known.ok, known.warn) == (True, False)
    assert (unknown.ok, unknown.warn) == (True, True)
    assert "nope" in unknown.detail


# --- subjects ---------------------------------------------------------------


def test_a_kind_no_rule_reads_warns_naming_lint_rules() -> None:
    result = _lint(input=pipe(Sprocket(name="s"), Sprocket(name="t"), kind="linttest.sprocket"))
    assert result.exit_code == 0
    assert _warnings(result) == [
        "warning: no installed rule reads linttest.sprocket records; see `untaped lint rules`"
    ]


def _project(path: Path, **plugins: str) -> None:
    entries = "".join(f'{name} = "{target}"\n' for name, target in plugins.items())
    path.joinpath("pyproject.toml").write_text(
        f'[project]\nname = "x"\n\n[project.entry-points."untaped.plugins"]\n{entries}',
        encoding="utf-8",
    )


def test_in_a_plugin_repository_its_plugins_are_checked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An entry point whose plugin is not installed is a finding, never a subject."""
    _project(tmp_path, lint="untaped_lint.plugin:LintPlugin", ghost="untaped_ghost.plugin:Ghost")
    (tmp_path / ".git").mkdir()
    (tmp_path / "src").mkdir()
    monkeypatch.chdir(tmp_path / "src")
    PluginReader.read.clear()
    result = run(["-f", "json"], providers={"acme": [PluginReader]})
    assert result.exit_code == 3, result.stderr
    rows = json.loads(result.stdout)
    assert [(row["rule"], row["target"]) for row in rows if row["rule"].startswith("lint.")] == [
        ("lint.plugin-not-installed", "ghost")
    ]
    assert [plugin.name for plugin in PluginReader.read] == ["lint"]


def test_nothing_to_lint_warns_and_exits_0(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = run([])
    assert result.exit_code == 0
    assert _warnings(result) == [
        "warning: nothing to lint here: run it in a plugin repository, a repository with "
        ".untaped/lint.yml, or pipe records with --stdin"
    ]


def test_a_malformed_ignore_is_a_usage_error_even_with_nothing_to_lint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    result = run(["--ignore", "Bad..x"])
    assert result.exit_code == 2


def test_piped_plugin_list_checks_each_plugin() -> None:
    """``untaped plugin list -f pipe | untaped lint --stdin``, the list cut to two plugins
    and untaped's own row, which lint skips."""
    listed = install(LintPlugin, Git).cli(["plugin", "list", "-f", "pipe"]).stdout.splitlines()
    wanted = ("git", "lint", "untaped")
    piped = [line for line in listed if json.loads(line)["record"]["name"] in wanted]
    assert len(piped) == 3
    PluginReader.read.clear()
    result = run(
        ["--stdin", "-f", "json"],
        input="\n".join(piped) + "\n",
        providers={"acme": [PluginReader]},
        plugins=[Git],
    )
    assert result.exit_code == 0, result.stderr
    assert sorted(plugin.name for plugin in PluginReader.read) == ["git", "lint"]
    assert _rules(result) == ["acme.seen", "acme.seen"]


def test_an_invalid_plugin_record_stops_the_read_before_any_plugin_is_checked() -> None:
    listed = install(LintPlugin, Git).cli(["plugin", "list", "-f", "pipe"]).stdout.splitlines()
    rows = [json.loads(line) for line in listed if json.loads(line)["record"]["name"] == "git"]
    broken = {**rows[0], "record": {**rows[0]["record"], "name": "broken", "status": 7}}
    PluginReader.read.clear()
    result = run(
        ["--stdin", "-f", "json"],
        input="\n".join(json.dumps(row) for row in (broken, rows[0])) + "\n",
        providers={"acme": [PluginReader]},
        plugins=[Git],
    )
    assert result.exit_code == 1
    assert PluginReader.read == []
    assert "line 1: status:" in result.stderr


def test_a_kind_no_installed_plugin_declares_is_refused() -> None:
    piped = json.dumps({"untaped": "1", "kind": "elsewhere.thing", "record": {"id": 1}})
    result = _lint(input=piped + "\n")
    assert result.exit_code == 1
    assert "no installed plugin declares the kind 'elsewhere.thing'" in result.stderr


def test_an_empty_pipe_says_nothing_was_received() -> None:
    result = _lint(input="")
    assert result.exit_code == 1
    assert "no identifiers received on stdin" in result.stderr


def test_a_rule_over_plugins_reads_the_record_after_conventions_ran(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Conventions composes, so it runs before any provider is asked; the provider gets a record."""
    import untaped_lint.providers.conventions as conventions

    events: list[str] = []
    check_plugin = conventions.check_plugin

    def spied(name: str, **kwargs: Any) -> list[PluginCheckRow]:
        events.append("conventions")
        return check_plugin(name, **kwargs)

    class Reader(PluginReader):
        def rules(self) -> list[Rule]:
            events.append("rules")
            return super().rules()

    monkeypatch.setattr(conventions, "check_plugin", spied)
    listed = json.loads(install(LintPlugin).cli(["plugin", "list", "-f", "json"]).stdout)
    lint = InstalledPlugin.model_validate(next(each for each in listed if each["name"] == "lint"))
    PluginReader.read.clear()
    result = _lint(input=pipe(lint, kind="untaped.installed_plugin"), providers={"acme": [Reader]})
    assert result.exit_code == 0, result.stderr
    assert events[:2] == ["conventions", "rules"]
    assert PluginReader.read == [lint]


# --- lint rules -------------------------------------------------------------


def test_lint_rules_lists_every_rule_and_composes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import untaped_lint.providers.conventions as conventions

    def never(*args: object, **kwargs: object) -> None:
        raise AssertionError("lint rules ran a plugin check")

    monkeypatch.setattr(conventions, "check_plugin", never)
    monkeypatch.chdir(tmp_path)
    result = run(["rules", "-f", "json"], providers={"acme": [Gizmo]})
    assert result.exit_code == 0, result.stderr
    rows = {row["id"]: row for row in json.loads(result.stdout)}
    assert {"lint.error-case", "lint.plugin-not-installed", "acme.default-branch"} <= set(rows)
    assert rows["acme.default-branch"]["reads"] == "linttest.gadget"
    assert rows["acme.default-branch"]["renamed_from"] == ["old"]


# --- the repository's lint file ---------------------------------------------------

NO_PRINT = '''\
"""No print() in library code."""


from untaped_lint.api import FileLocation, Finding, Tree, finding, rule, target_of


@rule("no-print", reads=Tree, severity="error", summary="No print() in library code.")
def no_print(tree: Tree) -> Iterator[Finding]:
    for file in tree.files("src/**/*.py"):
        for number, line in enumerate(file.lines, start=1):
            if "print(" in line:
                yield finding(
                    no_print, target_of(file), "print() in library code",
                    location=FileLocation(path=file.path, line=number),
                )
'''


def _house(root: Path, lint_yml: str | None = None, **files: str) -> Path:
    (root / ".git").mkdir(exist_ok=True)
    text = lint_yml or "version: 1\nname: house\nlinters:\n  house:\n    rules: .untaped/lint\n"
    for name, body in {
        ".untaped/lint.yml": text,
        ".untaped/lint/no_print.py": NO_PRINT,
        **files,
    }.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(body, encoding="utf-8")
    return root


def test_in_a_repository_with_a_lint_file_its_rules_run_from_any_subdirectory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _house(tmp_path, **{"src/a.py": "print(1)\n", "src/deep/b.py": "x = 1\n"})
    monkeypatch.chdir(tmp_path / "src" / "deep")
    result = run(["-f", "json"])
    assert result.exit_code == 3, result.stderr
    assert [(row["rule"], row["target"]) for row in json.loads(result.stdout)] == [
        ("house.no-print", "src/a.py")
    ]
    rules = run(["rules", "-f", "json"])
    assert "house.no-print" in {row["id"] for row in json.loads(rules.stdout)}


def test_config_lints_the_current_directory_with_that_files_rules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _house(
        tmp_path,
        **{
            "src/a.py": "print(1)\n",
            ".untaped/lint/fixtures/no-print/bad/src/x.py": "print(1)\n",
            ".untaped/lint/fixtures/no-print/good/src/x.py": "x = 1\n",
        },
    )
    bad = tmp_path / ".untaped/lint/fixtures/no-print/bad"
    monkeypatch.chdir(bad)
    result = run(["--config", "../../../../lint.yml", "--select", "house.no-print", "-f", "json"])
    assert result.exit_code == 3, result.stderr
    assert [row["target"] for row in json.loads(result.stdout)] == ["src/x.py"]
    monkeypatch.chdir(bad.parent / "good")
    result = run(["--config", "../../../../lint.yml"])
    assert result.exit_code == 0, result.stderr


@pytest.mark.parametrize(
    ("lint_yml", "files", "code"),
    [
        ("version: 1\nname: untaped\n", {}, 2),
        ("version: 1\nname: house\nlinters:\n  lint:\n    rules: .untaped/lint\n", {}, 2),
        ("version: 1\nname: house\nlinters: [1]\n", {}, 1),
        (None, {".untaped/lint/ghost.py": "import untaped_ghost.api\n"}, 4),
    ],
    ids=["reserved-key", "installed-key", "invalid-file", "missing-plugin"],
)
def test_a_lint_file_problem_exits_by_its_category(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    lint_yml: str | None,
    files: dict[str, str],
    code: int,
) -> None:
    _house(tmp_path, lint_yml, **files)
    monkeypatch.chdir(tmp_path)
    result = run([])
    assert result.exit_code == code, result.stderr


def test_lint_new_writes_the_rule_and_says_what_to_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    result = run(["new", "no-print", "--key", "house", "-f", "json"])
    assert result.exit_code == 0, result.stderr
    rows = json.loads(result.stdout)
    assert [Path(row["target_path"]).name for row in rows] == [
        "no_print.py",
        "test_no_print.py",
        "lint.yml",
    ]
    assert "untaped lint --select house.no-print" in result.stderr
    again = run(["--select", "house.no-print", "-f", "json"])
    assert again.exit_code == 0, again.stdout
