"""Plugin CLI tests for `plugins doctor` behavior."""

from __future__ import annotations

from pathlib import Path

import pytest

from untaped.plugins import DiagnosticResult, PluginRegistry, set_current_registry
from untaped.plugins import app as plugins_app
from untaped.testing import CliInvoker

pytestmark = pytest.mark.usefixtures("_isolated_config")


def test_plugins_doctor_success_path_reports_ok_diagnostics(_isolated_config: Path) -> None:
    registry = PluginRegistry()
    registry.add_plugin_id("demo")
    registry.add_diagnostic("demo", lambda: DiagnosticResult(name="demo", status="ok"))
    set_current_registry(registry)

    result = CliInvoker().invoke(plugins_app, ["doctor"])

    assert result.exit_code == 0, result.output
    assert result.stdout.splitlines() == ["ok\tdemo"]
