"""The ``{{ name }}`` renderer, as one file that imports only the standard library.

A process where untaped isn't installed (recipe's hook worker, in a pack's
own environment) loads this file by the path :func:`untaped_values.api.renderer_file`
gives and calls :func:`render_template`. That promise (standard library only,
that one function) is tested in this plugin's own suite, so nothing here may
import untaped or a third-party package.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

_TOKEN_RE = re.compile(r"{{.*?}}")
_BARE_TOKEN_RE = re.compile(r"{{\s*([A-Za-z_][A-Za-z0-9_]*)\s*}}")
_UNKNOWN_TOKEN_MODES = {"error", "keep"}


def render_template(
    template: str,
    inputs: Mapping[str, object],
    *,
    unknown_tokens: str = "error",
) -> str:
    """Render ``{{ name }}`` placeholders from resolved input values.

    ``unknown_tokens="keep"`` leaves a token naming no input, or one that is
    not a bare name (``{{ github.ref }}``), as written; ``"error"`` raises
    ``ValueError`` for either. A list or mapping value is never rendered.
    """
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
    """Every ``{{ ... }}`` token in ``text``, in order."""
    return _TOKEN_RE.findall(text)


def bare_token_name(token: str) -> str | None:
    """The input name of a bare ``{{ name }}`` token, else ``None``."""
    match = _BARE_TOKEN_RE.fullmatch(token)
    return None if match is None else match.group(1)


def structured_render_error(name: str) -> str:
    """The error for rendering a list or mapping input into text."""
    return f"structured input {name!r} cannot be rendered into text"
