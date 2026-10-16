"""``untaped lint new``: a rule module and its test, which fails until the rule is written."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

from untaped.sdk import UntapedError, UsageError
from untaped_lint.application.scaffold import scaffold

pytestmark = pytest.mark.usefixtures("fresh_composition")


def _checkout(root: Path, *, pytest_config: bool) -> Path:
    (root / ".git").mkdir()
    if pytest_config:
        (root / "pyproject.toml").write_text("[tool.pytest.ini_options]\n", encoding="utf-8")
    return root


def _written(root: Path, outcome: object) -> list[str]:
    return [
        Path(each.target_path).relative_to(root).as_posix()
        for each in outcome.files  # type: ignore[attr-defined]
    ]


def _test_module(path: Path) -> object:
    spec = importlib.util.spec_from_file_location(f"scaffolded_{path.stem}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_with_pytest_the_rule_and_its_test_and_a_new_lint_file(tmp_path: Path) -> None:
    root = _checkout(tmp_path, pytest_config=True)
    outcome = scaffold(root, "no-print", key="house")
    assert _written(root, outcome) == [
        ".untaped/lint/no_print.py",
        "tests/lint/test_no_print.py",
        ".untaped/lint.yml",
    ]
    assert [each.action for each in outcome.files] == ["created"] * 3
    assert yaml.safe_load((root / ".untaped/lint.yml").read_text()) == {
        "version": 1,
        "name": "house",
        "linters": {"house": {"rules": ".untaped/lint"}},
    }
    module = (root / ".untaped/lint/no_print.py").read_text()
    assert "reads=Tree" in module
    assert "lint.tree" not in module  # the model, never a kind string


def test_the_written_test_fails_until_the_rule_is_written(tmp_path: Path) -> None:
    root = _checkout(tmp_path, pytest_config=True)
    scaffold(root, "no-print", key="house")
    test_path = root / "tests/lint/test_no_print.py"
    written = _test_module(test_path)
    with pytest.raises(AssertionError):
        written.test_no_print_flags_the_bad_example()  # type: ignore[attr-defined]
    written.test_no_print_passes_the_good_example()  # type: ignore[attr-defined]

    rule = root / ".untaped/lint/no_print.py"
    rule.write_text(
        rule.read_text().replace("    return False", '    return "breaks" in line'),
        encoding="utf-8",
    )
    written = _test_module(test_path)
    written.test_no_print_flags_the_bad_example()  # type: ignore[attr-defined]
    written.test_no_print_passes_the_good_example()  # type: ignore[attr-defined]


def test_without_pytest_a_fixture_pair_and_the_commands_that_check_it(tmp_path: Path) -> None:
    root = _checkout(tmp_path, pytest_config=False)
    outcome = scaffold(root, "no-print", key="house")
    assert _written(root, outcome) == [
        ".untaped/lint/no_print.py",
        ".untaped/lint/fixtures/no-print/bad/example.txt",
        ".untaped/lint/fixtures/no-print/good/example.txt",
        ".untaped/lint.yml",
    ]
    assert outcome.next_steps == [
        "the bad example exits 3: (cd .untaped/lint/fixtures/no-print/bad && "
        "untaped lint --config ../../../../lint.yml --select house.no-print)",
        "the good example exits 0: (cd .untaped/lint/fixtures/no-print/good && "
        "untaped lint --config ../../../../lint.yml --select house.no-print)",
    ]


def test_an_existing_file_gains_the_entry_and_keeps_its_text(tmp_path: Path) -> None:
    root = _checkout(tmp_path, pytest_config=True)
    lint_yml = root / ".untaped" / "lint.yml"
    lint_yml.parent.mkdir()
    lint_yml.write_text(
        "# ours\nversion: 1\nname: house\nlinters:\n  shop:\n    rules: .untaped/shop\n"
    )
    (root / ".untaped" / "shop").mkdir()
    outcome = scaffold(root, "no-print")
    assert outcome.files[-1].action == "updated"
    text = lint_yml.read_text()
    assert text.startswith("# ours\n")
    assert yaml.safe_load(text)["linters"] == {
        "house": {"rules": ".untaped/lint"},
        "shop": {"rules": ".untaped/shop"},
    }
    again = scaffold(root, "no-tabs")
    assert _written(root, again) == [".untaped/lint/no_tabs.py", "tests/lint/test_no_tabs.py"]


def test_dry_run_writes_nothing(tmp_path: Path) -> None:
    root = _checkout(tmp_path, pytest_config=True)
    outcome = scaffold(root, "no-print", key="house", dry_run=True)
    assert {each.action for each in outcome.files} == {"planned"}
    assert not (root / ".untaped").exists()


def test_the_key_and_id_are_checked(tmp_path: Path) -> None:
    root = _checkout(tmp_path, pytest_config=True)
    with pytest.raises(UsageError, match="--key"):
        scaffold(root, "no-print")
    with pytest.raises(UsageError, match="not a rule id"):
        scaffold(root, "No_Print", key="house")
    with pytest.raises(UsageError, match="installed plugin"):
        scaffold(root, "no-print", key="lint", installed=["lint"])
    scaffold(root, "no-print", key="house")
    with pytest.raises(UntapedError, match="already exists"):
        scaffold(root, "no-print")
