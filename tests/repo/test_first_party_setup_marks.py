"""First-party plugins mark what needs setting up, and core reads them the same way.

No first-party settings model leans on the deprecated base_url/token
fallback or leaves a secret unmarked; only ``GithubSettings`` (api surface
until 12.0) still declares its own command setting and ``token_sources``.
GitHub with only ``GH_TOKEN`` exported reads set up, as before the marks.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from test_management.support import write_config
from untaped import bootstrap
from untaped.config_schema import unwrap_optional
from untaped.plugins.registry import PluginCandidate, PluginSpec
from untaped.setting_marks import declared_marks, self_declared_commands, setup_marks
from untaped.testing import invoke_cli

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
        marks = declared_marks(model)
        marked = {field.key for field in marks.fields} if marks is not None else set()
        secrets = {field.key for field in marks.secrets} if marks is not None else set()
        for name, field in model.model_fields.items():
            if name == "base_url" and field.default is None and name not in marked:
                unmarked.append(f"{spec.name}.{name}")
            if unwrap_optional(field.annotation) is SecretStr and name not in secrets:
                unmarked.append(f"{spec.name}.{name}")
        marks_read = setup_marks(model)
        assert marks_read is None or not marks_read.legacy, spec.name
        if self_declared_commands(model) or hasattr(model, "token_sources"):
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
    write_config(_isolated_config, "profiles:\n  default: {}\n")
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
