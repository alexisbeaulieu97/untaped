"""Values into text and rows: strict ``{{ name }}`` rendering, redaction, and the renderer file.

The renderer itself is :mod:`untaped_values._renderer`, standard library only,
so a process where untaped isn't installed can load it by the path
:func:`renderer_file` gives; :func:`render` checks a field, then calls it, so
there is one implementation.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from untaped.sdk import experimental
from untaped_values import _renderer as renderer
from untaped_values.errors import InputError
from untaped_values.inputs import Input

REDACTED = "***"
_STRUCTURED = frozenset({"list", "dict"})


@experimental
def renderer_file() -> Path:
    """The renderer as one file that imports only the standard library.

    For a process where untaped isn't installed: load it by path and call
    ``render_template(text, values, *, unknown_tokens="error")``, which
    renders ``{{ name }}`` and raises ``ValueError`` for an unknown token
    (``unknown_tokens="keep"`` leaves it) or a list or mapping value. The
    promise (standard library only, that one function) is tested in this
    plugin's own suite.
    """
    return Path(renderer.__file__)


@experimental
def render[S: Input](
    text: str, values: Mapping[str, object], specs: Mapping[str, S], *, field: str
) -> str:
    """``text`` with each ``{{ name }}`` replaced by its value, strictly.

    Raises :class:`InputError` naming ``field`` for a token that isn't a bare
    name, a name with no value, or a secret, ``list`` or ``dict`` input: a
    secret never reaches a path, a target or a command line through here.
    """
    for token in renderer.template_tokens(text):
        name = renderer.bare_token_name(token)
        if name is None:
            raise InputError(f"token {token!r} in field {field!r} is not a bare input name")
        spec = specs.get(name)
        if spec is not None and spec.secret:
            raise InputError(f"secret input {name!r} cannot be used in field {field!r}")
        if (spec is not None and spec.type in _STRUCTURED) or isinstance(
            values.get(name), Mapping | list | tuple
        ):
            raise InputError(f"structured input {name!r} cannot be used in field {field!r}")
        if name not in values:
            raise InputError(f"input {name!r} in field {field!r} has no value")
    return renderer.render_template(text, values, unknown_tokens="error")


@experimental
def redact[S: Input](specs: Mapping[str, S], values: Mapping[str, object]) -> dict[str, object]:
    """The declared values for display: each secret one as ``***``, undeclared names left out."""
    return {
        name: REDACTED if spec.secret else value
        for name, value in values.items()
        if (spec := specs.get(name)) is not None
    }
