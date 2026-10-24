"""First-party plugins mark what needs setting up, and core reads them the same way.

No first-party settings model leans on the deprecated base_url/token
fallback or keeps a secret in a setting that is not deprecated; only
``GithubSettings`` (api surface until 12.0) still declares its own command
setting and ``token_sources``. GitHub with only ``GH_TOKEN`` exported reads
set up, as before the marks.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from core_test_support.commands.support import write_config
from untaped.runtime import bootstrap
from untaped.runtime.registry import PluginCandidate, PluginSpec
from untaped.sdk._schema import unwrap_optional
from untaped.sdk.health.marks import setup_marks
from untaped.sdk.marks import field_marks
from untaped.sdk.testing import invoke_cli

pytestmark = pytest.mark.usefixtures("fresh_composition", "_isolated_config")


def test_no_first_party_model_relies_on_the_names(
    first_party_specs: tuple[PluginSpec, ...],
) -> None:
    unmarked: list[str] = []
    own_commands: set[str] = set()
    for spec in first_party_specs:
        model = spec.settings
        if model is None:
            continue
        marks = setup_marks(model, spec.credentials)
        assert marks is None or not marks.legacy, spec.name
        deprecated = field_marks(model)
        for name, field in model.model_fields.items():
            if unwrap_optional(field.annotation) is SecretStr and name not in deprecated:
                unmarked.append(f"{spec.name}.{name}")
        if "token_command" in model.model_fields or hasattr(model, "token_sources"):
            own_commands.add(model.__name__)
    assert unmarked == []
    assert own_commands == {"GithubSettings"}


def _cli(candidates: tuple[PluginCandidate, ...], *args: str) -> list[dict[str, Any]]:
    root = bootstrap.build_root_app(candidates=candidates)
    result = invoke_cli(root.meta, [*args, "--format", "json"])
    assert result.stdout, result.output
    rows: list[dict[str, Any]] = json.loads(result.stdout)
    return rows


def test_github_with_only_gh_token_is_set_up(
    _isolated_config: Path,
    monkeypatch: pytest.MonkeyPatch,
    first_party_candidates: tuple[PluginCandidate, ...],
) -> None:
    for name in ("GITHUB_TOKEN", "UNTAPED_PROFILE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GH_TOKEN", "ghp_ambient")
    write_config(_isolated_config, "format_version: 2\n")
    setup = {
        row["plugin"]: row
        for row in _cli(first_party_candidates, "doctor")
        if row["check"] == "setup"
    }
    assert (setup["github"]["status"], setup["github"]["detail"]) == (
        "pass",
        "token from $GH_TOKEN",
    )
    assert setup["awx"]["detail"] == setup["jira"]["detail"] == "not set up"
    plan = {row["step"]: row for row in _cli(first_party_candidates, "setup", "plan")}
    assert plan["github.token"]["state"] == "done"
    assert plan["awx.token"]["state"] == "todo"
