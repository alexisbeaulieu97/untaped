"""Structure rules read from a tmp plugin's files, one rule broken at a time."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from untaped_lint.infrastructure.files import source_files
from untaped_lint.providers.conventions.structure import structure_source_violations

_CLEAN = {
    "acme/__init__.py": '"""Acme plugin."""\n',
    "acme/errors.py": '''
        """Acme errors."""

        from untaped.sdk import UntapedError


        class AcmeError(UntapedError):
            """An acme failure."""

            system = "acme"
        ''',
    "acme/cli.py": '''
        """Acme commands."""

        from untaped.sdk import get_config_section


        def settings() -> object:
            return get_config_section("acme")
        ''',
}


def _violations(tmp_path: Path, files: dict[str, str]) -> list[str]:
    """Source structure violations of the plugin ``acme`` written from ``files``."""
    for name, body in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dedent(body).lstrip(), encoding="utf-8")
    source_dir = tmp_path / "acme"
    return structure_source_violations(
        "acme",
        "acme",
        source_dir,
        list(source_files(source_dir)),
        tests_dir=tmp_path / "acme_tests",
    )


def test_a_clean_package_has_no_violations(tmp_path: Path) -> None:
    assert _violations(tmp_path, _CLEAN) == []


def test_a_package_without_errors_module_is_flagged(tmp_path: Path) -> None:
    files = {name: body for name, body in _CLEAN.items() if name != "acme/errors.py"}
    assert _violations(tmp_path, files) == ["acme/errors.py::errors-module"]


def test_reading_another_plugin_section_is_flagged(tmp_path: Path) -> None:
    cli = _CLEAN["acme/cli.py"].replace('"acme"', '"github"')
    assert _violations(tmp_path, {**_CLEAN, "acme/cli.py": cli}) == [
        "acme/cli.py::foreign-section::github"
    ]


def test_tests_importing_private_names_are_flagged(tmp_path: Path) -> None:
    tests = {
        "acme_tests/test_acme.py": """
            import untaped._internal.tool
            from acme._parse import parse
            from untaped.sdk import _hidden
            from acme.cli import _helper  # untaped: allow private-test-import
            from other._private import thing
            from acme import __version__
            """,
    }
    assert _violations(tmp_path, {**_CLEAN, **tests}) == [
        "acme_tests/test_acme.py::private-test-import::untaped._internal.tool",
        "acme_tests/test_acme.py::private-test-import::acme._parse:_parse",
        "acme_tests/test_acme.py::private-test-import::untaped.sdk:_hidden",
    ]
