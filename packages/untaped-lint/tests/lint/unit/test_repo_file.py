"""``.untaped/lint.yml``: found walking up, checked before composing, its rules loaded."""

from __future__ import annotations

import os
import py_compile
import sys
from pathlib import Path
from textwrap import dedent

import pytest

from untaped.sdk import ConfigError, UsageError
from untaped.testing import compose_with
from untaped_lint.application import repo_file
from untaped_lint.testing import conformance, repo_rule, run, run_rule, tree

pytestmark = pytest.mark.usefixtures("fresh_composition")

NO_PRINT = '''\
"""No print() in library code."""

from collections.abc import Iterator

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

LONG = '''\
"""A README is short."""

from collections.abc import Iterator

from untaped_lint.api import Finding, Tree, finding, rule, target_of


@rule("short-readme", reads=Tree, severity="warning", summary="A README is short.")
def short_readme(tree: Tree) -> Iterator[Finding]:
    for file in tree.files("README.md"):
        if len(file.lines) > 2:
            yield finding(short_readme, target_of(file), "too long")
'''


def _repo(root: Path, lint_yml: str, files: dict[str, str]) -> Path:
    (root / ".git").mkdir(exist_ok=True)
    for name, body in {".untaped/lint.yml": dedent(lint_yml), **files}.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return root


HOUSE = """\
    version: 1
    name: house
    exclude: ["vendor/**"]
    linters:
      house:
        rules: .untaped/lint
"""


def _house(root: Path, **files: str) -> Path:
    return _repo(
        root,
        HOUSE,
        {".untaped/lint/no_print.py": NO_PRINT, ".untaped/lint/short_readme.py": LONG, **files},
    )


# --- finding and reading the file ---------------------------------------------


def test_the_file_is_found_walking_up_to_the_project_root(tmp_path: Path) -> None:
    _house(tmp_path)
    (tmp_path / "src" / "deep").mkdir(parents=True)
    found = repo_file.find(tmp_path / "src" / "deep")
    assert found == (tmp_path / ".untaped" / "lint.yml").resolve()
    assert repo_file.root_of(found) == tmp_path.resolve()
    assert repo_file.find(tmp_path.parent) is None


def test_two_modules_make_one_host_under_the_files_key(tmp_path: Path) -> None:
    root = _house(tmp_path, **{"src/a.py": "print(1)\n", "README.md": "1\n2\n3\n"})
    with compose_with("lint"):
        report = run(root=root)
    assert sorted((each.rule, each.target) for each in report.findings) == [
        ("house.no-print", "src/a.py"),
        ("house.short-readme", "README.md"),
    ]
    assert {each.source.plugin for each in report.findings if each.source} == {"house"}
    assert not report.failures


def test_exclude_and_the_rules_fixtures_stay_out_of_the_tree(tmp_path: Path) -> None:
    root = _house(
        tmp_path,
        **{
            "src/vendor/a.py": "print(1)\n",
            "vendor/src/b.py": "print(1)\n",
            ".untaped/lint/fixtures/no-print/bad/src/c.py": "print(1)\n",
        },
    )
    with compose_with("lint"):
        report = run(root=root)
    assert [each.target for each in report.findings] == ["src/vendor/a.py"]


def test_the_files_ignore_applies(tmp_path: Path) -> None:
    root = _repo(
        tmp_path,
        HOUSE + "    ignore: ['house.no-print:src/legacy/**']\n",
        {
            ".untaped/lint/no_print.py": NO_PRINT,
            "src/legacy/a.py": "print(1)\n",
            "src/b.py": "print(1)\n",
        },
    )
    with compose_with("lint"):
        report = run(root=root)
    assert [each.target for each in report.findings] == ["src/b.py"]


def test_a_file_whose_rules_read_no_files_says_so(tmp_path: Path) -> None:
    root = _repo(tmp_path, "version: 1\nname: house\n", {})
    with compose_with("lint"):
        report = run(root=root)
    assert report.warnings == [
        "nothing to lint here: no installed rule reads the repository's files; "
        "write one with `untaped lint new`"
    ]


# --- the allow marker --------------------------------------------------------


def test_the_marker_names_the_rule_with_its_key_or_bare(tmp_path: Path) -> None:
    root = _house(
        tmp_path,
        **{
            "src/a.py": "print(1)  # untaped: allow house.no-print\n",
            "src/b.py": "print(1)  # untaped: allow no-print\n",
            "src/c.py": "print(1)  # untaped: allow lint.no-print\n",
            "src/d.py": "\nprint(1)\n",
        },
    )
    with compose_with("lint"):
        report = run(root=root)
    assert sorted(each.target for each in report.findings) == ["src/c.py", "src/d.py"]
    found = {each.target: each for each in report.findings}
    assert found["src/d.py"].location is not None
    assert getattr(found["src/d.py"].location, "line", None) == 2


def test_a_bare_marker_two_keys_declare_warns_and_allows_nothing(tmp_path: Path) -> None:
    root = _repo(
        tmp_path,
        HOUSE + "      shop:\n        rules: .untaped/shop\n",
        {
            ".untaped/lint/no_print.py": NO_PRINT,
            ".untaped/shop/no_print.py": NO_PRINT,
            "src/a.py": "print(1)  # untaped: allow no-print\n",
        },
    )
    with compose_with("lint"):
        report = run(root=root)
    assert sorted(each.rule for each in report.findings) == ["house.no-print", "shop.no-print"]
    assert report.warnings == [
        "`no-print` names house.no-print, shop.no-print; write the one you mean"
    ]


# --- what a rule module may import --------------------------------------------


def test_a_module_importing_outside_the_allowlist_is_an_error_finding(tmp_path: Path) -> None:
    module = (
        "import yaml\nimport json\nfrom untaped.sdk import Record\n"
        "from untaped_git.api import Credential\n"
    )
    root = _house(tmp_path, **{".untaped/lint/extra.py": module})
    with compose_with("lint"):
        report = run(root=root)
    rows = sorted(
        (each.rule, each.target, getattr(each.location, "line", None)) for each in report.findings
    )
    assert rows == [
        ("lint.rule-imports", ".untaped/lint/extra.py", 1),
        ("lint.rule-imports", ".untaped/lint/extra.py", 3),
    ]
    assert not report.failures  # the allowlist is a finding; the module still loads


def test_a_module_importing_a_plugin_that_is_not_installed_fails_the_load(
    tmp_path: Path,
) -> None:
    root = _house(tmp_path, **{".untaped/lint/ghost.py": "from untaped_ghost.api import Ghost\n"})
    with compose_with("lint"), pytest.raises(ConfigError, match=r"install untaped\[ghost\]"):
        run(root=root)


def test_the_allowed_apis_may_be_imported_as_from_package_import_api(tmp_path: Path) -> None:
    module = "from untaped_lint import api\nfrom untaped_lint import records\n"
    root = _house(tmp_path, **{".untaped/lint/spelled.py": module})
    with compose_with("lint"):
        report = run(root=root)
    assert [(each.rule, getattr(each.location, "line", None)) for each in report.findings] == [
        ("lint.rule-imports", 2)
    ]


def test_the_source_checked_is_the_source_run_never_a_stale_pyc(tmp_path: Path) -> None:
    root = _house(tmp_path)
    planted = root / ".untaped" / "lint" / "planted.py"
    planted.write_text("import os\nos.environ['UNTAPED_LINT_PLANTED'] = 'ran'\n")
    cache = planted.parent / "__pycache__" / f"planted.{sys.implementation.cache_tag}.pyc"
    py_compile.compile(
        str(planted),
        cfile=str(cache),
        invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH,
    )
    planted.write_text('"""Harmless."""\n')
    with compose_with("lint"):
        report = run(root=root)
    assert "UNTAPED_LINT_PLANTED" not in os.environ
    assert not report.failures
    assert sorted(path.name for path in cache.parent.iterdir()) == [cache.name]


def test_a_module_runs_as_python_would_import_it_without_lints_future_flags(
    tmp_path: Path,
) -> None:
    module = "def annotated(x: int) -> None: ...\n"
    root = _house(tmp_path, **{".untaped/lint/plain.py": module})
    with compose_with("lint"):
        run(root=root)
    loaded = sys.modules["untaped_lint_repo_rules.house.plain"]
    assert loaded.annotated.__annotations__ == {"x": int, "return": None}


def test_a_module_that_exits_on_import_is_a_failure_row(tmp_path: Path) -> None:
    root = _house(tmp_path, **{".untaped/lint/quits.py": "import sys\nsys.exit(7)\n"})
    with compose_with("lint"):
        report = run(root=root)
    (failure,) = report.failures
    assert "quits.py" in failure.message


def test_a_module_that_raises_is_a_failure_row_and_the_others_still_run(tmp_path: Path) -> None:
    root = _house(
        tmp_path,
        **{".untaped/lint/broken.py": "raise RuntimeError('no')\n", "src/a.py": "print(1)\n"},
    )
    with compose_with("lint"):
        report = run(root=root)
    assert [each.rule for each in report.findings] == ["house.no-print"]
    (failure,) = report.failures
    assert failure.item == "house"
    assert "broken.py" in failure.message


# --- keys ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("lint_yml", "message"),
    [
        ("version: 1\nname: Bad_Key\n", "'Bad_Key' is not a key"),
        ("version: 1\nname: untaped\n", "core's prefix"),
        ("version: 1\nname: house\nlinters:\n  lint:\n    rules: x\n", "installed plugin"),
    ],
)
def test_a_key_that_could_shadow_a_plugin_is_a_usage_error(
    tmp_path: Path, lint_yml: str, message: str
) -> None:
    path = _repo(tmp_path, lint_yml, {}) / ".untaped" / "lint.yml"
    with pytest.raises(UsageError, match=message):
        repo_file.read(path, installed=["lint", "awx"])


@pytest.mark.parametrize(
    ("lint_yml", "message"),
    [
        ("name: house\n", "version"),
        ("version: 2\nname: house\n", "version"),
        ("version: 1\nname: house\nlinters:\n  ruff:\n    run: [ruff]\n", "run: entries"),
        ("version: 1\nname: house\nignore: ['Bad Id']\n", "ignore"),
        ("version: 1\nname: house\nlinters:\n  house:\n    rules: missing\n", "not a directory"),
        ("version: 1\nname: house\nlinters:\n  house:\n    rules: ../shared\n", "outside"),
        ("version: 1\nname: house\nlinters:\n  house:\n    rules: /tmp\n", "outside"),
    ],
)
def test_an_invalid_file_is_a_config_error_naming_the_problem(
    tmp_path: Path, lint_yml: str, message: str
) -> None:
    (tmp_path / "shared").mkdir()
    (tmp_path / "repo").mkdir()
    root = _repo(tmp_path / "repo", lint_yml, {})
    with compose_with("lint"), pytest.raises(ConfigError, match=message) as raised:
        run(root=root)
    assert raised.type is ConfigError


def test_a_readers_key_is_spelled_as_a_plugin_name(tmp_path: Path) -> None:
    path = _repo(tmp_path, "version: 1\nname: house\nreaders:\n  Not A Key: ['*']\n", {})
    with pytest.raises(UsageError, match="'Not A Key' is not a key"):
        repo_file.read(path / ".untaped" / "lint.yml")


# --- testing helpers -----------------------------------------------------------


def test_repo_rule_and_run_rule_test_one_rule_on_a_few_files(tmp_path: Path) -> None:
    rule = repo_rule("no-print", root=_house(tmp_path))
    (found,) = run_rule(rule, tree({"src/a.py": "x = 1\nprint(x)\n"}))
    assert (found.rule, found.target, getattr(found.location, "line", None)) == (
        "no-print",
        "src/a.py",
        2,
    )
    assert run_rule(rule, tree({"src/a.py": "x = 1\n"})) == []
    with pytest.raises(LookupError, match="no rule 'nope'"):
        repo_rule("nope", root=tmp_path)


def test_conformance_fails_a_host_reading_an_undeclared_kind() -> None:
    from untaped_lint.api import Rule, Rules

    class Bad(Rules):
        def rules(self) -> list[Rule]:
            return [Rule(id="x", reads="nobody.kind", severity="error", summary="s")]

    with pytest.raises(AssertionError, match=r"nobody\.kind"):
        conformance(Bad())


def test_conformance_passes_a_tree_reader_which_has_no_rule_table() -> None:
    from untaped_lint.api import Subject, Tree, TreeReader

    class Reader(TreeReader):
        def read(self, tree: Tree) -> list[Subject]:
            return []

    conformance(Reader())
