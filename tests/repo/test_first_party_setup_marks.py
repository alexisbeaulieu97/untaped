"""First-party plugins mark what needs setting up, and core reads them the same way.

No first-party settings model keeps a secret in a setting that is not
deprecated: each secret is a declared credential. GitHub with only
``GH_TOKEN`` exported reads set up.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from core_test_support.commands.support import write_config
from untaped.runtime import bootstrap
from untaped.runtime.registry import PluginCandidate
from untaped.sdk._schema import unwrap_optional
from untaped.sdk.marks import field_marks
from untaped.sdk.testing import invoke_cli

pytestmark = pytest.mark.usefixtures("fresh_composition", "_isolated_config")


def test_no_first_party_model_keeps_a_secret_in_a_setting(
    first_party_candidates: tuple[PluginCandidate, ...],
) -> None:
    composed = bootstrap.compose_root(candidates=first_party_candidates)
    assert composed.quarantine == ()
    unmarked: list[str] = []
    for plugin in composed.plugins:
        model = plugin.settings
        if model is None:
            continue
        deprecated = field_marks(model)
        for name, field in model.model_fields.items():
            if unwrap_optional(field.annotation) is SecretStr and name not in deprecated:
                unmarked.append(f"{plugin.name}.{name}")
    assert unmarked == []
    assert {plugin.name for plugin in composed.plugins if plugin.credentials} >= {
        "awx",
        "github",
        "jira",
    }


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
