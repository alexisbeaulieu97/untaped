"""LoadTestSuite use case: file → rendered → validated TestSuite."""

from __future__ import annotations

from pathlib import Path

import pytest
from untaped_awx.application.test.loader import LoadTestSuite
from untaped_awx.application.test.ports import Filesystem
from untaped_awx.domain.test_suite import RefSentinel, VariableSpec
from untaped_awx.errors import AwxApiError
from untaped_awx.infrastructure.test import DefaultParser, resolve_variables


class FakeFilesystem(Filesystem):
    def __init__(self, files: dict[Path, str]) -> None:
        self._files = files

    def read_text(self, path: Path) -> str:
        return self._files[path]


class StubPrompt:
    """Always non-interactive — no test reaches it without explicit values."""

    def is_interactive(self) -> bool:
        return False

    def ask(self, spec: VariableSpec) -> str:  # pragma: no cover — never called
        raise AssertionError("StubPrompt.ask called unexpectedly")


def _load(text: str, **kwargs: object) -> object:
    path = Path("/virtual/test.yml")
    fs = FakeFilesystem({path: text})
    loader = LoadTestSuite(
        fs,
        parser=DefaultParser(),
        vars_resolver=resolve_variables,
        prompt=StubPrompt(),  # type: ignore[arg-type]
    )
    return loader(path, **kwargs)  # type: ignore[arg-type]


def test_loads_minimal_suite_with_no_frontmatter() -> None:
    text = (
        "kind: AwxTestSuite\n"
        "name: deploy-app\n"
        "jobTemplate: Deploy app\n"
        "cases:\n"
        "  one:\n"
        "    launch:\n"
        "      limit: app-prod-*\n"
    )
    suite = _load(text)
    assert suite.name == "deploy-app"  # type: ignore[attr-defined]
    assert "one" in suite.cases  # type: ignore[attr-defined]


def test_jinja2_rendering_with_cli_var() -> None:
    text = (
        "---\n"
        "variables:\n"
        "  env: { type: string }\n"
        "---\n"
        "kind: AwxTestSuite\n"
        "name: deploy-app\n"
        "jobTemplate: Deploy app\n"
        "cases:\n"
        "  c:\n"
        "    launch:\n"
        "      limit: {{ env | to_yaml }}\n"
    )
    suite = _load(text, cli_vars={"env": "prod"})
    assert suite.cases["c"].launch["limit"] == "prod"  # type: ignore[attr-defined]


def test_strict_undefined_on_missing_var() -> None:
    text = (
        "---\n"
        "variables:\n"
        "  env: { type: string }\n"
        "---\n"
        "kind: AwxTestSuite\n"
        "name: x\n"
        "jobTemplate: y\n"
        "cases:\n"
        "  c:\n"
        "    launch:\n"
        "      limit: {{ unknown_var }}\n"
    )
    with pytest.raises(AwxApiError, match="undefined"):
        _load(text, cli_vars={"env": "prod"})


def test_assert_block_non_empty_is_rejected() -> None:
    text = (
        "kind: AwxTestSuite\n"
        "name: x\n"
        "jobTemplate: y\n"
        "cases:\n"
        "  c:\n"
        "    launch: {}\n"
        "    assert:\n"
        "      stdout_contains: ['x']\n"
    )
    with pytest.raises(AwxApiError, match="assert"):
        _load(text)


def test_empty_assert_block_is_allowed() -> None:
    text = (
        "kind: AwxTestSuite\n"
        "name: x\n"
        "jobTemplate: y\n"
        "cases:\n"
        "  c:\n"
        "    launch: {}\n"
        "    assert: {}\n"
    )
    suite = _load(text)
    assert suite.cases["c"].assert_ == {}  # type: ignore[attr-defined]


def test_case_without_launch_is_rejected() -> None:
    text = "kind: AwxTestSuite\nname: x\njobTemplate: y\ncases:\n  c:\n    extra_vars: {x: 1}\n"
    with pytest.raises(AwxApiError, match="launch"):
        _load(text)


def test_missing_kind_is_rejected() -> None:
    text = "name: x\njobTemplate: y\ncases: {c: {launch: {}}}\n"
    with pytest.raises(AwxApiError):
        _load(text)


def test_filename_stem_used_as_default_name() -> None:
    text = "kind: AwxTestSuite\njobTemplate: y\ncases:\n  c:\n    launch: {}\n"
    path = Path("/virtual/deploy-tests.yml")
    fs = FakeFilesystem({path: text})
    loader = LoadTestSuite(
        fs,
        parser=DefaultParser(),
        vars_resolver=resolve_variables,
        prompt=StubPrompt(),  # type: ignore[arg-type]
    )
    suite = loader(path)
    assert suite.name == "deploy-tests"


def test_ref_tag_survives_through_load() -> None:
    text = (
        "kind: AwxTestSuite\n"
        "name: x\n"
        "jobTemplate: y\n"
        "cases:\n"
        "  c:\n"
        "    launch:\n"
        '      inventory: !ref { kind: Inventory, name: "Web Inventory" }\n'
    )
    suite = _load(text)
    inv = suite.cases["c"].launch["inventory"]  # type: ignore[attr-defined]
    assert isinstance(inv, RefSentinel)
    assert inv.kind == "Inventory"
