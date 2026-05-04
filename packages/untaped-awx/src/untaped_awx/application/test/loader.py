"""LoadTestSuite: file path → validated :class:`TestSuite`.

The use case wires injected adapters end-to-end: read file → split
frontmatter → resolve variable values → render Jinja2 body → parse YAML
→ validate. Non-empty ``assert:`` blocks are rejected so users can't
silently green-run un-checked behaviour.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path

from pydantic import ValidationError

from untaped_awx.application.test.ports import (
    Filesystem,
    Parser,
    Prompt,
    VarsResolver,
)
from untaped_awx.domain.test_suite import TestSuite, VariableSpec
from untaped_awx.errors import AwxApiError


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
    ) -> TestSuite:
        text = self._fs.read_text(path)
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
            raise AwxApiError(
                f"{path}: rendered body must be a YAML mapping; got {type(data).__name__}"
            )
        if "kind" not in data:
            raise AwxApiError(f"{path}: missing required 'kind: AwxTestSuite' marker")
        data.setdefault("name", path.stem)
        # Carry the parsed frontmatter specs through so callers (e.g.
        # ``awx test list --format json``) can introspect required vars.
        data["variables"] = {name: spec for name, spec in var_specs.items()}
        try:
            suite = TestSuite.model_validate(data)
        except ValidationError as exc:
            raise AwxApiError(f"{path}: {exc}") from exc
        _reject_non_empty_assert(path, suite)
        return suite

    def parse_specs(self, path: Path) -> dict[str, VariableSpec]:
        """Read *path* and return its frontmatter variable specs only.

        Lets the CLI build the union of variables across multiple files
        before resolution, so a global ``--var foo=bar`` is accepted as
        long as *some* file declares ``foo`` — even if this particular
        file doesn't.
        """
        text = self._fs.read_text(path)
        meta_yaml, _ = self._parser.split_frontmatter(text)
        return self._parse_variable_specs(meta_yaml)

    def _parse_variable_specs(self, meta_yaml: str) -> dict[str, VariableSpec]:
        if not meta_yaml.strip():
            return {}
        meta = self._parser.parse_yaml(meta_yaml)
        if meta is None:
            return {}
        if not isinstance(meta, dict):
            raise AwxApiError("frontmatter must be a YAML mapping")
        raw_vars = meta.get("variables")
        if raw_vars is None:
            return {}
        if not isinstance(raw_vars, dict):
            raise AwxApiError("frontmatter 'variables' must be a mapping")
        specs: dict[str, VariableSpec] = {}
        for name, body in raw_vars.items():
            if not isinstance(body, dict):
                raise AwxApiError(f"variable {name!r} metadata must be a mapping")
            # Defensively drop ``name`` from the body so it can't conflict
            # with the explicit ``name=str(name)`` kwarg below — otherwise
            # ``VariableSpec(name=…, **body)`` raises a raw ``TypeError``
            # that would leak past the CLI's typed-error boundary.
            body_without_name = {k: v for k, v in body.items() if k != "name"}
            try:
                specs[str(name)] = VariableSpec(name=str(name), **body_without_name)
            except ValidationError as exc:
                raise AwxApiError(f"variable {name!r}: {exc}") from exc
        return specs


def _reject_non_empty_assert(path: Path, suite: TestSuite) -> None:
    locations: list[str] = []
    if suite.defaults is not None and suite.defaults.assert_:
        locations.append("defaults")
    for name, case in suite.cases.items():
        if case.assert_:
            locations.append(f"cases.{name}")
    if locations:
        joined = ", ".join(locations)
        raise AwxApiError(
            f"{path}: non-empty 'assert:' block(s) at {joined} — assertions land in v2; "
            "remove or empty the assert: block in v1."
        )
