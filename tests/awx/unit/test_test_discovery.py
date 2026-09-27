"""Finding ``awx test`` suites under a directory."""

from __future__ import annotations

from pathlib import Path

from untaped.capabilities.awx.infrastructure.suites.filesystem import suites_under

_SUITE = "kind: AwxTestSuite\nname: {name}\njobTemplate: jt\ncases: {{c: {{}}}}\n"


def test_suites_under_searches_recursively_and_skips_what_is_not_a_suite(
    tmp_path: Path,
) -> None:
    (tmp_path / "web").mkdir()
    (tmp_path / "vars").mkdir()
    (tmp_path / ".cache").mkdir()
    (tmp_path / "b.yml").write_text(_SUITE.format(name="b"))
    (tmp_path / "web" / "a.YAML").write_text(
        "---\nvariables: {env: {}}\n---\n" + _SUITE.format(name="a")
    )
    (tmp_path / "vars" / "prod.yml").write_text("env: prod\n")
    (tmp_path / ".cache" / "c.yml").write_text(_SUITE.format(name="c"))
    (tmp_path / "notes.md").write_text("kind: AwxTestSuite\n")

    assert suites_under(tmp_path) == [tmp_path / "b.yml", tmp_path / "web" / "a.YAML"]


def test_a_hidden_directory_can_itself_be_searched(tmp_path: Path) -> None:
    tests_dir = tmp_path / ".untaped" / "awx" / "tests"
    tests_dir.mkdir(parents=True)
    (tests_dir / "a.yml").write_text(_SUITE.format(name="a"))
    assert suites_under(tests_dir) == [tests_dir / "a.yml"]
