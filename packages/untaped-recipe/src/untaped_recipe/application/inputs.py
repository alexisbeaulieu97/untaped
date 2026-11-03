"""Resolve recipe inputs for one apply invocation and target.

The values plugin owns the shared part: conversion, precedence, the refusal of
undeclared names, asking and redaction. Recipe adds what only it has: global
and per-target scope, and per-target values derived by a recipe's ``from``
candidates or ``--input-from``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from untaped.sdk import UsageError
from untaped_recipe.application.targets import Target
from untaped_recipe.domain.input_jinja import (
    UNRESOLVED,
    CompiledInputSource,
    InputSourceError,
    compile_input_source,
    derive_input_value,
    ensure_derived_value_within_bound,
)
from untaped_recipe.domain.recipe import InputSpec, RecipeFile
from untaped_recipe.errors import RecipeError
from untaped_values.api import Ask, coerce, redact, resolve


@dataclass(frozen=True)
class InputResolutionConfig:
    """Invocation-level input resolution settings."""

    fixed_values: Mapping[str, object] = field(default_factory=dict)
    cli_sources: Mapping[str, CompiledInputSource] = field(default_factory=dict)
    recipe_sources: Mapping[str, CompiledInputSource] = field(default_factory=dict)
    ask: Ask | None = None


_STRUCTURED = frozenset({"list", "dict"})


@dataclass(frozen=True)
class InputResolutionResult:
    """Resolved real inputs plus a redacted display/audit view."""

    values: dict[str, object]
    display_values: dict[str, object]


def prepare_input_resolution(
    recipe: RecipeFile,
    *,
    fixed_values: Mapping[str, object],
    input_from: Mapping[str, str],
    ask: Ask | None = None,
) -> InputResolutionConfig:
    """Validate and compile invocation-level input resolution settings.

    Every ``--var``/``--vars-file`` value is converted here, once, so a bad
    one fails the whole run before any target, and a name the recipe doesn't
    declare is refused.
    """
    _validate_input_from(recipe, fixed_values=fixed_values, input_from=input_from)
    given = {name: spec for name, spec in recipe.inputs.items() if name in fixed_values}
    typed_fixed_values = resolve(given, [fixed_values], known=recipe.inputs).values
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
        ask=ask,
    )


def validate_recipe_input_sources(recipe: RecipeFile) -> None:
    """Validate recipe-owned input source expressions without resolving targets."""
    for name, spec in recipe.inputs.items():
        if spec.from_:
            _compile_named_source(name, spec.from_)


def resolve_global_values(recipe: RecipeFile, config: InputResolutionConfig) -> dict[str, object]:
    """Resolve invocation-global input values once before target planning."""
    specs = {name: spec for name, spec in recipe.inputs.items() if spec.scope == "global"}
    return _resolve(specs, [config.fixed_values], recipe=recipe, config=config, label="")


def resolve_target_inputs(
    recipe: RecipeFile,
    target: Target,
    *,
    config: InputResolutionConfig,
    global_values: Mapping[str, object],
) -> InputResolutionResult:
    """Resolve all declared inputs for one target.

    Per target, a value comes from ``--var``/``--vars-file``, then
    ``--input-from`` (which must resolve), then the recipe's ``from``
    candidates (which fall through), then the default, then an answer.
    """
    specs = {name: spec for name, spec in recipe.inputs.items() if spec.scope == "target"}
    derived_cli: dict[str, object] = {}
    derived_recipe: dict[str, object] = {}
    for name, spec in specs.items():
        if name in config.fixed_values:
            continue
        cli_source = config.cli_sources.get(name)
        if cli_source is not None:
            rendered = _derive_source_value(cli_source, target)
            if rendered is UNRESOLVED:
                raise ValueError(
                    f"--input-from for input {name!r} did not resolve for target: {target.path}"
                )
            derived_cli[name] = _coerce_derived_value(name, spec, rendered)
            continue
        recipe_source = config.recipe_sources.get(name)
        if recipe_source is not None:
            rendered = _derive_source_value(recipe_source, target)
            if rendered is not UNRESOLVED:
                derived_recipe[name] = _coerce_derived_value(name, spec, rendered)
    own = _resolve(
        specs,
        [config.fixed_values, derived_cli, derived_recipe],
        recipe=recipe,
        config=config,
        label=str(target.path),
    )
    values = {
        name: global_values[name] if name in global_values else own[name]
        for name in recipe.inputs
        if name in global_values or name in own
    }
    return InputResolutionResult(values=values, display_values=redact(recipe.inputs, values))


def has_secret_inputs(
    specs: Mapping[str, InputSpec],
    display_values: Mapping[str, object],
) -> bool:
    """Return whether the display row contains any resolved secret input."""
    return any((spec := specs.get(name)) is not None and spec.secret for name in display_values)


def _resolve(
    specs: Mapping[str, InputSpec],
    given: list[Mapping[str, object]],
    *,
    recipe: RecipeFile,
    config: InputResolutionConfig,
    label: str,
) -> dict[str, object]:
    """The values plugin's resolution over ``specs`` (one scope of ``recipe``'s inputs).

    The given values may name the other scope's inputs (``known``). A missing
    input fails with the ``--var`` hint.
    """
    resolved = resolve(specs, given, known=recipe.inputs, ask=config.ask, label=label)
    if resolved.missing:
        raise ValueError(_missing_message(resolved.missing))
    return resolved.values


def _missing_message(missing: tuple[str, ...]) -> str:
    if len(missing) == 1:
        name = missing[0]
        return f"missing required input: {name}; pass --var {name}=VALUE or --vars-file FILE"
    names = ", ".join(missing)
    return f"missing required inputs: {names}; pass --var NAME=VALUE or --vars-file FILE"


def _validate_input_from(
    recipe: RecipeFile,
    *,
    fixed_values: Mapping[str, object],
    input_from: Mapping[str, str],
) -> None:
    unknown_sources = sorted(set(input_from) - set(recipe.inputs))
    if unknown_sources:
        raise RecipeError(f"unknown input: {unknown_sources[0]}")
    for name in input_from:
        if recipe.inputs[name].scope == "global":
            raise RecipeError(f"cannot use --input-from for input {name!r} with scope global")
    conflicts = sorted(set(fixed_values) & set(input_from))
    if conflicts:
        raise UsageError(f"cannot combine --var/--vars-file and --input-from for {conflicts[0]}")


def _compile_named_source(name: str, candidates: tuple[str, ...]) -> CompiledInputSource:
    try:
        return compile_input_source(candidates)
    except InputSourceError as exc:
        raise RecipeError(f"invalid input source expression for {name}: {exc}") from exc


def _derive_source_value(source: CompiledInputSource, target: Target) -> object:
    return derive_input_value(source, context=_target_context(target))


def _coerce_derived_value(name: str, spec: InputSpec, value: object) -> object:
    structured = spec.type in _STRUCTURED
    ensure_derived_value_within_bound(value, structured=structured)
    coerced = coerce(spec, value, name=name)
    ensure_derived_value_within_bound(coerced, structured=structured)
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
