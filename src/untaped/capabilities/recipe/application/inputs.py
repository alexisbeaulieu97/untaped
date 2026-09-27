"""Resolve recipe inputs for one apply invocation and target."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from untaped.capabilities.recipe.application.ports import PromptFunc
from untaped.capabilities.recipe.application.targets import Target
from untaped.capabilities.recipe.domain.input_jinja import (
    UNRESOLVED,
    CompiledInputSource,
    InputSourceError,
    compile_input_source,
    derive_input_value,
    ensure_derived_value_within_bound,
)
from untaped.capabilities.recipe.domain.recipe import InputSpec, Recipe
from untaped.capabilities.recipe.errors import RecipeError
from untaped.capability_api import ConfigError

REDACTED = "***"
_UNSET = object()


class NoPromptAvailableError(RecipeError):
    """Raised when interactive input is requested without a prompt backend."""


@dataclass(frozen=True)
class InputResolutionConfig:
    """Invocation-level input resolution settings."""

    fixed_values: Mapping[str, object] = field(default_factory=dict)
    cli_sources: Mapping[str, CompiledInputSource] = field(default_factory=dict)
    recipe_sources: Mapping[str, CompiledInputSource] = field(default_factory=dict)
    interactive: bool = False
    prompt: PromptFunc | None = None


_STRUCTURED = frozenset({"list", "dict"})


@dataclass(frozen=True)
class InputResolutionResult:
    """Resolved real inputs plus a redacted display/audit view."""

    values: dict[str, object]
    display_values: dict[str, object]


def prepare_input_resolution(
    recipe: Recipe,
    *,
    fixed_values: Mapping[str, object],
    input_from: Mapping[str, str],
    interactive: bool = False,
    prompt: PromptFunc | None = None,
) -> InputResolutionConfig:
    """Validate and compile invocation-level input resolution settings."""
    _validate_config(recipe, fixed_values=fixed_values, input_from=input_from)
    typed_fixed_values = _coerce_fixed_values(recipe, fixed_values)
    cli_sources = {
        name: _compile_named_source(name, (expression,)) for name, expression in input_from.items()
    }
    recipe_sources = {
        name: _compile_named_source(name, spec.from_)
        for name, spec in recipe.inputs.items()
        if spec.from_
    }
    return InputResolutionConfig(
        fixed_values=typed_fixed_values,
        cli_sources=cli_sources,
        recipe_sources=recipe_sources,
        interactive=interactive,
        prompt=prompt,
    )


def prompts_per_target(recipe: Recipe, config: InputResolutionConfig) -> bool:
    """Whether planning a target may prompt, so targets must plan one at a time.

    Only a required scalar target input with no ``--var``/``--vars-file``
    value and no ``--input-from`` source can reach the prompt (a recipe
    ``from`` may miss); global inputs prompt once, before planning.
    """
    return config.interactive and any(
        spec.required
        and spec.scope != "global"
        and spec.type not in _STRUCTURED
        and name not in config.fixed_values
        and name not in config.cli_sources
        for name, spec in recipe.inputs.items()
    )


def validate_recipe_input_sources(recipe: Recipe) -> None:
    """Validate recipe-owned input source expressions without resolving targets."""
    for name, spec in recipe.inputs.items():
        if spec.from_:
            _compile_named_source(name, spec.from_)


def resolve_global_values(recipe: Recipe, config: InputResolutionConfig) -> dict[str, object]:
    """Resolve invocation-global input values once before target planning."""
    values: dict[str, object] = {}
    for name, spec in recipe.inputs.items():
        if spec.scope != "global":
            continue
        value = _resolve_one(name, spec, target=None, config=config)
        if value is not _UNSET:
            values[name] = value
    return values


def resolve_target_inputs(
    recipe: Recipe,
    target: Target,
    *,
    config: InputResolutionConfig,
    global_values: Mapping[str, object],
) -> InputResolutionResult:
    """Resolve all declared inputs for one target."""
    values: dict[str, object] = {}
    for name, spec in recipe.inputs.items():
        if spec.scope == "global":
            if name in global_values:
                values[name] = global_values[name]
            continue
        value = _resolve_one(name, spec, target=target, config=config)
        if value is not _UNSET:
            values[name] = value
    return InputResolutionResult(values=values, display_values=redact_inputs(recipe.inputs, values))


def redact_inputs(
    specs: Mapping[str, InputSpec],
    values: Mapping[str, object],
) -> dict[str, object]:
    """Return declared input values with sensitive entries redacted."""
    redacted: dict[str, object] = {}
    for name, value in values.items():
        spec = specs.get(name)
        if spec is None:
            continue
        redacted[name] = REDACTED if spec.sensitive else value
    return redacted


def has_sensitive_inputs(
    specs: Mapping[str, InputSpec],
    display_values: Mapping[str, object],
) -> bool:
    """Return whether the display row contains any resolved sensitive input."""
    return any((spec := specs.get(name)) is not None and spec.sensitive for name in display_values)


def _resolve_one(
    name: str,
    spec: InputSpec,
    *,
    target: Target | None,
    config: InputResolutionConfig,
) -> object:
    if name in config.fixed_values:
        return config.fixed_values[name]
    cli_source = config.cli_sources.get(name)
    if cli_source is not None and target is not None:
        rendered = _derive_source_value(cli_source, target)
        if rendered is UNRESOLVED:
            raise ValueError(
                f"--input-from for input {name!r} did not resolve for target: {target.path}"
            )
        return _coerce_derived_value(name, spec, rendered)
    recipe_source = config.recipe_sources.get(name) if target is not None else None
    if recipe_source is not None and target is not None:
        rendered = _derive_source_value(recipe_source, target)
        if rendered is not UNRESOLVED:
            return _coerce_derived_value(name, spec, rendered)
    if spec.default is not None:
        return _coerce_input(name, spec, spec.default)
    if not spec.required:
        return _UNSET
    # Structured inputs cannot be typed at a prompt.
    if config.interactive and spec.type not in _STRUCTURED:
        prompt_target = None if target is None else target.path
        return _coerce_input(name, spec, _prompt_value(name, spec, prompt_target, config))
    raise ValueError(f"missing required input: {name}; pass --var {name}=VALUE or --vars-file FILE")


def _validate_config(
    recipe: Recipe,
    *,
    fixed_values: Mapping[str, object],
    input_from: Mapping[str, str],
) -> None:
    unknown_values = sorted(set(fixed_values) - set(recipe.inputs))
    if unknown_values:
        raise ConfigError(f"unknown input: {unknown_values[0]}")
    unknown_sources = sorted(set(input_from) - set(recipe.inputs))
    if unknown_sources:
        raise ConfigError(f"unknown input: {unknown_sources[0]}")
    for name in input_from:
        if recipe.inputs[name].scope == "global":
            raise ConfigError(f"cannot use --input-from for input {name!r} with scope global")
    conflicts = sorted(set(fixed_values) & set(input_from))
    if conflicts:
        raise ConfigError(f"cannot combine --var/--vars-file and --input-from for {conflicts[0]}")


def _coerce_fixed_values(
    recipe: Recipe,
    fixed_values: Mapping[str, object],
) -> dict[str, object]:
    typed: dict[str, object] = {}
    for name, value in fixed_values.items():
        spec = recipe.inputs[name]
        try:
            typed[name] = spec.coerce(_prepare_fixed_value(name, spec, value))
        except ConfigError:
            raise
        except ValueError as exc:
            raise ConfigError(f"input {name!r}: {exc}") from exc
    return typed


def _prepare_fixed_value(name: str, spec: InputSpec, value: object) -> object:
    if spec.type not in {"list", "dict"} or not isinstance(value, str):
        return value
    expected = "list" if spec.type == "list" else "mapping"
    try:
        parsed = yaml.safe_load(value)
    except yaml.YAMLError as exc:
        raise ConfigError(f"input {name!r} expects YAML {expected}: {exc}") from exc
    if spec.type == "list" and not isinstance(parsed, list):
        raise ConfigError(f"input {name!r} expects YAML list: parsed value is not a list")
    if spec.type == "dict" and not isinstance(parsed, dict):
        raise ConfigError(f"input {name!r} expects YAML mapping: parsed value is not a mapping")
    return parsed


def _compile_named_source(name: str, candidates: tuple[str, ...]) -> CompiledInputSource:
    try:
        return compile_input_source(candidates)
    except InputSourceError as exc:
        raise ConfigError(f"invalid input source expression for {name}: {exc}") from exc


def _derive_source_value(source: CompiledInputSource, target: Target) -> object:
    try:
        return derive_input_value(source, context=_target_context(target))
    except InputSourceError as exc:
        raise ValueError(str(exc)) from exc


def _coerce_input(name: str, spec: InputSpec, value: object) -> object:
    """Coerce one input value, naming the input in any coercion error."""
    try:
        return spec.coerce(value)
    except ValueError as exc:
        raise ValueError(f"input {name!r}: {exc}") from exc


def _coerce_derived_value(name: str, spec: InputSpec, value: object) -> object:
    structured = spec.type in {"list", "dict"}
    try:
        ensure_derived_value_within_bound(value, structured=structured)
        coerced = _coerce_input(name, spec, value)
        ensure_derived_value_within_bound(coerced, structured=structured)
    except InputSourceError as exc:
        raise ValueError(str(exc)) from exc
    return coerced


def _target_context(target: Target) -> dict[str, object]:
    context: dict[str, object] = {
        "target": {
            "path": str(target.path),
            "name": target.path.name,
            "parent_path": str(target.path.parent),
            "parent_name": target.path.parent.name,
        }
    }
    if target.record is not None:
        context["record"] = dict(target.record)
    return context


def _prompt_value(
    name: str,
    spec: InputSpec,
    target: Path | None,
    config: InputResolutionConfig,
) -> object:
    if config.prompt is None:
        raise NoPromptAvailableError("interactive input requires a terminal prompt backend")
    suffix = f" ({spec.description})" if spec.description else ""
    message = f"{name}{suffix}" if target is None else f"{name} for {target}{suffix}"
    return config.prompt(message, sensitive=spec.sensitive)
