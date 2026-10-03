"""LoadTestSuite: file path → validated :class:`Suite`.

The use case wires injected adapters end-to-end: read file → split
frontmatter → resolve variable values → render Jinja2 body → parse YAML
→ validate. Every error names the file.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path

from pydantic import ValidationError

from untaped.sdk import ConfigError, UsageError, attribution, first_validation_error
from untaped_awx.application.suites.ports import (
    Filesystem,
    Parser,
    Prompt,
    VarsResolver,
)
from untaped_awx.domain.suite import Suite, VariableSpec


class LoadTestSuite:
    def __init__(
        self,
        filesystem: Filesystem,
        *,
        parser: Parser,
        vars_resolver: VarsResolver,
        prompt: Prompt,
    ) -> None:
        self._fs = filesystem
        self._parser = parser
        self._resolve_vars = vars_resolver
        self._prompt = prompt

    def __call__(
        self,
        path: Path,
        *,
        cli_vars: Mapping[str, str] | None = None,
        vars_files: Iterable[Path] = (),
        extra_known_names: Iterable[str] = (),
    ) -> Suite:
        text = self._fs.read_text(path)
        with _naming(path):
            meta_yaml, body = self._parser.split_frontmatter(text)
            var_specs = self._parse_variable_specs(meta_yaml)
            values = self._resolve_vars(
                var_specs,
                cli=cli_vars or {},
                files=vars_files,
                prompt=self._prompt,
                extra_known_names=extra_known_names,
            )
            rendered = self._parser.render_body(body, values)
            data = self._parser.parse_yaml(rendered)
            if not isinstance(data, dict):
                raise ConfigError(
                    f"rendered body must be a YAML mapping; got {type(data).__name__}",
                    category="invalid",
                )
            if "kind" not in data:
                raise ConfigError(
                    "missing required 'kind: AwxTestSuite' marker", category="invalid"
                )
            if "variables" in data:
                raise ConfigError(
                    "declare variables in the '---' header, not the body", category="invalid"
                )
            data.setdefault("name", path.stem)
            # Carry the parsed frontmatter specs through so callers (e.g.
            # ``awx test list --format json``) can introspect required vars.
            data["variables"] = {name: spec for name, spec in var_specs.items()}
            try:
                return Suite.model_validate(data)
            except ValidationError as exc:
                raise ConfigError(first_validation_error(exc), category="invalid") from exc

    def parse_specs(self, path: Path) -> dict[str, VariableSpec]:
        """Read *path* and return its frontmatter variable specs only.

        Lets the CLI build the union of variables across multiple files
        before resolution, so a global ``--var foo=bar`` is accepted as
        long as *some* file declares ``foo`` — even if this particular
        file doesn't.
        """
        text = self._fs.read_text(path)
        with _naming(path):
            meta_yaml, _ = self._parser.split_frontmatter(text)
            return self._parse_variable_specs(meta_yaml)

    def _parse_variable_specs(self, meta_yaml: str) -> dict[str, VariableSpec]:
        if not meta_yaml.strip():
            return {}
        meta = self._parser.parse_yaml(meta_yaml)
        if meta is None:
            return {}
        if not isinstance(meta, dict):
            raise ConfigError("frontmatter must be a YAML mapping", category="invalid")
        raw_vars = meta.get("variables")
        if raw_vars is None:
            return {}
        if not isinstance(raw_vars, dict):
            raise ConfigError("frontmatter 'variables' must be a mapping", category="invalid")
        specs: dict[str, VariableSpec] = {}
        for name, body in raw_vars.items():
            if not isinstance(body, dict):
                raise ConfigError(
                    f"variable {name!r} metadata must be a mapping", category="invalid"
                )
            # Defensively drop ``name`` from the body so it can't conflict
            # with the explicit ``name=str(name)`` kwarg below — otherwise
            # ``VariableSpec(name=…, **body)`` raises a raw ``TypeError``
            # that would leak past the CLI's typed-error boundary.
            body_without_name = {k: v for k, v in body.items() if k != "name"}
            try:
                specs[str(name)] = VariableSpec(name=str(name), **body_without_name)
            except ValidationError as exc:
                raise ConfigError(f"variable {name!r}: {exc}", category="invalid") from exc
        return specs


@contextmanager
def _naming(path: Path) -> Iterator[None]:
    """Prefix every config or usage error raised inside with ``path``, keeping its type
    and attribution."""
    try:
        yield
    except (ConfigError, UsageError) as exc:
        raise type(exc)(f"{path}: {exc}", **attribution(exc)) from exc
