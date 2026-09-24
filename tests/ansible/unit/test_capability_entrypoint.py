"""Ansible doctor check for deprecated settings, run through the unified root."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from cyclopts import App

from untaped import bootstrap
from untaped.capabilities.ansible import SPEC
from untaped.settings import get_settings
from untaped.testing import CliInvoker


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    cfg = tmp_path / "config.yml"
    monkeypatch.setenv("UNTAPED_CONFIG", str(cfg))
    monkeypatch.delenv("UNTAPED_PROFILE", raising=False)
    bootstrap._clear_for_tests()
    get_settings.cache_clear()
    yield cfg
    bootstrap._clear_for_tests()
    get_settings.cache_clear()


def _root() -> App:
    return bootstrap.build_root_app(builtins=(SPEC,), externals=())  # type: ignore[return-value]


def _doctor_row(cfg: Path, body: str) -> dict[str, object]:
    cfg.write_text(body)
    get_settings.cache_clear()
    result = CliInvoker().invoke(_root().meta, ["doctor", "--format", "json"])
    assert result.exit_code == 0, result.output
    rows = json.loads(result.stdout)
    return next(row for row in rows if row["check"] == "ansible.deprecated-settings")


def test_doctor_warns_when_freshness_ttl_is_set(_isolate: Path) -> None:
    row = _doctor_row(_isolate, "profiles:\n  default:\n    ansible:\n      freshness_ttl: 3600\n")

    assert row["status"] == "warn"
    assert "ansible.freshness_ttl is deprecated and ignored" in str(row["detail"])
    assert "config unset ansible.freshness_ttl" in str(row["detail"])


def test_doctor_passes_without_deprecated_settings(_isolate: Path) -> None:
    row = _doctor_row(_isolate, "profiles:\n  default:\n    ansible: {}\n")

    assert row["status"] == "pass"
