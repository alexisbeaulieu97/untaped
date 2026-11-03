"""Tests for recipe schema validation and input resolution."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from untaped_recipe.domain.recipe import (
    CopyStep,
    InputSpec,
    RecipeFile,
    RemoveStep,
    TemplateStep,
    TransformStep,
    ValidateStep,
)
from untaped_values.api import Input, coerce


def test_recipe_schema_accepts_all_v1_step_types() -> None:
    recipe = RecipeFile.model_validate(
        {
            "version": 1,
            "description": "Demo recipe.",
            "inputs": {
                "service": {"type": "str", "required": True},
                "replicas": {"type": "int", "default": 2},
            },
            "steps": [
                {"type": "validate", "hook": "has_pyproject"},
                {
                    "type": "transform",
                    "file": "pyproject.toml",
                    "hook": "bump_version",
                    "args": {"version": "1.2.0"},
                },
                {"type": "template", "template": "templates/config.yml", "dest": "config.yml"},
                {"type": "copy", "source": "files/README.md", "dest": "README.md"},
                {"type": "remove", "file": "legacy.yml"},
            ],
        }
    )

    assert recipe.version == 1
    assert isinstance(recipe.steps[0], ValidateStep)
    assert isinstance(recipe.steps[1], TransformStep)
    assert isinstance(recipe.steps[2], TemplateStep)
    assert isinstance(recipe.steps[3], CopyStep)
    assert isinstance(recipe.steps[4], RemoveStep)
    assert recipe.inputs["replicas"].default == 2
    assert recipe.steps[2].unknown_tokens == "error"
    assert recipe.steps[2].if_absent is False
    assert recipe.steps[3].if_absent is False


@pytest.mark.parametrize(
    "step",
    [
        {"type": "transform", "file": "local.yml", "files": ["site.yml"], "hook": "edit"},
        {"type": "transform", "file": "local.yml", "globs": ["*.yml"], "hook": "edit"},
        {"type": "transform", "files": ["local.yml"], "globs": ["*.yml"], "hook": "edit"},
        {"type": "transform", "hook": "edit"},
        {"type": "remove", "file": "ansible.cfg", "files": ["old.cfg"]},
        {"type": "remove", "file": "ansible.cfg", "globs": ["*.cfg"]},
        {"type": "remove", "files": ["ansible.cfg"], "globs": ["*.cfg"]},
        {"type": "remove"},
        {"type": "remove", "file": None},
    ],
)
def test_file_fanout_steps_require_exactly_one_of_file_files_or_globs(
    step: dict[str, object],
) -> None:
    with pytest.raises(ValidationError, match="exactly one of file, files, or globs"):
        RecipeFile.model_validate({"version": 1, "name": "bad", "steps": [step]})


@pytest.mark.parametrize(
    ("step", "message"),
    [
        (
            {"type": "transform", "file": "local.yml", "exclude": ["skip.yml"], "hook": "edit"},
            "exclude is only valid with globs",
        ),
        (
            {"type": "remove", "file": "old.yml", "exclude": ["skip.yml"]},
            "exclude is only valid with globs",
        ),
        (
            {"type": "transform", "globs": ["*.yml"], "optional": True, "hook": "edit"},
            "optional is not valid with globs",
        ),
        (
            {"type": "transform", "globs": ["*.yml"], "optional": False, "hook": "edit"},
            "optional is not valid with globs",
        ),
        ({"type": "transform", "files": [], "hook": "edit"}, "files must not be empty"),
        ({"type": "remove", "files": []}, "files must not be empty"),
        ({"type": "transform", "globs": [], "hook": "edit"}, "globs must not be empty"),
        ({"type": "remove", "globs": []}, "globs must not be empty"),
        (
            {"type": "template", "template": "t.yml", "dest": "t.yml", "unknown_tokens": "pass"},
            "unknown_tokens",
        ),
    ],
)
def test_steps_reject_invalid_options(step: dict[str, object], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        RecipeFile.model_validate({"version": 1, "name": "bad", "steps": [step]})


def test_recipe_rejects_unknown_version_and_step_type() -> None:
    with pytest.raises(ValidationError, match="version"):
        RecipeFile.model_validate({"version": 2, "name": "bad", "steps": []})

    with pytest.raises(ValidationError, match="shell"):
        RecipeFile.model_validate(
            {"version": 1, "name": "bad", "steps": [{"type": "shell", "command": "echo no"}]}
        )


def test_input_spec_is_the_values_input_with_recipes_own_fields() -> None:
    spec = InputSpec.model_validate({"type": "int", "default": "3"})

    assert isinstance(spec, Input)
    assert coerce(spec, "4") == 4
    assert not spec.is_required()  # no default and no `required: true` leaves it unset
    assert not InputSpec().is_required()
    assert InputSpec(required=True).is_required()


def test_input_spec_rejects_a_required_input_with_a_default() -> None:
    with pytest.raises(ValidationError, match="required input cannot declare a default"):
        InputSpec.model_validate({"type": "str", "required": True, "default": "x"})


def test_input_spec_refuses_the_old_sensitive_spelling() -> None:
    with pytest.raises(ValidationError, match="sensitive"):
        InputSpec.model_validate({"type": "str", "sensitive": True})


def test_input_spec_supports_metadata_scope_and_from_fallbacks() -> None:
    spec = InputSpec.model_validate(
        {
            "type": "str",
            "description": "Service name.",
            "required": True,
            "from": ["{{ record.repo }}", "{{ target.name }}"],
            "secret": True,
        }
    )

    assert spec.description == "Service name."
    assert spec.scope == "target"
    assert spec.from_ == ("{{ record.repo }}", "{{ target.name }}")
    assert spec.secret is True
    assert InputSpec.model_validate({"type": "str"}).scope == "global"


def test_input_spec_rejects_from_on_global_scope_and_unknown_fields() -> None:
    with pytest.raises(ValidationError, match=r"scope.*global.*from"):
        InputSpec.model_validate({"scope": "global", "from": "{{ target.name }}"})

    with pytest.raises(ValidationError, match="extra_forbidden"):
        InputSpec.model_validate({"type": "str", "form": "{{ target.name }}"})

    with pytest.raises(ValidationError, match="extra_forbidden"):
        InputSpec.model_validate({"type": "str", "from_": "{{ target.name }}"})


@pytest.mark.parametrize(
    "step",
    [
        {"type": "template", "template": "../template.txt", "dest": "out.txt"},
        {"type": "template", "template": "template.txt", "dest": "../out.txt"},
        {"type": "copy", "source": "/tmp/source.txt", "dest": "out.txt"},
        {"type": "copy", "source": "source.txt", "dest": "/tmp/out.txt"},
        {"type": "transform", "file": "../config.yml", "hook": "edit"},
        {"type": "transform", "files": ["local.yml", "../config.yml"], "hook": "edit"},
        {"type": "remove", "file": "/tmp/config.yml"},
        {"type": "remove", "files": ["old.yml", "/tmp/config.yml"]},
    ],
)
def test_recipe_rejects_paths_that_escape_recipe_or_target(step: dict[str, object]) -> None:
    with pytest.raises(ValidationError, match="safe relative path"):
        RecipeFile.model_validate({"version": 1, "name": "bad", "steps": [step]})
