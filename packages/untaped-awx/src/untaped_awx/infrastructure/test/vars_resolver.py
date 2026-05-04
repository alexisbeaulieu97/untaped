"""Resolve a set of :class:`VariableSpec` declarations to concrete values.

Precedence (high → low): CLI ``--var`` > ``--vars-file`` > metadata
``default`` > interactive prompt. Variables not in any source and lacking
a default are *required*; in non-interactive mode they fail-fast with a
list of missing names so the user can re-run with ``--var``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import yaml

from untaped_awx.application.test.ports import Prompt
from untaped_awx.domain.test_suite import VariableSpec
from untaped_awx.errors import AwxApiError

_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off"})


def resolve_variables(
    specs: Mapping[str, VariableSpec],
    *,
    cli: Mapping[str, str],
    files: Iterable[Path],
    prompt: Prompt,
    extra_known_names: Iterable[str] = (),
) -> dict[str, Any]:
    """Build the variable context that will be passed to Jinja2.

    ``extra_known_names`` lets the caller validate against the union of
    variables declared across multiple suites: a CLI ``--var foo=bar`` is
    accepted (but ignored) here as long as *some* suite declares ``foo``,
    even if this particular suite doesn't. Without it, a multi-file run
    where each file declares a disjoint set of variables would fail.
    """
    known_names: set[str] = set(specs.keys()) | set(extra_known_names)

    _reject_unknown(cli.keys(), known_names, "cli")

    file_values: dict[str, Any] = {}
    for path in files:
        loaded = _load_vars_file(path)
        _reject_unknown(loaded.keys(), known_names, f"vars-file {path}")
        file_values.update(loaded)

    resolved: dict[str, Any] = {}
    missing_in_non_interactive: list[str] = []
    for name, spec in specs.items():
        if name in cli:
            value: Any = _coerce(spec, cli[name], source=f"--var {name}=…")
        elif name in file_values:
            value = _coerce(spec, file_values[name], source=f"vars-file ({name})")
        elif spec.default is not None:
            # Coerce defaults too: a string-quoted ``default: "8080"`` for
            # ``type: int`` must produce an int, matching CLI/file input.
            value = _coerce(spec, spec.default, source=f"default ({name})")
        elif not prompt.is_interactive():
            missing_in_non_interactive.append(name)
            continue
        else:
            value = _coerce(spec, prompt.ask(spec), source=f"prompt ({name})")
        resolved[name] = value

    if missing_in_non_interactive:
        joined = ", ".join(missing_in_non_interactive)
        raise AwxApiError(
            f"required variable(s) not provided: {joined}. "
            "Set them with --var <name>=<value> or run interactively."
        )
    return resolved


def _reject_unknown(names: Iterable[str], known: Iterable[str], origin: str) -> None:
    known_set = set(known)
    unknown = sorted(set(names) - known_set)
    if unknown:
        joined = ", ".join(unknown)
        raise AwxApiError(
            f"unknown variable(s) in {origin}: {joined}. "
            f"Declared variables: {', '.join(sorted(known_set)) or '(none)'}"
        )


def _load_vars_file(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise AwxApiError(f"failed to read vars-file {path}: {exc}") from exc
    try:
        parsed = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise AwxApiError(f"vars-file {path} is not valid YAML: {exc}") from exc
    if parsed is None:
        return {}
    if not isinstance(parsed, dict):
        raise AwxApiError(f"vars-file {path} must be a YAML mapping")
    non_string = sorted(repr(key) for key in parsed if not isinstance(key, str))
    if non_string:
        raise AwxApiError(
            f"vars-file {path}: variable names must be strings (got {', '.join(non_string)})"
        )
    return parsed


def _coerce(spec: VariableSpec, value: Any, *, source: str) -> Any:
    """Apply ``spec.type`` coercion to a string-or-typed value."""
    if spec.type == "string":
        coerced: Any = str(value)
    elif spec.type == "int":
        try:
            coerced = int(value)
        except (TypeError, ValueError) as exc:
            raise AwxApiError(f"{source}: expected int, got {value!r}") from exc
    elif spec.type == "bool":
        coerced = _coerce_bool(value, source=source)
    elif spec.type == "list":
        coerced = _coerce_list(value)
    elif spec.type == "choice":
        coerced = str(value)
        if coerced not in spec.choices:
            choices = ", ".join(repr(c) for c in spec.choices)
            raise AwxApiError(f"{source}: {value!r} is not one of [{choices}]")
    else:  # pragma: no cover — exhausted by Literal
        raise AwxApiError(f"unsupported variable type {spec.type!r}")
    return coerced


def _coerce_bool(value: Any, *, source: str) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in _TRUE:
        return True
    if text in _FALSE:
        return False
    raise AwxApiError(f"{source}: expected bool, got {value!r}")


def _coerce_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return list(value)
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return [value]
