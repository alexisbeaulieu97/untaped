"""awx's lint providers: its rules for ``untaped lint``, and the repository reader."""

from __future__ import annotations

from pathlib import Path

import pytest

from awx_lint_support import CLEAN, DOCUMENTS
from untaped.sdk.testing import assert_fills, compose_with
from untaped_awx.api import Resources
from untaped_awx.domain.lint.rules import RULES
from untaped_awx.providers.lint import AwxDocuments, AwxRules
from untaped_awx.testing import resources
from untaped_lint.api import Tree
from untaped_lint.testing import conformance, run

pytestmark = pytest.mark.usefixtures("fresh_composition")


def _repository(root: Path, files: dict[str, str | bytes]) -> Path:
    (root / ".git").mkdir()
    (root / ".untaped").mkdir()
    (root / ".untaped" / "lint.yml").write_text(
        "version: 1\nname: house\nreaders:\n  awx: ['specs/**/*.yml']\n", encoding="utf-8"
    )
    for name, text in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(text, bytes):
            path.write_bytes(text)
        else:
            path.write_text(text, encoding="utf-8")
    return root


def test_the_rule_table_conforms() -> None:
    conformance(AwxRules())
    assert [each.id for each in AwxRules().rules()] == [each.rule.id for each in RULES]


def test_awx_fills_both_lint_contracts() -> None:
    assert_fills(AwxRules)
    assert_fills(AwxDocuments)


def test_the_rules_run_over_an_awx_subject() -> None:
    found = AwxRules().findings(resources(DOCUMENTS))
    assert sorted(each.rule for each in found) == [
        "branch-override-ignored",
        "ignored-launch-field",
        "survey-default-missing",
    ]


def test_the_reader_is_the_offline_loader(tmp_path: Path) -> None:
    root = _repository(tmp_path, {"specs/a.yml": DOCUMENTS, "specs/b.yml": CLEAN})
    (read,) = AwxDocuments().read(Tree(root=root, paths=("specs/a.yml", "specs/b.yml")))
    assert isinstance(read, Resources)
    assert read.loaded == ("Project", "Inventory", "JobTemplate", "Schedule")
    assert {each.location.path for each in read.items} == {"specs/a.yml", "specs/b.yml"}  # type: ignore[union-attr]


def test_an_empty_tree_reads_no_subject(tmp_path: Path) -> None:
    assert AwxDocuments().read(Tree(root=tmp_path, paths=())) == []


def test_a_repository_lints_its_documents_through_the_reader(tmp_path: Path) -> None:
    broken = (
        "kind: JobTemplate\nmetadata: {name: x, organization: Platform}\nspec: {project: nope}\n"
    )
    root = _repository(
        tmp_path,
        {
            "specs/templates.yml": DOCUMENTS,
            "specs/broken.yml": broken + "---\n[1]\n",
            "specs/binary.yml": b"\xff\xfe not text",
            "other/ignored.yml": "kind: Nope\n",
        },
    )
    with compose_with("awx", "lint"):
        report = run(root=root)
    found = sorted(
        (each.rule, each.target) for each in report.findings if each.rule.startswith("awx.")
    )
    assert ("awx.missing-reference", "JobTemplate/Platform/x") in found
    assert ("awx.invalid-document", "specs/broken.yml") in found
    assert ("awx.ignored-launch-field", "Schedule/Platform/nightly") in found
    assert not [each for each in found if "other/" in each[1]]
    assert not report.failures


def test_an_undecodable_file_is_for_lint_to_report(tmp_path: Path) -> None:
    root = _repository(tmp_path, {"specs/binary.yml": b"\xff\xfe not text"})
    with compose_with("awx", "lint"):
        report = run(root=root)
    assert [each.rule for each in report.findings if "binary" in each.target] == [
        "lint.unreadable-file"
    ]
