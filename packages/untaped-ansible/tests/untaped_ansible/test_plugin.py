"""The ansible plugin as a whole: lint, its plugin check, its surface, the root mounting it."""

from __future__ import annotations

import json

import pytest
import yaml

from ansible_test_support import run_root
from untaped.sdk.testing import Home, assert_surface, check_plugin, invoke_root
from untaped_ansible.plugin import Ansible
from untaped_lint.testing import assert_clean


def test_ansible_passes_lint() -> None:
    assert_clean("ansible")


def test_ansible_passes_its_plugin_check() -> None:
    """Every plugin installed, as ``untaped[all]`` has them: ``find``'s example pipes awx's."""
    failed = [row for row in check_plugin("ansible") if row.status in ("fail", "error")]
    assert failed == []


def test_ansible_keeps_its_surface() -> None:
    assert_surface(Ansible)  # snapshots/surface.txt beside this file


def test_the_root_mounts_the_plugin() -> None:
    listed = invoke_root(["plugin", "list", "--format", "json"])
    assert ("ansible", "ready") in [(r["name"], r["status"]) for r in json.loads(listed.stdout)]
    assert "impact" in invoke_root(["ansible", "--help"]).stdout


def _doctor_rows() -> dict[str, dict[str, object]]:
    result = run_root("doctor", "--format", "json")
    assert result.exit_code == 0, result.output
    return {str(row["check"]): row for row in json.loads(result.stdout)}


@pytest.mark.parametrize(("key", "status"), [("freshness_ttl", "warn"), ("default_source", "pass")])
def test_doctor_reports_an_unknown_key_and_not_a_known_one(
    home: Home, key: str, status: str
) -> None:
    config = {"format_version": 2, "ansible": {key: "prod"}}
    home.path.mkdir(parents=True, exist_ok=True)
    (home.path / "config.yml").write_text(yaml.safe_dump(config), encoding="utf-8")

    rows = _doctor_rows()

    assert rows["untaped.unknown-keys"]["status"] == status
    assert (f"ansible.{key}" in str(rows["untaped.unknown-keys"]["detail"])) == (status == "warn")
