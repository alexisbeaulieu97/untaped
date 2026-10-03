"""Ansible settings as seen by root ``untaped doctor``, run through the unified root."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from cyclopts import App

from untaped import bootstrap
from untaped.settings import get_settings
from untaped.testing import CliInvoker, provider_candidate
from untaped_ansible import SPEC


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fresh_composition: None) -> Path:
    cfg = tmp_path / "config.yml"
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    return cfg


_ANSIBLE_PROFILE = "profiles:\n  default:\n    ansible:\n      {}\n"


def _root() -> App:
    return bootstrap.build_root_app(candidates=(provider_candidate(SPEC),))  # type: ignore[return-value]


def _doctor_rows(cfg: Path, body: str) -> dict[str, dict[str, object]]:
    cfg.write_text(body)
    get_settings.cache_clear()
    result = CliInvoker().invoke(_root().meta, ["doctor", "--format", "json"])
    assert result.exit_code == 0, result.output
    return {str(row["check"]): row for row in json.loads(result.stdout)}


def test_removed_freshness_ttl_is_reported_as_an_unknown_key(_isolate: Path) -> None:
    rows = _doctor_rows(_isolate, _ANSIBLE_PROFILE.format("freshness_ttl: 3600"))

    assert "ansible.deprecated-settings" not in rows
    assert rows["unknown-keys"]["status"] == "warn"
    assert "ansible.freshness_ttl" in str(rows["unknown-keys"]["detail"])


def test_default_source_is_a_known_key(_isolate: Path) -> None:
    rows = _doctor_rows(_isolate, _ANSIBLE_PROFILE.format("default_source: prod"))

    assert rows["unknown-keys"]["status"] == "pass"
