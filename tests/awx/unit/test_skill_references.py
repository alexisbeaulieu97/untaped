"""The untaped-awx skill's suite reference and examples cannot drift from the suite models.

- Every ``examples/*.yml`` loads through the real suite loader (header,
  Jinja2, ``!ref``, validation) and resolves to launch payloads made only of
  fields AWX's launch endpoint knows.
- Every field of the suite models has a ``Field(description=…)`` (which also
  feeds ``untaped awx schema AwxTestSuite``) and is named in
  ``references/test-suites.md``.
"""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel

from untaped.capabilities.awx import SPEC
from untaped.capabilities.awx.application.suites.loader import LoadTestSuite
from untaped.capabilities.awx.application.suites.resolver import ResolveCasePayload
from untaped.capabilities.awx.domain.suite import (
    Case,
    Expectation,
    LogExpectation,
    Suite,
    VariableSpec,
)
from untaped.capabilities.awx.infrastructure.catalog import AwxResourceCatalog
from untaped.capabilities.awx.infrastructure.specs import JOB_TEMPLATE_SPEC
from untaped.capabilities.awx.infrastructure.suites import (
    DefaultParser,
    LocalFilesystem,
    UiPrompt,
    resolve_variables,
)

(_SKILL,) = SPEC.skills
SKILL_DIR = _SKILL.source
EXAMPLES = sorted((SKILL_DIR / "examples").glob("*.yml"))
SUITE_MODELS: tuple[type[BaseModel], ...] = (Suite, Case, Expectation, LogExpectation, VariableSpec)


class _AnyId:
    """Resolves every name to id 1: the examples' names exist on no controller."""

    def name_to_id(self, kind: str, name: str, *, scope: dict[str, str] | None = None) -> int:
        return 1


def _load(path: Path) -> Suite:
    loader = LoadTestSuite(
        LocalFilesystem(),
        parser=DefaultParser(),
        vars_resolver=resolve_variables,
        prompt=UiPrompt(force_non_interactive=True),
    )
    return loader(path)


def test_the_skill_ships_the_examples_its_manual_names() -> None:
    assert [path.name for path in EXAMPLES] == ["negative.yml", "smoke.yml", "variants.yml"]


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda path: path.name)
def test_example_loads_and_resolves_to_known_launch_fields(path: Path) -> None:
    suite = _load(path)
    resolver = ResolveCasePayload(_AnyId(), catalog=AwxResourceCatalog())

    with warnings.catch_warnings():
        warnings.simplefilter("error")  # an unknown launch field warns
        payloads = [
            resolver(JOB_TEMPLATE_SPEC, case, defaults=suite.defaults)
            for case in suite.cases.values()
        ]

    assert payloads


def _fields() -> list[tuple[str, str, Any]]:
    return [
        (model.__name__, field.alias or name, field)
        for model in SUITE_MODELS
        for name, field in model.model_fields.items()
    ]


@pytest.mark.parametrize(
    ("model", "key", "field"), _fields(), ids=lambda value: value if isinstance(value, str) else ""
)
def test_every_suite_field_is_described_and_documented(model: str, key: str, field: Any) -> None:
    reference = (SKILL_DIR / "references" / "test-suites.md").read_text(encoding="utf-8")

    assert field.description, f"{model}.{key} needs Field(description=...)"
    assert f"`{key}`" in reference, f"references/test-suites.md does not name {model}.{key}"
