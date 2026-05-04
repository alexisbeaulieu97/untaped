"""Frontmatter splitter, the ``!ref`` YAML tag, and the Jinja2 environment.

A test file is a YAML frontmatter document — a metadata block delimited
by ``---`` lines, followed by a Jinja2-rendered body. The metadata block
is parsed as raw YAML (so users can declare variable types without
needing the variable values yet); the body is rendered with the resolved
variable context, then parsed as YAML.

The ``!ref`` tag is a structurally distinct YAML node (a custom-tagged
mapping), so a regular dict that happens to contain ``name``/``kind``
keys is *never* misinterpreted as a foreign-key reference.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import yaml
from jinja2 import Environment, StrictUndefined, TemplateError, UndefinedError

from untaped_awx.domain.test_suite import RefSentinel
from untaped_awx.errors import AwxApiError

__all__ = [
    "DefaultParser",
    "RefSentinel",
    "build_jinja_env",
    "load_yaml_with_refs",
    "split_frontmatter",
]


# ---- frontmatter splitter ------------------------------------------------


def split_frontmatter(text: str) -> tuple[str, str]:
    """Split a frontmatter document into (metadata_yaml, body).

    The metadata block, if present, is the YAML between two ``---``
    lines at the top of the file. Bodies without a frontmatter return
    an empty string for the metadata.
    """
    stripped = text.lstrip("\n")
    if not stripped.startswith("---"):
        return "", text

    lines = stripped.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        return "", text

    for index in range(1, len(lines)):
        if lines[index].rstrip("\r\n") == "---":
            metadata = "".join(lines[1:index])
            body = "".join(lines[index + 1 :])
            return metadata, body

    raise AwxApiError(
        "frontmatter is missing its closing '---' delimiter "
        "(expected: '---\\n<yaml>\\n---\\n<body>')"
    )


# ---- !ref tag ------------------------------------------------------------


class _RefSafeLoader(yaml.SafeLoader):
    """Private loader so the ``!ref`` constructor can't leak globally.

    Also rejects duplicate mapping keys: PyYAML's default loader silently
    keeps only the last duplicate, which would let a Jinja2 matrix that
    accidentally produces the same case name twice quietly drop a case.
    """

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
        seen: set[Any] = set()
        duplicates: list[str] = []
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)  # type: ignore[no-untyped-call]
            try:
                if key in seen:
                    duplicates.append(repr(key))
                seen.add(key)
            except TypeError:
                # Unhashable key — let the base implementation raise.
                pass
        if duplicates:
            line = node.start_mark.line + 1
            raise AwxApiError(
                f"duplicate YAML mapping key(s) at line {line}: {', '.join(duplicates)}"
            )
        return super().construct_mapping(node, deep=deep)


def _construct_ref(loader: yaml.SafeLoader, node: yaml.Node) -> RefSentinel:
    if not isinstance(node, yaml.MappingNode):
        raise AwxApiError(
            f"!ref must be a mapping with 'kind' and 'name' (line {node.start_mark.line + 1})"
        )
    mapping = loader.construct_mapping(node, deep=True)
    kind = mapping.pop("kind", None)
    name = mapping.pop("name", None)
    if not isinstance(kind, str) or not kind:
        raise AwxApiError(f"!ref requires a 'kind' string (got {kind!r})")
    if not isinstance(name, str) or not name:
        raise AwxApiError(f"!ref requires a 'name' string (got {name!r})")
    scope = {str(k): str(v) for k, v in mapping.items()} or None
    return RefSentinel(kind=kind, name=name, scope=scope)


_RefSafeLoader.add_constructor("!ref", _construct_ref)


def load_yaml_with_refs(text: str) -> Any:
    """Parse YAML, recognising the ``!ref`` tag as :class:`RefSentinel`.

    Wraps :class:`yaml.YAMLError` in :class:`AwxApiError` so the
    application layer never has to import PyYAML's exception types.
    """
    try:
        return yaml.load(text, Loader=_RefSafeLoader)
    except yaml.YAMLError as exc:
        raise AwxApiError(f"invalid YAML: {exc}") from exc


# ---- Jinja2 env ---------------------------------------------------------


def _to_yaml(value: Any) -> str:
    """Render *value* as a single-line YAML literal for safe interpolation.

    PyYAML emits ``<scalar>\\n...\\n`` (the bare scalar followed by an
    end-of-document marker on its own line) for plain scalars; strip the
    ``\\n...`` end marker so the result can be embedded mid-document.

    Strip *only* the end-of-document marker — ``removesuffix("...")``
    here would also eat a user's literal trailing dots (e.g. a value
    like ``release...``), silently corrupting the launch payload.
    """
    out = yaml.safe_dump(value, default_flow_style=True, width=10_000).rstrip("\n")
    return out.removesuffix("\n...").rstrip("\n")


def _to_json(value: Any) -> str:
    """Filter: render *value* as JSON. JSON is valid YAML."""
    return json.dumps(value)


def build_jinja_env() -> Environment:
    """Construct the Jinja2 environment used for test-file bodies.

    - ``StrictUndefined`` so missing-variable typos raise rather than
      silently rendering as the empty string.
    - ``to_yaml`` / ``to_json`` filters for safe value interpolation.
    """
    env = Environment(
        undefined=StrictUndefined,
        autoescape=False,
        keep_trailing_newline=True,
    )
    env.filters["to_yaml"] = _to_yaml
    env.filters["to_json"] = _to_json
    return env


# ---- Parser adapter for the application layer ---------------------------


class DefaultParser:
    """Concrete :class:`untaped_awx.application.test.ports.Parser`.

    Wraps the module-level functions plus a single Jinja2 environment so
    callers don't pay rebuild cost per file. Stateless aside from the
    cached env.
    """

    def __init__(self) -> None:
        self._env = build_jinja_env()

    def split_frontmatter(self, text: str) -> tuple[str, str]:
        return split_frontmatter(text)

    def parse_yaml(self, text: str) -> Any:
        return load_yaml_with_refs(text)

    def render_body(
        self,
        body: str,
        values: Mapping[str, Any],
    ) -> str:
        try:
            template = self._env.from_string(body)
            return template.render(dict(values))
        except UndefinedError as exc:
            raise AwxApiError(f"undefined Jinja2 variable: {exc}") from exc
        except TemplateError as exc:
            # Covers ``TemplateSyntaxError`` (compile-time) and other Jinja2
            # errors raised during rendering (e.g. filter failures).
            raise AwxApiError(f"Jinja2 template error: {exc}") from exc
