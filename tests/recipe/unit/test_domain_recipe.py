"""Tests for recipe schema validation and input resolution."""

from __future__ import annotations

import traceback

import pytest
from pydantic import ValidationError

from untaped.capabilities.recipe.domain.recipe import (
    CopyStep,
    InputSpec,
    Recipe,
    RemoveStep,
    TemplateStep,
    TransformStep,
    ValidateStep,
)


def test_recipe_schema_accepts_all_v1_step_types() -> None:
    recipe = Recipe.model_validate(
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
        Recipe.model_validate({"version": 1, "name": "bad", "steps": [step]})


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
        Recipe.model_validate({"version": 1, "name": "bad", "steps": [step]})


def test_recipe_rejects_unknown_version_and_step_type() -> None:
    with pytest.raises(ValidationError, match="version"):
        Recipe.model_validate({"version": 2, "name": "bad", "steps": []})

    with pytest.raises(ValidationError, match="shell"):
        Recipe.model_validate(
            {"version": 1, "name": "bad", "steps": [{"type": "shell", "command": "echo no"}]}
        )


_INT_LIST = {"type": "list", "items": "int"}
_INT_DICT = {"type": "dict", "values": "int"}


@pytest.mark.parametrize(
    ("spec", "value", "expected"),
    [
        ({"type": "str"}, "api", "api"),
        ({"type": "int"}, "3", 3),
        ({"type": "bool"}, "false", False),
        ({"type": "float"}, "2.25", 2.25),
        (_INT_LIST, ["1", 2], [1, 2]),
        (_INT_LIST, (), []),
        (
            {"type": "dict", "values": "bool"},
            {"on": "true", "off": False},
            {"on": True, "off": False},
        ),
        ({"type": "dict", "values": "bool"}, {}, {}),
        # Structured shapes default to string elements.
        ({"type": "list"}, [1, "api"], ["1", "api"]),
        ({"type": "dict"}, {"replicas": 3}, {"replicas": "3"}),
    ],
)
def test_input_spec_coerces_declared_types(
    spec: dict[str, object], value: object, expected: object
) -> None:
    assert InputSpec.model_validate(spec).coerce(value) == expected


@pytest.mark.parametrize(
    ("spec", "value", "match"),
    [
        (_INT_LIST, "not-a-list", "cannot coerce value to list"),
        (_INT_LIST, ["not-an-int"], "cannot coerce value to list"),
        (_INT_LIST, [["nested"]], "cannot coerce value to list"),
        (_INT_DICT, "not-a-dict", "cannot coerce value to dict"),
        (_INT_DICT, {1: "2"}, "dict input keys must be strings"),
        (_INT_DICT, {"replicas": "not-an-int"}, "cannot coerce value to dict"),
    ],
)
def test_input_spec_structured_coercion_errors_use_pinned_messages(
    spec: dict[str, object], value: object, match: str
) -> None:
    with pytest.raises(ValueError, match=match):
        InputSpec.model_validate(spec).coerce(value)


def test_input_spec_rejects_invalid_structured_shape_metadata() -> None:
    with pytest.raises(ValidationError, match="items is only valid with type list"):
        InputSpec.model_validate({"type": "str", "items": "int"})

    with pytest.raises(ValidationError, match="values is only valid with type dict"):
        InputSpec.model_validate({"type": "str", "values": "int"})


@pytest.mark.parametrize("input_type", ["int", "float", "bool"])
def test_input_spec_coercion_errors_do_not_echo_values(input_type: str) -> None:
    secret = "TOP-SECRET-9000"

    with pytest.raises(ValueError, match=f"cannot coerce value to {input_type}") as excinfo:
        InputSpec(type=input_type).coerce(secret)  # type: ignore[arg-type]

    assert secret not in str(excinfo.value)
    assert secret not in "".join(
        traceback.format_exception(
            type(excinfo.value),
            excinfo.value,
            excinfo.value.__traceback__,
        )
    )


def test_input_spec_supports_metadata_scope_and_from_fallbacks() -> None:
    spec = InputSpec.model_validate(
        {
            "type": "str",
            "description": "Service name.",
            "required": True,
            "from": ["{{ record.repo }}", "{{ target.name }}"],
            "sensitive": True,
        }
    )

    assert spec.description == "Service name."
    assert spec.scope == "target"
    assert spec.from_ == ("{{ record.repo }}", "{{ target.name }}")
    assert spec.sensitive is True
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
        Recipe.model_validate({"version": 1, "name": "bad", "steps": [step]})
