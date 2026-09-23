"""The ``helpers`` object every hook receives, plus the recipe template renderer.

One implementation serves both runtimes: built-in hooks get it in-process and
external hooks get it inside the uv worker. The worker loads this file by path
in a pack's environment, where ``untaped`` is not installed, so it must stay
stdlib-only at import time; ``ruamel.yaml`` is imported only when a YAML helper
runs (a pack that uses them declares the dependency itself).
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from io import StringIO
from typing import TYPE_CHECKING, Any

_TOKEN_RE = re.compile(r"{{.*?}}")
_BARE_TOKEN_RE = re.compile(r"{{\s*([A-Za-z_][A-Za-z0-9_]*)\s*}}")
_UNKNOWN_TOKEN_MODES = {"error", "keep"}


def render_template(
    template: str,
    inputs: Mapping[str, object],
    *,
    unknown_tokens: str = "error",
) -> str:
    """Render ``{{ name }}`` placeholders from resolved recipe inputs."""
    if unknown_tokens not in _UNKNOWN_TOKEN_MODES:
        raise ValueError("unknown_tokens must be 'error' or 'keep'")

    def _replace(match: re.Match[str]) -> str:
        token = match.group(0)
        name = bare_token_name(token)
        if name is not None:
            if name in inputs:
                value = inputs[name]
                if isinstance(value, Mapping | list | tuple):
                    raise ValueError(structured_render_error(name))
                return str(value)
            if unknown_tokens == "keep":
                return token
            raise ValueError(f"template input {name!r} is not defined")
        if unknown_tokens == "keep":
            return token
        raise ValueError(
            f"template token {token!r} is not a bare input name; "
            "set unknown_tokens: keep to pass it through"
        )

    return _TOKEN_RE.sub(_replace, template)


def template_tokens(text: str) -> list[str]:
    """Return every ``{{ ... }}`` token in ``text``, in order."""
    return _TOKEN_RE.findall(text)


def bare_token_name(token: str) -> str | None:
    """Return the input name of a bare ``{{ name }}`` token, else ``None``."""
    match = _BARE_TOKEN_RE.fullmatch(token)
    return None if match is None else match.group(1)


def structured_render_error(name: str) -> str:
    """Error text for rendering a list/dict input into a string."""
    return f"structured input {name!r} cannot be rendered; hooks receive it natively"


def load_yaml(content: str) -> object:
    """Load YAML while preserving round-trip metadata (comments, quotes)."""
    from ruamel.yaml import YAML  # noqa: PLC0415

    yaml = YAML()
    yaml.preserve_quotes = True
    return yaml.load(content)


def dump_yaml(data: object, *, options: Mapping[str, object] | None = None) -> str:
    """Dump round-trip YAML with the supported formatting ``options``."""
    from ruamel.yaml import YAML  # noqa: PLC0415

    yaml = YAML()
    apply_yaml_dump_options(yaml, options)
    out = StringIO()
    yaml.dump(data, out)
    return out.getvalue()


class HookHelpers:
    """Helpers passed to every hook as ``helpers``.

    A fresh instance is built per hook invocation so ``warn`` accumulates
    warnings for exactly one target. Verdicts are plain dicts so the same
    values cross the worker's JSON protocol unchanged.
    """

    def __init__(self) -> None:
        self._warnings: list[str] = []

    def pass_(self, message: str = "") -> dict[str, str]:
        """Return a passing validation verdict."""
        return {"status": "pass", "message": message}

    def fail(self, message: str) -> dict[str, str]:
        """Return a failing validation verdict."""
        return {"status": "fail", "message": message}

    def skip(self, message: str = "") -> dict[str, str]:
        """Return a skip verdict marking the target not applicable."""
        return {"status": "skip", "message": message}

    def warn(self, message: str) -> None:
        """Accumulate a non-fatal warning for the current target."""
        self._warnings.append(str(message))

    @property
    def warnings(self) -> list[str]:
        """Warnings accumulated during this invocation."""
        return list(self._warnings)

    def render_template(
        self,
        template: str,
        inputs: dict[str, object],
        *,
        unknown_tokens: str = "error",
    ) -> str:
        """Render simple ``{{ input }}`` placeholders."""
        return render_template(template, inputs, unknown_tokens=unknown_tokens)

    def load_yaml(self, content: str) -> object:
        """Round-trip-load YAML content."""
        return load_yaml(content)

    def dump_yaml(self, data: object, *, options: Mapping[str, object] | None = None) -> str:
        """Round-trip-dump YAML data with optional formatting controls."""
        return dump_yaml(data, options=options)


if TYPE_CHECKING:
    from untaped.capabilities.recipe.hook_api import HookHelpers as HookHelpersContract

    _helpers_satisfy_public_contract: HookHelpersContract = HookHelpers()


_TOP_LEVEL_OPTIONS = frozenset(
    {
        "width",
        "preserve_quotes",
        "indent",
        "block_seq_indent",
        "explicit_start",
        "explicit_end",
    }
)
_INDENT_OPTIONS = frozenset({"mapping", "sequence", "offset"})
_SCALAR_OPTIONS: dict[str, tuple[type[object], str]] = {
    "block_seq_indent": (int, "an integer"),
    "explicit_start": (bool, "a boolean"),
    "explicit_end": (bool, "a boolean"),
}


def apply_yaml_dump_options(yaml: Any, options: Mapping[str, object] | None) -> None:
    """Apply supported YAML dump formatting options to a ruamel YAML instance."""
    opts: Mapping[str, object] = {} if options is None else options
    _reject_unknown_keys(opts, _TOP_LEVEL_OPTIONS, label="YAML dump option")
    preserve_quotes = _optional(opts, "preserve_quotes", bool, "a boolean")
    width = _optional(opts, "width", int, "an integer")
    yaml.preserve_quotes = True if preserve_quotes is None else preserve_quotes
    yaml.width = 4096 if width is None else width

    indent = opts.get("indent")
    if indent is not None:
        if not isinstance(indent, Mapping):
            raise TypeError("YAML dump option 'indent' must be a mapping")
        _reject_unknown_keys(indent, _INDENT_OPTIONS, label="YAML indent option")
        indent_options = {
            key: value
            for key in ("mapping", "sequence", "offset")
            if (value := _optional(indent, key, int, "an integer")) is not None
        }
        if indent_options:
            yaml.indent(**indent_options)

    for name, (expected_type, label) in _SCALAR_OPTIONS.items():
        scalar_value = _optional(opts, name, expected_type, label)
        if scalar_value is not None:
            setattr(yaml, name, scalar_value)


def _optional[T](
    options: Mapping[str, object],
    key: str,
    expected_type: type[T],
    label: str,
) -> T | None:
    value = options.get(key)
    if value is None:
        return None
    if type(value) is not expected_type:
        raise TypeError(f"YAML dump option {key!r} must be {label}")
    return value


def _reject_unknown_keys(
    options: Mapping[str, object],
    supported: frozenset[str],
    *,
    label: str,
) -> None:
    unknown = sorted(str(key) for key in options if key not in supported)
    if unknown:
        raise TypeError(f"unsupported {label}: {unknown[0]}")
