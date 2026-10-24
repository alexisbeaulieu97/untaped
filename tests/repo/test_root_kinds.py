"""Root management commands tag ``--format pipe`` records with ``untaped.*`` kinds."""

from __future__ import annotations

import json

import pytest

from untaped.runtime.bootstrap import build_root_app
from untaped.runtime.registry import PluginCandidate
from untaped.sdk import home
from untaped.sdk.testing import invoke_cli

pytestmark = pytest.mark.usefixtures("fresh_composition")


@pytest.mark.parametrize(
    ("args", "kind"),
    [
        (["config", "list"], "untaped.setting"),
        (["config", "get", "untaped.skills.updates"], "untaped.setting"),
        (["profile", "list"], "untaped.profile"),
        (["plugin", "list"], "untaped.installed_plugin"),
        (["doctor"], "untaped.doctor_check"),
        (["skills", "list"], "untaped.skill"),
        (["auth", "status"], "untaped.token_source"),
        (["config", "set", "untaped.skills.updates", "auto"], "untaped.setting_outcome"),
        (["config", "unset", "untaped.skills.updates"], "untaped.setting_outcome"),
        (["profile", "create", "qa"], "untaped.profile_outcome"),
        (["profile", "rename", "stage", "staging", "--dry-run"], "untaped.profile_outcome"),
        (["profile", "delete", "stage", "--dry-run"], "untaped.profile_outcome"),
    ],
)
def test_root_pipe_records_carry_an_untaped_kind(
    first_party_candidates: tuple[PluginCandidate, ...], args: list[str], kind: str
) -> None:
    config = home().expanduser() / "config.yml"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text("profiles:\n  default: {}\n  stage: {}\n", encoding="utf-8")
    root = build_root_app(candidates=first_party_candidates)
    result = invoke_cli(root, [*args, "--format", "pipe"])

    lines = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    assert lines, result.output
    assert {line["kind"] for line in lines} == {kind}
